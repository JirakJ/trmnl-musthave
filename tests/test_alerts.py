"""#12 Výstrahy ČHMÚ: výběr nejnovější zprávy, oblast (CISORP), platnost, priority, cache, bezpečný parser."""

from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from musthave.alerts import (AlertsConfig, CapError, alert_strip, fetch_alerts, latest_file, parse_cap)
from musthave.config import ConfigError, parse_alerts

FIX = Path(__file__).parent / "fixtures"
CAP = (FIX / "chmi_cap.xml").read_bytes()          # zmenšená reálná zpráva ČHMÚ z 10. 11. 2026 (ledovka)
LISTING = (FIX / "chmi_listing.html").read_text(encoding="utf-8")
TZ = ZoneInfo("Europe/Prague")
CFG = AlertsConfig(enabled=True, area_codes=("3206",))   # ORP v oblasti „Velmi kluzké povrchy“ (oranžová)


def cap(infos: str, sent="2026-11-10T10:34:05+01:00", msg_type="Alert", status="Actual") -> bytes:
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<alert xmlns="urn:oasis:names:tc:emergency:cap:1.2"><identifier>x</identifier><sender>chmi@chmi.cz</sender>
<sent>{sent}</sent><status>{status}</status><msgType>{msg_type}</msgType><scope>Public</scope>{infos}</alert>""".encode()


def info(event, level, onset, expires, codes=("6105",), lang="cs", certainty="Likely"):
    geo = "".join(f"<geocode><valueName>CISORP</valueName><value>{c}</value></geocode>" for c in codes)
    exp = f"<expires>{expires}</expires>" if expires else ""
    return (f"<info><language>{lang}</language><category>Met</category><event>{event}</event><urgency>Future</urgency>"
            f"<severity>Moderate</severity><certainty>{certainty}</certainty><onset>{onset}</onset>{exp}"
            f"<parameter><valueName>awareness_level</valueName><value>{level}; yellow; Moderate</value></parameter>"
            f"<area><areaDesc>Kraj Vysočina</areaDesc>{geo}</area></info>")


def test_latest_file_picks_newest_by_listing_time_not_name():
    assert latest_file(LISTING, "50") == ("alert_cap_50_120850.xml", "12-Nov-2026 08:50")
    assert latest_file(LISTING, "70") == ("alert_cap_70_101200.xml", "10-Nov-2026 14:00")
    assert latest_file("<html></html>", "50") is None


def test_real_message_selects_alerts_for_configured_area_only():
    out = parse_cap(CAP, ("3206",))
    assert out["sent"] == "2026-11-10T10:34:05+01:00" and out["msg_type"] == "Alert"
    assert [(a["event"], a["level"]) for a in out["alerts"]] == [("Velmi kluzké povrchy", 3)]
    assert out["alerts"][0]["expires"] == "2026-11-11T10:00:00+01:00"
    assert parse_cap(CAP, ("6105",))["alerts"] == []     # Jihlava v té zprávě výstrahu neměla
    assert len(parse_cap(CAP, ("2102",))["alerts"]) == 1  # „Kluzké povrchy“ (žlutá); „Žádná výstraha“ se ignoruje


@pytest.mark.parametrize("evil", [
    b'<?xml version="1.0"?><!DOCTYPE a [<!ENTITY x "y">]><alert xmlns="urn:oasis:names:tc:emergency:cap:1.2"/>',
    b'<?xml version="1.0"?><!DOCTYPE a SYSTEM "http://evil/x.dtd"><a/>',
    b"not xml at all",
    b'<?xml version="1.0"?><other/>',
    cap("", sent="garbage"),
])
def test_malicious_or_broken_input_is_rejected(evil):
    with pytest.raises(CapError):
        parse_cap(evil, ("6105",))


def test_oversized_input_is_rejected():
    with pytest.raises(CapError):
        parse_cap(b" " * (9 * 1024 * 1024), ("6105",))


def test_cancel_and_non_actual_messages_have_no_alerts():
    body = info("Silný vítr", 2, "2026-11-12T10:00:00+01:00", "2026-11-12T20:00:00+01:00")
    assert parse_cap(cap(body, msg_type="Cancel"), ("6105",))["alerts"] == []
    assert parse_cap(cap(body, status="Exercise"), ("6105",))["alerts"] == []
    assert len(parse_cap(cap(body, msg_type="Update"), ("6105",))["alerts"]) == 1
    english = info("Wind", 2, "2026-11-12T10:00:00+01:00", None, lang="en-GB")
    unlikely = info("Žádná", 2, "2026-11-12T10:00:00+01:00", None, certainty="Unlikely")
    assert parse_cap(cap(english + unlikely), ("6105",))["alerts"] == []


def state_of(*infos, sent="2026-11-12T08:00:00+01:00"):
    return {"ok": True, **parse_cap(cap("".join(infos), sent=sent), ("6105",))}


def test_strip_priority_count_and_validity():
    now = datetime(2026, 11, 12, 12, 0, tzinfo=TZ)
    st = state_of(
        info("Silný vítr", 2, "2026-11-12T10:00:00+01:00", "2026-11-12T20:00:00+01:00"),
        info("Vydatný déšť", 3, "2026-11-12T18:00:00+01:00", "2026-11-13T06:00:00+01:00"),
        info("Ledovka", 2, "2026-11-12T06:00:00+01:00", "2026-11-12T11:00:00+01:00"),        # už skončila
        info("Sníh", 2, "2026-11-14T06:00:00+01:00", "2026-11-14T12:00:00+01:00"))            # za horizontem 24 h
    s = alert_strip(st, now, AlertsConfig(enabled=True, area_codes=("6105",)))
    assert s["count"] == 2 and s["level"] == 3 and s["event"] == "Vydatný déšť" and s["label"] == "vysoký stupeň"
    assert s["valid"] == "od 18:00 do zítra 6:00" and s["src"] == "ČHMÚ" and s["sent"] == "8:00"


def test_active_alert_without_expiry_and_equal_level_tiebreak():
    now = datetime(2026, 11, 12, 12, 0, tzinfo=TZ)
    st = state_of(info("Vítr", 2, "2026-11-12T11:00:00+01:00", None), info("Bouřky", 2, "2026-11-12T09:00:00+01:00", None))
    s = alert_strip(st, now, AlertsConfig(enabled=True, area_codes=("6105",)))
    assert s["event"] == "Bouřky" and s["valid"] == "do odvolání" and s["count"] == 2


def test_no_alert_vs_unavailable_vs_disabled():
    now = datetime(2026, 11, 12, 12, 0, tzinfo=TZ)
    on = AlertsConfig(enabled=True, area_codes=("6105",))
    none = alert_strip(state_of(), now, on)
    assert none["ok"] and none["count"] == 0 and none["hide"] is True
    shown = alert_strip(state_of(), now, AlertsConfig(enabled=True, area_codes=("6105",), show_none=True))
    assert shown["count"] == 0 and "hide" not in shown
    assert alert_strip({"ok": False}, now, on) == {"ok": False, "src": "ČHMÚ"}   # nikdy „bez výstrah“
    assert alert_strip(state_of(), now, AlertsConfig()) is None


class FakeHttp:
    def __init__(self, files=None, fail=False):
        self.files, self.fail, self.calls = files or {}, fail, []

    def get_bytes(self, url, max_bytes, headers=None):
        self.calls.append(url)
        if self.fail:
            raise OSError("offline")
        if url.endswith("/cap/"):
            return LISTING.encode()
        return self.files[url.rsplit("/", 1)[1]]


def test_fetch_downloads_only_when_file_changes_and_respects_refresh():
    http = FakeHttp({"alert_cap_50_120850.xml": CAP})
    shown, rec = fetch_alerts(http, CFG, None, 1000.0)
    assert shown["ok"] and shown["alerts"][0]["event"] == "Velmi kluzké povrchy" and len(http.calls) == 2
    shown2, rec2 = fetch_alerts(http, CFG, rec, 1000.0 + 60)              # v rámci refresh_minutes: bez sítě
    assert len(http.calls) == 2 and shown2["alerts"] == shown["alerts"]
    shown3, rec3 = fetch_alerts(http, CFG, rec, 1000.0 + 16 * 60)         # stejný soubor: jen adresář
    assert len(http.calls) == 3 and rec3["ts"] == 1000.0 + 16 * 60


def test_older_message_never_replaces_newer_cache():
    newer = cap(info("Vítr", 2, "2026-11-12T10:00:00+01:00", None, codes=("3206",)), sent="2026-11-12T09:00:00+01:00")
    http = FakeHttp({"alert_cap_50_120850.xml": newer})
    _, rec = fetch_alerts(http, CFG, None, 1000.0)
    rec["data"]["files"]["50"]["name"] = "alert_cap_50_000000.xml"        # adresář teď ukazuje „jiný“ (starší) soubor
    http.files["alert_cap_50_120850.xml"] = CAP                            # zpráva z 10. 11.
    shown, rec2 = fetch_alerts(http, CFG, rec, 1000.0 + 20 * 60)
    assert shown["sent"] == "2026-11-12T09:00:00+01:00" and shown["alerts"][0]["event"] == "Vítr"


def test_outage_uses_cache_marked_stale_then_unavailable():
    _, rec = fetch_alerts(FakeHttp({"alert_cap_50_120850.xml": CAP}), CFG, None, 1000.0)
    offline = FakeHttp(fail=True)
    shown, rec2 = fetch_alerts(offline, CFG, rec, 1000.0 + 3600)
    assert shown["ok"] and shown["stale"] and shown["since"] == 1000.0 and rec2["retry_after"] == 1000.0 + 3600 + 120
    calls = len(offline.calls)
    fetch_alerts(offline, CFG, rec2, 1000.0 + 3600 + 30)                   # během backoffu se nestahuje
    assert len(offline.calls) == calls
    gone, _ = fetch_alerts(offline, CFG, rec, 1000.0 + 7 * 3600)          # cache starší než max_age_hours
    assert gone == {"ok": False}
    first, _ = fetch_alerts(offline, CFG, None, 1000.0)
    assert first == {"ok": False}


def test_stale_strip_shows_data_age():
    shown, _ = fetch_alerts(FakeHttp({"alert_cap_50_120850.xml": CAP}), CFG, None, 1000.0)
    st = {**shown, "stale": True, "since": datetime(2026, 11, 10, 19, 30, tzinfo=TZ).timestamp()}
    s = alert_strip(st, datetime(2026, 11, 10, 20, 0, tzinfo=TZ), CFG)
    assert s["stale"] and s["as_of"] == "19:30"
    assert s["event"] == "Velmi kluzké povrchy" and s["valid"] == "od zítra 3:00 do zítra 10:00"
    later = alert_strip(st, datetime(2026, 11, 11, 8, 0, tzinfo=TZ), CFG)
    assert later["valid"] == "do 10:00"
    assert alert_strip(st, datetime(2026, 11, 11, 10, 0, tzinfo=TZ), CFG)["count"] == 0   # prošlá cache neukazuje výstrahu


def test_config_validation():
    assert parse_alerts({}).enabled is False
    cfg = parse_alerts({"area_codes": ["6105"]})
    assert cfg.enabled and cfg.area_codes == ("6105",) and cfg.feeds == ("50",)
    assert parse_alerts({"area_codes": "6105", "enabled": False}).enabled is False
    with pytest.raises(ConfigError, match="CISORP"):
        parse_alerts({"area_codes": ["Jihlava"]})
    with pytest.raises(ConfigError, match="feeds"):
        parse_alerts({"area_codes": ["6105"], "feeds": ["99"]})
