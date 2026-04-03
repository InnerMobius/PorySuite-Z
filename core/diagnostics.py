import logging
import os
from PyQt6.QtCore import QSettings
from app_info import get_settings_path


class AdvancedDiagnosticsFilter(logging.Filter):
    """Logging filter that suppresses verbose diagnostic messages unless enabled.

    The enabled flag is stored in the local settings.ini file under key
    "advanced_diagnostics".  Messages containing diagnostic tags like
    "[TYPES-DIAG]" or "[GENDER-DIAG]" will be emitted only when that setting
    is True.
    """

    DIAG_TAGS = ("[TYPES-DIAG]", "[GENDER-DIAG]", "[TYPE-DIAG]", "[TYPE")

    def __init__(self):
        super().__init__()

    def _enabled(self) -> bool:
        try:
            s = QSettings(get_settings_path(), QSettings.Format.IniFormat)
            return bool(s.value("advanced_diagnostics", False, type=bool))
        except Exception:
            return False

    def filter(self, record: logging.LogRecord) -> bool:
        try:
            msg = str(record.getMessage())
        except Exception:
            return True
        if any(tag in msg for tag in self.DIAG_TAGS):
            return self._enabled()
        return True


def install_global_filter():
    root = logging.getLogger()
    # avoid multiple installs
    for f in list(root.filters):
        if isinstance(f, AdvancedDiagnosticsFilter):
            return
    root.addFilter(AdvancedDiagnosticsFilter())
