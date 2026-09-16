"""Statické kontroly Liquid šablon (bez Liquid enginu): existence, title bar, jen známé proměnné."""

import re
from pathlib import Path

import pytest

TEMPLATES = Path(__file__).parent.parent / "templates"
LAYOUTS = ["full", "half_horizontal", "half_vertical", "quadrant"]
# kořenové proměnné z payloadu + smyčkové/lokální proměnné použité v šablonách (first = assign)
ALLOWED_ROOTS = {"updated", "weather", "kick", "twitch", "item", "day", "forloop", "first"}
VAR_RE = re.compile(r"{{\s*([a-zA-Z_]\w*)")
TAG_VAR_RE = re.compile(r"{%\s*(?:if|unless|elsif|for\s+\w+\s+in)\s+([a-zA-Z_]\w*)")


@pytest.mark.parametrize("layout", LAYOUTS)
def test_template_exists_and_has_title_bar(layout):
    src = (TEMPLATES / f"{layout}.liquid").read_text(encoding="utf-8")
    assert 'class="title_bar"' in src
    assert "{{ updated }}" in src


@pytest.mark.parametrize("layout", LAYOUTS)
def test_template_uses_only_payload_variables(layout):
    src = (TEMPLATES / f"{layout}.liquid").read_text(encoding="utf-8")
    roots = set(VAR_RE.findall(src)) | set(TAG_VAR_RE.findall(src))
    assert roots <= ALLOWED_ROOTS, roots - ALLOWED_ROOTS


@pytest.mark.parametrize("layout", LAYOUTS)
def test_template_has_no_emoji(layout):
    src = (TEMPLATES / f"{layout}.liquid").read_text(encoding="utf-8")
    assert not re.search(r"[\U0001F300-\U0001FAFF☀-➿]", src)
