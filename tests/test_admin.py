"""#14 Webová správa: přihlášení tokenem, CSRF, validace, atomický zápis, merge pravidla, náhled, stav změny."""

import json
import os
import re
import stat
import threading
import tomllib
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime
from pathlib import Path

import pytest

from musthave.admin import AdminApp, apply_form, config_hash, ensure_token, form_values
from musthave.config import load_settings
from musthave.devices import DeviceRegistry
from musthave.frames import BITMAP_BYTES, FrameStore
from musthave.policy import DeviceState, PolicyConfig
from musthave.server import make_server
from musthave.tomlw import dumps

BASE = """
[location]
name = "Jihlava"
latitude = 49.3961
longitude = 15.5912
timezone = "Europe/Prague"

[streams]
kick = ["fattypillow"]
twitch = ["agraelus"]

[[countdown]]
name = "GTA VI"
date = 2026-11-19

[[reminder]]
name = "Koš"
weekly = ["po", "út", "st", "čt", "pá", "so", "ne"]
"""
LOCAL = '[server]\nport = 8085\nlabel = "RPi"\n\n[agenda]\nsources = ["https://cal.example.com/private-SECRET/basic.ics"]\n'


@pytest.fixture
def env(tmp_path):
    (tmp_path / "config.toml").write_text(BASE, encoding="utf-8")
    (tmp_path / "config.local.toml").write_text(LOCAL, encoding="utf-8")
    (tmp_path / ".env").write_text("TRMNL_USER_API_KEY=user_SECRETKEY\n", encoding="utf-8")
    frames = FrameStore(tmp_path / "state" / "frames")
    devices = DeviceRegistry(tmp_path / "state" / "devices.json")
    previews = []

    def preview(root: Path) -> bytes:
        previews.append(load_settings(root, secret_lookup=None))
        return b"\x89PNG fake"

    app = AdminApp(tmp_path, devices, preview=preview, clock=lambda: datetime(2026, 11, 12, 21, 0))
    srv = make_server(frames, devices, PolicyConfig(align_minutes=0), port=0, root=tmp_path, admin=app)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{srv.server_port}"
    yield {"base": base, "root": tmp_path, "frames": frames, "devices": devices, "previews": previews, "app": app}
    srv.shutdown()


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


OPENER = urllib.request.build_opener(NoRedirect)


def call(env, path, data=None, cookie=None, origin=True):
    headers = {}
    if cookie:
        headers["Cookie"] = cookie
    if data is not None and origin:
        headers["Origin"] = env["base"]
    body = urllib.parse.urlencode(data).encode() if data is not None else None
    req = urllib.request.Request(env["base"] + path, data=body, headers=headers)
    try:
        with OPENER.open(req, timeout=10) as r:
            return r.status, dict(r.headers), r.read().decode()
    except urllib.error.HTTPError as err:
        return err.code, dict(err.headers), err.read().decode()


def login(env):
    status, headers, _ = call(env, "/admin/login", {"token": ensure_token(env["root"])})
    assert status == 303
    cookie = headers["Set-Cookie"].split(";")[0]
    page = call(env, "/admin", cookie=cookie)[2]
    csrf = re.search(r'name="csrf" value="([^"]+)"', page).group(1)
    return cookie, csrf, page


def form(env, **overrides):
    values = form_values(env["root"])
    values.update(overrides)
    return values


def test_token_file_is_private_and_stable(tmp_path):
    token = ensure_token(tmp_path)
    assert len(token) >= 20 and ensure_token(tmp_path) == token
    assert stat.S_IMODE(os.stat(tmp_path / "state" / "admin_token").st_mode) == 0o600


def test_login_required_and_wrong_token_rejected(env):
    status, _, page = call(env, "/admin")
    assert status == 200 and "Přístupový token" in page and "Jihlava" not in page
    status, _, _ = call(env, "/admin/login", {"token": "wrong"})
    assert status == 403
    status, _, _ = call(env, "/admin/save", {"location_name": "X"})
    assert status == 403                                         # bez relace žádný zápis


