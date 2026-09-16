# TRMNL „Must-have" obrazovka – návrh

Datum: 2026-09-16 · Zařízení: TRMNL OG (800×480, 1-bit), friendly_id 3WKDCD, MAC E0:72:A1:34:F3:94

## Cíl

Jedna obrazovka na TRMNL, která ukazuje:

1. počasí pro Jihlavu (teď + 3 dny předpověď),
2. stav sledovaných Kick profilů (online/offline, diváci, kategorie),
3. stav sledovaných Twitch profilů (totéž).

Seznamy profilů jsou konfigurovatelné bez zásahu do kódu.

Výchozí profily:

- Kick: fattypillow, czechfather, astatoro, miken, czechcloud
- Twitch: arcadebulls, agraelus, artemis (zadáno jako „Atremis", ověřit), cruelladk, conducteir77, oliverovykecy, rob2628

## Rozhodnutí (odsouhlaseno 2026-09-16)

| Otázka | Rozhodnutí |
| --- | --- |
| Kde běží sběrač | Raspberry Pi (ssh alias `rpi`, Debian 12 arm64, Python 3.12, systemd timer) |
| Twitch | Bez vývojářské aplikace. Veřejný GraphQL endpoint webu twitch.tv (`gql.twitch.tv/gql`, Client-ID z HTML stránky) + fallback decapi.me |
| Kick | Neoficiální `kick.com/api/v2/channels/<slug>` bez klíčů |
| Počasí | Open-Meteo (bez klíče), lat 49.3961 lon 15.5912, timezone Europe/Prague |
| Layout | Jedna obrazovka „full": pás počasí nahoře, pod ním sloupce Kick / Twitch |
| Doručení do TRMNL | Privátní plugin, strategie Webhook (`POST https://trmnl.com/api/custom_plugins/<uuid>`) |
| Runtime | Python 3.12+, pouze stdlib (urllib, json, tomllib). Testy přes pytest (uv). |

## Limity TRMNL webhooku (ověřeno v dokumentaci)

- max 12 requestů za hodinu (bez TRMNL+), jinak 429
- max 2 kB payload (`merge_variables`)
- instance privátního pluginu nejde založit přes API („Manually setting a Plugin is not allowed"), založí se jednou v UI, UUID z pole Webhook URL jde do `.env`

## Architektura

```
trmnl/
  config.toml              # město, souřadnice, seznamy profilů, kadence (tracked, bez tajemství)
  .env.example             # TRMNL_WEBHOOK_UUID
  musthave/
    __init__.py
    config.py              # načtení config.toml + .env/prostředí → Settings
    http.py                # jediná funkce get_json/get_text (urllib, timeout, UA), injektovatelná pro testy
    weather.py             # Open-Meteo → {now, days[]}; WMO kód → český popisek
    kick.py                # slug → {name, live, viewers, game, since}
    twitch.py              # logins → totéž; GQL s auto-obnovou Client-ID, fallback decapi
    payload.py             # skládání merge_variables, zkracování, kontrola < 2 kB
    state.py               # state/last.json: poslední payload + čas odeslání + cache Client-ID
    trmnl.py               # POST webhook, zpracování 429
    __main__.py            # CLI: run | fetch (jen vypíše JSON) | preview
  templates/
    full.liquid            # hlavní obrazovka
    half_horizontal.liquid, half_vertical.liquid, quadrant.liquid   # pro mashupy
  preview/render.py        # lokální render Liquid → preview/out.html (uv run --with python-liquid)
  deploy/
    trmnl-musthave.service, trmnl-musthave.timer, install.sh   # rsync na rpi + systemctl
  tests/                   # pytest, fixtures JSON z reálných odpovědí
```

### Datový tok

1. systemd timer spustí `python3 -m musthave run` každých 5 minut.
2. Sběrač paralelně (ThreadPool) stáhne počasí, Kick (1 request na slug), Twitch (1 GQL request pro všechny loginy).
3. Selhání jednoho zdroje nezastaví ostatní: zdroj dostane `error: true` a v šabloně se ukáže „nedostupné", ostatní data se pošlou.
4. `payload.py` sestaví kompaktní JSON a ověří velikost (< 1 900 B, jinak zkrátí názvy kategorií, pak vyhodí výjimku).
5. `state.py` rozhodne, zda posílat:
   - payload (bez pole `updated`) se liší od posledního odeslaného **a** od posledního odeslání uplynulo ≥ 6 min, nebo
   - od posledního odeslání uplynulo ≥ 15 min (heartbeat, aby čas „aktualizováno" nebyl zastaralý).
   - Výsledek: nejvýše 10 requestů/h, bezpečně pod limitem 12.
6. `trmnl.py` pošle webhook; při 429 zaloguje a stav neuloží (další běh to zkusí znovu).

### Tvar payloadu (`merge_variables`)

```json
{
  "updated": "17:20",
  "weather": {
    "ok": true, "temp": 22.8, "feels": 22.2, "hum": 60, "wind": 15,
    "code": 3, "label": "Zataženo",
    "days": [{"d": "St", "hi": 24, "lo": 10, "p": 100, "code": 96, "label": "Bouřka"}, ...]
  },
  "kick": {"ok": true, "items": [{"n": "Astatoro", "live": true, "v": 1382, "g": "Valheim", "s": "12:17"}, {"n": "FATTYPILLOW", "live": false}]},
  "twitch": {"ok": true, "items": [...]}
}
```

Řazení v každém sloupci: live první (podle diváků sestupně), pak offline v pořadí z konfigurace. `g` zkráceno na 22 znaků. Odhad velikosti při 12 profilech: ~1,3 kB.

### Šablona `full.liquid`

- Horní pás (cca 1/3 výšky): vlevo velká teplota + popisek + pocitová/vlhkost/vítr, vpravo 3 dny (den, popisek, max/min, srážky %).
- Dolní část: dva sloupce. Hlavička „KICK" / „TWITCH" s počtem live. Řádek = jméno, badge LIVE (inverzní) nebo tečka offline, diváci, kategorie, čas začátku.
- Title bar: „Must-have · Jihlava · aktualizováno 17:20".
- Stavy chyb: „Kick nedostupný" místo seznamu, v počasí pomlčky.
- Jen TRMNL framework třídy + minimum inline CSS; žádné emoji (nespolehlivé na 1-bit).

### Konfigurace `config.toml`

```toml
[location]
name = "Jihlava"
latitude = 49.3961
longitude = 15.5912
timezone = "Europe/Prague"

[streams]
kick = ["fattypillow", "czechfather", "astatoro", "miken", "czechcloud"]
twitch = ["arcadebulls", "agraelus", "artemis", "cruelladk", "conducteir77", "oliverovykecy", "rob2628"]

[send]
min_interval_minutes = 6
heartbeat_minutes = 15
```

`.env` (negitované): `TRMNL_WEBHOOK_UUID=...`. Volitelně `TRMNL_USER_API_KEY` pro `status` příkaz (výpis zařízení). Klíče se nikdy nezapisují do repa.

### Nasazení

- `deploy/install.sh`: rsync projektu do `rpi:~/trmnl-musthave`, zkopíruje `.env`, nainstaluje systemd jednotky (`/etc/systemd/system`, sudo NOPASSWD), `systemctl enable --now trmnl-musthave.timer`.
- Timer: `OnBootSec=2min`, `OnUnitActiveSec=5min`, `Persistent=true`. Logy přes `journalctl -u trmnl-musthave`.

### Testování

- pytest s injektovaným HTTP klientem (žádná síť v testech), fixtures z reálných odpovědí zachycených 2026-09-16.
- Testy: mapování WMO kódů, parsování Kick live/offline, parsování Twitch GQL vč. `null` uživatele a fallbacku na decapi, řazení a velikost payloadu, rozhodovací logika odesílání (min interval, heartbeat, 429).
- Ruční ověření: `python3 -m musthave preview` → HTML, screenshot 800×480; po nasazení kontrola `GET /api/display` a fyzického zařízení.

## Mimo rozsah (YAGNI)

Oficiální Kick/Twitch OAuth, více měst, historie/grafy, notifikace, TRMNL+ funkce, marketplace publikace.
