"""Skládání merge_variables pro TRMNL webhook (limit 5 kB bez TRMNL+, držíme rezervu)."""

from __future__ import annotations

import copy
import json
from collections.abc import Iterable
from datetime import datetime

from .config import Countdown

MAX_BYTES = 4500
DAYS_CS_LONG = ["Pondělí", "Úterý", "Středa", "Čtvrtek", "Pátek", "Sobota", "Neděle"]


GAME_MAX = 22
GAME_MIN = 12
NAME_MAX = 18  # název události v odpočtu


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
    """Dny do každé události (kalendářní, v časové zóně `now`), seřazené od nejbližší; minulé se vynechají.

    Položka: n (název, zkrácený), days, label (hotový český text), d (den. měsíc.)."""
    today = now.date()
    items = []
    for event in events:
        days = (event.date - today).days
        if days >= 0:
            items.append({"n": _truncate(event.name, NAME_MAX), "days": days, "label": days_label(days),
                          "d": f"{event.date.day}. {event.date.month}."})
    return sorted(items, key=lambda i: i["days"])


def encode(payload: dict) -> bytes:
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")


def sort_items(items: list[dict]) -> list[dict]:
    live = sorted((i for i in items if i.get("live")), key=lambda i: -int(i.get("v") or 0))
    offline = [i for i in items if not i.get("live")]
    return live + offline


def _truncate(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[: limit - 1] + "…"


def _section(source: dict, game_max: int) -> dict:
    items = [dict(i) for i in sort_items(source.get("items", []))]
    for i in items:
        if "g" in i:
            i["g"] = _truncate(i["g"], game_max)
    out = {"ok": bool(source.get("ok")), "items": items, "live": sum(1 for i in items if i.get("live"))}
    if source.get("fallback"):
        out["fallback"] = True
    return out


def build_payload(
    weather: dict, kick: dict, twitch: dict, now: datetime, host: str = "", fw: str = "",
    countdowns: Iterable[Countdown] = (),
) -> dict:
    for game_max in (GAME_MAX, GAME_MIN):
        payload = {
            "updated": now.strftime("%H:%M"),
            "date": czech_date(now),
            "host": host,
            "fw": fw,
            "weather": copy.deepcopy(weather),
            "kick": _section(kick, game_max),
            "twitch": _section(twitch, game_max),
            "countdowns": countdown_items(countdowns, now),
        }
        if len(encode(payload)) <= MAX_BYTES:
            return payload
    raise PayloadTooLarge(f"payload exceeds {MAX_BYTES} B even after truncation")
