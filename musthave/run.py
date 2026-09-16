"""Jeden běh sběrače: stáhnout → poskládat → rozhodnout → poslat → uložit stav."""

from __future__ import annotations

import json
import logging
import sys
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path
from typing import Mapping
from zoneinfo import ZoneInfo

from .config import Settings, load_settings
from .http import Http
from .kick import fetch_kick
from .payload import PayloadTooLarge, build_payload, encode
from .state import State, load_state, save_state, should_send
from .trmnl import send_data, send_webhook
from .twitch import DEFAULT_CLIENT_ID, fetch_twitch
from .weather import fetch_weather

log = logging.getLogger("musthave")


def collect(http, settings: Settings, now: datetime) -> dict:
    """Paralelně stáhne všechny zdroje a poskládá payload. Twitch Client-ID se při 400 obnoví za běhu."""
    with ThreadPoolExecutor(max_workers=3) as pool:
        weather_f = pool.submit(fetch_weather, http, settings, now)
        kick_f = pool.submit(fetch_kick, http, settings.kick)
        twitch_f = pool.submit(fetch_twitch, http, settings.twitch, DEFAULT_CLIENT_ID)
        weather, kick = weather_f.result(), kick_f.result()
        twitch, _ = twitch_f.result()
    return build_payload(weather, kick, twitch, now)


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
        payload = collect(http, settings, now)
    except PayloadTooLarge as err:
        print(f"payload too large: {err}", file=sys.stderr)
        return 1
    size = len(encode(payload))

    if dry_run or fetch_only:
        print(json.dumps(payload, ensure_ascii=False, indent=1))
        if dry_run:
            print(f"dry-run: {size} B, nothing sent", file=sys.stderr)
        return 0

    send, reason = should_send(state, payload, now.timestamp(), settings.min_interval_s, settings.heartbeat_s)
    if not send:
        log.info("skip (%s)", reason)
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
    save_state(settings.state_path, State(last_sent_at=now.timestamp(), last_payload=payload))
    log.info("sent (%s) %d B", reason, size)
    return 0
