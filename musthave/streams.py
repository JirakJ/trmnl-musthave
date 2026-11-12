"""Streamy pro obrazovku: oblíbené kanály, režim zobrazení (vše / jen LIVE / LIVE + souhrn offline), společný seznam.

Identita kanálu je `platforma:id` (Kick slug / Twitch login z konfigurace), nikdy zobrazované jméno – dva kanály
se stejným jménem na Kicku a Twitchi jsou různé položky.

Stabilní řazení:
1. živé oblíbené v pořadí `favorites`,
2. ostatní živé podle diváků sestupně, při shodě podle platformy a id,
3. offline oblíbené v pořadí `favorites`, potom ostatní offline v pořadí konfigurace (Kick před Twitchem).
Limit `max_items` se uplatní po seřazení (živé mají přednost); zbytek je vidět jako `more`.
"""

from __future__ import annotations

from dataclasses import dataclass

PLATFORMS = {"kick": "Kick", "twitch": "Twitch"}
MODES = ("all", "live", "live_summary")


@dataclass(frozen=True)
class StreamsConfig:
    mode: str = "all"                         # all | live | live_summary
    favorites: tuple[str, ...] = ()           # ("kick:fattypillow", "twitch:agraelus")
    max_items: int = 0                        # 0 = bez limitu (na sekci)
    combined: bool = False                    # jeden společný seznam Kick + Twitch místo dvou sloupců


def parse_favorite(entry: str) -> str:
    platform, sep, ident = str(entry).strip().lower().partition(":")
    if not sep or platform not in PLATFORMS or not ident.strip():
        raise ValueError(f"favorites: {entry!r} musí být 'kick:<slug>' nebo 'twitch:<login>'")
    return f"{platform}:{ident.strip()}"


def fill_item_errors(fresh: dict, last: dict | None, now_ts: float, max_age_s: float) -> dict:
    """Jednotlivý kanál, jehož fetch selhal (`err`), převezme poslední známý stav (označený `stale`) místo
    falešného přechodu do offline. Bez historie zůstane `err` (na obrazovce „?“)."""
    if not fresh.get("ok") or not last or not last.get("data", {}).get("ok"):
        return fresh
    if now_ts - float(last.get("ts", 0)) > max_age_s:
        return fresh
    last_ts = float(last.get("ts", 0))
    previous = {i.get("id"): i for i in last["data"].get("items", []) if i.get("id") and not i.get("err")}
    items = []
    for item in fresh.get("items", []):
        prev = previous.get(item.get("id")) if item.get("err") else None
        # `since` = kdy byl stav kanálu skutečně získán; převzatý stav se tak neprodlužuje donekonečna
        since = float(prev.get("since") or last_ts) if prev else 0.0
        if prev and now_ts - since <= max_age_s:
            items.append({**prev, "stale": True, "since": since})
        else:
            items.append(item)
    return {**fresh, "items": items}


def _key(platform: str, item: dict) -> str:
    return f"{platform}:{(item.get('id') or item.get('n') or '').lower()}"


def ordered(entries: list[tuple[str, dict]], favorites: tuple[str, ...]) -> list[tuple[str, dict]]:
    """entries = [(platforma, položka)] v pořadí konfigurace → stabilně seřazené podle pravidel v hlavičce modulu."""
    fav = {key: i for i, key in enumerate(favorites)}
    indexed = list(enumerate(entries))

    def live_key(x):
        order, (pf, item) = x
        k = _key(pf, item)
        return (0, fav[k], 0, "", "") if k in fav else (1, 0, -int(item.get("v") or 0), pf, k)

    def off_key(x):
        order, (pf, item) = x
        k = _key(pf, item)
        return (0, fav[k], 0) if k in fav else (1, 0, order)

    live = sorted((x for x in indexed if x[1][1].get("live")), key=live_key)
    off = sorted((x for x in indexed if not x[1][1].get("live")), key=off_key)
    return [e for _, e in live + off]


def _truncate(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[: limit - 1] + "…"


def build_section(sources: list[tuple[str, dict]], cfg: StreamsConfig, game_max: int) -> dict:
    """Sekce pro šablonu z jednoho (kick/twitch) nebo obou zdrojů (společný seznam).

    Výstup: ok, items (n, live, v, g, s, pf; stale/err u jednotlivých), live (počet živých), mode,
    offline (jména v režimu live_summary), more (skryté položky), unavailable (platformy bez dat)."""
    combined = len(sources) > 1
    ok_sources = [(pf, src) for pf, src in sources if src.get("ok")]
    entries = [(pf, item) for pf, src in ok_sources for item in src.get("items", [])]
    entries = ordered(entries, cfg.favorites)
    fav = set(cfg.favorites)
    items, offline = [], []
    for pf, raw in entries:
        item = {k: v for k, v in raw.items() if k not in ("id", "since")}
        if "g" in item:
            item["g"] = _truncate(item["g"], game_max)
        if combined:
            item["pf"] = PLATFORMS[pf][0]   # K / T
        if _key(pf, raw) in fav:
            item["fav"] = True
        if not item.get("live") and cfg.mode != "all":
            if cfg.mode == "live_summary":
                offline.append(item["n"] + ("?" if item.get("err") else ""))
            continue
        items.append(item)
    out: dict = {"ok": bool(ok_sources), "items": items, "live": sum(1 for i in items if i.get("live")), "mode": cfg.mode}
    if cfg.max_items and len(items) > cfg.max_items:
        out["more"] = len(items) - cfg.max_items
        out["items"] = items[: cfg.max_items]
    if offline:
        out["offline"] = offline
    if combined:
        missing = [PLATFORMS[pf] for pf, src in sources if not src.get("ok")]
        if missing:
            out["unavailable"] = missing
    if any(src.get("fallback") for _, src in sources):
        out["fallback"] = True
    return out
