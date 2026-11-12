"""Výstrahy ČHMÚ (SIVS) z veřejného CAP/XML adresáře, bez API klíče.

Zdroj: https://opendata.chmi.cz/meteorology/weather/alerts/cap/ (dokumentace: ../metadata/Dokumentace_CAP.pdf).
Ověřeno 11/2026:
- Soubory `alert_cap_50_DDHHMM.xml` jsou zprávy SIVS (meteorologické výstrahy) a každá nese KOMPLETNÍ aktuální stav
  pro celé ČR (bloky <info> pro každý jev, včetně „Žádná výstraha …“). Jména se po měsíci recyklují (DDHHMM),
  proto se nejnovější soubor vybírá podle času v adresáři a ověřuje podle <sent>; starší zpráva nikdy nepřepíše
  novější. `alert_cap_70_*` jsou hydrologické zprávy o suchu (volitelně přes `feeds`).
- Území: každý <area> obsahuje ORP jako <geocode><valueName>CISORP</valueName> (číselník ČSÚ 65, např. Jihlava 6105).
- Platnost: <onset> a <expires> (příp. parametr eventEndingTime); úroveň v parametru awareness_level
  („2; yellow; Moderate“), u „žádné výstrahy“ chybí a <certainty> je Unlikely.
- msgType Alert/Update = nový stav, Cancel = zrušení všech výstrah zprávy; status jiný než Actual se ignoruje.

Bezpečnost parseru: omezená velikost stažení, zakázané DTD/ENTITY (bez externích referencí a expanze entit),
omezený počet bloků <info>/<area>. Výstrahy jsou informativní doplněk k oficiálním výstrahám ČHMÚ.
"""

from __future__ import annotations

import logging
import re
import time
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import datetime, timedelta

log = logging.getLogger(__name__)

LISTING_URL = "https://opendata.chmi.cz/meteorology/weather/alerts/cap/"
NS = "{urn:oasis:names:tc:emergency:cap:1.2}"
MAX_XML_BYTES = 8 * 1024 * 1024    # reálné zprávy mají ~1,5–2,3 MB
MAX_LISTING_BYTES = 2 * 1024 * 1024
MAX_INFOS = 400
MAX_AREAS = 4000
MAX_PARSE_S = 10.0
RETRY_S = 120                      # po chybě další pokus nejdřív za 2 min
SEVERITY = {"Minor": 1, "Moderate": 2, "Severe": 3, "Extreme": 4}
LEVEL_LABEL = {1: "informace", 2: "nízký stupeň", 3: "vysoký stupeň", 4: "extrémní stupeň"}
FORBIDDEN = re.compile(rb"<!DOCTYPE|<!ENTITY|<!ELEMENT|<!ATTLIST|SYSTEM\s+[\"']", re.I)
ROW_RE = re.compile(r'href="(alert_cap_(\d+)_\d+\.xml)">[^<]*</a>\s+(\d\d-\w{3}-\d{4} \d\d:\d\d)')


class CapError(ValueError):
    pass


@dataclass(frozen=True)
class AlertsConfig:
    enabled: bool = False
    area_codes: tuple[str, ...] = ()       # CISORP (ORP), např. ("6105",) = Jihlava
    feeds: tuple[str, ...] = ("50",)       # 50 = SIVS (meteorologie); 70 = sucho
    refresh_minutes: int = 15              # jak často kontrolovat adresář (nový soubor se stahuje jen při změně)
    max_age_hours: int = 6                 # stáří cache, po kterém se výstrahy prohlásí za nedostupné
    horizon_hours: int = 24                # zobrazit i výstrahy začínající do této doby
    show_none: bool = False                # zobrazit pruh „bez výstrah“ (jinak se pruh skryje)
    listing_url: str = LISTING_URL


def latest_file(listing_html: str, feed: str) -> tuple[str, str] | None:
    """(jméno, čas v adresáři) nejnovějšího souboru feedu podle času v adresáři (jména se recyklují)."""
    best = None
    for name, kind, stamp in ROW_RE.findall(listing_html):
        if kind != feed:
            continue
        try:
            when = datetime.strptime(stamp, "%d-%b-%Y %H:%M")
        except ValueError:
            continue
        if best is None or (when, name) > best[0]:
            best = ((when, name), name, stamp)
    return (best[1], best[2]) if best else None


