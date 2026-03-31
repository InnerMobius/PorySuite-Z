import os
import sys
import types
import unittest
import tempfile
import contextlib
import io
import unittest.mock

ROOT_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if ROOT_DIR not in sys.path:
    sys.path.insert(0, ROOT_DIR)

# Minimal PyQt6 stubs
qt_module = types.ModuleType("PyQt6")
qt_core = types.ModuleType("PyQt6.QtCore")
qt_gui = types.ModuleType("PyQt6.QtGui")
qt_widgets = types.ModuleType("PyQt6.QtWidgets")
qt_core.pyqtSignal = lambda *a, **k: None
qt_core.Qt = type(
    "Qt",
    (),
    {"ItemDataRole": type("ItemDataRole", (), {"UserRole": 32})},
)
qt_core.QEvent = type("QEvent", (), {})
qt_core.QSignalBlocker = contextlib.nullcontext
qt_gui.QImage = type("QImage", (), {})
qt_gui.QPixmap = type("QPixmap", (), {})
qt_gui.QFont = type("QFont", (), {})
qt_gui.QKeyEvent = type("QKeyEvent", (), {})
qt_gui.QKeySequence = type("QKeySequence", (), {})

class QTreeWidgetItem:
    def __init__(self, texts=None):
        self._texts = list(texts or [])
        self._data = {}
    def text(self, column):
        return self._texts[column] if column < len(self._texts) else ""
    def setText(self, column, value):
        while column >= len(self._texts):
            self._texts.append("")
        self._texts[column] = value
    def data(self, column, role):
        return self._data.get((column, role))
    def setData(self, column, role, value):
        self._data[(column, role)] = value

class QTreeWidget:
    def __init__(self):
        self._items = []
        self._selected = []
    def clear(self):
        self._items.clear()
    def addTopLevelItem(self, item):
        self._items.append(item)
    def insertTopLevelItem(self, index, item):
        self._items.insert(index, item)
    def topLevelItemCount(self):
        return len(self._items)
    def topLevelItem(self, index):
        return self._items[index]
    def indexOfTopLevelItem(self, item):
        try:
            return self._items.index(item)
        except ValueError:
            return -1
    def takeTopLevelItem(self, index):
        return self._items.pop(index)
    def selectedItems(self):
        return self._selected
    def setCurrentItem(self, item):
        self._selected = [item]
    def columnCount(self):
        return 3
    def resizeColumnToContents(self, *_):
        pass

qt_widgets.QApplication = type("QApplication", (), {})
class QMainWindow:
    def setWindowModified(self, *_):
        pass
qt_widgets.QMainWindow = QMainWindow
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
sys.modules["PyQt6"] = qt_module
sys.modules["PyQt6.QtCore"] = qt_core
sys.modules["PyQt6.QtGui"] = qt_gui
sys.modules["PyQt6.QtWidgets"] = qt_widgets

# Stub extra modules used by mainwindow
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

import mainwindow
import importlib
import importlib.util
importlib.reload(mainwindow)
mainwindow.QTreeWidgetItem = QTreeWidgetItem

pkg_name = "plugins.pokefirered"
sys.modules.setdefault("plugins", types.ModuleType("plugins"))
pkg_module = types.ModuleType(pkg_name)
pkg_module.__path__ = [os.path.join(ROOT_DIR, "plugins", "pokefirered")]
sys.modules[pkg_name] = pkg_module
spec = importlib.util.spec_from_file_location(
    f"{pkg_name}.pokemon_data_extractor",
    os.path.join(ROOT_DIR, "plugins", "pokefirered", "pokemon_data_extractor.py"),
)
pdextractor = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = pdextractor
spec.loader.exec_module(pdextractor)

class DummyBox:
    def __init__(self, text="", data=0):
        self.index = None
        self._text = text
        self._data = data
        self.editable = True
    def setCurrentIndex(self, idx):
        self.index = idx
    def currentText(self):
        return self._text
    def findText(self, text):
        return 0
    def currentData(self):
        return self._data
    def findData(self, data):
        return 0
    def setEnabled(self, enabled=True):
        self._enabled = bool(enabled)
    def isEnabled(self):
        return getattr(self, "_enabled", True)
    def setEditable(self, val):
        self.editable = val
    def isEditable(self):
        return self.editable
    def clear(self):
        pass
    def addItem(self, *a, **k):
        pass
    def setEditText(self, text):
        self._text = text

