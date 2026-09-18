"""TRMNL framework CSS/JS pro lokální render: cache na disku, aby render nikdy nečekal na síť.

Render v headless Chromiu má rozpočet několik sekund; když se plugins.css z trmnl.com nestihne stáhnout
(pomalá Wi-Fi na Raspberry Pi), sloupce se rozpadnou pod sebe. Proto se framework stahuje mimo render
(s dlouhým timeoutem, gzip), ukládá do `state/cache/` a Chromium dostává `file://` cestu. Stará kopie se
používá dál, když obnova selže; bez jakékoli kopie se (s varováním) použije vzdálená URL jako dřív.
"""

from __future__ import annotations

import gzip
import logging
import os
import time
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

log = logging.getLogger(__name__)

FRAMEWORK_CSS_URL = "https://trmnl.com/css/latest/plugins.css"
FRAMEWORK_JS_URL = "https://trmnl.com/js/latest/plugins.js"
MAX_AGE_S = 24 * 3600     # obnova nejdřív po dni (trmnl.com posílá max-age 4 h, ale nám stačí denně)
FETCH_TIMEOUT_S = 60      # stahování běží mimo render, může trvat
RETRY_AFTER_FAIL_S = 300  # po neúspěchu nezkoušet znovu při každém renderu

ASSETS = (
    ("plugins.css", FRAMEWORK_CSS_URL, b".trmnl .columns"),  # sanity: bez sloupců to není framework
    ("plugins.js", FRAMEWORK_JS_URL, b"function"),
)


@dataclass(frozen=True)
class Framework:
    css: str        # URL nebo file:// URI pro <link>
    js: str
    source: str     # "cache" | "stale-cache" | "remote"


def fetch(url: str, timeout_s: float = FETCH_TIMEOUT_S) -> bytes:
    req = urllib.request.Request(url, headers={"Accept-Encoding": "gzip", "User-Agent": "trmnl-musthave"})
    with urllib.request.urlopen(req, timeout=timeout_s) as r:
        body = r.read()
        if (r.headers.get("Content-Encoding") or "").lower() == "gzip":
            body = gzip.decompress(body)
        return body


class FrameworkCache:
    def __init__(self, directory: Path, fetch_fn: Callable[[str], bytes] = fetch, now_fn: Callable[[], float] = time.time,
                 max_age_s: float = MAX_AGE_S) -> None:
        self.dir = Path(directory)
        self.fetch_fn = fetch_fn
        self.now_fn = now_fn
        self.max_age_s = max_age_s
        self._failed_at: dict[str, float] = {}

    def _path(self, name: str) -> Path:
        return self.dir / name

    def _fresh(self, path: Path) -> bool:
        try:
            return self.now_fn() - path.stat().st_mtime < self.max_age_s
        except FileNotFoundError:
            return False

    def _refresh(self, name: str, url: str, marker: bytes) -> bool:
        """Stáhne a atomicky uloží; False při chybě nebo nesmyslném obsahu (stará kopie zůstane)."""
        failed = self._failed_at.get(name)
        if failed is not None and self.now_fn() - failed < RETRY_AFTER_FAIL_S:
            return False
        try:
            body = self.fetch_fn(url)
            if marker not in body:
                raise ValueError(f"unexpected content ({len(body)} B)")
        except Exception as err:  # noqa: BLE001
            self._failed_at[name] = self.now_fn()
            log.warning("framework %s: refresh failed (%s)", name, err)
            return False
        self.dir.mkdir(parents=True, exist_ok=True)
        tmp = self._path(f".{name}.{os.getpid()}.tmp")
        tmp.write_bytes(body)
        os.replace(tmp, self._path(name))
        self._failed_at.pop(name, None)
        log.info("framework %s: cached %d B", name, len(body))
        return True

    def resolve(self) -> Framework:
        uris: list[str] = []
        sources: list[str] = []
        for name, url, marker in ASSETS:
            path = self._path(name)
            if self._fresh(path) or self._refresh(name, url, marker):
                uris.append(path.resolve().as_uri())
                sources.append("cache")
            elif path.exists():
                uris.append(path.resolve().as_uri())
                sources.append("stale-cache")
            else:
                uris.append(url)
                sources.append("remote")
        source = "cache" if all(s == "cache" for s in sources) else ("remote" if "remote" in sources else "stale-cache")
        if source == "remote":
            log.warning("framework not cached yet, rendering with remote URLs (layout may break on a slow network)")
        return Framework(uris[0], uris[1], source)
