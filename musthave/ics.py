"""Minimální čtečka iCalendar (RFC 5545) pro agendu – jen standardní knihovna.

Podporovaný rozsah (zdokumentováno v README):
- VCALENDAR/VEVENT; VTIMEZONE se nečte (časové zóny podle TZID přes zoneinfo + mapování běžných Windows názvů
  z Outlooku/Exchange), VALARM a jiné vnořené komponenty se přeskočí,
- DTSTART/DTEND/DURATION: UTC (`…Z`), s `TZID`, „plovoucí“ čas (= zóna dashboardu), celodenní `VALUE=DATE`,
- RRULE: FREQ=DAILY/WEEKLY/MONTHLY/YEARLY, INTERVAL, COUNT, UNTIL, BYDAY (WEEKLY; MONTHLY i s pořadím 1MO/-1FR),
  BYMONTHDAY (MONTHLY), BYMONTH (YEARLY); jiné části (BYSETPOS, BYHOUR, SECONDLY…) = událost se přeskočí
  a započítá jako nepodporovaná (nikdy se nezobrazí špatně),
- EXDATE, RDATE, RECURRENCE-ID (přesunutá/zrušená instance), STATUS:CANCELLED,
- opakování se rozbaluje jen v okně dotazu a nejvýš MAX_EXPANSIONS kroků na událost.
Opakování drží místní čas začátku (DST): týdenní schůzka v 9:00 zůstane v 9:00 i po změně času.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

log = logging.getLogger(__name__)

MAX_EVENTS = 20_000
MAX_EXPANSIONS = 3_000
WEEKDAYS = {"MO": 0, "TU": 1, "WE": 2, "TH": 3, "FR": 4, "SA": 5, "SU": 6}
WINDOWS_TZ = {  # nejčastější Windows názvy z Outlooku/Exchange
    "Central Europe Standard Time": "Europe/Prague",
    "Central European Standard Time": "Europe/Warsaw",
    "W. Europe Standard Time": "Europe/Berlin",
    "Romance Standard Time": "Europe/Paris",
    "GMT Standard Time": "Europe/London",
    "E. Europe Standard Time": "Europe/Bucharest",
    "FLE Standard Time": "Europe/Kiev",
    "Eastern Standard Time": "America/New_York",
    "Pacific Standard Time": "America/Los_Angeles",
    "UTC": "UTC",
}
SUPPORTED_RRULE = {"FREQ", "INTERVAL", "COUNT", "UNTIL", "BYDAY", "BYMONTHDAY", "BYMONTH", "WKST"}


class IcsError(ValueError):
    pass


@dataclass(frozen=True)
class Occurrence:
    uid: str
    start: datetime          # aware (u celodenních půlnoc v zóně dashboardu)
    end: datetime
    all_day: bool
    summary: str
    location: str


def unfold(text: str) -> list[str]:
    lines: list[str] = []
    for raw in text.replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        if raw[:1] in (" ", "\t") and lines:
            lines[-1] += raw[1:]
        elif raw:
            lines.append(raw)
    return lines


_PARAM_RE = re.compile(r';([A-Za-z0-9-]+)=("[^"]*"|[^;:]*)')


def parse_line(line: str) -> tuple[str, dict[str, str], str]:
    """'DTSTART;TZID=Europe/Prague:20261112T090000' → ('DTSTART', {'TZID': 'Europe/Prague'}, '20261112T090000')."""
    in_quote = False
    for i, ch in enumerate(line):
        if ch == '"':
            in_quote = not in_quote
        elif ch == ":" and not in_quote:
            head, value = line[:i], line[i + 1:]
            break
    else:
        raise IcsError("content line without ':'")
    name, _, rest = head.partition(";")
    params = {k.upper(): v.strip('"') for k, v in _PARAM_RE.findall(";" + rest)} if rest else {}
    return name.upper(), params, value


def unescape(text: str) -> str:
    return re.sub(r"\\([\\;,nN])", lambda m: "\n" if m.group(1) in "nN" else m.group(1), text)


def _tz(params: dict[str, str], default_tz) -> object:
    tzid = params.get("TZID")
    if not tzid:
        return default_tz
    tzid = tzid.strip().lstrip("/")
    try:
        return ZoneInfo(WINDOWS_TZ.get(tzid, tzid))
    except (ZoneInfoNotFoundError, ValueError):
        return default_tz


def parse_value(value: str, params: dict[str, str], default_tz) -> tuple[datetime | date, bool]:
    """(datetime aware nebo date, all_day)."""
    value = value.strip()
    if params.get("VALUE") == "DATE" or re.fullmatch(r"\d{8}", value):
        return datetime.strptime(value[:8], "%Y%m%d").date(), True
    m = re.fullmatch(r"(\d{8})T(\d{6})(Z?)", value)
    if not m:
        raise IcsError(f"bad date-time {value!r}")
    naive = datetime.strptime(m.group(1) + m.group(2), "%Y%m%d%H%M%S")
    if m.group(3):
        return naive.replace(tzinfo=timezone.utc), False
    return naive.replace(tzinfo=_tz(params, default_tz)), False


_DUR_RE = re.compile(r"([+-])?P(?:(\d+)W)?(?:(\d+)D)?(?:T(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?)?$")


def parse_duration(text: str) -> timedelta:
    m = _DUR_RE.match(text.strip())
    if not m or text.strip() in ("P", "PT"):
        raise IcsError(f"bad duration {text!r}")
    sign, w, d, h, mi, s = m.groups()
    delta = timedelta(weeks=int(w or 0), days=int(d or 0), hours=int(h or 0), minutes=int(mi or 0), seconds=int(s or 0))
    return -delta if sign == "-" else delta


def parse_events(text: str, default_tz) -> list[dict]:
    """VEVENTy jako slovníky vlastností: {"UID", "SUMMARY", …, "_DTSTART": (value, params), "EXDATE": [...], …}."""
    events: list[dict] = []
    stack: list[str] = []
    current: dict | None = None
    for line in unfold(text):
        try:
            name, params, value = parse_line(line)
        except IcsError:
            continue
        if name == "BEGIN":
            stack.append(value.upper())
            if stack == ["VCALENDAR", "VEVENT"] or (len(stack) == 1 and value.upper() == "VEVENT"):
                current = {"EXDATE": [], "RDATE": []}
            continue
        if name == "END":
            if stack and stack[-1] == "VEVENT" and current is not None and len(stack) <= 2:
                events.append(current)
                current = None
                if len(events) > MAX_EVENTS:
                    raise IcsError("too many events")
            if stack:
                stack.pop()
            continue
        if current is None or not stack or stack[-1] != "VEVENT":
            continue  # vlastnosti kalendáře, VTIMEZONE, VALARM…
        if name in ("EXDATE", "RDATE"):
            current[name].extend((v, params) for v in value.split(",") if v.strip())
        elif name in ("DTSTART", "DTEND", "RECURRENCE-ID"):
            current[name] = (value, params)
        else:
            current[name] = unescape(value) if name in ("SUMMARY", "LOCATION", "DESCRIPTION") else value
    return events


def _rrule(text: str) -> dict[str, str]:
    parts = {}
    for chunk in text.split(";"):
        key, _, value = chunk.partition("=")
        if key:
            parts[key.strip().upper()] = value.strip()
    return parts


def _add_months(d: date, months: int) -> tuple[int, int]:
    total = d.year * 12 + (d.month - 1) + months
    return total // 12, total % 12 + 1


def _month_days(year: int, month: int) -> int:
    nxt = date(year + (month == 12), 1 if month == 12 else month + 1, 1)
    return (nxt - date(year, month, 1)).days


def _nth_weekday(year: int, month: int, weekday: int, nth: int) -> date | None:
    days = [date(year, month, d) for d in range(1, _month_days(year, month) + 1) if date(year, month, d).weekday() == weekday]
    if not days or nth == 0 or abs(nth) > len(days):
        return None
    return days[nth - 1] if nth > 0 else days[nth]


def _candidates(start: date, rule: dict[str, str], skip: int = 0):
    """Kandidátní dny opakování od `start` (vzestupně, nekonečný generátor), podle FREQ/INTERVAL/BY*."""
    freq = rule["FREQ"]
    interval = max(1, int(rule.get("INTERVAL", "1") or 1))
    byday = [b.strip() for b in rule.get("BYDAY", "").split(",") if b.strip()]
    bymonthday = [int(x) for x in rule.get("BYMONTHDAY", "").split(",") if x.strip()]
    bymonth = [int(x) for x in rule.get("BYMONTH", "").split(",") if x.strip()]
    if freq == "DAILY":
        k = skip
        while True:
            day = start + timedelta(days=k * interval)
            if not bymonth or day.month in bymonth:
                yield day
            k += 1
    elif freq == "WEEKLY":
        days = sorted({WEEKDAYS[b[-2:]] for b in byday}) if byday else [start.weekday()]
        week0 = start - timedelta(days=start.weekday())
        k = skip
        while True:
            week = week0 + timedelta(weeks=k * interval)
            for wd in days:
                day = week + timedelta(days=wd)
                if day >= start:
                    yield day
            k += 1
    elif freq == "MONTHLY":
        k = skip
        while True:
            year, month = _add_months(start.replace(day=1), k * interval)
            found = set()
            n = _month_days(year, month)
            for md in bymonthday or ([] if byday else [start.day]):
                real = md if md > 0 else n + md + 1
                if 1 <= real <= n:            # RFC 5545: neexistující den (31. v dubnu) se přeskočí
                    found.add(date(year, month, real))
            for b in byday:
                m = re.fullmatch(r"([+-]?\d{1,2})?(MO|TU|WE|TH|FR|SA|SU)", b)
                if not m:
                    continue
                if m.group(1):
                    d = _nth_weekday(year, month, WEEKDAYS[m.group(2)], int(m.group(1)))
                    if d:
                        found.add(d)
                else:
                    found.update(date(year, month, x) for x in range(1, n + 1)
                                 if date(year, month, x).weekday() == WEEKDAYS[m.group(2)])
            for day in sorted(found):
                if day >= start:
                    yield day
            k += 1
    elif freq == "YEARLY":
        k = skip
        months = bymonth or [start.month]
        while True:
            year = start.year + k * interval
            for month in sorted(months):
                if start.day <= _month_days(year, month):   # 29. 2. jen v přestupném roce
                    day = date(year, month, start.day)
                    if day >= start:
                        yield day
            k += 1


def _skip_periods(rule: dict[str, str], first: datetime, window_start: datetime, count: int | None) -> int:
    """Kolik celých period přeskočit, aby dlouholeté opakování (denní standup od roku 2015) nevyčerpalo limit kroků
    ještě před oknem. S COUNT se přeskakovat nesmí (počítá se od DTSTART)."""
    if count is not None:
        return 0
    gap = (window_start.astimezone(first.tzinfo).date() - first.date()).days
    if gap <= 0:
        return 0
    interval = max(1, int(rule.get("INTERVAL", "1") or 1))
    per_period = {"DAILY": 1, "WEEKLY": 7, "MONTHLY": 31, "YEARLY": 366}[rule["FREQ"]] * interval
    return max(0, gap // per_period - 1)


def _as_dt(value: datetime | date, all_day: bool, default_tz) -> datetime:
    if all_day:
        return datetime.combine(value, time(0), tzinfo=default_tz)
    return value  # type: ignore[return-value]


def expand(events: list[dict], window_start: datetime, window_end: datetime, default_tz) -> tuple[list[Occurrence], int]:
    """Výskyty, které zasahují do [window_start, window_end). Vrací (výskyty, počet nepodporovaných událostí)."""
    masters: dict[str, dict] = {}
    overrides: dict[tuple[str, str], dict] = {}
    unsupported = 0
    for ev in events:
        if "_bad" in ev or "DTSTART" not in ev:
            continue
        uid = ev.get("UID") or f"anon-{id(ev)}"
        if "RECURRENCE-ID" in ev:
            try:
                rid, rid_all = parse_value(ev["RECURRENCE-ID"][0], ev["RECURRENCE-ID"][1], default_tz)
            except IcsError:
                continue
            overrides[(uid, _key(_as_dt(rid, rid_all, default_tz)))] = ev
        else:
            masters[uid] = ev
    out: list[Occurrence] = []

    def occurrence(ev: dict, start: datetime, all_day: bool, length: timedelta, uid: str) -> None:
        end = start + length
        if end <= window_start or start >= window_end or ev.get("STATUS", "").upper() == "CANCELLED":
            return
        out.append(Occurrence(uid, start, end, all_day, (ev.get("SUMMARY") or "").strip() or "(bez názvu)",
                              (ev.get("LOCATION") or "").strip()))

    def span(ev: dict, start_v, all_day: bool) -> timedelta:
        if "DTEND" in ev:
            try:
                end_v, end_all = parse_value(ev["DTEND"][0], ev["DTEND"][1], default_tz)
                return _as_dt(end_v, end_all, default_tz) - _as_dt(start_v, all_day, default_tz)
            except IcsError:
                pass
        if "DURATION" in ev:
            try:
                return parse_duration(ev["DURATION"])
            except IcsError:
                pass
        return timedelta(days=1) if all_day else timedelta(0)

    for uid, ev in masters.items():
        try:
            start_v, all_day = parse_value(ev["DTSTART"][0], ev["DTSTART"][1], default_tz)
        except IcsError:
            continue
        length = max(span(ev, start_v, all_day), timedelta(0))
        first = _as_dt(start_v, all_day, default_tz)
        if ev.get("STATUS", "").upper() == "CANCELLED":
            continue
        exdates = set()
        for value, params in ev["EXDATE"]:
            try:
                ex_v, ex_all = parse_value(value, params, default_tz)
                exdates.add(_key(_as_dt(ex_v, ex_all, default_tz)))
            except IcsError:
                continue
        starts: list[datetime] = []
        if "RRULE" in ev:
            rule = _rrule(ev["RRULE"])
            if rule.get("FREQ") not in ("DAILY", "WEEKLY", "MONTHLY", "YEARLY") or set(rule) - SUPPORTED_RRULE:
                unsupported += 1
                continue
            count = int(rule["COUNT"]) if rule.get("COUNT", "").isdigit() else None
            until = None
            if rule.get("UNTIL"):
                try:
                    until_v, until_all = parse_value(rule["UNTIL"], {}, first.tzinfo)
                    until = _as_dt(until_v, until_all, first.tzinfo) + (timedelta(days=1) if until_all else timedelta(0))
                except IcsError:
                    until = None
            local_time = first.timetz() if not all_day else time(0)
            n = 0
            for steps, day in enumerate(_candidates(first.date(), rule, _skip_periods(rule, first, window_start, count))):
                if steps > MAX_EXPANSIONS:
                    log.debug("recurrence of %s truncated", uid)
                    break
                start = datetime.combine(day, local_time.replace(tzinfo=None), tzinfo=first.tzinfo)
                if until is not None and (start > until if not all_day else start >= until):
                    break
                n += 1
                if count is not None and n > count:
                    break
                if start >= window_end:
                    break
                starts.append(start)
        else:
            starts.append(first)
        for value, params in ev["RDATE"]:
            try:
                r_v, r_all = parse_value(value, params, default_tz)
                starts.append(_as_dt(r_v, r_all, default_tz))
            except IcsError:
                continue
        for start in starts:
            key = _key(start)
            if key in exdates:
                continue
            override = overrides.pop((uid, key), None)
            if override is not None:
                try:
                    o_v, o_all = parse_value(override["DTSTART"][0], override["DTSTART"][1], default_tz)
                except IcsError:
                    continue
                occurrence(override, _as_dt(o_v, o_all, default_tz), o_all, max(span(override, o_v, o_all), timedelta(0)), uid)
            else:
                occurrence(ev, start, all_day, length, uid)
    # přesunuté instance, jejichž původní termín je mimo okno (nebo bez mastera v tomto souboru)
    for (uid, _), ev in overrides.items():
        try:
            o_v, o_all = parse_value(ev["DTSTART"][0], ev["DTSTART"][1], default_tz)
        except IcsError:
            continue
        occurrence(ev, _as_dt(o_v, o_all, default_tz), o_all, max(span(ev, o_v, o_all), timedelta(0)), uid)
    return out, unsupported


def _key(dt: datetime) -> str:
    """Klíč instance (EXDATE/RECURRENCE-ID): okamžik v UTC."""
    return dt.astimezone(timezone.utc).strftime("%Y%m%dT%H%M%S")
