"""Lokální render obrazovky: úložiště screenů, převod na 1-bit a HTML shell."""

import io

from PIL import Image

from musthave.screen import build_html, to_device_image


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


def test_build_html_wraps_markup_in_og_shell():
    html = build_html("<b>hello</b>", layout="full")
    assert "screen--og_png" in html and 'view--full' in html and "<b>hello</b>" in html
    assert "plugins.css" in html
