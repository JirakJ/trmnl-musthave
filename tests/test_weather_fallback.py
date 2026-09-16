"""Při výpadku Open-Meteo se použije poslední známé počasí (max 3 h staré) označené jako stale."""

from musthave.weather import STALE_MAX_S, with_fallback

GOOD = {"ok": True, "temp": 20.0, "feels": 19.0, "hum": 50, "wind": 5, "code": 1, "label": "Skoro jasno", "days": []}


def test_fresh_weather_is_returned_and_remembered():
    out, remembered = with_fallback(GOOD, None, now_ts=1000.0)
    assert out == GOOD
    assert remembered == {"ts": 1000.0, "weather": GOOD}


def test_failure_uses_recent_last_good_marked_stale():
    out, remembered = with_fallback({"ok": False}, {"ts": 1000.0, "weather": GOOD}, now_ts=1000.0 + 600)
    assert out["ok"] is True and out["stale"] is True and out["temp"] == 20.0
    assert remembered == {"ts": 1000.0, "weather": GOOD}


def test_failure_with_too_old_last_good_stays_not_ok():
    out, remembered = with_fallback({"ok": False}, {"ts": 1000.0, "weather": GOOD}, now_ts=1000.0 + STALE_MAX_S + 1)
    assert out == {"ok": False}
    assert remembered == {"ts": 1000.0, "weather": GOOD}


def test_failure_without_history_stays_not_ok():
    assert with_fallback({"ok": False}, None, now_ts=5.0) == ({"ok": False}, None)
