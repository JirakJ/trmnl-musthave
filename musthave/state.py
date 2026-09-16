"""Stav mezi běhy: poslední odeslaný payload, čas odeslání a poslední dobré počasí."""

from __future__ import annotations

import json
import logging
from dataclasses import asdict, dataclass
from pathlib import Path

log = logging.getLogger(__name__)


@dataclass
class State:
    last_sent_at: float | None = None
    last_payload: dict | None = None
    last_weather: dict | None = None


def load_state(path: Path) -> State:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return State(**{k: data.get(k) for k in ("last_sent_at", "last_payload", "last_weather")})
    except FileNotFoundError:
        return State()
    except Exception as err:  # noqa: BLE001
        log.warning("state file unreadable, starting fresh: %s", err)
        return State()


def save_state(path: Path, state: State) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(asdict(state), ensure_ascii=False), encoding="utf-8")


def _comparable(payload: dict | None) -> dict | None:
    if payload is None:
        return None
    return {k: v for k, v in payload.items() if k != "updated"}


def should_send(state: State, payload: dict, now_ts: float, min_interval_s: int, heartbeat_s: int) -> tuple[bool, str]:
    if state.last_sent_at is None:
        return True, "first"
    elapsed = now_ts - state.last_sent_at
    if elapsed >= heartbeat_s:
        return True, "heartbeat"
    if _comparable(payload) == _comparable(state.last_payload):
        return False, "unchanged"
    if elapsed >= min_interval_s:
        return True, "changed"
    return False, "throttled"
