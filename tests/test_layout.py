"""#13 Panely a profily: výchozí rozložení, validace, skrývání prázdných panelů, plán profilů, frekvence přepnutí."""

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from musthave.config import ConfigError, load_settings
from musthave.layout import (LayoutConfig, LayoutError, View, active_view, parse_layout, profile_matches,
                             resolve_panels)
from musthave.payload import build_payload
from musthave.screen import render_markup

TZ = ZoneInfo("Europe/Prague")
WEATHER = {"ok": True, "temp": 5, "feels": 3, "hum": 80, "wind": 10, "label": "Zataženo", "days": []}
LIVE = {"ok": True, "items": [{"id": "a", "n": "A", "live": True, "v": 5}]}
OFF = {"ok": True, "items": [{"id": "b", "n": "B", "live": False}]}

PROFILES = {
    "panels": ["kick", "twitch"],
    "min_switch_minutes": 30,
    "profile": [
        {"name": "práce", "days": ["po", "út", "st", "čt", "pá"], "from": "07:00", "to": "17:00",
         "panels": ["twitch"], "weather": "compact"},
        {"name": "noc", "days": ["pá", "so"], "from": "22:00", "to": "02:00", "panels": ["streams"], "countdown": False},
        {"name": "překryv", "days": ["po"], "from": "08:00", "to": "09:00", "panels": ["kick"]},
    ],
}


def at(day, h, m=0):
    return datetime(2026, 11, day, h, m, tzinfo=TZ)  # 9. 11. 2026 = pondělí


def test_without_config_the_layout_is_unchanged():
    cfg = parse_layout(None)
    assert cfg.base == View(panels=("kick", "twitch")) and not cfg.profiles and not cfg.configured
    assert parse_layout(None, combined_streams=True).base.panels == ("streams",)
    p = build_payload(WEATHER, LIVE, LIVE, at(9, 12), max_bytes=None)
    assert [x["key"] for x in p["panels"]] == ["kick", "twitch"] and p["layout"] == {"weather": "full"}


@pytest.mark.parametrize("raw,msg", [
    ({"panels": ["kick", "youtube"]}, "neznámý panel"),
    ({"panels": ["kick", "kick"]}, "dvakrát"),
    ({"panels": []}, "aspoň jeden"),
    ({"panels": ["kick", "twitch", "streams"]}, "nelze kombinovat"),
    ({"weather": "big"}, "weather"),
    ({"widths": {"kick": 5}}, "1–3"),
    ({"hide_empty": ["kick"], "shrink_empty": ["kick"]}, "zároveň"),
    ({"colour": "red"}, "neznámé klíče"),
    ({"profile": [{"name": "x", "from": "25:00"}]}, "HH:MM"),
    ({"profile": [{"name": "x", "days": ["funday"]}]}, "neznámý den"),
    ({"profile": [{"days": ["po"]}]}, "chybí name"),
    ({"profile": [{"name": "x"}, {"name": "x"}]}, "dvakrát"),
])
def test_invalid_layout_has_understandable_error(raw, msg):
    with pytest.raises(LayoutError, match=msg):
        parse_layout(raw)


def test_layout_errors_surface_as_config_errors(tmp_path):
    (tmp_path / "config.toml").write_text('[location]\nname = "Brno"\nlatitude = 1\nlongitude = 2\n[streams]\n'
                                          '[layout]\npanels = ["nope"]\n', encoding="utf-8")
    with pytest.raises(ConfigError, match="neznámý panel"):
        load_settings(tmp_path, env={}, secret_lookup=None)


def test_profile_window_and_days_including_midnight():
    cfg = parse_layout(PROFILES)
    work, night = cfg.profiles[0], cfg.profiles[1]
    assert profile_matches(work, at(9, 7)) and not profile_matches(work, at(9, 17)) and not profile_matches(work, at(14, 10))
    assert profile_matches(night, at(13, 23))           # pátek 23:00
    assert profile_matches(night, at(14, 1, 30))        # sobota 01:30 = pokračování pátečního okna
    assert profile_matches(night, at(15, 1, 30))        # neděle 01:30 = pokračování sobotního okna
    assert not profile_matches(night, at(16, 1, 30))    # pondělí 01:30: neděle v days není
    assert not profile_matches(night, at(13, 2, 0))     # pátek 02:00: čtvrtek v days není a okno končí


