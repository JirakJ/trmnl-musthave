# Vlastní firmware TRMNL OG s regionálním částečným refreshem, krmený z Raspberry Pi – návrh

Datum: 2026-09-16 · Navazuje na `2026-09-16-trmnl-musthave-design.md` (sběrač + Liquid šablony + BYOS server)

## Cíl

Zařízení TRMNL OG (ESP32-C3, 7,5" e-ink 800×480, 1-bit) má:

1. brát obrazovku z našeho serveru na Raspberry Pi (žádná závislost na trmnl.com),
2. překreslovat **jen obdélníky, kde se změnil obsah**, částečným refreshem bez celoobrazovkového bliknutí,
3. aktualizovat se rychle (na USB řádově každou minutu, na baterii každých 5 minut),
4. zachovat pohodlí stock firmware: Wi-Fi captive portál, OTA, měření baterie, odesílání logů.

## Proč vlastní firmware (ověřeno 2026-09-16)

- Stock firmware 1.8.16 umí `REFRESH_PARTIAL`, ale jen dokud je panel napájený (light sleep). TRMNL OG jde mezi
  aktualizacemi do **deep sleep** (`esp_deep_sleep_start`, `display_sleep()`), obsah RAM řadiče panelu zmizí, proměnná
  `bCanDoPartial` se vynuluje → po každém probuzení plný nebo „fast" refresh = bliknutí.
- Protokol `/api/display` je celoobrazovkový (`image_url` + `filename`), nemá pojem oblasti.
- Server trmnl.com renderuje privátní plugin nejdřív po 15 min bez TRMNL+.

## Rozhodnutí (odsouhlaseno 2026-09-16)

| Otázka | Rozhodnutí |
| --- | --- |
| Základ firmware | **Fork `usetrmnl/firmware`** (GPLv3 → fork je veřejný repo pod GPLv3, oddělený od `trmnl-musthave` (MIT)). Zachovat: captive portál (`lib/wificaptive`), sleep/wake, baterie, OTA, logy. Nahradit: API klient, zpracování odpovědi, kreslení. |
| Napájení | **Obojí.** Režim spánku určuje server (`sleep_mode` v odpovědi) z hlášeného napětí baterie + konfigurace (`power = auto|usb|battery`). USB → light sleep, panel napájený, kadence ~60 s. Baterie → deep sleep, kadence 300 s. |
| Politika refreshů | **Server řídí vše**: interval, seznam obdélníků, kdy plný refresh (proti duchům), kdy nic. Firmware je vykonavatel. |
| Server | Rozšíření existujícího BYOS serveru v `trmnl-musthave` (Python, RPi). |
| Panel/knihovna | `bb_epaper` 2.1.9 (jako stock), panel `EP75_800x480` (UC8179). Částečné okno přes `setAddrWindow` + zápis starého a nového plánu + `REFRESH_PARTIAL`; záložně přímé příkazy UC8179 (PTL 0x90, PTIN 0x91, DTM1 0x10, DTM2 0x13, DRF 0x12, PTOUT 0x92). |
| Build/flash | PlatformIO env `trmnl_musthave` (extends `trmnl`, ESP32-C3). První flash přes USB-C (boot tlačítko), dál OTA z RPi. Návrat = flash stock `.bin` z releasů upstreamu. |

## Hardware (z `include/config.h`, `platformio.ini`)

ESP32-C3 @ 80 MHz, 400 kB RAM, 4 MB flash (partition `min_spiffs`), panel EPD_75 přes SPI (DC 8, RST 6, BUSY 10, CS 5,
MOSI 4, SCK 21, ale řídí se `dpList["og"]`), tlačítko + baterie ADC pin 3, Wi-Fi 2,4 GHz. Framebuffer 1-bit 48 000 B.
Cyklus stock: probuzení → Wi-Fi (~3 s) → API → stažení → kreslení → spánek, celkem ~10 s, 0,19 mAh.

## Architektura

```
┌─ Raspberry Pi (trmnl-musthave) ────────────────────────────┐   ┌─ TRMNL OG (fork firmware) ──────────┐
│ collector (data) ─► render (Liquid→Chrome→1-bit) ─► FrameStore │   │ wake ─► Wi-Fi ─► GET /api/display    │
│ FrameStore: frame_id → bitmap (800×480/8 B) + PNG, posl. 8 │◄──┤   hlavičky: ID, Access-Token,        │
│ DiffEngine: bitmap(old) vs bitmap(new) → dirty 8×8 → rects │   │   Battery-Voltage, RSSI, FW-Version, │
│ Policy: none | partial(rects) | full ; sleep_mode, seconds │──►│   X-Frame-Id (co je právě na panelu)  │
│ HTTP: /api/setup /api/display /api/log                     │   │ action none → spánek                 │
│       /frames/<id>.png  /frames/<id>.regions?from=<old>    │   │ action full → PNG → REFRESH_FULL/FAST│
│       /firmware/latest.bin (OTA)                            │   │ action partial → regions blob →      │
└────────────────────────────────────────────────────────────┘   │   per rect: window+old+new+PARTIAL   │
                                                                 │ ulož frame_id do NVS → spánek        │
                                                                 └──────────────────────────────────────┘
```

### Protokol v1 (server ↔ zařízení)

`GET /api/display` – hlavičky jako stock + `X-Frame-Id: <id nebo prázdné>`.

Odpověď (JSON):

```json
{"status": 0,
 "action": "partial",                       // none | partial | full
 "frame_id": "musthave-1a2b3c4d5e",          // cílový snímek
 "full_url": "http://rpi:8080/frames/musthave-1a2b3c4d5e.png",
 "regions_url": "http://rpi:8080/frames/musthave-1a2b3c4d5e.regions?from=musthave-0f9e8d7c6b",
 "full_mode": "full",                        // full | fast (jen pro action=full)
 "refresh_rate": 60,                         // sekund do dalšího dotazu
 "sleep_mode": "light",                      // light | deep
 "update_firmware": false, "firmware_url": null, "reset_firmware": false,
 "special_function": "none"}
```

Regions blob (binární, `application/octet-stream`):

```
magic "MHR1" | u8 count | count × { u16 x, u16 y, u16 w, u16 h, old[h·w/8], new[h·w/8] }   (x a w násobky 8, little-endian)
```

- Server sloučí špinavé dlaždice 8×8 do max 4 obdélníků (bounding boxy řádkových pásů). Když plocha > 40 % nebo
  server nezná `X-Frame-Id` (restart, neznámý snímek), pošle `action: full`.
- Zařízení po úspěšném vykreslení uloží `frame_id` do NVS; při chybě ho neuloží → příště dostane `full`.
- Politika proti duchům (server, per zařízení): plný refresh po 12 částečných, nejméně 1× za hodinu, a v 04:00.
  Na USB `full_mode: fast` (rychlejší, méně bliká), na baterii `full`.
- `/api/setup`, `/api/log` beze změny. OTA: `update_firmware: true` + `firmware_url`, když server má novější verzi
  v `deploy/firmware/` (verze z hlavičky `FW-Version`).

### Firmware (fork) – moduly

| Modul | Změna |
| --- | --- |
| `src/api-client/display.cpp`, `lib/trmnl/src/parse_response_api_display.cpp` | nový parser odpovědi (`action`, `frame_id`, `regions_url`, `sleep_mode`, `full_mode`); hlavička `X-Frame-Id` |
| `src/bl.cpp` | tok: none/partial/full; uložení `frame_id` (NVS `data/frame_id`); výběr spánku podle `sleep_mode` |
| **nový** `src/regions.cpp` | stažení a parsování MHR1, kontrola rozsahů, per obdélník: `setAddrWindow` → starý plán → nový plán → `refresh(REFRESH_PARTIAL)`; při selhání fallback na full |
| `src/display.cpp` | odstranit playlist/special screens TRMNL cloudu, ponechat PNG/BMP cestu pro `full`, chybové obrazovky, logo |
| `src/power.cpp` / `bl.cpp` | light sleep: panel zůstane napájený, Wi-Fi odpojit, `esp_light_sleep_start` s timerem; tlačítko funguje v obou režimech |
| `platformio.ini` | `[env:trmnl_musthave] extends = env:trmnl`, `-D MUSTHAVE_FW`, verze `2.0.x` |
| odstraněno | TRMNL X / gen2 / barevné panely, marketplace special functions, gas gauge (jen OG) |

Nález v `bb_epaper` 2.1.9 (`bb_ep.inl`): `bbepSetAddrWindow` pošle PTIN + PTL, ale poslední bajt PTL je natvrdo
`1` = „refresh celého panelu", a `bbepRefresh` pro UC81xx před DRF vždy pošle PTOU (partial out). Knihovna tedy okno
používá jen pro zápis dat, ne pro refresh. Oknový refresh proto bude vlastní sekvence v `regions.cpp`:
`PTIN (0x91)` → `PTL (0x90)` s x0,x1,y0,y1 a módem `0` → `DTM1 (0x10)` starý plán okna → `DTM2 (0x13)` nový plán okna
→ partial LUT init (převzatá `epd75_init_sequence_partial`, Apache-2.0) → `DRF (0x12)` → busy wait → `PTOU (0x92)`.
Zápis přes veřejné `bbep.writeCmd()/writeData()`. Spike (M3) ověří na zařízení: (a) bez duchů po deep sleep se starým
plánem ze serveru, (b) čas refreshe okna vs. celku, (c) chování EP75 „old" vs „GEN2" varianty panelu (stock používá
`EP75_800x480`).

### Server (trmnl-musthave) – moduly

| Modul | Účel |
| --- | --- |
| `musthave/frames.py` | `FrameStore`: uloží bitmapu (bytes 48 000) + PNG pod `frame_id`, drží posledních 8, persist v `state/frames/` |
| `musthave/diff.py` | `dirty_tiles(old, new)` → `merge_rects(tiles, max_rects=4)` → `encode_regions(old, new, rects)` (MHR1) |
| `musthave/policy.py` | per zařízení: `decide(device_state, frame_id_on_device, latest, now, voltage, config)` → `Decision(action, full_mode, sleep_mode, refresh_rate)` |
| `musthave/server.py` | nové odpovědi + `/frames/<id>.png`, `/frames/<id>.regions`, `/firmware/latest.bin`; stav zařízení v `state/devices.json` |
| `config.toml [server]` | `power = "auto"`, `usb_voltage_min = 4.15`, `interval_usb = 60`, `interval_battery = 300`, `full_after_partials = 12`, `full_every_minutes = 60`, `night_full_at = "04:00"`, `max_partial_area = 0.4` |

### Testování

- Server: pytest – diff (žádná změna → žádné rects; jedna změna → jeden rect zarovnaný na 8 px; rozptýlené změny →
  sloučení; velká změna → full), kódování/dekódování MHR1 (round-trip proti referenčnímu dekodéru v testu), politika
  (počítadla, hodina, noc, neznámý frame), HTTP odpovědi.
- Firmware: PlatformIO `native` testy pro parser odpovědi a MHR1 dekodér (upstream už má `test/` s Unity);
  na zařízení ruční ověření s fotkami: (a) parita se stock (full cesta), (b) partial jednoho řádku, (c) 12 partials →
  full, (d) light sleep na USB ≥ 24 h bez pádu, (e) deep sleep na baterii, (f) OTA z RPi.
- Měření: čas cyklu (log), počet bytů staženo, dohad spotřeby z napětí za 24 h.

### Milníky

1. **M0 toolchain**: `pio pkg install -e trmnl`, build stock `trmnl` env na Macu, flash stock přes USB-C = důkaz cesty zpět.
2. **M1 server**: FrameStore + DiffEngine + Policy + endpointy, pytest zelený, stock firmware proti novému serveru
   dál funguje (`action: full` kompatibilní s `image_url`/`filename`).
3. **M2 firmware full**: fork, env `trmnl_musthave`, nový parser + `X-Frame-Id`, full cesta, sleep podle serveru.
4. **M3 spike + partial**: regiony na zařízení, fallback, ghosting politika ověřená fotkami.
5. **M4 napájení**: light sleep na USB, přepínání podle serveru, tlačítko.
6. **M5 provoz**: OTA z RPi, systemd na RPi, dokumentace obou repozitářů, memory.

## Mimo rozsah

TRMNL X a barevné panely, kompatibilita s trmnl.com cloudem (ponechána jen v `trmnl-musthave` sběrači), šifrování
provozu v LAN, více zařízení na jednom serveru (struktura per-device stav to umožní, ale netestuje se).
