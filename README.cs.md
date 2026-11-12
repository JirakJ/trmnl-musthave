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
privátního pluginu). Profily jsou pole řetězců, přidání/odebrání = editace souboru a znovu nasadit. Hodnoty specifické
pro stroj (`[server] port`, `label`, `headless`) patří do negitovaného `config.local.toml` vedle něj; přepisuje stejné
tabulky a `deploy/install.sh` ho nikdy nepřepíše.

Volitelné `[[countdown]]` položky (`name` + `date`) vykreslí spodní lištu s odpočtem dnů (kalendářní dny v nastavené
časové zóně, řazeno od nejbližší). V den vydání se ukáže `DNES`, minulé události zmizí samy – kromě položek
s `yearly = true` (narozeniny, Vánoce), které se posunou na stejný den dalšího roku (29. 2. → 28. 2.); položky
z `config.local.toml` se k základnímu seznamu přidávají:

```toml
[[countdown]]
name = "GTA VI"
date = 2026-11-19

[[countdown]]
name = "Vánoce"
date = 2026-12-24
yearly = true
```

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
nebo jako heartbeat po 15 min. Server plugin přerenderuje nejdřív po 15 min (`refresh_interval: 15` v settings.yml, v minutách,
je minimum bez TRMNL+; 5 a 10 zaokrouhlí nahoru), proto je i refresh zařízení nastaven na 15 min. Payload má ~1,1 kB při 12 profilech;
nad 4,5 kB se zkrátí názvy kategorií, pak běh selže s chybou.

## BYOS režim: aktualizace po 5 minutách bez TRMNL cloudu

TRMNL bez TRMNL+ přerenderuje privátní plugin nejdřív po 15 minutách. Pro aktualizaci každých 5 minut slouží
vestavěný BYOS server: renderuje stejnou Liquid šablonu lokálně (headless Chrome → 1-bit PNG) a obsluhuje tři
endpointy, které firmware volá (`/api/setup`, `/api/display`, `/api/log`). Když stažení dat nebo render selže,
zůstane poslední obrázek a zařízení nic nepřekreslí (`filename` se nezmění). Server dál posílá data i do TRMNL
cloudu, takže návrat je jen soft reset zařízení.

