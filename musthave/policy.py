"""Politika serveru pro zařízení: akce (none/partial/full), režim plného refreshe, spánek a interval."""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime, timedelta

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


@dataclass(frozen=True)
class Decision:
    action: str          # none | partial | full
    full_mode: str       # full | fast
    sleep_mode: str      # deep | light
    refresh_rate: int


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

    if latest is None:
        return Decision("none", full_mode, sleep_mode, 60)
    if reported_frame == latest:
        return Decision("none", full_mode, sleep_mode, interval)

    def full() -> Decision:
        return Decision("full", full_mode, sleep_mode, interval)

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
    return Decision("partial", full_mode, sleep_mode, interval)
