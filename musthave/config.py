"""Načtení config.toml + .env do neměnného Settings objektu."""

from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping


@dataclass(frozen=True)
class Settings:
    location_name: str
    latitude: float
    longitude: float
    timezone: str
    kick: list[str]
    twitch: list[str]
    min_interval_s: int
    heartbeat_s: int
    webhook_uuid: str | None
    user_api_key: str | None
    state_path: Path


def read_dotenv(path: Path) -> dict[str, str]:
    """Minimalistický parser KEY=VALUE řádků; komentáře a prázdné řádky ignoruje."""
    values: dict[str, str] = {}
    if not path.exists():
        return values
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        values[key.strip()] = value
    return values


def load_settings(root: Path, env: Mapping[str, str] | None = None) -> Settings:
    env = os.environ if env is None else env
    raw = tomllib.loads((root / "config.toml").read_text(encoding="utf-8"))
    merged = {**read_dotenv(root / ".env"), **env}
    loc, streams, send = raw["location"], raw["streams"], raw.get("send", {})
    return Settings(
        location_name=loc["name"],
        latitude=float(loc["latitude"]),
        longitude=float(loc["longitude"]),
        timezone=loc.get("timezone", "Europe/Prague"),
        kick=[s.strip().lower() for s in streams.get("kick", [])],
        twitch=[s.strip().lower() for s in streams.get("twitch", [])],
        min_interval_s=int(send.get("min_interval_minutes", 6)) * 60,
        heartbeat_s=int(send.get("heartbeat_minutes", 15)) * 60,
        webhook_uuid=merged.get("TRMNL_WEBHOOK_UUID") or None,
        user_api_key=merged.get("TRMNL_USER_API_KEY") or None,
        state_path=root / "state" / "last.json",
    )