def _text(el, tag: str) -> str:
    found = el.find(NS + tag)
    return (found.text or "").strip() if found is not None and found.text else ""


def _dt(text: str) -> datetime | None:
    try:
        return datetime.fromisoformat(text) if text else None
    except ValueError:
        return None


def parse_cap(raw: bytes, area_codes: tuple[str, ...]) -> dict:
    """CAP XML → {"sent", "msg_type", "alerts": [...]} jen s výstrahami pro dané ORP (CISORP).

    Odmítá DTD/entity, příliš velký vstup a nadměrný počet bloků (CapError)."""
    if len(raw) > MAX_XML_BYTES:
        raise CapError("CAP too large")
    if FORBIDDEN.search(raw):
        raise CapError("DTD/ENTITY not allowed in CAP")
    started = time.monotonic()
    try:
        root = ET.fromstring(raw)
    except ET.ParseError as err:
        raise CapError(f"invalid XML: {err}") from None
    if root.tag != NS + "alert":
        raise CapError(f"not a CAP 1.2 alert: {root.tag}")
    sent = _dt(_text(root, "sent"))
    if sent is None:
        raise CapError("CAP without valid <sent>")
    status, msg_type = _text(root, "status"), _text(root, "msgType")
    out = {"sent": sent.isoformat(), "msg_type": msg_type, "status": status, "alerts": []}
    if status != "Actual" or msg_type == "Cancel":
        return out  # cvičení/test se nezobrazují; Cancel ruší výstrahy zprávy
    infos = root.findall(NS + "info")
    if len(infos) > MAX_INFOS:
        raise CapError("too many <info> blocks")
    wanted = set(area_codes)
    areas_seen = 0
    for info in infos:
        if time.monotonic() - started > MAX_PARSE_S:
            raise CapError("CAP parsing took too long")
        if _text(info, "language") != "cs":
            continue
        params = {_text(p, "valueName"): _text(p, "value") for p in info.findall(NS + "parameter")}
        level_raw = params.get("awareness_level", "")
        if not level_raw or _text(info, "certainty") == "Unlikely":
            continue  # „Žádná výstraha …“ a výhledy bez úrovně
        matched = []
        for area in info.findall(NS + "area"):
            areas_seen += 1
            if areas_seen > MAX_AREAS:
                raise CapError("too many <area> blocks")
            codes = {_text(g, "value") for g in area.findall(NS + "geocode") if _text(g, "valueName") == "CISORP"}
            if codes & wanted:
                matched.append(_text(area, "areaDesc"))
        if not matched:
            continue
        try:
            level = int(level_raw.split(";")[0])
        except ValueError:
            level = SEVERITY.get(_text(info, "severity"), 1)
        onset = _dt(_text(info, "onset")) or _dt(_text(info, "effective")) or sent
        expires = _dt(_text(info, "expires")) or _dt(params.get("eventEndingTime", ""))
        out["alerts"].append({
            "event": _text(info, "event"),
            "level": level,
            "severity": _text(info, "severity"),
            "onset": onset.isoformat(),
            "expires": expires.isoformat() if expires else None,
            "area": matched[0],
            "text": _text(info, "description").split("\n")[0][:160],
        })
    return out


