"""
Every UI string ships in EN and ES from day 1 — a project rule, not a nicety,
since the whole product is bilingual. The only test runner this repo has is
pytest, so the guard for the frontend's two locale files lives here: it reads
them as plain JSON, which keeps it offline and fast like the rest of the suite.

A key present in one file and missing from the other means a user on that
language sees a raw key like `sources.pause` in the UI.
"""
import json
from pathlib import Path

import pytest

LOCALES = Path(__file__).resolve().parents[2] / "frontend" / "src" / "i18n" / "locales"


def _flatten(node, prefix=""):
    """Every leaf path in a translation file, as dotted keys."""
    if isinstance(node, dict):
        keys: set[str] = set()
        for key, value in node.items():
            keys |= _flatten(value, f"{prefix}.{key}" if prefix else key)
        return keys
    return {prefix}


@pytest.fixture(scope="module")
def locales():
    return {
        lang: _flatten(json.loads((LOCALES / f"{lang}.json").read_text()))
        for lang in ("en", "es")
    }


def test_every_english_string_has_a_spanish_one(locales):
    missing = sorted(locales["en"] - locales["es"])
    assert not missing, f"missing from es.json: {missing}"


def test_no_spanish_string_is_orphaned(locales):
    """A key only in es.json is dead weight, or an EN string someone forgot."""
    orphaned = sorted(locales["es"] - locales["en"])
    assert not orphaned, f"missing from en.json: {orphaned}"
