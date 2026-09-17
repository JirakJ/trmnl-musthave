"""Lokální render obrazovky: úložiště screenů, převod na 1-bit a HTML shell."""

import io

import pytest

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


def test_broken_screenshot_is_rejected():
    """Chromium na RPi občas vrátí snímek s nedokresleným spodkem (šum/šedá) – takový render se zahodí."""
    from PIL import ImageDraw

    from musthave.screen import RenderError, check_screenshot

    good = Image.new("L", (800, 480), 255)
    ImageDraw.Draw(good).rectangle((10, 10, 300, 60), fill=0)
    check_screenshot(good)  # nic nevyhodí

    bad = good.copy()
    ImageDraw.Draw(bad).rectangle((0, 400, 799, 479), fill=128)   # šedý/nedokreslený spodek
    with pytest.raises(RenderError):
        check_screenshot(bad)

    tiny = Image.new("L", (800, 390), 255)
    with pytest.raises(RenderError):
        check_screenshot(tiny)


def test_render_screen_falls_back_to_old_headless_when_new_is_truncated(monkeypatch):
    from musthave import screen
    from PIL import ImageDraw

    def fake_png(h):
        img = Image.new("L", (800, h), 255)
        ImageDraw.Draw(img).rectangle((10, 10, 100, 40), fill=0)
        buf = io.BytesIO(); img.save(buf, format="PNG"); return buf.getvalue()

    calls = []
    monkeypatch.setattr(screen, "render_markup", lambda payload, layout="full": "<b>x</b>")
    monkeypatch.setattr(screen, "find_chrome", lambda explicit=None: "/bin/chrome")
    monkeypatch.setattr(screen, "screenshot", lambda html, exe, timeout_s=60, headless="new": (calls.append(headless), fake_png(390 if headless == "new" else 480))[1])
    monkeypatch.setattr(screen, "_preferred_headless", "new")
    out = screen.render_screen({"updated": "1"})
    assert calls == ["new", "old"]
    assert Image.open(io.BytesIO(out)).size == (800, 480)
    screen.render_screen({"updated": "2"})
    assert calls[2:] == ["old"]  # podruhé rovnou fungující režim
