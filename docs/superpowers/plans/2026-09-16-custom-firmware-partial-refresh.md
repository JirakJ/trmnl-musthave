# Custom TRMNL Firmware + Region Partial Refresh Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Server na RPi renderuje obrazovku, spočítá změněné obdélníky a řídí zařízení; fork firmware TRMNL OG stahuje jen změny a překresluje je částečným refreshem bez blikání, s režimem spánku podle napájení.

**Architecture:** Dva repozitáře. `trmnl-musthave` (MIT, Python stdlib + Pillow/python-liquid pro render) dostane `frames.py`, `diff.py`, `policy.py` a rozšířený `server.py`. `trmnl-musthave-firmware` (fork `usetrmnl/firmware`, GPLv3, PlatformIO/ESP32-C3) dostane nový parser odpovědi, tok none/partial/full v `bl.cpp`, `regions.cpp` s vlastní UC8179 sekvencí, light sleep. Protokol v1 je ve specu.

**Tech Stack:** Python 3.12 + pytest (server), C++17 Arduino/ESP-IDF + PlatformIO 6.2 + bb_epaper 2.1.9 + Unity (firmware), esptool přes `pio run -t upload`.

**Spec:** `docs/superpowers/specs/2026-09-16-custom-firmware-partial-refresh-design.md`

## Global Constraints

- Server část zůstává v `~/work/trmnl` (repo JirakJ/trmnl-musthave); firmware v `~/work/trmnl-firmware` (fork → JirakJ/trmnl-musthave-firmware, GPLv3, upstream remote `usetrmnl/firmware`).
- Bitmapa = 800×480, 1 bpp, řádek 100 B, bit 1 = bílá, 0 = černá (jako PNG mode "1" po `tobytes()`), řádky odshora. Stejné pořadí bytů posíláme panelu (UC8179 DTM: 1 = bílá).
- Regiony: `x` a `w` násobky 8, `1 ≤ count ≤ 4`, žádné překryvy, každý uvnitř 800×480.
- Kompatibilita: stock firmware proti novému serveru dál funguje (`action: full` nese i `image_url` + `filename`).
- Testy bez sítě a bez zařízení; hardware ověření jsou explicitní ruční kroky s fotkou.
- Commity: `feat(server): …` v trmnl-musthave, `feat(fw): …` ve firmware; git vždy s `timeout 30`.
- Rate limity trmnl.com se netýkají (cloud push zůstává volitelný, výchozí vypnuto v BYOS režimu).

---

## Část A – server (trmnl-musthave)

### Task A1: Bitmapa a FrameStore

**Files:** `musthave/frames.py`, `tests/test_frames.py`

**Interfaces:**
```python
WIDTH, HEIGHT, ROW_BYTES = 800, 480, 100
def png_to_bitmap(png: bytes) -> bytes            # PIL mode "1" → 48 000 B (1 = bílá)
def bitmap_to_png(bitmap: bytes) -> bytes         # inverzní, pro /frames/<id>.png
def frame_id(bitmap: bytes) -> str                # "musthave-" + sha256[:10]
class FrameStore:                                 # state/frames/<id>.bmp1 + index.json (posledních 8, FIFO)
    def put(self, bitmap: bytes) -> str           # vrací frame_id (idempotentní)
    def get(self, fid: str) -> bytes | None
    def latest(self) -> str | None
    def png(self, fid: str) -> bytes | None
```

- [ ] Testy: round-trip png↔bitmap na syntetickém obrázku (bílé pozadí, černý obdélník), délka 48 000, `frame_id` stabilní, `put` téhož = stejné id, FIFO drží 8, `latest`, přežije restart (nová instance nad stejným adresářem).
- [ ] Implementace, `uv run --with pytest --with pillow pytest -q`, commit `feat(server): 1-bit frame store`.

### Task A2: Diff a kódování regionů (MHR1)

**Files:** `musthave/diff.py`, `tests/test_diff.py`

