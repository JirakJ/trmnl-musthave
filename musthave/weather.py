"""Počasí z Open-Meteo (bez API klíče)."""

from __future__ import annotations

import logging
import math
from datetime import date, datetime
from urllib.parse import urlencode
from zoneinfo import ZoneInfo

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


HOURS = 6  # hodinový přehled: aktuální hodina + 5 dalších


def _num(values: list | None, i: int) -> float | None:
    """Hodnota z pole Open-Meteo, nebo None (chybí / null / mimo rozsah). Chybějící data nejsou nula."""
    if not values or i < 0 or i >= len(values) or values[i] is None:
        return None
    try:
        value = float(values[i])
    except (TypeError, ValueError):
        return None
    return value if math.isfinite(value) else None


def _day_of(value, tz: ZoneInfo) -> date:
    """Den z `daily.time`: unix timestamp (timeformat=unixtime) nebo ISO řetězec (starší odpovědi/fixtures)."""
    if isinstance(value, (int, float)):
        return datetime.fromtimestamp(value, tz).date()
    return date.fromisoformat(str(value)[:10])


def hourly_slots(hourly: dict, now: datetime, tz: ZoneInfo, count: int = HOURS) -> list[dict]:
    """Hodinová okna od aktuální hodiny: [{h, t, p, mm}].

    Open-Meteo hlásí srážky (`precipitation`, `precipitation_probability`) za PŘEDCHOZÍ hodinu, teplotu okamžitě.
    Okno h:00–h+1:00 proto bere teplotu z času h:00 a srážky z času h+1:00. Časy jsou unix timestamp, takže
    přechod přes půlnoc i změna času (dvě „02“ v říjnu, žádná „02“ v březnu) odpovídají skutečnosti."""
    times = hourly.get("time") or []
    index = {}
    for i, ts in enumerate(times):
        if isinstance(ts, (int, float)):
            index[int(ts)] = i
    start = int(now.timestamp()) // 3600 * 3600  # celé hodiny jsou stejné v UTC i v CET/CEST
    slots = []
    for k in range(count):
        begin = start + k * 3600
        i_begin, i_end = index.get(begin), index.get(begin + 3600)
        temp = _num(hourly.get("temperature_2m"), i_begin) if i_begin is not None else None
        prob = _num(hourly.get("precipitation_probability"), i_end) if i_end is not None else None
        rain = _num(hourly.get("precipitation"), i_end) if i_end is not None else None
        slots.append({
            "ts": begin,
            "h": datetime.fromtimestamp(begin, tz).strftime("%H"),
            "t": round(temp) if temp is not None else None,
            "p": int(round(prob)) if prob is not None else None,
            "mm": round(rain, 1) if rain is not None else None,
        })
    return slots


def _hour_end(slot_hour: str) -> str:
    return f"{(int(slot_hour) + 1) % 24}"


def rain_summary(slots: list[dict]) -> str:
    """Krátké shrnutí srážek za hodinová okna (bez předstírání minutové přesnosti).

    - bez dat → „Hodinová předpověď nedostupná“
    - max. pravděpodobnost < 20 % a úhrn < 0,1 mm → „Bez srážek do HH h“
    - jinak souvislý úsek kolem nejvyšší pravděpodobnosti (≥ max − 10 p. b.) → „Nejvyšší šance srážek 15–17 h (70 %)“
    Neúplná data se přiznají („část dat chybí“)."""
    probs = [s["p"] for s in slots if s.get("p") is not None]
    rains = [s["mm"] for s in slots if s.get("mm") is not None]
    if not slots or (not probs and not rains):
        return "Hodinová předpověď nedostupná"
    missing = any(s.get("p") is None for s in slots)
    suffix = " · část dat chybí" if missing else ""
    total = round(sum(rains), 1)
    top = max(probs) if probs else 0
    if top < 20 and total < 0.1:
        last = slots[-1]["h"]
        return f"Bez srážek do {_hour_end(last)} h{suffix}"
    if not probs:  # jen úhrny bez pravděpodobnosti
        return f"Srážky do {_hour_end(slots[-1]['h'])} h: {format_mm(total)}{suffix}"
    best = next(i for i, s in enumerate(slots) if s.get("p") == top)
    lo = hi = best
    threshold = max(20, top - 10)
    while lo > 0 and (slots[lo - 1].get("p") or -1) >= threshold:
        lo -= 1
    while hi < len(slots) - 1 and (slots[hi + 1].get("p") or -1) >= threshold:
        hi += 1
    # U+2060 (word joiner) a NBSP: rozsah „23–2 h“ se na displeji nezalomí uprostřed
    window = f"{int(slots[lo]['h'])}⁠–⁠{_hour_end(slots[hi]['h'])} h"
    amount = f" · {format_mm(total)}" if total >= 0.1 else ""
    return f"Nejvyšší šance srážek {window} ({top} %){amount}{suffix}"


def format_mm(value: float) -> str:
    return f"{value:.1f} mm".replace(".", ",")


def fetch_weather(http, settings: Settings, now: datetime) -> dict:
    """Vrací {"ok": True, temp, feels, hum, wind, code, label, days[3], hours[6], rain} nebo {"ok": False}."""
    query = urlencode(
        {
            "latitude": settings.latitude,
            "longitude": settings.longitude,
            "current": "temperature_2m,relative_humidity_2m,apparent_temperature,weather_code,wind_speed_10m",
            "daily": "weather_code,temperature_2m_max,temperature_2m_min,precipitation_probability_max",
            "hourly": "temperature_2m,precipitation_probability,precipitation",
            "timezone": settings.timezone,
            "forecast_days": 3,
            "timeformat": "unixtime",
        }
    )
    tz = ZoneInfo(settings.timezone)
    try:
        data = http.get_json(f"{API}?{query}")
        cur, daily = data["current"], data["daily"]
        days = []
        for i, day in enumerate(daily["time"]):
            days.append(
                {
                    "d": DAYS_CS[_day_of(day, tz).weekday()],
                    "hi": round(daily["temperature_2m_max"][i]),
                    "lo": round(daily["temperature_2m_min"][i]),
                    "p": int(daily["precipitation_probability_max"][i] or 0),
                    "code": int(daily["weather_code"][i]),
                    "label": wmo_label(int(daily["weather_code"][i])),
                }
            )
        out_hours = {}
        if isinstance(data.get("hourly"), dict):
            slots = hourly_slots(data["hourly"], now, tz)
            out_hours = {"hours": slots, "rain": rain_summary(slots)}
        return {
            **out_hours,
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
