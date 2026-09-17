#!/usr/bin/env bash
# Nasazení na Raspberry Pi: rsync projektu + systemd timer (každých 5 min).
# Použití: deploy/install.sh [ssh-host]   (výchozí host: rpi)
set -euo pipefail

HOST="${1:-rpi}"
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
REMOTE_USER="$(ssh "$HOST" 'echo $USER')"
REMOTE_DIR="/home/$REMOTE_USER/trmnl-musthave"
ENV_FILE="$ROOT/.env"

echo "→ rsync do $HOST:$REMOTE_DIR"
ssh "$HOST" "mkdir -p '$REMOTE_DIR'"
rsync -a --delete \
  --exclude .git --exclude state --exclude .venv --exclude config.local.toml --exclude preview/out.html \
  --exclude __pycache__ --exclude .pytest_cache --exclude .claude-flow --exclude docs --exclude "deploy/firmware/*.bin" \
  "$ROOT/" "$HOST:$REMOTE_DIR/"
if [[ -f "$ENV_FILE" ]]; then
  scp -q "$ENV_FILE" "$HOST:$REMOTE_DIR/.env"
else
  echo "! lokální .env chybí – na RPi zůstane stávající nebo prázdný (timer poběží, ale bez TRMNL_WEBHOOK_UUID nic nepošle)" >&2
  ssh "$HOST" "touch '$REMOTE_DIR/.env'"
fi
ssh "$HOST" "chmod 600 '$REMOTE_DIR/.env'"

echo "→ systemd jednotky (template unit s uživatelem $REMOTE_USER)"
ssh "$HOST" "sudo cp '$REMOTE_DIR/deploy/trmnl-musthave.service' /etc/systemd/system/trmnl-musthave@.service \
  && sudo cp '$REMOTE_DIR/deploy/trmnl-musthave.timer' /etc/systemd/system/trmnl-musthave@.timer \
  && sudo systemctl daemon-reload \
  && sudo systemctl enable --now 'trmnl-musthave@$REMOTE_USER.timer' \
  && sudo systemctl start --no-block 'trmnl-musthave@$REMOTE_USER.service'"

echo "→ firewall: povolit port serveru jen pro LAN (ufw, pokud je aktivní); port a podsíť se čtou na cílovém stroji"
ssh "$HOST" "cd '$REMOTE_DIR' && PORT=\$(python3 -c 'import tomllib,pathlib;c=tomllib.loads(pathlib.Path(\"config.toml\").read_text());l=pathlib.Path(\"config.local.toml\");c.get(\"server\",{}).update(tomllib.loads(l.read_text()).get(\"server\",{})) if l.exists() else None;print(c.get(\"server\",{}).get(\"port\",8080))') \
  && SUBNET=\$(ip -4 route show scope link 2>/dev/null | awk '/proto kernel/ {print \$1; exit}') \
  && if sudo ufw status 2>/dev/null | grep -q 'Status: active'; then sudo ufw allow from \"\${SUBNET:-192.168.0.0/24}\" to any port \"\$PORT\" proto tcp comment 'trmnl-musthave BYOS server (LAN only)'; else echo 'ufw není aktivní'; fi"

echo "→ stav"
ssh "$HOST" "systemctl list-timers 'trmnl-musthave@*' --no-pager; sleep 3; journalctl -u 'trmnl-musthave@$REMOTE_USER' -n 5 --no-pager"
