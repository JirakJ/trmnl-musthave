# TRMNL Must-have (česky)

Jedna obrazovka pro [TRMNL](https://trmnl.com) e-ink displej (800×480): počasí pro Jihlavu a stav sledovaných
Kick a Twitch streamerů (online/offline, diváci, kategorie, čas začátku). Anglická verze: [README.md](README.md).

Sběrač je čistý Python 3.12 (jen stdlib), běží periodicky (Raspberry Pi, systemd timer) a posílá data do
privátního TRMNL pluginu. Vykreslení dělá TRMNL z Liquid šablon v `templates/`.

## Zdroje dat (bez API klíčů)

| Zdroj | Jak |
| --- | --- |
| Počasí | [Open-Meteo](https://open-meteo.com), souřadnice z `config.toml` |
| Kick | neoficiální `kick.com/api/v2/channels/<slug>` |
| Twitch | veřejný GraphQL endpoint webu twitch.tv (Client-ID z HTML stránky, při změně se obnoví sám), fallback [decapi.me](https://decapi.me) |

Žádná vývojářská aplikace Twitch ani Kick není potřeba.

## Konfigurace

`config.toml` – město, souřadnice, seznamy profilů, kadence a `[trmnl] plugin_setting_id` (číselné id instance
privátního pluginu). Profily jsou pole řetězců, přidání/odebrání = editace souboru a znovu nasadit.

Tajemství nejsou v repu. Sběrač je čte v tomto pořadí z prostředí, z negitovaného `.env` v kořenu projektu, nebo
(macOS) z Keychain:

| Proměnná | K čemu |
| --- | --- |
| `TRMNL_USER_API_KEY` | uživatelský API klíč (trmnl.com → Account). Spolu s `plugin_setting_id` posílá data přes autentizované API a slouží příkazu `status`. |
| `TRMNL_WEBHOOK_UUID` | alternativa: UUID z Webhook URL pluginu. Když je nastavené, má přednost a klíč není potřeba. |

Keychain: `security add-generic-password -s trmnl-musthave -a TRMNL_USER_API_KEY -w <klíč>`.

## Založení privátního pluginu

Jde to celé přes API s uživatelským klíčem (nebo v TRMNL UI):

```bash
# 1. založit instanci (plugin_id 37 = Private Plugin), zapamatovat vrácené id
curl -X POST https://trmnl.com/api/plugin_settings -H "Authorization: Bearer $TRMNL_USER_API_KEY" \
  -H "Content-Type: application/json" -d '{"plugin_id":37,"name":"Must-have"}'
# 2. nahrát strategii webhook + šablony jako zip archiv
deploy/push_plugin.sh <id>
# 3. id zapsat do config.toml → [trmnl] plugin_setting_id
# 4. přidat plugin do playlistu zařízení (TRMNL UI → Playlists; API vyžaduje UUID, které jde jen z UI) a spustit
python3 -m musthave run
```

## Příkazy

```
python3 -m musthave run            # stáhnout + poslat (respektuje limity)
python3 -m musthave run --dry-run  # vypsat payload, nic neposílat
python3 -m musthave fetch          # jen JSON
python3 -m musthave status         # zařízení na účtu (potřebuje TRMNL_USER_API_KEY)
uv run --with pytest pytest -q     # testy (bez sítě)
uv run --with python-liquid preview/render.py --layout full   # lokální náhled → preview/out.html
```

## Limity TRMNL

12 odeslání/h (30 s TRMNL+) a 5 kB payload. Sběrač běží každých 5 min a pošle, když se data změnila (max 12/h),
nebo jako heartbeat po 15 min. Server plugin přerenderuje nejdřív po 6 min (`refresh_interval: 360` v settings.yml
je minimum bez TRMNL+) a zařízení si nový obrázek bere ve svém cyklu (nastaveno na 5 min). Payload má ~1,1 kB při 12 profilech;
nad 4,5 kB se zkrátí názvy kategorií, pak běh selže s chybou.

## BYOS režim: aktualizace po 5 minutách bez TRMNL cloudu

TRMNL bez TRMNL+ přerenderuje privátní plugin nejdřív po 15 minutách. Pro aktualizaci každých 5 minut slouží
vestavěný BYOS server: renderuje stejnou Liquid šablonu lokálně (headless Chrome → 1-bit PNG) a obsluhuje tři
endpointy, které firmware volá (`/api/setup`, `/api/display`, `/api/log`). Když stažení dat nebo render selže,
zůstane poslední obrázek a zařízení nic nepřekreslí (`filename` se nezmění). Server dál posílá data i do TRMNL
cloudu, takže návrat je jen soft reset zařízení.

```bash
uv venv .venv && uv pip install -p .venv/bin/python -r requirements-server.txt   # python-liquid + Pillow
.venv/bin/python -m musthave serve --once     # zkušební render → state/screen/current.png
deploy/install_server_launchd.sh              # macOS: launchd agent s KeepAlive (nahradí agenta sběrače)
```

Přepnutí zařízení: podržet tlačítko vzadu 5 s, připojit se k Wi-Fi `TRMNL`, v captive portálu **Advanced →
Custom Server → Yes**, zadat `http://<IP-v-LAN>:8080` (bez lomítka na konci), pak domácí Wi-Fi a Connect. Hostu
dejte pevnou IP (DHCP rezervace). Návrat na trmnl.com: párovací režim → Advanced → Soft Reset a pole serveru
vymazat. Sekce `[server]` v `config.toml` nastavuje port, interval, formát (`png`/`bmp`) a cestu k Chrome.
Na Raspberry Pi slouží `deploy/trmnl-musthave-server.service` (`apt install chromium`).

## Nasazení na Raspberry Pi

```
deploy/install.sh [ssh-host]      # rsync do ~/trmnl-musthave + systemd timer (5 min); .env se zkopíruje, pokud existuje
ssh rpi journalctl -u 'trmnl-musthave@*' -n 20
```

## Struktura

```
musthave/   config, http, weather, kick, twitch, payload, state, trmnl (odesílání), run, screen + server + serve (BYOS), __main__
templates/  full / half_horizontal / half_vertical / quadrant .liquid
preview/    render.py, sample.json (reálný payload), screenshoty
deploy/     systemd jednotky + timer, install.sh, push_plugin.sh, launchd plisty, BYOS server unit
tests/      pytest, fixtures z reálných odpovědí
docs/superpowers/  spec + implementační plán
```
