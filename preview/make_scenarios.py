#!/usr/bin/env python3
"""Vygeneruje náhledové payloady (preview/scenarios/*.json) přes skutečný build_payload.

Spuštění: python3 preview/make_scenarios.py && .venv/bin/python preview/shot.py preview/scenarios/*.json
Data jsou smyšlená, ale tvarem odpovídají produkčním zdrojům (Open-Meteo, Kick, Twitch, ČHMÚ).
"""

from __future__ import annotations

import json
import sys
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from musthave.config import Countdown  # noqa: E402
from musthave.layout import View  # noqa: E402
from musthave.payload import build_payload  # noqa: E402
from musthave.streams import StreamsConfig  # noqa: E402
from musthave.weather import hourly_slots, rain_summary  # noqa: E402

TZ = ZoneInfo("Europe/Prague")
OUT = ROOT / "preview" / "scenarios"
NOW = datetime(2026, 11, 12, 14, 5, tzinfo=TZ)
COUNTDOWNS = (Countdown("GTA VI", date(2026, 11, 19)), Countdown("Vánoce", date(2026, 12, 24), yearly=True))
KICK_IDS = ["fattypillow", "czechfather", "astatoro", "miken", "czechcloud"]
TWITCH_IDS = ["arcadebulls", "agraelus", "artemis", "cruelladk", "conducteir77", "oliverovykecy", "rob2628"]


def weather(now=NOW, temps=None, probs=None, rains=None, ok=True):
    if not ok:
        return {"ok": False}
    t0 = int(now.replace(minute=0, second=0).timestamp())
    temps = temps or [8, 9, 9, 8, 7, 6, 5]
    probs = probs or [0, 10, 60, 90, 85, 40, 10]
    rains = rains or [0, 0, 1.2, 4.5, 2.1, 0.3, 0]
    hourly = {"time": [t0 + i * 3600 for i in range(7)], "temperature_2m": temps,
              "precipitation_probability": probs, "precipitation": rains}
    slots = hourly_slots(hourly, now, TZ)
    return {"ok": True, "temp": 8.4, "feels": 5.9, "hum": 81, "wind": 17, "code": 61, "label": "Déšť",
            "days": [{"d": "Čt", "hi": 9, "lo": 3, "p": 90, "code": 61, "label": "Déšť"},
                     {"d": "Pá", "hi": 7, "lo": 1, "p": 20, "code": 3, "label": "Zataženo"},
                     {"d": "So", "hi": 6, "lo": -1, "p": 5, "code": 1, "label": "Skoro jasno"}],
            "hours": slots, "rain": rain_summary(slots)}


def item(i, live=False, v=0, g="Just Chatting", s="12:02", n=None):
    d = {"id": i, "n": n or i.capitalize(), "live": live}
    if live:
        d.update({"v": v, "g": g, "s": s})
    return d


def kick(live=()):
    return {"ok": True, "items": [item(i, i in live, 1200 - 100 * k, "Valheim") for k, i in enumerate(KICK_IDS)]}


def twitch(live=()):
    return {"ok": True, "items": [item(i, i in live, 5000 - 700 * k, "World of Warcraft") for k, i in enumerate(TWITCH_IDS)]}


def save(name, payload):
    payload["battery_percent"] = payload.get("battery_percent") or 64
    (OUT / f"{name}.json").write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")
    print(name)


