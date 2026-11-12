"""#10 Agenda z ICS: časové zóny, změna času, celodenní akce, výjimky opakování, výpadek zdroje, soukromí."""

import logging
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pytest

from musthave.agenda import AgendaConfig, _request, agenda_panel, fetch_agenda
from musthave.config import ConfigError, load_settings, parse_agenda
from musthave.ics import expand, parse_duration, parse_events, unfold

TZ = ZoneInfo("Europe/Prague")
URL = "https://calendar.example.com/private-abc123/basic.ics"


def cal(*events: str) -> str:
    body = "\r\n".join(events)
    return f"BEGIN:VCALENDAR\r\nVERSION:2.0\r\nPRODID:-//test//EN\r\n{body}\r\nEND:VCALENDAR\r\n"


def ev(uid, start, end=None, summary="Událost", extra=""):
    lines = [f"BEGIN:VEVENT", f"UID:{uid}", start] + ([end] if end else []) + [f"SUMMARY:{summary}"]
    if extra:
        lines += extra.split("\n")
    return "\r\n".join(lines + ["END:VEVENT"])


def occ(text, day_from, day_to):
    start = datetime.combine(day_from, datetime.min.time(), tzinfo=TZ)
    end = datetime.combine(day_to, datetime.min.time(), tzinfo=TZ)
    found, bad = expand(parse_events(text, TZ), start, end, TZ)
    return sorted(found, key=lambda o: o.start), bad


def test_unfold_params_and_escapes():
    text = cal(ev("a", "DTSTART;TZID=\"Europe/Prague\":20261112T090000", "DTEND;TZID=Europe/Prague:20261112T100000",
                  "Porada\\, tým\\nB", "LOCATION:Zasedačka\r\n  2. patro\r\nBEGIN:VALARM\r\nSUMMARY:alarm\r\nEND:VALARM"))
    assert "Zasedačka 2. patro" in "".join(unfold(text))
    [o], _ = occ(text, date(2026, 11, 12), date(2026, 11, 13))
    assert o.summary == "Porada, tým\nB" and o.location == "Zasedačka 2. patro" and o.start.hour == 9


def test_utc_floating_and_windows_timezones():
    text = cal(ev("u", "DTSTART:20261112T080000Z", "DTEND:20261112T090000Z"),
               ev("f", "DTSTART:20261112T120000", "DURATION:PT30M"),
               ev("w", "DTSTART;TZID=Central Europe Standard Time:20261112T150000", "DURATION:PT1H"),
               ev("x", "DTSTART;TZID=Not/AZone:20261112T170000", "DURATION:PT1H"))
    found, _ = occ(text, date(2026, 11, 12), date(2026, 11, 13))
    local = {o.uid: o.start.astimezone(TZ).strftime("%H:%M") for o in found}
    assert local == {"u": "09:00", "f": "12:00", "w": "15:00", "x": "17:00"}   # neznámá zóna → zóna dashboardu


def test_weekly_meeting_keeps_local_time_across_dst_change():
    text = cal(ev("w", "DTSTART;TZID=Europe/Prague:20261019T090000", "DTEND;TZID=Europe/Prague:20261019T093000",
                  extra="RRULE:FREQ=WEEKLY;BYDAY=MO"))
    found, _ = occ(text, date(2026, 10, 19), date(2026, 11, 3))
    assert [o.start.astimezone(TZ).strftime("%d.%m %H:%M") for o in found] == ["19.10 09:00", "26.10 09:00", "02.11 09:00"]
    assert found[1].start.utcoffset() == timedelta(hours=1) and found[0].start.utcoffset() == timedelta(hours=2)


