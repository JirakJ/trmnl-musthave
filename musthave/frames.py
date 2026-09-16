"""1-bit bitmapa 800×480 (řádek 100 B, bit 1 = bílá) a úložiště posledních snímků pro diff."""

from __future__ import annotations

import hashlib
import io
import json
import os
from pathlib import Path

WIDTH, HEIGHT = 800, 480
ROW_BYTES = WIDTH // 8
BITMAP_BYTES = ROW_BYTES * HEIGHT


def png_to_bitmap(png: bytes) -> bytes:
    """PNG (libovolný mód) → 48 000 B packed 1 bpp, MSB první, 1 = bílá. Jiná velikost se přeškáluje."""
    from PIL import Image

    img = Image.open(io.BytesIO(png))
    if img.size != (WIDTH, HEIGHT):
        img = img.convert("L").resize((WIDTH, HEIGHT), Image.Resampling.LANCZOS)
    if img.mode != "1":
        img = img.convert("1")
    data = img.tobytes()
    assert len(data) == BITMAP_BYTES, len(data)
    return data


def bitmap_to_png(bitmap: bytes) -> bytes:
    from PIL import Image

    if len(bitmap) != BITMAP_BYTES:
        raise ValueError(f"bitmap must be {BITMAP_BYTES} B, got {len(bitmap)}")
    img = Image.frombytes("1", (WIDTH, HEIGHT), bitmap)
    buf = io.BytesIO()
    img.save(buf, format="PNG", optimize=True)
    return buf.getvalue()


def frame_id(bitmap: bytes) -> str:
    return "musthave-" + hashlib.sha256(bitmap).hexdigest()[:10]


class FrameStore:
    """Posledních `keep` bitmap na disku: <dir>/<id>.bmp1 + index.json (pořadí vložení)."""

    def __init__(self, directory: Path, keep: int = 8) -> None:
        self.dir = Path(directory)
        self.dir.mkdir(parents=True, exist_ok=True)
        self.keep = keep
        self.index = self.dir / "index.json"
        self._ids: list[str] = self._load_index()

    def _load_index(self) -> list[str]:
        try:
            ids = json.loads(self.index.read_text(encoding="utf-8"))
        except (FileNotFoundError, ValueError):
            return []
        return [i for i in ids if (self.dir / f"{i}.bmp1").exists()]

    def _save_index(self) -> None:
        tmp = self.dir / ".index.json.tmp"
        tmp.write_text(json.dumps(self._ids), encoding="utf-8")
        os.replace(tmp, self.index)

    def put(self, bitmap: bytes) -> str:
        if len(bitmap) != BITMAP_BYTES:
            raise ValueError(f"bitmap must be {BITMAP_BYTES} B, got {len(bitmap)}")
        fid = frame_id(bitmap)
        if fid in self._ids:
            self._ids.remove(fid)
        else:
            tmp = self.dir / f".{fid}.tmp"
            tmp.write_bytes(bitmap)
            os.replace(tmp, self.dir / f"{fid}.bmp1")
        self._ids.append(fid)
        while len(self._ids) > self.keep:
            old = self._ids.pop(0)
            try:
                (self.dir / f"{old}.bmp1").unlink()
            except FileNotFoundError:
                pass
        self._save_index()
        return fid

    def get(self, fid: str) -> bytes | None:
        if fid not in self._ids:
            return None
        try:
            return (self.dir / f"{fid}.bmp1").read_bytes()
        except FileNotFoundError:
            return None

    def latest(self) -> str | None:
        return self._ids[-1] if self._ids else None

    def png(self, fid: str) -> bytes | None:
        bm = self.get(fid)
        return bitmap_to_png(bm) if bm is not None else None
