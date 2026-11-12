"""Agenda z ICS kalendářů (jen čtení): nejbližší události dneška a zítřka pro panel `agenda`.

Soukromí: adresy kalendářů (často s tajným tokenem) se čtou jen z negitovaného `config.local.toml`
(`[agenda] sources`) nebo z proměnné prostředí / `.env` `MUSTHAVE_ICS_URLS` (oddělené mezerou/novým řádkem).
Do logů, payloadu ani stavu se adresa nedostane – zdroj se označuje pořadím a otiskem (sha256). Přihlašovací
údaje v URL (`https://user:heslo@…`) se pošlou jako HTTP Basic a z URL se odstraní.
"""

from __future__ import annotations

import base64
import hashlib
import logging
import urllib.error
from dataclasses import dataclass, field
from datetime import datetime, time, timedelta
from pathlib import Path
from urllib.parse import unquote, urlsplit, urlunsplit
from zoneinfo import ZoneInfo

from .ics import IcsError, expand, parse_events

log = logging.getLogger(__name__)

MAX_ICS_BYTES = 3 * 1024 * 1024
DAYS_CS = ["Po", "Út", "St", "Čt", "Pá", "So", "Ne"]
TITLE_MAX = 34
LOCATION_MAX = 26
RETRY_S = 120   # po chybě zdroje další pokus za 2 min


@dataclass(frozen=True)
class AgendaConfig:
    sources: tuple[str, ...] = field(default=(), repr=False)  # URL nebo cesta k .ics; NIKDY v config.toml ani v logu
    days: int = 2                         # dnes + zítra
    max_items: int = 5
    private: bool = False                 # skrýt názvy a místa událostí na displeji
    show_location: bool = True
    refresh_minutes: int = 15
    max_age_hours: int = 12               # starší cache = „Agenda nedostupná“
    timeout_s: float = 15.0

    @property
    def enabled(self) -> bool:
        return bool(self.sources)


def source_id(index: int, source: str) -> str:
    """Stabilní neprozrazující označení zdroje pro stav a logy."""
    return f"{index + 1}-{hashlib.sha256(source.encode('utf-8')).hexdigest()[:10]}"


def _request(source: str) -> tuple[str, dict[str, str]]:
    """URL bez přihlašovacích údajů + hlavičky (Basic auth). webcal:// → https://."""
    parts = urlsplit(source)
    scheme = "https" if parts.scheme in ("webcal", "webcals") else parts.scheme
    headers = {"Accept": "text/calendar, */*"}
    netloc = parts.hostname or ""
    if parts.port:
        netloc += f":{parts.port}"
    if parts.username is not None:
        token = f"{unquote(parts.username)}:{unquote(parts.password or '')}".encode("utf-8")
        headers["Authorization"] = "Basic " + base64.b64encode(token).decode("ascii")
    return urlunsplit((scheme, netloc, parts.path, parts.query, "")), headers


def read_source(http, source: str, cfg: AgendaConfig) -> str:
    if source.startswith("/") or source.startswith("~"):
        path = Path(source).expanduser()
        if path.stat().st_size > MAX_ICS_BYTES:
            raise IcsError("calendar file too large")
        return path.read_text(encoding="utf-8", errors="replace")
    url, headers = _request(source)
    if urlsplit(url).scheme not in ("http", "https"):
        raise IcsError("unsupported calendar scheme")
    return http.get_bytes(url, MAX_ICS_BYTES, headers).decode("utf-8", errors="replace")


def _describe(err: Exception) -> str:
    """Popis chyby bez URL (HTTPError/URLError ji mohou obsahovat)."""
    if isinstance(err, urllib.error.HTTPError):
        return f"HTTP {err.code}"
    if isinstance(err, urllib.error.URLError):
        return f"URLError {type(err.reason).__name__}"
    return type(err).__name__


def _serialize(occ, index: int) -> dict:
    return {"s": occ.start.isoformat(), "e": occ.end.isoformat(), "a": occ.all_day, "n": occ.summary,
            "l": occ.location, "u": occ.uid, "c": index}


