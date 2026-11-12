"""Denní přehled: osobní priority a opakované připomínky (panel `reminders`), oddělené od odpočtů (`[[countdown]]`).

Význam termínu:
- `date = 2026-11-20` – jednorázová připomínka v ten den,
- `weekly = "út"` (nebo seznam dnů) – každý týden v daný den,
- `monthly = 15` – každý měsíc v daný den; pro 29.–31. v kratším měsíci platí `short_month`:
  "last" (výchozí) = poslední den měsíce (31. → 30. 4., 28./29. 2.), "skip" = ten měsíc se vynechá.
- `lead_days = N` – výskyt se ukazuje už N dní předem („za 2 dny“); bez něj jen v daný den.
Výskyt je vidět od (termín − lead_days) do konce dne termínu; další den zmizí a nepřenáší se. Splnění se ukládá
pro konkrétní výskyt (`state/done.json`, klíč jméno + datum) – příští týden je připomínka znovu nesplněná.
Vše se počítá v místním kalendářním datu (timezone z [location]).
"""

from __future__ import annotations

import calendar
import json
import os
import threading
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path

DAY_NAMES = {"po": 0, "út": 1, "ut": 1, "st": 2, "čt": 3, "ct": 3, "pá": 4, "pa": 4, "so": 5, "ne": 6,
             "mon": 0, "tue": 1, "wed": 2, "thu": 3, "fri": 4, "sat": 5, "sun": 6}
TEXT_MAX = 40
MAX_PRIORITIES = 3
DONE_KEEP_DAYS = 120
_LOCK = threading.Lock()


class ReminderError(ValueError):
    pass


@dataclass(frozen=True)
class Reminder:
    name: str
    kind: str                    # once | weekly | monthly
    on_date: date | None = None
    weekdays: tuple[int, ...] = ()
    month_day: int = 0
    short_month: str = "last"    # last | skip
    lead_days: int = 0

    def occurrences(self, start: date, end: date):
        """Termíny v intervalu [start, end] vzestupně."""
        if self.kind == "once":
            if self.on_date and start <= self.on_date <= end:
                yield self.on_date
            return
        if self.kind == "weekly":
            day = start
            while day <= end:
                if day.weekday() in self.weekdays:
                    yield day
                day += timedelta(days=1)
            return
        year, month = start.year, start.month
        while date(year, month, 1) <= end:
            last = calendar.monthrange(year, month)[1]
            if self.month_day <= last:
                when = date(year, month, self.month_day)
            elif self.short_month == "last":
                when = date(year, month, last)
            else:
                when = None
            if when and start <= when <= end:
                yield when
            year, month = (year + 1, 1) if month == 12 else (year, month + 1)


@dataclass(frozen=True)
class Priority:
    text: str
    on_date: date | None = None  # None = každý den


@dataclass(frozen=True)
class RemindersConfig:
    reminders: tuple[Reminder, ...] = ()
    priorities: tuple[Priority, ...] = ()
    max_items: int = 5

    @property
    def enabled(self) -> bool:
        return bool(self.reminders or self.priorities)


def _as_date(value, where: str) -> date:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    try:
        return date.fromisoformat(str(value))
    except ValueError:
        raise ReminderError(f"{where}: datum musí být YYYY-MM-DD, je {value!r}") from None


