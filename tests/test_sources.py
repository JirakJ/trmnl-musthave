"""Poslední dobrá data zdrojů: streamy ≤ 30 min, počasí ≤ 3 h; výpadek všeho = obrazovku neměnit."""

from musthave.sources import STALE_MAX_S, all_down, with_last_good

KICK = {"ok": True, "items": [{"n": "astatoro", "live": True, "v": 100}]}


def test_fresh_data_is_returned_and_remembered():
    assert with_last_good("kick", KICK, None, 1000.0) == (KICK, {"ts": 1000.0, "data": KICK})


def test_failure_uses_recent_last_good_marked_stale():
    shown, remembered = with_last_good("kick", {"ok": False, "items": []}, {"ts": 1000.0, "data": KICK}, 1000.0 + 600)
    assert shown == {**KICK, "stale": True} and remembered == {"ts": 1000.0, "data": KICK}


def test_failure_with_too_old_last_good_stays_not_ok():
    down = {"ok": False, "items": []}
    assert with_last_good("twitch", down, {"ts": 1000.0, "data": KICK}, 1000.0 + STALE_MAX_S["twitch"] + 1) == (down, {"ts": 1000.0, "data": KICK})
    assert with_last_good("twitch", down, None, 5.0) == (down, None)


def test_all_down_only_when_nothing_is_fresh():
    down = {"ok": False}
    stale = {**KICK, "stale": True}
    assert all_down(down, down, down) and all_down(stale, stale, down) and all_down(stale, stale, stale)
    assert not all_down(KICK, down, down) and not all_down(stale, KICK, down)
