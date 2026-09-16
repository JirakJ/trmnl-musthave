#!/usr/bin/env bash
# Nahraje strategii (webhook) + všechny Liquid šablony do existující instance privátního pluginu na trmnl.com.
# Použití: deploy/push_plugin.sh <plugin_setting_id>
# Klíč: TRMNL_USER_API_KEY v prostředí, nebo macOS Keychain (security add-generic-password -s trmnl-musthave -a TRMNL_USER_API_KEY -w …).
set -euo pipefail

ID="${1:?plugin_setting_id (číselné id instance z config.toml / TRMNL dashboardu)}"
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
KEY="${TRMNL_USER_API_KEY:-$(security find-generic-password -s trmnl-musthave -a TRMNL_USER_API_KEY -w 2>/dev/null || true)}"
[[ -n "$KEY" ]] || { echo "TRMNL_USER_API_KEY není v prostředí ani v Keychain" >&2; exit 2; }

TMP="$(mktemp -d)"
trap 'command rm -rf "$TMP"' EXIT
cp "$ROOT"/templates/*.liquid "$TMP/"
printf -- "---\nstrategy: webhook\nno_screen_padding: 'no'\ndark_mode: 'no'\nid: %s\n" "$ID" > "$TMP/settings.yml"
(cd "$TMP" && zip -q plugin.zip settings.yml ./*.liquid)

curl -sS -f -X POST "https://trmnl.com/api/plugin_settings/$ID/archive" \
  -H "Authorization: Bearer $KEY" -F "file=@$TMP/plugin.zip;type=application/zip" >/dev/null
echo "→ nahráno do plugin_setting $ID"
curl -sS -H "Authorization: Bearer $KEY" "https://trmnl.com/api/plugin_settings/$ID/details" \
  | python3 -c 'import sys,json; d=json.load(sys.stdin)["data"]; print("strategy:", d["strategy"], "| sizes:", {k:v for k,v in d["sizes"].items() if v})'
