from pathlib import Path

from musthave.config import load_settings


def write(root: Path, config: str, env: str | None = None) -> None:
    (root / "config.toml").write_text(config, encoding="utf-8")
    if env is not None:
        (root / ".env").write_text(env, encoding="utf-8")


CONFIG = """
[location]
name = "Jihlava"
latitude = 49.3961
longitude = 15.5912
timezone = "Europe/Prague"

[streams]
kick = ["fattypillow", "astatoro"]
twitch = ["agraelus"]

[send]
min_interval_minutes = 6
heartbeat_minutes = 15
"""


def test_load_settings_reads_toml_and_dotenv(tmp_path):
    write(tmp_path, CONFIG, "# comment\nTRMNL_WEBHOOK_UUID=abc123\n")
    s = load_settings(tmp_path, env={})
    assert s.location_name == "Jihlava"
    assert s.latitude == 49.3961
    assert s.kick == ["fattypillow", "astatoro"]
    assert s.twitch == ["agraelus"]
    assert s.min_interval_s == 360
    assert s.heartbeat_s == 900
    assert s.webhook_uuid == "abc123"
    assert s.state_path == tmp_path / "state" / "last.json"


def test_environment_overrides_dotenv(tmp_path):
    write(tmp_path, CONFIG, "TRMNL_WEBHOOK_UUID=from-file\n")
    s = load_settings(tmp_path, env={"TRMNL_WEBHOOK_UUID": "from-env"})
    assert s.webhook_uuid == "from-env"


def test_missing_uuid_is_none(tmp_path):
    write(tmp_path, CONFIG)
    assert load_settings(tmp_path, env={}).webhook_uuid is None


def test_dotenv_strips_quotes(tmp_path):
    write(tmp_path, CONFIG, 'TRMNL_WEBHOOK_UUID="quoted"\n')
    assert load_settings(tmp_path, env={}).webhook_uuid == "quoted"


CONFIG_WITH_TRMNL = CONFIG + """
[trmnl]
plugin_setting_id = 479481
"""


def test_plugin_setting_id_from_toml(tmp_path):
    write(tmp_path, CONFIG_WITH_TRMNL)
    s = load_settings(tmp_path, env={})
    assert s.plugin_setting_id == 479481


def test_plugin_setting_id_absent_is_none(tmp_path):
    write(tmp_path, CONFIG)
    assert load_settings(tmp_path, env={}).plugin_setting_id is None


def test_secret_lookup_fills_missing_api_key(tmp_path):
    write(tmp_path, CONFIG)
    s = load_settings(tmp_path, env={}, secret_lookup=lambda name: "from-keychain" if name == "TRMNL_USER_API_KEY" else None)
    assert s.user_api_key == "from-keychain"


def test_environment_wins_over_secret_lookup(tmp_path):
    write(tmp_path, CONFIG)
    s = load_settings(tmp_path, env={"TRMNL_USER_API_KEY": "env"}, secret_lookup=lambda name: "kc")
    assert s.user_api_key == "env"