def test_secrets_and_private_urls_never_in_page(env):
    _, _, page = login(env)
    assert "SECRET" not in page and "user_SECRETKEY" not in page and "1 nastaveno" in page


def test_csrf_and_origin_are_required_for_writes(env):
    cookie, csrf, _ = login(env)
    data = form(env, location_name="Brno", csrf="bad")
    assert call(env, "/admin/save", data, cookie=cookie)[0] == 403
    data["csrf"] = csrf
    assert call(env, "/admin/save", data, cookie=cookie, origin=False)[0] == 403
    assert "Brno" not in (env["root"] / "config.local.toml").read_text(encoding="utf-8")


def test_save_writes_local_overlay_atomically_keeps_other_sections_and_backup(env):
    cookie, csrf, _ = login(env)
    data = form(env, location_name="Brno", latitude="49.19", longitude="16.61", twitch="agraelus\nrob2628", csrf=csrf)
    status, _, page = call(env, "/admin/save", data, cookie=cookie)
    assert status == 200 and "Uloženo" in page
    local = tomllib.loads((env["root"] / "config.local.toml").read_text(encoding="utf-8"))
    assert local["server"] == {"port": 8085, "label": "RPi"}                       # ruční nastavení zůstalo
    assert local["agenda"]["sources"] == ["https://cal.example.com/private-SECRET/basic.ics"]
    assert local["location"] == {"name": "Brno", "latitude": 49.19, "longitude": 16.61}  # timezone = základ → nezapsáno
    assert local["streams"] == {"twitch": ["agraelus", "rob2628"]}                 # kick = základ → nezapsáno
    assert (env["root"] / "config.local.toml.bak").read_text(encoding="utf-8") == LOCAL
    s = load_settings(env["root"], env={}, secret_lookup=None)
    assert s.location_name == "Brno" and s.twitch == ["agraelus", "rob2628"] and s.server_port == 8085


def test_invalid_values_show_specific_errors_and_change_nothing(env):
    cookie, csrf, _ = login(env)
    before = (env["root"] / "config.local.toml").read_text(encoding="utf-8")
    data = form(env, latitude="99", timezone="Mars/Olympus", countdowns="2026-13-01 | Špatně", kick="bad slug!", csrf=csrf)
    status, _, page = call(env, "/admin/save", data, cookie=cookie)
    assert status == 400
    for text in ("−90…90", "Mars/Olympus", "2026-13-01", "neplatné jméno kanálu"):
        assert text in page
    assert (env["root"] / "config.local.toml").read_text(encoding="utf-8") == before
    data = form(env, panels="kick, kick", csrf=csrf)                                 # projde formulářem, chytí load_settings
    status, _, page = call(env, "/admin/save", data, cookie=cookie)
    assert status == 400 and "dvakrát" in page


def test_repeated_saves_do_not_duplicate_countdowns(env):
    cookie, csrf, _ = login(env)
    cds = "2026-12-24 | Vánoce | yearly\n2026-12-24 | Vánoce | yearly\n2026-11-19 | GTA VI"
    for _ in range(3):
        values = form(env, csrf=csrf)
        values["countdowns"] = cds if _ == 0 else values["countdowns"]
        assert call(env, "/admin/save", values, cookie=cookie)[0] == 200
    local = tomllib.loads((env["root"] / "config.local.toml").read_text(encoding="utf-8"))
    assert [c["name"] for c in local["countdown"]] == ["Vánoce"]          # duplicitní řádek i základní GTA VI vynechány
    s = load_settings(env["root"], env={}, secret_lookup=None)
    assert [c.name for c in s.countdowns] == ["GTA VI", "Vánoce"]


