from musthave.devices import DeviceRegistry
from musthave.policy import DeviceState


def test_registry_defaults_and_persists(tmp_path):
    reg = DeviceRegistry(tmp_path / "devices.json")
    assert reg.get("AA:BB") == DeviceState()
    reg.save("AA:BB", DeviceState(frame_id="f1", partials_since_full=2, last_full_at=5.0, last_seen_at=6.0))
    again = DeviceRegistry(tmp_path / "devices.json")
    assert again.get("AA:BB") == DeviceState(frame_id="f1", partials_since_full=2, last_full_at=5.0, last_seen_at=6.0)
    assert again.get("CC:DD") == DeviceState()