**Interfaces:**
```python
TILE = 8
@dataclass(frozen=True)
class Rect: x: int; y: int; w: int; h: int        # x, w násobky 8
def dirty_tiles(old: bytes, new: bytes) -> set[tuple[int, int]]   # (tx, ty) dlaždice 8×8 s rozdílem
def merge_rects(tiles: set[tuple[int, int]], max_rects: int = 4) -> list[Rect]
    # 1) sloučit sousední dlaždice do řádkových pásů (bounding box na řádek dlaždic), 2) spojit pásy, které se
    #    vertikálně dotýkají nebo překrývají v x, 3) dokud > max_rects: spojit dvojici s nejmenším nárůstem plochy
def area(rects) -> int
def encode_regions(old: bytes, new: bytes, rects: list[Rect]) -> bytes   # "MHR1" + u8 count + per rect (u16 x,y,w,h LE) + old + new
def decode_regions(blob: bytes) -> list[tuple[Rect, bytes, bytes]]       # referenční dekodér (i pro testy firmware)
```

- [ ] Testy: identické bitmapy → prázdno; jeden černý bod (x=123,y=45) → jeden rect `x=120,w=8,y=40,h=8`; dva body v jednom řádku dlaždic → jeden pás; body ve 3 vzdálených řádcích a `max_rects=2` → sloučení s nejmenší plochou; encode/decode round-trip, old/new výřezy odpovídají zdrojovým bitmapám (bit-přesně), délka = 5 + n·(8 + 2·h·w/8); regiony uvnitř plátna a nepřekrývající se.
- [ ] Implementace, testy, commit `feat(server): dirty-rect diff and MHR1 encoding`.

### Task A3: Politika (interval, spánek, plný refresh)

**Files:** `musthave/policy.py`, `tests/test_policy.py`, `config.toml [server]` nové klíče

**Interfaces:**
```python
@dataclass
class DeviceState: frame_id: str | None = None; partials_since_full: int = 0; last_full_at: float | None = None; last_seen_at: float | None = None
@dataclass(frozen=True)
class PolicyConfig: power: str = "auto"; usb_voltage_min: float = 4.15; interval_usb: int = 60; interval_battery: int = 300;
    full_after_partials: int = 12; full_every_s: int = 3600; night_full_at: str = "04:00"; max_partial_area: float = 0.4
@dataclass(frozen=True)
class Decision: action: str; full_mode: str; sleep_mode: str; refresh_rate: int
def power_mode(voltage: float | None, cfg: PolicyConfig) -> str             # "usb" | "battery"
def decide(state: DeviceState, reported_frame: str | None, latest: str | None, rects_area: int | None,
           voltage: float | None, now: datetime, cfg: PolicyConfig) -> Decision
```
Pravidla: `latest is None` → none; `reported_frame == latest` → none; `reported_frame` neznámý serveru nebo `state.frame_id != reported_frame` → full; `rects_area is None` (frame chybí) → full; plocha > max → full; počítadlo ≥ full_after_partials nebo od `last_full_at` ≥ full_every_s nebo noc (první dotaz po `night_full_at`) → full; jinak partial. `full_mode` = fast na USB, full na baterii; `sleep_mode` light na USB, deep na baterii; `refresh_rate` podle režimu. `power="usb"|"battery"` přebíjí odhad z napětí.

- [ ] Testy pro každé pravidlo + `power_mode` (4.2 V → usb, 3.9 → battery, None → battery, override).
- [ ] Implementace, testy, commit `feat(server): refresh/sleep policy`.

### Task A4: Server endpointy v1 + stav zařízení

**Files:** `musthave/server.py`, `musthave/devices.py`, `tests/test_server.py` (rozšířit), `tests/test_devices.py`

