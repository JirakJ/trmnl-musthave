"""Stav zařízení (per MAC) v JSON souboru."""

from __future__ import annotations

import json
import os
import time
from dataclasses import asdict
from pathlib import Path

from .policy import DeviceState

PIN_MAX_AGE_S = 48 * 3600   # snímky drží jen zařízení viděná v posledních 48 h
PIN_MAX_DEVICES = 8         # a nejvýš tolik naposledy viděných zařízení


class DeviceRegistry:
    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self._data: dict[str, dict] = {}
        try:
            self._data = json.loads(self.path.read_text(encoding="utf-8"))
        except (FileNotFoundError, ValueError):
            self._data = {}

    def get(self, mac: str) -> DeviceState:
        raw = self._data.get(mac) or {}
        return DeviceState(**{k: raw.get(k) for k in ("frame_id", "target_frame_id", "last_full_at", "last_seen_at",
                                                      "fw_version", "voltage")},
                           partials_since_full=int(raw.get("partials_since_full") or 0))

    def save(self, mac: str, state: DeviceState) -> None:
        self._data[mac] = asdict(state)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self._data, indent=1), encoding="utf-8")
        os.replace(tmp, self.path)

    def states(self) -> list[DeviceState]:
        return [self.get(mac) for mac in self._data]

    def frame_ids(self, now: float | None = None, max_age_s: float = PIN_MAX_AGE_S, limit: int = PIN_MAX_DEVICES) -> set[str]:
        """Snímky, které zařízení zobrazují nebo právě dostala (FrameStore je nesmí vyřadit).

        Jen zařízení viděná v posledních `max_age_s` a nejvýš `limit` naposledy viděných, aby zapomenutá
        nebo podvržená ID nedržela snímky navždy."""
        now = time.time() if now is None else now
        live = sorted((st for st in self.states() if st.last_seen_at and now - st.last_seen_at <= max_age_s),
                      key=lambda st: st.last_seen_at, reverse=True)[:limit]
        return {fid for st in live for fid in (st.frame_id, st.target_frame_id) if fid}

    def latest_seen(self) -> DeviceState | None:
        best = None
        for st in self.states():
            if st.last_seen_at and (best is None or st.last_seen_at > best.last_seen_at):
                best = st
        return best
