# Changelog

All notable changes to the trmnl-musthave server. Format: Keep a Changelog, versions: SemVer.

## [Unreleased]

### Added
- Frames still shown by a device survive the FrameStore limit (`FrameStore.put(..., pinned=...)`, `DeviceRegistry.frame_ids()`), so after a long server outage the device gets a partial refresh instead of a full-screen flash. Pairs with firmware 2.0.7 outage recovery.

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
