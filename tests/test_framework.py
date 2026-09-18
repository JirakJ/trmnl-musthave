"""Framework CSS/JS pro render: cache na disku, obnova mimo render, stará kopie při výpadku sítě."""

from musthave.framework import ASSETS, RETRY_AFTER_FAIL_S, FrameworkCache

CSS = b"body{} .trmnl .columns{display:flex}"
JS = b"function x(){}"


def fake_fetch(store):
    def _fetch(url):
        body = store.get(url)
        if isinstance(body, Exception):
            raise body
        return body
    return _fetch


def test_first_resolve_downloads_and_uses_file_uris(tmp_path):
    store = {ASSETS[0][1]: CSS, ASSETS[1][1]: JS}
    cache = FrameworkCache(tmp_path / "cache", fetch_fn=fake_fetch(store), now_fn=lambda: 1000.0)
    fw = cache.resolve()
    assert fw.source == "cache" and fw.css.startswith("file://") and fw.css.endswith("plugins.css") and fw.js.endswith("plugins.js")
    assert (tmp_path / "cache" / "plugins.css").read_bytes() == CSS
    assert not list((tmp_path / "cache").glob(".*.tmp"))


def test_fresh_cache_is_not_refetched(tmp_path):
    calls = []
    store = {ASSETS[0][1]: CSS, ASSETS[1][1]: JS}

    def fetch(url):
        calls.append(url)
        return store[url]
    cache = FrameworkCache(tmp_path / "cache", fetch_fn=fetch, now_fn=lambda: 1000.0)
    cache.resolve()
    cache.resolve()
    assert len(calls) == 2  # css + js jen jednou


def test_stale_copy_is_used_when_refresh_fails(tmp_path):
    clock = {"now": 1000.0}
    store = {ASSETS[0][1]: CSS, ASSETS[1][1]: JS}
    cache = FrameworkCache(tmp_path / "cache", fetch_fn=fake_fetch(store), now_fn=lambda: clock["now"], max_age_s=10)
    assert cache.resolve().source == "cache"
    import os
    for name in ("plugins.css", "plugins.js"):
        os.utime(tmp_path / "cache" / name, (900.0, 900.0))
    store[ASSETS[0][1]] = OSError("network down")
    store[ASSETS[1][1]] = OSError("network down")
    fw = cache.resolve()
    assert fw.source == "stale-cache" and fw.css.startswith("file://")
    assert (tmp_path / "cache" / "plugins.css").read_bytes() == CSS   # stará kopie zůstala


def test_remote_urls_when_nothing_cached_and_download_fails(tmp_path):
    store = {ASSETS[0][1]: OSError("no network"), ASSETS[1][1]: OSError("no network")}
    fw = FrameworkCache(tmp_path / "cache", fetch_fn=fake_fetch(store), now_fn=lambda: 1000.0).resolve()
    assert fw.source == "remote" and fw.css == ASSETS[0][1] and fw.js == ASSETS[1][1]


def test_garbage_download_is_rejected_and_retry_is_throttled(tmp_path):
    calls = []
    clock = {"now": 1000.0}

    def fetch(url):
        calls.append(url)
        return b"<html>captive portal</html>"
    cache = FrameworkCache(tmp_path / "cache", fetch_fn=fetch, now_fn=lambda: clock["now"])
    assert cache.resolve().source == "remote"
    assert not (tmp_path / "cache" / "plugins.css").exists()
    n = len(calls)
    cache.resolve()                      # do RETRY_AFTER_FAIL_S se nezkouší znovu
    assert len(calls) == n
    clock["now"] += RETRY_AFTER_FAIL_S + 1
    cache.resolve()
    assert len(calls) == n + 2
