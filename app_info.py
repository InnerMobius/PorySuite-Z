import os

APP_NAME = "PorySuite"
AUTHOR = "jschoeny"

# Root directory of the application (where app.py / app_info.py live)
_APP_DIR = os.path.dirname(os.path.abspath(__file__))


def get_data_dir() -> str:
    """Local data directory for PorySuite settings, plugins, and project list.

    Returns a ``data/`` folder sitting alongside the application files instead
    of the OS user-data directory (e.g. AppData).  This keeps everything
    self-contained and portable.
    """
    return os.path.join(_APP_DIR, "data")


def get_settings_path() -> str:
    """Path to the INI settings file inside the local data directory."""
    return os.path.join(get_data_dir(), "settings.ini")
