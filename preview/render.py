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

import sys

from liquid import Environment

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
SIZES = {"full": (800, 480), "half_horizontal": (800, 240), "half_vertical": (400, 480), "quadrant": (400, 240)}

from musthave.screen import build_html  # stejný shell jako lokální BYOS render


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--layout", default="full", choices=sorted(SIZES))
    ap.add_argument("--data", default=str(ROOT / "preview" / "sample.json"))
    ap.add_argument("--out", default=str(ROOT / "preview" / "out.html"))
    args = ap.parse_args()

    data = json.loads(Path(args.data).read_text(encoding="utf-8"))
    template = Environment().from_string((ROOT / "templates" / f"{args.layout}.liquid").read_text(encoding="utf-8"))
    html = build_html(template.render(**data), args.layout)
    Path(args.out).write_text(html, encoding="utf-8")
    w, h = SIZES[args.layout]
    print(f"written {args.out} ({args.layout}, {w}x{h})")


if __name__ == "__main__":
    main()
