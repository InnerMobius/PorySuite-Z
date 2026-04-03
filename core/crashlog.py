import json
import logging
import os
import sys
import threading
import traceback
from datetime import datetime

def _app_root() -> str:
    # Root directory of the running application (repo root)
    # This file lives in core/, so go up one level
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def logs_dir() -> str:
    # Place logs in a crashlogs folder at the app root
    path = os.path.join(_app_root(), "crashlogs")
    os.makedirs(path, exist_ok=True)
    return path


def purge_old_logs(keep_days: int = 7, max_mb: int = 0) -> int:
    """Delete crashlog files older than *keep_days* and enforce *max_mb* cap.

    Purge strategy:
    1. Delete anything older than *keep_days*.
    2. If total size still exceeds *max_mb* (when > 0), delete oldest files
       until under the cap.

    Returns count deleted.
    """
    import time
    deleted = 0
    try:
        d = logs_dir()
        cutoff = time.time() - (keep_days * 86400)

        # Build list of (mtime, path, size) for all files
        entries = []
        for f in os.listdir(d):
            p = os.path.join(d, f)
            if os.path.isfile(p):
                try:
                    entries.append((os.path.getmtime(p), p, os.path.getsize(p)))
                except OSError:
                    pass

        # Phase 1: delete files older than keep_days
        remaining = []
        for mtime, p, size in entries:
            if mtime < cutoff:
                try:
                    os.remove(p)
                    deleted += 1
                except OSError:
                    remaining.append((mtime, p, size))
            else:
                remaining.append((mtime, p, size))

        # Phase 2: enforce size cap (oldest files first)
        if max_mb > 0:
            max_bytes = max_mb * 1024 * 1024
            total = sum(s for _, _, s in remaining)
            if total > max_bytes:
                # Sort oldest first so we delete the oldest to free space
                remaining.sort(key=lambda x: x[0])
                still_kept = []
                for mtime, p, size in remaining:
                    if total > max_bytes:
                        try:
                            os.remove(p)
                            total -= size
                            deleted += 1
                        except OSError:
                            still_kept.append((mtime, p, size))
                    else:
                        still_kept.append((mtime, p, size))
    except Exception:
        pass
    return deleted


def read_purge_settings() -> tuple[int, int]:
    """Read crashlog retention settings from settings.ini.

    Returns (keep_days, max_mb).  Falls back to defaults (3 days, 100 MB)
    if the settings file doesn't exist or can't be read.
    """
    try:
        from PyQt6.QtCore import QSettings
        from app_info import get_settings_path
        s = QSettings(get_settings_path(), QSettings.Format.IniFormat)
        keep_days = int(s.value("crashlog/keep_days", 3, type=int))
        max_mb = int(s.value("crashlog/max_size_mb", 100, type=int))
        return keep_days, max_mb
    except Exception:
        return 3, 100


_SESSION_TS: str | None = None
_JSON_PATH: str | None = None
_ORIG_STDOUT = None
_ORIG_STDERR = None


class _JsonLineHandler(logging.Handler):
    """Logs each record as a JSON object on a single line.

    Fields: ts (ISO8601), level, name, message.
    """

    def __init__(self, path: str):
        super().__init__()
        self._path = path
        # Ensure directory exists
        os.makedirs(os.path.dirname(self._path), exist_ok=True)

    def emit(self, record: logging.LogRecord) -> None:
        try:
            obj = {
                "ts": datetime.fromtimestamp(record.created).isoformat(timespec="milliseconds"),
                "level": record.levelname,
                "name": record.name,
                "message": self.format(record) if self.formatter else record.getMessage(),
            }
            with open(self._path, "a", encoding="utf-8") as f:
                f.write(json.dumps(obj, ensure_ascii=False))
                f.write("\n")
        except Exception:
            # Never raise from logging
            pass


def init_logging() -> str:
    """Initialize application-wide logging to a timestamped file.

    Returns the log file path for this run.
    """
    global _SESSION_TS, _JSON_PATH
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    _SESSION_TS = ts
    log_path = os.path.join(logs_dir(), f"porysuite_{ts}.log")
    _JSON_PATH = os.path.join(logs_dir(), f"porysuite_{ts}.jsonl")

    root = logging.getLogger()
    root.setLevel(logging.DEBUG)

    # File handler
    fh = logging.FileHandler(log_path, encoding="utf-8")
    fh.setLevel(logging.DEBUG)
    fmt = logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s")
    fh.setFormatter(fmt)
    root.addHandler(fh)

    # Console handler (info+)
    ch = logging.StreamHandler(stream=sys.stdout)
    ch.setLevel(logging.INFO)
    ch.setFormatter(fmt)
    root.addHandler(ch)

    # JSON line handler (captures everything including redirected stdout/stderr)
    jh = _JsonLineHandler(_JSON_PATH)
    # Keep message plain in JSON; attach a simple formatter for consistency
    jh.setFormatter(logging.Formatter("%(message)s"))
    root.addHandler(jh)

    # Exception hooks
    def _excepthook(exc_type, exc, tb):
        logging.critical("Uncaught exception:")
        logging.critical("".join(traceback.format_exception(exc_type, exc, tb)).rstrip())
    sys.excepthook = _excepthook

    if hasattr(threading, "excepthook"):
        def _thread_excepthook(args):
            logging.critical("Uncaught thread exception in %s:", getattr(args, 'thread', None))
            logging.critical("".join(traceback.format_exception(args.exc_type, args.exc_value, args.exc_traceback)).rstrip())
        threading.excepthook = _thread_excepthook  # type: ignore[attr-defined]

    # Qt message handler (optional, installed by caller after QApplication exists)
    return log_path


