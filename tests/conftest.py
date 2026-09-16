"""Testy nesmí číst reálný Keychain ani prostředí – tajemství dostávají jen explicitně."""

import pytest

from musthave import config


@pytest.fixture(autouse=True)
def no_keychain(monkeypatch):
    monkeypatch.setattr(config, "keychain_lookup", lambda name: None)
