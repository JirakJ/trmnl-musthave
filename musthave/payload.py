"""Skládání merge_variables pro TRMNL webhook (limit 5 kB bez TRMNL+, držíme rezervu)."""

from __future__ import annotations

import copy
import json
from collections.abc import Iterable
from datetime import datetime

from .config import Countdown
from .policy import battery_percent  # noqa: F401 – re-export (šablony, status)
from .layout import View, resolve_panels
from .streams import StreamsConfig, build_section

MAX_BYTES = 4500
DAYS_CS_LONG = ["Pondělí", "Úterý", "Středa", "Čtvrtek", "Pátek", "Sobota", "Neděle"]


GAME_MAX = 22
GAME_MIN = 12
NAME_MAX = 18  # název události v odpočtu
SAVING_NOTES = {"quiet": "noční režim", "low": "úspora", "quiet+low": "noc · úspora"}  # krátký údaj u baterie


class PayloadTooLarge(Exception):
    pass


def czech_date(now: datetime) -> str:
    return f"{DAYS_CS_LONG[now.weekday()]} {now.day}. {now.month}. {now.year}"


def days_label(days: int) -> str:
    """Česká podoba zbývajících dnů: DNES / 1 den / 2–4 dny / 5+ dní."""
    if days == 0:
        return "DNES"
    if days == 1:
        return "1 den"
    return f"{days} dny" if days < 5 else f"{days} dní"


def countdown_items(events: Iterable[Countdown], now: datetime) -> list[dict]:
    """Dny do každé události (kalendářní, v časové zóně `now`), seřazené od nejbližší; minulé se vynechají,
    opakované (yearly) se posunou na další rok.

    Položka: n (název, zkrácený), days, label (hotový český text), d (den. měsíc.)."""
    today = now.date()
    items = []
    for event in events:
        when = event.next_date(today)
        days = (when - today).days
        if days >= 0:
            items.append({"n": _truncate(event.name, NAME_MAX), "days": days, "label": days_label(days),
                          "d": f"{when.day}. {when.month}."})
    return sorted(items, key=lambda i: i["days"])


def encode(payload: dict) -> bytes:
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")


def sort_items(items: list[dict]) -> list[dict]:
    live = sorted((i for i in items if i.get("live")), key=lambda i: -int(i.get("v") or 0))
    offline = [i for i in items if not i.get("live")]
    return live + offline


