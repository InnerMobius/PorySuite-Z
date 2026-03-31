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

qt_core.pyqtSignal = lambda *a, **k: None
qt_core.Qt = type("Qt", (), {"ItemDataRole": type("ItemDataRole", (), {"UserRole": 32})})
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

class QListWidgetItem:
    def __init__(self, text=""):
        self._text = text
        self._data = {}
    def data(self, role):
        return self._data.get(role)
    def setData(self, role, value):
        self._data[role] = value
    def text(self):
        return self._text

class QListWidget:
    def __init__(self, items=None):
        self._items = list(items or [])
        self._selected = []
    def addItem(self, item):
        if isinstance(item, str):
            item = QListWidgetItem(item)
        self._items.append(item)
    def count(self):
        return len(self._items)
    def item(self, index):
        return self._items[index]
    def selectedItems(self):
        return self._selected
    def setSelected(self, item):
        self._selected = [item]
    def setCurrentItem(self, item):
        self._selected = [item]
    def clearSelection(self):
        self._selected = []
    class Signal:
        def __init__(self):
            self._target = None
        def connect(self, func):
            self._target = func
    itemSelectionChanged = Signal()

class QPlainTextEdit:
    def __init__(self):
        self.text = ""
        self.read_only = False
    def setPlainText(self, txt):
        self.text = txt
    def toPlainText(self):
        return self.text
    def isReadOnly(self):
        return self.read_only

qt_widgets.QApplication = type("QApplication", (), {})
qt_widgets.QMainWindow = type("QMainWindow", (), {})
qt_widgets.QListWidgetItem = QListWidgetItem
qt_widgets.QTableWidgetItem = type("QTableWidgetItem", (), {})
qt_widgets.QListWidget = QListWidget
qt_widgets.QLabel = type("QLabel", (), {})
qt_widgets.QProgressBar = type("QProgressBar", (), {})
qt_widgets.QPlainTextEdit = QPlainTextEdit
qt_widgets.QInputDialog = type("QInputDialog", (), {})
qt_widgets.QMessageBox = type("QMessageBox", (), {})
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

sys.modules["PyQt6"] = qt_module
sys.modules["PyQt6.QtCore"] = qt_core
sys.modules["PyQt6.QtGui"] = qt_gui
sys.modules["PyQt6.QtWidgets"] = qt_widgets

# Stub additional modules imported by mainwindow
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


class DummySource:
    def __init__(self):
        self.species_by_dex = {
            "NATIONAL_DEX_TEST": "SPECIES_TEST",
            "NATIONAL_DEX_OTHER": "SPECIES_OTHER",
        }
        self.species_data = {
            "SPECIES_TEST": {"name": "Test", "dex_num": 1, "dex_constant": "NATIONAL_DEX_TEST"},
            "SPECIES_OTHER": {"name": "Other", "dex_num": 2, "dex_constant": "NATIONAL_DEX_OTHER"},
        }
        self.species_info = {
            "SPECIES_TEST": {"categoryName": "Alpha", "baseHP": 10},
            "SPECIES_OTHER": {"categoryName": "Beta", "baseHP": 20},
        }
        self.data = {"pokedex": types.SimpleNamespace(data={"national_dex": [
            {"dex_constant": "NATIONAL_DEX_TEST", "categoryName": "Alpha", "descriptionText": "Desc A"},
            {"dex_constant": "NATIONAL_DEX_OTHER", "categoryName": "Beta", "descriptionText": "Desc B"},
        ], "pokedex_text": {}})}

    def get_species_by_dex_constant(self, const):
        return self.species_by_dex.get(const)

    def get_pokemon_data(self):
        return {"SPECIES_TEST": {}, "SPECIES_OTHER": {}}

    def get_species_data(self, species, field):
        return self.species_data.get(species, {}).get(field)

    def get_species_info(self, species, field, form=None):
        return self.species_info.get(species, {}).get(field)


