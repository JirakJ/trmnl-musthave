"""Smyčka serveru: jeden tick = data → render → uložit; při selhání zůstane poslední obrázek."""

import json
from datetime import datetime
from pathlib import Path

from musthave.screen import ScreenStore
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
refresh_seconds = 300
"""


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


def test_tick_renders_and_stores_screen(tmp_path):
    project(tmp_path)
    store = ScreenStore(tmp_path / "state" / "screen")
    calls = []

    def renderer(payload):
        calls.append(payload)
        return b"IMG-" + payload["updated"].encode()

    ok = tick(tmp_path, store, http=FakeHttp(), env={}, now=datetime(2026, 9, 16, 17, 20), renderer=renderer, push=False)
    assert ok is True
    assert calls[0]["kick"]["items"][0]["n"] == "Astatoro"
    name, data = store.current()
    assert data == b"IMG-17:20" and name.endswith(".png")


def test_tick_keeps_last_screen_when_render_fails(tmp_path):
    project(tmp_path)
    store = ScreenStore(tmp_path / "state" / "screen")
    store.update(b"OLD", ext="png")

    def broken(payload):
        raise RuntimeError("chrome missing")

    ok = tick(tmp_path, store, http=FakeHttp(), env={}, now=datetime(2026, 9, 16, 17, 20), renderer=broken, push=False)
    assert ok is False
    assert store.current()[1] == b"OLD"


def test_tick_pushes_to_trmnl_when_enabled(tmp_path):
    project(tmp_path)
    store = ScreenStore(tmp_path / "state" / "screen")
    http = FakeHttp()
    tick(tmp_path, store, http=http, env={"TRMNL_WEBHOOK_UUID": "u-1"}, now=datetime(2026, 9, 16, 17, 20), renderer=lambda p: b"X", push=True)
    assert [p for p in http.posts if "custom_plugins/u-1" in p[0]]


def test_tick_without_push_never_calls_trmnl(tmp_path):
    project(tmp_path)
    store = ScreenStore(tmp_path / "state" / "screen")
    http = FakeHttp()
    tick(tmp_path, store, http=http, env={"TRMNL_WEBHOOK_UUID": "u-1"}, now=datetime(2026, 9, 16, 17, 20), renderer=lambda p: b"X", push=False)
    assert not [p for p in http.posts if "trmnl.com" in p[0]]
