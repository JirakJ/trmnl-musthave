import json
from datetime import datetime
from pathlib import Path

from musthave.run import collect, run
from musthave.state import State, load_state, save_state

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
"""


class FakeHttp:
    def __init__(self, webhook_status=200):
        self.webhook_status, self.posts = webhook_status, []

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
        if "gql.twitch.tv" in url:
            return 200, TWITCH
        return self.webhook_status, "{}"


def project(tmp_path, uuid="uuid-1", api=False):
    cfg = CONFIG + ("\n[trmnl]\nplugin_setting_id = 479481\n" if api else "")
    (tmp_path / "config.toml").write_text(cfg, encoding="utf-8")
    env = {"TRMNL_WEBHOOK_UUID": uuid} if uuid else {}
    if api:
        env["TRMNL_USER_API_KEY"] = "key-1"
    return env


def test_collect_combines_all_sources():
    from musthave.config import load_settings
    import tempfile
    root = Path(tempfile.mkdtemp())
    env = project(root)
    settings = load_settings(root, env)
    payload, state = collect(FakeHttp(), settings, State(), datetime(2026, 9, 16, 17, 20))
    assert payload["updated"] == "17:20"
    assert payload["weather"]["ok"] and payload["kick"]["ok"] and payload["twitch"]["ok"]
    assert payload["kick"]["items"][0]["n"] == "Astatoro"
    assert payload["twitch"]["items"][0]["n"] == "ArcadeBulls"
    assert state.twitch_client_id


def test_run_sends_and_saves_state(tmp_path):
    env = project(tmp_path)
    http = FakeHttp()
    assert run(tmp_path, http=http, env=env, now=datetime(2026, 9, 16, 17, 20)) == 0
    webhook_posts = [p for p in http.posts if "custom_plugins/uuid-1" in p[0]]
    assert len(webhook_posts) == 1
    assert webhook_posts[0][1]["merge_variables"]["updated"] == "17:20"
    st = load_state(tmp_path / "state" / "last.json")
    assert st.last_sent_at is not None and st.last_payload["updated"] == "17:20"


def test_run_dry_run_does_not_post_or_save(tmp_path, capsys):
    env = project(tmp_path)
    http = FakeHttp()
    assert run(tmp_path, http=http, env=env, dry_run=True) == 0
    assert not [p for p in http.posts if "custom_plugins" in p[0]]
    assert not (tmp_path / "state" / "last.json").exists()
    assert '"weather"' in capsys.readouterr().out


def test_run_without_uuid_fails_with_code_2(tmp_path, capsys):
    env = project(tmp_path, uuid=None)
    assert run(tmp_path, http=FakeHttp(), env=env) == 2
    assert "TRMNL_WEBHOOK_UUID" in capsys.readouterr().err


def test_run_skips_when_throttled(tmp_path):
    env = project(tmp_path)
    now = datetime(2026, 9, 16, 17, 20)
    save_state(tmp_path / "state" / "last.json", State(last_sent_at=now.timestamp() - 60, last_payload={"x": 1}))
    http = FakeHttp()
    assert run(tmp_path, http=http, env=env, now=now) == 0
    assert not [p for p in http.posts if "custom_plugins" in p[0]]


def test_run_on_429_keeps_previous_state(tmp_path):
    env = project(tmp_path)
    now = datetime(2026, 9, 16, 17, 20)
    old = State(last_sent_at=now.timestamp() - 2000, last_payload={"x": 1})
    save_state(tmp_path / "state" / "last.json", old)
    assert run(tmp_path, http=FakeHttp(webhook_status=429), env=env, now=now) == 1
    assert load_state(tmp_path / "state" / "last.json") == old


def test_run_uses_authenticated_data_endpoint_when_no_uuid(tmp_path):
    env = project(tmp_path, uuid=None, api=True)
    http = FakeHttp()
    assert run(tmp_path, http=http, env=env, now=datetime(2026, 9, 16, 17, 20)) == 0
    posts = [p for p in http.posts if "plugin_settings/479481/data" in p[0]]
    assert len(posts) == 1 and posts[0][1]["merge_variables"]["updated"] == "17:20"
    assert not [p for p in http.posts if "custom_plugins" in p[0]]


def test_run_prefers_webhook_uuid_when_both_configured(tmp_path):
    env = project(tmp_path, uuid="uuid-1", api=True)
    http = FakeHttp()
    assert run(tmp_path, http=http, env=env, now=datetime(2026, 9, 16, 17, 20)) == 0
    assert [p for p in http.posts if "custom_plugins/uuid-1" in p[0]]
    assert not [p for p in http.posts if "/data" in p[0]]
