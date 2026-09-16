"""BYOS server – protokol v1 pro fork firmware + kompatibilita se stock firmware."""

import json
import threading
import urllib.error
import urllib.request
from datetime import datetime
from pathlib import Path

import pytest

from musthave.devices import DeviceRegistry
from musthave.diff import decode_regions
from musthave.frames import BITMAP_BYTES, ROW_BYTES, FrameStore
from musthave.policy import PolicyConfig
from musthave.server import make_server

WHITE = b"\xff" * BITMAP_BYTES
MAC = "E0:72:A1:00:00:01"


def with_black_row(y: int) -> bytes:
    buf = bytearray(WHITE)
    buf[y * ROW_BYTES:(y + 1) * ROW_BYTES] = b"\x00" * ROW_BYTES
    return bytes(buf)


@pytest.fixture
def env(tmp_path):
    frames = FrameStore(tmp_path / "frames")
    devices = DeviceRegistry(tmp_path / "devices.json")
    fw_dir = tmp_path / "firmware"
    fw_dir.mkdir()
    clock = {"now": datetime(2026, 9, 16, 21, 0)}
    srv = make_server(frames, devices, PolicyConfig(power="battery", align_minutes=0), port=0, firmware_dir=fw_dir, clock=lambda: clock["now"])
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield {"base": f"http://127.0.0.1:{srv.server_port}", "frames": frames, "devices": devices, "fw": fw_dir, "clock": clock}
    srv.shutdown()


def get(url, headers=None):
    req = urllib.request.Request(url, headers=headers or {})
    with urllib.request.urlopen(req, timeout=5) as r:
        return r.status, dict(r.headers), r.read()


def display(env, frame_id=None, voltage="3.9", fw="2.0.0"):
    headers = {"ID": MAC, "Access-Token": "x", "Battery-Voltage": voltage, "FW-Version": fw}
    if frame_id is not None:
        headers["X-Frame-Id"] = frame_id
    return json.loads(get(env["base"] + "/api/display", headers)[2])


def test_display_without_frames_is_none(env):
    d = display(env)
    assert d["status"] == 0 and d["action"] == "none" and d["refresh_rate"] == 60


def test_first_contact_gets_full_with_stock_compatible_fields(env):
    f1 = env["frames"].put(WHITE)
    d = display(env)
    assert d["action"] == "full" and d["frame_id"] == f1
    assert d["full_url"] == f"{env['base']}/frames/{f1}.png"
    assert d["image_url"] == d["full_url"] and d["filename"] == f1  # stock firmware
    assert d["sleep_mode"] == "deep" and d["full_mode"] == "full" and d["refresh_rate"] == 300
    assert d["update_firmware"] is False and d["reset_firmware"] is False


def test_partial_after_full_when_small_change(env):
    f1 = env["frames"].put(WHITE)
    display(env)                         # full f1
    assert display(env, frame_id=f1)["action"] == "none"
    f2 = env["frames"].put(with_black_row(100))
    d = display(env, frame_id=f1)
    assert d["action"] == "partial" and d["frame_id"] == f2
    assert d["regions_url"] == f"{env['base']}/frames/{f2}.regions?from={f1}"
    blob = get(d["regions_url"])[2]
    regions = decode_regions(blob)
    assert len(regions) == 1 and regions[0][0].y == 96 and regions[0][0].h == 8 and regions[0][0].w == 800
    assert env["devices"].get(MAC).partials_since_full == 1


def test_unknown_reported_frame_gets_full(env):
    env["frames"].put(WHITE)
    d = display(env, frame_id="musthave-unknown")
    assert d["action"] == "full"


def test_regions_unknown_from_is_404(env):
    f1 = env["frames"].put(WHITE)
    with pytest.raises(urllib.error.HTTPError) as err:
        get(f"{env['base']}/frames/{f1}.regions?from=musthave-nope")
    assert err.value.code == 404


def test_frame_png_and_legacy_screens_alias(env):
    f1 = env["frames"].put(WHITE)
    status, headers, body = get(f"{env['base']}/frames/{f1}.png")
    assert status == 200 and headers["Content-Type"] == "image/png" and body[:8] == b"\x89PNG\r\n\x1a\n"
    status, _, body2 = get(f"{env['base']}/screens/anything.png")
    assert status == 200 and body2 == body


def test_ota_offered_when_version_differs(env):
    env["frames"].put(WHITE)
    (env["fw"] / "latest.bin").write_bytes(b"\xe9binary")
    (env["fw"] / "firmware_version.txt").write_text("2.0.1\n")
    d = display(env, fw="2.0.0")
    assert d["update_firmware"] is True and d["firmware_url"] == f"{env['base']}/firmware/latest.bin"
    assert get(env["base"] + "/firmware/latest.bin")[2] == b"\xe9binary"
    assert display(env, fw="2.0.1")["update_firmware"] is False


def test_usb_voltage_switches_to_light_sleep(tmp_path):
    frames = FrameStore(tmp_path / "frames")
    frames.put(WHITE)
    srv = make_server(frames, DeviceRegistry(tmp_path / "d.json"), PolicyConfig(align_minutes=0), port=0)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        d = json.loads(get(f"http://127.0.0.1:{srv.server_port}/api/display", {"ID": MAC, "Battery-Voltage": "4.2"})[2])
        assert d["sleep_mode"] == "light" and d["refresh_rate"] == 60 and d["full_mode"] == "fast"
    finally:
        srv.shutdown()


def test_setup_and_log_still_work(env):
    d = json.loads(get(env["base"] + "/api/setup", {"ID": MAC})[2])
    assert d["status"] == 200 and d["api_key"] and d["friendly_id"]
    req = urllib.request.Request(env["base"] + "/api/log", data=b'{"log":1}', method="POST", headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=5) as r:
        assert r.status == 200


def test_unknown_path_is_404(env):
    with pytest.raises(urllib.error.HTTPError) as err:
        get(env["base"] + "/nope")
    assert err.value.code == 404
