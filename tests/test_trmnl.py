from musthave.trmnl import WEBHOOK_URL, send_webhook


class FakeHttp:
    def __init__(self, status=200, text="{}"):
        self.status, self.text, self.calls = status, text, []

    def post_json(self, url, body, headers=None):
        self.calls.append((url, body, headers))
        return self.status, self.text


def test_send_webhook_posts_merge_variables_to_uuid_url():
    http = FakeHttp()
    status, _ = send_webhook(http, "abc-uuid", {"updated": "10:00"})
    assert status == 200
    url, body, headers = http.calls[0]
    assert url == WEBHOOK_URL.format(uuid="abc-uuid") == "https://trmnl.com/api/custom_plugins/abc-uuid"
    assert body == {"merge_variables": {"updated": "10:00"}}
    assert headers["Content-Type"] == "application/json"


def test_send_webhook_returns_rate_limit_status():
    status, text = send_webhook(FakeHttp(429, "rate limited"), "u", {})
    assert (status, text) == (429, "rate limited")


def test_send_data_posts_with_bearer_to_plugin_setting_id():
    from musthave.trmnl import DATA_URL, send_data

    http = FakeHttp()
    status, _ = send_data(http, "key-1", 479481, {"updated": "10:00"})
    assert status == 200
    url, body, headers = http.calls[0]
    assert url == DATA_URL.format(id=479481) == "https://trmnl.com/api/plugin_settings/479481/data"
    assert body == {"merge_variables": {"updated": "10:00"}}
    assert headers["Authorization"] == "Bearer key-1"
