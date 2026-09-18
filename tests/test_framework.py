"""Framework CSS/JS pro render: obnova mimo render (podmíněný GET), resolve() bez sítě, stará kopie při výpadku."""

import json

from musthave.framework import ASSETS, FrameworkCache, NotModified

CSS_URL, JS_URL = ASSETS[0][2], ASSETS[1][2]
CSS = b"body{} .trmnl .columns{display:flex}"
JS = b"function x(){}"


class FakeNet:
    def __init__(self, store=None):
        self.store = store or {CSS_URL: CSS, JS_URL: JS}
        self.calls = []

    def __call__(self, url, etag=None, last_modified=None):
        self.calls.append((url, etag, last_modified))
        body = self.store.get(url)
        if isinstance(body, Exception):
            raise body
        return body, {"etag": f'"{len(body)}"', "last_modified": "Fri, 18 Sep 2026 14:19:39 GMT"}


def test_refresh_downloads_and_resolve_uses_file_uris(tmp_path):
    net = FakeNet()
    cache = FrameworkCache(tmp_path / "cache", fetch_fn=net, now_fn=lambda: 1000.0)
    cache.refresh()
    fw = cache.resolve()
    assert fw.source == "cache" and fw.css.startswith("file://") and fw.css.endswith("plugins.css") and fw.js.endswith("plugins.js")
    assert (tmp_path / "cache" / "plugins.css").read_bytes() == CSS
    assert json.loads((tmp_path / "cache" / "plugins.css.meta.json").read_text())["etag"] == f'"{len(CSS)}"'
    assert not list((tmp_path / "cache").glob(".*.tmp"))


def test_resolve_never_touches_the_network(tmp_path):
    net = FakeNet()
    cache = FrameworkCache(tmp_path / "cache", fetch_fn=net, now_fn=lambda: 1000.0)
    fw = cache.resolve()
    assert fw.source == "remote" and fw.css == CSS_URL and fw.js == JS_URL and net.calls == []


def test_refresh_within_the_interval_is_a_no_op(tmp_path):
    net = FakeNet()
    cache = FrameworkCache(tmp_path / "cache", fetch_fn=net, now_fn=lambda: 1000.0)
    cache.refresh()
    cache.refresh()
    assert len(net.calls) == 2  # css + js jen jednou


def test_daily_refresh_is_a_conditional_get_and_304_keeps_the_copy(tmp_path):
    clock = {"now": 1000.0}
    net = FakeNet()
    cache = FrameworkCache(tmp_path / "cache", fetch_fn=net, now_fn=lambda: clock["now"], refresh_every_s=10)
    cache.refresh()
    clock["now"] += 11
    net.store = {CSS_URL: NotModified(), JS_URL: NotModified()}
    cache.refresh()
    assert net.calls[-1] == (JS_URL, f'"{len(JS)}"', "Fri, 18 Sep 2026 14:19:39 GMT")  # validátory z minula
    assert cache.resolve().source == "cache"                                     # 304 = zkontrolováno, čerstvé
    assert (tmp_path / "cache" / "plugins.css").read_bytes() == CSS


def test_stale_copy_is_used_when_refresh_fails(tmp_path):
    clock = {"now": 1000.0}
    net = FakeNet()
    cache = FrameworkCache(tmp_path / "cache", fetch_fn=net, now_fn=lambda: clock["now"], refresh_every_s=10)
    cache.refresh()
    clock["now"] += 11
    net.store = {CSS_URL: OSError("network down"), JS_URL: OSError("network down")}
    cache.refresh()
    fw = cache.resolve()
    assert fw.source == "stale-cache" and fw.css.startswith("file://")
    assert (tmp_path / "cache" / "plugins.css").read_bytes() == CSS   # stará kopie zůstala


def test_garbage_download_is_rejected(tmp_path):
    net = FakeNet({CSS_URL: b"<html>captive portal</html>", JS_URL: b"<html>captive portal</html>"})
    cache = FrameworkCache(tmp_path / "cache", fetch_fn=net, now_fn=lambda: 1000.0)
    cache.refresh()
    assert cache.resolve().source == "remote" and not (tmp_path / "cache" / "plugins.css").exists()


def test_background_refresh_thread_runs_to_completion(tmp_path):
    net = FakeNet()
    cache = FrameworkCache(tmp_path / "cache", fetch_fn=net, now_fn=lambda: 1000.0)
    cache.refresh_in_background().join(timeout=5)
    assert cache.resolve().source == "cache"
