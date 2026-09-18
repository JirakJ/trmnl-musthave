"""Lokální render obrazovky pro BYOS režim: Liquid → HTML → headless Chrome → 1-bit obrázek.

Závislosti navíc oproti sběrači: python-liquid, Pillow a Chrome/Chromium (viz requirements-server.txt).
Importy třetích stran jsou líné, aby zbytek balíčku zůstal čistě stdlib.
"""

from __future__ import annotations

import io
import logging
import os
import shutil
import subprocess
import tempfile
from pathlib import Path

log = logging.getLogger(__name__)

from .framework import Framework, FrameworkCache

ROOT = Path(__file__).resolve().parent.parent
WIDTH, HEIGHT = 800, 480
ASSETS_DIR = ROOT / "musthave" / "assets"
FRAMEWORK_CACHE_DIR = ROOT / "state" / "cache"
FRAMEWORK = FrameworkCache(FRAMEWORK_CACHE_DIR)  # testy nahrazují monkeypatchem
GOOGLE_FONTS_INTER = "https://fonts.googleapis.com/css2?family=Inter:wght@300..700&display=swap"

# Struktura layoutu (flex sloupce) nezávislá na frameworku TRMNL: kdyby se plugins.css nenačetl, layout se
# nesmí rozpadnout pod sebe. Jen pro naše šablony (.mh), framework má přednost tam, kde se načte.
FALLBACK_CSS = """    .mh.layout { display: flex; flex-direction: column; width: 100%; height: var(--screen-h, 480px); box-sizing: border-box; }
    .mh.layout:not(.layout--col) { flex-direction: row; align-items: center; }
    .mh .flex { display: flex; }
    .mh .flex--col { flex-direction: column; }
    .mh .columns { display: flex; flex-direction: row; width: 100%; align-items: flex-start; }
    .mh .column { flex: 1 1 0; width: 0; min-width: 0; display: flex; flex-direction: column; overflow: hidden; }"""

# Inter (variabilní 300–700) přibalený v repu: render nesmí záviset na fonts.googleapis.com.
INTER_FACES = (
    ("inter-latin-ext.woff2", "U+0100-02BA, U+02BD-02C5, U+02C7-02CC, U+02CE-02D7, U+02DD-02FF, U+0304, U+0308, U+0329, "
                              "U+1D00-1DBF, U+1E00-1E9F, U+1EF2-1EFF, U+2020, U+20A0-20AB, U+20AD-20C0, U+2113, U+2C60-2C7F, U+A720-A7FF"),
    ("inter-latin.woff2", "U+0000-00FF, U+0131, U+0152-0153, U+02BB-02BC, U+02C6, U+02DA, U+02DC, U+0304, U+0308, U+0329, "
                          "U+2000-206F, U+20AC, U+2122, U+2191, U+2193, U+2212, U+2215, U+FEFF, U+FFFD"),
)

