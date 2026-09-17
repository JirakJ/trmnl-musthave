"""Stav zařízení (per MAC) v JSON souboru."""

from __future__ import annotations

import json
import os
import threading
import time
from dataclasses import asdict, fields
from pathlib import Path

from .policy import DeviceState

PIN_MAX_AGE_S = 48 * 3600   # snímky drží jen zařízení viděná v posledních 48 h

# Jeden zámek pro celý proces: HTTP vlákna (ThreadingHTTPServer) i render smyčka pracují nad stejným souborem
# a každé čtení/zápis musí být atomické vůči ostatním (jinak vznikne prázdný devices.json a ztráta připnutí).
LOCK = threading.RLock()
_FIELDS = [f.name for f in fields(DeviceState)]


class DeviceRegistry:
    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self._data: dict[str, dict] = {}
        with LOCK:
            try:
                self._data = json.loads(self.path.read_text(encoding="utf-8"))
            except (FileNotFoundError, ValueError):
                self._data = {}

    def get(self, mac: str) -> DeviceState:
        with LOCK:
            raw = self._data.get(mac) or {}
        values = {k: raw.get(k) for k in _FIELDS if k != "partials_since_full"}
        return DeviceState(**values, partials_since_full=int(raw.get("partials_since_full") or 0))

    def save(self, mac: str, state: DeviceState) -> None:
        with LOCK:
            self._data[mac] = asdict(state)
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_name(f".{self.path.name}.{os.getpid()}.{threading.get_ident()}.tmp")
            tmp.write_text(json.dumps(self._data, indent=1), encoding="utf-8")
            os.replace(tmp, self.path)

    def states(self) -> list[DeviceState]:
        with LOCK:
            macs = list(self._data)
        return [self.get(mac) for mac in macs]

    def frame_ids(self, now: float | None = None, max_age_s: float = PIN_MAX_AGE_S) -> set[str]:
        """Snímky, které zařízení zobrazují nebo právě dostala (FrameStore je nesmí vyřadit).

        Jen zařízení viděná v posledních `max_age_s`, aby zapomenutá ID nedržela snímky navždy. (Podvržená ID
        s konstantním klíčem na LAN nejsou v modelu hrozeb; server je určen pro důvěryhodnou domácí síť.)"""
        now = time.time() if now is None else now
        live = [st for st in self.states() if isinstance(st.last_seen_at, (int, float)) and now - st.last_seen_at <= max_age_s]
        return {fid for st in live for fid in (st.frame_id, st.target_frame_id) if fid}

    def latest_seen(self) -> DeviceState | None:
        best = None
        for st in self.states():
            if st.last_seen_at and (best is None or st.last_seen_at > best.last_seen_at):
                best = st
        return best
