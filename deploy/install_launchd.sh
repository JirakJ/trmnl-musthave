#!/usr/bin/env bash
# macOS alternativa k Raspberry Pi: launchd agent, který spouští sběrač každých 5 minut.
# Použití: deploy/install_launchd.sh            (instalace / aktualizace)
#          deploy/install_launchd.sh --remove   (odinstalace)
# Tajemství čte sběrač z Keychain (security add-generic-password -s trmnl-musthave -a TRMNL_USER_API_KEY -w …).
set -euo pipefail

LABEL="com.trmnl-musthave"
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"
DOMAIN="gui/$(id -u)"

if [[ "${1:-}" == "--remove" ]]; then
  launchctl bootout "$DOMAIN/$LABEL" 2>/dev/null || true
  command rm -f "$PLIST"
  echo "→ $LABEL odinstalován"
  exit 0
fi

PYTHON="$(command -v python3)"
mkdir -p "$ROOT/state" "$HOME/Library/LaunchAgents"
sed -e "s|__ROOT__|$ROOT|g" -e "s|__PYTHON__|$PYTHON|g" "$ROOT/deploy/$LABEL.plist" > "$PLIST"
launchctl bootout "$DOMAIN/$LABEL" 2>/dev/null || true
launchctl bootstrap "$DOMAIN" "$PLIST"
launchctl kickstart -k "$DOMAIN/$LABEL"
sleep 4
echo "→ $LABEL běží každých 5 min; log: $ROOT/state/launchd.log"
tail -n 3 "$ROOT/state/launchd.log" 2>/dev/null || true
