"""Stav Twitch kanálů bez vývojářské aplikace.

Primárně veřejný GraphQL endpoint, který používá samotný web twitch.tv (Client-ID je
veřejná hodnota vložená v HTML stránky; při 400 se znovu vyčte ze stránky).
Fallback: decapi.me (jen online/offline + diváci).
"""

from __future__ import annotations

import json
import logging
import re
from datetime import datetime
from zoneinfo import ZoneInfo

log = logging.getLogger(__name__)

# Veřejné Client-ID webové aplikace twitch.tv (atribut clientId v HTML www.twitch.tv), není tajemství.
DEFAULT_CLIENT_ID = "kimne78kx3ncx6brgo4mv6wki5h1ko"
GQL_URL = "https://gql.twitch.tv/gql"
PAGE_URL = "https://www.twitch.tv/"
DECAPI_URL = "https://decapi.me/twitch/viewercount/{login}"
QUERY = (
    "query($logins:[String!]){users(logins:$logins){login displayName "
    "stream{title viewersCount createdAt game{displayName}}}}"
)
LOCAL_TZ = ZoneInfo("Europe/Prague")
CLIENT_ID_RE = re.compile(r'clientId="([a-z0-9]{30,})"')


def discover_client_id(http) -> str | None:
    try:
        match = CLIENT_ID_RE.search(http.get_text(PAGE_URL, {"User-Agent": "Mozilla/5.0"}))
    except Exception as err:  # noqa: BLE001
        log.warning("twitch client id discovery failed: %s", err)
        return None
    return match.group(1) if match else None


def local_hhmm(iso_utc: str) -> str:
    return datetime.fromisoformat(iso_utc.replace("Z", "+00:00")).astimezone(LOCAL_TZ).strftime("%H:%M")


def parse_user(login: str, user: dict | None) -> dict:
    if not user:
        return {"n": login, "live": False}
    name = user.get("displayName") or login
    stream = user.get("stream")
    if not stream:
        return {"n": name, "live": False}
    item = {"n": name, "live": True, "v": int(stream.get("viewersCount") or 0)}
    game = (stream.get("game") or {}).get("displayName")
    if game:
        item["g"] = game
    if stream.get("createdAt"):
        item["s"] = local_hhmm(stream["createdAt"])
    return item


def _gql(http, logins: list[str], client_id: str) -> list[dict]:
    body = [{"query": QUERY, "variables": {"logins": logins}}]
    status, text = http.post_json(GQL_URL, body, {"Client-Id": client_id, "Content-Type": "application/json"})
    if status == 400:
        raise PermissionError("client id rejected")
    if status != 200:
        raise RuntimeError(f"gql status {status}")
    users = json.loads(text)[0]["data"]["users"]
    return [parse_user(login, user) for login, user in zip(logins, users)]


def _decapi(http, logins: list[str]) -> list[dict]:
    items, failures = [], 0
    for login in logins:
        try:
            text = http.get_text(DECAPI_URL.format(login=login)).strip()
        except Exception as err:  # noqa: BLE001
            log.warning("decapi %s failed: %s", login, err)
            failures += 1
            items.append({"n": login, "live": False, "err": True})
            continue
        if text.endswith("is offline") or not text.replace(",", "").isdigit():
            items.append({"n": login, "live": False})
        else:
            items.append({"n": login, "live": True, "v": int(text.replace(",", ""))})
    if failures == len(logins):
        raise RuntimeError("decapi unavailable")
    return items


def fetch_twitch(http, logins: list[str], client_id: str) -> tuple[dict, str]:
    """Vrací ({"ok", "items", ["fallback"]}, použité client_id)."""
    if not logins:
        return {"ok": True, "items": []}, client_id
    try:
        return {"ok": True, "items": _gql(http, logins, client_id)}, client_id
    except PermissionError:
        fresh = discover_client_id(http)
        if fresh and fresh != client_id:
            try:
                return {"ok": True, "items": _gql(http, logins, fresh)}, fresh
            except Exception as err:  # noqa: BLE001
                log.warning("twitch gql retry failed: %s", err)
        else:
            log.warning("twitch gql rejected client id and discovery found nothing new")
    except Exception as err:  # noqa: BLE001
        log.warning("twitch gql failed: %s", err)
    try:
        return {"ok": True, "items": _decapi(http, logins), "fallback": True}, client_id
    except Exception as err:  # noqa: BLE001
        log.warning("twitch fallback failed: %s", err)
        return {"ok": False, "items": []}, client_id
