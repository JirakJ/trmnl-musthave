"""#8 Stav zařízení: RSSI, potvrzení snímku přes X-Frame-Id, očekávaný kontakt, stáří dat."""

import json
import threading
import urllib.request
from datetime import datetime, timedelta

import pytest

from musthave.devices import DeviceRegistry
from musthave.frames import BITMAP_BYTES, ROW_BYTES, FrameStore
from musthave.policy import DeviceState, PolicyConfig
from musthave.server import make_server
from musthave.status import availability, build_status, render_html, valid_rssi, wifi_percent

WHITE = b"\xff" * BITMAP_BYTES
MAC = "E0:72:A1:00:00:01"
CFG = PolicyConfig(power="battery", align_minutes=0, interval_battery=300, offline_tolerance_s=600)


def black_row(y):
    buf = bytearray(WHITE)
    buf[y * ROW_BYTES:(y + 1) * ROW_BYTES] = b"\x00" * ROW_BYTES
    return bytes(buf)


@pytest.fixture
def env(tmp_path):
    frames = FrameStore(tmp_path / "state" / "frames")
    devices = DeviceRegistry(tmp_path / "state" / "devices.json")
    clock = {"now": datetime(2026, 11, 12, 21, 0)}
    srv = make_server(frames, devices, CFG, port=0, clock=lambda: clock["now"], root=tmp_path)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield {"base": f"http://127.0.0.1:{srv.server_port}", "frames": frames, "devices": devices, "clock": clock, "root": tmp_path}
    srv.shutdown()


def get(url, headers=None):
    with urllib.request.urlopen(urllib.request.Request(url, headers=headers or {}), timeout=5) as r:
        return r.read()


def display(env, frame_id=None, rssi=None, voltage="3.9"):
    headers = {"ID": MAC, "Access-Token": "musthave-local", "Battery-Voltage": voltage, "FW-Version": "2.0.9"}
    if frame_id is not None:
        headers["X-Frame-Id"] = frame_id
    if rssi is not None:
        headers["RSSI"] = rssi
    return json.loads(get(env["base"] + "/api/display", headers))


def status(env):
    return json.loads(get(env["base"] + "/status.json"))


@pytest.mark.parametrize("raw,expected", [("-67", -67), ("-67.0", -67), ("0", None), ("12", None), ("", None),
                                          (None, None), ("abc", None), ("-200", None), ("nan", None)])
def test_valid_rssi(raw, expected):
    assert valid_rssi(raw) == expected


def test_wifi_percent_conversion_is_documented_linear_scale():
    assert wifi_percent(-100) == 0 and wifi_percent(-75) == 50 and wifi_percent(-50) == 100
    assert wifi_percent(-30) == 100 and wifi_percent(-110) == 0 and wifi_percent(None) is None


def test_first_start_without_any_measurement(env):
    """Server bez zařízení a bez dat: přehled to přizná, nespadne."""
    s = status(env)
    assert s["devices"] == [] and s["frame_rendered"] is None
    assert all(v["last_success"] is None for v in s["sources"].values())
    page = get(env["base"] + "/status").decode()
    assert "Žádné zařízení se zatím neohlásilo" in page and "zatím žádné úspěšné získání dat" in page


def test_offered_frame_is_confirmed_only_by_next_x_frame_id(env):
    f1 = env["frames"].put(WHITE)
    d = display(env, rssi="-61")
    assert d["action"] == "full"
    dev = status(env)["devices"][0]
    assert dev["frame_offered"] == f1 and dev["frame_acknowledged"] is None and not dev["offered_frame_confirmed"]
    env["clock"]["now"] += timedelta(seconds=300)
    display(env, frame_id=f1)
    dev = status(env)["devices"][0]
    assert dev["frame_acknowledged"] == f1 and dev["offered_frame_confirmed"]
    assert dev["frame_acknowledged_at"].startswith("2026-11-12T21:05")


def test_unconfirmed_frame_stays_unconfirmed_when_device_reports_old_frame(env):
    f1 = env["frames"].put(WHITE)
    display(env)
    display(env, frame_id=f1)                    # potvrdí f1
    f2 = env["frames"].put(black_row(10))
    display(env, frame_id=f1)                    # nabídnut f2
    display(env, frame_id=f1)                    # firmware dál hlásí f1 → f2 nepotvrzen
    dev = status(env)["devices"][0]
    assert dev["frame_offered"] == f2 and dev["frame_acknowledged"] == f1 and not dev["offered_frame_confirmed"]
    assert "zatím nepotvrzen" in get(env["base"] + "/status").decode()


