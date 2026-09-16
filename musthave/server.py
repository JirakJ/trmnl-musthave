"""BYOS HTTP server pro firmware TRMNL (stdlib http.server).

Firmware volá GET /api/setup (jednou, s hlavičkou ID = MAC), pak periodicky GET /api/display
(hlavičky ID, Access-Token, Battery-Voltage, RSSI, FW-Version, Refresh-Rate) a POST /api/log.
Zařízení překreslí displej jen když se změní "filename".
"""

from __future__ import annotations

import json
import logging
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from .screen import ScreenStore

log = logging.getLogger(__name__)

API_KEY = "musthave-local"
FRIENDLY_ID = "MUSTHAVE"


def make_server(store: ScreenStore, port: int = 8080, refresh_s: int = 300, host: str = "0.0.0.0") -> ThreadingHTTPServer:
    class Handler(BaseHTTPRequestHandler):
        server_version = "trmnl-musthave/1.0"

        def log_message(self, fmt, *args):  # do našeho loggeru místo stderr
            log.debug("%s " + fmt, self.client_address[0], *args)

        def _base(self) -> str:
            host_hdr = self.headers.get("Host") or f"{self.server.server_address[0]}:{self.server.server_port}"
            return f"http://{host_hdr}"

        def _json(self, code: int, data: dict) -> None:
            body = json.dumps(data).encode("utf-8")
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _image(self) -> None:
            cur = store.current()
            if cur is None:
                self.send_error(404, "no screen yet")
                return
            name, data = cur
            self.send_response(200)
            self.send_header("Content-Type", "image/bmp" if name.endswith(".bmp") else "image/png")
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(data)

        def do_GET(self) -> None:
            path = self.path.split("?", 1)[0]
            if path == "/api/setup":
                cur = store.current()
                image_url = f"{self._base()}/screens/{cur[0]}" if cur else ""
                log.info("setup from %s (ID %s)", self.client_address[0], self.headers.get("ID"))
                self._json(200, {"status": 200, "api_key": API_KEY, "friendly_id": FRIENDLY_ID,
                                 "image_url": image_url, "message": "Must-have BYOS"})
            elif path == "/api/display":
                cur = store.current()
                log.info("display from %s (ID %s, batt %s, rssi %s, fw %s)", self.client_address[0],
                         self.headers.get("ID"), self.headers.get("Battery-Voltage"),
                         self.headers.get("RSSI"), self.headers.get("FW-Version"))
                if cur is None:
                    self._json(200, {"status": 500, "error": "no screen rendered yet", "refresh_rate": 60,
                                     "image_url": "", "filename": "", "update_firmware": False,
                                     "reset_firmware": False, "firmware_url": None, "special_function": "none"})
                    return
                name, _ = cur
                self._json(200, {"status": 0, "image_url": f"{self._base()}/screens/{name}", "filename": name,
                                 "refresh_rate": refresh_s, "update_firmware": False, "reset_firmware": False,
                                 "firmware_url": None, "special_function": "none", "action": ""})
            elif path.startswith("/screens/"):
                self._image()
            elif path == "/api/log":
                self._json(200, {"status": 200})
            elif path in ("/", "/status"):
                cur = store.current()
                body = (f"<h1>TRMNL Must-have BYOS</h1><p>screen: {cur[0] if cur else 'none'}</p>"
                        f"<p>refresh: {refresh_s}s</p>" + (f"<img src=\"/screens/{cur[0]}\">" if cur else "")).encode()
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
            else:
                self.send_error(404)

        def do_POST(self) -> None:
            path = self.path.split("?", 1)[0]
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