def test_all_day_single_and_multi_day():
    text = cal(ev("a", "DTSTART;VALUE=DATE:20261112", "DTEND;VALUE=DATE:20261113", "Svátek"),
               ev("m", "DTSTART;VALUE=DATE:20261111", "DTEND;VALUE=DATE:20261114", "Konference"))
    found, _ = occ(text, date(2026, 11, 12), date(2026, 11, 13))
    assert {o.uid for o in found} == {"a", "m"} and all(o.all_day for o in found)
    panel = agenda_panel({"ok": True, "items": [{"s": o.start.isoformat(), "e": o.end.isoformat(), "a": True, "n": o.summary,
                                                  "u": o.uid, "c": 0} for o in found]},
                         datetime(2026, 11, 12, 10, tzinfo=TZ), AgendaConfig(sources=("x",)))
    assert [(i["day"], i["n"], i.get("all")) for i in panel["items"]] == [("dnes", "Konference", True), ("dnes", "Svátek", True)]  # dřívější začátek první


def test_recurrence_exceptions_moved_and_cancelled_instances_are_not_duplicated():
    master = ev("r", "DTSTART;TZID=Europe/Prague:20261109T100000", "DTEND;TZID=Europe/Prague:20261109T110000", "Standup",
                "RRULE:FREQ=DAILY;COUNT=5\nEXDATE;TZID=Europe/Prague:20261110T100000")
    moved = ev("r", "DTSTART;TZID=Europe/Prague:20261111T140000", "DTEND;TZID=Europe/Prague:20261111T150000", "Standup (přesunuto)",
               "RECURRENCE-ID;TZID=Europe/Prague:20261111T100000")
    cancelled = ev("r", "DTSTART;TZID=Europe/Prague:20261112T100000", "DTEND;TZID=Europe/Prague:20261112T110000", "Standup",
                   "RECURRENCE-ID;TZID=Europe/Prague:20261112T100000\nSTATUS:CANCELLED")
    found, _ = occ(cal(master, moved, cancelled), date(2026, 11, 9), date(2026, 11, 20))
    assert [(o.start.astimezone(TZ).strftime("%d %H:%M"), o.summary) for o in found] == [
        ("09 10:00", "Standup"), ("11 14:00", "Standup (přesunuto)"), ("13 10:00", "Standup")]


def test_cancelled_master_until_and_monthly_31st():
    gone = ev("c", "DTSTART:20261112T100000Z", "DTEND:20261112T110000Z", extra="STATUS:CANCELLED")
    until = ev("u", "DTSTART;TZID=Europe/Prague:20261109T080000", "DURATION:PT1H", extra="RRULE:FREQ=DAILY;UNTIL=20261111T070000Z")
    monthly = ev("m", "DTSTART;VALUE=DATE:20260131", "DTEND;VALUE=DATE:20260201", extra="RRULE:FREQ=MONTHLY")
    found, _ = occ(cal(gone, until), date(2026, 11, 9), date(2026, 11, 20))
    assert [o.start.day for o in found] == [9, 10, 11]
    months, _ = occ(cal(monthly), date(2026, 1, 1), date(2026, 8, 1))
    assert [o.start.month for o in months] == [1, 3, 5, 7]          # RFC 5545: měsíce bez 31. se přeskočí


def test_long_running_daily_recurrence_reaches_the_window():
    text = cal(ev("d", "DTSTART;TZID=Europe/Prague:20150105T091500", "DURATION:PT15M", extra="RRULE:FREQ=DAILY"))
    found, _ = occ(text, date(2026, 11, 12), date(2026, 11, 14))
    assert [o.start.astimezone(TZ).strftime("%d %H:%M") for o in found] == ["12 09:15", "13 09:15"]


def test_monthly_nth_weekday_and_unsupported_rule_is_skipped_not_wrong():
    last_friday = ev("p", "DTSTART;TZID=Europe/Prague:20260130T160000", "DURATION:PT1H", extra="RRULE:FREQ=MONTHLY;BYDAY=-1FR")
    weird = ev("x", "DTSTART:20261112T100000Z", "DURATION:PT1H", extra="RRULE:FREQ=MONTHLY;BYDAY=MO,TU;BYSETPOS=-1")
    found, bad = occ(cal(last_friday, weird), date(2026, 11, 1), date(2026, 12, 31))
    assert [o.start.date() for o in found] == [date(2026, 11, 27), date(2026, 12, 25)] and bad == 1


def test_duration_parsing():
    assert parse_duration("PT1H30M") == timedelta(hours=1, minutes=30)
    assert parse_duration("P1W") == timedelta(weeks=1) and parse_duration("-PT15M") == timedelta(minutes=-15)


