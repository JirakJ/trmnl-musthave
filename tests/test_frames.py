"""1-bit bitmapa 800×480 a FrameStore (poslední snímky pro diff)."""

import io

from PIL import Image, ImageDraw

from musthave.frames import HEIGHT, ROW_BYTES, WIDTH, FrameStore, bitmap_to_png, frame_id, png_to_bitmap


def synthetic_png(rect=None, size=(WIDTH, HEIGHT)) -> bytes:
    img = Image.new("1", size, 1)
    if rect:
        ImageDraw.Draw(img).rectangle(rect, fill=0)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def test_png_to_bitmap_size_and_white_is_one():
    bm = png_to_bitmap(synthetic_png())
    assert len(bm) == ROW_BYTES * HEIGHT == 48000
    assert bm == b"\xff" * 48000


def test_black_rect_clears_bits_at_expected_position():
    bm = png_to_bitmap(synthetic_png(rect=(16, 10, 23, 10)))  # 8 px wide na řádku 10, x 16..23
    row = bm[10 * ROW_BYTES:(10 + 1) * ROW_BYTES]
    assert row[2] == 0x00 and row[1] == 0xFF and row[3] == 0xFF


def test_bitmap_png_roundtrip():
    src = synthetic_png(rect=(100, 200, 300, 250))
    bm = png_to_bitmap(src)
    assert png_to_bitmap(bitmap_to_png(bm)) == bm
    assert Image.open(io.BytesIO(bitmap_to_png(bm))).mode == "1"


def test_wrong_size_png_is_resized():
    assert len(png_to_bitmap(synthetic_png(size=(400, 240)))) == 48000


def test_frame_id_is_stable_and_content_based():
    a = png_to_bitmap(synthetic_png(rect=(0, 0, 7, 0)))
    b = png_to_bitmap(synthetic_png(rect=(8, 0, 15, 0)))
    assert frame_id(a) == frame_id(a)
    assert frame_id(a) != frame_id(b)
    assert frame_id(a).startswith("musthave-frame-") and len(frame_id(a)) == len("musthave-frame-") + 10


def test_store_put_get_latest_and_idempotent(tmp_path):
    store = FrameStore(tmp_path / "frames")
    assert store.latest() is None
    bm = png_to_bitmap(synthetic_png(rect=(0, 0, 7, 7)))
    fid = store.put(bm)
    assert store.put(bm) == fid
    assert store.latest() == fid
    assert store.get(fid) == bm
    assert store.get("musthave-nope") is None
    assert png_to_bitmap(store.png(fid)) == bm


def test_store_keeps_last_eight(tmp_path):
    store = FrameStore(tmp_path / "frames", keep=8)
    ids = [store.put(png_to_bitmap(synthetic_png(rect=(i * 8, 0, i * 8 + 7, 7)))) for i in range(10)]
    assert store.get(ids[0]) is None and store.get(ids[1]) is None
    assert all(store.get(i) is not None for i in ids[2:])
    assert store.latest() == ids[-1]


def test_store_survives_restart(tmp_path):
    bm = png_to_bitmap(synthetic_png(rect=(8, 8, 15, 15)))
    fid = FrameStore(tmp_path / "frames").put(bm)
    again = FrameStore(tmp_path / "frames")
    assert again.latest() == fid and again.get(fid) == bm
