"""Rozdíl dvou 1-bit bitmap → špinavé dlaždice 8×8 → obdélníky → binární MHR1 blob pro firmware.

MHR1: b"MHR1" | u8 count | count × { u16 x, u16 y, u16 w, u16 h (LE) | old[h·w/8] | new[h·w/8] }
x a w jsou násobky 8, řádky obdélníku jsou packed 1 bpp (MSB první, 1 = bílá), odshora dolů.
"""

from __future__ import annotations

import struct
from dataclasses import dataclass

from .frames import BITMAP_BYTES, HEIGHT, ROW_BYTES, WIDTH

TILE = 8
TILES_X, TILES_Y = WIDTH // TILE, HEIGHT // TILE
HEADER = b"MHR1"


@dataclass(frozen=True, order=True)
class Rect:
    x: int
    y: int
    w: int
    h: int

    @property
    def x1(self) -> int:
        return self.x + self.w

    @property
    def y1(self) -> int:
        return self.y + self.h


def dirty_tiles(old: bytes, new: bytes) -> set[tuple[int, int]]:
    """Dlaždice (tx, ty), kde se liší aspoň jeden bajt (1 bajt = 8 px na šířku, dlaždice = 8 řádků)."""
    if len(old) != BITMAP_BYTES or len(new) != BITMAP_BYTES:
        raise ValueError("bitmaps must be 800x480 1bpp")
    if old == new:
        return set()
    tiles: set[tuple[int, int]] = set()
    for y in range(HEIGHT):
        o = old[y * ROW_BYTES:(y + 1) * ROW_BYTES]
        n = new[y * ROW_BYTES:(y + 1) * ROW_BYTES]
        if o == n:
            continue
        ty = y // TILE
        for bx in range(ROW_BYTES):
            if o[bx] != n[bx]:
                tiles.add((bx, ty))
    return tiles


def _union(a: Rect, b: Rect) -> Rect:
    x, y = min(a.x, b.x), min(a.y, b.y)
    return Rect(x, y, max(a.x1, b.x1) - x, max(a.y1, b.y1) - y)


def _touch_or_overlap_vertically(a: Rect, b: Rect) -> bool:
    return not (a.x1 < b.x or b.x1 < a.x) and not (a.y1 < b.y or b.y1 < a.y)


def merge_rects(tiles: set[tuple[int, int]], max_rects: int = 4) -> list[Rect]:
    """1) pásy po řádcích dlaždic, 2) sloučit pásy, které se v x překrývají a v y dotýkají, 3) doplnit do max_rects."""
    if not tiles:
        return []
    rows: dict[int, list[int]] = {}
    for tx, ty in tiles:
        rows.setdefault(ty, []).append(tx)
    rects = [Rect(min(xs) * TILE, ty * TILE, (max(xs) - min(xs) + 1) * TILE, TILE) for ty, xs in sorted(rows.items())]

    merged = True
    while merged:
        merged = False
        for i in range(len(rects)):
            for j in range(i + 1, len(rects)):
                if _touch_or_overlap_vertically(rects[i], rects[j]):
                    rects[i] = _union(rects[i], rects[j])
                    del rects[j]
                    merged = True
                    break
            if merged:
                break

    while len(rects) > max_rects:
        best, best_cost = None, None
        for i in range(len(rects)):
            for j in range(i + 1, len(rects)):
                u = _union(rects[i], rects[j])
                cost = u.w * u.h - rects[i].w * rects[i].h - rects[j].w * rects[j].h
                if best_cost is None or cost < best_cost:
                    best, best_cost = (i, j), cost
        i, j = best
        rects[i] = _union(rects[i], rects[j])
        del rects[j]
        # sloučením mohly vzniknout nové překryvy
        merged = True
        while merged:
            merged = False
            for a in range(len(rects)):
                for b in range(a + 1, len(rects)):
                    if _touch_or_overlap_vertically(rects[a], rects[b]):
                        rects[a] = _union(rects[a], rects[b])
                        del rects[b]
                        merged = True
                        break
                if merged:
                    break
    return sorted(rects)


def area(rects) -> int:
    return sum(r.w * r.h for r in rects)


def _validate(rect: Rect) -> None:
    if rect.x % 8 or rect.w % 8 or rect.w <= 0 or rect.h <= 0:
        raise ValueError(f"rect must be 8px aligned with positive size: {rect}")
    if rect.x < 0 or rect.y < 0 or rect.x1 > WIDTH or rect.y1 > HEIGHT:
        raise ValueError(f"rect outside canvas: {rect}")


def _crop(bitmap: bytes, rect: Rect) -> bytes:
    bx, bw = rect.x // 8, rect.w // 8
    return b"".join(bitmap[(rect.y + r) * ROW_BYTES + bx:(rect.y + r) * ROW_BYTES + bx + bw] for r in range(rect.h))


def encode_regions(old: bytes, new: bytes, rects: list[Rect]) -> bytes:
    if not 1 <= len(rects) <= 255:
        raise ValueError("1..255 rects")
    out = bytearray(HEADER)
    out.append(len(rects))
    for rect in rects:
        _validate(rect)
        out += struct.pack("<HHHH", rect.x, rect.y, rect.w, rect.h)
        out += _crop(old, rect)
        out += _crop(new, rect)
    return bytes(out)


def decode_regions(blob: bytes) -> list[tuple[Rect, bytes, bytes]]:
    """Referenční dekodér (testy, kontrola firmware)."""
    if blob[:4] != HEADER:
        raise ValueError("bad magic")
    count, pos, out = blob[4], 5, []
    for _ in range(count):
        x, y, w, h = struct.unpack("<HHHH", blob[pos:pos + 8])
        pos += 8
        rect = Rect(x, y, w, h)
        _validate(rect)
        n = h * w // 8
        out.append((rect, blob[pos:pos + n], blob[pos + n:pos + 2 * n]))
        pos += 2 * n
    if pos != len(blob):
        raise ValueError("trailing bytes")
    return out
