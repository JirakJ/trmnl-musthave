"""TRMNL framework CSS/JS pro lokální render: cache na disku, aby render nikdy nečekal na síť.

Render v headless Chromiu má rozpočet několik sekund; když se plugins.css z trmnl.com nestihne stáhnout
(pomalá Wi-Fi na Raspberry Pi), sloupce se rozpadnou pod sebe. Proto se framework stahuje MIMO render
(`refresh()` volá server při startu a pak v pozadí jednou denně; podmíněný GET s ETag/Last-Modified, gzip,
dlouhý timeout), ukládá do `state/cache/` a Chromium dostává `file://` cestu (`resolve()` je čistě lokální
a nikdy nesahá na síť). Stará kopie se používá dál, když obnova selže; bez jakékoli kopie se (s varováním)
použije vzdálená URL jako dřív.
"""

from __future__ import annotations

import gzip
import json
import logging
import os
import threading
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

log = logging.getLogger(__name__)

FRAMEWORK_CSS_URL = "https://trmnl.com/css/latest/plugins.css"
FRAMEWORK_JS_URL = "https://trmnl.com/js/latest/plugins.js"
REFRESH_EVERY_S = 24 * 3600   # trmnl.com posílá max-age 4 h; denní podmíněný GET stačí (304 = pár bajtů)
FETCH_TIMEOUT_S = 60          # stahování běží mimo render, může trvat

ASSETS = (
    ("css", "plugins.css", FRAMEWORK_CSS_URL, b".trmnl .columns"),  # sanity: bez sloupců to není framework
    ("js", "plugins.js", FRAMEWORK_JS_URL, b"function"),
)
SOURCE_ORDER = ("cache", "stale-cache", "remote")  # od nejlepšího k nejhoršímu


@dataclass(frozen=True)
class Framework:
    css: str        # URL nebo file:// URI pro <link>
    js: str
    source: str     # "cache" (obnoveno v limitu) | "stale-cache" (starší kopie) | "remote" (nic v cache)


class NotModified(Exception):
    pass


def fetch(url: str, etag: str | None = None, last_modified: str | None = None, timeout_s: float = FETCH_TIMEOUT_S) -> tuple[bytes, dict]:
    """GET s podmíněnými hlavičkami; vrací (tělo, {"etag", "last_modified"}). 304 → NotModified."""
    headers = {"Accept-Encoding": "gzip", "User-Agent": "trmnl-musthave"}
    if etag:
        headers["If-None-Match"] = etag
    if last_modified:
        headers["If-Modified-Since"] = last_modified
    req = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=timeout_s) as r:
            body = r.read()
            if (r.headers.get("Content-Encoding") or "").lower() == "gzip":
                body = gzip.decompress(body)
            return body, {"etag": r.headers.get("ETag"), "last_modified": r.headers.get("Last-Modified")}
    except urllib.error.HTTPError as err:
        if err.code == 304:
            raise NotModified from err
        raise


class FrameworkCache:
    def __init__(self, directory: Path, fetch_fn: Callable[..., tuple[bytes, dict]] = fetch,
                 now_fn: Callable[[], float] = time.time, refresh_every_s: float = REFRESH_EVERY_S) -> None:
        self.dir = Path(directory)
        self.fetch_fn = fetch_fn
        self.now_fn = now_fn
        self.refresh_every_s = refresh_every_s
        self._lock = threading.Lock()

    # --- soubory ---------------------------------------------------------------------------------
    def _path(self, name: str) -> Path:
        return self.dir / name

    def _meta_path(self, name: str) -> Path:
        return self.dir / f"{name}.meta.json"

    def _meta(self, name: str) -> dict:
        try:
            return json.loads(self._meta_path(name).read_text(encoding="utf-8"))
        except (FileNotFoundError, ValueError):
            return {}

    def _write(self, path: Path, body: bytes) -> None:
        self.dir.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
        tmp.write_bytes(body)
        os.replace(tmp, path)

    def _checked_at(self, name: str) -> float:
        return float(self._meta(name).get("checked_at") or 0)

    # --- síť (jen refresh) -----------------------------------------------------------------------
    def refresh(self, force: bool = False) -> None:
        """Obnoví cache, když je starší než `refresh_every_s` (podmíněný GET). Nikdy nevyhazuje – při chybě
        zůstane stará kopie a zkusí se to příště. Volat mimo render (start serveru, pozadí)."""
        with self._lock:
            now = self.now_fn()
            for _, name, url, marker in ASSETS:
                path = self._path(name)
                if not force and path.exists() and now - self._checked_at(name) < self.refresh_every_s:
                    continue
                meta = self._meta(name) if path.exists() else {}
                try:
                    body, validators = self.fetch_fn(url, etag=meta.get("etag"), last_modified=meta.get("last_modified"))
                    if marker not in body:
                        raise ValueError(f"unexpected content ({len(body)} B)")
                except NotModified:
                    log.info("framework %s: unchanged (304)", name)
                    self._write(self._meta_path(name), json.dumps({**meta, "checked_at": now}).encode())
                    continue
                except Exception as err:  # noqa: BLE001
                    log.warning("framework %s: refresh failed (%s), keeping %s", name, err, "the cached copy" if path.exists() else "nothing")
                    continue
                self._write(path, body)
                self._write(self._meta_path(name), json.dumps({**validators, "checked_at": now}).encode())
                log.info("framework %s: cached %d B", name, len(body))

    def refresh_in_background(self) -> threading.Thread:
        t = threading.Thread(target=self.refresh, daemon=True, name="framework-refresh")
        t.start()
        return t

    # --- render (bez sítě) -----------------------------------------------------------------------
    def _locate(self, name: str, url: str) -> tuple[str, str]:
        path = self._path(name)
        if not path.exists():
            return url, "remote"
        fresh = self.now_fn() - self._checked_at(name) < self.refresh_every_s
        return path.resolve().as_uri(), "cache" if fresh else "stale-cache"

    def resolve(self) -> Framework:
        """Co dát do <head>. Čistě lokální: nikdy nečeká na síť."""
        located = {key: self._locate(name, url) for key, name, url, _ in ASSETS}
        source = max((src for _, src in located.values()), key=SOURCE_ORDER.index)
        if source == "remote":
            log.warning("framework not cached yet, rendering with remote URLs (layout may break on a slow network)")
        return Framework(located["css"][0], located["js"][0], source)