def _truncate(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[: limit - 1] + "…"


def as_of(since: float | None, now: datetime) -> str | None:
    """Čas posledního úspěšného získání dat ve stejné zóně jako `now` (HH:MM, starší než den i s datem)."""
    if not isinstance(since, (int, float)) or since <= 0:
        return None
    when = datetime.fromtimestamp(since, now.tzinfo)  # tzinfo None = místní čas stroje
    if when.date() == now.date():
        return when.strftime("%H:%M")
    return f"{when.day}. {when.month}. {when.strftime('%H:%M')}"


def _stale_marks(out: dict, source: dict, now: datetime) -> dict:
    """`since` (timestamp) → `as_of` (text pro obrazovku); timestamp do payloadu nepatří."""
    out.pop("since", None)
    if source.get("stale"):
        out["stale"] = True
        stamp = as_of(source.get("since"), now)
        if stamp:
            out["as_of"] = stamp
    return out


def _section(sources: list[tuple[str, dict]], game_max: int, now: datetime | None = None,
             cfg: StreamsConfig | None = None) -> dict:
    """Sekce streamů (jedna platforma nebo společný seznam), viz streams.build_section."""
    cfg = cfg or StreamsConfig()
    out = build_section(sources, cfg, game_max)
    if out.get("mode") == "all":
        out.pop("mode")
    if not out["ok"]:
        out = {"ok": False, "items": [], "live": 0}
    stale = [src for _, src in sources if src.get("ok") and src.get("stale")]
    if stale:  # stale = poslední dobrá data po výpadku; čas = nejstarší z nich
        out["stale"] = True
        if now is not None:
            stamp = as_of(min(float(s.get("since") or 0) for s in stale) or None, now)
            if stamp:
                out["as_of"] = stamp
    return out


def build_payload(
    weather: dict, kick: dict, twitch: dict, now: datetime, host: str = "", fw: str = "",
    countdowns: Iterable[Countdown] = (),
    battery_voltage: float | None = None,
    saving: str | None = None,
    max_bytes: int | None = MAX_BYTES,
    location: str = "",
    extras: dict | None = None,
    streams_cfg: StreamsConfig | None = None,
    view: View | None = None,
    profile: str | None = None,
) -> dict:
    """Payload pro šablony. `max_bytes` = limit TRMNL cloudu (zkrátí/odebere volitelná data, viz fit_for_cloud);
    None = lokální BYOS render bez limitu. `extras` = volitelné panely (alert, agenda, …), None hodnoty se vynechají.
    `view` = rozložení (panely, varianta počasí, odpočet), výchozí podle [streams] combined."""
    streams_cfg = streams_cfg or StreamsConfig()
    view = view or View(panels=("streams",) if streams_cfg.combined else ("kick", "twitch"))
    payload = {
        "updated": now.strftime("%H:%M"),
        "location": location,
        "date": czech_date(now),
        "host": host,
        "fw": fw,
        "battery_percent": battery_percent(battery_voltage),
        "saving": SAVING_NOTES.get(saving or ""),
        "weather": _weather(weather, now),
        "kick": _section([("kick", kick)], GAME_MAX, now, streams_cfg),
        "twitch": _section([("twitch", twitch)], GAME_MAX, now, streams_cfg),
        "countdowns": countdown_items(countdowns, now) if view.countdown else [],
        "layout": {"weather": view.weather, **({"profile": profile} if profile else {})},
    }
    if "streams" in view.panels or streams_cfg.combined:
        payload["streams"] = _section([("kick", kick), ("twitch", twitch)], GAME_MAX, now, streams_cfg)
    payload.update({k: v for k, v in (extras or {}).items() if v is not None})
    payload["panels"] = resolve_panels(view, payload)
    return payload if max_bytes is None else fit_for_cloud(payload, max_bytes)


def _weather(weather: dict, now: datetime) -> dict:
    """Počasí pro payload; ze starších (stale) dat se vynechají hodinová okna, která už skončila."""
    from .weather import rain_summary

    out = _stale_marks(copy.deepcopy(weather), weather, now)
    hours = out.get("hours")
    if isinstance(hours, list):
        now_ts = now.timestamp()
        current = [h for h in hours if not isinstance(h.get("ts"), (int, float)) or h["ts"] + 3600 > now_ts]
        if len(current) != len(hours):
            out["hours"] = current
            out["rain"] = rain_summary(current) if current else "Hodinová předpověď nedostupná"
    return out


# Co se v cloudovém payloadu (limit 5 kB) obětuje první: volitelné panely, pak hodinová předpověď.
CLOUD_OPTIONAL = ("agenda", "reminders", "alert", ("weather", "hours"))


def fit_for_cloud(payload: dict, max_bytes: int = MAX_BYTES) -> dict:
    """Menší reprezentace pro TRMNL cloud: zkrátí názvy her, pak odebírá volitelná data podle CLOUD_OPTIONAL.

    Lokální BYOS render limit nemá; cloud dostane kompaktní verzi (šablony s chybějícími klíči počítají)."""
    if len(encode(payload)) <= max_bytes:
        return payload
    out = copy.deepcopy(payload)
    for key in ("kick", "twitch", "streams"):
        for item in (out.get(key) or {}).get("items", []):
            if "g" in item:
                item["g"] = _truncate(item["g"], GAME_MIN)
    for key in CLOUD_OPTIONAL:
        if len(encode(out)) <= max_bytes:
            return out
        if isinstance(key, tuple):
            parent = out.get(key[0])
            if isinstance(parent, dict) and key[1] in parent:
                parent.pop(key[1])
                parent.pop("rain", None)
        else:
            out.pop(key, None)
    if len(encode(out)) <= max_bytes:
        return out
    raise PayloadTooLarge(f"payload exceeds {max_bytes} B even after truncation")
