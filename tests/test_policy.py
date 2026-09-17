"""Server řídí akci (none/partial/full), režim plného refreshe, spánek a interval."""

from datetime import datetime

from musthave.policy import DeviceState, PolicyConfig, decide, power_mode

CFG = PolicyConfig(align_minutes=0)  # klasické intervaly; zarovnání má vlastní testy
NOW = datetime(2026, 9, 16, 21, 0)


def test_power_mode_from_voltage_and_override():
    assert power_mode(4.2, CFG) == "usb"
    assert power_mode(3.9, CFG) == "battery"
    assert power_mode(None, CFG) == "battery"
    assert power_mode(3.5, PolicyConfig(power="usb")) == "usb"
    assert power_mode(4.3, PolicyConfig(power="battery")) == "battery"


def test_no_frame_yet_means_none_with_short_retry():
    d = decide(DeviceState(), None, None, None, 3.9, NOW, CFG)
    assert d.action == "none" and d.refresh_rate == 60


def test_device_already_shows_latest():
    st = DeviceState(frame_id="f1", partials_since_full=3)
    d = decide(st, "f1", "f1", None, 3.9, NOW, CFG)
    assert d.action == "none" and d.sleep_mode == "deep" and d.refresh_rate == CFG.interval_battery


def test_unknown_reported_frame_forces_full():
    d = decide(DeviceState(frame_id="f0"), "unknown", "f1", 64, 4.2, NOW, CFG)
    assert d.action == "full" and d.full_mode == "fast" and d.sleep_mode == "light" and d.refresh_rate == CFG.interval_usb


def test_missing_rects_area_forces_full():
    d = decide(DeviceState(frame_id="f0"), "f0", "f1", None, 3.9, NOW, CFG)
    assert d.action == "full" and d.full_mode == "full"


def test_small_change_is_partial():
    d = decide(DeviceState(frame_id="f0", last_full_at=NOW.timestamp() - 60), "f0", "f1", 64, 3.9, NOW, CFG)
    assert d.action == "partial"


def test_large_change_is_full():
    big = int(800 * 480 * CFG.max_partial_area) + 8
    d = decide(DeviceState(frame_id="f0", last_full_at=NOW.timestamp() - 60), "f0", "f1", big, 3.9, NOW, CFG)
    assert d.action == "full"


def test_partial_counter_limit_forces_full():
    st = DeviceState(frame_id="f0", partials_since_full=CFG.full_after_partials, last_full_at=NOW.timestamp() - 60)
    assert decide(st, "f0", "f1", 64, 3.9, NOW, CFG).action == "full"


def test_hourly_full_refresh():
    st = DeviceState(frame_id="f0", partials_since_full=1, last_full_at=NOW.timestamp() - CFG.full_every_s - 1)
    assert decide(st, "f0", "f1", 64, 3.9, NOW, CFG).action == "full"


def test_first_request_after_night_time_is_full():
    before = datetime(2026, 9, 17, 3, 59).timestamp()
    st = DeviceState(frame_id="f0", partials_since_full=1, last_full_at=before)
    assert decide(st, "f0", "f1", 64, 3.9, datetime(2026, 9, 17, 4, 5), CFG).action == "full"
    st2 = DeviceState(frame_id="f0", partials_since_full=1, last_full_at=datetime(2026, 9, 17, 4, 1).timestamp())
    assert decide(st2, "f0", "f1", 64, 3.9, datetime(2026, 9, 17, 4, 5), CFG).action == "partial"


def test_never_did_full_yet_forces_full():
    st = DeviceState(frame_id="f0")
    assert decide(st, "f0", "f1", 64, 3.9, NOW, CFG).action == "full"


def test_refresh_rate_aligns_to_next_5_minute_boundary_with_lead():
    from musthave.policy import seconds_to_next_slot

    cfg = PolicyConfig(align_minutes=5, align_lead_s=10)
    # 21:02:30 → příští hranice 21:05:00, minus 10 s předstih = 140 s
    assert seconds_to_next_slot(datetime(2026, 9, 16, 21, 2, 30), cfg) == 140
    # těsně před hranicí (21:04:55) by vyšlo −5 s → přeskočit na další slot 21:10:00 − 10 s = 295 s
    assert seconds_to_next_slot(datetime(2026, 9, 16, 21, 4, 55), cfg) == 295
    # přesně na hranici 21:05:00 → další slot 21:10:00 − 10 s = 290 s
    assert seconds_to_next_slot(datetime(2026, 9, 16, 21, 5, 0), cfg) == 290


def test_decide_uses_aligned_refresh_rate_when_enabled():
    cfg = PolicyConfig(power="usb", align_minutes=5, align_lead_s=10)
    st = DeviceState(frame_id="f0", last_full_at=NOW.timestamp() - 60)
    d = decide(st, "f0", "f0", None, 4.8, datetime(2026, 9, 16, 21, 2, 30), cfg)
    assert d.action == "none" and d.refresh_rate == 140


def test_align_disabled_keeps_interval():
    cfg = PolicyConfig(power="usb", align_minutes=0)
    d = decide(DeviceState(), None, None, None, 4.8, NOW, cfg)
    assert d.refresh_rate == 60


def test_implausible_voltage_is_ignored():
    """ESP32-C3 neběží pod ~3 V; nižší hodnota je chyba měření (ADC), ne stav baterie."""
    from musthave.policy import VOLTAGE_PLAUSIBLE_MIN, plausible_voltage

    assert VOLTAGE_PLAUSIBLE_MIN == 3.0
    assert plausible_voltage(2.05) is None and plausible_voltage(0.0) is None and plausible_voltage(None) is None
    assert plausible_voltage(3.0) == 3.0 and plausible_voltage(4.75) == 4.75 and plausible_voltage(5.5) == 5.5
    assert plausible_voltage(6.0) is None and plausible_voltage(float("inf")) is None and plausible_voltage(float("nan")) is None
    assert power_mode(2.05, CFG) == "battery"
    assert power_mode(2.37, PolicyConfig(usb_voltage_min=2.0)) == "battery"  # ani nízký práh implausibilní hodnotu nepustí
