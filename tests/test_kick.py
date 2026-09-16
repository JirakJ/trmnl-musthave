import json
from pathlib import Path

from musthave.kick import fetch_kick

FIX = Path(__file__).parent / "fixtures"
LIVE = json.loads((FIX / "kick_live.json").read_text(encoding="utf-8"))
OFFLINE = json.loads((FIX / "kick_offline.json").read_text(encoding="utf-8"))


class FakeHttp:
    """Mapuje slug → odpověď nebo výjimka."""

    def __init__(self, responses):
        self.responses, self.urls = responses, []

    def get_json(self, url, headers=None):
        self.urls.append(url)
        slug = url.rsplit("/", 1)[1]
        r = self.responses[slug]
        if isinstance(r, Exception):
            raise r
        return r


def test_live_channel_has_viewers_game_and_local_start_time():
    out = fetch_kick(FakeHttp({"astatoro": LIVE}), ["astatoro"])
    assert out["ok"] is True
    item = out["items"][0]
    assert item == {"n": "Astatoro", "live": True, "v": 1394, "g": "Valheim", "s": "14:17"}


def test_offline_channel_is_minimal():
    out = fetch_kick(FakeHttp({"fattypillow": OFFLINE}), ["fattypillow"])
    assert out["items"][0] == {"n": "FATTYPILLOW", "live": False}


def test_result_order_follows_configuration():
    http = FakeHttp({"fattypillow": OFFLINE, "astatoro": LIVE})
    out = fetch_kick(http, ["fattypillow", "astatoro"])
    assert [i["n"] for i in out["items"]] == ["FATTYPILLOW", "Astatoro"]


def test_single_failure_marks_item_but_keeps_ok():
    http = FakeHttp({"astatoro": LIVE, "broken": OSError("timeout")})
    out = fetch_kick(http, ["astatoro", "broken"])
    assert out["ok"] is True
    assert out["items"][1] == {"n": "broken", "live": False, "err": True}


def test_all_failures_return_not_ok():
    out = fetch_kick(FakeHttp({"a": OSError(), "b": OSError()}), ["a", "b"])
    assert out == {"ok": False, "items": []}


def test_requests_use_kick_v2_endpoint():
    http = FakeHttp({"astatoro": LIVE})
    fetch_kick(http, ["astatoro"])
    assert http.urls == ["https://kick.com/api/v2/channels/astatoro"]
