import json
from datetime import datetime

import pytest

from musthave.payload import GAME_MAX, MAX_BYTES, PayloadTooLarge, build_payload, encode, sort_items

WEATHER = {"ok": True, "temp": 22.6, "feels": 22.0, "hum": 60, "wind": 14, "code": 3, "label": "Zataženo",
           "days": [{"d": "St", "hi": 24, "lo": 10, "p": 100, "code": 96, "label": "Bouřka"},
                    {"d": "Čt", "hi": 19, "lo": 12, "p": 100, "code": 80, "label": "Přeháňky"},
                    {"d": "Pá", "hi": 20, "lo": 9, "p": 3, "code": 3, "label": "Zataženo"}]}


def item(n, live=False, v=0, g=None, s=None):
    d = {"n": n, "live": live}
    if live:
        d["v"] = v
        if g:
            d["g"] = g
        if s:
            d["s"] = s
    return d


def test_sort_live_by_viewers_then_offline_in_original_order():
    items = [item("a"), item("b", True, 10), item("c"), item("d", True, 500)]
    assert [i["n"] for i in sort_items(items)] == ["d", "b", "a", "c"]


def test_build_payload_adds_updated_and_live_counts():
    kick = {"ok": True, "items": [item("x", True, 5), item("y")]}
    twitch = {"ok": True, "items": [item("z")]}
    p = build_payload(WEATHER, kick, twitch, datetime(2026, 9, 16, 17, 20))
    assert p["updated"] == "17:20"
    assert p["kick"]["live"] == 1 and p["twitch"]["live"] == 0
    assert p["weather"] == WEATHER
    assert p["kick"]["items"][0]["n"] == "x"


def test_build_payload_truncates_long_game_names():
    kick = {"ok": True, "items": [item("x", True, 5, "A" * 40)]}
    p = build_payload(WEATHER, kick, {"ok": True, "items": []}, datetime(2026, 9, 16))
    g = p["kick"]["items"][0]["g"]
    assert len(g) == GAME_MAX and g.endswith("…")


def test_failed_source_keeps_ok_false():
    p = build_payload({"ok": False}, {"ok": False, "items": []}, {"ok": True, "items": []}, datetime(2026, 9, 16))
    assert p["weather"] == {"ok": False}
    assert p["kick"] == {"ok": False, "items": [], "live": 0}


def test_realistic_payload_fits_limit():
    kick = {"ok": True, "items": [item(f"KickStreamerName{i}", True, 12345, "World of Warcraft Classic", "12:34") for i in range(5)]}
    twitch = {"ok": True, "items": [item(f"TwitchStreamerName{i}", True, 12345, "Just Chatting", "12:34") for i in range(7)]}
    p = build_payload(WEATHER, kick, twitch, datetime(2026, 9, 16))
    assert len(encode(p)) < MAX_BYTES


def test_encode_is_compact_utf8():
    raw = encode({"a": "Zataženo", "b": [1, 2]})
    assert raw == '{"a":"Zataženo","b":[1,2]}'.encode("utf-8")
    assert json.loads(raw) == {"a": "Zataženo", "b": [1, 2]}


def test_oversized_payload_raises():
    kick = {"ok": True, "items": [item(f"S{i}", True, 1, "Game" * 5, "12:34") for i in range(200)]}
    with pytest.raises(PayloadTooLarge):
        build_payload(WEATHER, kick, {"ok": True, "items": []}, datetime(2026, 9, 16))
