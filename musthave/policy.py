"""Politika serveru pro zařízení: akce (none/partial/full), režim plného refreshe, spánek a interval."""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime, time, timedelta

from .frames import HEIGHT, WIDTH


@dataclass
class DeviceState:
    frame_id: str | None = None          # snímek, o kterém víme, že je na panelu (z X-Frame-Id)
    target_frame_id: str | None = None   # snímek, který jsme zařízení naposledy poslali (bude na panelu po překreslení)
    partials_since_full: int = 0
    last_full_at: float | None = None    # timestamp posledního plného refreshe
    last_seen_at: float | None = None
    fw_version: str | None = None
    voltage: float | None = None         # poslední věrohodné napětí baterie (V)
    voltage_at: float | None = None      # kdy bylo `voltage` změřeno
    rssi: int | None = None              # poslední platné RSSI Wi-Fi (dBm, hlavička RSSI)
    rssi_at: float | None = None         # kdy bylo `rssi` změřeno
    first_seen_at: float | None = None
    offered_at: float | None = None      # kdy server naposledy nabídl `target_frame_id` (action partial/full)
    acked_frame_id: str | None = None    # snímek potvrzený firmwarem: další dotaz nesl X-Frame-Id == nabídnutý
    acked_at: float | None = None
    refresh_rate: int | None = None      # interval, který zařízení naposledy dostalo (s)
    next_expected_at: float | None = None  # last_seen_at + refresh_rate – kdy čekáme další probuzení
    low_battery: bool = False            # režim nízké baterie (s hysterezí: zapne pod low_enter, vypne až nad low_exit)
    saving: str | None = None            # aktivní úsporný režim z posledního rozhodnutí (viz Decision.saving)
    saving_reason: str | None = None     # proč zařízení dostalo svůj interval (pro /status)


@dataclass(frozen=True)
class PolicyConfig:
    power: str = "auto"                  # auto | usb | battery
    usb_voltage_min: float = 4.15
    interval_usb: int = 60
    interval_battery: int = 300
    full_after_partials: int = 12
    full_every_s: int = 3600
    night_full_at: str = "04:00"
    max_partial_area: float = 0.4
    align_minutes: int = 5              # 0 = vypnuto; jinak probouzet v násobcích N minut (:00, :05, …)
    align_lead_s: int = 10              # předstih na probuzení + Wi-Fi, aby obrazovka byla hotová na hranici
    offline_tolerance_s: int = 600      # zařízení je „neaktivní“ až po next_expected_at + tolerance (deep sleep ≠ výpadek)
    # Úspora baterie (#11). Výchozí hodnoty = vypnuto, chování jako dřív.
    saving_mode: str = "auto"           # auto | off (nikdy nešetřit) | quiet | low (ručně vynucený režim)
    quiet_start: str = ""               # "23:00" – začátek doby klidu (místní čas); prázdné = vypnuto
    quiet_end: str = ""                 # "06:00" – konec (může být přes půlnoc)
    interval_quiet: int = 1800          # s, interval v době klidu (probuzení nejpozději na jejím konci)
    quiet_on_usb: bool = False          # doba klidu i na USB (výchozí jen na baterii)
    low_battery_enter: int = 0          # % – pod/na této hodnotě zapnout úsporu; 0 = vypnuto
    low_battery_exit: int = 0           # % – vypnout až nad touto hodnotou (hystereze); 0 = enter + 5
    interval_low_battery: int = 900     # s, interval při nízké baterii
    max_refresh_s: int = 3600           # strop refresh_rate posílaného zařízení


@dataclass(frozen=True)
class Decision:
    action: str          # none | partial | full
    full_mode: str       # full | fast
    sleep_mode: str      # deep | light
    refresh_rate: int
    saving: str = "none"         # none | quiet | low | quiet+low – aktivní úsporný režim
    low_battery: bool = False    # nový stav hystereze nízké baterie (server ho uloží do DeviceState)
    reason: str = ""             # krátké vysvětlení intervalu pro /status


VOLTAGE_PLAUSIBLE_MIN = 3.0  # V; ESP32-C3 pod ~3 V neběží, nižší hodnota je chyba měření (ADC), ne stav baterie
VOLTAGE_PLAUSIBLE_MAX = 5.5  # V; nad USB napětím už jde jen o chybu měření nebo podvrženou hlavičku