def test_restore_previous_settings(env):
    cookie, csrf, _ = login(env)
    call(env, "/admin/save", form(env, location_name="Brno", csrf=csrf), cookie=cookie)
    status, _, page = call(env, "/admin/restore", {"csrf": csrf}, cookie=cookie)
    assert status == 200 and "obnoveno" in page
    assert (env["root"] / "config.local.toml").read_text(encoding="utf-8") == LOCAL


def test_preview_uses_draft_but_saves_nothing_and_sends_nothing(env):
    cookie, csrf, _ = login(env)
    frames_before = env["frames"].latest()
    before = (env["root"] / "config.local.toml").read_text(encoding="utf-8")
    status, _, page = call(env, "/admin/preview", form(env, location_name="Praha", csrf=csrf), cookie=cookie)
    assert status == 200 and "data:image/png;base64," in page and "neuloženo" in page
    assert env["previews"][-1].location_name == "Praha"                            # náhled z konceptu
    assert (env["root"] / "config.local.toml").read_text(encoding="utf-8") == before
    assert env["frames"].latest() == frames_before                                 # do FrameStore nic nepřibylo


def test_change_state_saved_rendered_confirmed(env):
    cookie, csrf, _ = login(env)
    call(env, "/admin/save", form(env, location_name="Brno", csrf=csrf), cookie=cookie)
    page = call(env, "/admin", cookie=cookie)[2]
    assert "vyrenderováno: zatím ne" in page and "potvrzeno zařízením: zatím ne" in page
    fid = env["frames"].put(b"\xff" * BITMAP_BYTES)
    (env["root"] / "state" / "frames" / "render.json").write_text(
        json.dumps({"frame_id": fid, "config_hash": config_hash(env["root"])}), encoding="utf-8")
    assert "vyrenderováno: ano" in call(env, "/admin", cookie=cookie)[2]
    env["devices"].save("AA", DeviceState(acked_frame_id=fid, last_seen_at=1.0))
    assert "potvrzeno zařízením: ano" in call(env, "/admin", cookie=cookie)[2]


def test_mark_reminder_done_and_logout(env):
    cookie, csrf, page = login(env)
    assert "Koš" in page and "Splněno" in page
    today = datetime.now().date().isoformat()
    status, _, _ = call(env, "/admin/done", {"csrf": csrf, "name": "Koš", "date": today, "done": "1"}, cookie=cookie)
    assert status == 303
    assert f"Koš|{today}" in json.loads((env["root"] / "state" / "done.json").read_text(encoding="utf-8"))
    assert call(env, "/admin/done", {"csrf": csrf, "name": "Neznámá", "date": today, "done": "1"}, cookie=cookie)[0] == 400
    assert call(env, "/admin/logout", {"csrf": csrf}, cookie=cookie)[0] == 303
    assert "Přístupový token" in call(env, "/admin", cookie=cookie)[2]


def test_apply_form_keeps_base_values_out_of_local():
    base = tomllib.loads(BASE)
    out, errors = apply_form(base, {}, {"location_name": "Jihlava", "latitude": "49.3961", "longitude": "15.5912",
                                        "timezone": "Europe/Prague", "kick": "fattypillow", "twitch": "agraelus",
                                        "streams_mode": "all", "max_items": "0", "weather": "full"})
    assert errors == [] and out == {}


def test_toml_writer_round_trip():
    data = {"a": 1, "s": 'x "y"\n', "l": ["a", "b"], "t": {"f": 1.5, "b": True},
            "layout": {"panels": ["kick"], "profile": [{"name": "noc", "widths": {"kick": 2}}]},
            "countdown": [{"name": "Vánoce", "date": datetime(2026, 12, 24).date(), "yearly": True}]}
    assert tomllib.loads(dumps(data, "hlavička")) == data


def test_login_rate_limit(env):
    for _ in range(10):
        call(env, "/admin/login", {"token": "nope"})
    assert call(env, "/admin/login", {"token": ensure_token(env["root"])})[0] == 429
