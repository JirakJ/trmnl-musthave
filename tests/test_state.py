from musthave.state import State, load_state, save_state, should_send

P1 = {"updated": "10:00", "kick": {"items": [{"n": "a", "live": True}]}}
P1_LATER = {"updated": "10:05", "kick": {"items": [{"n": "a", "live": True}]}}
P2 = {"updated": "10:05", "kick": {"items": [{"n": "a", "live": False}]}}
MIN, HB = 360, 900


def test_first_run_sends():
    assert should_send(State(), P1, 1000.0, MIN, HB) == (True, "first")


def test_unchanged_within_heartbeat_is_skipped():
    st = State(last_sent_at=1000.0, last_payload=P1)
    assert should_send(st, P1_LATER, 1000.0 + 600, MIN, HB) == (False, "unchanged")


def test_changed_after_min_interval_sends():
    st = State(last_sent_at=1000.0, last_payload=P1)
    assert should_send(st, P2, 1000.0 + MIN, MIN, HB) == (True, "changed")


def test_changed_before_min_interval_is_throttled():
    st = State(last_sent_at=1000.0, last_payload=P1)
    assert should_send(st, P2, 1000.0 + MIN - 1, MIN, HB) == (False, "throttled")


def test_heartbeat_sends_even_if_unchanged():
    st = State(last_sent_at=1000.0, last_payload=P1)
    assert should_send(st, P1_LATER, 1000.0 + HB, MIN, HB) == (True, "heartbeat")


def test_state_roundtrip_and_missing_file(tmp_path):
    path = tmp_path / "nested" / "last.json"
    assert load_state(path) == State()
    st = State(last_sent_at=5.0, last_payload=P1, twitch_client_id="cid")
    save_state(path, st)
    assert load_state(path) == st


def test_corrupt_state_file_is_ignored(tmp_path):
    path = tmp_path / "last.json"
    path.write_text("{not json", encoding="utf-8")
    assert load_state(path) == State()