def plausible_voltage(voltage: float | None) -> float | None:
    """Napětí z hlavičky Battery-Voltage, nebo None, když je fyzikálně nemožné (poloviční čtení ADC, NaN, inf)."""
    if voltage is None or not math.isfinite(voltage) or not VOLTAGE_PLAUSIBLE_MIN <= voltage <= VOLTAGE_PLAUSIBLE_MAX:
        return None
    return voltage


def power_mode(voltage: float | None, cfg: PolicyConfig) -> str:
    if cfg.power in ("usb", "battery"):
        return cfg.power
    voltage = plausible_voltage(voltage)
    return "usb" if voltage is not None and voltage >= cfg.usb_voltage_min else "battery"


def _night_boundary(now: datetime, hhmm: str) -> float:
    hour, minute = (int(p) for p in hhmm.split(":"))
    boundary = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
    if boundary > now:
        boundary -= timedelta(days=1)
    return boundary.timestamp()


def seconds_to_next_slot(now: datetime, cfg: PolicyConfig) -> int:
    """Sekundy do (příští N-minutová hranice − předstih). Když by vyšlo < 20 s, vezme se další slot."""
    slot = cfg.align_minutes * 60
    epoch = int(now.timestamp())
    nxt = (epoch // slot + 1) * slot
    wait = nxt - epoch - cfg.align_lead_s
    if wait < 20:
        wait += slot
    return wait


def battery_percent(voltage: float | None) -> int | None:
    """Odhad pro TRMNL OG podle https://help.trmnl.com/en/articles/10556850-device-battery-faq.

    Chybějící/neplatné měření není prázdná baterie (None, nikdy 0 %). Horní pásma odpovídají odhadu v TRMNL účtu.
    """
    voltage = plausible_voltage(voltage)
    if voltage is None:
        return None
    percent = round((voltage - 3.0) / 0.012, 2)
    if percent >= 88:
        return 100
    if percent >= 85:
        return 95
    if percent >= 83:
        return 90
    return round(percent) if percent >= 10 else 1


def _hhmm(text: str) -> time | None:
    try:
        hour, minute = (int(p) for p in text.strip().split(":"))
        return time(hour, minute)
    except (ValueError, AttributeError):
        return None


def quiet_window_end(now: datetime, start: str, end: str) -> datetime | None:
    """Konec doby klidu, pokud do ní `now` spadá (jinak None). Okno může jít přes půlnoc (23:00–06:00).

    Počítá se v časové zóně `now` (ZoneInfo): konec 06:00 je skutečných 06:00 místního času i v noci se změnou
    letního času, takže zařízení se po konci okna vrátí k běžnému intervalu."""
    s, e = _hhmm(start), _hhmm(end)
    if s is None or e is None or s == e:
        return None
    t = now.time().replace(tzinfo=None)
    if s < e:
        inside, end_date = s <= t < e, now.date()
    else:
        inside = t >= s or t < e
        end_date = now.date() if t < e else now.date() + timedelta(days=1)
    if not inside:
        return None
    return datetime.combine(end_date, e, tzinfo=now.tzinfo)


def low_battery_state(voltage: float | None, was_low: bool, cfg: PolicyConfig) -> bool:
    """Hystereze: zapnout při ≤ enter %, vypnout až při ≥ exit %. Neznámé napětí stav nemění (žádných falešných 0 %)."""
    if cfg.low_battery_enter <= 0:
        return False
    pct = battery_percent(voltage)
    if pct is None:
        return was_low
    exit_pct = cfg.low_battery_exit if cfg.low_battery_exit > cfg.low_battery_enter else cfg.low_battery_enter + 5
    return pct < exit_pct if was_low else pct <= cfg.low_battery_enter


def _align_long(now: datetime, seconds: int, cfg: PolicyConfig) -> int:
    """Delší interval zarovnaný dolů na N-minutovou hranici − předstih (když zarovnání vychází rozumně)."""
    if cfg.align_minutes <= 0:
        return seconds
    slot = cfg.align_minutes * 60
    target = int(now.timestamp()) + seconds
    aligned = (target // slot) * slot - cfg.align_lead_s - int(now.timestamp())
    return aligned if aligned >= max(20, seconds - slot) else seconds


def saving_interval(state: DeviceState, voltage: float | None, mode: str, now: datetime, cfg: PolicyConfig,
                    normal: int) -> tuple[int, str, bool, str]:
    """(interval, saving, low_battery, reason). Pořadí: vynucený režim → napájení → nízká baterie → doba klidu.

    (OTA čekání má přednost před vším a řeší ho server.)"""
    low = low_battery_state(voltage, state.low_battery, cfg) if mode == "battery" else False
    forced = cfg.saving_mode if cfg.saving_mode in ("off", "quiet", "low") else "auto"
    if forced == "off":
        return normal, "none", low, "úspora vypnuta v konfiguraci"
    quiet_end = quiet_window_end(now, cfg.quiet_start, cfg.quiet_end)
    if quiet_end is not None and quiet_end.timestamp() - now.timestamp() <= cfg.align_lead_s + 30:
        quiet_end = None  # probuzení naplánované na konec okna: už běžný režim, ne další krátký „noční“ cyklus
    in_quiet = quiet_end is not None and (mode == "battery" or cfg.quiet_on_usb)
    if forced == "auto" and mode == "usb" and not in_quiet:
        return normal, "none", low, "napájení z USB"
    use_low = forced == "low" or (forced == "auto" and low)
    use_quiet = forced == "quiet" or (forced == "auto" and in_quiet)
    interval, parts, reasons, capped = normal, [], [], False
    if use_low:
        interval = max(interval, cfg.interval_low_battery)
        parts.append("low")
        reasons.append("nízká baterie" if forced == "auto" else "vynucená úspora baterie")
    if use_quiet:
        quiet = cfg.interval_quiet
        if quiet_end is not None:  # nikdy nepřespat konec okna: probuzení nejpozději na konci doby klidu
            until_end = max(20, int(quiet_end.timestamp() - now.timestamp()) - cfg.align_lead_s)
            if until_end < quiet:
                quiet, capped = until_end, True
        if quiet > interval:
            interval = quiet
        else:
            capped = False  # rozhoduje delší interval nízké baterie
        parts.insert(0, "quiet")
        reasons.insert(0, "doba klidu" if forced == "auto" else "vynucená doba klidu")
    if not parts:
        return normal, "none", low, "běžný režim"
    interval = min(interval, cfg.max_refresh_s)
    if not capped and interval > normal:
        interval = _align_long(now, interval, cfg)
    return interval, "+".join(parts), low, ", ".join(reasons)


def decide(
    state: DeviceState,
    reported_frame: str | None,
    latest: str | None,
    rects_area: int | None,
    voltage: float | None,
    now: datetime,
    cfg: PolicyConfig,
) -> Decision:
    mode = power_mode(voltage, cfg)
    sleep_mode = "light" if mode == "usb" else "deep"
    full_mode = "fast" if mode == "usb" else "full"
    interval = cfg.interval_usb if mode == "usb" else cfg.interval_battery
    if cfg.align_minutes > 0:
        interval = seconds_to_next_slot(now, cfg)
    battery_voltage = plausible_voltage(voltage)
    if battery_voltage is None:
        battery_voltage = state.voltage  # poslední věrohodné měření; bez něj hystereze drží předchozí stav
    interval, saving, low, reason = saving_interval(state, battery_voltage, mode, now, cfg, interval)

    def mk(action: str, rate: int | None = None) -> Decision:
        return Decision(action, full_mode, sleep_mode, rate if rate is not None else interval, saving, low, reason)

    if latest is None:
        return mk("none", 60)
    if reported_frame == latest:
        return mk("none")

    def full() -> Decision:
        return mk("full")

    if not reported_frame or state.frame_id != reported_frame or rects_area is None:
        return full()
    if state.last_full_at is None:
        return full()
    if rects_area > WIDTH * HEIGHT * cfg.max_partial_area:
        return full()
    if state.partials_since_full >= cfg.full_after_partials:
        return full()
    if now.timestamp() - state.last_full_at >= cfg.full_every_s:
        return full()
    if state.last_full_at < _night_boundary(now, cfg.night_full_at):
        return full()
    return mk("partial")
