# TRMNL Must-have Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Sběrač v Pythonu, který každých 5 min pošle počasí pro Jihlavu + stav Kick/Twitch profilů na TRMNL webhook, a Liquid šablona, která to vykreslí na 800×480 e-ink.

**Architecture:** Stdlib-only Python balíček `musthave` (fetch → payload → state → webhook), Liquid šablony v `templates/`, systemd timer na Raspberry Pi. Zdroje dat jsou nezávislé; výpadek jednoho nezablokuje ostatní.

**Tech Stack:** Python 3.12 (stdlib: urllib, json, tomllib, concurrent.futures), pytest přes `uv run --with pytest`, python-liquid jen pro lokální preview, TRMNL framework CSS (`https://trmnl.com/css/latest/plugins.css`).

**Spec:** `docs/superpowers/specs/2026-09-16-trmnl-musthave-design.md`

## Global Constraints

- Runtime jen stdlib, Python ≥ 3.12 (RPi má 3.12.5).
- Webhook payload < 1 900 B serializovaný `json.dumps(..., separators=(",", ":"), ensure_ascii=False)`.
- Max 10 odeslání/h: `min_interval_minutes = 6`, `heartbeat_minutes = 15`.
- Žádná tajemství v repu: UUID webhooku jen v `.env` / prostředí. `.env`, `state/` v `.gitignore`.
- Testy nesahají na síť: každý fetcher bere `http` objekt s metodami `get_json(url, headers=None)`, `get_text(url, headers=None)`, `post_json(url, body, headers=None)`.
- git vždy s `timeout 30` (jinak visí v tomto prostředí).
- Commity: `feat|test|chore(musthave): ...` + Co-Authored-By trailer ze session reminderu.

---

### Task 1: Kostra projektu, config, http klient

**Files:** `config.toml`, `.env.example`, `.gitignore`, `README.md`, `musthave/__init__.py`, `musthave/config.py`, `musthave/http.py`, `tests/test_config.py`

**Interfaces (Produces):**
```python
@dataclass(frozen=True)
class Settings:
    location_name: str; latitude: float; longitude: float; timezone: str
    kick: list[str]; twitch: list[str]
    min_interval_s: int; heartbeat_s: int
    webhook_uuid: str | None
    state_path: Path
def load_settings(root: Path, env: Mapping[str, str] | None = None) -> Settings  # čte root/config.toml + root/.env (KEY=VALUE, # komentáře) + env override
class Http:  # timeout 15 s, UA "trmnl-musthave/1.0"
    def get_json(self, url, headers=None) -> Any
    def get_text(self, url, headers=None) -> str
    def post_json(self, url, body, headers=None) -> tuple[int, str]  # (status, text), 4xx/5xx nevyhazuje
```

- [ ] Test: `load_settings` z dočasného `config.toml` + `.env` vrátí seznamy a uuid; env proměnná přebíjí `.env`; bez uuid vrací `None`.
- [ ] Implementace, `uv run --with pytest pytest -q`, commit `chore(musthave): project skeleton, config loader, http client`.

### Task 2: Počasí (Open-Meteo)

**Files:** `musthave/weather.py`, `tests/test_weather.py`, `tests/fixtures/openmeteo.json` (reálná odpověď z 2026-09-16)

**Interfaces:**
```python
WMO_LABELS: dict[int, str]  # 0 Jasno, 1 Skoro jasno, 2 Polojasno, 3 Zataženo, 45/48 Mlha, 51-57 Mrholení, 61-67 Déšť, 71-77 Sněžení, 80-82 Přeháňky, 85-86 Sněh. přeháňky, 95-99 Bouřka
def wmo_label(code: int) -> str  # neznámý kód → "—"
def fetch_weather(http, settings, now: datetime) -> dict
# → {"ok": True, "temp": 22.8, "feels": 22.2, "hum": 60, "wind": 15, "code": 3, "label": "Zataženo",
#    "days": [{"d": "St", "hi": 24, "lo": 10, "p": 100, "code": 96, "label": "Bouřka"}, ×3 (dnes + 2 další)]}
# při výjimce → {"ok": False}
```
Day labels: `["Po","Út","St","Čt","Pá","So","Ne"]` podle `date.weekday()`; první den = dnes. `wind` zaokrouhlený int, `hi/lo` int.

- [ ] Testy: mapování kódů (3, 96, 999), parse fixture → 3 dny, správné zkratky dnů, `ok False` když http vyhodí.
- [ ] Implementace, testy, commit `feat(musthave): open-meteo weather fetcher`.

### Task 3: Kick

**Files:** `musthave/kick.py`, `tests/test_kick.py`, fixtures `kick_live.json`, `kick_offline.json`

