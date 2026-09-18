"""Poslední dobrá data zdrojů: když fetch selže, ukáže se poslední známý stav označený „stale“.

Jeden výpadek Wi-Fi nesmí na obrazovce vyrobit „Kick nedostupný“ s prázdným sloupcem (a tím i zbytečné
překreslení e-inku). Každý zdroj má vlastní maximální stáří; po jeho překročení se ukáže stav „nedostupné“.
"""

from __future__ import annotations

STALE_MAX_S = {
    "weather": 3 * 3600,   # předpověď se mění pomalu
    "kick": 30 * 60,       # live stav streamů zastarává rychle
    "twitch": 30 * 60,
}


def with_last_good(name: str, fresh: dict, last: dict | None, now_ts: float) -> tuple[dict, dict | None]:
    """(data k zobrazení, záznam k uložení). Záznam = {"ts", "data"} posledního úspěšného fetche."""
    if fresh.get("ok"):
        return fresh, {"ts": now_ts, "data": fresh}
    max_age = STALE_MAX_S[name]
    if last and last.get("data", {}).get("ok") and now_ts - float(last.get("ts", 0)) <= max_age:
        return {**last["data"], "stale": True}, last
    return fresh, last


def all_down(*sources: dict) -> bool:
    """True, když žádný zdroj nemá čerstvá data (vše selhalo nebo jede ze zálohy) – typicky výpadek sítě."""
    return all(not s.get("ok") or s.get("stale") for s in sources)