def test_invalid_rssi_keeps_last_valid_measurement_and_time(env):
    env["frames"].put(WHITE)
    display(env, rssi="-72")
    env["clock"]["now"] += timedelta(minutes=5)
    display(env, rssi="0")
    display(env)
    dev = status(env)["devices"][0]
    assert dev["rssi_dbm"] == -72 and dev["wifi_percent"] == 56 and dev["rssi_measured"].startswith("2026-11-12T21:00")


def test_rssi_unknown_without_valid_measurement(env):
    env["frames"].put(WHITE)
    display(env, rssi="garbage")
    dev = status(env)["devices"][0]
    assert dev["rssi_dbm"] is None and dev["wifi_quality"] == "neznámý"
    assert "neznámý (žádné platné měření)" in get(env["base"] + "/status").decode()


def test_expected_next_contact_uses_interval_actually_sent(env):
    env["frames"].put(WHITE)
    d = display(env)
    dev = status(env)["devices"][0]
    assert dev["refresh_rate_s"] == d["refresh_rate"] == 300
    assert dev["next_expected"].startswith("2026-11-12T21:05:00")
    assert dev["firmware"] == "2.0.9" and dev["battery_percent"] is not None


def test_late_wakeup_is_late_then_inactive_only_after_tolerance(env):
    env["frames"].put(WHITE)
    display(env)
    env["clock"]["now"] += timedelta(seconds=299)
    assert status(env)["devices"][0]["availability"] == "aktivní"
    env["clock"]["now"] += timedelta(seconds=300)          # 10 min po kontaktu, očekáváno po 5 → v toleranci
    assert status(env)["devices"][0]["availability"] == "opožděné"
    env["clock"]["now"] += timedelta(seconds=600)
    assert status(env)["devices"][0]["availability"] == "neaktivní"


def test_long_deep_sleep_interval_is_not_an_outage():
    st = DeviceState(last_seen_at=1000.0, refresh_rate=3600, next_expected_at=4600.0)
    assert availability(st, 4000.0, CFG) == "aktivní"   # 50 min spánku při hodinovém intervalu
    assert availability(DeviceState(), 4000.0, CFG) == "nikdy neviděno"
    # starší záznam bez uloženého intervalu: odhad z konfigurace, ne falešný výpadek
    assert availability(DeviceState(last_seen_at=1000.0), 1200.0, CFG) == "aktivní"


def test_source_freshness_comes_from_last_successful_fetch(tmp_path):
    (tmp_path / "state").mkdir()
    (tmp_path / "state" / "last.json").write_text(json.dumps({
        "last_weather": {"ts": 1000.0, "weather": {"ok": True}}, "last_kick": None,
        "last_twitch": {"ts": 1900.0, "data": {"ok": True}}}), encoding="utf-8")
    frames = FrameStore(tmp_path / "state" / "frames")
    s = build_status(frames, DeviceRegistry(tmp_path / "state" / "devices.json"), CFG, 2000.0, root=tmp_path)
    assert s["sources"]["weather"]["age_s"] == 1000 and s["sources"]["twitch"]["age_s"] == 100
    assert s["sources"]["kick"] == {"last_success": None, "age_s": None}


def test_status_html_escapes_device_values():
    s = {"devices": [{"id": "<x>", "availability": "aktivní", "last_seen": None, "last_seen_age_s": None,
                      "next_expected": None, "refresh_rate_s": None, "firmware": "<script>", "battery_percent": None,
                      "battery_voltage": None, "battery_measured": None, "rssi_dbm": None, "wifi_percent": None,
                      "wifi_quality": "neznámý", "rssi_measured": None, "frame_offered": None, "frame_offered_at": None,
                      "frame_acknowledged": None, "frame_acknowledged_at": None, "offered_frame_confirmed": False}],
         "sources": {}, "frame_rendered": None}
    page = render_html(s)
    assert "<script>" not in page and "&lt;script&gt;" in page


def test_registry_reads_records_written_before_new_fields(tmp_path):
    path = tmp_path / "devices.json"
    path.write_text(json.dumps({MAC: {"frame_id": "f", "last_seen_at": 5.0, "partials_since_full": 1, "voltage": 3.8}}))
    st = DeviceRegistry(path).get(MAC)
    assert st.frame_id == "f" and st.rssi is None and st.acked_frame_id is None and st.next_expected_at is None
