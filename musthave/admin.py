"""Lokální webová správa (/admin): úprava config.local.toml, náhled 800×480 a označení splněných připomínek.

Bezpečnost (server je určen pro domácí LAN, běží po HTTP):
- přístup jen s lokálně spravovaným tokenem (`state/admin_token`, 0600; vypíše ho `python3 -m musthave admin-token`,
  token se nikdy neloguje), po přihlášení relace v cookie HttpOnly + SameSite=Strict (jen v paměti procesu),
- každý zápis (POST) vyžaduje CSRF token relace a hlavičku Origin/Referer ze stejného hostitele,
- tajemství (.env, API klíče) se v administraci vůbec neobjevují; adresy kalendářů (`[agenda] sources`) se
  nezobrazují ani neupravují (jen jejich počet),
- zápis je atomický (tmp + rename), předchozí verze zůstává v `config.local.toml.bak` a lze ji obnovit; neplatný
  formulář nic nezapíše a běžící konfiguraci nezmění (validace stejným `load_settings` jako server).
Náhled používá stejnou renderovací cestu jako displej (collect → build_payload → Chromium → 1-bit PNG), ale
snímek se neukládá do FrameStore, takže ho zařízení nedostane. Aktivace = samostatné „Uložit“; změna se projeví
při dalším renderu a stav ukazuje, zda je uložená, vyrenderovaná a potvrzená firmwarem.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import html
import json
import logging
import os
import re
import secrets
import shutil
import tempfile
import threading
import time
import tomllib
from datetime import date, datetime
from pathlib import Path
from typing import Callable
from urllib.parse import parse_qs, urlsplit
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from .config import ConfigError, load_settings
from .layout import PANEL_TITLES, WEATHER_VARIANTS
from .streams import MODES
from .tomlw import dumps

log = logging.getLogger(__name__)

SESSION_TTL_S = 12 * 3600
MAX_BODY = 64 * 1024
COOKIE = "mh_admin"
SLUG_RE = re.compile(r"^[a-z0-9_]{2,40}$")
LOCAL_HEADER = ("Spravuje webová správa (/admin) – lze upravovat i ručně.\n"
                "Předchozí verze je v config.local.toml.bak. Tajemství sem nepatří (.env).")


def config_hash(root: Path) -> str:
    h = hashlib.sha256()
    for name in ("config.toml", "config.local.toml"):
        try:
            h.update((root / name).read_bytes())
        except FileNotFoundError:
            h.update(b"-")
        h.update(b"\0")
    return h.hexdigest()[:12]


def ensure_token(root: Path) -> str:
    path = root / "state" / "admin_token"
    try:
        token = path.read_text(encoding="utf-8").strip()
        if len(token) >= 20:
            return token
    except FileNotFoundError:
        pass
    path.parent.mkdir(parents=True, exist_ok=True)
    token = secrets.token_urlsafe(24)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        fh.write(token + "\n")
    return token


def _read_toml(path: Path) -> dict:
    try:
        return tomllib.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}


def _lines(text: str) -> list[str]:
    return [line.strip() for line in (text or "").replace(",", "\n").splitlines() if line.strip()]


def effective(root: Path) -> tuple[dict, dict, dict]:
    """(base config.toml, local config.local.toml, sloučený pohled) – sloučení stejně jako load_settings."""
    base = _read_toml(root / "config.toml")
    local = _read_toml(root / "config.local.toml")
    merged = json.loads(json.dumps(base, default=str))
    for table, values in local.items():
        if isinstance(values, dict) and isinstance(merged.get(table), dict):
            merged[table] = {**merged[table], **json.loads(json.dumps(values, default=str))}
        elif isinstance(values, list) and isinstance(merged.get(table), list):
            merged[table] = merged[table] + json.loads(json.dumps(values, default=str))
        else:
            merged[table] = json.loads(json.dumps(values, default=str))
    return base, local, merged


def form_values(root: Path) -> dict[str, str]:
    base, local, merged = effective(root)
    loc, streams, layout = merged.get("location", {}), merged.get("streams", {}), merged.get("layout", {})
    countdowns = []
    for c in local.get("countdown", []) or []:
        countdowns.append(f"{c.get('date')} | {c.get('name', '')}" + (" | yearly" if c.get("yearly") else ""))
    return {
        "location_name": str(loc.get("name", "")),
        "latitude": str(loc.get("latitude", "")),
        "longitude": str(loc.get("longitude", "")),
        "timezone": str(loc.get("timezone", "Europe/Prague")),
        "kick": "\n".join(streams.get("kick", [])),
        "twitch": "\n".join(streams.get("twitch", [])),
        "streams_mode": str(streams.get("mode", "all")),
        "favorites": "\n".join(streams.get("favorites", [])),
        "max_items": str(streams.get("max_items", 0)),
        "combined": "on" if streams.get("combined") else "",
        "countdowns": "\n".join(countdowns),
        "panels": ", ".join(layout.get("panels", [])) if "panels" in layout else "",
        "weather": str(layout.get("weather", "full")),
        "hide_empty": ", ".join(layout.get("hide_empty", [])),
    }


def _set(table: dict, base_table: dict, key: str, value, default=None) -> None:
    """Hodnotu shodnou se základním config.toml do lokálního souboru nepsat (base změny v Gitu pak nejsou maskované)."""
    if base_table.get(key, default) == value:
        table.pop(key, None)
    else:
        table[key] = value


def apply_form(base: dict, local: dict, form: dict[str, str]) -> tuple[dict, list[str]]:
    """Nový obsah config.local.toml z formuláře + seznam srozumitelných chyb (prázdný = OK)."""
    errors: list[str] = []
    out = json.loads(json.dumps(local, default=str))  # hluboká kopie; data převedena na text, vrátí se níž
    # data v [[countdown]]/[[reminder]] jinde v lokálním souboru vrátit jako date
    for key in ("countdown", "reminder", "priority"):
        for item in out.get(key, []) or []:
            if isinstance(item.get("date"), str):
                try:
                    item["date"] = date.fromisoformat(item["date"])
                except ValueError:
                    pass

    loc = out.setdefault("location", {})
    base_loc = base.get("location", {})
    name = form.get("location_name", "").strip()
    if not name or len(name) > 40:
        errors.append("Lokalita: název musí mít 1–40 znaků.")
    try:
        lat = float(form.get("latitude", "").replace(",", "."))
        lon = float(form.get("longitude", "").replace(",", "."))
        if not (-90 <= lat <= 90 and -180 <= lon <= 180):
            raise ValueError
    except ValueError:
        errors.append("Lokalita: zeměpisná šířka musí být −90…90 a délka −180…180 (desetinné číslo).")
        lat = lon = None
    tz = form.get("timezone", "").strip()
    try:
        ZoneInfo(tz)
    except (ZoneInfoNotFoundError, ValueError):
        errors.append(f"Lokalita: neznámá časová zóna {tz!r} (např. Europe/Prague).")
    if not errors:
        _set(loc, base_loc, "name", name)
        _set(loc, base_loc, "latitude", lat)
        _set(loc, base_loc, "longitude", lon)
        _set(loc, base_loc, "timezone", tz)

    st = out.setdefault("streams", {})
    base_st = base.get("streams", {})
    for platform in ("kick", "twitch"):
        slugs = [s.lower() for s in _lines(form.get(platform, ""))]
        bad = [s for s in slugs if not SLUG_RE.match(s)]
        if bad:
            errors.append(f"{platform.capitalize()}: neplatné jméno kanálu {', '.join(bad)} (a–z, 0–9, _).")
        elif len(slugs) != len(set(slugs)):
            errors.append(f"{platform.capitalize()}: kanál je v seznamu dvakrát.")
        else:
            _set(st, base_st, platform, slugs)
    mode = form.get("streams_mode", "all")
    if mode not in MODES:
        errors.append("Streamy: neplatný režim zobrazení.")
    else:
        _set(st, base_st, "mode", mode, default="all")
    favorites = [f.lower() for f in _lines(form.get("favorites", ""))]
    for fav in favorites:
        platform, _, ident = fav.partition(":")
        if platform not in ("kick", "twitch") or not SLUG_RE.match(ident):
            errors.append(f"Oblíbení: {fav!r} musí být kick:<kanál> nebo twitch:<kanál>.")
    _set(st, base_st, "favorites", favorites) if favorites or "favorites" in base_st else st.pop("favorites", None)
    try:
        max_items = int(form.get("max_items", "0") or 0)
        if not 0 <= max_items <= 20:
            raise ValueError
        _set(st, base_st, "max_items", max_items) if max_items or "max_items" in base_st else st.pop("max_items", None)
    except ValueError:
        errors.append("Streamy: limit položek musí být celé číslo 0–20.")
    combined = form.get("combined") == "on"
    _set(st, base_st, "combined", combined) if combined or "combined" in base_st else st.pop("combined", None)

    countdowns = []
    for line in (form.get("countdowns") or "").splitlines():
        if not line.strip():
            continue
        parts = [p.strip() for p in line.split("|")]
        try:
            when = date.fromisoformat(parts[0])
        except ValueError:
            errors.append(f"Odpočet: neplatné datum {parts[0]!r} (YYYY-MM-DD).")
            continue
        title = parts[1] if len(parts) > 1 else ""
        if not title or len(title) > 40:
            errors.append(f"Odpočet {parts[0]}: název musí mít 1–40 znaků.")
            continue
        entry = {"name": title, "date": when}
        if len(parts) > 2 and parts[2].lower() in ("yearly", "ročně", "rocne"):
            entry["yearly"] = True
        if entry not in countdowns:   # opakované uložení netvoří duplicity
            countdowns.append(entry)
    base_names = {(c.get("name"), str(c.get("date"))) for c in base.get("countdown", []) or []}
    countdowns = [c for c in countdowns if (c["name"], str(c["date"])) not in base_names]  # základ se nezdvojuje
    if countdowns:
        out["countdown"] = countdowns
    else:
        out.pop("countdown", None)

    lay = out.setdefault("layout", {})
    panels = [p.lower() for p in _lines(form.get("panels", ""))]
    if panels:
        unknown = [p for p in panels if p not in PANEL_TITLES]
        if unknown:
            errors.append(f"Rozložení: neznámý panel {', '.join(unknown)} (povolené: {', '.join(PANEL_TITLES)}).")
        lay["panels"] = panels
    else:
        lay.pop("panels", None)
    weather = form.get("weather", "full")
    if weather not in WEATHER_VARIANTS:
        errors.append("Rozložení: neplatná varianta počasí.")
    elif weather != "full" or "weather" in lay:
        lay["weather"] = weather
    hide = [p.lower() for p in _lines(form.get("hide_empty", ""))]
    if hide:
        lay["hide_empty"] = hide
    else:
        lay.pop("hide_empty", None)
    for table in ("location", "streams", "layout"):
        if not out.get(table):
            out.pop(table, None)
    return out, errors


def validate(root: Path, local_text: str) -> list[str]:
    """Plná validace stejným načítáním jako server (v dočasné kopii, běžící konfigurace se nemění)."""
    with tempfile.TemporaryDirectory(prefix="musthave-admin-") as tmp:
        shutil.copy(root / "config.toml", Path(tmp) / "config.toml")
        (Path(tmp) / "config.local.toml").write_text(local_text, encoding="utf-8")
        try:
            load_settings(Path(tmp), env={}, secret_lookup=None)
        except (ConfigError, ValueError, KeyError, TypeError, tomllib.TOMLDecodeError) as err:
            return [f"Konfigurace: {err}"]
    return []


def write_local(root: Path, text: str, now: datetime) -> str:
    """Atomický zápis; předchozí verze → config.local.toml.bak. Vrací hash nové konfigurace."""
    path = root / "config.local.toml"
    if path.exists():
        shutil.copy2(path, root / "config.local.toml.bak")
    tmp = path.with_name(f".config.local.toml.{os.getpid()}.tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)
    digest = config_hash(root)
    meta = root / "state" / "admin.json"
    meta.parent.mkdir(parents=True, exist_ok=True)
    meta.write_text(json.dumps({"saved_at": now.timestamp(), "hash": digest}), encoding="utf-8")
    return digest


def restore_backup(root: Path, now: datetime) -> bool:
    bak = root / "config.local.toml.bak"
    if not bak.exists():
        return False
    text = bak.read_text(encoding="utf-8")
    if validate(root, text):
        return False
    current = (root / "config.local.toml")
    previous = current.read_text(encoding="utf-8") if current.exists() else None
    write_local(root, text, now)
    if previous is not None:  # obnovou se z aktuální verze stane záloha (lze vrátit zpět)
        bak.write_text(previous, encoding="utf-8")
    return True


def change_state(root: Path, devices) -> dict:
    """Uloženo → vyrenderováno → potvrzeno firmwarem (podle hashe konfigurace v render.json a X-Frame-Id)."""
    meta = _json(root / "state" / "admin.json")
    render = _json(root / "state" / "frames" / "render.json")
    current = config_hash(root)
    out = {"hash": current, "saved_at": meta.get("saved_at") if meta.get("hash") == current else None,
           "rendered": bool(render.get("config_hash") == current), "frame": render.get("frame_id"), "confirmed": False}
    if out["rendered"] and render.get("frame_id"):
        out["confirmed"] = any(st.acked_frame_id == render["frame_id"] for _, st in devices.items())
    return out


def _json(path: Path) -> dict:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (FileNotFoundError, ValueError, OSError):
        return {}


class AdminApp:
    """HTTP logika správy; server jí předá požadavek a pošle odpověď (status, hlavičky, tělo)."""

    def __init__(self, root: Path, devices, preview: Callable[[Path], bytes] | None = None,
                 clock: Callable[[], datetime] | None = None) -> None:
        self.root = Path(root)
        self.devices = devices
        self.preview_fn = preview
        self.clock = clock or datetime.now
        self.sessions: dict[str, dict] = {}
        self.lock = threading.Lock()
        self.preview_lock = threading.Lock()
        self.failed_logins: list[float] = []

    # --- relace a ochrana ---------------------------------------------------------------------------
    def _session(self, headers) -> dict | None:
        raw = headers.get("Cookie") or ""
        sid = next((c.split("=", 1)[1] for c in (p.strip() for p in raw.split(";")) if c.startswith(COOKIE + "=")), None)
        with self.lock:
            sess = self.sessions.get(sid or "")
            if sess and sess["expires"] > time.time():
                return {**sess, "sid": sid}
            if sid in self.sessions:
                self.sessions.pop(sid, None)
        return None

    @staticmethod
    def _same_origin(headers) -> bool:
        host = headers.get("Host") or ""
        origin = headers.get("Origin") or headers.get("Referer") or ""
        return bool(origin) and urlsplit(origin).netloc == host

    def _login(self, form: dict[str, str]) -> tuple[int, dict, bytes]:
        now = time.time()
        self.failed_logins = [t for t in self.failed_logins if now - t < 60]
        if len(self.failed_logins) >= 10:
            return self._page(429, "<p>Příliš mnoho pokusů, zkus to za minutu.</p>")
        if not hmac.compare_digest(form.get("token", "").strip().encode(), ensure_token(self.root).encode()):
            self.failed_logins.append(now)
            log.warning("admin: failed login")
            return self._page(403, self._login_form("Neplatný přístupový token."))
        sid = secrets.token_urlsafe(24)
        with self.lock:
            self.sessions[sid] = {"csrf": secrets.token_urlsafe(24), "expires": now + SESSION_TTL_S}
        cookie = f"{COOKIE}={sid}; Path=/admin; HttpOnly; SameSite=Strict; Max-Age={SESSION_TTL_S}"
        return 303, {"Location": "/admin", "Set-Cookie": cookie}, b""

    # --- vstupní bod ---------------------------------------------------------------------------------
    def handle(self, method: str, path: str, headers, body: bytes) -> tuple[int, dict, bytes]:
        if len(body) > MAX_BODY:
            return self._page(413, "<p>Požadavek je příliš velký.</p>")
        form = {k: v[-1] for k, v in parse_qs(body.decode("utf-8", "replace"), keep_blank_values=True).items()}
        if method == "POST" and path == "/admin/login":
            if not self._same_origin(headers):
                return self._page(403, "<p>Požadavek z cizího původu.</p>")
            return self._login(form)
        sess = self._session(headers)
        if sess is None:
            return self._page(200 if method == "GET" else 403, self._login_form())
        if method == "GET" and path in ("/admin", "/admin/"):
            return self._page(200, self._main(sess, form_values(self.root), notice=None))
        if method != "POST":
            return self._page(404, "<p>Neznámá stránka.</p>")
        if not self._same_origin(headers) or not hmac.compare_digest(form.get("csrf", "").encode(), sess["csrf"].encode()):
            return self._page(403, "<p>Neplatný CSRF token nebo původ požadavku – obnov stránku.</p>")
        if path == "/admin/logout":
            with self.lock:
                self.sessions.pop(sess["sid"], None)
            return 303, {"Location": "/admin", "Set-Cookie": f"{COOKIE}=; Path=/admin; Max-Age=0"}, b""
        if path in ("/admin/preview", "/admin/save"):
            return self._edit(sess, form, save=path.endswith("save"))
        if path == "/admin/restore":
            ok = restore_backup(self.root, self.clock())
            return self._page(200, self._main(sess, form_values(self.root),
                                              notice="Předchozí nastavení obnoveno." if ok else "Záloha není k dispozici nebo je neplatná."))
        if path == "/admin/done":
            return self._done(sess, form)
        return self._page(404, "<p>Neznámá akce.</p>")

    def _edit(self, sess: dict, form: dict[str, str], save: bool) -> tuple[int, dict, bytes]:
        base, local, _ = effective(self.root)
        new_local, errors = apply_form(base, local, form)
        text = ""
        if not errors:
            try:
                text = dumps(new_local, LOCAL_HEADER)
            except ValueError as err:
                errors.append(f"Zápis TOML: {err}")
        if not errors:
            errors = validate(self.root, text)
        if errors:
            return self._page(400, self._main(sess, form, errors=errors))
        if save:
            digest = write_local(self.root, text, self.clock())
            log.info("admin: config.local.toml saved (%s)", digest)
            return self._page(200, self._main(sess, form_values(self.root), notice="Uloženo. Projeví se při dalším renderu."))
        image = None
        if self.preview_fn is not None:
            with self.preview_lock, tempfile.TemporaryDirectory(prefix="musthave-preview-") as tmp:
                shutil.copy(self.root / "config.toml", Path(tmp) / "config.toml")
                (Path(tmp) / "config.local.toml").write_text(text, encoding="utf-8")
                (Path(tmp) / "state").symlink_to(self.root / "state")  # cache a stav jen ke čtení (preview neukládá)
                try:
                    image = self.preview_fn(Path(tmp))
                except Exception as err:  # noqa: BLE001
                    log.warning("admin preview failed: %s", type(err).__name__)
                    return self._page(500, self._main(sess, form, errors=[f"Náhled se nepodařil: {type(err).__name__}"]))
        return self._page(200, self._main(sess, form, preview=image, notice="Náhled (neuloženo, zařízení ho nedostane)."))

    def _done(self, sess: dict, form: dict[str, str]) -> tuple[int, dict, bytes]:
        from .reminders import DoneStore, occurrence_key

        settings = load_settings(self.root, secret_lookup=None)
        name, day = form.get("name", ""), form.get("date", "")
        try:
            when = date.fromisoformat(day)
        except ValueError:
            return self._page(400, "<p>Neplatné datum.</p>")
        if name not in {r.name for r in settings.reminders.reminders}:
            return self._page(400, "<p>Neznámá připomínka.</p>")
        DoneStore(self.root / "state" / "done.json").set(occurrence_key(name, when), form.get("done") == "1", self.clock())
        return 303, {"Location": "/admin"}, b""

    # --- HTML ---------------------------------------------------------------------------------------
    def _page(self, code: int, inner: str) -> tuple[int, dict, bytes]:
        page = f"""<!DOCTYPE html><html lang="cs"><head><meta charset="utf-8"><title>Must-have · správa</title>
