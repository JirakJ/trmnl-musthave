#!/usr/bin/env python3
"""Náhled skutečného 1-bit renderu (stejná cesta jako BYOS displej: Liquid → Chromium → 1-bit PNG).

Spuštění: .venv/bin/python preview/shot.py preview/scenarios/*.json [--layout full] [--out-dir preview/shots]
Každý JSON je payload (jako preview/sample.json); výstup <out-dir>/<jméno>-<layout>.png.
Náhled se jen uloží na disk – neposílá se do FrameStore ani zařízení.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from musthave.screen import render_screen  # noqa: E402


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("payloads", nargs="+")
    ap.add_argument("--layout", default="full")
    ap.add_argument("--out-dir", default=str(ROOT / "preview" / "shots"))
    ap.add_argument("--chrome", default=None)
    args = ap.parse_args()
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    for name in args.payloads:
        payload = json.loads(Path(name).read_text(encoding="utf-8"))
        png = render_screen(payload, args.chrome, "png", layout=args.layout)
        out = out_dir / f"{Path(name).stem}-{args.layout}.png"
        out.write_bytes(png)
        print(out)


if __name__ == "__main__":
    main()
