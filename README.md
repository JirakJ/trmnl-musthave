# TRMNL Must-have

One screen for a [TRMNL](https://trmnl.com) e-ink display (800×480): local weather plus the live status of the
Kick and Twitch streamers you follow (online/offline, viewers, category, start time).

The collector is plain Python 3.12 (standard library only). It runs on a timer, fetches the data and pushes it to a
TRMNL private plugin. TRMNL renders the screen from the Liquid templates in `templates/`.

Česká verze: [README.cs.md](README.cs.md)

![full layout](preview/full.png)

## Data sources (no API keys required)

| Source | How |
| --- | --- |
| Weather | [Open-Meteo](https://open-meteo.com), coordinates from `config.toml` |
| Kick | unofficial `kick.com/api/v2/channels/<slug>` endpoint |
| Twitch | the public GraphQL endpoint the twitch.tv website uses (Client-ID read from the page, refreshed automatically), fallback [decapi.me](https://decapi.me) |

No Twitch or Kick developer application is needed.

## Configuration

`config.toml` holds the city, coordinates, the streamer lists and the send cadence. Streamers are plain string arrays;
edit the file and redeploy. Host-specific values (`[server] port`, `label`, `headless`) go into a git-ignored
`config.local.toml` next to it, which overrides the same tables and is never overwritten by `deploy/install.sh`.

```toml
[streams]
kick = ["fattypillow", "czechfather", "astatoro", "miken", "czechcloud"]
twitch = ["arcadebulls", "agraelus", "artemis", "cruelladk", "conducteir77", "oliverovykecy", "rob2628"]

[trmnl]
plugin_setting_id = 479481   # numeric id of your private plugin instance

[[countdown]]                # optional: days-to-go strip at the bottom of the screen
name = "GTA VI"
date = 2026-11-19
```

Each `[[countdown]]` entry becomes a "days to go" item (calendar days in the configured timezone, sorted by the
nearest date). The release day shows `DNES`; past events disappear on their own, unless the entry has
`yearly = true` (birthdays, Christmas), which rolls it over to the same day next year (29 Feb → 28 Feb). `[[countdown]]` entries in
`config.local.toml` are appended to the base list.

Secrets never live in the repo. The collector reads them, in this order of precedence, from the environment,
from a git-ignored `.env` file in the project root, or (macOS) from the Keychain:

| Variable | Purpose |
| --- | --- |
| `TRMNL_USER_API_KEY` | user API key (trmnl.com → Account). Used with `plugin_setting_id` to push data through the authenticated API and for `status`. |
| `TRMNL_WEBHOOK_UUID` | alternative: the UUID from the plugin's Webhook URL. If set, it is preferred and no API key is needed. |

Keychain entry: `security add-generic-password -s trmnl-musthave -a TRMNL_USER_API_KEY -w <key>`.

## Creating the private plugin

Everything can be done through the API with the user key (the TRMNL UI works too):

```bash
# 1. create the instance (plugin_id 37 = Private Plugin); note the returned id
curl -X POST https://trmnl.com/api/plugin_settings -H "Authorization: Bearer $TRMNL_USER_API_KEY" \
  -H "Content-Type: application/json" -d '{"plugin_id":37,"name":"Must-have"}'
# 2. upload strategy + templates as a zip archive (settings.yml with `strategy: webhook` + the .liquid files)
deploy/push_plugin.sh <id>
# 3. put the id into config.toml → [trmnl] plugin_setting_id
# 4. add the plugin to your device playlist (TRMNL UI → Playlists) and run once
python3 -m musthave run
```

## Commands

```
python3 -m musthave run            # fetch + push (respects the rate limits)
python3 -m musthave run --dry-run  # print the payload, send nothing
python3 -m musthave fetch          # JSON only
python3 -m musthave status         # devices on the account (needs TRMNL_USER_API_KEY)
uv run --with pytest pytest -q     # tests (no network)
uv run --with python-liquid preview/render.py --layout full   # local preview → preview/out.html
```

## Rate limits

TRMNL allows 12 pushes per hour (30 with TRMNL+) and a 5 kB payload. The collector runs every 5 minutes and
sends when the data changed (at most 12 pushes/hour) or as a heartbeat after 15 minutes. TRMNL re-renders a
private plugin no faster than every 15 minutes (`refresh_interval: 15` in settings.yml, in minutes, is the minimum
without TRMNL+; 5 and 10 get rounded up), so the device refresh is set to 15 minutes too. The payload is about 1.1 kB for 12 streamers; category names are shortened first and the
run fails loudly above 4.5 kB.

## BYOS mode: 5-minute updates without the TRMNL cloud

TRMNL re-renders a private plugin at most every 15 minutes unless you pay for TRMNL+. To update the screen every
5 minutes, run the built-in BYOS server instead and point the device at it. The server renders the same Liquid
template locally (headless Chrome → 1-bit PNG; the TRMNL framework CSS/JS is cached in `state/cache/` and the Inter
font ships in `musthave/assets/`, so a render never waits for the network; `deploy/install.sh` seeds the cache from the
local machine) and serves the three endpoints the firmware expects
(`/api/setup`, `/api/display`, `/api/log`). If a fetch or render fails, the previous image stays and the device
does not redraw (the `filename` does not change). The server still pushes data to the TRMNL cloud, so switching
back is a soft reset away.

The full layout shows a battery icon with a proportional fill and charge percentage next to the date.
The percentage is estimated from the device's last reported `Battery-Voltage` using the
[TRMNL OG calculation](https://help.trmnl.com/en/articles/10556850-device-battery-faq) and updates on the next
render. Without a valid reading, it shows `— %`.

```bash
uv venv .venv && uv pip install -p .venv/bin/python -r requirements-server.txt   # python-liquid + Pillow
.venv/bin/python -m musthave serve --once     # test render → state/screen/current.png
deploy/install_server_launchd.sh              # macOS: launchd agent with KeepAlive (replaces the collector agent)
```

Point the device at the server: hold the button on the back for 5 s, join the `TRMNL` Wi-Fi, in the captive
portal open **Advanced → Custom Server → Yes**, enter `http://<LAN-IP>:8080` (no trailing slash), then your Wi-Fi
credentials and Connect. Give the host a fixed LAN IP (DHCP reservation). Back to trmnl.com: pairing mode →
Advanced → Soft Reset, and clear the server field. `[server]` in `config.toml` sets port, refresh, image format
(`png` or `bmp`) and an explicit Chrome path. On a Raspberry Pi use `deploy/trmnl-musthave-server.service`
(`apt install chromium`).

## Protocol v1 (custom firmware)

With the [trmnl-musthave-firmware](https://github.com/JirakJ/trmnl-musthave-firmware) fork the device sends
`X-Frame-Id` (the frame it currently shows) and the server answers with `action: none | partial | full`. For
`partial` it serves `/frames/<id>.regions?from=<old>`: a small binary blob (`MHR1`) with up to 4 changed rectangles,
each carrying the old and new 1-bit pixels, so the panel refreshes only those windows without flashing. The server
also decides `sleep_mode` (light on USB, deep on battery, from the reported voltage), `refresh_rate`, when to do a
full refresh against ghosting (`full_after_partials`, `full_every_minutes`, `night_full_at`) and offers OTA from
`deploy/firmware/latest.bin` when `firmware_version.txt` differs from the device's version. Stock firmware keeps
working against the same server (it always takes the `full` path). See `docs/superpowers/specs/2026-09-16-custom-firmware-partial-refresh-design.md`.

## Hourly forecast (next 6 hours)

Under the current weather the full layout shows six hourly windows starting with the current hour: temperature,
probability of precipitation (bar + %) and the expected amount, plus a short summary such as
`Nejvyšší šance srážek 16–18 h (90 %) · 17,0 mm` or `Bez srážek do 15 h`. Data come from the same Open-Meteo
request (`hourly=temperature_2m,precipitation_probability,precipitation`, `timeformat=unixtime`):

- Open-Meteo reports precipitation for the *preceding* hour, so the window `h:00–h+1:00` takes its temperature
  from `h:00` and its precipitation from `h+1:00`. The summary names whole-hour windows only (no minute precision).
- Timestamps are unix time converted to the configured timezone, so windows cross midnight and DST correctly
  (two `02h` in October, none in March).
- Missing values are shown as `—`, never as zero, and the summary admits `část dat chybí`.
- When the weather runs on its last good data (up to 3 h), windows that already ended are dropped and the screen
  shows `data z HH:MM`.
- The TRMNL cloud payload (5 kB limit) is fitted by `fit_for_cloud()`: hourly data are dropped before anything
  essential when the payload would not fit; the local BYOS render has no such limit.

Previews: `preview/scenarios/hourly-{dry,heavy,missing,midnight}.json`, rendered with `preview/shot.py`.

## ČHMÚ weather warnings

An optional strip under the header shows the most severe valid or upcoming ČHMÚ warning for the configured area:
level (`nízký` / `vysoký` / `extrémní stupeň`; high levels are inverted black so they read without colour), event,
validity, `+N další` when more warnings apply, the source and the issue time. It is an informative addition to the
official warnings, not a replacement.

```toml
[alerts]
area_codes = ["6105"]   # ORP codes from the ČSÚ codebook 65 (CISORP); Jihlava = 6105; empty = disabled
feeds = ["50"]          # 50 = meteorological warnings (SIVS), 70 = hydrological drought
refresh_minutes = 15    # listing check interval; a new XML is downloaded only when the file changes
max_age_hours = 6       # cached warnings older than this → "Výstrahy ČHMÚ nedostupné"
horizon_hours = 24      # show warnings starting within this time
show_none = false       # true = show "Bez platné výstrahy" too
```

How the source is used (verified 11/2026 against <https://opendata.chmi.cz/meteorology/weather/alerts/cap/> and its
`metadata/Dokumentace_CAP.pdf`): each `alert_cap_50_DDHHMM.xml` is a complete SIVS snapshot for the whole country;
file names are recycled monthly, so the newest file is picked by its listing time, verified by `<sent>`, and an older
message never replaces a newer cache. Areas are matched by `CISORP` geocodes, only Czech `<info>` blocks with an
`awareness_level` and a certainty other than `Unlikely` count, `Cancel`/non-`Actual` messages carry no warnings, and
warnings past `<expires>` (or `eventEndingTime`) disappear even from the cache. Priority: highest level, then earliest
onset, then name. "No warning" (strip hidden or `Bez platné výstrahy`) is distinct from "source unavailable"
(`Výstrahy ČHMÚ nedostupné`), so a dead feed never looks like an all-clear. The parser limits download size and the
number of blocks and rejects DTD/ENTITY declarations (no entity expansion, no external references). Data: ČHMÚ open
data (attribution "ČHMÚ" is shown on the strip; check the dataset entry in the national open-data catalogue for the
current licence terms).

## Battery saving (night mode, low battery)

Saving changes the `refresh_rate` the server sends to the device, so the device actually wakes up less often
(skipping renders alone would not save anything). Settings live in `[server]`:

| Key | Default | Meaning |
| --- | --- | --- |
| `saving_mode` | `auto` | `auto`, `off` (never save) or a forced `quiet` / `low` |
| `quiet_start`, `quiet_end` | empty (off) | quiet window in local time, may cross midnight (`"23:00"`–`"06:00"`) |
| `interval_quiet` | 1800 | seconds between wake-ups in the quiet window |
| `quiet_on_usb` | false | apply the quiet window on USB power too |
| `low_battery_enter` / `low_battery_exit` | 0 (off) / enter + 5 | battery % thresholds with hysteresis |
| `interval_low_battery` | 900 | seconds between wake-ups on low battery |
| `max_refresh_seconds` | 3600 | upper bound of any `refresh_rate` sent to the device |

Order of rules: OTA waiting (20 s) → forced `saving_mode` → power (USB does not save) → low battery → quiet window;
when both apply the longer interval wins. The quiet window is computed in the configured timezone, so it works across
midnight and DST changes, and the last wake-up in the window is scheduled for its end (never a whole extra quiet
interval later). The low-battery state uses the last plausible voltage; an unknown or implausible reading keeps the
previous state instead of pretending 0 %. Long intervals are aligned to the `align_minutes` slots. `/status` shows
the active mode, its reason and the expected next contact; the full layout shows a short `noční režim` / `úspora`
tag next to the battery.

## Status page (`/status`, `/status.json`)

The BYOS server shows a readable overview at `http://<host>:<port>/status` (JSON at `/status.json`):

- **frames**: the last *rendered* frame (render loop), the frame *offered* to each device (sent in a `partial`/`full`
  answer) and the frame *confirmed* by the firmware. A frame counts as confirmed only when the device's next request
  carries it in `X-Frame-Id`; a sent response is not a confirmation. Stock firmware sends no `X-Frame-Id`, so its
  frames always stay unconfirmed.
- **device**: last contact, the interval it was actually given (`refresh_rate`) and the expected next contact,
  firmware, battery estimate and Wi-Fi. A device is `opožděné` (late) after the expected wake-up and `neaktivní`
  (inactive) only after `[server] offline_tolerance_seconds` (default 600) more, so deep sleep is never an outage.
- **Wi-Fi**: the last valid `RSSI` header (−120…−1 dBm) and when it was measured; missing or invalid values keep the
  previous measurement, without any measurement the state is `neznámý`. Percent = `2 × (RSSI + 100)` clamped to
  0–100 (−100 dBm = 0 %, −50 dBm or better = 100 %).
- **data freshness**: the time of the last *successful* fetch of each source, independent of the render time.
  When a source runs on its last good data, the screen shows `data z HH:MM` (the fetch time) instead of the render time.

The status is server-side only; the frozen e-ink frame never claims the display is online or offline.

## Deploying to a Raspberry Pi

```
deploy/install.sh [ssh-host]      # rsync to ~/trmnl-musthave + systemd timer (5 min); copies .env if present
ssh rpi journalctl -u 'trmnl-musthave@*' -n 20
```

## Layout

```
musthave/   config, http, weather, kick, twitch, payload, state, trmnl (push clients), run,
            screen (render) + frames + diff + policy + devices + server + serve (BYOS v1), __main__
templates/  full / half_horizontal / half_vertical / quadrant .liquid
preview/    render.py, sample.json (real payload), screenshots
deploy/     systemd units + timer, install.sh, push_plugin.sh, launchd plists, BYOS server unit
tests/      pytest with fixtures captured from the real APIs
docs/superpowers/  design spec and implementation plan (Czech)
```

## License

MIT
