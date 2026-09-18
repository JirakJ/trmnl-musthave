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


def test_build_html_embeds_vendored_inter_local_framework_and_fallback_css(tmp_path, monkeypatch):
    from musthave import screen
    from musthave.framework import FrameworkCache

    (tmp_path / "plugins.css").write_bytes(b".trmnl .columns{display:flex}")
    (tmp_path / "plugins.js").write_bytes(b"function x(){}")
    monkeypatch.setattr(screen, "FRAMEWORK", FrameworkCache(tmp_path, fetch_fn=lambda *a, **k: (_ for _ in ()).throw(AssertionError("no network in render"))))
    html = build_html("<b>hello</b>", layout="full")
    assert f'href="{(tmp_path / "plugins.css").resolve().as_uri()}"' in html
    assert "fonts.googleapis.com" not in html and "@font-face" in html
    assert "inter-latin-ext.woff2" in html and "font-weight: 300 700" in html
    assert "https://" not in html.split("<body")[0]                       # hlavička bez síťových závislostí
    assert ".mh .columns { display: flex" in html and html.index(".mh .columns") < html.index("plugins.css")  # fallback před frameworkem


def test_build_html_explicit_framework_and_missing_font_fall_back(tmp_path):
    from musthave import screen
    from musthave.framework import Framework

    html = build_html("<b>x</b>", framework=Framework("file:///tmp/a.css", "file:///tmp/a.js", "cache"))
    assert 'href="file:///tmp/a.css"' in html
    assert "fonts.googleapis.com" in screen.font_css(tmp_path)  # bez přibalených fontů → Google Fonts


def test_build_html_wraps_markup_in_og_shell():
    from musthave.framework import Framework

    html = build_html("<b>hello</b>", layout="full", framework=Framework("plugins.css", "plugins.js", "cache"))
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


def test_render_screen_falls_back_when_chromium_crashes_in_preferred_mode(monkeypatch):
    from musthave import screen
    from PIL import ImageDraw

    def good_png():
        img = Image.new("L", (800, 480), 255)
        ImageDraw.Draw(img).rectangle((10, 10, 100, 40), fill=0)
        buf = io.BytesIO(); img.save(buf, format="PNG"); return buf.getvalue()

    calls = []

    def shot(html, exe, timeout_s=60, headless="new"):
        calls.append(headless)
        if headless == "new":
            raise RuntimeError("chrome screenshot failed (1): crashed")
        return good_png()

    monkeypatch.setattr(screen, "render_markup", lambda payload, layout="full": "<b>x</b>")
    monkeypatch.setattr(screen, "find_chrome", lambda explicit=None: "/bin/chrome")
    monkeypatch.setattr(screen, "screenshot", shot)
    monkeypatch.setattr(screen, "_preferred_headless", "new")
    out = screen.render_screen({"updated": "1"})
    assert calls == ["new", "old"] and Image.open(io.BytesIO(out)).size == (800, 480)


def test_render_screen_respects_configured_headless_mode(monkeypatch):
    from musthave import screen
    from PIL import ImageDraw

    img = Image.new("L", (800, 480), 255); ImageDraw.Draw(img).rectangle((10, 10, 100, 40), fill=0)
    buf = io.BytesIO(); img.save(buf, format="PNG"); good = buf.getvalue()
    calls = []
    monkeypatch.setattr(screen, "render_markup", lambda payload, layout="full": "<b>x</b>")
    monkeypatch.setattr(screen, "find_chrome", lambda explicit=None: "/bin/chrome")
    monkeypatch.setattr(screen, "screenshot", lambda html, exe, timeout_s=60, headless="new": (calls.append(headless), good)[1])
    monkeypatch.setattr(screen, "_preferred_headless", "new")
    screen.render_screen({"updated": "1"}, headless="old")
    assert calls == ["old"]
