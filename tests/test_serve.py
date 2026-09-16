"""Smyčka serveru: jeden tick = data → render → uložit; při selhání zůstane poslední obrázek."""

import json
from datetime import datetime
from pathlib import Path

from musthave.frames import FrameStore, png_to_bitmap
from musthave.serve import tick
from musthave.state import State

FIX = Path(__file__).parent / "fixtures"
OPENMETEO = json.loads((FIX / "openmeteo.json").read_text(encoding="utf-8"))
KICK_LIVE = json.loads((FIX / "kick_live.json").read_text(encoding="utf-8"))
TWITCH = (FIX / "twitch_gql.json").read_text(encoding="utf-8")

CONFIG = """
[location]
name = "Jihlava"
latitude = 49.3961
longitude = 15.5912
timezone = "Europe/Prague"
[streams]
kick = ["astatoro"]
twitch = ["arcadebulls"]
[server]
port = 0
power = "battery"
"""


def png(color: int) -> bytes:
    import io
    from PIL import Image
    buf = io.BytesIO()
    Image.new("1", (800, 480), color).save(buf, format="PNG")
    return buf.getvalue()


class FakeHttp:
    def __init__(self):
        self.posts = []

    def get_json(self, url, headers=None):
        if "open-meteo" in url:
            return OPENMETEO
        if "kick.com" in url:
            return KICK_LIVE
        raise AssertionError(url)

    def get_text(self, url, headers=None):
        raise AssertionError(url)

    def post_json(self, url, body, headers=None):
        self.posts.append((url, body))
        return (200, TWITCH) if "gql.twitch.tv" in url else (200, "{}")


def project(tmp_path):
    (tmp_path / "config.toml").write_text(CONFIG, encoding="utf-8")


def test_tick_renders_and_stores_frame(tmp_path):
    project(tmp_path)
    frames = FrameStore(tmp_path / "state" / "frames")
    calls = []

    def renderer(payload):
        calls.append(payload)
        return png(1)

    ok = tick(tmp_path, frames, http=FakeHttp(), env={}, now=datetime(2026, 9, 16, 17, 20), renderer=renderer, push=False)
    assert ok is True
    assert calls[0]["kick"]["items"][0]["n"] == "Astatoro"
    assert frames.latest() is not None and frames.get(frames.latest()) == png_to_bitmap(png(1))


def test_tick_keeps_last_frame_when_render_fails(tmp_path):
    project(tmp_path)
    frames = FrameStore(tmp_path / "state" / "frames")
    old = frames.put(png_to_bitmap(png(0)))

    def broken(payload):
        raise RuntimeError("chrome missing")

    ok = tick(tmp_path, frames, http=FakeHttp(), env={}, now=datetime(2026, 9, 16, 17, 20), renderer=broken, push=False)
    assert ok is False
    assert frames.latest() == old


def test_tick_pushes_to_trmnl_when_enabled(tmp_path):
    project(tmp_path)
    frames = FrameStore(tmp_path / "state" / "frames")
    http = FakeHttp()
    tick(tmp_path, frames, http=http, env={"TRMNL_WEBHOOK_UUID": "u-1"}, now=datetime(2026, 9, 16, 17, 20), renderer=lambda p: png(1), push=True)
    assert [p for p in http.posts if "custom_plugins/u-1" in p[0]]


def test_tick_without_push_never_calls_trmnl(tmp_path):
    project(tmp_path)
    frames = FrameStore(tmp_path / "state" / "frames")
    http = FakeHttp()
    tick(tmp_path, frames, http=http, env={"TRMNL_WEBHOOK_UUID": "u-1"}, now=datetime(2026, 9, 16, 17, 20), renderer=lambda p: png(1), push=False)
    assert not [p for p in http.posts if "trmnl.com" in p[0]]