Vedle data je ikona baterie s výplní podle procent nabití. Procenta se odhadují z posledního hlášeného
`Battery-Voltage` podle [výpočtu TRMNL OG](https://help.trmnl.com/en/articles/10556850-device-battery-faq)
a aktualizují se při dalším renderu. Bez platného měření se ukáže `— %`.

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

## Protokol v1 (vlastní firmware)

S forkem [trmnl-musthave-firmware](https://github.com/JirakJ/trmnl-musthave-firmware) zařízení posílá `X-Frame-Id`
(snímek, který právě ukazuje) a server odpoví `action: none | partial | full`. Pro `partial` servíruje
`/frames/<id>.regions?from=<old>`: malý binární blob (`MHR1`) s nejvýše 4 změněnými obdélníky, každý se starými
i novými 1-bit pixely, takže panel překreslí jen tato okna bez bliknutí. Server také určuje `sleep_mode` (light na
USB, deep na baterii, podle hlášeného napětí), `refresh_rate`, kdy udělat plný refresh proti duchům
(`full_after_partials`, `full_every_minutes`, `night_full_at`) a nabízí OTA z `deploy/firmware/latest.bin`, když se
`firmware_version.txt` liší od verze zařízení. Stock firmware proti stejnému serveru dál funguje (jde vždy cestou `full`).
Detaily: `docs/superpowers/specs/2026-09-16-custom-firmware-partial-refresh-design.md`.

## Hodinová předpověď (příštích 6 hodin)

Pod aktuálním počasím full layout ukazuje šest hodinových oken od aktuální hodiny: teplotu, pravděpodobnost srážek
(sloupec + %) a očekávaný úhrn, plus krátké shrnutí, např. `Nejvyšší šance srážek 16–18 h (90 %) · 17,0 mm` nebo
`Bez srážek do 15 h`. Data jsou ze stejného dotazu na Open-Meteo
(`hourly=temperature_2m,precipitation_probability,precipitation`, `timeformat=unixtime`):

- Open-Meteo hlásí srážky za *předchozí* hodinu, okno `h:00–h+1:00` proto bere teplotu z `h:00` a srážky z `h+1:00`.
  Shrnutí jmenuje jen celé hodiny (žádná minutová přesnost).
- Časy jsou unix timestamp převedený do nastavené časové zóny – okna správně přecházejí přes půlnoc i změnu času
  (v říjnu dvakrát `02h`, v březnu žádná).
- Chybějící hodnoty se ukazují jako `—`, nikdy jako nula, a shrnutí přizná `část dat chybí`.
- Když počasí jede ze zálohy (do 3 h), okna, která už skončila, se vynechají a obrazovka ukáže `data z HH:MM`.
- Payload pro TRMNL cloud (limit 5 kB) upravuje `fit_for_cloud()`: když by se nevešel, hodinová data se obětují
  dřív než cokoli podstatného; lokální BYOS render limit nemá.

Náhledy: `preview/scenarios/hourly-{dry,heavy,missing,midnight}.json`, render přes `preview/shot.py`.

## Panely a profily (full layout)

Bez tabulky `[layout]` zůstává full layout jako dřív (počasí s hodinovou předpovědí, sloupce Kick a Twitch, odpočet).
S ní se volí panely, jejich pořadí a šířka a časové profily:

```toml
[layout]
panels = ["kick", "twitch"]   # 1–3 sloupce: kick, twitch, streams (společný seznam), agenda, reminders
widths = { twitch = 2 }       # váha šířky 1–3 (výchozí 1)
weather = "full"              # full (s hodinovou předpovědí) | compact (bez ní, menší) | off
countdown = true
hide_empty = ["kick"]         # skrýt, když je prázdný (nikdo live / žádné položky) – místo dostanou ostatní
shrink_empty = []             # nebo zmenšit na úzký sloupec jen s hlavičkou
min_switch_minutes = 30       # změna profilu se převezme nejvýš takhle často (žádný carousel)

[[layout.profile]]
name = "pracovní den"
days = ["po", "út", "st", "čt", "pá"]   # po út st čt pá so ne (nebo mon … sun)
from = "07:00"
to = "17:00"                            # může přes půlnoc; den = den, kdy okno začíná
panels = ["agenda", "reminders"]
weather = "compact"
```

Pravidla: vyhrává první vyhovující profil v pořadí souboru (překryvy jsou povolené), jinak platí základní `[layout]`.
Časy jsou místní v `[location] timezone`. Okno `22:00–02:00` s `days = ["pá"]` platí i v sobotu v 01:30. Změna profilu
se převezme nejdřív `min_switch_minutes` po předchozí a protože stejná data dávají stejný snímek (`action: none`),
zařízení překresluje jen při skutečné změně obsahu. Nedostupný zdroj se nikdy nepovažuje za „prázdný“ a neskrývá se;
kdyby se měly skrýt všechny panely, první zůstane. Neznámý panel, duplicita, víc než tři sloupce, `streams`
s `kick`/`twitch`, chybný čas či den skončí srozumitelnou chybou konfigurace (server ponechá předchozí snímek).
Panely mají pevné limity obsahu: řádky, které se celé nevejdou, se přesunou do skrytého sloupce, takže na e-inku
nikdy není useknutý řádek.

Šablony half/quadrant mají dál pevný obsah (počasí + Kick/Twitch); `[layout]` platí pro full layout. Náhledy:
`preview/make_scenarios.py` vygeneruje `preview/scenarios/*.json` (běžný den, všechny streamy offline, dlouhé texty,
varianty compact/shrink, …) a `preview/shot.py` je vyrenderuje cestou zařízení.

## Agenda z ICS kalendářů

Panel `agenda` (přidej ho do `[layout] panels`) ukazuje nejbližší události dneška a zítřka z jednoho či více ICS
zdrojů (jen čtení): čas, název a volitelně místo; probíhající událost má `TEĎ`, celodenní `celý den`, prázdný den
`Dnes bez událostí`, další události `+N dalších`.

Adresy kalendářů obvykle obsahují tajný token, proto se **nikdy** nečtou z veřejného `config.toml` (skončí chybou):
patří do negitovaného `config.local.toml` nebo do prostředí / `.env`:

```toml
# config.local.toml
[agenda]
sources = ["https://calendar.google.com/calendar/ical/…/private-…/basic.ics", "/home/pi/export.ics"]
```

```
MUSTHAVE_ICS_URLS="webcal://… https://…"   # oddělené mezerou, spojí se s config.local.toml
```

Ostatní klíče `[agenda]` mohou být v `config.toml`: `days` (2 = dnes + zítra), `max_items` (5), `private` (true =
názvy jako `Obsazeno`, bez míst), `show_location`, `refresh_minutes` (15), `max_age_hours` (12). `webcal://` se
změní na `https://`, přihlašovací údaje v URL (`https://user:heslo@…`) se pošlou jako HTTP Basic. Adresy se nikdy
nedostanou do payloadu, stavového souboru ani logů (zdroj se označuje pořadím a otiskem; chyba se loguje jako
`agenda source #1 failed: HTTP 404`).

Podporovaný rozsah iCalendar (RFC 5545, parser ze standardní knihovny v `musthave/ics.py`): `VEVENT` s `DTSTART`/
`DTEND`/`DURATION` v UTC, s `TZID` (IANA názvy i běžné Windows názvy z Outlooku/Exchange; neznámá zóna = zóna
dashboardu) nebo plovoucí; celodenní `VALUE=DATE` (i vícedenní); `RRULE` s `FREQ=DAILY|WEEKLY|MONTHLY|YEARLY`,
`INTERVAL`, `COUNT`, `UNTIL`, `BYDAY` (i `1MO`/`-1FR` měsíčně), `BYMONTHDAY`, `BYMONTH`; `EXDATE`, `RDATE`,
`RECURRENCE-ID` (přesunutá či zrušená instance), `STATUS:CANCELLED`. Opakování drží místní čas začátku i přes změnu
času. Události s jinými částmi `RRULE` (`BYSETPOS`, `BYHOUR`, …) se přeskočí a započítají v logu, místo aby se
ukázaly špatně; definice `VTIMEZONE` se neinterpretují. Limity: 3 MB na zdroj, timeout 15 s, 20 000 událostí,
rozbalování opakování jen v okně a nejvýš 3 000 kroků na událost (dlouholeté denní řady se přetočí dopředu).
Řazení je stabilní: den → celodenní první → začátek → konec → pořadí kalendáře → název.

Výpadky: každý zdroj se stahuje nejvýš jednou za `refresh_minutes` (po chybě znovu za 2 min); selhávající zdroj
použije svou cache nejvýš `max_age_hours` a panel ukáže `data z HH:MM`; starší cache se za aktuální agendu
nevydává (`Agenda nedostupná`, případně `část kalendářů nedostupná`, když selže jen část zdrojů). Ověřeno na
reálném exportu z Google Kalendáře.

## Denní priority a opakované připomínky

Panel `reminders` (nadpis `DNES`, přidej ho do `[layout] panels`) ukazuje až tři priority pro dnešek a nejbližší
připomínky. Nastavují se lokálně, nepotřebují žádný účet a jsou oddělené od lišty `[[countdown]]`:

```toml
[[priority]]
text = "Dopsat nabídku"            # každý den; s date = 2026-11-12 jen ten den (zobrazí se max. 3)

[[reminder]]
name = "Vynést koš"
weekly = ["út", "pá"]               # po út st čt pá so ne

[[reminder]]
name = "Zaplatit nájem"
monthly = 31                        # den v měsíci
short_month = "last"                # 29.–31. v kratším měsíci: "last" = poslední den (30. 4., 28./29. 2.), "skip" = nic
lead_days = 3                       # ukázat 3 dny předem („za 3 dny“); 0 = jen v daný den

[[reminder]]
name = "Zubař"
date = 2026-11-14                   # jednorázově

[reminders]
max_items = 5                       # nad limit „+N dalších“
```

Výskyt je vidět od `datum − lead_days` do konce svého dne a další den zmizí (nic se nepřenáší). Data jsou místní
kalendářní data v `[location] timezone`. Pořadí: datum, pak pořadí v konfiguraci; splněné výskyty jdou na konec.
Splnění se ukládá pro konkrétní výskyt (`state/done.json`, název + datum), takže příští týden je připomínka znovu
otevřená: `python3 -m musthave done "Vynést koš" [--date 2026-11-12] [--undo]` (totéž nabízí lokální webová správa).
Položky `[[reminder]]`/`[[priority]]` v `config.local.toml` se přidají k základnímu seznamu.

## Streamy: oblíbení, jen LIVE, společný seznam

```toml
[streams]
kick = ["fattypillow", "czechfather"]
twitch = ["agraelus", "rob2628"]
mode = "all"                 # all (výchozí, jako dřív) | live | live_summary (živé + řádek „offline: …“)
favorites = ["twitch:agraelus", "kick:fattypillow"]   # identita = platforma:slug/login, nikdy zobrazované jméno
max_items = 0                # na seznam, 0 = bez limitu; skryté jako „+N dalších“
combined = false             # jeden seznam přes Kick i Twitch se štítkem K/T
```

Stabilní řazení: živí oblíbení (v pořadí `favorites`) → ostatní živí podle diváků, při shodě podle platformy a id →
offline oblíbení → ostatní offline v pořadí konfigurace. Limit se uplatní po seřazení, živí mají přednost. Režim `live`
ukáže `Nikdo nevysílá`, když nikdo nevysílá; výpadek zdroje zůstává `Kick nedostupný` (nikdy „nikdo nevysílá“). Když
selže dotaz na jeden kanál, zůstane jeho poslední známý stav (≤ 30 min od skutečného získání) místo falešného přechodu
do offline; bez historie se ukáže `jméno ?`. Společný seznam zachová kategorii, diváky i čas začátku a přizná platformu
bez dat (`bez Kick`). Náhledy: `preview/scenarios/streams-*.json`.

## Výstrahy ČHMÚ

Volitelný pruh pod hlavičkou ukazuje nejzávažnější platnou nebo blížící se výstrahu ČHMÚ pro nastavenou oblast:
stupeň (`nízký` / `vysoký` / `extrémní stupeň`; vyšší stupně jsou inverzně černé, takže jsou čitelné bez barev),
jev, platnost, `+N další` při více výstrahách, zdroj a čas vydání. Jde o informativní doplněk k oficiálním výstrahám.

```toml
[alerts]
area_codes = ["6105"]   # kódy ORP z číselníku ČSÚ 65 (CISORP); Jihlava = 6105; prázdné = vypnuto
feeds = ["50"]          # 50 = meteorologické výstrahy (SIVS), 70 = hydrologické sucho
refresh_minutes = 15    # jak často kontrolovat adresář; nový XML se stahuje jen při změně souboru
max_age_hours = 6       # cache starší než tohle → „Výstrahy ČHMÚ nedostupné“
horizon_hours = 24      # zobrazit i výstrahy začínající do této doby
show_none = false       # true = ukázat i „Bez platné výstrahy“
```

Jak se zdroj používá (ověřeno 11/2026 na <https://opendata.chmi.cz/meteorology/weather/alerts/cap/> a v
`metadata/Dokumentace_CAP.pdf`): každý `alert_cap_50_DDHHMM.xml` je kompletní stav SIVS pro celé ČR; jména se
po měsíci recyklují, proto se nejnovější soubor vybírá podle času v adresáři, ověřuje podle `<sent>` a starší zpráva
nikdy nepřepíše novější cache. Oblast se páruje přes geokódy `CISORP`; počítají se jen české bloky `<info>`
s `awareness_level` a jistotou jinou než `Unlikely`, zprávy `Cancel`/ne-`Actual` výstrahy nenesou a výstrahy po
`<expires>` (příp. `eventEndingTime`) zmizí i z cache. Priorita: nejvyšší stupeň → dřívější začátek → název.
„Bez výstrahy“ (pruh skrytý nebo `Bez platné výstrahy`) se liší od „zdroj nedostupný“ (`Výstrahy ČHMÚ nedostupné`),
takže nefunkční feed nikdy nevypadá jako ujištění. Parser omezuje velikost stažení i počet bloků a odmítá DTD/ENTITY
(žádná expanze entit ani externí odkazy). Data: otevřená data ČHMÚ (na pruhu je uveden zdroj „ČHMÚ“; aktuální
licenční podmínky ověř u datové sady v Národním katalogu otevřených dat).

## Úspora baterie (noční režim, nízké nabití)

Úspora mění `refresh_rate`, který server posílá zařízení – zařízení se tedy skutečně budí méně často (samotné
vynechání renderu by nic neušetřilo). Nastavení v `[server]`:

| Klíč | Výchozí | Význam |
| --- | --- | --- |
| `saving_mode` | `auto` | `auto`, `off` (nikdy nešetřit) nebo vynucený `quiet` / `low` |
| `quiet_start`, `quiet_end` | prázdné (vypnuto) | doba klidu v místním čase, může přes půlnoc (`"23:00"`–`"06:00"`) |
| `interval_quiet` | 1800 | sekundy mezi probuzeními v době klidu |
| `quiet_on_usb` | false | doba klidu i při napájení z USB |
| `low_battery_enter` / `low_battery_exit` | 0 (vypnuto) / enter + 5 | prahy v % s hysterezí |
| `interval_low_battery` | 900 | sekundy mezi probuzeními při nízké baterii |
| `max_refresh_seconds` | 3600 | strop každého `refresh_rate` posílaného zařízení |

Pořadí pravidel: OTA čekání (20 s) → vynucený `saving_mode` → napájení (USB nešetří) → nízká baterie → doba klidu;
platí-li obojí, vyhrává delší interval. Doba klidu se počítá v nastavené časové zóně (funguje přes půlnoc i změnu
času) a poslední probuzení v okně je naplánované na jeho konec, ne o celý další interval později. Stav nízké baterie
vychází z posledního věrohodného napětí; neznámé nebo nevěrohodné měření ponechá předchozí stav (žádných falešných
0 %). Dlouhé intervaly se zarovnávají na sloty `align_minutes`. `/status` ukazuje aktivní režim, jeho důvod
a očekávaný další kontakt; full layout má u baterie krátký štítek `noční režim` / `úspora`.

## Stránka stavu (`/status`, `/status.json`)

BYOS server ukazuje čitelný přehled na `http://<host>:<port>/status` (JSON na `/status.json`):

- **snímky**: poslední *vyrenderovaný* snímek (render smyčka), snímek *nabídnutý* každému zařízení (odeslaný
  v odpovědi `partial`/`full`) a snímek *potvrzený* firmwarem. Potvrzení vzniká, až když další dotaz zařízení nese
  daný snímek v `X-Frame-Id`; odeslaná odpověď potvrzením není. Stock firmware `X-Frame-Id` neposílá, jeho snímky
  proto zůstávají nepotvrzené.
- **zařízení**: poslední kontakt, interval, který skutečně dostalo (`refresh_rate`), a očekávaný další kontakt,
  firmware, odhad baterie a Wi-Fi. Po očekávaném probuzení je zařízení `opožděné`, `neaktivní` až po další
  toleranci `[server] offline_tolerance_seconds` (výchozí 600 s), takže deep sleep nevyvolá falešný výpadek.
- **Wi-Fi**: poslední platná hlavička `RSSI` (−120…−1 dBm) a čas měření; chybějící nebo neplatná hodnota ponechá
  předchozí měření, bez měření je stav `neznámý`. Procenta = `2 × (RSSI + 100)` oříznuto na 0–100 (−100 dBm = 0 %,
  −50 dBm a lépe = 100 %).
- **stáří dat**: čas posledního *úspěšného* získání dat každého zdroje, nezávisle na čase renderu. Když zdroj jede
  ze zálohy, obrazovka ukáže `data z HH:MM` (čas získání), ne čas renderu.

Stav se sleduje jen na serveru; zamrzlý snímek na e-inku netvrdí, že je displej online/offline.

## Nasazení na Raspberry Pi

```
deploy/install.sh [ssh-host]      # rsync do ~/trmnl-musthave + systemd timer (5 min); .env se zkopíruje, pokud existuje
ssh rpi journalctl -u 'trmnl-musthave@*' -n 20
```

## Struktura

```
musthave/   config, http, weather, kick, twitch, payload, state, trmnl (odesílání), run,
            screen (render) + frames + diff + policy + devices + server + serve (BYOS v1), __main__
templates/  full / half_horizontal / half_vertical / quadrant .liquid
preview/    render.py, sample.json (reálný payload), screenshoty
deploy/     systemd jednotky + timer, install.sh, push_plugin.sh, launchd plisty, BYOS server unit
tests/      pytest, fixtures z reálných odpovědí
docs/superpowers/  spec + implementační plán
```
