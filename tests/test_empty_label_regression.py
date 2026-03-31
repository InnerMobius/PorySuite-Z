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

sys.modules.setdefault("ui", types.ModuleType("ui"))
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
        mw.previous_selected_species = None
        mw.save_species_data = lambda *a, **k: None
        mw.save_data = lambda *a, **k: None
        mw.ui = types.SimpleNamespace(
            tree_pokemon=tree,
            mainTabs=types.SimpleNamespace(currentIndex=lambda: 0),
        )
        # Should not raise
        mw.update_save()
        self.assertEqual(item.text(0), "")

if __name__ == "__main__":
    unittest.main()