def fetch_agenda(http, cfg: AgendaConfig, last: dict | None, now: datetime) -> tuple[dict, dict]:
    """(stav k zobrazení, záznam k uložení).

    Záznam: {"sources": {id: {"ts", "retry_at", "failed_at", "items": [...]}}}; každý zdroj se stahuje nejvýš jednou za
    `refresh_minutes`, při chybě se použije jeho cache do `max_age_hours` (stale). Výskyty se ukládají pro okno
    včera … dnes + days + 1, filtr podle aktuálního času se dělá až při sestavení panelu."""
    tz = now.tzinfo or ZoneInfo("Europe/Prague")
    now_ts = now.timestamp()
    last_sources = (last or {}).get("sources", {}) if isinstance(last, dict) else {}
    window_start = datetime.combine(now.date() - timedelta(days=1), time(0), tzinfo=tz)
    window_end = datetime.combine(now.date() + timedelta(days=cfg.days + 1), time(0), tzinfo=tz)
    record: dict = {"sources": {}}
    items: list[dict] = []
    stale_since: float | None = None
    failed = 0
    unsupported = 0
    for index, source in enumerate(cfg.sources):
        sid = source_id(index, source)
        prev = last_sources.get(sid) if isinstance(last_sources.get(sid), dict) else None

        def use_cache(entry: dict) -> None:
            nonlocal stale_since, failed
            ts = float(entry.get("ts") or 0)
            if now_ts - ts > cfg.max_age_hours * 3600:
                failed += 1                       # prošlá cache se za aktuální agendu nevydává
                return
            items.extend(entry.get("items", []))
            if float(entry.get("failed_at") or 0) > ts:   # poslední pokus selhal → data jsou starší
                stale_since = ts if stale_since is None else min(stale_since, ts)

        if prev and now_ts < float(prev.get("retry_at") or 0):
            record["sources"][sid] = prev
            if prev.get("ts"):
                use_cache(prev)
            else:
                failed += 1
            continue
        try:
            occurrences, bad = expand(parse_events(read_source(http, source, cfg), tz), window_start, window_end, tz)
            unsupported += bad
            entry = {"ts": now_ts, "retry_at": now_ts + cfg.refresh_minutes * 60,
                     "items": [_serialize(o, index) for o in occurrences]}
            record["sources"][sid] = entry
            items.extend(entry["items"])
        except Exception as err:  # noqa: BLE001 – kalendář nesmí shodit render; URL do logu nepatří
            log.warning("agenda source #%d failed: %s", index + 1, _describe(err))
            entry = {**(prev or {"ts": 0, "items": []}), "failed_at": now_ts, "retry_at": now_ts + RETRY_S}
            record["sources"][sid] = entry
            if entry.get("ts"):
                use_cache(entry)
            else:
                failed += 1
    if unsupported:
        log.info("agenda: %d recurring events use unsupported RRULE parts and were skipped", unsupported)
    ok = failed < len(cfg.sources)
    state = {"ok": ok, "items": items, "failed": failed}
    if stale_since is not None:
        state.update({"stale": True, "since": stale_since})
    record["ts"] = max((float(s.get("ts") or 0) for s in record["sources"].values()), default=0.0)
    return state, record


def _clip(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[: limit - 1] + "…"


def _day_label(day, today) -> str:
    if day == today:
        return "dnes"
    if day == today + timedelta(days=1):
        return "zítra"
    return f"{DAYS_CS[day.weekday()]} {day.day}. {day.month}."


def agenda_panel(state: dict | None, now: datetime, cfg: AgendaConfig) -> dict | None:
    """Panel: {"ok", "items": [{day, t, e, n, l, now, all}], "today_empty", "more", "stale", "as_of", "partial"}.

    Pořadí je stabilní: den → celodenní před časovanými → začátek → konec → pořadí kalendáře → název → UID.
    Probíhající a celodenní události jsou označené; skončené se vynechají."""
    if not cfg.enabled:
        return None
    if not state or not state.get("ok"):
        return {"ok": False}
    tz = now.tzinfo
    today = now.date()
    last_day = today + timedelta(days=cfg.days - 1)
    rows = []
    seen = set()
    for it in state.get("items", []):
        try:
            start = datetime.fromisoformat(it["s"]).astimezone(tz)
            end = datetime.fromisoformat(it["e"]).astimezone(tz)
        except (KeyError, ValueError, TypeError):
            continue
        if end <= now or (it.get("a") and end <= now):
            continue
        key = (it.get("u"), it["s"], it.get("c"))
        if key in seen:
            continue
        seen.add(key)
        day = max(start.date(), today) if start < now else start.date()
        if day > last_day:
            continue
        all_day = bool(it.get("a"))
        rows.append((day, 0 if all_day else 1, start, end, int(it.get("c") or 0), it.get("n") or "", it.get("u") or "",
                     all_day, it.get("l") or ""))
    rows.sort(key=lambda r: r[:7])
    items = []
    for day, _, start, end, _, title, _, all_day, location in rows:
        entry = {"day": _day_label(day, today), "n": "Obsazeno" if cfg.private else _clip(title, TITLE_MAX)}
        if all_day:
            entry["all"] = True
        else:
            entry["t"] = start.strftime("%H:%M") if start.date() == day else "…"
            entry["e"] = end.strftime("%H:%M") if end.date() == day else "…"
        if start <= now < end and not all_day:
            entry["now"] = True
        if location and cfg.show_location and not cfg.private:
            entry["l"] = _clip(location, LOCATION_MAX)
        items.append(entry)
    out: dict = {"ok": True, "items": items[: cfg.max_items]}
    if len(items) > cfg.max_items:
        out["more"] = len(items) - cfg.max_items
    if not any(i["day"] == "dnes" for i in items):
        out["today_empty"] = True
    if state.get("failed"):
        out["partial"] = True
    if state.get("stale"):
        from .payload import as_of

        out["stale"] = True
        stamp = as_of(state.get("since"), now)
        if stamp:
            out["as_of"] = stamp
    return out
