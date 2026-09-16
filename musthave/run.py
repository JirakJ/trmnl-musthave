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
from .trmnl import send_webhook
from .twitch import DEFAULT_CLIENT_ID, fetch_twitch
from .weather import fetch_weather

log = logging.getLogger("musthave")


def collect(http, settings: Settings, state: State, now: datetime) -> tuple[dict, State]:
    client_id = state.twitch_client_id or DEFAULT_CLIENT_ID
    with ThreadPoolExecutor(max_workers=3) as pool:
        weather_f = pool.submit(fetch_weather, http, settings, now)
        kick_f = pool.submit(fetch_kick, http, settings.kick)
        twitch_f = pool.submit(fetch_twitch, http, settings.twitch, client_id)
        weather, kick = weather_f.result(), kick_f.result()
        twitch, used_client_id = twitch_f.result()
    payload = build_payload(weather, kick, twitch, now)
    return payload, State(state.last_sent_at, state.last_payload, used_client_id)


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

    if not dry_run and not fetch_only and not settings.webhook_uuid:
        print("TRMNL_WEBHOOK_UUID chybí (.env nebo prostředí). Vezmi UUID z Webhook URL privátního pluginu.", file=sys.stderr)
        return 2

    try:
        payload, new_state = collect(http, settings, state, now)
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
        save_state(settings.state_path, new_state)
        return 0

    status, text = send_webhook(http, settings.webhook_uuid, payload)
    if status == 429:
        log.error("429 rate limited by TRMNL; will retry next run")
        return 1
    if status >= 400:
        log.error("webhook failed %s: %s", status, text[:200])
        return 1
    new_state.last_sent_at = now.timestamp()
    new_state.last_payload = payload
    save_state(settings.state_path, new_state)
    log.info("sent (%s) %d B", reason, size)
    return 0