def test_overlap_first_profile_wins_and_fallback_to_base():
    cfg = parse_layout(PROFILES)
    view, name, _ = active_view(cfg, at(9, 8, 30), None)
    assert name == "práce" and view.panels == ("twitch",) and view.weather == "compact"
    view, name, _ = active_view(cfg, at(9, 19), None)
    assert name is None and view.panels == ("kick", "twitch")


def test_profile_switch_respects_min_interval_not_a_carousel():
    cfg = parse_layout(PROFILES)
    _, name, rec = active_view(cfg, at(9, 16, 50), None)
    assert name == "práce"
    _, name, rec2 = active_view(cfg, at(9, 17, 5), rec)       # okno skončilo, ale přepnuto před 15 min
    assert name == "práce" and rec2 == rec
    _, name, rec3 = active_view(cfg, at(9, 17, 21), rec)      # 31 min od přepnutí → základní rozložení
    assert name is None and rec3["since"] == at(9, 17, 21).timestamp()
    _, name, rec4 = active_view(cfg, at(9, 17, 22), rec3)
    assert name is None and rec4 is rec3                       # beze změny = žádné nové přepnutí


def test_profile_removed_from_config_switches_immediately():
    cfg = parse_layout(PROFILES)
    _, name, _ = active_view(cfg, at(9, 19), {"profile": "smazaný", "since": at(9, 18, 59).timestamp()})
    assert name is None


def test_timezone_matters_for_profile_selection():
    cfg = parse_layout(PROFILES)
    utc_evening = datetime(2026, 11, 9, 6, 30, tzinfo=ZoneInfo("UTC"))   # 07:30 v Praze
    assert active_view(cfg, utc_evening.astimezone(TZ), None)[1] == "práce"


def test_empty_panels_hide_or_shrink_and_free_space():
    view = View(panels=("kick", "twitch"), hide_empty=("kick",))
    assert [p["key"] for p in resolve_panels(view, {"kick": OFF, "twitch": LIVE})] == ["twitch"]
    shrink = resolve_panels(View(panels=("kick", "twitch"), shrink_empty=("kick",)), {"kick": OFF, "twitch": LIVE})
    assert shrink[0]["compact"] and shrink[0]["w"] == 0 and shrink[1]["w"] == 1
    both = resolve_panels(View(panels=("kick", "twitch"), hide_empty=("kick", "twitch")), {"kick": OFF, "twitch": OFF})
    assert [p["key"] for p in both] == ["kick"]                 # nikdy prázdná obrazovka
    unavailable = resolve_panels(view, {"kick": {"ok": False}, "twitch": LIVE})
    assert [p["key"] for p in unavailable] == ["kick", "twitch"]  # výpadek se neschovává jako „prázdno“


def test_widths_and_payload_variants():
    view = View(panels=("twitch", "kick"), widths=(("twitch", 2),), weather="off", countdown=False)
    p = build_payload(WEATHER, LIVE, LIVE, at(9, 12), max_bytes=None, view=view, profile="večer")
    assert [(x["key"], x["w"]) for x in p["panels"]] == [("twitch", 2), ("kick", 1)]
    assert p["layout"] == {"weather": "off", "profile": "večer"} and p["countdowns"] == []
    html = render_markup(p)
    assert "mh-weather" not in html.split("<div class=\"layout")[1] and "flex-grow: 2" in html


def test_full_template_renders_every_variant():
    for view in (View(), View(weather="compact"), View(weather="off"), View(panels=("streams",)),
                 View(panels=("kick", "twitch"), shrink_empty=("kick",))):
        p = build_payload(WEATHER, OFF, LIVE, at(9, 12), max_bytes=None, view=view)
        html = render_markup(p)
        assert "MUST-HAVE" in html and "Kick nedostupný" not in html


def test_settings_carry_layout(tmp_path):
    (tmp_path / "config.toml").write_text('[location]\nname = "Brno"\nlatitude = 1\nlongitude = 2\n[streams]\ncombined = true\n',
                                          encoding="utf-8")
    s = load_settings(tmp_path, env={}, secret_lookup=None)
    assert isinstance(s.layout, LayoutConfig) and s.layout.base.panels == ("streams",)


def test_half_and_quadrant_templates_still_render_the_new_payload():
    for layout in ("half_horizontal", "half_vertical", "quadrant"):
        p = build_payload(WEATHER, LIVE, OFF, at(9, 12), max_bytes=None, view=View(panels=("streams",), weather="off"),
                          location="Brno")
        html = render_markup(p, layout)
        assert "KICK" in html and "TWITCH" in html, layout
