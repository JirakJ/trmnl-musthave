"""#17 Nasazení jedním příkazem: archiv + manifest, záloha, chráněné cesty, ověření, rollback, potvrzení zařízením.

Cíl se simuluje lokálně (Remote(host=None) = bash), systemctl/journalctl jsou falešné skripty a /status.json
obsluhuje malý HTTP server – žádné SSH ani skutečná služba."""

import io
import json
import subprocess
import sys
import threading
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "deploy"))
from deploy_server import DeployError, Deployer, Remote, build_archive, png_info  # noqa: E402

FRAME = "musthave-frame-abcdef0123"


def png_1bit() -> bytes:
    from PIL import Image

    buf = io.BytesIO()
    Image.new("1", (800, 480), 1).save(buf, format="PNG")
    return buf.getvalue()


@pytest.fixture
def world(tmp_path):
    # fake status server
    state = {"status": {"frame_rendered": FRAME, "devices": []}, "png": png_1bit()}

    class H(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def do_GET(self):
            body = json.dumps(state["status"]).encode() if self.path == "/status.json" else state["png"]
            self.send_response(200)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    port = srv.server_port

    # repo s novou verzí
    repo = tmp_path / "repo"
    (repo / "musthave").mkdir(parents=True)
    (repo / "config.toml").write_text('[server]\nport = 1\n', encoding="utf-8")
    (repo / "musthave" / "__init__.py").write_text("NEW\n", encoding="utf-8")
    (repo / "requirements-server.txt").write_text("pillow\n", encoding="utf-8")
    (repo / "deploy" / "firmware").mkdir(parents=True)
    (repo / "deploy" / "firmware" / "firmware_version.txt").write_text("2.0.9\n", encoding="utf-8")
    for cmd in (["init", "-q"], ["add", "-A"], ["-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "v"],
                ["tag", "v9.0.0"]):
        subprocess.run(["git", *cmd], cwd=repo, check=True, capture_output=True)

    # cíl se starou verzí a chráněnými soubory
    app = tmp_path / "app"
    (app / "musthave").mkdir(parents=True)
    (app / "config.toml").write_text('[server]\nport = 1\n', encoding="utf-8")
    (app / "musthave" / "__init__.py").write_text("OLD\n", encoding="utf-8")
    (app / "musthave" / "old_only.py").write_text("OLD ONLY\n", encoding="utf-8")
    (app / "requirements-server.txt").write_text("pillow\n", encoding="utf-8")
    (app / "config.local.toml").write_text(f'[server]\nport = {port}\nlabel = "RPi"\n', encoding="utf-8")
    (app / ".env").write_text("TRMNL_USER_API_KEY=secret\n", encoding="utf-8")
    (app / "state" / "frames").mkdir(parents=True)
    (app / "state" / "devices.json").write_text('{"AA": {}}', encoding="utf-8")
    (app / "deploy" / "firmware").mkdir(parents=True)
    (app / "deploy" / "firmware" / "latest.bin").write_bytes(b"\xe9fw")
    (app / ".venv").mkdir()
    (app / ".venv" / "marker").write_text("venv", encoding="utf-8")

    # falešné systemctl / journalctl
    fake = tmp_path / "fake"
    fake.mkdir()
    (fake / "active").write_text("active", encoding="utf-8")
    (fake / "journal").write_text("INFO musthave.serve: frame " + FRAME, encoding="utf-8")
    systemctl = fake / "systemctl"
    systemctl.write_text(f"""#!/bin/bash
case "$1" in
  cat) exit 0;;
  is-active) cat {fake}/active;;
  is-enabled) echo disabled;;
  restart) echo restart >> {fake}/restarts;;
esac
""", encoding="utf-8")
    journal = fake / "journalctl"
    journal.write_text(f"#!/bin/bash\ncat {fake}/journal\n", encoding="utf-8")
    systemctl.chmod(0o755)
    journal.chmod(0o755)
    logs = []
    deployer = Deployer(Remote(None), log=logs.append, systemctl=str(systemctl), journal=str(journal),
                        sleep=lambda s: None, render_timeout_s=5, app=str(app), repo=repo)
    yield {"deployer": deployer, "app": app, "fake": fake, "state": state, "logs": logs, "repo": repo, "port": port}
    srv.shutdown()


def restarts(world):
    path = world["fake"] / "restarts"
    return len(path.read_text().splitlines()) if path.exists() else 0


def assert_protected_untouched(app):
    assert (app / "config.local.toml").read_text(encoding="utf-8").endswith('label = "RPi"\n')
    assert (app / ".env").read_text(encoding="utf-8") == "TRMNL_USER_API_KEY=secret\n"
    assert (app / "state" / "devices.json").read_text(encoding="utf-8") == '{"AA": {}}'
    assert (app / "deploy" / "firmware" / "latest.bin").read_bytes() == b"\xe9fw"
    assert (app / ".venv" / "marker").exists()


def test_archive_is_exact_commit_with_manifest_and_no_secrets(world, tmp_path):
    commit, archive, manifest = build_archive("v9.0.0", world["repo"])
    assert len(commit) == 40 and set(manifest) == {"config.toml", "musthave/__init__.py", "requirements-server.txt",
                                                   "deploy/firmware/firmware_version.txt"}
    (world["repo"] / ".env").write_text("x", encoding="utf-8")
    subprocess.run(["git", "add", "-f", ".env"], cwd=world["repo"], check=True)
    subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "oops"], cwd=world["repo"], check=True)
    with pytest.raises(DeployError, match="protected"):
        build_archive("HEAD", world["repo"])


