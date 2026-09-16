"""Tenký HTTP klient nad urllib. Injektuje se do fetcherů, aby testy nešly na síť."""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from typing import Any

USER_AGENT = "trmnl-musthave/1.0 (+https://github.com/JirakJ)"


class Http:
    def __init__(self, timeout: float = 15.0) -> None:
        self.timeout = timeout

    def _request(self, url: str, headers: dict[str, str] | None, data: bytes | None = None) -> tuple[int, str]:
        req = urllib.request.Request(url, data=data, headers={"User-Agent": USER_AGENT, **(headers or {})})
        with urllib.request.urlopen(req, timeout=self.timeout) as resp:
            return resp.status, resp.read().decode("utf-8", errors="replace")

    def get_text(self, url: str, headers: dict[str, str] | None = None) -> str:
        return self._request(url, headers)[1]

    def get_json(self, url: str, headers: dict[str, str] | None = None) -> Any:
        return json.loads(self.get_text(url, {"Accept": "application/json", **(headers or {})}))

    def post_json(self, url: str, body: Any, headers: dict[str, str] | None = None) -> tuple[int, str]:
        """Vrací (status, text). HTTP chyby (4xx/5xx) nevyhazuje, aby šlo reagovat na 429."""
        data = json.dumps(body, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        try:
            return self._request(url, {"Content-Type": "application/json", **(headers or {})}, data)
        except urllib.error.HTTPError as err:
            return err.code, err.read().decode("utf-8", errors="replace")
