#!/usr/bin/env bash
# macOS: BYOS server (lokální render + HTTP pro zařízení) jako launchd agent s KeepAlive.
# Použití: deploy/install_server_launchd.sh            (instalace / aktualizace, nahradí agenta sběrače)
#          deploy/install_server_launchd.sh --remove   (odinstalace)
# Vyžaduje .venv s requirements-server.txt (uv venv .venv && uv pip install -p .venv/bin/python -r requirements-server.txt)
# a Google Chrome / Chromium. Tajemství pro push do TRMNL čte z Keychain (volitelné).
set -euo pipefail

LABEL="com.trmnl-musthave-server"
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"
DOMAIN="gui/$(id -u)"

if [[ "${1:-}" == "--remove" ]]; then
  launchctl bootout "$DOMAIN/$LABEL" 2>/dev/null || true
  command rm -f "$PLIST"
  echo "→ $LABEL odinstalován"
  exit 0
fi

[[ -x "$ROOT/.venv/bin/python" ]] || { echo "chybí $ROOT/.venv – viz hlavička skriptu" >&2; exit 2; }
mkdir -p "$ROOT/state" "$HOME/Library/LaunchAgents"
sed -e "s|__ROOT__|$ROOT|g" "$ROOT/deploy/$LABEL.plist" > "$PLIST"
# sběrač (timer) nahrazuje server – smyčka serveru posílá data do TRMNL sama
launchctl bootout "$DOMAIN/com.trmnl-musthave" 2>/dev/null || true
launchctl bootout "$DOMAIN/$LABEL" 2>/dev/null || true
launchctl bootstrap "$DOMAIN" "$PLIST"
sleep 6
IP="$(ipconfig getifaddr en0 2>/dev/null || ipconfig getifaddr en1 2>/dev/null || echo '<ip>')"
PORT="$(python3 -c 'import tomllib,pathlib;c=tomllib.loads(pathlib.Path("config.toml").read_text());l=pathlib.Path("config.local.toml");c.get("server",{}).update(tomllib.loads(l.read_text()).get("server",{})) if l.exists() else None;print(c.get("server",{}).get("port",8080))' 2>/dev/null || echo 8080)"
echo "→ $LABEL běží; zařízení nastav na http://$IP:${PORT:-8080} (captive portál → Advanced → Custom Server)"
curl -s -m 5 "http://127.0.0.1:${PORT:-8080}/api/display" || true
echo
tail -n 3 "$ROOT/state/server.log" 2>/dev/null || true
