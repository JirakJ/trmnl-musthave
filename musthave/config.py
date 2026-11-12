"""Načtení config.toml + .env (+ volitelně Keychain) do neměnného Settings objektu."""

from __future__ import annotations

import os
import subprocess
import sys
import tomllib
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Callable, Mapping

from .agenda import AgendaConfig
from .alerts import AlertsConfig
from .layout import LayoutConfig, LayoutError, parse_layout
from .policy import PolicyConfig
from .reminders import ReminderError, RemindersConfig, parse_reminders
from .streams import MODES, StreamsConfig, parse_favorite

KEYCHAIN_SERVICE = "trmnl-musthave"
SECRET_NAMES = ("TRMNL_WEBHOOK_UUID", "TRMNL_USER_API_KEY")


@dataclass(frozen=True)
class Countdown:
    """Událost, ke které obrazovka odpočítává dny (např. vydání hry)."""

    name: str
    date: date
    yearly: bool = False  # opakuje se každý rok (Vánoce, narozeniny) – po uplynutí se posune na další rok

    def next_date(self, today: date) -> date:
        """Nejbližší výskyt od `today` včetně; jednorázová událost vrací své datum (i minulé)."""
        if not self.yearly or self.date >= today:
            return self.date
        for year in (today.year, today.year + 1):
            try:
                candidate = self.date.replace(year=year)
            except ValueError:  # 29. 2. v nepřestupném roce
                candidate = date(year, 2, 28)
            if candidate >= today:
                return candidate
        raise AssertionError("unreachable")


def parse_countdowns(raw: list) -> tuple[Countdown, ...]:
    """[[countdown]] name + date (+ volitelně yearly); TOML datum přijde jako date/datetime, řetězec jako ISO 'YYYY-MM-DD'."""
    out = []
    for entry in raw:
        value = entry["date"]
        if isinstance(value, datetime):
            value = value.date()
        elif not isinstance(value, date):
            value = date.fromisoformat(str(value))
        out.append(Countdown(str(entry["name"]).strip(), value, yearly=bool(entry.get("yearly", False))))
    return tuple(out)


@dataclass(frozen=True)
class Settings:
    location_name: str
    latitude: float
    longitude: float
    timezone: str
    kick: list[str]
    twitch: list[str]
    min_interval_s: int
    heartbeat_s: int
    webhook_uuid: str | None
    user_api_key: str | None
    plugin_setting_id: int | None
    state_path: Path
    server_port: int = 8080
    refresh_seconds: int = 300
    chrome: str | None = None
    image_format: str = "png"
    policy: PolicyConfig = PolicyConfig()
    firmware_dir: Path | None = None
    label: str = ""   # zdroj zobrazený na obrazovce (RPi / MacBook); prázdné = hostname
    headless: str = "auto"  # auto | new | old – režim headless Chromia (RPi s Chromium 126 potřebuje old)
    countdowns: tuple[Countdown, ...] = ()  # [[countdown]] – odpočty dnů do událostí
    alerts: AlertsConfig = AlertsConfig()   # [alerts] – výstrahy ČHMÚ (vypnuto, dokud není nastavena oblast)
    streams: StreamsConfig = StreamsConfig()  # [streams] mode / favorites / max_items / combined
    layout: LayoutConfig = LayoutConfig()     # [layout] panely, varianty, profily
    agenda: AgendaConfig = AgendaConfig()     # [agenda] – zdroje jen z config.local.toml / MUSTHAVE_ICS_URLS
    reminders: RemindersConfig = RemindersConfig()  # [[reminder]], [[priority]], [reminders]


class ConfigError(ValueError):
    """Neplatná hodnota v config.toml / config.local.toml (srozumitelná zpráva s názvem klíče)."""


def _choice(value, allowed: tuple[str, ...], key: str) -> str:
    text = str(value).strip().lower()
    if text not in allowed:
        raise ConfigError(f"{key}: neplatná hodnota {value!r}, povolené: {', '.join(allowed)}")
    return text


def _time_or_empty(value, key: str) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    try:
        hour, minute = (int(p) for p in text.split(":"))
        if not (0 <= hour < 24 and 0 <= minute < 60):
            raise ValueError
    except ValueError:
        raise ConfigError(f"{key}: čas musí být HH:MM, je {value!r}") from None
    return f"{hour:02d}:{minute:02d}"


def read_dotenv(path: Path) -> dict[str, str]:
    """Minimalistický parser KEY=VALUE řádků; komentáře a prázdné řádky ignoruje."""
    values: dict[str, str] = {}
    if not path.exists():
        return values
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        values[key.strip()] = value
    return values


