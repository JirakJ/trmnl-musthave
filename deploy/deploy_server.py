#!/usr/bin/env python3
"""Nasazení BYOS serveru (jen server, žádný sběrač/timer) na Raspberry Pi jedním příkazem, se zálohou a ověřením.

    deploy/deploy_server.py deploy v1.7.0 [--host rpi] [--dry-run] [--no-wait] [--ack-timeout S]
    deploy/deploy_server.py rollback v1.7.0 [--host rpi]

Postup `deploy` (automatizuje ruční postup z vydání v1.6.0):
 1. přesný commit z tagu/refu, archiv `git archive` (jen soubory z Gitu → žádná tajemství, stav ani venv)
    a manifest se SHA-256 každého souboru; archiv se ověří, že neobsahuje chráněné cesty,
 2. kontrola cíle (služba existuje, sběračový timer se NEzapíná, místo na disku),
 3. záloha běžícího kódu `state/deployments/<tag>/before-<tag>.tar.gz` (bez chráněných cest),
 4. aktivace: rozbalení do stage + rsync do aplikace; chráněné cesty se nikdy nepřepíší ani nesmažou:
    config.local.toml(.bak), .env, .venv, state/ (registr zařízení, snímky, cache, admin token),
    deploy/firmware/*.bin a ota_wait; závislosti se instalují jen při změně requirements-server.txt,
 5. restart služby a ověření: služba běží, nasazené soubory odpovídají manifestu, render po restartu proběhl
    bez chyby a aktuální snímek je platný 1-bit PNG 800×480 – jinak automatický návrat zálohy + restart,
 6. zvlášť se vykáže aktivace serveru, nabídnutí snímku zařízení a potvrzení přes skutečný X-Frame-Id;
    čekání má timeout podle naplánovaného probuzení zařízení (+ tolerance). Nepotvrzení = stav
    „nasazeno, zařízení nepotvrdilo“, žádný rollback (spící/offline displej není chyba nasazení),
 7. stručný výsledek `state/deployments/<tag>/result.json` (verze, commit, snímek, stav zařízení).
Příkaz je opakovatelný (stejný tag = stejný archiv, záloha se nepřepíše) a `--dry-run` nic nemění.
"""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import shlex
import struct
import subprocess
import sys
import tarfile
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

ROOT = Path(__file__).resolve().parent.parent
APP = "trmnl-musthave"
SERVICE = "trmnl-musthave-server@{user}.service"
COLLECTOR_TIMER = "trmnl-musthave@{user}.timer"
# Cesty, které nasazení nikdy nepřepíše ani nesmaže (rsync --exclude + kontrola archivu).
PROTECTED = (".env", "config.local.toml", "config.local.toml.bak", ".venv/", "state/", "__pycache__/",
             "deploy/firmware/*.bin", "deploy/firmware/ota_wait", ".pytest_cache/", "preview/out.html", "preview/shots/")
FORBIDDEN_IN_ARCHIVE = (".env", "config.local.toml", "state/", ".venv/", "deploy/firmware/latest.bin")
DEFAULT_ACK_TOLERANCE_S = 600
MAX_ACK_WAIT_S = 2 * 3600


class DeployError(RuntimeError):
    pass


# --- místní strana -------------------------------------------------------------------------------------------
def git(*args: str, cwd: Path = ROOT) -> str:
    out = subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, timeout=60)
    if out.returncode != 0:
        raise DeployError(f"git {' '.join(args)}: {out.stderr.strip()}")
    return out.stdout.strip()


def build_archive(ref: str, cwd: Path = ROOT) -> tuple[str, bytes, dict[str, str]]:
    """(commit, tar.gz bytes, manifest {cesta: sha256}) pro přesný commit refu."""
    commit = git("rev-parse", f"{ref}^{{commit}}", cwd=cwd)
    raw = subprocess.run(["git", "archive", "--format=tar", commit], cwd=cwd, capture_output=True, timeout=120)
    if raw.returncode != 0:
        raise DeployError(f"git archive failed: {raw.stderr.decode(errors='replace')}")
    manifest: dict[str, str] = {}
    out = io.BytesIO()
    with tarfile.open(fileobj=io.BytesIO(raw.stdout)) as src, tarfile.open(fileobj=out, mode="w:gz") as dst:
        for member in src.getmembers():
            name = member.name
            if any(name == f.rstrip("/") or name.startswith(f) for f in FORBIDDEN_IN_ARCHIVE):
                raise DeployError(f"archive contains protected path {name}")
            if member.isfile():
                data = src.extractfile(member).read()
                manifest[name] = hashlib.sha256(data).hexdigest()
                dst.addfile(member, io.BytesIO(data))
            elif member.isdir() or member.issym():
                dst.addfile(member)
    return commit, out.getvalue(), manifest