def build(name, w=None, k=None, t=None, now=NOW, cfg=StreamsConfig(), view=None, extras=None, **kw):
    save(name, build_payload(w or weather(now), k or kick(("astatoro", "miken")), t or twitch(("rob2628", "agraelus", "artemis")),
                             now, host="RPi", fw="2.0.9", countdowns=COUNTDOWNS, max_bytes=None, location="Jihlava",
                             streams_cfg=cfg, view=view, extras=extras, **kw))


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    build("default")
    build("hourly-heavy", w=weather(probs=[0, 10, 60, 90, 85, 40, 10], rains=[0, 0, 2.5, 18.0, 6.1, 0.4, 0]))
    build("hourly-dry", w=weather(probs=[0, 5, 5, 0, 0, 10, 0], rains=[0] * 7))
    build("hourly-missing", w=weather(temps=[10, None, 12, None, None, None, None],
                                      probs=[None, 30, None, None, None, None, None], rains=[None, 0.5, None, None, None, None, None]))
    late = datetime(2026, 11, 12, 22, 40, tzinfo=TZ)
    build("hourly-midnight", w=weather(late, [4, 3, 3, 2, -1, -2, -3], [5, 20, 35, 40, 30, 10, 0], [0, 0.2, 0.3, 0.6, 0.2, 0, 0]), now=late)
    build("saving-stale", w={**weather(), "stale": True, "since": datetime(2026, 11, 12, 11, 50, tzinfo=TZ).timestamp()},
          k={**kick(("astatoro",)), "stale": True, "since": datetime(2026, 11, 12, 13, 50, tzinfo=TZ).timestamp()}, saving="quiet+low")
    alert = {"ok": True, "count": 3, "src": "ČHMÚ", "sent": "10:34", "level": 3, "label": "vysoký stupeň",
             "event": "Velmi kluzké povrchy", "valid": "od zítra 3:00 do zítra 10:00"}
    build("alert-severe-multi", extras={"alert": alert})
    build("alert-yellow", extras={"alert": {**alert, "count": 1, "level": 2, "label": "nízký stupeň", "event": "Silný vítr",
                                            "valid": "od 18:00 do zítra 6:00"}})
    build("alert-unavailable", extras={"alert": {"ok": False, "src": "ČHMÚ"}})
    build("streams-none-live", k=kick(), t=twitch(), cfg=StreamsConfig(mode="live_summary"))
    build("streams-over-limit", t=twitch(tuple(TWITCH_IDS)), cfg=StreamsConfig(mode="live_summary", max_items=5))
    same_k = {"ok": True, "items": [item("agraelus", True, 320, "Counter-Strike 2", n="Agraelus"), item("miken")]}
    same_t = {"ok": True, "items": [item("agraelus", True, 320, "Warhammer 40,000: Space Marine 2 – Ultra", n="Agraelus"),
                                    item("rob2628", True, 5100, "Diablo IV", n="Rob2628")]}
    build("streams-combined-same-name", k=same_k, t=same_t, cfg=StreamsConfig(combined=True, favorites=("twitch:agraelus",)))
    # #13 rozložení
    build("layout-all-offline-hidden", k=kick(), t=twitch(),
          view=View(panels=("kick", "twitch"), hide_empty=("kick", "twitch")))
    build("layout-compact-shrink", k=kick(), view=View(panels=("kick", "twitch"), weather="compact", shrink_empty=("kick",)))
    long_t = {"ok": True, "items": [item(i, True, 99999, "Warhammer 40,000: Space Marine 2 – Ultra Deluxe Edition",
                                         n="Velmidlouhejmenostreamera" + str(k)) for k, i in enumerate(TWITCH_IDS)]}
    build("layout-long-texts", t=long_t, view=View(panels=("twitch", "kick"), widths=(("twitch", 2),), weather="off"))
    # #10 agenda
    agenda = {"ok": True, "items": [
        {"day": "dnes", "n": "Týdenní porada týmu", "t": "13:30", "e": "14:30", "now": True, "l": "Zasedačka 2. patro"},
        {"day": "dnes", "n": "Zubař", "t": "16:15", "e": "17:00", "l": "Masarykovo nám. 12"},
        {"day": "zítra", "n": "Státní svátek", "all": True},
        {"day": "zítra", "n": "Oběd s Michalem a dlouhý název události, který se nevejde", "t": "12:00", "e": "13:00"},
        {"day": "zítra", "n": "Trénink", "t": "18:00", "e": "19:30"}], "more": 2}
    build("agenda-workday", extras={"agenda": agenda}, view=View(panels=("agenda", "streams"), weather="compact",
                                                                   widths=(("agenda", 2),)), cfg=StreamsConfig(mode="live"))
    build("agenda-empty-today", extras={"agenda": {"ok": True, "today_empty": True, "stale": True, "as_of": "11:50",
                                                    "items": [{"day": "zítra", "n": "Trénink", "t": "18:00", "e": "19:30"}]}},
          view=View(panels=("agenda", "kick", "twitch")))


if __name__ == "__main__":
    main()
