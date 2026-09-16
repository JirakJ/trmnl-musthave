"""Stav Kick kanálů přes neoficiální endpoint webu kick.com (bez klíčů)."""

from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

log = logging.getLogger(__name__)

CHANNEL_URL = "https://kick.com/api/v2/channels/{slug}"
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_0) AppleWebKit/537.36 trmnl-musthave",
    "Accept": "application/json",
}
LOCAL_TZ = ZoneInfo("Europe/Prague")


def local_hhmm(utc_naive: str) -> str:
    """'2026-09-16 12:17:11' (UTC) → '14:17' v Europe/Prague."""
    dt = datetime.strptime(utc_naive[:19], "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
    return dt.astimezone(LOCAL_TZ).strftime("%H:%M")


def parse_channel(slug: str, data: dict) -> dict:
    name = (data.get("user") or {}).get("username") or slug
    live = data.get("livestream")
    if not live:
        return {"n": name, "live": False}
    item = {"n": name, "live": True, "v": int(live.get("viewer_count") or 0)}
    categories = live.get("categories") or []
    if categories and categories[0].get("name"):
        item["g"] = categories[0]["name"]
    if live.get("created_at"):
        item["s"] = local_hhmm(live["created_at"])
    return item


def fetch_kick(http, slugs: list[str]) -> dict:
    """{"ok": bool, "items": [...]} v pořadí konfigurace; jednotlivé chyby → {"err": True}."""
    if not slugs:
        return {"ok": True, "items": []}

    def one(slug: str) -> dict:
        try:
            return parse_channel(slug, http.get_json(CHANNEL_URL.format(slug=slug), HEADERS))
        except Exception as err:  # noqa: BLE001
            log.warning("kick %s failed: %s", slug, err)
            return {"n": slug, "live": False, "err": True}

    with ThreadPoolExecutor(max_workers=min(5, len(slugs))) as pool:
        items = list(pool.map(one, slugs))
    if all(i.get("err") for i in items):
        return {"ok": False, "items": []}
    return {"ok": True, "items": items}
