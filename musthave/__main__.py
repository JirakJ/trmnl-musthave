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


def mark_done(root: Path, name: str, when: str | None, undo: bool = False) -> int:
    from datetime import date, datetime
    from zoneinfo import ZoneInfo

    from .reminders import DoneStore, occurrence_key

    settings = load_settings(root, secret_lookup=None)
    now = datetime.now(ZoneInfo(settings.timezone))
    known = {r.name for r in settings.reminders.reminders}
    if name not in known:
        print(f"neznámá připomínka {name!r}; známé: {', '.join(sorted(known)) or '—'}", file=sys.stderr)
        return 2
    try:
        day = date.fromisoformat(when) if when else now.date()
    except ValueError:
        print("--date musí být YYYY-MM-DD", file=sys.stderr)
        return 2
    DoneStore(settings.state_path.parent / "done.json").set(occurrence_key(name, day), not undo, now)
    print(f"{name} {day.isoformat()}: {'nesplněno' if undo else 'splněno'}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="musthave", description="TRMNL Must-have collector")
    sub = parser.add_subparsers(dest="cmd")
    run_p = sub.add_parser("run", help="stáhnout data a poslat webhook (výchozí)")
    run_p.add_argument("--dry-run", action="store_true", help="vypsat payload, neposílat, neukládat stav")
    sub.add_parser("fetch", help="jen vypsat payload jako JSON")
    sub.add_parser("status", help="vypsat zařízení z TRMNL účtu")
    ota_p = sub.add_parser("ota-mode", help="OTA čekací režim zařízení (on = polling 20 s bez kreslení, dokud nepřijde update)")
    ota_p.add_argument("state", choices=["on", "off", "status"])
    done_p = sub.add_parser("done", help="označit výskyt připomínky jako splněný (jen ten den, ne celé opakování)")
    done_p.add_argument("name", help="název připomínky z [[reminder]]")
    done_p.add_argument("--date", default=None, help="datum výskytu YYYY-MM-DD (výchozí dnes)")
    done_p.add_argument("--undo", action="store_true", help="zrušit označení")
    serve_p = sub.add_parser("serve", help="BYOS server: lokální render + HTTP pro zařízení")
    serve_p.add_argument("--port", type=int, default=None)
    serve_p.add_argument("--push", action="store_true", help="posílat data i do TRMNL cloudu (webhook)")
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
    if cmd == "ota-mode":
        flag = ROOT / "deploy" / "firmware" / "ota_wait"
        if args.state == "on":
            flag.parent.mkdir(parents=True, exist_ok=True)
            flag.touch()
        elif args.state == "off":
            flag.unlink(missing_ok=True)
        print("ota-mode:", "on" if flag.exists() else "off")
        return 0
    if cmd == "done":
        return mark_done(ROOT, args.name, args.date, args.undo)
    if cmd == "serve":
        from .serve import serve

        return serve(ROOT, port=args.port, push=args.push, once=args.once)
    parser.print_help()
    return 2


if __name__ == "__main__":
    sys.exit(main())
