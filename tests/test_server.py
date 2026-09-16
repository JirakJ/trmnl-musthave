"""BYOS server: endpoints, které volá firmware TRMNL."""

import json
import threading
import urllib.request

import pytest

from musthave.screen import ScreenStore
from musthave.server import make_server


@pytest.fixture
def served(tmp_path):
    store = ScreenStore(tmp_path / "screen")
    store.update(b"PNGDATA", ext="png")
    srv = make_server(store, port=0, refresh_s=300)
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    yield f"http://127.0.0.1:{srv.server_port}", store
    srv.shutdown()


def get(url, headers=None):
    req = urllib.request.Request(url, headers=headers or {})
    with urllib.request.urlopen(req, timeout=5) as r:
        return r.status, r.headers, r.read()


def test_setup_returns_credentials_and_absolute_image_url(served):
    base, _ = served
    status, _, body = get(base + "/api/setup", {"ID": "AA:BB:CC:DD:EE:FF"})
    d = json.loads(body)
    assert status == 200 and d["status"] == 200
    assert d["api_key"] and d["friendly_id"]
    assert d["image_url"].startswith(base + "/screens/")


def test_display_returns_current_filename_and_refresh(served):
    base, store = served
    name, _ = store.current()
    _, _, body = get(base + "/api/display", {"ID": "AA:BB:CC:DD:EE:FF", "Access-Token": "x"})
    d = json.loads(body)
    assert d["status"] == 0
    assert d["filename"] == name
    assert d["image_url"] == f"{base}/screens/{name}"
    assert d["refresh_rate"] == 300
    assert d["update_firmware"] is False and d["reset_firmware"] is False


def test_screen_bytes_are_served_with_image_content_type(served):
    base, store = served
    name, _ = store.current()
    status, headers, body = get(f"{base}/screens/{name}")
    assert status == 200 and body == b"PNGDATA"
    assert headers["Content-Type"] == "image/png"


def test_unknown_screen_name_still_serves_current_image(served):
    """Firmware si může cache-breaker přidat; vždy vracíme aktuální obrázek."""
    base, _ = served
    status, _, body = get(base + "/screens/musthave-deadbeef.png")
    assert status == 200 and body == b"PNGDATA"


def test_display_without_any_screen_reports_error_status(tmp_path):
    store = ScreenStore(tmp_path / "empty")
    srv = make_server(store, port=0, refresh_s=300)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        _, _, body = get(f"http://127.0.0.1:{srv.server_port}/api/display")
        d = json.loads(body)
        assert d["status"] == 500 and d["refresh_rate"] == 60
    finally:
        srv.shutdown()


def test_log_endpoint_accepts_post(served):
    base, _ = served
    req = urllib.request.Request(base + "/api/log", data=b'{"log":{"x":1}}', method="POST", headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=5) as r:
        assert r.status == 200


def test_unknown_path_is_404(served):
    base, _ = served
    with pytest.raises(urllib.error.HTTPError) as err:
        get(base + "/nope")
    assert err.value.code == 404