<meta name="viewport" content="width=device-width, initial-scale=1"><meta name="referrer" content="same-origin">
<style>body{{font-family:system-ui,sans-serif;margin:16px;max-width:860px}}label{{display:block;margin:8px 0 2px;font-weight:600}}
input[type=text],input[type=number],textarea,select{{width:100%;box-sizing:border-box;font:inherit;padding:4px}}textarea{{min-height:5em}}
.err{{background:#fee;border:1px solid #c00;padding:8px}}.ok{{background:#efe;border:1px solid #070;padding:8px}}
fieldset{{margin:12px 0}}img{{max-width:100%;border:1px solid #888}}button{{font:inherit;padding:6px 12px;margin:6px 6px 0 0}}
.muted{{color:#555;font-size:.9em}}</style></head><body><h1>Must-have · správa</h1>{inner}</body></html>"""
        return code, {"Content-Type": "text/html; charset=utf-8", "Cache-Control": "no-store",
                      "X-Frame-Options": "DENY", "Content-Security-Policy": "default-src 'self'; img-src 'self' data:; style-src 'unsafe-inline'"}, page.encode("utf-8")

    @staticmethod
    def _login_form(error: str = "") -> str:
        err = f'<p class="err">{html.escape(error)}</p>' if error else ""
        return f"""{err}<form method="post" action="/admin/login"><label for="token">Přístupový token</label>
<input type="password" id="token" name="token" autocomplete="current-password">
<p class="muted">Token vypíše na serveru <code>python3 -m musthave admin-token</code>.</p><button>Přihlásit</button></form>"""

    def _main(self, sess: dict, values: dict[str, str], errors: list[str] | None = None, notice: str | None = None,
              preview: bytes | None = None) -> str:
        e = lambda k: html.escape(values.get(k, "") or "")  # noqa: E731
        csrf = html.escape(sess["csrf"])
        state = change_state(self.root, self.devices)
        saved = (datetime.fromtimestamp(state["saved_at"]).strftime("%d. %m. %H:%M") if state["saved_at"] else "—")
        status = (f"<p>Konfigurace <code>{state['hash']}</code> · uloženo přes správu: {saved} · "
                  f"vyrenderováno: {'ano' if state['rendered'] else 'zatím ne'} · "
                  f"potvrzeno zařízením: {'ano' if state['confirmed'] else 'zatím ne'} · <a href=\"/status\">stav</a></p>")
        msgs = "".join(f'<p class="err">{html.escape(m)}</p>' for m in errors or [])
        if notice:
            msgs += f'<p class="ok">{html.escape(notice)}</p>'
        img = (f'<h2>Náhled 800×480</h2><img alt="náhled" src="data:image/png;base64,{base64.b64encode(preview).decode()}">'
               if preview else "")
        base, local, merged = effective(self.root)
        base_cd = "".join(f"<li>{html.escape(str(c.get('date')))} {html.escape(str(c.get('name')))}</li>"
                          for c in base.get("countdown", []) or [])
        n_sources = len((merged.get("agenda") or {}).get("sources") or [])
        mode_opts = "".join(f'<option value="{m}"{" selected" if values.get("streams_mode") == m else ""}>{m}</option>' for m in MODES)
        weather_opts = "".join(f'<option value="{w}"{" selected" if values.get("weather") == w else ""}>{w}</option>'
                               for w in WEATHER_VARIANTS)
        profiles = ", ".join(html.escape(str(p.get("name"))) for p in (merged.get("layout") or {}).get("profile", []) or [])
        done_rows = self._done_rows(csrf)
        return f"""{status}{msgs}
<form method="post" action="/admin/preview"><input type="hidden" name="csrf" value="{csrf}">
<fieldset><legend>Lokalita</legend>
<label for="location_name">Název</label><input type="text" id="location_name" name="location_name" value="{e('location_name')}">
<label for="latitude">Zeměpisná šířka</label><input type="text" id="latitude" name="latitude" value="{e('latitude')}">
<label for="longitude">Zeměpisná délka</label><input type="text" id="longitude" name="longitude" value="{e('longitude')}">
<label for="timezone">Časová zóna</label><input type="text" id="timezone" name="timezone" value="{e('timezone')}"></fieldset>
<fieldset><legend>Streamy</legend>
<label for="kick">Kick (jeden kanál na řádek)</label><textarea id="kick" name="kick">{e('kick')}</textarea>
<label for="twitch">Twitch (jeden kanál na řádek)</label><textarea id="twitch" name="twitch">{e('twitch')}</textarea>
<label for="streams_mode">Režim</label><select id="streams_mode" name="streams_mode">{mode_opts}</select>
<label for="favorites">Oblíbení (kick:kanál / twitch:kanál)</label><textarea id="favorites" name="favorites">{e('favorites')}</textarea>
<label for="max_items">Limit položek na seznam (0 = bez limitu)</label><input type="number" id="max_items" name="max_items" min="0" max="20" value="{e('max_items')}">
<label><input type="checkbox" name="combined"{' checked' if values.get('combined') == 'on' else ''}> Společný seznam Kick + Twitch</label></fieldset>
<fieldset><legend>Odpočty</legend>
<p class="muted">Ze základního config.toml (jen ke čtení):</p><ul>{base_cd or '<li>—</li>'}</ul>
<label for="countdowns">Vlastní odpočty: <code>YYYY-MM-DD | Název | yearly</code> (jeden na řádek)</label>
<textarea id="countdowns" name="countdowns">{e('countdowns')}</textarea></fieldset>
<fieldset><legend>Panely (full layout)</legend>
<label for="panels">Panely (prázdné = výchozí): {', '.join(PANEL_TITLES)}</label><input type="text" id="panels" name="panels" value="{e('panels')}">
<label for="weather">Počasí</label><select id="weather" name="weather">{weather_opts}</select>
<label for="hide_empty">Skrýt, když jsou prázdné</label><input type="text" id="hide_empty" name="hide_empty" value="{e('hide_empty')}">
<p class="muted">Profily ({profiles or 'žádné'}) a kalendáře ({n_sources} nastaveno) se upravují v config.local.toml.</p></fieldset>
<button type="submit">Náhled</button><button type="submit" formaction="/admin/save">Uložit a aktivovat</button></form>
<form method="post" action="/admin/restore"><input type="hidden" name="csrf" value="{csrf}"><button>Obnovit předchozí nastavení</button></form>
{done_rows}
<form method="post" action="/admin/logout"><input type="hidden" name="csrf" value="{csrf}"><button>Odhlásit</button></form>
{img}"""

    def _done_rows(self, csrf: str) -> str:
        from .reminders import DoneStore, occurrence_key, upcoming

        try:
            settings = load_settings(self.root, secret_lookup=None)
        except Exception:  # noqa: BLE001
            return ""
        today = datetime.now(ZoneInfo(settings.timezone)).date()
        done = DoneStore(self.root / "state" / "done.json").load()
        rows = []
        for when, _, rem in upcoming(settings.reminders, today):
            key = occurrence_key(rem.name, when)
            is_done = key in done
            rows.append(f"""<li>{html.escape(rem.name)} · {when.day}. {when.month}. · {'splněno' if is_done else 'otevřené'}
<form method="post" action="/admin/done" style="display:inline"><input type="hidden" name="csrf" value="{csrf}">
<input type="hidden" name="name" value="{html.escape(rem.name)}"><input type="hidden" name="date" value="{when.isoformat()}">
<input type="hidden" name="done" value="{'0' if is_done else '1'}"><button>{'Vrátit' if is_done else 'Splněno'}</button></form></li>""")
        return f"<h2>Připomínky</h2><ul>{''.join(rows)}</ul>" if rows else ""
