"""BYOS režim v1: HTTP server pro zařízení + smyčka, která každých N sekund stáhne data a vyrenderuje snímek.

Snímky jdou do FrameStore (poslední 8 bitmap); server z nich počítá regiony pro částečný refresh.
Když stažení nebo render selže, zůstane poslední snímek a zařízení dostane `none`.
Volitelně se data pošlou i do TRMNL cloudu (webhook), aby šlo kdykoli přepnout zpět.
"""

from __future__ import annotations

import json
import logging
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Callable, Mapping
from zoneinfo import ZoneInfo

from .config import load_settings
from .devices import DeviceRegistry
from .frames import FrameStore, png_to_bitmap
from .http import Http
from .run import collect
from .screen import render_screen
from .server import make_server
from .state import State, load_state, save_state, should_send
from .trmnl import send_data, send_webhook

log = logging.getLogger("musthave.serve")


def _push(settings, http, state: State, payload: dict, now: datetime, last_weather) -> State:
    can_webhook = bool(settings.webhook_uuid)
    can_api = bool(settings.user_api_key and settings.plugin_setting_id)
    if not (can_webhook or can_api):
        return State(state.last_sent_at, state.last_payload, last_weather)
    send, reason = should_send(state, payload, now.timestamp(), settings.min_interval_s, settings.heartbeat_s)
    if not send:
        return State(state.last_sent_at, state.last_payload, last_weather)
    if can_webhook:
        status, text = send_webhook(http, settings.webhook_uuid, payload)
    else:
        status, text = send_data(http, settings.user_api_key, settings.plugin_setting_id, payload)
    if status >= 400:
        log.warning("trmnl push failed %s: %s", status, text[:120])
        return State(state.last_sent_at, state.last_payload, last_weather)
    log.info("trmnl push (%s)", reason)
    return State(now.timestamp(), payload, last_weather)


def tick(
    root: Path,
    frames: FrameStore,
    http=None,
    env: Mapping[str, str] | None = None,
    now: datetime | None = None,
    renderer: Callable[[dict], bytes] | None = None,
    push: bool = False,
) -> bool:
    """Jeden průchod: data → (TRMNL push) → render → FrameStore. Vrací True, když vznikl snímek."""
    settings = load_settings(root, env)
    http = http or Http()
    now = now or datetime.now(ZoneInfo(settings.timezone))
    state = load_state(settings.state_path)
    renderer = renderer or (lambda payload: render_screen(payload, settings.chrome, "png", headless=settings.headless))

    seen = DeviceRegistry(root / "state" / "devices.json").latest_seen()
    fw = seen.fw_version if seen and seen.fw_version else ""
    try:
        payload, last_weather = collect(http, settings, now, state.last_weather, fw=fw)
    except Exception as err:  # noqa: BLE001
        log.error("collect failed, keeping last frame: %s", err)
        return False

    new_state = _push(settings, http, state, payload, now, last_weather) if push else State(state.last_sent_at, state.last_payload, last_weather)
    save_state(settings.state_path, new_state)

    # Stejná data → stejný snímek. Čas "aktualizováno" sám o sobě změnu nedělá, jinak by zařízení
    # překreslovalo každou minutu jen kvůli hodinám v hlavičce.
    rendered_path = frames.dir / "last_payload.json"
    comparable = {k: v for k, v in payload.items() if k != "updated"}
    try:
        previous = json.loads(rendered_path.read_text(encoding="utf-8"))
    except (FileNotFoundError, ValueError):
        previous = None
    if previous == comparable and frames.latest() is not None:
        log.info("data unchanged, keeping frame %s", frames.latest())
        return True

    try:
        png = renderer(payload)
        fid = frames.put(png_to_bitmap(png))
    except Exception as err:  # noqa: BLE001
        log.error("render failed, keeping last frame: %s", err)
        return False
    rendered_path.write_text(json.dumps(comparable, ensure_ascii=False), encoding="utf-8")
    log.info("frame %s", fid)
    return True


def serve(root: Path, port: int | None = None, push: bool = False, once: bool = False) -> int:
    settings = load_settings(root)
    frames = FrameStore(root / "state" / "frames")
    devices = DeviceRegistry(root / "state" / "devices.json")
    srv = make_server(frames, devices, settings.policy, port=port if port is not None else settings.server_port,
                      firmware_dir=settings.firmware_dir)
    threading.Thread(target=srv.serve_forever, daemon=True, name="byos-http").start()
    interval = min(settings.policy.interval_usb, settings.policy.interval_battery)
    log.info("BYOS v1 server on port %d, render every %ds, push_to_trmnl=%s", srv.server_port, interval, push)
    try:
        while True:
            started = time.monotonic()
            try:
                tick(root, frames, push=push)
            except Exception as err:  # noqa: BLE001
                log.exception("tick crashed: %s", err)
            if once:
                return 0
            time.sleep(max(5.0, interval - (time.monotonic() - started)))
    except KeyboardInterrupt:
        return 0
    finally:
        srv.shutdown()
