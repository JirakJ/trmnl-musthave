"""BYOS HTTP server – protokol v1 pro fork firmware, kompatibilní se stock firmware TRMNL.

Firmware volá GET /api/setup (hlavička ID = MAC), pak periodicky GET /api/display
(ID, Access-Token, Battery-Voltage, RSSI, FW-Version, X-Frame-Id) a POST /api/log.
Odpověď /api/display: action none | partial | full, frame_id, full_url, regions_url, full_mode, sleep_mode,
refresh_rate + stock pole image_url/filename (stock firmware dělá vždy full).
"""

from __future__ import annotations

import json
import logging
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Callable
from urllib.parse import parse_qs, urlparse

from .devices import DeviceRegistry
from .diff import area, dirty_tiles, encode_regions, merge_rects
from .frames import FrameStore
from .devices import LOCK as DEVICES_LOCK
from .policy import Decision, DeviceState, PolicyConfig, decide, plausible_voltage

log = logging.getLogger(__name__)

API_KEY = "musthave-local"
FRIENDLY_ID = "MUSTHAVE"
MAX_RECTS = 4


def _regions_blob(frames: FrameStore, from_id: str, to_id: str) -> bytes | None:
    old, new = frames.get(from_id), frames.get(to_id)
    if old is None or new is None:
        return None
    rects = merge_rects(dirty_tiles(old, new), MAX_RECTS)
    if not rects:
        return None
    return encode_regions(old, new, rects)


