"""Rozložení full layoutu: volitelné panely, jejich pořadí a šířky, skrývání prázdných panelů a časové profily.

Bez `[layout]` v konfiguraci zůstává původní rozložení (počasí + hodinová předpověď, sloupce Kick a Twitch,
odpočet). Profily (`[[layout.profile]]`) se vybírají podle místního času a dne v týdnu:
- okno `from`–`to` může jít přes půlnoc; den (`days`) se vztahuje ke dni, kdy okno ZAČALO (pátek 22:00–02:00
  platí i v sobotu ve 01:00),
- při překryvu vyhrává první vyhovující profil v pořadí konfigurace, bez shody platí základní `[layout]`,
- přepnutí profilu není carousel: nový profil se převezme nejdřív `min_switch_minutes` po posledním přepnutí,
  a protože stejná data dávají stejný snímek, zařízení se kvůli profilu nepřekresluje víc než jednou.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime, time, timedelta

PANEL_TITLES = {
    "kick": ("KICK", "Kick nedostupný"),
    "twitch": ("TWITCH", "Twitch nedostupný"),
    "streams": ("STREAMY", "Streamy nedostupné"),
}
WEATHER_VARIANTS = ("full", "compact", "off")
MAX_PANELS = 3
DAYS = {"po": 0, "ut": 1, "út": 1, "st": 2, "ct": 3, "čt": 3, "pa": 4, "pá": 4, "so": 5, "ne": 6,
        "mon": 0, "tue": 1, "wed": 2, "thu": 3, "fri": 4, "sat": 5, "sun": 6}


class LayoutError(ValueError):
    pass


@dataclass(frozen=True)
class View:
    """Co se má vykreslit (základní rozložení nebo profil)."""
    panels: tuple[str, ...] = ("kick", "twitch")
    widths: tuple[tuple[str, int], ...] = ()      # (panel, flex-grow 1–3)
    weather: str = "full"                         # full (vč. hodinové předpovědi) | compact | off
    countdown: bool = True
    hide_empty: tuple[str, ...] = ()              # panely, které se při prázdném obsahu skryjí
    shrink_empty: tuple[str, ...] = ()            # panely, které se při prázdném obsahu zmenší na hlavičku

    def width(self, panel: str) -> int:
        return dict(self.widths).get(panel, 1)


@dataclass(frozen=True)
class Profile:
    name: str
    days: frozenset[int]
    start: time
    end: time
    view: View


@dataclass(frozen=True)
class LayoutConfig:
    base: View = View()
    profiles: tuple[Profile, ...] = ()
    min_switch_minutes: int = 30
    configured: bool = False                     # [layout] v konfiguraci (jinak se řídí [streams] combined)


def _hhmm(text, key: str) -> time:
    try:
        hour, minute = (int(p) for p in str(text).strip().split(":"))
        return time(hour, minute)
    except (ValueError, TypeError):
        raise LayoutError(f"{key}: čas musí být HH:MM, je {text!r}") from None


def _names(value, key: str, allowed) -> tuple[str, ...]:
    if isinstance(value, str):
        value = [value]
    if not isinstance(value, list):
        raise LayoutError(f"{key}: očekávám seznam")
    out = []
    for item in value:
        name = str(item).strip().lower()
        if name not in allowed:
            raise LayoutError(f"{key}: neznámý panel {item!r} (povolené: {', '.join(sorted(allowed))})")
        if name in out:
            raise LayoutError(f"{key}: panel {item!r} je uvedený dvakrát")
        out.append(name)
    return tuple(out)


def _view(raw: dict, base: View, where: str) -> View:
    view = base
    if "panels" in raw:
        panels = _names(raw["panels"], f"{where}.panels", PANEL_TITLES)
        if not panels:
            raise LayoutError(f"{where}.panels: aspoň jeden panel")
        if len(panels) > MAX_PANELS:
            raise LayoutError(f"{where}.panels: nejvýš {MAX_PANELS} sloupce (800 px), je {len(panels)}")
        if {"kick", "streams"} <= set(panels) or {"twitch", "streams"} <= set(panels):
            raise LayoutError(f"{where}.panels: 'streams' (společný seznam) nelze kombinovat s 'kick'/'twitch'")
        view = replace(view, panels=panels)
    if "widths" in raw:
        widths = raw["widths"]
        if not isinstance(widths, dict):
            raise LayoutError(f"{where}.widths: očekávám tabulku panel = 1–3")
        clean = []
        for name, value in widths.items():
            _names([name], f"{where}.widths", PANEL_TITLES)
            if not isinstance(value, int) or not 1 <= value <= 3:
                raise LayoutError(f"{where}.widths.{name}: šířka 1–3, je {value!r}")
            clean.append((name, value))
        view = replace(view, widths=tuple(clean))
    if "weather" in raw:
        weather = str(raw["weather"]).strip().lower()
        if weather not in WEATHER_VARIANTS:
            raise LayoutError(f"{where}.weather: {raw['weather']!r}, povolené: {', '.join(WEATHER_VARIANTS)}")
        view = replace(view, weather=weather)
    if "countdown" in raw:
        view = replace(view, countdown=bool(raw["countdown"]))
    for key in ("hide_empty", "shrink_empty"):
        if key in raw:
            view = replace(view, **{key: _names(raw[key], f"{where}.{key}", PANEL_TITLES)})
    if set(view.hide_empty) & set(view.shrink_empty):
        raise LayoutError(f"{where}: panel nemůže být zároveň v hide_empty i shrink_empty")
    return view


def _days(value, where: str) -> frozenset[int]:
    if value is None:
        return frozenset(range(7))
    if isinstance(value, str):
        value = [value]
    out = set()
    for item in value:
        key = str(item).strip().lower()
        if key not in DAYS:
            raise LayoutError(f"{where}.days: neznámý den {item!r} (po, út, st, čt, pá, so, ne)")
        out.add(DAYS[key])
    if not out:
        raise LayoutError(f"{where}.days: aspoň jeden den")
    return frozenset(out)


def parse_layout(raw: dict | None, combined_streams: bool = False) -> LayoutConfig:
    """[layout] + [[layout.profile]] → LayoutConfig. Neplatná kombinace = LayoutError se srozumitelnou zprávou."""
    default = View(panels=("streams",) if combined_streams else ("kick", "twitch"))
    if not raw:
        return LayoutConfig(base=default)
    if not isinstance(raw, dict):
        raise LayoutError("[layout] musí být tabulka")
    known = {"panels", "widths", "weather", "countdown", "hide_empty", "shrink_empty", "profile", "min_switch_minutes"}
    unknown = set(raw) - known
    if unknown:
        raise LayoutError(f"[layout]: neznámé klíče {', '.join(sorted(unknown))}")
    base = _view(raw, default, "[layout]")
    profiles = []
    names = set()
    for i, prof in enumerate(raw.get("profile", []) or []):
        where = f"[[layout.profile]] #{i + 1}"
        if not isinstance(prof, dict):
            raise LayoutError(f"{where}: očekávám tabulku")
        name = str(prof.get("name", "")).strip()
        if not name:
            raise LayoutError(f"{where}: chybí name")
        if name in names:
            raise LayoutError(f"{where}: profil {name!r} je dvakrát")
        names.add(name)
        bad = set(prof) - (known - {"profile", "min_switch_minutes"}) - {"name", "days", "from", "to"}
        if bad:
            raise LayoutError(f"{where}: neznámé klíče {', '.join(sorted(bad))}")
        start = _hhmm(prof.get("from", "00:00"), f"{where}.from")
        end = _hhmm(prof.get("to", "00:00"), f"{where}.to")
        profiles.append(Profile(name, _days(prof.get("days"), where), start, end, _view(prof, base, where)))
    minutes = int(raw.get("min_switch_minutes", 30))
    if minutes < 0:
        raise LayoutError("[layout] min_switch_minutes ≥ 0")
    return LayoutConfig(base=base, profiles=tuple(profiles), min_switch_minutes=minutes, configured=True)


def profile_matches(profile: Profile, now: datetime) -> bool:
    t = now.time().replace(tzinfo=None)
    s, e = profile.start, profile.end
    if s == e:  # celý den
        return now.weekday() in profile.days
    if s < e:
        return s <= t < e and now.weekday() in profile.days
    if t >= s:                                   # večerní část okna přes půlnoc
        return now.weekday() in profile.days
    if t < e:                                    # ranní část patří ke dni, kdy okno začalo
        return (now - timedelta(days=1)).weekday() in profile.days
    return False


def wanted_profile(cfg: LayoutConfig, now: datetime) -> Profile | None:
    return next((p for p in cfg.profiles if profile_matches(p, now)), None)


def active_view(cfg: LayoutConfig, now: datetime, last: dict | None) -> tuple[View, str | None, dict]:
    """(view, jméno profilu nebo None, záznam k uložení {"profile", "since"}).

    Nový profil se převezme až `min_switch_minutes` po posledním přepnutí (žádný carousel)."""
    want = wanted_profile(cfg, now)
    want_name = want.name if want else None
    known = {p.name: p for p in cfg.profiles}
    now_ts = now.timestamp()
    last = last if isinstance(last, dict) else {}

    def view_of(name: str | None) -> View:
        return known[name].view if name in known else cfg.base

    current = last.get("profile")
    valid = "profile" in last and (current is None or current in known)  # profil mohl z konfigurace zmizet
    if valid and current == want_name:
        return view_of(current), current, last
    since = float(last.get("since") or 0)
    if valid and now_ts - since < cfg.min_switch_minutes * 60:
        return view_of(current), current, last   # čerstvě přepnuto: počkat (žádný carousel)
    return view_of(want_name), want_name, {"profile": want_name, "since": now_ts}


def is_empty(key: str, section: dict | None) -> bool:
    """Prázdný panel: streamy bez živého kanálu, agenda/připomínky bez položek. Nedostupný zdroj NENÍ prázdný."""
    if not section or not section.get("ok"):
        return False
    if key in ("kick", "twitch", "streams"):
        return int(section.get("live") or 0) == 0
    return not section.get("items")


def resolve_panels(view: View, sections: dict[str, dict | None]) -> list[dict]:
    """Panely k vykreslení: skryje/zmenší prázdné podle view; když by se skrylo vše, zůstane první panel."""
    out = []
    for key in view.panels:
        title, na = PANEL_TITLES[key]
        empty = is_empty(key, sections.get(key))
        if empty and key in view.hide_empty:
            continue
        panel = {"key": key, "title": title, "na": na, "w": view.width(key)}
        if empty and key in view.shrink_empty:
            panel["compact"] = True
            panel["w"] = 0
        out.append(panel)
    if not out and view.panels:
        key = view.panels[0]
        title, na = PANEL_TITLES[key]
        out.append({"key": key, "title": title, "na": na, "w": 1})
    if out and all(p["w"] == 0 for p in out):
        out[0]["w"] = 1
    return out
