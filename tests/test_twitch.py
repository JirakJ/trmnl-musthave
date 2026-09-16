import json
from pathlib import Path

from musthave.twitch import DEFAULT_CLIENT_ID, GQL_URL, discover_client_id, fetch_twitch

GQL = json.loads((Path(__file__).parent / "fixtures" / "twitch_gql.json").read_text(encoding="utf-8"))
LOGINS = ["arcadebulls", "agraelus", "artemis", "cruelladk", "conducteir77", "oliverovykecy", "rob2628", "thislogindoesnotexist123"]
NEW_ID = "abcdefghijklmnopqrstuvwxyz0123"


class FakeHttp:
    def __init__(self, post=None, page=None, decapi=None):
        self.post_responses = list(post or [])
        self.page, self.decapi = page, decapi or {}
        self.posts, self.gets = [], []

    def post_json(self, url, body, headers=None):
        self.posts.append((url, body, headers))
        r = self.post_responses.pop(0)
        if isinstance(r, Exception):
            raise r
        return r

    def get_text(self, url, headers=None):
        self.gets.append(url)
        if "twitch.tv" in url:
            if isinstance(self.page, Exception):
                raise self.page
            return self.page
        login = url.rsplit("/", 1)[1]
        r = self.decapi[login]
        if isinstance(r, Exception):
            raise r
        return r


def ok_gql():
    return (200, json.dumps(GQL))


def test_gql_parses_live_and_offline_in_input_order():
    http = FakeHttp(post=[ok_gql()])
    out, cid = fetch_twitch(http, LOGINS, DEFAULT_CLIENT_ID)
    assert out["ok"] is True and cid == DEFAULT_CLIENT_ID
    names = [i["n"] for i in out["items"]]
    assert names[:7] == ["ArcadeBulls", "Agraelus", "Artemis", "Cruelladk", "Conducteir77", "OliverovyKecy", "Rob2628"]
    live = [i for i in out["items"] if i["live"]]
    assert len(live) == 5
    arcade = out["items"][0]
    assert arcade["live"] is True and arcade["g"] == "Just Chatting" and isinstance(arcade["v"], int)
    assert len(arcade["s"]) == 5 and arcade["s"][2] == ":"
    assert out["items"][1] == {"n": "Agraelus", "live": False}


def test_unknown_login_is_offline_with_login_as_name():
    http = FakeHttp(post=[ok_gql()])
    out, _ = fetch_twitch(http, LOGINS, DEFAULT_CLIENT_ID)
    assert out["items"][7] == {"n": "thislogindoesnotexist123", "live": False}


def test_gql_request_shape():
    http = FakeHttp(post=[ok_gql()])
    fetch_twitch(http, ["agraelus"], DEFAULT_CLIENT_ID)
    url, body, headers = http.posts[0]
    assert url == GQL_URL
    assert headers["Client-Id"] == DEFAULT_CLIENT_ID
    assert body[0]["variables"] == {"logins": ["agraelus"]}
    assert "users(logins:$logins)" in body[0]["query"]


def test_400_triggers_client_id_discovery_and_retry():
    page = f'<script>window.__twilightBuildID="x";</script><body clientId="{NEW_ID}">'
    http = FakeHttp(post=[(400, '{"error":"Bad Request"}'), ok_gql()], page=page)
    out, cid = fetch_twitch(http, LOGINS, "stale-id")
    assert out["ok"] is True
    assert cid == NEW_ID
    assert http.posts[1][2]["Client-Id"] == NEW_ID


def test_discover_client_id_returns_none_when_page_fails():
    assert discover_client_id(FakeHttp(page=OSError("down"))) is None


def test_gql_failure_falls_back_to_decapi():
    http = FakeHttp(post=[OSError("gql down")], decapi={"agraelus": "agraelus is offline", "rob2628": "4963"})
    out, cid = fetch_twitch(http, ["agraelus", "rob2628"], DEFAULT_CLIENT_ID)
    assert out["ok"] is True
    assert out["items"] == [{"n": "agraelus", "live": False}, {"n": "rob2628", "live": True, "v": 4963}]
    assert out["fallback"] is True


def test_everything_failing_returns_not_ok():
    http = FakeHttp(post=[OSError("gql down")], decapi={"agraelus": OSError("x")})
    out, _ = fetch_twitch(http, ["agraelus"], DEFAULT_CLIENT_ID)
    assert out == {"ok": False, "items": []}