class PokedexEntryUpdateTest(unittest.TestCase):
    def setUp(self):
        self.mw = mainwindow.MainWindow.__new__(mainwindow.MainWindow)
        self.mw.source_data = DummySource()
        self.Qt = qt_core.Qt
        self.mw.ui = types.SimpleNamespace(
            species_name=types.SimpleNamespace(setText=lambda *_: None),
            dex_num=types.SimpleNamespace(setText=lambda *_: None),
            species_category=types.SimpleNamespace(setText=lambda *_: None),
            species_description=QPlainTextEdit(),
            list_pokedex_national=QListWidget(),
            list_pokedex_regional=QListWidget(),
        )
        self.mw.update_data = lambda *a, **k: None

    def test_description_updates_with_selection(self):
        item_a = QListWidgetItem()
        item_a.setData(self.Qt.ItemDataRole.UserRole, "HOENN_DEX_TEST")
        self.mw.ui.list_pokedex_national.setSelected(item_a)
        self.mw.sender = lambda: self.mw.ui.list_pokedex_national
        self.mw.update_pokedex_entry()
        self.assertEqual(self.mw.ui.species_description.toPlainText(), "Desc A")
        self.assertFalse(self.mw.ui.species_description.isReadOnly())


class StatsRefreshTest(unittest.TestCase):
    def setUp(self):
        self.mw = mainwindow.MainWindow.__new__(mainwindow.MainWindow)
        self.mw.source_data = DummySource()
        self.Qt = qt_core.Qt
        class DummySpin:
            def __init__(self):
                self._val = None
            def setValue(self, v):
                self._val = v
            def value(self):
                return self._val
        self.base_hp = DummySpin()
        self.calls = []
        def update_data(species, form=None):
            self.calls.append(species)
            hp = self.mw.source_data.get_species_info(species, "baseHP")
            self.base_hp.setValue(hp)
        self.mw.update_data = update_data
        self.mw.sender = lambda: self.mw.ui.list_pokedex_national
        self.mw.ui = types.SimpleNamespace(
            species_name=types.SimpleNamespace(setText=lambda *_: None),
            dex_num=types.SimpleNamespace(setText=lambda *_: None),
            species_category=types.SimpleNamespace(setText=lambda *_: None),
            species_description=QPlainTextEdit(),
            list_pokedex_national=QListWidget(),
            list_pokedex_regional=QListWidget(),
            base_hp=self.base_hp,
        )

    def test_stats_update_on_selection(self):
        item_a = QListWidgetItem()
        item_a.setData(self.Qt.ItemDataRole.UserRole, "HOENN_DEX_TEST")
        self.mw.ui.list_pokedex_national.setSelected(item_a)
        self.mw.update_pokedex_entry()
        self.assertEqual(self.base_hp.value(), 10)
        self.assertEqual(self.calls[-1], "SPECIES_TEST")

        item_b = QListWidgetItem()
        item_b.setData(self.Qt.ItemDataRole.UserRole, "HOENN_DEX_OTHER")
        self.mw.ui.list_pokedex_national.setSelected(item_b)
        self.mw.update_pokedex_entry()
        self.assertEqual(self.base_hp.value(), 20)
        self.assertEqual(self.calls[-1], "SPECIES_OTHER")


class TreeSelectionUpdateTest(unittest.TestCase):
    def setUp(self):
        self.mw = mainwindow.MainWindow.__new__(mainwindow.MainWindow)
        self.mw.source_data = DummySource()
        self.Qt = qt_core.Qt
        self.mw.save_species_data = lambda *a, **k: False
        self.mw.setWindowModified = lambda *a, **k: None
        self.mw.update_data = lambda *a, **k: None
        self.mw.sender = lambda: None
        self.mw.previous_selected_species = None
        self.mw.previous_selected_form = None
        self.item_a = QTreeWidgetItem(["Test", "SPECIES_TEST", ""])
        self.item_b = QTreeWidgetItem(["Other", "SPECIES_OTHER", ""])
        tree = QTreeWidget([self.item_a, self.item_b])
        self.mw.ui = types.SimpleNamespace(
            tree_pokemon=tree,
            species_description=QPlainTextEdit(),
            list_pokedex_national=QListWidget(),
            list_pokedex_regional=QListWidget(),
            species_name=types.SimpleNamespace(setText=lambda *_: None),
            dex_num=types.SimpleNamespace(setText=lambda *_: None),
            species_category=types.SimpleNamespace(setText=lambda *_: None),
        )

    def test_tree_selection_updates_description(self):
        item_a = QListWidgetItem()
        item_a.setData(self.Qt.ItemDataRole.UserRole, "HOENN_DEX_TEST")
        self.mw.ui.list_pokedex_national.setSelected(item_a)
        self.mw.ui.tree_pokemon.setSelected(self.item_a)
        self.mw.update_tree_pokemon()
        self.assertEqual(self.mw.ui.species_description.toPlainText(), "Desc A")

        item_b = QListWidgetItem()
        item_b.setData(self.Qt.ItemDataRole.UserRole, "HOENN_DEX_OTHER")
        self.mw.ui.list_pokedex_national.setSelected(item_b)
        self.mw.ui.tree_pokemon.setSelected(self.item_b)
        self.mw.update_tree_pokemon()
        self.assertEqual(self.mw.ui.species_description.toPlainText(), "Desc B")

        item_b = QListWidgetItem()
        item_b.setData(self.Qt.ItemDataRole.UserRole, "HOENN_DEX_OTHER")
        self.mw.ui.list_pokedex_national.setSelected(item_b)
        self.mw.update_pokedex_entry()
        self.assertEqual(self.mw.ui.species_description.toPlainText(), "Desc B")
        self.assertFalse(self.mw.ui.species_description.isReadOnly())


