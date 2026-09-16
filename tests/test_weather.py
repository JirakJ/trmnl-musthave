import json
from datetime import datetime
from pathlib import Path

import pytest

from musthave.config import Settings
from musthave.weather import fetch_weather, wmo_label

FIXTURE = json.loads((Path(__file__).parent / "fixtures" / "openmeteo.json").read_text(encoding="utf-8"))


class FakeHttp:
    def __init__(self, payload=None, error=None):
        self.payload, self.error, self.urls = payload, error, []

    def get_json(self, url, headers=None):
        self.urls.append(url)
        if self.error:
            raise self.error
        return self.payload


def settings():
    return Settings("Jihlava", 49.3961, 15.5912, "Europe/Prague", [], [], 360, 900, None, None, Path("x"))


@pytest.mark.parametrize("code,label", [(0, "Jasno"), (3, "Zataženo"), (61, "Déšť"), (96, "Bouřka"), (999, "—")])
def test_wmo_label(code, label):
    assert wmo_label(code) == label


def test_fetch_weather_parses_current_and_days():
    http = FakeHttp(FIXTURE)
    w = fetch_weather(http, settings(), datetime(2026, 9, 16, 17, 30))
    assert w["ok"] is True
    assert w["temp"] == FIXTURE["current"]["temperature_2m"]
    assert w["hum"] == FIXTURE["current"]["relative_humidity_2m"]
    assert isinstance(w["wind"], int)
    assert w["label"] == wmo_label(FIXTURE["current"]["weather_code"])
    assert len(w["days"]) == 3
    first = w["days"][0]
    assert first["d"] == "St"  # 2026-09-16 je středa
    assert first["hi"] == round(FIXTURE["daily"]["temperature_2m_max"][0])
    assert first["lo"] == round(FIXTURE["daily"]["temperature_2m_min"][0])
    assert first["p"] == FIXTURE["daily"]["precipitation_probability_max"][0]
    assert first["label"] == wmo_label(FIXTURE["daily"]["weather_code"][0])
    assert [d["d"] for d in w["days"]] == ["St", "Čt", "Pá"]


def test_fetch_weather_requests_configured_location():
    http = FakeHttp(FIXTURE)
    fetch_weather(http, settings(), datetime(2026, 9, 16))
    assert "latitude=49.3961" in http.urls[0]
    assert "longitude=15.5912" in http.urls[0]
    assert "timezone=Europe%2FPrague" in http.urls[0]


def test_fetch_weather_returns_not_ok_on_error():
    assert fetch_weather(FakeHttp(error=OSError("boom")), settings(), datetime(2026, 9, 16)) == {"ok": False}
