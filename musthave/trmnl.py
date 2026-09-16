"""Klienti TRMNL pro zápis merge variables do privátního pluginu.

Dvě cesty:
- webhook (UUID z Webhook URL, bez auth): POST /api/custom_plugins/<uuid>
- autentizovaná (uživatelský API klíč + číselné id instance): POST /api/plugin_settings/<id>/data
"""

from __future__ import annotations

WEBHOOK_URL = "https://trmnl.com/api/custom_plugins/{uuid}"
DATA_URL = "https://trmnl.com/api/plugin_settings/{id}/data"
JSON = {"Content-Type": "application/json"}


def send_webhook(http, uuid: str, payload: dict) -> tuple[int, str]:
    return http.post_json(WEBHOOK_URL.format(uuid=uuid), {"merge_variables": payload}, dict(JSON))


def send_data(http, api_key: str, plugin_setting_id: int, payload: dict) -> tuple[int, str]:
    headers = {**JSON, "Authorization": f"Bearer {api_key}"}
    return http.post_json(DATA_URL.format(id=plugin_setting_id), {"merge_variables": payload}, headers)
