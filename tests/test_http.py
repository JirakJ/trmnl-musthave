import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from musthave.http import Http


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):  # silence
        pass

    def do_GET(self):
        if self.path == "/json":
            body = json.dumps({"ok": True, "ua": self.headers.get("User-Agent")}).encode()
            self.send_response(200)
        elif self.path == "/text":
            body = b"hello"
            self.send_response(200)
        else:
            body = b"nope"
            self.send_response(404)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):
        n = int(self.headers.get("Content-Length", 0))
        data = json.loads(self.rfile.read(n))
        body = json.dumps({"echo": data}).encode()
        self.send_response(429 if data.get("limit") else 200)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


@pytest.fixture(scope="module")
def server():
    srv = HTTPServer(("127.0.0.1", 0), Handler)
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    yield f"http://127.0.0.1:{srv.server_port}"
    srv.shutdown()


def test_get_json_sends_user_agent(server):
    data = Http().get_json(server + "/json")
    assert data["ok"] is True
    assert data["ua"].startswith("trmnl-musthave/")


def test_get_text(server):
    assert Http().get_text(server + "/text") == "hello"


def test_get_raises_on_404(server):
    with pytest.raises(Exception):
        Http().get_text(server + "/missing")


def test_post_json_returns_status_and_body_without_raising(server):
    status, text = Http().post_json(server + "/hook", {"limit": True})
    assert status == 429
    assert json.loads(text)["echo"] == {"limit": True}