def install_qt_message_handler():
    try:
        from PyQt6.QtCore import qInstallMessageHandler, QtMsgType
    except Exception:
        return

    def handler(mode, context, message):
        if mode == QtMsgType.QtDebugMsg:
            logging.debug(message)
        elif mode == QtMsgType.QtInfoMsg:
            logging.info(message)
        elif mode == QtMsgType.QtWarningMsg:
            logging.warning(message)
        elif mode == QtMsgType.QtCriticalMsg:
            logging.error(message)
        elif mode == QtMsgType.QtFatalMsg:
            logging.critical(message)

    try:
        qInstallMessageHandler(handler)
    except Exception:
        pass


def latest_log_file() -> str | None:
    try:
        files = [f for f in os.listdir(logs_dir()) if f.endswith('.log')]
        if not files:
            return None
        files.sort(reverse=True)
        return os.path.join(logs_dir(), files[0])
    except Exception:
        return None


def latest_json_log_file() -> str | None:
    try:
        files = [f for f in os.listdir(logs_dir()) if f.endswith('.jsonl') or f.endswith('.json')]
        if not files:
            return None
        files.sort(reverse=True)
        return os.path.join(logs_dir(), files[0])
    except Exception:
        return None


def session_json_path() -> str | None:
    return _JSON_PATH


class _LoggerWriter:
    """Redirects writes to a logging.Logger at a given level.

    Buffers until a newline to avoid fragmenting lines.
    """

    def __init__(self, logger: logging.Logger, level: int):
        self._logger = logger
        self._level = level
        self._buf: list[str] = []

    def write(self, msg: str):
        if not isinstance(msg, str):
            msg = str(msg)
        if msg == "":
            return
        self._buf.append(msg)
        # Flush on newline
        if "\n" in msg:
            self.flush()

    def flush(self):
        if not self._buf:
            return
        text = "".join(self._buf)
        self._buf.clear()
        # Split to preserve multiple lines
        for line in text.splitlines():
            if line:
                self._logger.log(self._level, line)

    def isatty(self):
        return False


class _TeeWriter:
    """Writes both to the original stream and to logging.

    Preserves terminal output while mirroring content into the logger.
    Buffers until newline to avoid fragmenting lines in logs.
    """

    def __init__(self, stream, logger: logging.Logger, level: int):
        self._stream = stream
        self._logger = logger
        self._level = level
        self._buf: list[str] = []
        self._flushing = False  # reentrancy guard

    def write(self, msg: str):
        try:
            # Always write through to the real terminal stream
            if hasattr(self._stream, "write"):
                self._stream.write(msg)
        except Exception:
            pass
        # Mirror to log (buffered)
        if not isinstance(msg, str):
            msg = str(msg)
        if msg == "":
            return
        self._buf.append(msg)
        if "\n" in msg:
            self.flush()

    def flush(self):
        try:
            if hasattr(self._stream, "flush"):
                self._stream.flush()
        except Exception:
            pass
        if not self._buf:
            return
        # Guard against re-entrant calls (e.g. logging handler error → stderr write → flush)
        if self._flushing:
            self._buf.clear()
            return
        self._flushing = True
        try:
            text = "".join(self._buf)
            self._buf.clear()
            for line in text.splitlines():
                if line:
                    try:
                        self._logger.log(self._level, line)
                    except Exception:
                        pass
        finally:
            self._flushing = False

    def isatty(self):
        try:
            return bool(getattr(self._stream, "isatty", lambda: False)())
        except Exception:
            return False


def install_std_redirects():
    """Tee sys.stdout and sys.stderr to logging while keeping terminal output."""
    global _ORIG_STDOUT, _ORIG_STDERR
    try:
        if _ORIG_STDOUT is None:
            _ORIG_STDOUT = sys.stdout
        if _ORIG_STDERR is None:
            _ORIG_STDERR = sys.stderr
        logger = logging.getLogger()
        sys.stdout = _TeeWriter(_ORIG_STDOUT, logger, logging.INFO)  # type: ignore[assignment]
        sys.stderr = _TeeWriter(_ORIG_STDERR, logger, logging.ERROR)  # type: ignore[assignment]
    except Exception:
        pass
