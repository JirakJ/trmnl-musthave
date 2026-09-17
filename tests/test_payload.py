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


def test_build_payload_adds_date_host_and_fw():
    from musthave.payload import czech_date

    assert czech_date(datetime(2026, 9, 17, 10, 20)) == "Čtvrtek 17. 9. 2026"
    p = build_payload(WEATHER, {"ok": True, "items": []}, {"ok": True, "items": []}, datetime(2026, 9, 17, 10, 20), host="RPi", fw="2.0.6")
    assert p["date"] == "Čtvrtek 17. 9. 2026" and p["host"] == "RPi" and p["fw"] == "2.0.6"
    q = build_payload(WEATHER, {"ok": True, "items": []}, {"ok": True, "items": []}, datetime(2026, 9, 17, 10, 20))
    assert q["host"] == "" and q["fw"] == "" and q["date"] == "Čtvrtek 17. 9. 2026"


def test_countdowns_days_left_and_short_date():
    from datetime import date

    from musthave.config import Countdown

    events = [Countdown("GTA VI", date(2026, 11, 19)), Countdown("WoW Forever", date(2026, 11, 4))]
    p = build_payload(WEATHER, {"ok": True, "items": []}, {"ok": True, "items": []}, datetime(2026, 9, 17, 23, 59), countdowns=events)
    assert p["countdowns"] == [
        {"n": "WoW Forever", "days": 48, "d": "4. 11."},
        {"n": "GTA VI", "days": 63, "d": "19. 11."},
    ]


def test_countdowns_skip_past_events_and_keep_release_day():
    from datetime import date

    from musthave.config import Countdown

    events = [Countdown("Old", date(2026, 9, 16)), Countdown("Today", date(2026, 9, 17))]
    p = build_payload(WEATHER, {"ok": True, "items": []}, {"ok": True, "items": []}, datetime(2026, 9, 17, 8, 0), countdowns=events)
    assert p["countdowns"] == [{"n": "Today", "days": 0, "d": "17. 9."}]


def test_countdowns_default_empty():
    p = build_payload(WEATHER, {"ok": True, "items": []}, {"ok": True, "items": []}, datetime(2026, 9, 17))
    assert p["countdowns"] == []
