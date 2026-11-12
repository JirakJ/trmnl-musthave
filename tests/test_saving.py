"""#11 Výdrž baterie: doba klidu, nízká baterie s hysterezí, pořadí pravidel, půlnoc a změna času."""

from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from musthave.config import ConfigError, load_settings
from musthave.policy import (DeviceState, PolicyConfig, battery_percent, decide, low_battery_state,
                             quiet_window_end)

TZ = ZoneInfo("Europe/Prague")
BASE = PolicyConfig(power="battery", align_minutes=0, interval_battery=300)
QUIET = PolicyConfig(power="battery", align_minutes=0, interval_battery=300, quiet_start="23:00", quiet_end="06:00",
                     interval_quiet=1800)
LOW = PolicyConfig(power="battery", align_minutes=0, interval_battery=300, low_battery_enter=20, low_battery_exit=25,
                   interval_low_battery=900)
V_OK, V_LOW = 3.9, 3.2  # ~75 %, ~17 %


def at(h, m=0, day=12, month=11):
    return datetime(2026, month, day, h, m, tzinfo=TZ)


def rate(cfg, now, voltage=V_OK, state=None):
    return decide(state or DeviceState(), "f1", "f1", None, voltage, now, cfg)


def test_defaults_keep_previous_behaviour():
    d = rate(BASE, at(2))
    assert d.refresh_rate == 300 and d.saving == "none"
    assert rate(PolicyConfig(power="battery"), at(2, 2)).refresh_rate == 170  # zarovnání na 2:05 − 10 s jako dřív


def test_quiet_window_across_midnight():
    assert quiet_window_end(at(23, 30), "23:00", "06:00") == at(6, day=13)
    assert quiet_window_end(at(2), "23:00", "06:00") == at(6)
    assert quiet_window_end(at(6), "23:00", "06:00") is None
    assert quiet_window_end(at(12), "23:00", "06:00") is None
    assert quiet_window_end(at(13), "12:00", "14:00") == at(14)
    assert quiet_window_end(at(13), "", "06:00") is None


def test_quiet_interval_never_oversleeps_the_end_of_the_window():
    assert rate(QUIET, at(1)).refresh_rate == 1800 and rate(QUIET, at(1)).saving == "quiet"
    d = rate(QUIET, at(5, 50))                     # do konce 10 min → probudit se v 6:00 − předstih
    assert d.refresh_rate == 600 - QUIET.align_lead_s
    near_end = datetime(2026, 11, 12, 5, 59, 40, tzinfo=TZ)    # probuzení naplánované na konec okna (6:00 − 10 s ± drift)
    assert rate(QUIET, near_end).saving == "none" and rate(QUIET, near_end).refresh_rate == 300
    assert rate(QUIET, at(6, 1)).refresh_rate == 300


def test_quiet_window_end_respects_dst_change():
    # 25. 10. 2026 končí letní čas (03:00 CEST → 02:00 CET): mezi 01:00 a 06:00 je 6 skutečných hodin
    end = quiet_window_end(datetime(2026, 10, 25, 1, 0, tzinfo=TZ), "23:00", "06:00")
    assert end.timestamp() - datetime(2026, 10, 25, 1, 0, tzinfo=TZ).timestamp() == 6 * 3600
    d = rate(QUIET, datetime(2026, 10, 25, 5, 45, tzinfo=TZ))
    assert d.refresh_rate == 15 * 60 - QUIET.align_lead_s


def test_quiet_applies_on_battery_only_unless_configured():
    usb = PolicyConfig(**{**QUIET.__dict__, "power": "usb"})
    assert rate(usb, at(1)).refresh_rate == usb.interval_usb and rate(usb, at(1)).saving == "none"
    usb_quiet = PolicyConfig(**{**usb.__dict__, "quiet_on_usb": True})
    assert rate(usb_quiet, at(1)).refresh_rate == 1800


def test_low_battery_hysteresis():
    assert battery_percent(V_LOW) <= 20 < battery_percent(3.27) < 25 <= battery_percent(3.33)
    assert low_battery_state(V_LOW, False, LOW) is True
    assert low_battery_state(3.27, True, LOW) is True         # mezi prahy: zůstává
    assert low_battery_state(3.27, False, LOW) is False        # mezi prahy: nezapne se
    assert low_battery_state(3.33, True, LOW) is False         # nad exit: vypne se
    assert low_battery_state(None, True, LOW) is True          # neznámé napětí: stav se nemění, žádných 0 %
    assert low_battery_state(None, False, LOW) is False
    assert low_battery_state(V_LOW, False, BASE) is False      # funkce vypnutá


