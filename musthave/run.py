"""Jeden běh sběrače: stáhnout → poskládat → rozhodnout → poslat → uložit stav."""

from __future__ import annotations

import json
import logging
import socket
import sys
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Mapping
from zoneinfo import ZoneInfo

from .config import Settings, load_settings
from .http import Http
from .kick import fetch_kick
from .payload import PayloadTooLarge, build_payload, encode
from .sources import all_down, with_last_good
from .state import State, load_state, save_state, should_send
from .trmnl import send_data, send_webhook
from .twitch import DEFAULT_CLIENT_ID, fetch_twitch
from .weather import fetch_weather, with_fallback

log = logging.getLogger("musthave")


@dataclass(frozen=True)
class Collected:
    payload: dict
    last_weather: dict | None   # záznamy posledních dobrých dat k uložení do State
    last_kick: dict | None
    last_twitch: dict | None
    all_down: bool              # žádný zdroj nemá čerstvá data (výpadek sítě) → obrazovku neměnit

    def __iter__(self):  # zpětná kompatibilita: `payload, last_weather = collect(...)`
        return iter((self.payload, self.last_weather))


def collect(http, settings: Settings, now: datetime, last_weather: dict | None = None, fw: str = "",
            last_kick: dict | None = None, last_twitch: dict | None = None) -> Collected:
    """Paralelně stáhne všechny zdroje a poskládá payload.

    Twitch Client-ID se při 400 obnoví za běhu. Když zdroj selže, ukáže se jeho poslední dobrý stav označený
    „stale“ (počasí ≤ 3 h, streamy ≤ 30 min); teprve potom „nedostupné“."""
    with ThreadPoolExecutor(max_workers=3) as pool:
        weather_f = pool.submit(fetch_weather, http, settings, now)
        kick_f = pool.submit(fetch_kick, http, settings.kick)
        twitch_f = pool.submit(fetch_twitch, http, settings.twitch, DEFAULT_CLIENT_ID)
        weather, kick = weather_f.result(), kick_f.result()
        twitch, _ = twitch_f.result()
    ts = now.timestamp()
    weather, rem_weather = with_fallback(weather, last_weather, ts)
    kick, rem_kick = with_last_good("kick", kick, last_kick, ts)
    twitch, rem_twitch = with_last_good("twitch", twitch, last_twitch, ts)
    host = settings.label or socket.gethostname().split(".")[0]
    payload = build_payload(weather, kick, twitch, now, host=host, fw=fw, countdowns=settings.countdowns)
    return Collected(payload, rem_weather, rem_kick, rem_twitch, all_down(weather, kick, twitch))


def run(
    root: Path,
    http=None,
    env: Mapping[str, str] | None = None,
    now: datetime | None = None,
    dry_run: bool = False,
    fetch_only: bool = False,
) -> int:
    settings = load_settings(root, env)
    http = http or Http()
    now = now or datetime.now(ZoneInfo(settings.timezone))
    state = load_state(settings.state_path)

    can_webhook = bool(settings.webhook_uuid)
    can_api = bool(settings.user_api_key and settings.plugin_setting_id)
    if not dry_run and not fetch_only and not (can_webhook or can_api):
        print(
            "Chybí cíl: nastav TRMNL_WEBHOOK_UUID (Webhook URL privátního pluginu), "
            "nebo TRMNL_USER_API_KEY + [trmnl] plugin_setting_id v config.toml.",
            file=sys.stderr,
        )
        return 2

    try:
        col = collect(http, settings, now, state.last_weather, last_kick=state.last_kick, last_twitch=state.last_twitch)
    except PayloadTooLarge as err:
        print(f"payload too large: {err}", file=sys.stderr)
        return 1
    payload = col.payload
    size = len(encode(payload))

    if dry_run or fetch_only:
        print(json.dumps(payload, ensure_ascii=False, indent=1))
        if dry_run:
            print(f"dry-run: {size} B, nothing sent", file=sys.stderr)
        return 0

    send, reason = should_send(state, payload, now.timestamp(), settings.min_interval_s, settings.heartbeat_s)
    if not send:
        log.info("skip (%s)", reason)
        remembered = State(state.last_sent_at, state.last_payload, col.last_weather, col.last_kick, col.last_twitch)
        if remembered != state:
            save_state(settings.state_path, remembered)
        return 0

    if can_webhook:
        status, text = send_webhook(http, settings.webhook_uuid, payload)
    else:
        status, text = send_data(http, settings.user_api_key, settings.plugin_setting_id, payload)
    if status == 429:
        log.error("429 rate limited by TRMNL; will retry next run")
        return 1
    if status >= 400:
        log.error("webhook failed %s: %s", status, text[:200])
        return 1
    save_state(settings.state_path, State(last_sent_at=now.timestamp(), last_payload=payload, last_weather=col.last_weather,
                                          last_kick=col.last_kick, last_twitch=col.last_twitch))
    log.info("sent (%s) %d B", reason, size)
    return 0
