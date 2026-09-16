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
edit the file and redeploy.

```toml
[streams]
kick = ["fattypillow", "czechfather", "astatoro", "miken", "czechcloud"]
twitch = ["arcadebulls", "agraelus", "artemis", "cruelladk", "conducteir77", "oliverovykecy", "rob2628"]

[trmnl]
plugin_setting_id = 479481   # numeric id of your private plugin instance
```

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

TRMNL allows 12 pushes per hour (30 with TRMNL+) and a 5 kB payload. The collector runs every 5 minutes but only
sends when the data changed and at least 6 minutes passed, or as a heartbeat after 15 minutes, so it stays at
10 pushes/hour at most. The payload is about 1.1 kB for 12 streamers; category names are shortened first and the
run fails loudly above 4.5 kB.

## Deploying to a Raspberry Pi

```
deploy/install.sh [ssh-host]      # rsync to ~/trmnl-musthave + systemd timer (5 min); copies .env if present
ssh rpi journalctl -u 'trmnl-musthave@*' -n 20
```

## Layout

```
musthave/   config, http, weather, kick, twitch, payload, state, trmnl (push clients), run, __main__
templates/  full / half_horizontal / half_vertical / quadrant .liquid
preview/    render.py, sample.json (real payload), screenshots
deploy/     systemd service + timer, install.sh, push_plugin.sh
tests/      pytest with fixtures captured from the real APIs
docs/superpowers/  design spec and implementation plan (Czech)
```

## License

MIT
