"""#16 Priority a připomínky: jednorázové/týdenní/měsíční, 29.–31., předstih, pořadí, limity, splnění výskytu."""

from datetime import date, datetime
from zoneinfo import ZoneInfo

import pytest

from musthave.__main__ import mark_done
from musthave.config import ConfigError, load_settings
from musthave.layout import View
from musthave.payload import build_payload
from musthave.reminders import DoneStore, occurrence_key, parse_reminders, reminders_panel
from musthave.screen import render_markup

TZ = ZoneInfo("Europe/Prague")


def at(y, m, d, h=9):
    return datetime(y, m, d, h, tzinfo=TZ)


def cfg(reminders=(), priorities=(), **kw):
    return parse_reminders(list(reminders), list(priorities), kw)


def test_one_off_weekly_monthly_and_lead_days():
    c = cfg([{"name": "Zubař", "date": date(2026, 11, 14), "lead_days": 2},
             {"name": "Koš", "weekly": ["út", "pá"]},
             {"name": "Nájem", "monthly": 15, "lead_days": 3}])
    assert [(i["n"], i["when"]) for i in reminders_panel(c, at(2026, 11, 12))["items"]] == [
        ("Zubař", "za 2 dny"), ("Nájem", "za 3 dny")]                 # čtvrtek: koš až v pátek, bez předstihu
    p = reminders_panel(c, at(2026, 11, 13))
    assert [(i["n"], i["when"], i.get("today", False)) for i in p["items"]] == [
        ("Koš", "dnes", True), ("Zubař", "zítra", False), ("Nájem", "za 2 dny", False)]
    assert reminders_panel(c, at(2026, 11, 15))["items"][0] == {"n": "Nájem", "when": "dnes", "today": True}
    assert all(i["n"] != "Zubař" for i in reminders_panel(c, at(2026, 11, 15))["items"])   # po termínu zmizí


def test_month_days_29_to_31_rule_is_explicit():
    last = cfg([{"name": "Faktura", "monthly": 31}])
    skip = cfg([{"name": "Faktura", "monthly": 31, "short_month": "skip"}])
    assert reminders_panel(last, at(2026, 4, 30))["items"][0]["when"] == "dnes"          # 31. → 30. 4.
    assert reminders_panel(last, at(2026, 2, 28))["items"][0]["when"] == "dnes"          # 31. → 28. 2.
    assert reminders_panel(last, at(2028, 2, 29))["items"][0]["when"] == "dnes"          # přestupný rok
    assert reminders_panel(skip, at(2026, 4, 30))["items"] == []
    assert reminders_panel(skip, at(2026, 5, 31))["items"][0]["when"] == "dnes"
    m29 = cfg([{"name": "X", "monthly": 29}])
    assert reminders_panel(m29, at(2026, 2, 28))["items"][0]["when"] == "dnes"
    assert reminders_panel(m29, at(2026, 3, 29))["items"][0]["when"] == "dnes"


def test_month_start_and_year_end_with_lead():
    c = cfg([{"name": "Silvestr", "date": date(2026, 12, 31), "lead_days": 3},
             {"name": "Daně", "monthly": 1, "lead_days": 2},
             {"name": "Úklid", "weekly": "pá", "lead_days": 1}])
    p = reminders_panel(c, at(2026, 12, 30))
    assert [(i["n"], i["when"]) for i in p["items"]] == [("Silvestr", "zítra"), ("Daně", "za 2 dny")]
    p = reminders_panel(c, at(2027, 1, 1))     # pátek
    assert [(i["n"], i["when"]) for i in p["items"]] == [("Daně", "dnes"), ("Úklid", "dnes")]


def test_priorities_limit_order_and_dates():
    c = cfg(priorities=["Dopsat nabídku", {"text": "Zavolat do banky", "date": date(2026, 11, 12)},
                        {"text": "Jen zítra", "date": date(2026, 11, 13)}, "Třetí", "Čtvrtá (nevejde se)"])
    p = reminders_panel(c, at(2026, 11, 12))
    assert p["priorities"] == ["Dopsat nabídku", "Zavolat do banky", "Třetí"]


