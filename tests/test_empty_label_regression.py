import os
import sys
import types
import unittest

ROOT_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if ROOT_DIR not in sys.path:
    sys.path.insert(0, ROOT_DIR)

# Stub PyQt6 modules
qt_module = types.ModuleType("PyQt6")
qt_core = types.ModuleType("PyQt6.QtCore")
qt_gui = types.ModuleType("PyQt6.QtGui")
qt_widgets = types.ModuleType("PyQt6.QtWidgets")

qt_core.pyqtSignal = lambda *a, **kw: None
qt_core.Qt = type("Qt", (), {})
qt_core.QEvent = type("QEvent", (), {})
class _DummyBlocker:
    def __init__(self, *_):
        pass
    def __enter__(self):
        return self
    def __exit__(self, *a):
        return False
qt_core.QSignalBlocker = _DummyBlocker
qt_gui.QFont = type("QFont", (), {})
qt_gui.QKeyEvent = type("QKeyEvent", (), {})
qt_gui.QKeySequence = type("QKeySequence", (), {})
qt_gui.QImage = type("QImage", (), {})
qt_gui.QPixmap = type("QPixmap", (), {})

class QTreeWidgetItem:
    def __init__(self, texts=None):
        self._texts = list(texts or [])
    def text(self, column):
        return self._texts[column] if column < len(self._texts) else ""
    def setText(self, column, value):
        while column >= len(self._texts):
            self._texts.append("")
        self._texts[column] = value

class QTreeWidget:
    def __init__(self, items=None):
        self._items = list(items or [])
    def topLevelItemCount(self):
        return len(self._items)
    def topLevelItem(self, index):
        return self._items[index]

qt_widgets.QApplication = type("QApplication", (), {})
qt_widgets.QMainWindow = type("QMainWindow", (), {})
qt_widgets.QTreeWidgetItem = QTreeWidgetItem
qt_widgets.QTreeWidget = QTreeWidget
qt_widgets.QLabel = type("QLabel", (), {})
qt_widgets.QProgressBar = type("QProgressBar", (), {})
qt_widgets.QListWidgetItem = type("QListWidgetItem", (), {})
qt_widgets.QTableWidgetItem = type("QTableWidgetItem", (), {})
qt_widgets.QMessageBox = type("QMessageBox", (), {})
qt_widgets.QInputDialog = type("QInputDialog", (), {})
qt_widgets.QPushButton = type("QPushButton", (), {})

qt_module.QtCore = qt_core
qt_module.QtGui = qt_gui
qt_module.QtWidgets = qt_widgets
# Shared PyQt6 stub helpers -- see tests/_qt_stub.py for why none of this is a
# hand-written list of Qt class names any more.
from tests._qt_stub import (  # noqa: E402
    StubQtFinder as _StubQtFinder,
    UiStub as _UiStub,
    auto_qt_class as _auto_qt_class,
    autofill_qt_stub as _autofill_qt_stub,
    is_qt_method_name as _is_qt_method_name,
)

if not any(isinstance(f, _StubQtFinder) for f in sys.meta_path):
    sys.meta_path.insert(0, _StubQtFinder())
qt_module.__path__ = []  # marks the stub as a package
_autofill_qt_stub(qt_module, qt_core, qt_gui, qt_widgets)
sys.modules.setdefault("PyQt6", qt_module)
sys.modules.setdefault("PyQt6.QtCore", qt_core)
sys.modules.setdefault("PyQt6.QtGui", qt_gui)
sys.modules.setdefault("PyQt6.QtWidgets", qt_widgets)

# Stub additional modules imported by mainwindow
sys.modules.setdefault("app_util", types.ModuleType("app_util")).reveal_directory = lambda *a, **k: None

# Provide minimal stubs for UI-dependent modules
sys.modules.setdefault("newproject", types.ModuleType("newproject")).NewProject = type("NewProject", (), {})
sys.modules.setdefault("exportingwindow", types.ModuleType("exportingwindow")).Exporting = type("Exporting", (), {})
sys.modules.setdefault("plugininfodialog", types.ModuleType("plugininfodialog")).PluginInfoDialog = type("PluginInfoDialog", (), {})

# The window imports its own submodules (`from ui.items_tab_widget import ...`),
# so this placeholder package needs a real __path__ or those imports fail with
# "'ui' is not a package". Submodules stubbed below still win -- they are
# already in sys.modules by the time anything asks for them.
_ui_pkg = sys.modules.setdefault("ui", types.ModuleType("ui"))
if not hasattr(_ui_pkg, "__path__"):
    _ui_pkg.__path__ = [os.path.join(ROOT_DIR, "ui")]
ui_main = types.ModuleType("ui.ui_mainwindow")
class Ui_MainWindow:
    def setupUi(self, _):
        pass
ui_main.Ui_MainWindow = Ui_MainWindow
sys.modules["ui.ui_mainwindow"] = ui_main
sys.modules.setdefault("ui.delegates.pokedexitemdelegate", types.ModuleType("ui.delegates.pokedexitemdelegate")).PokedexItemDelegate = type("PokedexItemDelegate", (), {})

import mainwindow

class EmptyLabelTest(unittest.TestCase):
    def test_update_save_handles_empty_label(self):
        item = QTreeWidgetItem(["", "Species"])
        tree = QTreeWidget([item])
        mw = mainwindow.MainWindow.__new__(mainwindow.MainWindow)
        # MainWindow.__init__ is skipped on purpose (it would need a real Qt
        # app), so any plain attribute the methods under test read has to be
        # seeded here. _loading_depth is the re-entrancy counter that
        # update_data and the loading guard increment.
        mw._loading_depth = 0
        mw.previous_selected_species = None
        mw.save_species_data = lambda *a, **k: None
        mw.save_data = lambda *a, **k: None
        mw.ui = _UiStub(
            tree_pokemon=tree,
            mainTabs=types.SimpleNamespace(currentIndex=lambda: 0),
        )
        # Should not raise
        mw.update_save()
        self.assertEqual(item.text(0), "")

if __name__ == "__main__":
    unittest.main()