def png_info(data: bytes) -> tuple[int, int, int, int]:
    """(šířka, výška, bitová hloubka, typ barev) z hlavičky PNG; ValueError, když to není PNG."""
    if data[:8] != b"\x89PNG\r\n\x1a\n" or data[12:16] != b"IHDR":
        raise ValueError("not a PNG")
    width, height, depth, color = struct.unpack(">IIBB", data[16:26])
    return width, height, depth, color


# --- vzdálená strana ----------------------------------------------------------------------------------------
@dataclass
class Remote:
    """Spouští příkazy na cíli. host=None = lokálně (testy)."""
    host: str | None
    timeout: int = 300

    def run(self, script: str, data: bytes | None = None, check: bool = True) -> subprocess.CompletedProcess:
        cmd = ["bash", "-c", script] if self.host is None else ["ssh", "-o", "BatchMode=yes", self.host, script]
        proc = subprocess.run(cmd, input=data, capture_output=True, timeout=self.timeout)
        if check and proc.returncode != 0:
            raise DeployError(f"remote command failed ({proc.returncode}): {proc.stderr.decode(errors='replace')[-400:]}")
        return proc

    def text(self, script: str) -> str:
        return self.run(script).stdout.decode("utf-8", "replace").strip()


@dataclass
class Plan:
    tag: str
    commit: str
    app: str
    deploy_dir: str
    service: str
    port: int
    archive: bytes = b""
    manifest: dict[str, str] = field(default_factory=dict)