**Interfaces:**
```python
class DeviceRegistry:  # state/devices.json, klíč = ID hlavička (MAC)
    def get(self, mac) -> DeviceState; def save(self, mac, state) -> None
def make_server(frames: FrameStore, devices: DeviceRegistry, cfg: PolicyConfig, port=8080, firmware_dir: Path | None = None) -> ThreadingHTTPServer
```
`GET /api/display`: hlavičky `ID`, `X-Frame-Id`, `Battery-Voltage`, `FW-Version` → `decide` → JSON dle specu; pro `partial` spočítat rects (cache podle dvojice (from, to)); pro `full` přidat i `image_url`/`filename` (stock kompatibilita). Po odpovědi uložit `state` (partials_since_full++, last_full_at, frame_id = cílový snímek se ukládá až když zařízení příště hlásí `X-Frame-Id`; do té doby zůstává staré). `GET /frames/<id>.png`, `GET /frames/<id>.regions?from=<old>` (404 když neznámé), `GET /firmware/latest.bin` + `firmware_version.txt` → `update_firmware` když `FW-Version` ≠ obsah souboru. `/api/setup`, `/api/log`, `/`, `/screens/<name>` (legacy alias na latest PNG) beze změny.

- [ ] Testy: none/partial/full sekvence přes fake FrameStore se dvěma snímky; regions endpoint vrací MHR1 dekódovatelný `decode_regions`; stock-kompatibilní pole; neznámý from → 404; OTA pole podle verze; stav zařízení persistuje.
- [ ] Implementace, testy, commit `feat(server): protocol v1 endpoints, device registry, OTA serving`.

### Task A5: Smyčka serve → FrameStore, konfigurace, README

**Files:** `musthave/serve.py`, `musthave/config.py`, `config.toml`, `README.md`, `README.cs.md`, `tests/test_serve.py`

- [ ] `tick()` ukládá render do `FrameStore.put(png_to_bitmap(png))`; `ScreenStore` zůstává jen jako legacy alias (`/screens/`) → nahradit voláním `frames.png(latest)`. `[server]` klíče z A3. README: sekce „Protokol v1" + odkaz na firmware repo.
- [ ] Testy upravit (tick → frames.latest()), commit `feat(server): serve loop feeds frame store`.

## Část B – firmware (trmnl-musthave-firmware)

### Task B1: Fork, env, verze, nativní testy

**Files:** `platformio.ini` (`[env:trmnl_musthave] extends = env:trmnl`, `-D MUSTHAVE_FW`), `include/config.h` (verze 2.0.0, `API_BASE_URL` default prázdný = jen captive portál), `README.md` (GPLv3, co je jinak), `LICENSE` beze změny.

- [ ] `gh repo fork usetrmnl/firmware --fork-name trmnl-musthave-firmware --clone=false`, v `~/work/trmnl-firmware` přidat remote `origin` = fork, `upstream` = usetrmnl; větev `musthave`.
- [ ] `pio run -e trmnl_musthave` zelený; `pio test -e native` (upstream testy) zelený; commit `feat(fw): musthave env and version`.

### Task B2: Parser odpovědi v1

**Files:** `lib/trmnl/include/api_types.h` (`ApiDisplayResponse` + `action`, `frame_id`, `regions_url`, `full_url`, `full_mode`, `sleep_mode`), `lib/trmnl/src/parse_response_api_display.cpp`, `test/test_parse_display_v1/test_main.cpp` (native, Unity)

- [ ] Testy: JSON s action=partial naplní pole; chybějící nová pole → `action=full`, `full_url = image_url` (stock kompatibilita); neplatný `sleep_mode` → deep.
- [ ] Implementace, `pio test -e native`, commit `feat(fw): parse protocol v1 fields`.

### Task B3: Tok none/partial/full + X-Frame-Id + NVS

**Files:** `src/bl.cpp`, `lib/trmnl/src/api-client/request_headers.cpp` (+ `X-Frame-Id`), `include/preferences_persistence.h` (`PREFERENCES_FRAME_ID`)

