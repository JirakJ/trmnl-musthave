from musthave.devices import DeviceRegistry
from musthave.policy import DeviceState


def test_registry_defaults_and_persists(tmp_path):
    reg = DeviceRegistry(tmp_path / "devices.json")
    assert reg.get("AA:BB") == DeviceState()
    reg.save("AA:BB", DeviceState(frame_id="f1", partials_since_full=2, last_full_at=5.0, last_seen_at=6.0))
    again = DeviceRegistry(tmp_path / "devices.json")
    assert again.get("AA:BB") == DeviceState(frame_id="f1", partials_since_full=2, last_full_at=5.0, last_seen_at=6.0)
    assert again.get("CC:DD") == DeviceState()


def test_frame_ids_lists_frames_shown_by_devices(tmp_path):
    from musthave.devices import DeviceRegistry
    from musthave.policy import DeviceState

    reg = DeviceRegistry(tmp_path / "devices.json")
    assert reg.frame_ids() == set()
    reg.save("AA", DeviceState(frame_id="musthave-frame-aaaaaaaaaa", last_seen_at=1.0))
    reg.save("BB", DeviceState(frame_id=None, last_seen_at=2.0))
    reg.save("CC", DeviceState(frame_id="musthave-frame-cccccccccc", target_frame_id="musthave-frame-tttttttttt", last_seen_at=3.0))
    assert DeviceRegistry(tmp_path / "devices.json").frame_ids(now=10.0) == {
        "musthave-frame-aaaaaaaaaa", "musthave-frame-cccccccccc", "musthave-frame-tttttttttt"}


def test_frame_ids_ignore_stale_devices(tmp_path):
    from musthave.devices import PIN_MAX_AGE_S, DeviceRegistry
    from musthave.policy import DeviceState

    reg = DeviceRegistry(tmp_path / "devices.json")
    now = 1_000_000.0
    reg.save("OLD", DeviceState(frame_id="musthave-frame-old", last_seen_at=now - PIN_MAX_AGE_S - 1))
    for i in range(12):  # 12 zařízení viděných nedávno, všechna se počítají
        reg.save(f"D{i}", DeviceState(frame_id=f"musthave-frame-{i:010d}", last_seen_at=now - i))
    ids = reg.frame_ids(now=now)
    assert "musthave-frame-old" not in ids
    assert ids == {f"musthave-frame-{i:010d}" for i in range(12)}
    reg.save("BAD", DeviceState(frame_id="musthave-frame-bad", last_seen_at="123"))  # ruční edit souboru
    assert "musthave-frame-bad" not in reg.frame_ids(now=now)


def test_concurrent_saves_never_corrupt_the_file(tmp_path):
    import threading

    from musthave.devices import DeviceRegistry
    from musthave.policy import DeviceState

    reg = DeviceRegistry(tmp_path / "devices.json")

    def worker(n):
        for i in range(30):
            reg.save(f"D{n}", DeviceState(frame_id=f"musthave-frame-{n}-{i}", last_seen_at=float(i)))

    threads = [threading.Thread(target=worker, args=(n,)) for n in range(6)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    again = DeviceRegistry(tmp_path / "devices.json")
    assert len(again.states()) == 6 and all(st.frame_id.endswith("-29") for st in again.states())
    assert not list(tmp_path.glob(".devices.json.*.tmp"))
