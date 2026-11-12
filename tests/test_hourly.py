"""#9 Hodinová předpověď: okna podle timezone, přes půlnoc a změnu času, chybějící data, shrnutí, cloudový limit."""

import json
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from musthave.config import Settings
from musthave.payload import MAX_BYTES, build_payload, encode, fit_for_cloud
from musthave.weather import fetch_weather, hourly_slots, rain_summary

TZ = ZoneInfo("Europe/Prague")
FIXTURE = json.loads((Path(__file__).parent / "fixtures" / "openmeteo_hourly.json").read_text(encoding="utf-8"))


def hourly(start: datetime, temps, probs, rains):
    """Syntetická odpověď Open-Meteo (unixtime): hodnota v čase T = srážky za hodinu před T."""
    t0 = int(start.timestamp())
    return {"time": [t0 + i * 3600 for i in range(len(temps))], "temperature_2m": temps,
            "precipitation_probability": probs, "precipitation": rains}


def slots_for(now, temps, probs, rains, start=None):
    return hourly_slots(hourly(start or now.replace(minute=0), temps, probs, rains), now, TZ)


def test_real_response_gives_six_slots_from_current_hour():
    now = datetime.fromtimestamp(FIXTURE["current"]["time"], TZ)
    w = fetch_weather(type("H", (), {"get_json": lambda self, url, headers=None: FIXTURE})(),
                      Settings("Jihlava", 49.4, 15.6, "Europe/Prague", [], [], 360, 900, None, None, None, Path("x")), now)
    assert w["ok"] and len(w["hours"]) == 6 and w["hours"][0]["h"] == now.strftime("%H")
    assert w["rain"] and len(w["days"]) == 3
    assert [d["d"] for d in w["days"]][0] == ["Po", "Út", "St", "Čt", "Pá", "So", "Ne"][now.weekday()]


def test_precipitation_is_taken_from_end_of_interval_temperature_from_start():
    now = datetime(2026, 11, 12, 14, 20, tzinfo=TZ)
    s = slots_for(now, [10, 11, 12, 13, 14, 15, 16], [0, 10, 70, 80, 20, 0, 0], [0, 0, 1.2, 2.0, 0.1, 0, 0])
    assert [x["h"] for x in s] == ["14", "15", "16", "17", "18", "19"]
    assert s[0] == {"ts": s[0]["ts"], "h": "14", "t": 10, "p": 10, "mm": 0.0}   # okno 14–15: srážky z 15:00
    assert s[1]["p"] == 70 and s[1]["mm"] == 1.2 and s[1]["t"] == 11


def test_window_crosses_midnight():
    now = datetime(2026, 11, 12, 22, 5, tzinfo=TZ)
    s = slots_for(now, [5] * 7, [0] * 7, [0] * 7)
    assert [x["h"] for x in s] == ["22", "23", "00", "01", "02", "03"]


def test_dst_end_repeats_the_hour():
    now = datetime(2026, 10, 25, 1, 30, tzinfo=TZ)  # 03:00 CEST → 02:00 CET
    s = slots_for(now, [5] * 7, [0] * 7, [0] * 7)
    assert [x["h"] for x in s] == ["01", "02", "02", "03", "04", "05"]


def test_dst_start_skips_the_hour():
    now = datetime(2026, 3, 29, 1, 30, tzinfo=TZ)  # 02:00 CET → 03:00 CEST
    s = slots_for(now, [5] * 7, [0] * 7, [0] * 7)
    assert [x["h"] for x in s] == ["01", "03", "04", "05", "06", "07"]


def test_missing_data_is_not_zero():
    now = datetime(2026, 11, 12, 14, 0, tzinfo=TZ)
    s = slots_for(now, [10, None, 12], [None, 30, None], [None, 0.5, None])
    assert s[0]["t"] == 10 and s[0]["p"] == 30 and s[1]["t"] is None and s[1]["p"] is None
    assert s[3] == {"ts": s[3]["ts"], "h": "17", "t": None, "p": None, "mm": None}   # za koncem dat
    assert "část dat chybí" in rain_summary(s)


def test_summary_dry_heavy_and_unavailable():
    dry = slots_for(datetime(2026, 7, 1, 9, 0, tzinfo=TZ), [20] * 7, [0, 5, 10, 0, 0, 0, 0], [0] * 7)
    assert rain_summary(dry) == "Bez srážek do 15 h"
    heavy = slots_for(datetime(2026, 7, 1, 14, 0, tzinfo=TZ), [20] * 7, [0, 10, 60, 90, 85, 40, 10],
                      [0, 0, 2.5, 8.0, 6.1, 0.4, 0])
    assert rain_summary(heavy) == "Nejvyšší šance srážek 16\u2060–\u206018\u00a0h (90 %) · 17,0 mm"
    assert rain_summary([]) == "Hodinová předpověď nedostupná"
    none = slots_for(datetime(2026, 7, 1, 14, 0, tzinfo=TZ), [None] * 7, [None] * 7, [None] * 7)
    assert rain_summary(none) == "Hodinová předpověď nedostupná"


def test_stale_weather_drops_past_hours():
    now = datetime(2026, 11, 12, 14, 10, tzinfo=TZ)
    s = slots_for(now, [10] * 7, [0, 0, 0, 80, 0, 0, 0], [0] * 7)
    weather = {"ok": True, "temp": 1, "feels": 1, "hum": 1, "wind": 1, "label": "x", "days": [], "hours": s,
               "rain": rain_summary(s), "stale": True, "since": now.timestamp()}
    later = datetime(2026, 11, 12, 16, 30, tzinfo=TZ)
    p = build_payload(weather, {"ok": True, "items": []}, {"ok": True, "items": []}, later, max_bytes=None)
    assert [h["h"] for h in p["weather"]["hours"]] == ["16", "17", "18", "19"]
    assert p["weather"]["as_of"] == "14:10"


def test_cloud_payload_gets_smaller_representation():
    now = datetime(2026, 11, 12, 14, 0, tzinfo=TZ)
    s = slots_for(now, [10] * 7, [10] * 7, [0.1] * 7)
    weather = {"ok": True, "temp": 1, "feels": 1, "hum": 1, "wind": 1, "label": "x", "days": [], "hours": s, "rain": "r"}
    items = [{"n": f"Streamer{i:02d}", "live": True, "v": 1000, "g": "World of Warcraft Classic", "s": "12:00"} for i in range(3)]
    local = build_payload(weather, {"ok": True, "items": items}, {"ok": True, "items": []}, now, max_bytes=None)
    limit = len(encode(local)) - 50          # těsně menší limit: hodinová předpověď se musí obětovat
    cloud = fit_for_cloud(local, limit)
    assert len(encode(cloud)) <= limit < MAX_BYTES and "hours" not in cloud["weather"] and "rain" not in cloud["weather"]
    assert local["weather"]["hours"]  # originál pro lokální render zůstal beze změny
    small = build_payload(weather, {"ok": True, "items": items[:3]}, {"ok": True, "items": []}, now)
    assert small["weather"]["hours"] == s   # když se vejde, cloud dostane i hodinovou předpověď
