#!/usr/bin/env python3
"""Lokální render Liquid šablony do preview/out.html (vývoj bez TRMNL editoru).

Shell napodobuje TRMNL OG (classes + CSS proměnné z GET /api/models, model og_png).

Spuštění: uv run --with python-liquid preview/render.py [--layout full] [--data preview/sample.json]
Výsledek otevři v prohlížeči v okně 800x480 (nebo screenshot přes Chrome).
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from liquid import Environment

ROOT = Path(__file__).resolve().parent.parent
SIZES = {"full": (800, 480), "half_horizontal": (800, 240), "half_vertical": (400, 480), "quadrant": (400, 240)}

SHELL = """<!DOCTYPE html>
<html>
<head>
  <meta charset="utf-8">
  <link rel="stylesheet" href="https://trmnl.com/css/latest/plugins.css">
  <script src="https://trmnl.com/js/latest/plugins.js"></script>
  <link rel="preconnect" href="https://fonts.googleapis.com">
  <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
  <link href="https://fonts.googleapis.com/css2?family=Inter:wght@300;350;375;400;450;600;700&display=swap" rel="stylesheet">
  <style>body {{ margin: 0; background: #888; }} .screen {{ margin: 0; }}</style>
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


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--layout", default="full", choices=sorted(SIZES))
    ap.add_argument("--data", default=str(ROOT / "preview" / "sample.json"))
    ap.add_argument("--out", default=str(ROOT / "preview" / "out.html"))
    args = ap.parse_args()

    data = json.loads(Path(args.data).read_text(encoding="utf-8"))
    template = Environment().from_string((ROOT / "templates" / f"{args.layout}.liquid").read_text(encoding="utf-8"))
    html = SHELL.format(layout=args.layout, body=template.render(**data))
    Path(args.out).write_text(html, encoding="utf-8")
    w, h = SIZES[args.layout]
    print(f"written {args.out} ({args.layout}, {w}x{h})")


if __name__ == "__main__":
    main()