class DummyWidget:
    def __init__(self, val=0):
        self._val = val
        self.style = None
    def setText(self, *_):
        pass
    def setPlainText(self, *_):
        pass
    def setValue(self, v):
        self._val = v
    def value(self):
        return self._val
    def setStyleSheet(self, *_):
        self.style = _
    def setCurrentIndex(self, *_):
        pass
    def setEnabled(self, *_):
        pass
    def currentData(self):
        return 0

class EvolutionsTreeTest(unittest.TestCase):
    def setUp(self):
        self.mw = mainwindow.MainWindow.__new__(mainwindow.MainWindow)
        self.mw.update_gender_ratio = lambda *_: None
        self.mw.setWindowModified = lambda *_: None
        self.tree = QTreeWidget()
        self.mw.ui = types.SimpleNamespace(
            species_name=DummyWidget(),
            dex_num=DummyWidget(),
            species_category=DummyWidget(),
            species_description=DummyWidget(),
            base_hp=DummyWidget(),
            base_atk=DummyWidget(),
            base_def=DummyWidget(),
            base_speed=DummyWidget(),
            base_spatk=DummyWidget(),
            base_spdef=DummyWidget(),
            type1=DummyBox(),
            type2=DummyBox(),
            ability1=DummyBox(),
            ability2=DummyBox(),
            ability_hidden=DummyBox(),
            evs_hp=DummyWidget(),
            evs_atk=DummyWidget(),
            evs_def=DummyWidget(),
            evs_speed=DummyWidget(),
            evs_spatk=DummyWidget(),
            evs_spdef=DummyWidget(),
            catch_rate=DummyWidget(),
            exp_yield=DummyWidget(),
            gender_ratio=DummyWidget(),
            held_item_common=DummyWidget(),
            held_item_rare=DummyWidget(),
            egg_cycles=DummyWidget(),
            egg_group_1=DummyBox(),
            egg_group_2=DummyBox(),
            exp_growth_rate=DummyWidget(),
            base_friendship=DummyWidget(),
            safari_zone_flee_rate=DummyWidget(),
            frontPic_0=DummyWidget(),
            frontPic_1=DummyWidget(),
            backPic=DummyWidget(),
            iconPic=DummyWidget(),
            footprintPic=DummyWidget(),
            evolutions=self.tree,
            evo_species=DummyBox("SPECIES_B"),
            evo_method=DummyBox("EVO_LEVEL"),
            evo_param=DummyBox("10"),
            evoDeleteButton=DummyWidget(),
            pushButton_7=DummyWidget(),
            tab_pokemon_data=DummyWidget(),
        )
        self.mw.type_index_map = {"TYPE_NORMAL": 1}
        self.data = {
            "SPECIES_A": {"species_info": {"evolutions": [
                {"targetSpecies": "SPECIES_B", "method": "EVO_LEVEL", "param": 10}
            ]}}
        }
        def get_evolutions(sp):
            return self.data.get(sp, {}).get("species_info", {}).get("evolutions", [])

        def set_evolutions(sp, value):
            self.data.setdefault(sp, {}).setdefault("species_info", {})["evolutions"] = value
        def set_species_info(sp, key, value, form=None):
            self.data.setdefault(sp, {}).setdefault("species_info", {})[key] = value
        def get_species_info(sp, key, form=None):
            return self.data.get(sp, {}).get("species_info", {}).get(key)
        self.mw.source_data = types.SimpleNamespace(
            get_evolutions=get_evolutions,
            set_evolutions=set_evolutions,
            set_species_info=set_species_info,
            get_species_info=get_species_info,
            get_species_data=lambda *a, **k: 0,
            get_constant_data=lambda *a, **k: {"value": 0, "name": ""},
            get_item_data=lambda *a, **k: 0,
            get_species_image_path=lambda *a, **k: None,
            get_species_ability=lambda *a, **k: 0,
            get_ability_data=lambda *a, **k: 0,
            get_ability=lambda *a, **k: {"id": 0},
            get_constant=lambda c: {"EVO_LEVEL": {"name": "EVO_LEVEL"}} if c == "evolution_types" else {},
        )

    def test_update_data_populates_tree(self):
        self.mw.update_data("SPECIES_A")
        self.assertEqual(self.tree.topLevelItemCount(), 2)
        item = self.tree.topLevelItem(0)
        self.assertEqual(item.text(0), "SPECIES_B")
        self.assertEqual(item.text(1), "EVO_LEVEL")
        self.assertEqual(item.text(2), "10")
        last = self.tree.topLevelItem(1)
        self.assertEqual(last.text(0), "Add New Evolution...")

    def test_add_and_save_evolution(self):
        self.mw.update_data("SPECIES_A")
        self.tree.setCurrentItem(self.tree.topLevelItem(1))
        self.mw.add_evolution()
        self.assertEqual(self.tree.topLevelItemCount(), 3)
        self.mw.save_species_data("SPECIES_A")
        evols = self.data["SPECIES_A"]["species_info"]["evolutions"]
        self.assertEqual(len(evols), 2)
        self.assertEqual(evols[1]["targetSpecies"], "SPECIES_B")
        self.assertEqual(evols[1]["method"], "EVO_LEVEL")
        self.assertEqual(evols[1]["param"], 10)

    def test_delete_evolution(self):
        self.mw.update_data("SPECIES_A")
        first = self.tree.topLevelItem(0)
        self.tree.setCurrentItem(first)
        self.mw.delete_evolution()
        self.assertEqual(self.tree.topLevelItemCount(), 1)
        self.mw.save_species_data("SPECIES_A")
        self.assertEqual(self.data["SPECIES_A"]["species_info"]["evolutions"], [])

    def test_update_data_uses_manager_evolutions(self):
        self.data["SPECIES_M"] = {"species_info": {}}
        manager_evos = {"SPECIES_M": [
            {"targetSpecies": "SPECIES_N", "method": "EVO_LEVEL", "param": 5}
        ]}

        def get_evolutions(sp):
            return manager_evos.get(sp, [])

        def set_evolutions(sp, evols):
            manager_evos[sp] = evols

        self.mw.source_data.get_evolutions = get_evolutions
        self.mw.source_data.set_evolutions = set_evolutions

        self.mw.update_data("SPECIES_M")
        self.assertEqual(self.tree.topLevelItemCount(), 2)
        item = self.tree.topLevelItem(0)
        self.assertEqual(item.text(0), "SPECIES_N")
        self.assertEqual(item.text(1), "EVO_LEVEL")
        self.assertEqual(item.text(2), "5")

    def test_method_constant_preserved_after_edit(self):
        self.mw.ui.evo_method.currentData = lambda: "EVO_LEVEL"
        self.mw.update_data("SPECIES_A")
        item = self.tree.topLevelItem(0)
        if hasattr(item, "data"):
            self.assertEqual(
                item.data(1, qt_core.Qt.ItemDataRole.UserRole), "EVO_LEVEL"
            )
        self.tree.setCurrentItem(item)
        self.mw.update_evolutions()
        self.mw.ui.evo_param.setEditText("20")
        self.mw.edit_evolution()
        if hasattr(item, "data"):
            self.assertEqual(
                item.data(1, qt_core.Qt.ItemDataRole.UserRole), "EVO_LEVEL"
            )

    def test_select_existing_evolution_preserves_method_param(self):
        self.mw.update_data("SPECIES_A")
        item = self.tree.topLevelItem(0)
        self.mw.ui.evo_method.setEditText("EVO_TRADE")
        self.mw.ui.evo_param.setEditText("0")
        self.tree.setCurrentItem(item)
        self.mw.update_evolutions()
        self.assertEqual(item.text(1), "EVO_LEVEL")
        self.assertEqual(item.text(2), "10")

