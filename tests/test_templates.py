"""Statické kontroly Liquid šablon (bez Liquid enginu): existence, title bar, jen známé proměnné."""

import re
from pathlib import Path

import pytest

TEMPLATES = Path(__file__).parent.parent / "templates"
LAYOUTS = ["full", "half_horizontal", "half_vertical", "quadrant"]
# kořenové proměnné z payloadu + smyčkové/lokální proměnné použité v šablonách (first = assign)
ALLOWED_ROOTS = {"updated", "weather", "kick", "twitch", "item", "day", "forloop", "first", "fw", "host", "date", "countdowns", "event"}
VAR_RE = re.compile(r"{{\s*([a-zA-Z_]\w*)")
TAG_VAR_RE = re.compile(r"{%\s*(?:if|unless|elsif|for\s+\w+\s+in)\s+([a-zA-Z_]\w*)")


@pytest.mark.parametrize("layout", LAYOUTS)
def test_template_exists_and_shows_updated_time(layout):
    src = (TEMPLATES / f"{layout}.liquid").read_text(encoding="utf-8")
    assert "{{ updated }}" in src  # čas aktualizace musí být vidět (title bar nebo vlastní hlavička)


@pytest.mark.parametrize("layout", LAYOUTS)
def test_template_uses_only_payload_variables(layout):
    src = (TEMPLATES / f"{layout}.liquid").read_text(encoding="utf-8")
    roots = set(VAR_RE.findall(src)) | set(TAG_VAR_RE.findall(src))
    assert roots <= ALLOWED_ROOTS, roots - ALLOWED_ROOTS


@pytest.mark.parametrize("layout", LAYOUTS)
def test_template_has_structural_fallback_without_framework(layout):
    """Kdyby se framework CSS nenačetl (pomalá síť), musí sloupce/flex držet z vlastních pravidel šablony."""
    src = (TEMPLATES / f"{layout}.liquid").read_text(encoding="utf-8")
    assert re.search(r"\.mh\.layout \{[^}]*display: flex", src)
    if layout == "full":
        assert re.search(r"\.mh \.columns \{[^}]*display: flex", src) and re.search(r"\.mh \.column \{[^}]*flex: 1 1 0", src)


@pytest.mark.parametrize("layout", LAYOUTS)
def test_template_has_no_emoji(layout):
    src = (TEMPLATES / f"{layout}.liquid").read_text(encoding="utf-8")
    assert not re.search(r"[\U0001F300-\U0001FAFF☀-➿]", src)


@pytest.mark.parametrize("layout", ["full", "half_horizontal", "half_vertical"])
def test_template_shows_countdowns(layout):
    src = (TEMPLATES / f"{layout}.liquid").read_text(encoding="utf-8")
    assert "for event in countdowns" in src
