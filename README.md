# TRMNL Must-have

Jedna obrazovka pro [TRMNL](https://trmnl.com) e-ink displej (800×480): počasí pro Jihlavu, stav sledovaných
Kick a Twitch streamerů (online/offline, diváci, kategorie, čas začátku).

Sběrač je čistý Python 3.12 (jen stdlib), běží periodicky na Raspberry Pi a posílá data na webhook
privátního TRMNL pluginu. Vykreslení dělá TRMNL z Liquid šablony v `templates/`.

## Zdroje dat (bez API klíčů)

| Zdroj | Jak |
| --- | --- |
| Počasí | [Open-Meteo](https://open-meteo.com), souřadnice z `config.toml` |
| Kick | neoficiální `kick.com/api/v2/channels/<slug>` |
| Twitch | veřejný GraphQL endpoint webu twitch.tv (Client-ID z HTML stránky, při změně se obnoví sám), fallback [decapi.me](https://decapi.me) |

## Konfigurace

`config.toml` – město, souřadnice, seznamy profilů a kadence odesílání. Profily jsou jen pole řetězců,
přidání/odebrání = editace souboru a znovu nasadit (`deploy/install.sh`).

`.env` (negitované, vytvoř ručně v kořenu projektu):

```
TRMNL_WEBHOOK_UUID=<uuid z Webhook URL privátního pluginu>
TRMNL_USER_API_KEY=<volitelné, pro `python3 -m musthave status`>
```

## Založení privátního pluginu na trmnl.com (jednou, ručně)

1. trmnl.com → Plugins → Private Plugin → **New**.
2. Name `Must-have`, Strategy **Webhook**, Save. Zobrazí se **Webhook URL**
   `https://trmnl.com/api/custom_plugins/<UUID>` – UUID zkopíruj do `.env`.
3. **Edit Markup** → do pole *Full* vlož obsah `templates/full.liquid`; volitelně
   `half_horizontal.liquid`, `half_vertical.liquid`, `quadrant.liquid` do příslušných polí (pro mashupy). Save.
4. Playlists → přidej plugin do playlistu zařízení.
5. Lokálně `python3 -m musthave run` – první běh pošle data hned, TRMNL vyrenderuje obrazovku (Force Refresh
   v nastavení pluginu urychlí náhled).

## Příkazy

```
python3 -m musthave run            # stáhnout + poslat (respektuje limity)
python3 -m musthave run --dry-run  # vypsat payload, nic neposílat
python3 -m musthave fetch          # jen JSON
python3 -m musthave status         # zařízení na účtu (potřebuje TRMNL_USER_API_KEY)
uv run --with pytest pytest -q     # testy (bez sítě)
uv run --with python-liquid preview/render.py --layout full   # lokální náhled → preview/out.html
```

## Limity TRMNL webhooku a jak je držíme

TRMNL bez TRMNL+ povoluje 12 webhooků/h a 2 kB payload. Sběrač běží každých 5 min, ale pošle jen když se
data změnila a od posledního odeslání uplynulo ≥ 6 min, nebo jako heartbeat po 15 min → max 10/h.
Payload má ~1,1 kB při 12 profilech; při překročení 1,9 kB se zkrátí názvy kategorií, pak běh selže s chybou.

## Nasazení na Raspberry Pi

```
deploy/install.sh        # rsync do rpi:~/trmnl-musthave + systemd timer (5 min), .env se zkopíruje
ssh rpi journalctl -u 'trmnl-musthave@*' -n 20   # logy
```

## Struktura

```
musthave/   config, http, weather, kick, twitch, payload, state, trmnl (webhook), run, __main__
templates/  full / half_horizontal / half_vertical / quadrant .liquid
preview/    render.py + sample.json (reálný payload)
deploy/     systemd service + timer + install.sh
tests/      pytest, fixtures z reálných odpovědí
docs/superpowers/  spec + implementační plán
```