def test_low_battery_interval_and_unknown_voltage_uses_last_plausible():
    d = rate(LOW, at(12), V_LOW)
    assert d.refresh_rate == 900 and d.saving == "low" and d.low_battery
    st = DeviceState(voltage=V_LOW, low_battery=True)
    d2 = rate(LOW, at(12), 2.05, st)                          # nevěrohodné čtení → poslední věrohodné
    assert d2.saving == "low" and d2.low_battery


def test_order_forced_mode_then_power_then_low_then_quiet():
    both = PolicyConfig(**{**QUIET.__dict__, "low_battery_enter": 20, "interval_low_battery": 900})
    d = rate(both, at(1), V_LOW)
    assert d.saving == "quiet+low" and d.refresh_rate == 1800    # delší z obou
    assert rate(PolicyConfig(**{**both.__dict__, "saving_mode": "off"}), at(1), V_LOW).refresh_rate == 300
    forced = rate(PolicyConfig(**{**BASE.__dict__, "saving_mode": "low"}), at(12))
    assert forced.saving == "low" and forced.refresh_rate == 900
    usb = rate(PolicyConfig(**{**both.__dict__, "power": "usb"}), at(12), V_LOW)
    assert usb.saving == "none"                                  # na USB se baterie nešetří


def test_refresh_rate_is_capped_for_firmware():
    cfg = PolicyConfig(**{**QUIET.__dict__, "interval_quiet": 7200, "max_refresh_s": 3600})
    assert rate(cfg, at(23, 5)).refresh_rate == 3600


def test_long_interval_is_aligned_to_slots():
    cfg = PolicyConfig(**{**QUIET.__dict__, "align_minutes": 5, "align_lead_s": 10})
    d = rate(cfg, at(1, 2))
    wake = at(1, 2).timestamp() + d.refresh_rate
    assert (wake + 10) % 300 == 0 and 1500 <= d.refresh_rate <= 1800


def test_full_refresh_decisions_still_work_in_saving_mode():
    st = DeviceState(frame_id="f0")
    assert decide(st, "f0", "f1", 64, V_OK, at(1), QUIET).action == "full"


CONFIG = """
[location]
name = "X"
latitude = 1
longitude = 2
[streams]
[server]
{extra}
"""


def test_config_parsing_and_validation(tmp_path):
    (tmp_path / "config.toml").write_text(CONFIG.format(extra='quiet_start = "23:00"\nquiet_end = "6:00"\nlow_battery_enter = 15'))
    s = load_settings(tmp_path, env={}, secret_lookup=None)
    assert s.policy.quiet_start == "23:00" and s.policy.quiet_end == "06:00" and s.policy.low_battery_enter == 15
    (tmp_path / "config.toml").write_text(CONFIG.format(extra='quiet_start = "25:00"'))
    with pytest.raises(ConfigError, match="quiet_start"):
        load_settings(tmp_path, env={}, secret_lookup=None)
    (tmp_path / "config.toml").write_text(CONFIG.format(extra='saving_mode = "turbo"'))
    with pytest.raises(ConfigError, match="saving_mode"):
        load_settings(tmp_path, env={}, secret_lookup=None)


def test_server_persists_hysteresis_and_reports_mode(tmp_path):
    import json, threading, urllib.request
    from musthave.devices import DeviceRegistry
    from musthave.frames import BITMAP_BYTES, FrameStore
    from musthave.server import make_server

    frames = FrameStore(tmp_path / "state" / "frames")
    frames.put(b"\xff" * BITMAP_BYTES)
    devices = DeviceRegistry(tmp_path / "state" / "devices.json")
    srv = make_server(frames, devices, LOW, port=0, clock=lambda: at(12), root=tmp_path)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        def call(v):
            req = urllib.request.Request(f"http://127.0.0.1:{srv.server_port}/api/display",
                                         headers={"ID": "AA", "Access-Token": "musthave-local", "Battery-Voltage": v})
            with urllib.request.urlopen(req, timeout=5) as r:
                return json.loads(r.read())
        assert call("3.2")["refresh_rate"] == 900 and devices.get("AA").low_battery
        assert call("3.27")["refresh_rate"] == 900       # hystereze přes uložený stav
        assert call("3.4")["refresh_rate"] == 300 and not devices.get("AA").low_battery
        call("3.2")
        with urllib.request.urlopen(f"http://127.0.0.1:{srv.server_port}/status.json", timeout=5) as r:
            dev = json.loads(r.read())["devices"][0]
        assert dev["saving"] == "low" and dev["saving_reason"] == "nízká baterie" and dev["refresh_rate_s"] == 900
    finally:
        srv.shutdown()
