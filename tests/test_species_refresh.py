import os
import sys
import types
import unittest

ROOT_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if ROOT_DIR not in sys.path:
    sys.path.insert(0, ROOT_DIR)

# Minimal PyQt6 stubs
qt_module = types.ModuleType("PyQt6")
qt_core = types.ModuleType("PyQt6.QtCore")
qt_gui = types.ModuleType("PyQt6.QtGui")
qt_widgets = types.ModuleType("PyQt6.QtWidgets")
qt_core.pyqtSignal = lambda *a, **k: None
class _DummyBlocker:
    def __init__(self, *_):
        pass
    def __enter__(self):
        return self
    def __exit__(self, *a):
        return False
qt_core.QSignalBlocker = _DummyBlocker
qt_widgets.QApplication = type("QApplication", (), {})
qt_widgets.QMainWindow = type("QMainWindow", (), {})
qt_widgets.QLabel = type("QLabel", (), {})
qt_widgets.QProgressBar = type("QProgressBar", (), {})
qt_widgets.QListWidgetItem = type("QListWidgetItem", (), {})
qt_widgets.QTableWidgetItem = type("QTableWidgetItem", (), {})
qt_widgets.QMessageBox = type("QMessageBox", (), {})
qt_widgets.QInputDialog = type("QInputDialog", (), {})
qt_widgets.QPushButton = type("QPushButton", (), {})

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
        self._selected = []
    def selectedItems(self):
        return self._selected
    def setSelected(self, item):
        self._selected = [item]
    def addTopLevelItem(self, item):
        self._items.append(item)
    def topLevelItemCount(self):
        return len(self._items)
    def topLevelItem(self, index):
        return self._items[index]

qt_widgets.QTreeWidget = QTreeWidget
qt_widgets.QTreeWidgetItem = QTreeWidgetItem
qt_module.QtCore = qt_core
qt_module.QtGui = qt_gui
qt_module.QtWidgets = qt_widgets
sys.modules.setdefault("PyQt6", qt_module)
sys.modules.setdefault("PyQt6.QtCore", qt_core)
sys.modules.setdefault("PyQt6.QtGui", qt_gui)
sys.modules.setdefault("PyQt6.QtWidgets", qt_widgets)

# Stub extra modules
sys.modules.setdefault("app_util", types.ModuleType("app_util"))
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

import importlib
import mainwindow
importlib.reload(mainwindow)

class SpeciesRefreshTest(unittest.TestCase):
    def setUp(self):
        self.mw = mainwindow.MainWindow.__new__(mainwindow.MainWindow)
        self.calls = []
        self.mw.update_data = lambda sp, form=None: self.calls.append((sp, form))
        self.mw._select_pokedex_item = lambda *_: None
        self.mw.update_pokedex_entry = lambda *_: None
        self.mw.save_species_data = lambda *a, **k: False
        self.mw.setWindowModified = lambda *a, **k: None
        self.item_a = QTreeWidgetItem(["A", "SPECIES_A", ""])
        self.item_b = QTreeWidgetItem(["B", "SPECIES_B", ""])
        tree = QTreeWidget([self.item_a, self.item_b])
        self.mw.ui = types.SimpleNamespace(tree_pokemon=tree)
        self.mw.source_data = types.SimpleNamespace(
            get_pokemon_data=lambda: {"SPECIES_A": {}, "SPECIES_B": {}}
        )
        self.mw.previous_selected_species = "SPECIES_A"
        self.mw.previous_selected_form = None

    def test_refresh_uses_tree_selection(self):
        self.mw.ui.tree_pokemon.setSelected(self.item_b)
        self.mw.refresh_current_species()
        self.assertEqual(self.calls[-1], ("SPECIES_B", None))

    def test_update_tree_refreshes_current_species(self):
        self.mw.ui.tree_pokemon.setSelected(self.item_a)
        self.calls.clear()
        self.mw.update_tree_pokemon()
        self.assertIn(("SPECIES_A", None), self.calls)
        self.calls.clear()
        self.mw.ui.tree_pokemon.setSelected(self.item_b)
        self.mw.update_tree_pokemon()
        self.assertIn(("SPECIES_B", None), self.calls)

if __name__ == "__main__":
    unittest.main()