class FakeHttp:
    def __init__(self, body=None, fail=False):
        self.body, self.fail, self.calls = body, fail, []

    def get_bytes(self, url, max_bytes, headers=None):
        self.calls.append((url, headers))
        if self.fail:
            raise OSError(f"connection refused while fetching {url}")
        return self.body.encode()


CFG = AgendaConfig(sources=(URL,))
NOW = datetime(2026, 11, 12, 10, 30, tzinfo=TZ)
DAY = cal(ev("a", "DTSTART;TZID=Europe/Prague:20261112T090000", "DTEND;TZID=Europe/Prague:20261112T110000", "Probíhá",
             "LOCATION:Kancelář"),
          ev("b", "DTSTART;TZID=Europe/Prague:20261112T140000", "DTEND;TZID=Europe/Prague:20261112T150000", "Odpoledne"),
          ev("c", "DTSTART;TZID=Europe/Prague:20261112T080000", "DTEND;TZID=Europe/Prague:20261112T083000", "Ráno (skončila)"),
          ev("d", "DTSTART;TZID=Europe/Prague:20261113T090000", "DTEND;TZID=Europe/Prague:20261113T100000", "Zítra"),
          ev("e", "DTSTART;TZID=Europe/Prague:20261115T090000", "DTEND;TZID=Europe/Prague:20261115T100000", "Mimo okno"))


def test_panel_shows_ongoing_upcoming_and_tomorrow_in_stable_order():
    state, rec = fetch_agenda(FakeHttp(DAY), CFG, None, NOW)
    panel = agenda_panel(state, NOW, CFG)
    assert [(i["day"], i["n"], i.get("now", False)) for i in panel["items"]] == [
        ("dnes", "Probíhá", True), ("dnes", "Odpoledne", False), ("zítra", "Zítra", False)]
    assert panel["items"][0]["l"] == "Kancelář" and panel["items"][0]["t"] == "09:00" and panel["items"][0]["e"] == "11:00"
    assert "today_empty" not in panel and "stale" not in panel


def test_overlapping_events_from_two_calendars_have_stable_order():
    other = cal(ev("z", "DTSTART;TZID=Europe/Prague:20261112T140000", "DTEND;TZID=Europe/Prague:20261112T150000", "Alfa"))
    cfg = AgendaConfig(sources=(URL, URL + "?2"))

    class Two(FakeHttp):
        def get_bytes(self, url, max_bytes, headers=None):
            return (other if url.endswith("2") else DAY).encode()

    state, _ = fetch_agenda(Two(), cfg, None, NOW)
    names = [i["n"] for i in agenda_panel(state, NOW, cfg)["items"]]
    assert names[:3] == ["Probíhá", "Odpoledne", "Alfa"]   # stejný čas: pořadí kalendářů, ne abeceda


def test_empty_today_and_limit():
    empty_day = cal(ev("d", "DTSTART;TZID=Europe/Prague:20261113T090000", "DURATION:PT1H", "Zítra"))
    state, _ = fetch_agenda(FakeHttp(empty_day), CFG, None, NOW)
    panel = agenda_panel(state, NOW, CFG)
    assert panel["today_empty"] and [i["day"] for i in panel["items"]] == ["zítra"]
    many = cal(*[ev(f"m{i}", f"DTSTART;TZID=Europe/Prague:20261112T{12 + i // 2:02d}{30 * (i % 2):02d}00", "DURATION:PT15M",
                    f"Bod {i}") for i in range(9)])
    state, _ = fetch_agenda(FakeHttp(many), AgendaConfig(sources=(URL,), max_items=4), None, NOW)
    panel = agenda_panel(state, NOW, AgendaConfig(sources=(URL,), max_items=4))
    assert len(panel["items"]) == 4 and panel["more"] == 5


def test_private_mode_hides_titles_and_locations():
    state, _ = fetch_agenda(FakeHttp(DAY), CFG, None, NOW)
    panel = agenda_panel(state, NOW, AgendaConfig(sources=(URL,), private=True))
    assert {i["n"] for i in panel["items"]} == {"Obsazeno"} and not any("l" in i for i in panel["items"])