def make_server(
    frames: FrameStore,
    devices: DeviceRegistry,
    cfg: PolicyConfig,
    port: int = 8080,
    host: str = "0.0.0.0",
    firmware_dir: Path | None = None,
    clock: Callable[[], datetime] | None = None,
) -> ThreadingHTTPServer:
    now_fn = clock or datetime.now
    fw_dir = Path(firmware_dir) if firmware_dir else None

    implausible_seen: set[str] = set()  # MAC zařízení, jejichž poslední hlášené napětí bylo nevěrohodné

    def firmware_version() -> str | None:
        if not fw_dir:
            return None
        try:
            return (fw_dir / "firmware_version.txt").read_text(encoding="utf-8").strip() or None
        except FileNotFoundError:
            return None

    class Handler(BaseHTTPRequestHandler):
        server_version = "trmnl-musthave/2.0"

        def log_message(self, fmt, *args):
            log.debug("%s " + fmt, self.client_address[0], *args)

        def _base(self) -> str:
            host_hdr = self.headers.get("Host") or f"{self.server.server_address[0]}:{self.server.server_port}"
            return f"http://{host_hdr}"

        def _send(self, code: int, body: bytes, ctype: str) -> None:
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def _json(self, code: int, data: dict) -> None:
            self._send(code, json.dumps(data).encode("utf-8"), "application/json")

        def _header_float(self, name: str) -> float | None:
            try:
                return float(self.headers.get(name, ""))
            except ValueError:
                return None

        # --- endpoints -------------------------------------------------------------------------
        def _setup(self) -> None:
            latest = frames.latest()
            image_url = f"{self._base()}/frames/{latest}.png" if latest else ""
            log.info("setup from %s (ID %s)", self.client_address[0], self.headers.get("ID"))
            self._json(200, {"status": 200, "api_key": API_KEY, "friendly_id": FRIENDLY_ID,
                             "image_url": image_url, "message": "Must-have BYOS"})

        def _display(self) -> None:
            with DEVICES_LOCK:  # čtení stavu → rozhodnutí → zápis atomicky vůči ostatním vláknům a render smyčce
                self._display_locked()

        def _display_locked(self) -> None:
            mac = self.headers.get("ID") or ""
            reported = self.headers.get("X-Frame-Id") or None
            raw_voltage = self._header_float("Battery-Voltage")
            fw = (self.headers.get("FW-Version") or "").strip()
            now = now_fn()
            state = devices.get(mac) if mac else DeviceState()
            state.frame_id = reported if reported and frames.get(reported) is not None else None
            state.last_seen_at = now.timestamp()
            if fw:
                state.fw_version = fw
            # Nevěrohodné napětí (mimo 3–5.5 V, NaN; např. poloviční čtení ADC) politika nevidí (→ baterie,
            # bezpečný režim) a neukládá se; varuje se jen při přechodu věrohodné → nevěrohodné.
            voltage = plausible_voltage(raw_voltage)
            if voltage is None and raw_voltage is not None and mac not in implausible_seen:
                log.warning("display %s: implausible Battery-Voltage %s (last plausible %s), treating as battery",
                            mac, raw_voltage, state.voltage)
            if voltage is None:
                implausible_seen.add(mac)
            else:
                implausible_seen.discard(mac)
                state.voltage = voltage
            latest = frames.latest()

            target_version = firmware_version()
            update = bool(target_version and fw and fw != target_version)
            # OTA čekací režim: soubor deploy/firmware/ota_wait (CLI `ota-mode on`). Zařízení nekreslí a ptá se
            # každých 20 s, dokud nedostane update; po ohlášení cílové verze se režim sám vypne. Rozhoduje se
            # před politikou, aby čekací dotazy nepočítaly falešné partial/full.
            ota_flag = fw_dir / "ota_wait" if fw_dir else None
            ota_wait = bool(ota_flag and ota_flag.exists())
            if ota_wait and target_version and fw == target_version:
                ota_flag.unlink(missing_ok=True)
                ota_wait = False
                log.info("ota-mode finished: %s reports %s", mac, fw)

            rects_area = None
            if latest and state.frame_id and state.frame_id != latest:
                old, new = frames.get(state.frame_id), frames.get(latest)
                if old is not None and new is not None:
                    rects = merge_rects(dirty_tiles(old, new), MAX_RECTS)
                    rects_area = area(rects) if rects else 0

            d = decide(state, state.frame_id, latest, rects_area, voltage, now, cfg)
            if ota_wait:
                d = Decision("none", d.full_mode, d.sleep_mode, 20)
            elif d.action == "partial":
                state.partials_since_full += 1
            elif d.action == "full":
                state.partials_since_full = 0
                state.last_full_at = now.timestamp()
            if d.action != "none":
                state.target_frame_id = latest  # po překreslení bude na panelu; FrameStore ho nesmí vyřadit
            if mac and self.headers.get("Access-Token") == API_KEY:  # jen zařízení spárované přes /api/setup
                devices.save(mac, state)

            base = self._base()
            resp = {
                "status": 0, "action": d.action, "frame_id": latest or "",
                "full_url": f"{base}/frames/{latest}.png" if latest else "",
                "regions_url": (f"{base}/frames/{latest}.regions?from={state.frame_id}"
                                if d.action == "partial" else ""),
                "full_mode": d.full_mode, "sleep_mode": d.sleep_mode, "refresh_rate": d.refresh_rate,
                # stock firmware: vždy full přes image_url/filename; stejný filename → nic nedělá
                "image_url": f"{base}/frames/{latest}.png" if latest else "",
                "filename": latest or "",
                "update_firmware": update, "firmware_url": f"{base}/firmware/latest.bin" if update else None,
                "reset_firmware": False, "special_function": "none",
                "ota_wait": ota_wait,
            }
            log.info("display %s frame=%s → %s (batt %s, fw %s, %s/%ss)", mac, reported, d.action, raw_voltage, fw,
                     d.sleep_mode, d.refresh_rate)
            self._json(200, resp)

        def _frame_png(self, fid: str) -> None:
            png = frames.png(fid)
            if png is None:
                self.send_error(404, "unknown frame")
                return
            self._send(200, png, "image/png")

        def _frame_regions(self, fid: str, query: dict) -> None:
            from_id = (query.get("from") or [""])[0]
            blob = _regions_blob(frames, from_id, fid) if from_id else None
            if blob is None:
                self.send_error(404, "unknown frame pair or no difference")
                return
            self._send(200, blob, "application/octet-stream")

        def _legacy_screen(self) -> None:
            latest = frames.latest()
            if latest is None:
                self.send_error(404, "no screen yet")
                return
            self._frame_png(latest)

        def _firmware(self) -> None:
            path = fw_dir / "latest.bin" if fw_dir else None
            if not path or not path.exists():
                self.send_error(404, "no firmware")
                return
            self._send(200, path.read_bytes(), "application/octet-stream")

        def _status_page(self) -> None:
            latest = frames.latest()
            body = (f"<h1>TRMNL Must-have BYOS v1</h1><p>latest: {latest or 'none'}</p>"
                    f"<p>firmware: {firmware_version() or '-'}</p>"
                    + (f'<img src="/frames/{latest}.png">' if latest else "")).encode()
            self._send(200, body, "text/html; charset=utf-8")

        def do_GET(self) -> None:
            url = urlparse(self.path)
            path, query = url.path, parse_qs(url.query)
            if path == "/api/setup":
                self._setup()
            elif path == "/api/display":
                self._display()
            elif path == "/api/log":
                self._json(200, {"status": 200})
            elif path.startswith("/frames/") and path.endswith(".png"):
                self._frame_png(path[len("/frames/"):-4])
            elif path.startswith("/frames/") and path.endswith(".regions"):
                self._frame_regions(path[len("/frames/"):-8], query)
            elif path.startswith("/screens/"):
                self._legacy_screen()
            elif path == "/firmware/latest.bin":
                self._firmware()
            elif path in ("/", "/status"):
                self._status_page()
            else:
                self.send_error(404)

        def do_POST(self) -> None:
            path = urlparse(self.path).path
            length = int(self.headers.get("Content-Length") or 0)
            raw = self.rfile.read(length) if length else b""
            if path == "/api/log":
                log.warning("device log (ID %s): %s", self.headers.get("ID"), raw[:500].decode("utf-8", "replace"))
                self._json(200, {"status": 200})
            elif path in ("/api/setup", "/api/display"):
                self.do_GET()
            else:
                self.send_error(404)

    return ThreadingHTTPServer((host, port), Handler)