def keychain_lookup(name: str) -> str | None:
    """macOS Keychain: security add-generic-password -s trmnl-musthave -a <NAME> -w <value>."""
    if sys.platform != "darwin":
        return None
    try:
        out = subprocess.run(
            ["security", "find-generic-password", "-s", KEYCHAIN_SERVICE, "-a", name, "-w"],
            capture_output=True, text=True, timeout=5, check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    return out.stdout.strip() or None if out.returncode == 0 else None


_USE_KEYCHAIN = object()


def load_settings(
    root: Path,
    env: Mapping[str, str] | None = None,
    secret_lookup: Callable[[str], str | None] | None | object = _USE_KEYCHAIN,
) -> Settings:
    """secret_lookup: None = žádné doplňování tajemství, jinak callable(name) -> hodnota; výchozí je Keychain."""
    env = os.environ if env is None else env
    if secret_lookup is _USE_KEYCHAIN:
        secret_lookup = keychain_lookup  # rozhoduje se až za běhu, aby šlo v testech nahradit
    raw = tomllib.loads((root / "config.toml").read_text(encoding="utf-8"))
    if isinstance(raw.get("agenda"), dict) and raw["agenda"].get("sources"):
        # soukromé adresy kalendářů nepatří do veřejného (gitovaného) config.toml
        raise ConfigError("[agenda] sources patří do config.local.toml nebo do MUSTHAVE_ICS_URLS, ne do config.toml")
    local = root / "config.local.toml"  # host-specifické hodnoty (port, label…), negitované a rsync je vynechá
    if local.exists():
        for table, values in tomllib.loads(local.read_text(encoding="utf-8")).items():
            if isinstance(values, dict):
                raw.setdefault(table, {}).update(values)
            elif isinstance(values, list) and isinstance(raw.get(table), list):
                raw[table] = raw[table] + values  # pole tabulek ([[countdown]]) se spojují, nepřepisují
            else:
                raw[table] = values
    merged = {**read_dotenv(root / ".env"), **env}
    for name in SECRET_NAMES:
        if not merged.get(name) and secret_lookup is not None:
            found = secret_lookup(name)
            if found:
                merged[name] = found
    loc, streams, send, trmnl = raw["location"], raw["streams"], raw.get("send", {}), raw.get("trmnl", {})
    server = raw.get("server", {})
    plugin_setting_id = trmnl.get("plugin_setting_id") or merged.get("TRMNL_PLUGIN_SETTING_ID")
    return Settings(
        location_name=loc["name"],
        latitude=float(loc["latitude"]),
        longitude=float(loc["longitude"]),
        timezone=loc.get("timezone", "Europe/Prague"),
        kick=[s.strip().lower() for s in streams.get("kick", [])],
        twitch=[s.strip().lower() for s in streams.get("twitch", [])],
        min_interval_s=int(send.get("min_interval_minutes", 6)) * 60,
        heartbeat_s=int(send.get("heartbeat_minutes", 15)) * 60,
        webhook_uuid=merged.get("TRMNL_WEBHOOK_UUID") or None,
        user_api_key=merged.get("TRMNL_USER_API_KEY") or None,
        plugin_setting_id=int(plugin_setting_id) if plugin_setting_id else None,
        state_path=root / "state" / "last.json",
        server_port=int(server.get("port", 8080)),
        refresh_seconds=int(server.get("refresh_seconds", 300)),
        chrome=server.get("chrome") or None,
        image_format=server.get("format", "png"),
        policy=PolicyConfig(
            power=str(server.get("power", "auto")),
            usb_voltage_min=float(server.get("usb_voltage_min", 4.15)),
            interval_usb=int(server.get("interval_usb", 60)),
            interval_battery=int(server.get("interval_battery", server.get("refresh_seconds", 300))),
            full_after_partials=int(server.get("full_after_partials", 12)),
            full_every_s=int(server.get("full_every_minutes", 60)) * 60,
            night_full_at=str(server.get("night_full_at", "04:00")),
            max_partial_area=float(server.get("max_partial_area", 0.4)),
            align_minutes=int(server.get("align_minutes", 5)),
            align_lead_s=int(server.get("align_lead_seconds", 10)),
            offline_tolerance_s=int(server.get("offline_tolerance_seconds", 600)),
            saving_mode=_choice(server.get("saving_mode", "auto"), ("auto", "off", "quiet", "low"), "saving_mode"),
            quiet_start=_time_or_empty(server.get("quiet_start", ""), "quiet_start"),
            quiet_end=_time_or_empty(server.get("quiet_end", ""), "quiet_end"),
            interval_quiet=int(server.get("interval_quiet", 1800)),
            quiet_on_usb=bool(server.get("quiet_on_usb", False)),
            low_battery_enter=int(server.get("low_battery_enter", 0)),
            low_battery_exit=int(server.get("low_battery_exit", 0)),
            interval_low_battery=int(server.get("interval_low_battery", 900)),
            max_refresh_s=int(server.get("max_refresh_seconds", 3600)),
        ),
        firmware_dir=root / "deploy" / "firmware",
        label=str(server.get("label", "")),
        headless=str(server.get("headless", "auto")),
        countdowns=parse_countdowns(raw.get("countdown", [])),
        alerts=parse_alerts(raw.get("alerts", {})),
        streams=(streams_cfg := parse_streams(streams)),
        layout=_parse_layout(raw.get("layout"), streams_cfg.combined),
        agenda=parse_agenda(raw.get("agenda", {}), merged.get("MUSTHAVE_ICS_URLS", "")),
        reminders=_parse_reminders(raw),
    )


def _parse_reminders(raw: dict) -> RemindersConfig:
    try:
        return parse_reminders(raw.get("reminder", []), raw.get("priority", []), raw.get("reminders", {}))
    except (ReminderError, TypeError, ValueError) as err:
        raise ConfigError(str(err)) from None


def parse_agenda(raw: dict, env_sources: str = "") -> AgendaConfig:
    """[agenda] (bez sources v config.toml) + sources z config.local.toml a/nebo MUSTHAVE_ICS_URLS."""
    if not isinstance(raw, dict):
        raise ConfigError("[agenda] musí být tabulka")
    sources = []
    listed = raw.get("sources", [])
    if isinstance(listed, str):
        listed = [listed]
    for entry in list(listed) + env_sources.split():
        text = str(entry).strip()
        if not text:
            continue
        if not (text.startswith(("https://", "http://", "webcal://", "webcals://", "/", "~/"))):
            raise ConfigError("[agenda] sources: zdroj musí být https/webcal URL nebo absolutní cesta k .ics")
        if text not in sources:
            sources.append(text)
    days = int(raw.get("days", 2))
    max_items = int(raw.get("max_items", 5))
    if not 1 <= days <= 7:
        raise ConfigError("[agenda] days: 1–7")
    if not 1 <= max_items <= 8:
        raise ConfigError("[agenda] max_items: 1–8")
    return AgendaConfig(sources=tuple(sources), days=days, max_items=max_items, private=bool(raw.get("private", False)),
                        show_location=bool(raw.get("show_location", True)),
                        refresh_minutes=max(5, int(raw.get("refresh_minutes", 15))),
                        max_age_hours=max(1, int(raw.get("max_age_hours", 12))))


def _parse_layout(raw, combined: bool) -> LayoutConfig:
    try:
        return parse_layout(raw, combined)
    except LayoutError as err:
        raise ConfigError(str(err)) from None


def parse_streams(raw: dict) -> StreamsConfig:
    mode = _choice(raw.get("mode", "all"), MODES, "[streams] mode")
    favorites = []
    for entry in raw.get("favorites", []):
        try:
            key = parse_favorite(entry)
        except ValueError as err:
            raise ConfigError(f"[streams] {err}") from None
        if key not in favorites:
            favorites.append(key)
    max_items = int(raw.get("max_items", 0))
    if max_items < 0:
        raise ConfigError("[streams] max_items musí být ≥ 0")
    return StreamsConfig(mode=mode, favorites=tuple(favorites), max_items=max_items, combined=bool(raw.get("combined", False)))


def parse_alerts(raw: dict) -> AlertsConfig:
    """[alerts]: area_codes = CISORP kódy ORP (číselník ČSÚ 65, Jihlava = "6105"); bez oblasti je funkce vypnutá."""
    if not isinstance(raw, dict):
        raise ConfigError("[alerts] musí být tabulka")
    codes = raw.get("area_codes", [])
    if isinstance(codes, (str, int)):
        codes = [codes]
    clean = []
    for code in codes:
        text = str(code).strip()
        if not text.isdigit() or len(text) != 4:
            raise ConfigError(f"[alerts] area_codes: {code!r} není čtyřmístný kód ORP (CISORP)")
        clean.append(text)
    feeds = tuple(str(f).strip() for f in raw.get("feeds", ["50"]))
    for feed in feeds:
        if feed not in ("50", "70"):
            raise ConfigError(f"[alerts] feeds: {feed!r}, povolené 50 (SIVS) a 70 (sucho)")
    enabled = bool(raw.get("enabled", True)) and bool(clean)
    return AlertsConfig(
        enabled=enabled, area_codes=tuple(clean), feeds=feeds or ("50",),
        refresh_minutes=max(5, int(raw.get("refresh_minutes", 15))),
        max_age_hours=max(1, int(raw.get("max_age_hours", 6))),
        horizon_hours=max(0, int(raw.get("horizon_hours", 24))),
        show_none=bool(raw.get("show_none", False)),
    )
