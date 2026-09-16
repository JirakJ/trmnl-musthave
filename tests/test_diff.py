"""Diff bitmap → špinavé dlaždice → obdélníky → MHR1 blob."""

import struct

import pytest

from musthave.diff import Rect, area, decode_regions, dirty_tiles, encode_regions, merge_rects
from musthave.frames import HEIGHT, ROW_BYTES, WIDTH

WHITE = b"\xff" * (ROW_BYTES * HEIGHT)


def with_pixels(base: bytes, pixels) -> bytes:
    buf = bytearray(base)
    for x, y in pixels:
        buf[y * ROW_BYTES + x // 8] &= ~(0x80 >> (x % 8)) & 0xFF
    return bytes(buf)


def test_identical_bitmaps_have_no_dirty_tiles():
    assert dirty_tiles(WHITE, WHITE) == set()


def test_single_pixel_marks_its_tile():
    new = with_pixels(WHITE, [(123, 45)])
    assert dirty_tiles(WHITE, new) == {(15, 5)}  # 123//8, 45//8


def test_single_tile_becomes_aligned_rect():
    rects = merge_rects({(15, 5)})
    assert rects == [Rect(x=120, y=40, w=8, h=8)]


def test_tiles_on_same_row_merge_into_one_band():
    rects = merge_rects({(2, 3), (5, 3), (9, 3)})
    assert rects == [Rect(x=16, y=24, w=64, h=8)]


def test_adjacent_rows_merge_vertically():
    rects = merge_rects({(2, 3), (3, 4)})
    assert rects == [Rect(x=16, y=24, w=16, h=16)]


def test_far_apart_rows_stay_separate_within_limit():
    rects = merge_rects({(0, 0), (0, 30)}, max_rects=4)
    assert len(rects) == 2


def test_max_rects_merges_cheapest_pair():
    tiles = {(0, 0), (0, 2), (90, 50)}  # dva blízko nahoře, jeden daleko
    rects = merge_rects(tiles, max_rects=2)
    assert len(rects) == 2
    assert Rect(x=0, y=0, w=8, h=24) in rects
    assert Rect(x=720, y=400, w=8, h=8) in rects


def test_area_sums_rects():
    assert area([Rect(0, 0, 8, 8), Rect(16, 16, 16, 8)]) == 64 + 128


def test_encode_decode_roundtrip_bit_exact():
    new = with_pixels(WHITE, [(123, 45), (124, 46), (700, 470)])
    rects = merge_rects(dirty_tiles(WHITE, new))
    blob = encode_regions(WHITE, new, rects)
    assert blob[:4] == b"MHR1" and blob[4] == len(rects)
    assert len(blob) == 5 + sum(8 + 2 * r.h * r.w // 8 for r in rects)
    decoded = decode_regions(blob)
    assert [d[0] for d in decoded] == rects
    for rect, old_plane, new_plane in decoded:
        for row in range(rect.h):
            y = rect.y + row
            start = y * ROW_BYTES + rect.x // 8
            assert old_plane[row * rect.w // 8:(row + 1) * rect.w // 8] == WHITE[start:start + rect.w // 8]
            assert new_plane[row * rect.w // 8:(row + 1) * rect.w // 8] == new[start:start + rect.w // 8]


def test_encode_header_is_little_endian_u16():
    blob = encode_regions(WHITE, WHITE, [Rect(120, 40, 8, 8)])
    assert struct.unpack("<HHHH", blob[5:13]) == (120, 40, 8, 8)


def test_rects_must_be_inside_canvas_and_aligned():
    with pytest.raises(ValueError):
        encode_regions(WHITE, WHITE, [Rect(4, 0, 8, 8)])
    with pytest.raises(ValueError):
        encode_regions(WHITE, WHITE, [Rect(WIDTH - 8, HEIGHT - 4, 8, 8)])
