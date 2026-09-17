"""Stav zařízení (per MAC) v JSON souboru."""

from __future__ import annotations

import json
import os
from dataclasses import asdict
from pathlib import Path

from .policy import DeviceState


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
        return DeviceState(**{k: raw.get(k) for k in ("frame_id", "last_full_at", "last_seen_at", "fw_version")},
                           partials_since_full=int(raw.get("partials_since_full") or 0))

    def save(self, mac: str, state: DeviceState) -> None:
        self._data[mac] = asdict(state)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self._data, indent=1), encoding="utf-8")
        os.replace(tmp, self.path)

    def frame_ids(self) -> set[str]:
        """Snímky, které některé zařízení právě zobrazuje (FrameStore je nesmí vyřadit)."""
        return {st.frame_id for st in (self.get(mac) for mac in self._data) if st.frame_id}

    def latest_seen(self) -> DeviceState | None:
        best = None
        for mac in self._data:
            st = self.get(mac)
            if st.last_seen_at and (best is None or st.last_seen_at > best.last_seen_at):
                best = st
        return best