def fetch_alerts(http, cfg: AlertsConfig, last: dict | None, now_ts: float) -> tuple[dict, dict | None]:
    """(stav k zobrazení, záznam k uložení). Záznam {"ts", "checked", "data": {"file", "sent", "alerts"}}.

    Adresář se kontroluje nejvýš jednou za `refresh_minutes`; nový XML se stahuje jen při změně souboru.
    Při selhání se použije cache do `max_age_hours` (označená stale), potom {"ok": False}."""
    last = last if isinstance(last, dict) else {}
    data = last.get("data")
    checked = float(last.get("checked") or 0)
    if data is not None and now_ts - checked < cfg.refresh_minutes * 60:
        return {"ok": True, **data}, last

    def fallback() -> tuple[dict, dict]:
        ts = float(last.get("ts") or 0)
        record = {**last, "retry_after": now_ts + RETRY_S}  # při výpadku nezkoušet stahovat každou minutu
        if data is not None and now_ts - ts <= cfg.max_age_hours * 3600:
            return {"ok": True, **data, "stale": True, "since": ts}, record
        return {"ok": False}, record

    if float(last.get("retry_after") or 0) > now_ts:
        return fallback()
    try:
        listing = http.get_bytes(cfg.listing_url, MAX_LISTING_BYTES).decode("utf-8", "replace")
        merged_alerts, files, sent_max = [], {}, None
        for feed in cfg.feeds:
            found = latest_file(listing, feed)
            if not found:
                raise CapError(f"no CAP file for feed {feed}")
            name, stamp = found
            prev = (data or {}).get("files", {}).get(feed) if data else None
            if prev and prev.get("name") == name and prev.get("stamp") == stamp:
                parsed = {"sent": prev["sent"], "alerts": prev["alerts"]}
            else:
                parsed = parse_cap(http.get_bytes(cfg.listing_url + name, MAX_XML_BYTES), cfg.area_codes)
                if prev and _dt(prev.get("sent") or "") and _dt(parsed["sent"]) < _dt(prev["sent"]):
                    log.warning("chmi feed %s: %s older than cached message, keeping cache", feed, name)
                    parsed = {"sent": prev["sent"], "alerts": prev["alerts"]}
                    name, stamp = prev["name"], prev["stamp"]
            files[feed] = {"name": name, "stamp": stamp, "sent": parsed["sent"], "alerts": parsed["alerts"]}
            merged_alerts.extend(parsed["alerts"])
            if sent_max is None or _dt(parsed["sent"]) > _dt(sent_max):
                sent_max = parsed["sent"]
        new_data = {"files": files, "sent": sent_max, "alerts": merged_alerts}
        record = {"ts": now_ts, "checked": now_ts, "data": new_data}
        return {"ok": True, **new_data}, record
    except Exception as err:  # noqa: BLE001 – výstrahy nesmí shodit render
        log.warning("chmi alerts failed: %s", err)
        return fallback()


def _when(dt: datetime, now: datetime) -> str:
    local = dt.astimezone(now.tzinfo)
    hhmm = f"{local.hour}:{local.minute:02d}"
    if local.date() == now.date():
        return hhmm
    if local.date() == (now + timedelta(days=1)).date():
        return f"zítra {hhmm}"
    return f"{local.day}. {local.month}. {hhmm}"


def alert_strip(state: dict, now: datetime, cfg: AlertsConfig) -> dict | None:
    """Payload pruhu výstrah: {"ok", "count", "level", "label", "event", "valid", "src", "sent", "stale", "as_of"}.

    Výstrahy, které skončily, se vynechají vždy (i z cache); budoucí jen do `horizon_hours`.
    Priorita: nejvyšší úroveň → dřívější začátek → název. `count` = počet platných výstrah pro oblast."""
    if not cfg.enabled:
        return None
    if not state.get("ok"):
        return {"ok": False, "src": "ČHMÚ"}
    now_aware = now if now.tzinfo else now.astimezone()
    horizon = now_aware + timedelta(hours=cfg.horizon_hours)
    active = []
    seen = set()
    for a in state.get("alerts") or []:
        onset = _dt(a.get("onset") or "")
        expires = _dt(a.get("expires") or "")
        if onset is None or (expires is not None and expires <= now_aware) or onset > horizon:
            continue
        key = (a.get("event"), a.get("onset"), a.get("expires"))
        if key in seen:  # stejný jev pro víc oblastí/feedů
            continue
        seen.add(key)
        active.append((a, onset, expires))
    active.sort(key=lambda x: (-int(x[0].get("level") or 0), x[1], x[0].get("event") or ""))
    out: dict = {"ok": True, "count": len(active), "src": "ČHMÚ"}
    sent = _dt(state.get("sent") or "")
    if sent:
        out["sent"] = _when(sent, now_aware)
    if state.get("stale"):
        from .payload import as_of

        out["stale"] = True
        stamp = as_of(state.get("since"), now_aware)
        if stamp:
            out["as_of"] = stamp
    if active:
        top, onset, expires = active[0]
        level = int(top.get("level") or 1)
        if onset > now_aware:
            valid = f"od {_when(onset, now_aware)}" + (f" do {_when(expires, now_aware)}" if expires else "")
        else:
            valid = f"do {_when(expires, now_aware)}" if expires else "do odvolání"
        out.update({"level": level, "label": LEVEL_LABEL.get(level, "výstraha"), "event": top.get("event", ""),
                    "valid": valid})
    elif not cfg.show_none:
        out["hide"] = True
    return out