def test_item_limit_and_long_texts():
    c = cfg([{"name": f"Připomínka číslo {i} s opravdu velmi dlouhým názvem", "weekly": "čt"} for i in range(8)], max_items=4)
    p = reminders_panel(c, at(2026, 11, 12))
    assert len(p["items"]) == 4 and p["more"] == 4 and all(len(i["n"]) <= 40 for i in p["items"])
    assert [i["n"][:19] for i in p["items"]] == [f"Připomínka číslo {i} " for i in range(4)]   # stabilní pořadí


def test_done_is_per_occurrence_and_does_not_carry_over(tmp_path):
    c = cfg([{"name": "Koš", "weekly": "čt"}, {"name": "Pití", "weekly": "čt"}])
    store = DoneStore(tmp_path / "done.json")
    store.set(occurrence_key("Koš", date(2026, 11, 12)), True, at(2026, 11, 12))
    p = reminders_panel(c, at(2026, 11, 12), store.load())
    assert [(i["n"], i.get("done", False)) for i in p["items"]] == [("Pití", False), ("Koš", True)]  # splněné na konec
    assert not any(i.get("done") for i in reminders_panel(c, at(2026, 11, 19), store.load())["items"])
    store.set(occurrence_key("Koš", date(2026, 11, 12)), False, at(2026, 11, 12))
    assert store.load() == {}


def test_done_store_prunes_old_entries(tmp_path):
    store = DoneStore(tmp_path / "done.json")
    store.set(occurrence_key("A", date(2026, 1, 1)), True, at(2026, 1, 1))
    store.set(occurrence_key("B", date(2026, 11, 12)), True, at(2026, 11, 12))
    assert list(store.load()) == ["B|2026-11-12"]


def test_empty_day_and_disabled():
    assert reminders_panel(cfg(), at(2026, 11, 12)) is None
    p = reminders_panel(cfg([{"name": "Koš", "weekly": "po"}]), at(2026, 11, 12))
    assert p == {"ok": True, "priorities": [], "items": []}
    weather = {"ok": True, "temp": 1, "feels": 1, "hum": 1, "wind": 1, "label": "x", "days": []}
    html = render_markup(build_payload(weather, {"ok": True, "items": []}, {"ok": True, "items": []}, at(2026, 11, 12),
                                       max_bytes=None, view=View(panels=("reminders",)), extras={"reminders": p}))
    assert "Dnes nic naplánovaného" in html


@pytest.mark.parametrize("entry,msg", [
    ({"name": "X"}, "právě jedno"),
    ({"name": "X", "weekly": "po", "monthly": 1}, "právě jedno"),
    ({"name": "X", "weekly": "funday"}, "neznámý den"),
    ({"name": "X", "monthly": 32}, "1–31"),
    ({"name": "X", "monthly": 31, "short_month": "next"}, "short_month"),
    ({"name": "X", "date": "příští úterý"}, "YYYY-MM-DD"),
    ({"weekly": "po"}, "chybí name"),
])
def test_validation(entry, msg):
    with pytest.raises(ValueError, match=msg):
        parse_reminders([entry], [])


def test_config_and_cli(tmp_path, capsys):
    (tmp_path / "config.toml").write_text(
        '[location]\nname = "Brno"\nlatitude = 1\nlongitude = 2\n[streams]\n'
        '[[reminder]]\nname = "Koš"\nweekly = "čt"\n[[priority]]\ntext = "Nabídka"\n[[countdown]]\nname = "GTA VI"\ndate = 2026-11-19\n',
        encoding="utf-8")
    (tmp_path / "config.local.toml").write_text('[[reminder]]\nname = "Lokální"\nmonthly = 1\n', encoding="utf-8")
    s = load_settings(tmp_path, env={}, secret_lookup=None)
    assert [r.name for r in s.reminders.reminders] == ["Koš", "Lokální"] and len(s.countdowns) == 1  # nesměšuje se s odpočty
    assert mark_done(tmp_path, "Koš", "2026-11-12") == 0
    assert DoneStore(tmp_path / "state" / "done.json").load().keys() == {"Koš|2026-11-12"}
    assert mark_done(tmp_path, "Neznámá", None) == 2
    (tmp_path / "config.local.toml").write_text('[[reminder]]\nname = "Zlá"\n', encoding="utf-8")
    with pytest.raises(ConfigError, match="právě jedno"):
        load_settings(tmp_path, env={}, secret_lookup=None)
