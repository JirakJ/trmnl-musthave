"""Přehled stavu serveru a zařízení pro /status (HTML) a /status.json.

Rozlišuje tři stavy snímku:
- vyrenderovaný: nejnovější snímek ve FrameStore (render smyčka, `render.json`),
- nabídnutý: snímek, který server zařízení poslal v odpovědi /api/display (action partial/full),
- potvrzený: snímek, který zařízení v DALŠÍM dotazu ohlásilo v hlavičce X-Frame-Id (protokol v1).
Odeslaná odpověď tedy není potvrzení; stock firmware X-Frame-Id neposílá, takže potvrzení nikdy nevznikne.

Dostupnost zařízení se počítá z intervalu, který zařízení skutečně dostalo (`refresh_rate`): dokud neuplyne
očekávané probuzení + tolerance, zařízení v deep sleep není „neaktivní“.
"""

from __future__ import annotations

import html
import json
from datetime import datetime
from pathlib import Path
from typing import Any

from .policy import DeviceState, PolicyConfig, battery_percent, plausible_voltage

SAVING_LABELS = {"none": "běžný", "quiet": "doba klidu", "low": "úspora baterie", "quiet+low": "doba klidu + úspora baterie"}
RSSI_MIN, RSSI_MAX = -120, -1  # dBm; 0 a kladné hodnoty posílá firmware, když měření nemá


def valid_rssi(raw: str | None) -> int | None:
    """Hlavička RSSI → dBm, nebo None (chybí, není číslo, mimo rozsah −120…−1 dBm)."""
    try:
        value = int(float(str(raw).strip()))
    except (TypeError, ValueError, OverflowError):
        return None
    return value if RSSI_MIN <= value <= RSSI_MAX else None


def wifi_percent(rssi: int | None) -> int | None:
    """Převod dBm → % (lineárně: −100 dBm = 0 %, −50 dBm a lépe = 100 %), běžná konvence ESP32/Windows."""
    if rssi is None:
        return None
    return max(0, min(100, 2 * (rssi + 100)))


def wifi_quality(rssi: int | None) -> str:
    if rssi is None:
        return "neznámý"
    if rssi >= -60:
        return "výborný"
    if rssi >= -70:
        return "dobrý"
    if rssi >= -80:
        return "slabý"
    return "velmi slabý"


def availability(state: DeviceState, now_ts: float, cfg: PolicyConfig) -> str:
    """aktivní | opožděné (po očekávaném probuzení, v toleranci) | neaktivní | nikdy neviděno."""
    if not state.last_seen_at:
        return "nikdy neviděno"
    expected = state.next_expected_at
    if expected is None:  # starší záznam bez intervalu: odhad z nejdelšího možného intervalu
        expected = state.last_seen_at + max(cfg.interval_usb, cfg.interval_battery)
    if now_ts <= expected:
        return "aktivní"
    if now_ts <= expected + cfg.offline_tolerance_s:
        return "opožděné"
    return "neaktivní"


def _iso(ts: float | None, tz=None) -> str | None:
    if not isinstance(ts, (int, float)):
        return None
    return datetime.fromtimestamp(ts, tz).isoformat(timespec="seconds")


def _age_s(ts: float | None, now_ts: float) -> int | None:
    return int(now_ts - ts) if isinstance(ts, (int, float)) else None


def device_summary(mac: str, state: DeviceState, now_ts: float, cfg: PolicyConfig, tz=None) -> dict[str, Any]:

    voltage = plausible_voltage(state.voltage)
    confirmed = bool(state.acked_frame_id and state.acked_frame_id == state.target_frame_id)
    return {
        "id": mac,
        "availability": availability(state, now_ts, cfg),
        "last_seen": _iso(state.last_seen_at, tz),
        "last_seen_age_s": _age_s(state.last_seen_at, now_ts),
        "first_seen": _iso(state.first_seen_at, tz),
        "next_expected": _iso(state.next_expected_at, tz),
        "refresh_rate_s": state.refresh_rate,
        "firmware": state.fw_version,
        "battery_voltage": voltage,
        "battery_percent": battery_percent(voltage),
        "battery_measured": _iso(state.voltage_at, tz),
        "rssi_dbm": state.rssi,
        "wifi_percent": wifi_percent(state.rssi),
        "wifi_quality": wifi_quality(state.rssi),
        "rssi_measured": _iso(state.rssi_at, tz),
        "frame_shown": state.frame_id,
        "frame_offered": state.target_frame_id,
        "frame_offered_at": _iso(state.offered_at, tz),
        "frame_acknowledged": state.acked_frame_id,
        "frame_acknowledged_at": _iso(state.acked_at, tz),
        "offered_frame_confirmed": confirmed,
        "saving": state.saving or "none",
        "saving_reason": state.saving_reason,
        "low_battery": bool(state.low_battery),
    }


def read_json(path: Path) -> Any:
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (FileNotFoundError, ValueError, OSError):
        return None


def source_freshness(state_file: Path, now_ts: float, tz=None) -> dict[str, Any]:
    """Čas posledního ÚSPĚŠNÉHO získání dat každého zdroje (ze state/last.json), nezávisle na čase renderu."""
    raw = read_json(state_file) or {}
    out = {}
    for name, key in (("weather", "last_weather"), ("kick", "last_kick"), ("twitch", "last_twitch")):
        rec = raw.get(key) or {}
        ts = rec.get("ts") if isinstance(rec, dict) else None
        ts = ts if isinstance(ts, (int, float)) and ts > 0 else None
        out[name] = {"last_success": _iso(ts, tz), "age_s": _age_s(ts, now_ts)}
    for name, rec in (raw.get("sources") or {}).items():  # další zdroje ukládají {"ts", "data"} pod "sources"
        ts = rec.get("ts") if isinstance(rec, dict) else None
        ts = ts if isinstance(ts, (int, float)) and ts > 0 else None
        out[name] = {"last_success": _iso(ts, tz), "age_s": _age_s(ts, now_ts)}
    return out


