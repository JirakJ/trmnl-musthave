# Changelog

All notable changes to the trmnl-musthave server. Format: Keep a Changelog, versions: SemVer.

## [Unreleased]

## [1.7.1] - 2026-11-12

### Fixed
- **deploy**: the device confirmation waited for the *newest* frame, but the server renders every minute, so the frame the device confirms is usually an older one. `deploy_server.py` now tracks the frame offered to the device after the activation (rendered by the new version) and its `X-Frame-Id`, and extends the wait to the device's scheduled wake-up after that offer (+10 min, max 2 h). Found while deploying v1.7.0 to the Raspberry Pi.

## [1.7.0] - 2026-11-12

All ten backlog issues (#8–#17). Package version in `musthave.__version__`, shown on `/status` and in `/status.json`
(the deploy script checks it).

### Added
- **status** (#8): `/status` and `/status.json` distinguish the rendered, offered and firmware-confirmed frame (confirmation = the next `X-Frame-Id`), store the last valid RSSI with its measurement time (documented % conversion), show last contact, battery estimate, firmware and the expected next contact from the interval the device actually received; `opožděné`/`neaktivní` only after `offline_tolerance_seconds`. Source freshness comes from the last successful fetch; stale sources show `data z HH:MM` on the screen.
- **battery saving** (#11): optional quiet window (`quiet_start`/`quiet_end`, across midnight and DST, wake-up at its end) and low-battery mode with hysteresis (`low_battery_enter`/`low_battery_exit`) change the `refresh_rate` sent to the device; rule order OTA → forced `saving_mode` → power → low battery → quiet; `max_refresh_seconds` cap; `/status` and a short tag next to the battery explain the active mode. Defaults keep the previous behaviour; `config.toml` enables 00:00–06:00 and 20/25 %.
- **hourly forecast** (#9): six hourly windows (temperature, precipitation probability and amount) with a rain summary under the current weather; Open-Meteo `timeformat=unixtime` so midnight and DST are correct; missing data shown as `—`; past windows dropped from stale data; the cloud payload drops hourly data first via `fit_for_cloud()` (the local render has no 5 kB limit).
- **ČHMÚ warnings** (#12): `[alerts]` strip with the most severe valid/upcoming SIVS warning for configured ORP codes (CISORP, Jihlava 6105) from the public CAP feed: newest file by listing time verified by `<sent>`, cancellations/expiry respected, level readable in 1-bit, `+N další`, source, issue time and data age; "no warning" vs "source unavailable" distinguished; size-limited parser rejecting DTD/ENTITY; cache up to `max_age_hours`.
- The header shows `[location] name` instead of a hard-coded city.
- **streams** (#15): `[streams] mode = all | live | live_summary`, `favorites` (`platform:id`), `max_items` (`+N dalších`), `combined` list with K/T badges; stable ordering (favourites, viewers, platform/id tie-break); per-channel fetch errors keep the last known state instead of a fake offline transition; stream items carry the platform id. The full layout renders stream columns from a `panels` list.
- **layout & profiles** (#13): `[layout]` panels (order, 1–3 columns, `widths`), weather `full | compact | off`, `countdown`, `hide_empty` / `shrink_empty` (free space goes to the other panels, outages are never hidden as empty) and `[[layout.profile]]` time windows by weekday (local timezone, across midnight, first match wins, `min_switch_minutes` against flapping) with validation errors; no `[layout]` = unchanged layout. Rows that do not fit are wrapped into a hidden overflow column instead of being cut. `preview/make_scenarios.py` builds all preview payloads through `build_payload`.
- **agenda** (#10): `agenda` panel from ICS feeds (stdlib RFC 5545 subset: TZID incl. Windows names, UTC/floating/all-day, RRULE DAILY/WEEKLY/MONTHLY/YEARLY with COUNT/UNTIL/BYDAY/BYMONTHDAY/BYMONTH, EXDATE/RDATE/RECURRENCE-ID/CANCELLED, DST-correct expansion with fast-forward and step limits); sources only from `config.local.toml` or `MUSTHAVE_ICS_URLS`, never logged or stored; `private` mode; size/timeout limits; per-feed cache with `data z HH:MM`, no stale agenda presented as current.
- **reminders** (#16): `reminders` panel with up to three `[[priority]]` items and `[[reminder]]` entries (one-off `date`, `weekly`, `monthly` with explicit `short_month = last | skip` for 29–31, `lead_days`), local calendar dates, stable order, item/text limits, per-occurrence completion in `state/done.json` (`python3 -m musthave done`), separate from countdowns.
- **web admin** (#14): `/admin` edits location, streams, own countdowns and base panels into `config.local.toml` (values equal to `config.toml` omitted, other sections kept, base countdowns never duplicated); token login (`python3 -m musthave admin-token`, `state/admin_token` 0600), session cookie, CSRF + same-origin checks, login rate limit; specific validation errors and full `load_settings` check, atomic write with `.bak` restore; draft preview through the device render path without storing a frame; saved → rendered (configuration hash in `render.json`) → firmware-confirmed status; mark reminders done. Secrets and calendar URLs never shown.
- **deployment** (#17): `deploy/deploy_server.py deploy|rollback <tag>` – exact commit, `git archive` + SHA-256 manifest, backup of the running code, rsync that never touches protected paths (local config, `.env`, `.venv`, `state/`, firmware binaries), dependency install only on change, restart, verification (manifest, service, render after restart, 1-bit 800×480 frame) with automatic rollback, separate reporting of activation / offered frame / `X-Frame-Id` confirmation with a wake-up-based timeout, `--dry-run`, `result.json`. `deploy/install.sh` refuses to enable the collector timer on a BYOS server host.

## [1.6.0] - 2026-10-18

### Added

- **dashboard**: show battery charge beside the date ([`7b3e37b`](https://github.com/JirakJ/trmnl-musthave/commit/7b3e37b05e0d48a9d6494ee396f11db8a677d7d1))

## [1.5.0] - 2026-09-27

### Added
- Recurring countdowns: `yearly = true` on a `[[countdown]]` entry rolls it over to the same day next year once it has passed (29 Feb falls back to 28 Feb in common years). Ships with a yearly Christmas countdown (24 Dec).

## [1.4.0] - 2026-09-18

### Fixed
- Rendering no longer depends on the network: the TRMNL framework CSS/JS is cached in `state/cache/` (fetched outside the render: at server start and then a daily conditional GET with ETag/Last-Modified in a background thread; gzip, long timeout, old copy kept when the refresh fails) and Chromium loads it from disk; the Inter font is vendored in `musthave/assets/`; the render shell carries fallback flex rules for the columns. On a slow Wi-Fi the 8-second render budget used to expire before `plugins.css` arrived and the columns collapsed into a single stack of rows.
- Kick and Twitch keep their last good state for 30 minutes when a fetch fails (shown as `starší data`, like the weather's 3-hour fallback) instead of `nedostupný` with an empty column. When every source is down (network outage) the screen is not re-rendered at all, so the device keeps the last good frame.

## [1.3.0] - 2026-09-17

### Added
- Frames still shown by a device survive the FrameStore limit (`FrameStore.put(..., pinned=...)`, `DeviceRegistry.frame_ids()`), so a device that was unreachable while the server kept rendering gets a partial refresh instead of a full-screen flash, as long as the hourly / 04:00 ghost-cleaning full is not due (pinned: the frame each device reports and the one it was last sent; only devices seen in the last 48 h; the pin snapshot and the store update happen under the registry lock). Pairs with firmware 2.0.7+ outage recovery.
- Battery voltage plausibility: readings outside 3.0–5.5 V or non-finite (an exactly halved ADC value was seen on some firmware builds) never reach the power policy (→ battery mode) and are not stored; the last plausible value is kept per device (`DeviceState.voltage`) and the warning is logged once per transition.
- Device registry writes are serialised by a process-wide lock with unique temp files (two devices waking in the same slot could leave an empty `devices.json`); `ota_wait` is decided before the policy so waiting polls no longer count phantom partial/full refreshes.

## [1.2.0] - 2026-09-17

### Added
- Countdown strip: `[[countdown]]` entries in `config.toml` (name + date) render "days to go" at the bottom of the full layout and as a line in the half layouts; past events drop out automatically, the release day shows `DNES`. Ships with GTA VI (2026-11-19) and World of Warcraft: Forever (2026-11-04).

## [1.1.0] - 2026-09-17

### Added
- Wake-ups aligned to 5-minute slots (`align_minutes`, `align_lead_seconds`): `refresh_rate` is computed to the next boundary minus a lead time.
- OTA waiting mode (`python3 -m musthave ota-mode on|off|status`): the device polls every 20 s without drawing until it reports the target firmware version.
- Header shows the serving host (`[server] label`) and today's date; the firmware version reported by the device is shown top right.
- Device registry tracks `fw_version`; only requests carrying `Access-Token` (real firmware) update it.
- Screenshot guard: unfinished Chromium renders (truncated viewport, grey/noisy bottom band) are rejected and the old headless mode is used as a fallback (Chromium 126 on Raspberry Pi). The working mode is remembered.
- Raspberry Pi deployment opens the server port for the LAN in `ufw`; the macOS agent runs under `caffeinate -i`.

### Changed
- `config.local.toml` overlay for host-specific values; `deploy/install.sh` excludes `.venv`, `config.local.toml` and firmware binaries, reads the port with tomllib and derives the LAN subnet on the target for the ufw rule.
- `date`, `host` and `fw` are part of the payload for every consumer (cloud push included); templates guard them with `{% if %}`.
- Chromium fallback also covers crashes and timeouts, decodes the screenshot once and honours `[server] headless = "auto"|"new"|"old"`.
- Device registry is updated only for requests carrying the issued API key and a device ID.
- GitHub Actions workflow runs the test suite on pushes and pull requests.

### Fixed
- Frames were re-rendered every minute because of the clock in the header; identical data now keeps the same frame id, so the device gets `none`.
- Frame names share a stable 14-character prefix so the stock firmware cache purge deletes older frames.

## [1.0.0] - 2026-09-17

### Added
- BYOS protocol v1: `X-Frame-Id`, `action` none | partial | full, frame store (last 8 bitmaps), region diff (MHR1, up to 4 rectangles with old and new planes), policy-driven `sleep_mode`, `full_mode` and ghost-cleaning full refreshes, OTA serving from `deploy/firmware/`.
- Legacy BYOS server (`/api/setup`, `/api/display`, `/api/log`, `/screens/<name>`) compatible with the stock TRMNL firmware.
- Collector for weather (Open-Meteo), Kick and Twitch live status without developer applications; Liquid templates for the four TRMNL layouts; TRMNL cloud push (webhook or authenticated data endpoint).

[Unreleased]: https://github.com/JirakJ/trmnl-musthave/compare/v1.7.1...HEAD
[1.7.1]: https://github.com/JirakJ/trmnl-musthave/compare/v1.7.0...v1.7.1
[1.7.0]: https://github.com/JirakJ/trmnl-musthave/compare/v1.6.0...v1.7.0
[1.6.0]: https://github.com/JirakJ/trmnl-musthave/compare/v1.5.0...v1.6.0