class PokedexListSyncTest(unittest.TestCase):
    def setUp(self):
        self.mw = mainwindow.MainWindow.__new__(mainwindow.MainWindow)
        self.mw.source_data = DummySource()
        self.Qt = qt_core.Qt
        self.mw.save_species_data = lambda *a, **k: False
        self.mw.setWindowModified = lambda *a, **k: None
        self.mw.update_data = lambda *a, **k: None
        self.mw.sender = lambda: None
        self.mw.previous_selected_species = None
        self.mw.previous_selected_form = None
        self.item_a = QTreeWidgetItem(["Test", "SPECIES_TEST", ""])
        self.item_b = QTreeWidgetItem(["Other", "SPECIES_OTHER", ""])
        tree = QTreeWidget([self.item_a, self.item_b])

        nat_a = QListWidgetItem("Test")
        nat_a.setData(self.Qt.ItemDataRole.UserRole, "HOENN_DEX_TEST")
        nat_b = QListWidgetItem("Other")
        nat_b.setData(self.Qt.ItemDataRole.UserRole, "HOENN_DEX_OTHER")
        list_nat = QListWidget([nat_a, nat_b])

        reg_a = QListWidgetItem("Test")
        reg_a.setData(self.Qt.ItemDataRole.UserRole, "HOENN_DEX_TEST")
        reg_b = QListWidgetItem("Other")
        reg_b.setData(self.Qt.ItemDataRole.UserRole, "HOENN_DEX_OTHER")
        list_reg = QListWidget([reg_a, reg_b])

        self.mw.ui = types.SimpleNamespace(
            tree_pokemon=tree,
            species_description=QPlainTextEdit(),
            list_pokedex_national=list_nat,
            list_pokedex_regional=list_reg,
            species_name=types.SimpleNamespace(setText=lambda *_: None),
            dex_num=types.SimpleNamespace(setText=lambda *_: None),
            species_category=types.SimpleNamespace(setText=lambda *_: None),
        )

    def test_list_highlights_follow_tree_selection(self):
        self.mw.ui.tree_pokemon.setSelected(self.item_a)
        self.mw.update_tree_pokemon()
        self.assertEqual(
            self.mw.ui.list_pokedex_national.selectedItems()[0],
            self.mw.ui.list_pokedex_national.item(0),
        )
        self.assertEqual(
            self.mw.ui.list_pokedex_regional.selectedItems()[0],
            self.mw.ui.list_pokedex_regional.item(0),
        )
        self.assertEqual(self.mw.ui.species_description.toPlainText(), "Desc A")

        self.mw.ui.tree_pokemon.setSelected(self.item_b)
        self.mw.update_tree_pokemon()
        self.assertEqual(
            self.mw.ui.list_pokedex_national.selectedItems()[0],
            self.mw.ui.list_pokedex_national.item(1),
        )
        self.assertEqual(
            self.mw.ui.list_pokedex_regional.selectedItems()[0],
            self.mw.ui.list_pokedex_regional.item(1),
        )
        self.assertEqual(self.mw.ui.species_description.toPlainText(), "Desc B")


if __name__ == "__main__":
    unittest.main()
