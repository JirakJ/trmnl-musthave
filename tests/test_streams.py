"""#15 Streamy: oblíbené, režimy all/live/live_summary, společný seznam, limit, výpadky."""

from datetime import datetime

import pytest

from musthave.config import ConfigError, parse_streams
from musthave.payload import build_payload
from musthave.screen import render_markup
from musthave.streams import StreamsConfig, build_section, fill_item_errors, ordered

WEATHER = {"ok": True, "temp": 5, "feels": 3, "hum": 80, "wind": 10, "label": "Zataženo", "days": []}


def it(i, live=False, v=0, n=None, g=None, s=None, **kw):
    d = {"id": i, "n": n or i.capitalize(), "live": live, **kw}
    if live:
        d["v"] = v
        if g:
            d["g"] = g
        if s:
            d["s"] = s
    return d


KICK = {"ok": True, "items": [it("alpha"), it("beta", True, 50, g="Valheim", s="14:00"), it("gamma", True, 900)]}
TWITCH = {"ok": True, "items": [it("alpha", True, 50, n="Alpha"), it("delta"), it("eps", True, 900)]}


def names(section):
    return [(i.get("pf"), i["n"]) for i in section["items"]]


def test_default_mode_keeps_previous_behaviour():
    s = build_section([("kick", KICK)], StreamsConfig(), 22)
    assert [i["n"] for i in s["items"]] == ["Gamma", "Beta", "Alpha"] and s["live"] == 2
    assert "id" not in s["items"][0] and "pf" not in s["items"][0]


def test_favorites_first_then_viewers_with_stable_tiebreak():
    cfg = StreamsConfig(favorites=("kick:beta", "kick:alpha"))
    s = build_section([("kick", KICK)], cfg, 22)
    assert [i["n"] for i in s["items"]] == ["Beta", "Gamma", "Alpha"]          # oblíbený live má přednost před 900 diváky
    assert s["items"][0]["fav"] and s["items"][2]["fav"] and "fav" not in s["items"][1]
    tie = ordered([("twitch", it("zed", True, 10)), ("kick", it("amy", True, 10)), ("kick", it("bob", True, 10))], ())
    assert [e[1]["id"] for e in tie] == ["amy", "bob", "zed"]                  # shoda diváků → platforma, id


def test_identity_includes_platform_not_display_name():
    cfg = StreamsConfig(favorites=("twitch:alpha",), combined=True)
    s = build_section([("kick", KICK), ("twitch", TWITCH)], cfg, 22)
    assert names(s)[0] == ("T", "Alpha")                                       # jen Twitch Alpha je oblíbený
    assert ("K", "Alpha") in names(s) and s["items"][0]["fav"] and not any(i.get("fav") for i in s["items"][1:])


def test_combined_list_marks_platform_and_keeps_details():
    s = build_section([("kick", KICK), ("twitch", TWITCH)], StreamsConfig(combined=True), 22)
    assert names(s)[:4] == [("K", "Gamma"), ("T", "Eps"), ("K", "Beta"), ("T", "Alpha")]
    beta = next(i for i in s["items"] if i["n"] == "Beta")
    assert beta["g"] == "Valheim" and beta["s"] == "14:00" and beta["v"] == 50 and s["live"] == 4


def test_live_only_mode_and_meaningful_empty_state():
    s = build_section([("kick", KICK)], StreamsConfig(mode="live"), 22)
    assert all(i["live"] for i in s["items"]) and "offline" not in s
    nobody = build_section([("kick", {"ok": True, "items": [it("a"), it("b")]})], StreamsConfig(mode="live"), 22)
    assert nobody["ok"] and nobody["items"] == [] and nobody["live"] == 0
    html = render_markup(build_payload(WEATHER, {"ok": True, "items": [it("a")]}, {"ok": True, "items": []},
                                       datetime(2026, 11, 12, 20), max_bytes=None, streams_cfg=StreamsConfig(mode="live")))
    assert "Nikdo nevysílá" in html


