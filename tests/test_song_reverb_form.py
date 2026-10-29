"""The two reverb forms in a song header are not interchangeable.

    .equ <label>_rev, reverb_set+N   set the global reverb to N for this song
    .equ <label>_rev, N              leave the global reverb alone -- inherit it

The engine only applies the value when the ``reverb_set`` bit (0x80) is present
(``src/m4a.c``: ``if (songHeader->reverb & SOUND_MODE_REVERB_SET)``), and the
setting is GLOBAL -- so forcing 0 on a short jingle changes the reverb for
whatever plays next, not just itself.

``song_parser`` reports both forms as a plain integer, so the import wizard used
to write ``reverb_set+N`` unconditionally and silently converted any
inherit-style song into a force-0 one just by being replaced. A stock project
here has 25 songs written the inherit way, so replacing any of them changed
runtime behaviour nobody asked to change.
"""

import os
import sys
import types

_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

# Shared PyQt6 stub helpers -- see tests/_qt_stub.py.
qt_module = types.ModuleType("PyQt6")
qt_core = types.ModuleType("PyQt6.QtCore")
qt_gui = types.ModuleType("PyQt6.QtGui")
qt_widgets = types.ModuleType("PyQt6.QtWidgets")
qt_core.pyqtSignal = lambda *a, **k: None
qt_core.QThread = type("QThread", (), {"__init__": lambda self, *a, **k: None})
qt_module.QtCore = qt_core
qt_module.QtGui = qt_gui
qt_module.QtWidgets = qt_widgets

from tests._qt_stub import (  # noqa: E402
    StubQtFinder as _StubQtFinder,
    autofill_qt_stub as _autofill_qt_stub,
)

if not any(isinstance(f, _StubQtFinder) for f in sys.meta_path):
    sys.meta_path.insert(0, _StubQtFinder())

qt_module.__path__ = []  # marks the stub as a package
_autofill_qt_stub(qt_module, qt_core, qt_gui, qt_widgets)
sys.modules.setdefault("PyQt6", qt_module)
sys.modules.setdefault("PyQt6.QtCore", qt_core)
sys.modules.setdefault("PyQt6.QtGui", qt_gui)
sys.modules.setdefault("PyQt6.QtWidgets", qt_widgets)

from ui.dialogs.s_file_import_dialog import _SImportWorker  # noqa: E402


def _worker(reverb):
    w = _SImportWorker.__new__(_SImportWorker)
    w._reverb = reverb
    return w


# ── the value was not touched: keep the source's form ──────────────────────

def test_inherit_form_is_preserved_when_value_unchanged():
    """A song that inherits must still inherit after a replace."""
    assert _worker(0)._reverb_value("0") == "0"


def test_explicit_form_is_preserved_when_value_unchanged():
    assert _worker(50)._reverb_value("reverb_set+50") == "reverb_set+50"
    assert _worker(0)._reverb_value("reverb_set+0") == "reverb_set+0"


def test_whitespace_in_the_source_does_not_change_the_decision():
    assert _worker(50)._reverb_value("  reverb_set+50  ") == "reverb_set+50"
    assert _worker(0)._reverb_value("  0 ") == "0"


# ── the user changed it: write the explicit form ───────────────────────────

def test_changing_the_value_writes_the_explicit_form():
    assert _worker(50)._reverb_value("0") == "reverb_set+50"
    assert _worker(40)._reverb_value("reverb_set+50") == "reverb_set+40"


def test_turning_reverb_off_from_a_set_value_forces_zero():
    """Going 50 -> 0 is a deliberate "off", so it must be the explicit form.

    Writing plain 0 there would leave the previous song's reverb in place,
    which is the opposite of what the user asked for.
    """
    assert _worker(0)._reverb_value("reverb_set+50") == "reverb_set+0"


def test_unparseable_source_falls_back_to_the_explicit_form():
    """A hand-written expression we cannot read must not be silently kept."""
    assert _worker(50)._reverb_value("SOME_CONST") == "reverb_set+50"
    assert _worker(0)._reverb_value("") == "reverb_set+0"