@dataclass
class Deployer:
    remote: Remote
    log: Callable[[str], None] = print
    systemctl: str = "sudo systemctl"
    journal: str = "sudo journalctl"
    sleep: Callable[[float], None] = time.sleep
    render_timeout_s: int = 240
    clock: Callable[[], float] = time.time
    app: str | None = None              # cílový adresář aplikace (výchozí ~/trmnl-musthave na cíli)
    repo: Path = ROOT                   # místní git repozitář

    # pomocné ------------------------------------------------------------------------------------------------
    def http(self, port: int, path: str, binary: bool = False) -> bytes:
        code = ("import sys,urllib.request;"
                f"sys.stdout.buffer.write(urllib.request.urlopen('http://127.0.0.1:{port}{path}',timeout=10).read())")
        return self.remote.run(f"python3 -c {shlex.quote(code)}").stdout

    def status(self, port: int) -> dict:
        return json.loads(self.http(port, "/status.json"))

    def plan(self, tag: str, service: str | None = None) -> Plan:
        commit, archive, manifest = build_archive(tag, self.repo)
        user = self.remote.text("id -un")
        app = self.app or f"{self.remote.text('printf %s \"$HOME\"')}/{APP}"
        port_code = ("import tomllib,pathlib;c=tomllib.loads(pathlib.Path('config.toml').read_text());"
                     "l=pathlib.Path('config.local.toml');"
                     "c.get('server',{}).update(tomllib.loads(l.read_text()).get('server',{})) if l.exists() else None;"
                     "print(c.get('server',{}).get('port',8080))")
        port = int(self.remote.text(f"cd {shlex.quote(app)} && python3 -c {shlex.quote(port_code)}"))
        return Plan(tag, commit, app, f"{app}/state/deployments/{tag}", service or SERVICE.format(user=user), port,
                    archive, manifest)

    def preflight(self, plan: Plan) -> dict:
        q = shlex.quote
        info = {
            "service_exists": self.remote.run(f"{self.systemctl} cat {q(plan.service)} >/dev/null 2>&1", check=False).returncode == 0,
            "service_active": self.remote.text(f"{self.systemctl} is-active {q(plan.service)} || true"),
            "collector_timer": self.remote.text(
                f"{self.systemctl} is-enabled {q(COLLECTOR_TIMER.format(user=self.remote.text('id -un')))} 2>/dev/null || true"),
            "free_mb": int(self.remote.text(f"df -Pm {q(plan.app)} | awk 'NR==2 {{print $4}}'") or 0),
            "local_config": self.remote.run(f"test -f {q(plan.app)}/config.local.toml", check=False).returncode == 0,
        }
        if not info["service_exists"]:
            raise DeployError(f"service {plan.service} not installed on target (use deploy/install.sh first)")
        if info["free_mb"] < 100:
            raise DeployError(f"not enough disk space on target ({info['free_mb']} MB)")
        return info

    # kroky -------------------------------------------------------------------------------------------------
    def upload(self, plan: Plan) -> None:
        q = shlex.quote
        d = plan.deploy_dir
        self.remote.run(f"mkdir -p {q(d)} && cat > {q(d)}/source-{q(plan.tag)}.tar.gz", data=plan.archive)
        manifest = {"tag": plan.tag, "commit": plan.commit, "files": plan.manifest}
        self.remote.run(f"cat > {q(d)}/manifest.json", data=json.dumps(manifest, indent=1).encode())

    def backup(self, plan: Plan) -> str:
        """Záloha běžícího kódu (bez chráněných cest). Existující záloha se nepřepisuje (opakované nasazení)."""
        q = shlex.quote
        target = f"{plan.deploy_dir}/before-{plan.tag}.tar.gz"
        patterns = [(p if p == "__pycache__/" else "./" + p).rstrip("/") for p in PROTECTED]  # __pycache__ kdekoli
        excludes = " ".join(f"--exclude={q(p)}" for p in patterns)
        self.remote.run(f"test -s {q(target)} || (cd {q(plan.app)} && tar czf {q(target)}.tmp {excludes} . && mv {q(target)}.tmp {q(target)})")
        self.remote.run(f"tar tzf {q(target)} >/dev/null")  # záloha musí jít přečíst
        return target

    def activate(self, plan: Plan) -> bool:
        """Rozbalí archiv do stage a rsyncem přenese do aplikace. Vrací True, když se změnily závislosti."""
        q = shlex.quote
        stage = f"{plan.deploy_dir}/stage"
        excludes = " ".join(f"--exclude={q(p)}" for p in PROTECTED)
        req_before = self.remote.text(f"sha256sum {q(plan.app)}/requirements-server.txt 2>/dev/null | cut -c1-64 || true")
        self.remote.run(f"rm -rf {q(stage)} && mkdir -p {q(stage)} && tar xzf {q(plan.deploy_dir)}/source-{q(plan.tag)}.tar.gz -C {q(stage)}")
        self.remote.run(f"rsync -a --checksum --delete {excludes} {q(stage)}/ {q(plan.app)}/")
        return req_before != plan.manifest.get("requirements-server.txt", "")

    def install_requirements(self, plan: Plan) -> None:
        q = shlex.quote
        self.remote.run(f"cd {q(plan.app)} && (command -v uv >/dev/null && uv pip install -q -p .venv/bin/python -r requirements-server.txt"
                        f" || .venv/bin/python -m pip install -q -r requirements-server.txt)")

    def restart(self, plan: Plan) -> float:
        started = self.clock()
        self.remote.run(f"{self.systemctl} restart {shlex.quote(plan.service)}")
        return started

    def verify_files(self, plan: Plan) -> list[str]:
        q = shlex.quote
        listing = "\n".join(f"{digest}  {path}" for path, digest in sorted(plan.manifest.items())) + "\n"
        proc = self.remote.run(f"cd {q(plan.app)} && sha256sum --quiet -c -", data=listing.encode(), check=False)
        return [line for line in proc.stdout.decode(errors="replace").splitlines() if line.strip()] if proc.returncode else []

    def verify_running(self, plan: Plan, since: float) -> dict:
        """Služba aktivní, po restartu proběhl tick bez chyby, /status.json odpovídá a snímek je 1-bit 800×480."""
        q = shlex.quote
        deadline = self.clock() + self.render_timeout_s
        since_text = f"@{int(since) - 2}"   # journalctl --since @unix (nezávislé na časové zóně)
        last_err = "no render yet"
        while self.clock() < deadline:
            active = self.remote.text(f"{self.systemctl} is-active {q(plan.service)} || true")
            if active == "failed":
                raise DeployError("service failed after restart")
            journal = self.remote.text(f"{self.journal} -u {q(plan.service)} --since {q(since_text)} --no-pager -o cat 2>/dev/null || true")
            if "tick crashed" in journal or "Traceback" in journal:
                raise DeployError("render loop crashed after restart: " + journal[-300:])
            if active == "active" and ("musthave.serve: frame " in journal or "data unchanged" in journal):
                try:
                    status = self.status(plan.port)
                    frame = status.get("frame_rendered")
                    if not frame:
                        raise DeployError("server reports no frame")
                    w, h, depth, color = png_info(self.http(plan.port, f"/frames/{frame}.png"))
                    if (w, h, depth, color) != (800, 480, 1, 0):
                        raise DeployError(f"frame is not 1-bit 800x480 ({w}x{h}, depth {depth}, color {color})")
                    want = plan.tag[1:] if plan.tag[:1] == "v" and plan.tag[1:2].isdigit() else None
                    if want and status.get("version") and status["version"] != want:
                        raise DeployError(f"server reports version {status['version']}, expected {want}")
                    return status
                except (DeployError, ValueError, json.JSONDecodeError) as err:
                    last_err = str(err)
            self.sleep(5)
        raise DeployError(f"render not verified within {self.render_timeout_s}s: {last_err}")

    def rollback_code(self, plan: Plan, reason: str) -> None:
        """Vrátí kód ze zálohy (smaže soubory nové verze, rozbalí zálohu) a restartuje službu."""
        q = shlex.quote
        self.log(f"! rollback: {reason}")
        backup = f"{plan.deploy_dir}/before-{plan.tag}.tar.gz"
        files = " ".join(q(p) for p in sorted(plan.manifest))
        if files:
            self.remote.run(f"cd {q(plan.app)} && rm -f -- {files}")
        self.remote.run(f"cd {q(plan.app)} && tar xzf {q(backup)}")
        self.remote.run(f"{self.systemctl} restart {q(plan.service)}")
        self.sleep(3)
        state = self.remote.text(f"{self.systemctl} is-active {q(plan.service)} || true")
        self.log(f"  previous code restored, service {state}")

    def wait_for_device(self, plan: Plan, status: dict, activated_at: float, timeout_s: int | None) -> dict:
        """Nabídnutí a potvrzení snímku přes X-Frame-Id. Timeout = do očekávaného probuzení + tolerance."""
        frame = status.get("frame_rendered")
        devices = status.get("devices") or []
        if not devices:
            return {"state": "no-device", "frame": frame}
        if timeout_s is None:
            waits = []
            for dev in devices:
                try:
                    expected = datetime.fromisoformat(dev["next_expected"]).timestamp()
                except (KeyError, TypeError, ValueError):
                    expected = self.clock() + 300
                waits.append(max(0.0, expected - self.clock()) + DEFAULT_ACK_TOLERANCE_S)
            timeout_s = int(min(MAX_ACK_WAIT_S, min(waits)))
        started = self.clock()
        deadline = started + timeout_s
        hard_deadline = started + MAX_ACK_WAIT_S
        offered = None
        since = activated_at - 1
        first_offer: dict = {}   # zařízení → (čas, snímek) prvního nabídnutí po aktivaci

        def ts(value) -> float:
            try:
                return datetime.fromisoformat(value).timestamp() if value else 0.0
            except (TypeError, ValueError):
                return 0.0

        while True:
            try:
                status = self.status(plan.port)
            except (DeployError, ValueError):
                status = {}
            for dev in status.get("devices") or []:
                # Server renderuje průběžně a při každém kontaktu nabídne novější snímek, takže `frame_offered`
                # se posouvá. Rozhoduje PRVNÍ nabídnutí po aktivaci (snímek nové verze): jakékoli potvrzení
                # přes X-Frame-Id zaznamenané po něm potvrzuje snímek nabídnutý až po aktivaci.
                offered_at = ts(dev.get("frame_offered_at"))
                key = dev.get("id")
                if dev.get("frame_offered") and offered_at >= since and key not in first_offer:
                    first_offer[key] = (offered_at, dev.get("frame_offered"))
                    # počkat do probuzení naplánovaného při tomto nabídnutí + tolerance (ukotveno, neposouvá se)
                    wake = ts(dev.get("next_expected"))
                    if wake:
                        deadline = max(deadline, min(hard_deadline, wake + DEFAULT_ACK_TOLERANCE_S))
                if key not in first_offer:
                    continue
                offered = first_offer[key][1]
                acked_at = ts(dev.get("frame_acknowledged_at"))
                if dev.get("frame_acknowledged") and acked_at > first_offer[key][0]:
                    return {"state": "confirmed", "device": key, "frame": dev.get("frame_acknowledged"),
                            "acknowledged_at": dev.get("frame_acknowledged_at")}
            if self.clock() >= deadline:
                return {"state": "offered-unconfirmed" if offered else "not-contacted", "frame": offered or frame,
                        "waited_s": int(self.clock() - started)}
            self.sleep(30)

    # celé příkazy ------------------------------------------------------------------------------------------
    def deploy(self, tag: str, dry_run: bool = False, wait: bool = True, ack_timeout: int | None = None,
               service: str | None = None) -> dict:
        plan = self.plan(tag, service)
        info = self.preflight(plan)
        self.log(f"→ {tag} = {plan.commit[:12]}, {len(plan.manifest)} files, {len(plan.archive) // 1024} KiB → {plan.app}")
        self.log(f"  service {plan.service} ({info['service_active']}), port {plan.port}, "
                 f"config.local.toml {'present' if info['local_config'] else 'missing'}, free {info['free_mb']} MB")
        if info["collector_timer"] in ("enabled", "enabled-runtime"):
            self.log("! collector timer is enabled on the target; deploy does not touch it")
        if dry_run:
            self.log("dry-run: nothing uploaded, nothing changed")
            return {"tag": tag, "commit": plan.commit, "dry_run": True, "files": len(plan.manifest)}
        self.upload(plan)
        backup = self.backup(plan)
        self.log(f"→ backup {backup}")
        deps_changed = self.activate(plan)
        try:
            if deps_changed:
                self.log("→ requirements changed, installing")
                self.install_requirements(plan)
            mismatched = self.verify_files(plan)
            if mismatched:
                raise DeployError(f"deployed files differ from manifest: {mismatched[:5]}")
            started = self.restart(plan)
            self.log("→ service restarted, waiting for a render")
            status = self.verify_running(plan, started)
        except Exception as err:  # noqa: BLE001
            self.rollback_code(plan, str(err))
            result = {"tag": tag, "commit": plan.commit, "result": "rolled-back", "error": str(err)}
            self._save_result(plan, result)
            raise DeployError(f"deploy failed and was rolled back: {err}") from err
        self.log(f"✓ server active, frame {status.get('frame_rendered')} rendered and valid")
        device = {"state": "not-waited"}
        if wait:
            self.log("→ waiting for the device to fetch and confirm the frame (X-Frame-Id)")
            device = self.wait_for_device(plan, status, started, ack_timeout)
        summary = {"confirmed": "nasazeno, zařízení potvrdilo snímek",
                   "offered-unconfirmed": "nasazeno, snímek nabídnut, zařízení nepotvrdilo",
                   "not-contacted": "nasazeno, zařízení nepotvrdilo (neozvalo se)",
                   "no-device": "nasazeno, žádné zařízení v registru",
                   "not-waited": "nasazeno, potvrzení zařízení se nečekalo"}[device["state"]]
        result = {"tag": tag, "commit": plan.commit, "result": "deployed", "summary": summary,
                  "frame": status.get("frame_rendered"), "device": device,
                  "activated_at": datetime.fromtimestamp(started, timezone.utc).isoformat(timespec="seconds")}
        self._save_result(plan, result)
        self.log(f"✓ {summary}")
        return result

    def rollback(self, tag: str, service: str | None = None) -> dict:
        plan = self.plan(tag, service)
        q = shlex.quote
        manifest = json.loads(self.remote.text(f"cat {q(plan.deploy_dir)}/manifest.json"))
        plan.manifest = manifest.get("files", {})
        if self.remote.run(f"test -s {q(plan.deploy_dir)}/before-{q(tag)}.tar.gz", check=False).returncode:
            raise DeployError(f"no backup for {tag} on target")
        self.rollback_code(plan, f"manual rollback of {tag}")
        state = self.remote.text(f"{self.systemctl} is-active {q(plan.service)} || true")
        result = {"tag": tag, "result": "rolled-back-manually", "service": state}
        self._save_result(plan, result)
        return result

    def _save_result(self, plan: Plan, result: dict) -> None:
        data = json.dumps({**result, "saved_at": datetime.now(timezone.utc).isoformat(timespec="seconds")}, indent=1)
        self.remote.run(f"mkdir -p {shlex.quote(plan.deploy_dir)} && cat > {shlex.quote(plan.deploy_dir)}/result.json",
                        data=data.encode(), check=False)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("command", choices=["deploy", "rollback"])
    ap.add_argument("tag", help="git tag/ref, např. v1.7.0")
    ap.add_argument("--host", default="rpi")
    ap.add_argument("--service", default=None, help=f"systemd služba (výchozí {SERVICE})")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--no-wait", action="store_true", help="nečekat na potvrzení snímku zařízením")
    ap.add_argument("--ack-timeout", type=int, default=None, help="max. čekání na zařízení v s (výchozí podle probuzení)")
    ap.add_argument("--app", default=None, help="adresář aplikace na cíli (výchozí ~/trmnl-musthave)")
    args = ap.parse_args(argv)
    deployer = Deployer(Remote(args.host), app=args.app)
    try:
        if args.command == "deploy":
            result = deployer.deploy(args.tag, dry_run=args.dry_run, wait=not args.no_wait, ack_timeout=args.ack_timeout,
                                     service=args.service)
        else:
            result = deployer.rollback(args.tag, service=args.service)
    except DeployError as err:
        print(f"✗ {err}", file=sys.stderr)
        return 1
    print(json.dumps(result, ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
