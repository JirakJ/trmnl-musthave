"""Klient TRMNL webhooku pro privátní plugin."""

from __future__ import annotations

WEBHOOK_URL = "https://trmnl.com/api/custom_plugins/{uuid}"


def send_webhook(http, uuid: str, payload: dict) -> tuple[int, str]:
    return http.post_json(
        WEBHOOK_URL.format(uuid=uuid),
        {"merge_variables": payload},
        {"Content-Type": "application/json"},
    )