def parse_reminders(reminders: list, priorities: list, raw_cfg: dict | None = None) -> RemindersConfig:
    out = []
    for i, entry in enumerate(reminders or []):
        where = f"[[reminder]] #{i + 1}"
        if not isinstance(entry, dict):
            raise ReminderError(f"{where}: očekávám tabulku")
        name = str(entry.get("name", "")).strip()
        if not name:
            raise ReminderError(f"{where}: chybí name")
        kinds = [k for k in ("date", "weekly", "monthly") if k in entry]
        if len(kinds) != 1:
            raise ReminderError(f"{where} ({name}): uveď právě jedno z date / weekly / monthly")
        lead = int(entry.get("lead_days", 0))
        if not 0 <= lead <= 30:
            raise ReminderError(f"{where} ({name}): lead_days 0–30")
        if kinds[0] == "date":
            out.append(Reminder(name, "once", on_date=_as_date(entry["date"], where), lead_days=lead))
        elif kinds[0] == "weekly":
            days = entry["weekly"]
            days = [days] if isinstance(days, str) else list(days)
            try:
                weekdays = tuple(sorted({DAY_NAMES[str(d).strip().lower()] for d in days}))
            except KeyError:
                raise ReminderError(f"{where} ({name}): neznámý den v {days!r} (po … ne)") from None
            if not weekdays:
                raise ReminderError(f"{where} ({name}): weekly bez dnů")
            out.append(Reminder(name, "weekly", weekdays=weekdays, lead_days=lead))
        else:
            day = entry["monthly"]
            if not isinstance(day, int) or not 1 <= day <= 31:
                raise ReminderError(f"{where} ({name}): monthly = den v měsíci 1–31")
            short = str(entry.get("short_month", "last")).strip().lower()
            if short not in ("last", "skip"):
                raise ReminderError(f"{where} ({name}): short_month = last | skip")
            out.append(Reminder(name, "monthly", month_day=day, short_month=short, lead_days=lead))
    prios = []
    for i, entry in enumerate(priorities or []):
        if isinstance(entry, str):
            entry = {"text": entry}
        text = str(entry.get("text", "")).strip() if isinstance(entry, dict) else ""
        if not text:
            raise ReminderError(f"[[priority]] #{i + 1}: chybí text")
        prios.append(Priority(text, _as_date(entry["date"], f"[[priority]] #{i + 1}") if entry.get("date") else None))
    max_items = int((raw_cfg or {}).get("max_items", 5))
    if not 1 <= max_items <= 8:
        raise ReminderError("[reminders] max_items 1–8")
    return RemindersConfig(tuple(out), tuple(prios), max_items)


def occurrence_key(name: str, when: date) -> str:
    return f"{name}|{when.isoformat()}"


class DoneStore:
    """Splnění konkrétních výskytů (JSON, atomický zápis); staré záznamy se po DONE_KEEP_DAYS zahodí."""

    def __init__(self, path: Path) -> None:
        self.path = Path(path)

    def load(self) -> dict[str, float]:
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            return {str(k): float(v) for k, v in data.items()} if isinstance(data, dict) else {}
        except (FileNotFoundError, ValueError, OSError):
            return {}

    def set(self, key: str, done: bool, now: datetime) -> None:
        with _LOCK:
            data = self.load()
            if done:
                data[key] = now.timestamp()
            else:
                data.pop(key, None)
            cutoff = (now.date() - timedelta(days=DONE_KEEP_DAYS)).isoformat()
            data = {k: v for k, v in data.items() if k.rsplit("|", 1)[-1] >= cutoff}
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_name(f".{self.path.name}.{os.getpid()}.tmp")
            tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
            os.replace(tmp, self.path)


def _clip(text: str) -> str:
    return text if len(text) <= TEXT_MAX else text[: TEXT_MAX - 1] + "…"


def _when(day: date, today: date) -> str:
    delta = (day - today).days
    if delta == 0:
        return "dnes"
    if delta == 1:
        return "zítra"
    if delta < 5:
        return f"za {delta} dny"
    return f"{day.day}. {day.month}."


def upcoming(cfg: RemindersConfig, today: date) -> list[tuple[date, int, Reminder]]:
    """(termín, pořadí v konfiguraci, připomínka) viditelné dnes – stabilně: termín → pořadí v konfiguraci."""
    rows = []
    for index, rem in enumerate(cfg.reminders):
        for when in rem.occurrences(today, today + timedelta(days=rem.lead_days)):
            rows.append((when, index, rem))
    rows.sort(key=lambda r: (r[0], r[1]))
    return rows


def reminders_panel(cfg: RemindersConfig, now: datetime, done: dict[str, float] | None = None) -> dict | None:
    """Panel: {"ok", "priorities": [...], "items": [{n, when, today, done?}], "more"} nebo None (nenastaveno)."""
    if not cfg.enabled:
        return None
    today = now.date()
    done = done or {}
    prios = [_clip(p.text) for p in cfg.priorities if p.on_date in (None, today)][:MAX_PRIORITIES]
    items = []
    for when, _, rem in upcoming(cfg, today):
        key = occurrence_key(rem.name, when)
        item = {"n": _clip(rem.name), "when": _when(when, today)}
        if when == today:
            item["today"] = True
        if key in done:
            item["done"] = True
        items.append(item)
    # splněné až na konec (stabilně), aby nesplněné měly přednost v omezeném místě
    items = [i for i in items if not i.get("done")] + [i for i in items if i.get("done")]
    out = {"ok": True, "priorities": prios, "items": items[: cfg.max_items]}
    if len(items) > cfg.max_items:
        out["more"] = len(items) - cfg.max_items
    return out