def test_outage_uses_limited_cache_marked_stale_and_url_never_logged(caplog):
    state, rec = fetch_agenda(FakeHttp(DAY), CFG, None, NOW)
    later = NOW + timedelta(minutes=20)
    with caplog.at_level(logging.WARNING):
        stale, rec2 = fetch_agenda(FakeHttp(fail=True), CFG, rec, later)
    assert stale["ok"] and stale["stale"] and stale["since"] == NOW.timestamp()
    assert URL not in caplog.text and "private-abc123" not in caplog.text and "agenda source #1 failed" in caplog.text
    panel = agenda_panel(stale, later, CFG)
    assert panel["stale"] and panel["as_of"] == "10:30"
    assert URL not in repr(rec2) and URL not in repr(panel) and URL not in repr(CFG)
    gone, _ = fetch_agenda(FakeHttp(fail=True), CFG, rec2, NOW + timedelta(hours=13))
    assert gone["ok"] is False and agenda_panel(gone, NOW, CFG) == {"ok": False}
    first, _ = fetch_agenda(FakeHttp(fail=True), CFG, None, NOW)
    assert first["ok"] is False


def test_refresh_interval_and_retry_backoff():
    http = FakeHttp(DAY)
    _, rec = fetch_agenda(http, CFG, None, NOW)
    fetch_agenda(http, CFG, rec, NOW + timedelta(minutes=5))
    assert len(http.calls) == 1
    fetch_agenda(http, CFG, rec, NOW + timedelta(minutes=16))
    assert len(http.calls) == 2


def test_credentials_in_url_become_basic_auth_and_webcal_is_https():
    url, headers = _request("webcal://user%40x:s3cret@cal.example.com/c.ics?k=1")
    assert url == "https://cal.example.com/c.ics?k=1" and headers["Authorization"].startswith("Basic ")
    assert "s3cret" not in url


def test_sources_only_from_local_config_or_environment(tmp_path):
    base = '[location]\nname = "Brno"\nlatitude = 1\nlongitude = 2\n[streams]\n'
    (tmp_path / "config.toml").write_text(base + '[agenda]\nsources = ["https://x/y.ics"]\n', encoding="utf-8")
    with pytest.raises(ConfigError, match="config.local.toml"):
        load_settings(tmp_path, env={}, secret_lookup=None)
    (tmp_path / "config.toml").write_text(base + "[agenda]\nmax_items = 4\n", encoding="utf-8")
    (tmp_path / "config.local.toml").write_text('[agenda]\nsources = ["https://a/b.ics"]\n', encoding="utf-8")
    s = load_settings(tmp_path, env={"MUSTHAVE_ICS_URLS": "webcal://c/d.ics https://a/b.ics"}, secret_lookup=None)
    assert s.agenda.sources == ("https://a/b.ics", "webcal://c/d.ics") and s.agenda.max_items == 4
    assert "a/b.ics" not in repr(s)
    with pytest.raises(ConfigError):
        parse_agenda({"sources": ["ftp://x"]})
    assert parse_agenda({}).enabled is False


def test_panel_without_configuration_says_so():
    from musthave.layout import View
    from musthave.payload import build_payload
    from musthave.screen import render_markup

    weather = {"ok": True, "temp": 1, "feels": 1, "hum": 1, "wind": 1, "label": "x", "days": []}
    p = build_payload(weather, {"ok": True, "items": []}, {"ok": True, "items": []}, NOW, max_bytes=None,
                      view=View(panels=("agenda", "kick")), extras={"agenda": None})
    assert "Kalendář není nastavený" in render_markup(p)
    state, _ = fetch_agenda(FakeHttp(DAY), CFG, None, NOW)
    p2 = build_payload(weather, {"ok": True, "items": []}, {"ok": True, "items": []}, NOW, max_bytes=None,
                       view=View(panels=("agenda",)), extras={"agenda": agenda_panel(state, NOW, CFG)})
    html = render_markup(p2)
    assert "TEĎ" in html and "Odpoledne" in html and "zítra" in html