class EvolutionsTreeFileLoadTest(unittest.TestCase):
    """Load evolutions from parsed JSON and populate the tree."""

    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        root = self.tempdir.name
        os.makedirs(os.path.join(root, "src", "data", "pokemon"), exist_ok=True)
        os.makedirs(os.path.join(root, "src", "data", "text"), exist_ok=True)
        os.makedirs(os.path.join(root, "include", "constants"), exist_ok=True)

        with open(os.path.join(root, "src", "data", "pokemon", "species_info.h"), "w") as f:
            f.write(
                """[SPECIES_TEST] = {\n    .baseHP = 1, .types = {TYPE_NORMAL, TYPE_NORMAL}, .abilities = {ABILITY_NONE, ABILITY_NONE, ABILITY_NONE},\n},\n[SPECIES_OTHER] = {\n    .baseHP = 1, .types = {TYPE_NORMAL, TYPE_NORMAL}, .abilities = {ABILITY_NONE, ABILITY_NONE, ABILITY_NONE},\n},"""
            )

        with open(os.path.join(root, "src", "data", "pokemon", "evolution.h"), "w") as f:
            f.write(
                """const struct Evolution gEvolutionTable[NUM_SPECIES][EVOS_PER_MON] = {\n    [SPECIES_TEST] = {{EVO_LEVEL, 5, SPECIES_OTHER}},\n};"""
            )

        with open(os.path.join(root, "src", "data", "text", "species_names.h"), "w") as f:
            f.write(
                "const u8 gSpeciesNames[][POKEMON_NAME_LENGTH + 1] = {\n    [SPECIES_TEST] = _(\"TEST\"),\n    [SPECIES_OTHER] = _(\"OTHER\"),\n};"
            )

        with open(os.path.join(root, "include", "constants", "species.h"), "w") as f:
            f.write(
                "#define SPECIES_TEST 0\n#define SPECIES_OTHER 1\n#define SPECIES_EGG 2\n#define NUM_SPECIES SPECIES_EGG\n"
            )

        self.project_info = {"dir": root, "project_name": "proj"}
        with contextlib.redirect_stdout(io.StringIO()):
            with unittest.mock.patch.object(pdextractor.PokedexDataExtractor, "extract_data", return_value={"national_dex": []}):
                ext = pdextractor.SpeciesDataExtractor(self.project_info, "species.json")
                self.data = ext.extract_data()

        self.mw = mainwindow.MainWindow.__new__(mainwindow.MainWindow)
        self.mw.update_gender_ratio = lambda *_: None
        self.mw.setWindowModified = lambda *_: None
        self.tree = QTreeWidget()
        self.mw.ui = types.SimpleNamespace(
            species_name=DummyWidget(),
            dex_num=DummyWidget(),
            species_category=DummyWidget(),
            species_description=DummyWidget(),
            base_hp=DummyWidget(),
            base_atk=DummyWidget(),
            base_def=DummyWidget(),
            base_speed=DummyWidget(),
            base_spatk=DummyWidget(),
            base_spdef=DummyWidget(),
            type1=DummyBox(),
            type2=DummyBox(),
            ability1=DummyBox(),
            ability2=DummyBox(),
            ability_hidden=DummyBox(),
            evs_hp=DummyWidget(),
            evs_atk=DummyWidget(),
            evs_def=DummyWidget(),
            evs_speed=DummyWidget(),
            evs_spatk=DummyWidget(),
            evs_spdef=DummyWidget(),
            catch_rate=DummyWidget(),
            exp_yield=DummyWidget(),
            gender_ratio=DummyWidget(),
            held_item_common=DummyWidget(),
            held_item_rare=DummyWidget(),
            egg_cycles=DummyWidget(),
            egg_group_1=DummyBox(),
            egg_group_2=DummyBox(),
            exp_growth_rate=DummyWidget(),
            base_friendship=DummyWidget(),
            safari_zone_flee_rate=DummyWidget(),
            frontPic_0=DummyWidget(),
            frontPic_1=DummyWidget(),
            backPic=DummyWidget(),
            iconPic=DummyWidget(),
            footprintPic=DummyWidget(),
            evolutions=self.tree,
            evo_species=DummyBox("SPECIES_OTHER"),
            evo_method=DummyBox("EVO_LEVEL"),
            evo_param=DummyBox("5"),
            evoDeleteButton=DummyWidget(),
            pushButton_7=DummyWidget(),
            tab_pokemon_data=DummyWidget(),
        )
        self.mw.type_index_map = {"TYPE_NORMAL": 1}

        def get_evolutions(sp):
            return self.data.get(sp, {}).get("species_info", {}).get("evolutions", [])

        def set_evolutions(sp, value):
            self.data.setdefault(sp, {}).setdefault("species_info", {})["evolutions"] = value

        def set_species_info(sp, key, value, form=None):
            self.data.setdefault(sp, {}).setdefault("species_info", {})[key] = value

        def get_species_info(sp, key, form=None):
            return self.data.get(sp, {}).get("species_info", {}).get(key)

        self.mw.source_data = types.SimpleNamespace(
            get_evolutions=get_evolutions,
            set_evolutions=set_evolutions,
            set_species_info=set_species_info,
            get_species_info=get_species_info,
            get_species_data=lambda *a, **k: 0,
            get_constant_data=lambda *a, **k: {"value": 0, "name": ""},
            get_item_data=lambda *a, **k: 0,
            get_species_image_path=lambda *a, **k: None,
            get_species_ability=lambda *a, **k: 0,
            get_ability_data=lambda *a, **k: 0,
            get_ability=lambda *a, **k: {"id": 0},
            get_constant=lambda c: {"EVO_LEVEL": {"name": "EVO_LEVEL"}} if c == "evolution_types" else {},
        )

    def tearDown(self):
        self.tempdir.cleanup()

    def test_tree_loads_parsed_evolution(self):
        self.mw.update_data("SPECIES_TEST")
        self.assertEqual(self.tree.topLevelItemCount(), 2)
        item = self.tree.topLevelItem(0)
        self.assertEqual(item.text(0), "SPECIES_OTHER")
        self.assertEqual(item.text(1), "EVO_LEVEL")
        self.assertEqual(item.text(2), "5")

if __name__ == "__main__":
    unittest.main()