def test_live_summary_lists_offline_compactly_and_marks_unknown():
    src = {"ok": True, "items": [it("a"), it("b", True, 5), it("c", err=True)]}
    s = build_section([("kick", src)], StreamsConfig(mode="live_summary"), 22)
    assert [i["n"] for i in s["items"]] == ["B"] and s["offline"] == ["A", "C?"]


def test_limit_counts_hidden_items_and_keeps_live_first():
    many = {"ok": True, "items": [it(f"s{i}", True, i) for i in range(8)] + [it("off")]}
    s = build_section([("twitch", many)], StreamsConfig(max_items=5), 22)
    assert len(s["items"]) == 5 and s["more"] == 4 and s["live"] == 8
    assert [i["v"] for i in s["items"]] == [7, 6, 5, 4, 3]


def test_long_category_is_truncated():
    src = {"ok": True, "items": [it("a", True, 1, g="Warhammer 40,000: Space Marine II – Ultra Edition")]}
    assert build_section([("kick", src)], StreamsConfig(), 22)["items"][0]["g"].endswith("…")


def test_unavailable_platform_in_combined_list():
    s = build_section([("kick", {"ok": False, "items": []}), ("twitch", TWITCH)], StreamsConfig(combined=True), 22)
    assert s["ok"] and s["unavailable"] == ["Kick"] and all(i["pf"] == "T" for i in s["items"])
    none = build_section([("kick", {"ok": False}), ("twitch", {"ok": False})], StreamsConfig(combined=True), 22)
    assert none["ok"] is False


def test_single_channel_error_is_not_a_fresh_offline_transition():
    last = {"ts": 1000.0, "data": {"ok": True, "items": [it("beta", True, 50, g="Valheim")]}}
    fresh = {"ok": True, "items": [it("beta", err=True)]}
    filled = fill_item_errors(fresh, last, 1000.0 + 600, 1800)
    assert filled["items"][0]["live"] is True and filled["items"][0]["stale"] and filled["items"][0]["since"] == 1000.0
    # převzatý stav se neprodlužuje: další chyba po 31 min od skutečného získání → err
    again = fill_item_errors(fresh, {"ts": 1600.0, "data": filled}, 1000.0 + 1900, 1800)
    assert again["items"][0].get("err") and not again["items"][0]["live"]
    assert fill_item_errors(fresh, None, 1000.0, 1800) == fresh


def test_payload_panels_and_stale_time():
    cfg = StreamsConfig(combined=True)
    kick = {**KICK, "stale": True, "since": datetime(2026, 11, 12, 19, 40).timestamp()}
    p = build_payload(WEATHER, kick, TWITCH, datetime(2026, 11, 12, 20), max_bytes=None, streams_cfg=cfg)
    assert [x["key"] for x in p["panels"]] == ["streams"] and p["streams"]["as_of"] == "19:40"
    p2 = build_payload(WEATHER, KICK, TWITCH, datetime(2026, 11, 12, 20), max_bytes=None)
    assert [x["key"] for x in p2["panels"]] == ["kick", "twitch"] and "streams" not in p2
    html = render_markup(p)
    assert "STREAMY" in html and ">K<" in html and ">T<" in html


def test_config_parsing():
    cfg = parse_streams({"mode": "live_summary", "favorites": ["Kick:FattyPillow", "twitch:agraelus", "kick:fattypillow"],
                         "max_items": 6, "combined": True})
    assert cfg.favorites == ("kick:fattypillow", "twitch:agraelus") and cfg.max_items == 6 and cfg.combined
    with pytest.raises(ConfigError, match="favorites"):
        parse_streams({"favorites": ["youtube:x"]})
    with pytest.raises(ConfigError, match="mode"):
        parse_streams({"mode": "sometimes"})
    with pytest.raises(ConfigError):
        parse_streams({"max_items": -1})
