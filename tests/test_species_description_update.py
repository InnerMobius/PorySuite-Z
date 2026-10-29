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
    def setCurrentItem(self, item):
        # _select_species_in_tree syncs the tree to the pokedex selection.
        self._selected = [item]
    def currentItem(self):
        return self._selected[0] if self._selected else None

qt_widgets.QTreeWidget = QTreeWidget
qt_widgets.QTreeWidgetItem = QTreeWidgetItem

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
sys.modules["PyQt6"] = qt_module
sys.modules["PyQt6.QtCore"] = qt_core
sys.modules["PyQt6.QtGui"] = qt_gui
sys.modules["PyQt6.QtWidgets"] = qt_widgets

# Stub additional modules imported by mainwindow
sys.modules.setdefault("app_util", types.ModuleType("app_util"))
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

    def get_evolutions(self, species):
        # update_pokedex_entry compares the current evolution chain; these
        # fixtures define none.
        return []

    def set_species_info(self, species, field, value, form=None):
        # The category name is edited through the pokedex panel, which
        # writes it back here. Record it so the test can read it back.
        self.species_info.setdefault(species, {})[field] = value
class PokedexEntryUpdateTest(unittest.TestCase):
    def setUp(self):
        self.mw = mainwindow.MainWindow.__new__(mainwindow.MainWindow)
        # MainWindow.__init__ is skipped on purpose (it would need a real Qt
        # app), so any plain attribute the methods under test read has to be
        # seeded here. _loading_depth is the re-entrancy counter that
        # update_data and the loading guard increment.
        self.mw._loading_depth = 0
        # update_pokedex_entry reads these to avoid saving the wrong
        # species when the selection moves; __init__ would have set them.
        self.mw.previous_selected_species = None
        self.mw.previous_selected_form = None
        self.mw.project_info = {"dir": "", "project_name": "proj"}
        self.mw.source_data = DummySource()
        self.Qt = qt_core.Qt
        self.mw.ui = _UiStub(
            species_name=types.SimpleNamespace(setText=lambda *_: None),
            dex_num=types.SimpleNamespace(setText=lambda *_: None),
            species_category=types.SimpleNamespace(setText=lambda *_: None),
            species_description=QPlainTextEdit(),
            list_pokedex_national=QListWidget(),
            # update_pokedex_entry syncs the species tree to the
            # pokedex selection, so it needs a real tree even when
            # this case does not look at it.
            tree_pokemon=QTreeWidget(),
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
        # MainWindow.__init__ is skipped on purpose (it would need a real Qt
        # app), so any plain attribute the methods under test read has to be
        # seeded here. _loading_depth is the re-entrancy counter that
        # update_data and the loading guard increment.
        self.mw._loading_depth = 0
        # update_pokedex_entry reads these to avoid saving the wrong
        # species when the selection moves; __init__ would have set them.
        self.mw.previous_selected_species = None
        self.mw.previous_selected_form = None
        self.mw.project_info = {"dir": "", "project_name": "proj"}
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
        self.mw.ui = _UiStub(
            species_name=types.SimpleNamespace(setText=lambda *_: None),
            dex_num=types.SimpleNamespace(setText=lambda *_: None),
            species_category=types.SimpleNamespace(setText=lambda *_: None),
            species_description=QPlainTextEdit(),
            list_pokedex_national=QListWidget(),
            # update_pokedex_entry syncs the species tree to the
            # pokedex selection, so it needs a real tree even when
            # this case does not look at it.
            tree_pokemon=QTreeWidget(),
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
        # MainWindow.__init__ is skipped on purpose (it would need a real Qt
        # app), so any plain attribute the methods under test read has to be
        # seeded here. _loading_depth is the re-entrancy counter that
        # update_data and the loading guard increment.
        self.mw._loading_depth = 0
        # update_pokedex_entry reads these to avoid saving the wrong
        # species when the selection moves; __init__ would have set them.
        self.mw.previous_selected_species = None
        self.mw.previous_selected_form = None
        self.mw.project_info = {"dir": "", "project_name": "proj"}
        self.mw.source_data = DummySource()
        self.Qt = qt_core.Qt
        self.mw.save_species_data = lambda *a, **k: False
        self.mw.setWindowModified = lambda *a, **k: None
        self.mw.update_data = lambda *a, **k: None
        self.mw.sender = lambda: None
        self.mw.previous_selected_species = None
        self.mw.previous_selected_form = None
        self.mw.project_info = {"dir": "", "project_name": "proj"}
        self.item_a = QTreeWidgetItem(["Test", "SPECIES_TEST", ""])
        self.item_b = QTreeWidgetItem(["Other", "SPECIES_OTHER", ""])
        tree = QTreeWidget([self.item_a, self.item_b])
        self.mw.ui = _UiStub(
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
        # MainWindow.__init__ is skipped on purpose (it would need a real Qt
        # app), so any plain attribute the methods under test read has to be
        # seeded here. _loading_depth is the re-entrancy counter that
        # update_data and the loading guard increment.
        self.mw._loading_depth = 0
        # update_pokedex_entry reads these to avoid saving the wrong
        # species when the selection moves; __init__ would have set them.
        self.mw.previous_selected_species = None
        self.mw.previous_selected_form = None
        self.mw.project_info = {"dir": "", "project_name": "proj"}
        self.mw.source_data = DummySource()
        self.Qt = qt_core.Qt
        self.mw.save_species_data = lambda *a, **k: False
        self.mw.setWindowModified = lambda *a, **k: None
        self.mw.update_data = lambda *a, **k: None
        self.mw.sender = lambda: None
        self.mw.previous_selected_species = None
        self.mw.previous_selected_form = None
        self.mw.project_info = {"dir": "", "project_name": "proj"}
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

        self.mw.ui = _UiStub(
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