# Shell napodobuje TRMNL OG (třídy + CSS proměnné z GET /api/models, model og_png).
SHELL = """<!DOCTYPE html>
<html>
<head>
  <meta charset="utf-8">
  <style>
{fallback}
  </style>
  <link rel="stylesheet" href="{css}">
  <script src="{js}"></script>
{font}
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

class RenderError(RuntimeError):
    pass


def check_screenshot(img) -> None:
    """Zahodí screenshot, který Chromium nedokreslilo: špatná velikost nebo spodní pás plný ne-bílých pixelů.

    Šablona má dole vždy bílé pozadí (max. tenké linky), takže > 20 % tmavších pixelů v posledních 60 řádcích
    znamená šedou/šumovou výplň nedokresleného viewportu (viděno na Chromium 126 na Raspberry Pi)."""
    if img.size != (WIDTH, HEIGHT):
        raise RenderError(f"screenshot size {img.size}, expected {(WIDTH, HEIGHT)}")
    band = img.crop((0, HEIGHT - 60, WIDTH, HEIGHT)).convert("L")
    hist = band.histogram()
    dark = sum(hist[:200])
    ratio = dark / (band.size[0] * band.size[1])
    if ratio > 0.2:
        raise RenderError(f"bottom band {ratio:.0%} non-white – unfinished render")


_preferred_headless = "new"  # po prvním úspěšném fallbacku si zapamatuje "old"

CHROME_CANDIDATES = [
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
    "/Applications/Chromium.app/Contents/MacOS/Chromium",
    "chromium",
    "chromium-browser",
    "google-chrome",
    "google-chrome-stable",
]


def font_css(assets_dir: Path = ASSETS_DIR) -> str:
    """@font-face pro přibalený Inter (file://); když soubory chybí, <link> na Google Fonts jako dřív."""
    faces = []
    for name, ranges in INTER_FACES:
        path = Path(assets_dir) / name
        if not path.exists():
            return f'  <link href="{GOOGLE_FONTS_INTER}" rel="stylesheet">'
        faces.append("    @font-face { font-family: 'Inter'; font-style: normal; font-weight: 300 700; font-display: block; "
                     f"src: url({path.resolve().as_uri()}) format('woff2'); unicode-range: {ranges}; }}")
    return "  <style>\n" + "\n".join(faces) + "\n  </style>"


_FONT_CSS = font_css()  # cesty se za běhu nemění


def build_html(markup: str, layout: str = "full", framework: Framework | None = None) -> str:
    """HTML pro Chromium. Bez `framework` se použije lokální cache (state/cache, bez sítě), viz framework.py."""
    fw = framework or FRAMEWORK.resolve()
    return SHELL.format(css=fw.css, js=fw.js, font=_FONT_CSS, fallback=FALLBACK_CSS, layout=layout, body=markup)


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


def screenshot(html: str, chrome: str, timeout_s: int = 60, headless: str = "new") -> bytes:
    """Vyrenderuje HTML v headless Chrome na 800×480 PNG. headless: "new" | "old" (Chromium ≤ 126 na RPi)."""
    if headless not in ("new", "old"):
        raise ValueError(f"headless must be new|old, got {headless!r}")
    with tempfile.TemporaryDirectory(prefix="musthave-") as tmp:
        page = Path(tmp) / "page.html"
        out = Path(tmp) / "shot.png"
        page.write_text(html, encoding="utf-8")
        # bez --user-data-dir: čerstvý profil v headless režimu na macOS visí
        cmd = [
            chrome, "--headless=new" if headless == "new" else "--headless", "--disable-gpu", "--hide-scrollbars", "--no-sandbox",
            f"--window-size={WIDTH},{HEIGHT}", "--virtual-time-budget=8000",
            f"--screenshot={out}", page.as_uri(),
        ]
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout_s, check=False)
        if not out.exists():
            raise RuntimeError(f"chrome screenshot failed ({proc.returncode}): {proc.stderr[-300:]}")
        return out.read_bytes()


def to_device_image(png, fmt: str = "png") -> bytes:
    """Převede screenshot (bytes PNG nebo otevřený PIL obrázek) na 1-bit 800×480 PNG nebo BMP pro firmware TRMNL.

    Přeškálování jiné velikosti tu zůstává pro přímé použití (testy, jiné zdroje); render_screen() takový
    screenshot zamítne dřív (check_screenshot), protože oříznutý viewport by na e-inku vypadal rozbitě."""
    from PIL import Image  # Pillow

    img = (png if isinstance(png, Image.Image) else Image.open(io.BytesIO(png))).convert("L")
    if img.size != (WIDTH, HEIGHT):
        img = img.resize((WIDTH, HEIGHT), Image.Resampling.LANCZOS)
    mono = img.convert("1")  # Floyd–Steinberg dithering, jako TRMNL cloud pro 1-bit
    buf = io.BytesIO()
    if fmt == "bmp":
        mono.save(buf, format="BMP")
    else:
        mono.save(buf, format="PNG", optimize=True)
    return buf.getvalue()


def render_screen(payload: dict, chrome: str | None = None, fmt: str = "png", layout: str = "full", headless: str = "auto") -> bytes:
    """Celý řetězec payload → bytes obrázku pro zařízení."""
    html = build_html(render_markup(payload, layout), layout)
    exe = find_chrome(chrome)
    from PIL import Image

    global _preferred_headless
    if headless in ("new", "old"):
        order: tuple[str, ...] = (headless,)  # nastaveno v config.toml – žádné zkoušení
    else:
        order = (_preferred_headless, "old" if _preferred_headless == "new" else "new")
    last_err: Exception = RenderError("render failed")
    for mode in order:
        try:
            png = screenshot(html, exe, headless=mode)
            img = Image.open(io.BytesIO(png))
            check_screenshot(img)
            if mode != _preferred_headless:
                log.info("headless mode %s works here, using it from now on", mode)
                _preferred_headless = mode
            return to_device_image(img, fmt)
        except (RenderError, RuntimeError, OSError, subprocess.SubprocessError) as err:
            # Chromium 126 (RPi) v novém headless režimu ořízne viewport na 390 px; pád/timeout Chromia zkusíme v druhém režimu
            log.warning("screenshot rejected (%s mode): %s", mode, err)
            last_err = err
    raise last_err
