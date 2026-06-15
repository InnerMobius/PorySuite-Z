"""Tests for the app-wide Light/Dark/Automatic theme helper.

normalize_theme is pure logic (no QApplication needed); it guards the settings
round-trip and the live apply against bad/missing values defaulting to "auto".
"""

import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)
sys.path.insert(0, os.path.join(_ROOT, "core"))
sys.path.insert(0, os.path.join(_ROOT, "ui"))

import pytest  # noqa: E402

try:
    import PyQt6  # noqa: F401  (app_theme imports PyQt6 at module load)
    _QT = True
except Exception:
    _QT = False


@pytest.mark.skipif(not _QT, reason="PyQt6 unavailable")
def test_normalize_theme_valid_and_fallback():
    from app_theme import normalize_theme, DEFAULT_THEME, VALID_THEMES
    assert DEFAULT_THEME == "auto"
    assert set(VALID_THEMES) == {"auto", "light", "dark"}
    # valid values pass through (case-insensitive, trimmed)
    assert normalize_theme("dark") == "dark"
    assert normalize_theme("LIGHT") == "light"
    assert normalize_theme("  Auto ") == "auto"
    # anything unrecognised / empty / None falls back to the default
    assert normalize_theme("nonsense") == "auto"
    assert normalize_theme("") == "auto"
    assert normalize_theme(None) == "auto"
    assert normalize_theme(123) == "auto"


@pytest.mark.skipif(not _QT, reason="PyQt6 unavailable")
def test_scheme_map_covers_every_valid_mode():
    from app_theme import _SCHEME, VALID_THEMES
    from PyQt6.QtCore import Qt
    assert set(_SCHEME) == set(VALID_THEMES)
    assert _SCHEME["auto"] == Qt.ColorScheme.Unknown
    assert _SCHEME["light"] == Qt.ColorScheme.Light
    assert _SCHEME["dark"] == Qt.ColorScheme.Dark