**Interfaces:**
```python
def fetch_kick(http, slugs: list[str]) -> dict
# → {"ok": True, "items": [{"n": "Astatoro", "live": True, "v": 1382, "g": "Valheim", "s": "12:17"}, {"n": "FATTYPILLOW", "live": False}]}
# jeden slug selže → položka {"n": slug, "live": False, "err": True}; všechny selžou → {"ok": False, "items": []}
```
URL `https://kick.com/api/v2/channels/{slug}`, hlavičky `User-Agent: Mozilla/5.0 (Macintosh) trmnl-musthave`, `Accept: application/json`. `n` = `user.username`; live když `livestream` není null; `v` = `livestream.viewer_count`; `g` = první `categories[].name`; `s` = HH:MM z `livestream.created_at` ("2026-09-16 12:17:11" je UTC → převést do `Europe/Prague` přes `zoneinfo`). Slugs se stahují paralelně (`ThreadPoolExecutor(max_workers=5)`), pořadí výsledku = pořadí konfigurace.

- [ ] Testy: live fixture → v/g/s (s = 14:17 Prague), offline fixture → live False, výjimka na jednom slugu → err položka, všechny výjimky → ok False.
- [ ] Implementace, testy, commit `feat(musthave): kick status fetcher`.

### Task 4: Twitch (GQL + decapi fallback)

**Files:** `musthave/twitch.py`, `tests/test_twitch.py`, fixture `twitch_gql.json`

**Interfaces:**
```python
DEFAULT_CLIENT_ID = "<veřejné Client-ID webu twitch.tv, atribut clientId v HTML www.twitch.tv>"
GQL_URL = "https://gql.twitch.tv/gql"
def discover_client_id(http) -> str | None   # regex r'clientId="([a-z0-9]{30,})"' nad https://www.twitch.tv/
def fetch_twitch(http, logins: list[str], client_id: str) -> tuple[dict, str]
# → ({"ok": True, "items": [...stejný tvar jako kick...]}, použité_client_id)
```
Postup: POST GQL `[{"query": "query($logins:[String!]){users(logins:$logins){login displayName stream{title viewersCount createdAt game{displayName}}}}", "variables": {"logins": [...]}}]` s hlavičkami `Client-Id`, `Content-Type: application/json`. Status 400 → `discover_client_id`, jeden retry. `users[i]` null → `{"n": login, "live": False}`. Jiné selhání → fallback decapi: pro každý login `GET https://decapi.me/twitch/viewercount/{login}`; text končí na " is offline" → offline, jinak int → live s `v`, bez `g`/`s`. Vše selže → `ok False`. `s` = HH:MM z `createdAt` (ISO UTC) v Europe/Prague.

- [ ] Testy: fixture → 5 live + 2 offline, řazení dle vstupu; 400 → discovery + retry vrací nové client_id; GQL výjimka → decapi fallback; vše selže → ok False.
- [ ] Implementace, testy, commit `feat(musthave): twitch status fetcher with GQL and decapi fallback`.

### Task 5: Payload

**Files:** `musthave/payload.py`, `tests/test_payload.py`

**Interfaces:**
```python
MAX_BYTES = 1900
GAME_MAX = 22
def build_payload(weather: dict, kick: dict, twitch: dict, now: datetime) -> dict
def encode(payload: dict) -> bytes   # json compact, ensure_ascii=False, utf-8
def sort_items(items: list[dict]) -> list[dict]  # live (v desc) first, then offline v původním pořadí
class PayloadTooLarge(Exception)
```
`build_payload` seřadí položky, zkrátí `g` na 22 znaků (+ "…" když delší), doplní `updated` = `now.strftime("%H:%M")`, spočítá `kick.live`/`twitch.live` (počty), a pokud `len(encode(p)) > MAX_BYTES` zkrátí `g` na 12 a pak vyhodí `PayloadTooLarge`.

- [ ] Testy: řazení, zkrácení, `updated`, počty live, velikost < 1900 pro 5+7 profilů s dlouhými názvy, PayloadTooLarge pro 60 profilů.
- [ ] Implementace, testy, commit `feat(musthave): payload builder with size guard`.

### Task 6: State + rozhodnutí o odeslání + webhook

**Files:** `musthave/state.py`, `musthave/trmnl.py`, `tests/test_state.py`, `tests/test_trmnl.py`