def build_status(frames, devices, cfg: PolicyConfig, now_ts: float, root: Path | None = None,
                 firmware_version: str | None = None, tz=None) -> dict[str, Any]:
    render = read_json(root / "state" / "frames" / "render.json") if root else None
    latest = frames.latest()
    rendered_at = render.get("rendered_at") if isinstance(render, dict) and render.get("frame_id") == latest else None
    with_ids = sorted(devices.items(), key=lambda kv: -(kv[1].last_seen_at or 0))
    return {
        "now": _iso(now_ts, tz),
        "frame_rendered": latest,
        "frame_rendered_at": _iso(rendered_at, tz),
        "firmware_offered": firmware_version,
        "sources": source_freshness(root / "state" / "last.json", now_ts, tz) if root else {},
        "devices": [device_summary(mac, st, now_ts, cfg, tz) for mac, st in with_ids],
    }


def _fmt_age(age: int | None) -> str:
    if age is None:
        return "—"
    if age < 90:
        return f"{age} s"
    if age < 5400:
        return f"{round(age / 60)} min"
    if age < 2 * 86400:
        return f"{age / 3600:.1f} h"
    return f"{age // 86400} d"


def _short(ts: str | None) -> str:
    return ts.replace("T", " ")[:19] if ts else "—"


def render_html(status: dict[str, Any]) -> str:
    e = lambda v: html.escape("—" if v is None or v == "" else str(v))  # noqa: E731
    rows = []
    for name, src in status.get("sources", {}).items():
        if src["last_success"]:
            rows.append(f"<tr><td>{e(name)}</td><td>{e(_short(src['last_success']))}</td><td>{e(_fmt_age(src['age_s']))}</td></tr>")
        else:
            rows.append(f"<tr><td>{e(name)}</td><td colspan=2>zatím žádné úspěšné získání dat</td></tr>")
    devs = []
    for d in status.get("devices", []):
        if d["frame_acknowledged"] and d["offered_frame_confirmed"]:
            ack = f"potvrzen {e(_short(d['frame_acknowledged_at']))}"
        elif d["frame_acknowledged"]:
            ack = f"nabídnutý zatím nepotvrzen (naposledy potvrzen {e(d['frame_acknowledged'])} {e(_short(d['frame_acknowledged_at']))})"
        else:
            ack = "nepotvrzen (firmware zatím neohlásil X-Frame-Id nabídnutého snímku)"
        wifi = (f"{e(d['rssi_dbm'])} dBm ≈ {e(d['wifi_percent'])} % ({e(d['wifi_quality'])}), měřeno {e(_short(d['rssi_measured']))}"
                if d["rssi_dbm"] is not None else "neznámý (žádné platné měření)")
        batt = (f"{e(d['battery_percent'])} % ({e(d['battery_voltage'])} V), měřeno {e(_short(d['battery_measured']))}"
                if d["battery_percent"] is not None else "neznámá (žádné věrohodné měření)")
        devs.append(
            f"<section><h2>{e(d['id'])} · {e(d['availability'])}</h2><table>"
            f"<tr><th>Poslední kontakt</th><td>{e(_short(d['last_seen']))} (před {e(_fmt_age(d['last_seen_age_s']))})</td></tr>"
            f"<tr><th>Očekávaný další kontakt</th><td>{e(_short(d['next_expected']))} (interval {e(d['refresh_rate_s'])} s)</td></tr>"
            f"<tr><th>Režim intervalu</th><td>{e(SAVING_LABELS.get(d.get('saving') or 'none', d.get('saving')))}"
            f" – {e(d.get('saving_reason'))}</td></tr>"
            f"<tr><th>Firmware</th><td>{e(d['firmware'])}</td></tr>"
            f"<tr><th>Baterie (odhad)</th><td>{batt}</td></tr>"
            f"<tr><th>Wi-Fi</th><td>{wifi}</td></tr>"
            f"<tr><th>Nabídnutý snímek</th><td>{e(d['frame_offered'])} ({e(_short(d['frame_offered_at']))})</td></tr>"
            f"<tr><th>Potvrzení</th><td>{ack}</td></tr>"
            "</table></section>")
    if not devs:
        devs.append("<p>Žádné zařízení se zatím neohlásilo.</p>")
    latest = status.get("frame_rendered")
    img = f'<img src="/frames/{html.escape(latest)}.png" alt="aktuální snímek" width="800" height="480">' if latest else ""
    return f"""<!DOCTYPE html><html lang="cs"><head><meta charset="utf-8"><title>Must-have · stav</title>
<meta name="viewport" content="width=device-width, initial-scale=1">
<style>body{{font-family:system-ui,sans-serif;margin:16px;max-width:860px}}table{{border-collapse:collapse;margin:6px 0 14px}}
td,th{{text-align:left;padding:3px 10px 3px 0;vertical-align:top}}img{{max-width:100%;height:auto;border:1px solid #888}}</style></head>
<body><h1>TRMNL Must-have BYOS v1</h1>
<p>Vyrenderovaný snímek: <b>{e(latest)}</b> ({e(_short(status.get('frame_rendered_at')))}) · nabízený firmware: {e(status.get('firmware_offered'))}
· <a href="/status.json">JSON</a></p>
<h2>Data (poslední úspěšné získání)</h2><table>{''.join(rows)}</table>
{''.join(devs)}
{img}
</body></html>"""
