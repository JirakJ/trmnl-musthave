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
    reg.save("CC", DeviceState(frame_id="musthave-frame-cccccccccc", last_seen_at=3.0))
    assert DeviceRegistry(tmp_path / "devices.json").frame_ids() == {"musthave-frame-aaaaaaaaaa", "musthave-frame-cccccccccc"}