**Interfaces:**
```python
@dataclass
class State: last_sent_at: float | None; last_payload: dict | None; twitch_client_id: str | None
def load_state(path) -> State; def save_state(path, state) -> None   # JSON, chybějící soubor → prázdný State
def should_send(state, payload, now_ts: float, min_interval_s, heartbeat_s) -> tuple[bool, str]
# nikdy neposláno → (True,"first"); elapsed ≥ heartbeat → (True,"heartbeat");
# elapsed ≥ min_interval and payload bez 'updated' != last bez 'updated' → (True,"changed"); jinak (False,"unchanged"|"throttled")
def send_webhook(http, uuid: str, payload: dict) -> tuple[int, str]  # POST https://trmnl.com/api/custom_plugins/{uuid}, body {"merge_variables": payload}
```

- [ ] Testy: first / heartbeat / changed / throttled / unchanged; state roundtrip; send_webhook posílá správné URL+body (fake http zachytí).
- [ ] Implementace, testy, commit `feat(musthave): send decision, state file, webhook client`.

### Task 7: CLI `python3 -m musthave`

**Files:** `musthave/__main__.py`, `musthave/run.py`, `tests/test_run.py`

**Interfaces:**
```python
def collect(http, settings, state, now) -> tuple[dict, State]  # paralelně weather/kick/twitch, vrací payload + state s případně novým client_id
def run(root: Path, http=None, now=None, dry_run=False) -> int  # exit code; loguje na stderr: "sent (changed) 1234 B" / "skip (throttled)" / "429 rate limited"
```
Příkazy: `run` (výchozí), `run --dry-run` (vypíše payload, nepošle, neuloží state), `fetch` (jen JSON na stdout), `status` (GET /api/devices přes `TRMNL_USER_API_KEY`, volitelné). Bez `TRMNL_WEBHOOK_UUID` `run` skončí kódem 2 se srozumitelnou hláškou. Po 429 se state neuloží.

- [ ] Testy: `collect` s fake http vrátí payload se všemi třemi zdroji; `run(dry_run=True)` nevolá post; `run` bez uuid → 2; 429 → state nezměněn.
- [ ] Implementace, testy, commit `feat(musthave): CLI run/fetch/status`.

### Task 8: Liquid šablony + lokální preview

**Files:** `templates/full.liquid`, `templates/half_horizontal.liquid`, `templates/half_vertical.liquid`, `templates/quadrant.liquid`, `preview/render.py`, `preview/sample.json`, `tests/test_templates.py`

`preview/render.py`: `uv run --with python-liquid preview/render.py [--layout full] [--data preview/sample.json]` → `preview/out.html` s TRMNL CSS/JS + Inter fontem, `<div class="screen"><div class="view view--full">…`. `tests/test_templates.py` (bez liquid): šablony existují, obsahují `title_bar`, odkazují jen na klíče z payloadu (regex `{{\s*(\w+)` ⊂ {weather, kick, twitch, updated, item, day, forloop}).

Design full: title_bar (`Must-have · Jihlava · aktualizováno {{updated}}`), horní pás: velká teplota (`value value--xxlarge`), label, meta řádek (pocitově, vlhkost, vítr), vpravo 3 dny grid; dolní část `columns` se dvěma `column`: hlavička `KICK · {{kick.live}} live`, řádky s `label label--inverted` "LIVE" nebo `label label--gray-out` "off". Bez emoji.

- [ ] Napsat šablony, sample.json (reálná data), render, screenshot 800×480 v Chrome, zkontrolovat čitelnost, iterovat.
- [ ] Commit `feat(musthave): liquid templates + local preview`.

### Task 9: Nasazení na RPi

**Files:** `deploy/trmnl-musthave.service`, `deploy/trmnl-musthave.timer`, `deploy/install.sh`

Service: `Type=oneshot`, `WorkingDirectory=/home/<user>/trmnl-musthave`, `ExecStart=/usr/bin/python3 -m musthave run`, `EnvironmentFile=/home/<user>/trmnl-musthave/.env`. Timer: `OnBootSec=2min`, `OnUnitActiveSec=5min`, `Persistent=true`, `WantedBy=timers.target`. install.sh: `rsync -a --delete --exclude .git --exclude state --exclude preview/out.html ./ rpi:~/trmnl-musthave/`, scp `.env` když existuje lokálně, `ssh rpi sudo cp deploy/*.service deploy/*.timer /etc/systemd/system/ && sudo systemctl daemon-reload && sudo systemctl enable --now trmnl-musthave.timer && systemctl list-timers trmnl-musthave.timer`.

- [ ] Vytvořit privátní plugin v TRMNL UI (strategy Webhook, název „Must-have"), vložit markup, získat UUID → `.env`.
- [ ] `python3 -m musthave run` lokálně, ověřit render přes `GET /api/display` / UI preview.
- [ ] `deploy/install.sh`, ověřit `journalctl -u trmnl-musthave -n 20`.
- [ ] Commit `chore(musthave): systemd deploy for raspberry pi`, README aktualizovat.
