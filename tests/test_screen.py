"""Lokální render obrazovky: úložiště screenů, převod na 1-bit a HTML shell."""

import io

from PIL import Image

from musthave.screen import ScreenStore, build_html, to_device_image


def png_bytes(color: int, size=(800, 480)) -> bytes:
    buf = io.BytesIO()
    Image.new("L", size, color).save(buf, format="PNG")
    return buf.getvalue()


def test_to_device_image_png_is_1bit_800x480():
    out = to_device_image(png_bytes(128), fmt="png")
    img = Image.open(io.BytesIO(out))
    assert img.format == "PNG" and img.mode == "1" and img.size == (800, 480)


def test_to_device_image_bmp_is_1bit():
    out = to_device_image(png_bytes(0), fmt="bmp")
    img = Image.open(io.BytesIO(out))
    assert img.format == "BMP" and img.mode == "1" and img.size == (800, 480)


def test_to_device_image_resizes_wrong_size():
    out = to_device_image(png_bytes(255, size=(400, 240)), fmt="png")
    assert Image.open(io.BytesIO(out)).size == (800, 480)


def test_store_update_returns_hash_filename_and_keeps_current(tmp_path):
    store = ScreenStore(tmp_path / "screen")
    assert store.current() is None
    name1 = store.update(b"image-one", ext="png")
    assert name1.startswith("musthave-") and name1.endswith(".png")
    assert store.current() == (name1, b"image-one")


def test_store_same_bytes_keep_same_filename(tmp_path):
    store = ScreenStore(tmp_path / "screen")
    assert store.update(b"same", ext="png") == store.update(b"same", ext="png")


def test_store_new_bytes_change_filename(tmp_path):
    store = ScreenStore(tmp_path / "screen")
    assert store.update(b"one", ext="png") != store.update(b"two", ext="png")


def test_store_survives_restart(tmp_path):
    name = ScreenStore(tmp_path / "screen").update(b"persisted", ext="png")
    assert ScreenStore(tmp_path / "screen").current() == (name, b"persisted")


def test_build_html_wraps_markup_in_og_shell():
    html = build_html("<b>hello</b>", layout="full")
    assert "screen--og_png" in html and 'view--full' in html and "<b>hello</b>" in html
    assert "plugins.css" in html
