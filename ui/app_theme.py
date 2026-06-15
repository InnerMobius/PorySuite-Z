"""app_theme.py — application-wide Light / Dark / Automatic theming.

PorySuite-Z renders with Qt's native Windows style and never forced a palette,
so its appearance has always followed the Windows "app mode" (light/dark)
setting. Qt 6.8+ lets us OVERRIDE that per-application via
``QStyleHints.setColorScheme()`` without switching widget styles — so the app
keeps its native look and only the light/dark scheme flips.

Modes:
    "auto"  -> follow Windows   (Qt.ColorScheme.Unknown)
    "light" -> force light      (Qt.ColorScheme.Light)
    "dark"  -> force dark       (Qt.ColorScheme.Dark)

The choice lives in data/settings.ini under "appearance/theme". It is applied
once at startup (default "auto") and live whenever changed in Tools -> Settings.
"""

from PyQt6.QtCore import Qt, QSettings
from PyQt6.QtWidgets import QApplication

from app_info import get_settings_path

THEME_KEY = "appearance/theme"
DEFAULT_THEME = "auto"
VALID_THEMES = ("auto", "light", "dark")

_SCHEME = {
    "auto": Qt.ColorScheme.Unknown,   # Unknown == "no override, follow the OS"
    "light": Qt.ColorScheme.Light,
    "dark": Qt.ColorScheme.Dark,
}


def normalize_theme(mode) -> str:
    """Coerce any value to a valid mode string, falling back to the default."""
    mode = str(mode or DEFAULT_THEME).strip().lower()
    return mode if mode in VALID_THEMES else DEFAULT_THEME


def get_saved_theme() -> str:
    """Return the saved theme mode ("auto"/"light"/"dark"), default "auto"."""
    try:
        s = QSettings(get_settings_path(), QSettings.Format.IniFormat)
        return normalize_theme(s.value(THEME_KEY, DEFAULT_THEME))
    except Exception:
        return DEFAULT_THEME


def set_saved_theme(mode) -> None:
    """Persist the theme mode to settings."""
    try:
        s = QSettings(get_settings_path(), QSettings.Format.IniFormat)
        s.setValue(THEME_KEY, normalize_theme(mode))
        s.sync()
    except Exception:
        pass


def apply_theme(mode, app=None) -> None:
    """Apply a theme mode to the running application immediately.

    Uses ``QStyleHints.setColorScheme`` (Qt 6.8+). On older Qt where that
    method is missing this is a no-op, so the app still launches and simply
    follows the OS as it did before.
    """
    app = app or QApplication.instance()
    if app is None:
        return
    try:
        hints = app.styleHints()
        setter = getattr(hints, "setColorScheme", None)
        if setter is not None:
            setter(_SCHEME[normalize_theme(mode)])
    except Exception:
        pass


def apply_saved_theme(app=None) -> None:
    """Read the saved theme and apply it (called once at startup)."""
    apply_theme(get_saved_theme(), app)
