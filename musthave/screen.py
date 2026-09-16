"""Lokální render obrazovky pro BYOS režim: Liquid → HTML → headless Chrome → 1-bit obrázek.

Závislosti navíc oproti sběrači: python-liquid, Pillow a Chrome/Chromium (viz requirements-server.txt).
Importy třetích stran jsou líné, aby zbytek balíčku zůstal čistě stdlib.
"""

from __future__ import annotations

import hashlib
import io
import json
import logging
import os
import shutil
import subprocess
import tempfile
from pathlib import Path

log = logging.getLogger(__name__)

ROOT = Path(__file__).resolve().parent.parent
WIDTH, HEIGHT = 800, 480
FRAMEWORK_CSS = "https://trmnl.com/css/latest/plugins.css"
FRAMEWORK_JS = "https://trmnl.com/js/latest/plugins.js"

# Shell napodobuje TRMNL OG (třídy + CSS proměnné z GET /api/models, model og_png).
SHELL = """<!DOCTYPE html>
<html>
<head>
  <meta charset="utf-8">
  <link rel="stylesheet" href="{css}">
  <script src="{js}"></script>
  <link rel="preconnect" href="https://fonts.googleapis.com">
  <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
  <link href="https://fonts.googleapis.com/css2?family=Inter:wght@300;350;375;400;450;600;700&display=swap" rel="stylesheet">
  <style>body {{ margin: 0; background: #fff; }} .screen {{ margin: 0; }}</style>
</head>
<body class="environment trmnl">
  <div class="screen screen--og_png screen--md screen--density-1x" style="--screen-w:800px;--screen-h:480px;--pixel-ratio:1.0;--dither-pixel-ratio:1.0;--device-ui-scale:1.0;--gap-scale:1.0">
    <div class="view view--{layout}">
{body}
    </div>
  </div>
</body>
</html>
"""

CHROME_CANDIDATES = [
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
    "/Applications/Chromium.app/Contents/MacOS/Chromium",
    "chromium",
    "chromium-browser",
    "google-chrome",
    "google-chrome-stable",
]


def build_html(markup: str, layout: str = "full") -> str:
    return SHELL.format(css=FRAMEWORK_CSS, js=FRAMEWORK_JS, layout=layout, body=markup)


def render_markup(payload: dict, layout: str = "full") -> str:
    from liquid import Environment  # python-liquid

    template = Environment().from_string((ROOT / "templates" / f"{layout}.liquid").read_text(encoding="utf-8"))
    return template.render(**payload)


def find_chrome(explicit: str | None = None) -> str:
    for cand in ([explicit] if explicit else []) + CHROME_CANDIDATES:
        if not cand:
            continue
        if os.path.isabs(cand) and os.path.exists(cand):
            return cand
        found = shutil.which(cand)
        if found:
            return found
    raise RuntimeError("Chrome/Chromium nenalezen – nastav [server] chrome v config.toml")


def screenshot(html: str, chrome: str, timeout_s: int = 60) -> bytes:
    """Vyrenderuje HTML v headless Chrome na 800×480 PNG."""
    with tempfile.TemporaryDirectory(prefix="musthave-") as tmp:
        page = Path(tmp) / "page.html"
        out = Path(tmp) / "shot.png"
        page.write_text(html, encoding="utf-8")
        # bez --user-data-dir: čerstvý profil v headless režimu na macOS visí
        cmd = [
            chrome, "--headless=new", "--disable-gpu", "--hide-scrollbars", "--no-sandbox",
            f"--window-size={WIDTH},{HEIGHT}", "--virtual-time-budget=8000",
            f"--screenshot={out}", page.as_uri(),
        ]
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout_s, check=False)
        if not out.exists():
            raise RuntimeError(f"chrome screenshot failed ({proc.returncode}): {proc.stderr[-300:]}")
        return out.read_bytes()


def to_device_image(png: bytes, fmt: str = "png") -> bytes:
    """Převede screenshot na 1-bit 800×480 PNG (výchozí) nebo BMP, jak čeká firmware TRMNL."""
    from PIL import Image  # Pillow

    img = Image.open(io.BytesIO(png)).convert("L")
    if img.size != (WIDTH, HEIGHT):
        img = img.resize((WIDTH, HEIGHT), Image.Resampling.LANCZOS)
    mono = img.convert("1")  # Floyd–Steinberg dithering, jako TRMNL cloud pro 1-bit
    buf = io.BytesIO()
    if fmt == "bmp":
        mono.save(buf, format="BMP")
    else:
        mono.save(buf, format="PNG", optimize=True)
    return buf.getvalue()


def render_screen(payload: dict, chrome: str | None = None, fmt: str = "png", layout: str = "full") -> bytes:
    """Celý řetězec payload → bytes obrázku pro zařízení."""
    html = build_html(render_markup(payload, layout), layout)
    return to_device_image(screenshot(html, find_chrome(chrome)), fmt)


class ScreenStore:
    """Aktuální obrázek na disku + meta.json s názvem (hash obsahu = cache-breaker pro firmware)."""

    def __init__(self, directory: Path) -> None:
        self.dir = Path(directory)
        self.dir.mkdir(parents=True, exist_ok=True)
        self.meta = self.dir / "meta.json"

    def _read_meta(self) -> dict | None:
        try:
            return json.loads(self.meta.read_text(encoding="utf-8"))
        except (FileNotFoundError, ValueError):
            return None

    def current(self) -> tuple[str, bytes] | None:
        meta = self._read_meta()
        if not meta:
            return None
        path = self.dir / meta["file"]
        if not path.exists():
            return None
        return meta["filename"], path.read_bytes()

    def update(self, data: bytes, ext: str = "png") -> str:
        digest = hashlib.sha256(data).hexdigest()[:10]
        filename = f"musthave-{digest}.{ext}"
        file = f"current.{ext}"
        tmp = self.dir / f".{file}.tmp"
        tmp.write_bytes(data)
        os.replace(tmp, self.dir / file)
        meta_tmp = self.dir / ".meta.json.tmp"
        meta_tmp.write_text(json.dumps({"filename": filename, "file": file, "bytes": len(data)}), encoding="utf-8")
        os.replace(meta_tmp, self.meta)
        return filename
