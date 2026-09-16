"""CLI: python3 -m musthave [run [--dry-run] | fetch | status | serve [--port N] [--no-push] [--once]]"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path

from .config import load_settings
from .http import Http
from .run import run

ROOT = Path(__file__).resolve().parent.parent


def status(root: Path) -> int:
    settings = load_settings(root)
    if not settings.user_api_key:
        print("TRMNL_USER_API_KEY chybí (.env nebo prostředí).", file=sys.stderr)
        return 2
    data = Http().get_json("https://trmnl.com/api/devices", {"Authorization": f"Bearer {settings.user_api_key}"})
    for d in data.get("data", []):
        print(
            f"{d.get('name')} ({d.get('friendly_id')}) fw {d.get('firmware_version')} "
            f"battery {d.get('percent_charged')}% wifi {d.get('wifi_strength')}% refresh {d.get('refresh_interval')}s "
            f"last ping {d.get('last_ping_at')}"
        )
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="musthave", description="TRMNL Must-have collector")
    sub = parser.add_subparsers(dest="cmd")
    run_p = sub.add_parser("run", help="stáhnout data a poslat webhook (výchozí)")
    run_p.add_argument("--dry-run", action="store_true", help="vypsat payload, neposílat, neukládat stav")
    sub.add_parser("fetch", help="jen vypsat payload jako JSON")
    sub.add_parser("status", help="vypsat zařízení z TRMNL účtu")
    serve_p = sub.add_parser("serve", help="BYOS server: lokální render + HTTP pro zařízení")
    serve_p.add_argument("--port", type=int, default=None)
    serve_p.add_argument("--no-push", action="store_true", help="neposílat data do TRMNL cloudu")
    serve_p.add_argument("--once", action="store_true", help="jeden tick a konec (test renderu)")
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        stream=sys.stderr,
    )
    cmd = args.cmd or "run"
    if cmd == "run":
        return run(ROOT, dry_run=args.dry_run)
    if cmd == "fetch":
        return run(ROOT, fetch_only=True)
    if cmd == "status":
        return status(ROOT)
    if cmd == "serve":
        from .serve import serve

        return serve(ROOT, port=args.port, push=not args.no_push, once=args.once)
    parser.print_help()
    return 2


if __name__ == "__main__":
    sys.exit(main())
