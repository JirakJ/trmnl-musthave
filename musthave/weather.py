"""Počasí z Open-Meteo (bez API klíče)."""

from __future__ import annotations

import logging
from datetime import date, datetime
from urllib.parse import urlencode

from .config import Settings

log = logging.getLogger(__name__)

API = "https://api.open-meteo.com/v1/forecast"
DAYS_CS = ["Po", "Út", "St", "Čt", "Pá", "So", "Ne"]

WMO_LABELS: dict[int, str] = {
    0: "Jasno",
    1: "Skoro jasno",
    2: "Polojasno",
    3: "Zataženo",
    45: "Mlha",
    48: "Mlha",
    51: "Mrholení",
    53: "Mrholení",
    55: "Mrholení",
    56: "Mrznoucí mrholení",
    57: "Mrznoucí mrholení",
    61: "Déšť",
    63: "Déšť",
    65: "Silný déšť",
    66: "Mrznoucí déšť",
    67: "Mrznoucí déšť",
    71: "Sněžení",
    73: "Sněžení",
    75: "Silné sněžení",
    77: "Sněhové zrna",
    80: "Přeháňky",
    81: "Přeháňky",
    82: "Silné přeháňky",
    85: "Sněhové přeháňky",
    86: "Sněhové přeháňky",
    95: "Bouřka",
    96: "Bouřka",
    99: "Bouřka s kroupami",
}


def wmo_label(code: int) -> str:
    return WMO_LABELS.get(code, "—")


def fetch_weather(http, settings: Settings, now: datetime) -> dict:
    """Vrací {"ok": True, temp, feels, hum, wind, code, label, days[3]} nebo {"ok": False}."""
    query = urlencode(
        {
            "latitude": settings.latitude,
            "longitude": settings.longitude,
            "current": "temperature_2m,relative_humidity_2m,apparent_temperature,weather_code,wind_speed_10m",
            "daily": "weather_code,temperature_2m_max,temperature_2m_min,precipitation_probability_max",
            "timezone": settings.timezone,
            "forecast_days": 3,
        }
    )
    try:
        data = http.get_json(f"{API}?{query}")
        cur, daily = data["current"], data["daily"]
        days = []
        for i, day in enumerate(daily["time"]):
            days.append(
                {
                    "d": DAYS_CS[date.fromisoformat(day).weekday()],
                    "hi": round(daily["temperature_2m_max"][i]),
                    "lo": round(daily["temperature_2m_min"][i]),
                    "p": int(daily["precipitation_probability_max"][i] or 0),
                    "code": int(daily["weather_code"][i]),
                    "label": wmo_label(int(daily["weather_code"][i])),
                }
            )
        return {
            "ok": True,
            "temp": cur["temperature_2m"],
            "feels": cur["apparent_temperature"],
            "hum": int(cur["relative_humidity_2m"]),
            "wind": round(cur["wind_speed_10m"]),
            "code": int(cur["weather_code"]),
            "label": wmo_label(int(cur["weather_code"])),
            "days": days[:3],
        }
    except Exception as err:  # noqa: BLE001 - jeden zdroj nesmí shodit celý běh
        log.warning("weather failed: %s", err)
        return {"ok": False}


from .sources import STALE_MAX_S as _STALE, with_last_good

STALE_MAX_S = _STALE["weather"]


def with_fallback(weather: dict, last_weather: dict | None, now_ts: float) -> tuple[dict, dict | None]:
    """Když aktuální fetch selhal, vrátí poslední dobré počasí (do 3 h) označené "stale".

    Vrací (počasí k zobrazení, záznam k uložení do state; formát {"ts", "weather"} kvůli starším state souborům)."""
    last = {"ts": last_weather.get("ts", 0), "data": last_weather.get("weather", {})} if last_weather else None
    shown, remembered = with_last_good("weather", weather, last, now_ts)
    return shown, ({"ts": remembered["ts"], "weather": remembered["data"]} if remembered else None)
