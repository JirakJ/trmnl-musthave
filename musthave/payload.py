"""Skládání merge_variables pro TRMNL webhook (limit 5 kB bez TRMNL+, držíme rezervu)."""

from __future__ import annotations

import copy
import json
from datetime import datetime

MAX_BYTES = 4500
GAME_MAX = 22
GAME_MIN = 12


class PayloadTooLarge(Exception):
    pass


def encode(payload: dict) -> bytes:
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")


def sort_items(items: list[dict]) -> list[dict]:
    live = sorted((i for i in items if i.get("live")), key=lambda i: -int(i.get("v") or 0))
    offline = [i for i in items if not i.get("live")]
    return live + offline


def _truncate(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[: limit - 1] + "…"


def _section(source: dict, game_max: int) -> dict:
    items = [dict(i) for i in sort_items(source.get("items", []))]
    for i in items:
        if "g" in i:
            i["g"] = _truncate(i["g"], game_max)
    out = {"ok": bool(source.get("ok")), "items": items, "live": sum(1 for i in items if i.get("live"))}
    if source.get("fallback"):
        out["fallback"] = True
    return out


def build_payload(weather: dict, kick: dict, twitch: dict, now: datetime) -> dict:
    for game_max in (GAME_MAX, GAME_MIN):
        payload = {
            "updated": now.strftime("%H:%M"),
            "weather": copy.deepcopy(weather),
            "kick": _section(kick, game_max),
            "twitch": _section(twitch, game_max),
        }
        if len(encode(payload)) <= MAX_BYTES:
            return payload
    raise PayloadTooLarge(f"payload exceeds {MAX_BYTES} B even after truncation")