- [ ] `none` → jen spánek podle `refresh_rate`/`sleep_mode`; `full` → stávající download+decode cesta z `full_url`, po úspěchu `preferences.putString(FRAME_ID, frame_id)`; `partial` → B4; chyba partial → okamžitý pokus `full`. Odstranit special functions cloudu (ponechat SF_NONE), playlist klíče.
- [ ] Build zelený; hardware test 1 (stock parita): zařízení proti serveru s `power="battery"`, fotka po `full`; log ukazuje `X-Frame-Id` v dalším dotazu → server odpoví `none`. Commit `feat(fw): v1 display flow with frame id`.

### Task B4: Regiony (spike + implementace)

**Files:** `src/regions.cpp`, `include/regions.h`, `test/test_regions/test_main.cpp` (native: dekodér MHR1 nad bytes z Python `encode_regions`)

**Interfaces:**
```cpp
struct Region { uint16_t x, y, w, h; const uint8_t *oldPlane; const uint8_t *newPlane; };
bool regions_parse(const uint8_t *blob, size_t len, Region *out, uint8_t maxCount, uint8_t *count); // validace rozsahů, násobků 8
bool regions_draw(BBEPAPER &bbep, const Region *r, uint8_t count);   // per region: PTIN, PTL(mód 0), DTM1 old, DTM2 new, partial LUT, DRF, wait, PTOU
```
- [ ] Spike na zařízení (před plnou implementací): natvrdo jeden region s old=aktuální obsah z předchozího full snímku (server), new=změněný; ověřit bez bliknutí a bez duchů po deep sleep; změřit čas. Výsledek zapsat do specu.
- [ ] Nativní test dekodéru (fixture z Pythonu uložená v `test/fixtures/regions_two.bin`), implementace, build, hardware test 2 (jeden řádek diváků) + fotka, test 3 (12 partial → server pošle full). Commit `feat(fw): region partial refresh`.

### Task B5: Napájení – light sleep na USB

**Files:** `src/bl.cpp` (`goToSleep`), `src/power.cpp`

- [ ] `sleep_mode=light`: neposílat `display_sleep()` (panel zůstane napájený, jen `bbep.sleep(LIGHT)` pokud knihovna podporuje), `WiFi.disconnect(true)`, `esp_sleep_enable_timer_wakeup`, `esp_light_sleep_start`, po probuzení pokračovat ve smyčce bez rebootu (stav v RAM: `bCanDoPartial`, počítadla). Tlačítko: 5 s = portál i v light sleepu (GPIO wake). `deep` = stock chování.
- [ ] Hardware test 4: 24 h na USB s intervalem 60 s, log bez pádu, fotky ráno; test 5: baterie, deep sleep, 300 s. Commit `feat(fw): server-driven light/deep sleep`.

### Task B6: OTA z RPi + provoz

**Files:** `src/services/firmware_update.*` (HTTP bez TLS pro LAN, když URL začíná `http://`), server `deploy/firmware/` + `deploy/install.sh` (kopíruje `.pio/build/trmnl_musthave/firmware.bin` → `deploy/firmware/latest.bin` + `firmware_version.txt`), `deploy/trmnl-musthave-server.service`

- [ ] Server: `GET /firmware/latest.bin`; firmware: OTA přes HTTP z LAN (stock používá HTTPS klient; přidat větev pro `http://`). Hardware test 6: OTA 2.0.0 → 2.0.1 z RPi/Macu.
- [ ] Nasazení na RPi (chromium + venv + systemd), README obou repozitářů, memory. Commit `feat(fw): OTA over LAN`, `chore(server): rpi deployment for BYOS v1`.

## Pořadí a závislosti

A1 → A2 → A3 → A4 → A5 (vše bez hardware) ; B1 → B2 → B3 (hardware test 1) → B4 (spike + hardware) → B5 → B6.
A4 je podmínkou B3 (server musí umět v1). Flash vyžaduje zařízení na USB-C u Macu (boot tlačítko při prvním flashi).
