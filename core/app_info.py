import os

APP_NAME = "PorySuite"
APP_DISPLAY_NAME = "PorySuite-Z"
VERSION = "0.0.3b"
AUTHOR = "InnerMobius"

# Root directory of the application (where app.py / app_info.py live)
_APP_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def get_data_dir() -> str:
    """Local data directory for PorySuite settings, plugins, and project list.

    Returns a ``data/`` folder sitting alongside the application files instead
    of the OS user-data directory (e.g. AppData).  This keeps everything
    self-contained and portable.
    """
    return os.path.join(_APP_DIR, "data")


def get_settings_path() -> str:
    """Path to the INI settings file in the application root."""
    return os.path.join(_APP_DIR, "settings.ini")


def get_cache_dir(project_dir: str = "") -> str:
    """Return a cache directory inside the porysuite app folder.

    If *project_dir* is given, returns a project-specific subfolder so
    multiple projects don't collide.  The folder is created automatically.

    All working/temp files go here instead of inside the user's game repo.
    """
    import hashlib
    base = os.path.join(_APP_DIR, "cache")
    if project_dir:
        # Short hash of the absolute project path for a unique, safe folder name
        slug = hashlib.sha1(os.path.abspath(project_dir).encode()).hexdigest()[:12]
        base = os.path.join(base, slug)
    os.makedirs(base, exist_ok=True)
    return base