def test_png_info():
    assert png_info(png_1bit()) == (800, 480, 1, 0)
    with pytest.raises(ValueError):
        png_info(b"GIF89a")


def test_dry_run_changes_nothing(world):
    result = world["deployer"].deploy("v9.0.0", dry_run=True)
    assert result["dry_run"] and (world["app"] / "musthave" / "__init__.py").read_text() == "OLD\n"
    assert not (world["app"] / "state" / "deployments").exists() and restarts(world) == 0


def test_successful_deploy_backs_up_activates_verifies_and_reports_confirmation(world):
    now = datetime.now(timezone.utc)
    world["state"]["status"]["devices"] = [{"id": "AA", "frame_offered": FRAME, "frame_acknowledged": FRAME,
                                            "frame_acknowledged_at": now.isoformat(), "frame_shown": FRAME,
                                            "last_seen": now.isoformat(), "next_expected": now.isoformat()}]
    result = world["deployer"].deploy("v9.0.0")
    app = world["app"]
    assert result["result"] == "deployed" and result["device"]["state"] == "confirmed"
    assert "potvrdilo" in result["summary"] and result["frame"] == FRAME
    assert (app / "musthave" / "__init__.py").read_text() == "NEW\n"
    assert not (app / "musthave" / "old_only.py").exists()                       # rsync --delete jen pro kód
    assert_protected_untouched(app)
    d = app / "state" / "deployments" / "v9.0.0"
    assert (d / "before-v9.0.0.tar.gz").stat().st_size > 0 and json.loads((d / "manifest.json").read_text())["files"]
    assert json.loads((d / "result.json").read_text())["result"] == "deployed" and restarts(world) == 1
    import tarfile
    names = tarfile.open(d / "before-v9.0.0.tar.gz").getnames()
    assert "./musthave/old_only.py" in names and not any(n.startswith(("./state", "./.venv", "./.env", "./config.local"))
                                                          for n in names)


def test_failed_render_rolls_back_and_restarts(world):
    (world["fake"] / "journal").write_text("Traceback (most recent call last): boom", encoding="utf-8")
    with pytest.raises(DeployError, match="rolled back"):
        world["deployer"].deploy("v9.0.0")
    app = world["app"]
    assert (app / "musthave" / "__init__.py").read_text() == "OLD\n" and (app / "musthave" / "old_only.py").exists()
    assert_protected_untouched(app)
    assert restarts(world) == 2
    result = json.loads((app / "state" / "deployments" / "v9.0.0" / "result.json").read_text())
    assert result["result"] == "rolled-back" and "crashed" in result["error"]


def test_invalid_frame_rolls_back(world):
    world["state"]["png"] = b"\x89PNG\r\n\x1a\n" + b"\x00\x00\x00\rIHDR" + (400).to_bytes(4, "big") + (240).to_bytes(4, "big") + b"\x08\x02"
    with pytest.raises(DeployError, match="rolled back"):
        world["deployer"].deploy("v9.0.0")
    assert (world["app"] / "musthave" / "__init__.py").read_text() == "OLD\n"


def test_unconfirmed_device_is_reported_without_rollback(world):
    world["state"]["status"]["devices"] = [{"id": "AA", "frame_offered": FRAME, "frame_acknowledged": "old",
                                            "frame_acknowledged_at": None, "frame_shown": "old", "last_seen": None,
                                            "next_expected": None}]
    result = world["deployer"].deploy("v9.0.0", ack_timeout=0)
    assert result["result"] == "deployed" and result["device"]["state"] == "offered-unconfirmed"
    assert "nepotvrdilo" in result["summary"] and (world["app"] / "musthave" / "__init__.py").read_text() == "NEW\n"


def test_repeated_deploy_keeps_original_backup_and_manual_rollback(world):
    world["deployer"].deploy("v9.0.0", wait=False)
    world["deployer"].deploy("v9.0.0", wait=False)                               # opakovatelné
    tar = world["app"] / "state" / "deployments" / "v9.0.0" / "before-v9.0.0.tar.gz"
    import tarfile
    assert "./musthave/old_only.py" in tarfile.open(tar).getnames()               # záloha = stav před prvním nasazením
    result = world["deployer"].rollback("v9.0.0")
    assert result["result"] == "rolled-back-manually"
    assert (world["app"] / "musthave" / "__init__.py").read_text() == "OLD\n"
    assert_protected_untouched(world["app"])


def test_missing_service_stops_before_any_change(world):
    (world["fake"] / "systemctl").write_text("#!/bin/bash\nexit 1\n", encoding="utf-8")
    with pytest.raises(DeployError, match="not installed"):
        world["deployer"].deploy("v9.0.0")
    assert (world["app"] / "musthave" / "__init__.py").read_text() == "OLD\n"
