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
qt_widgets.QApplication = type("QApplication", (), {})
qt_widgets.QMainWindow = type("QMainWindow", (), {})
qt_widgets.QLabel = type("QLabel", (), {})
qt_widgets.QProgressBar = type("QProgressBar", (), {})
qt_widgets.QTreeWidgetItem = type("QTreeWidgetItem", (), {})
qt_widgets.QTreeWidget = type("QTreeWidget", (), {})
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

# Stub additional modules used by mainwindow
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

class DummyBox:
    def __init__(self):
        self.index = None
    def setCurrentIndex(self, idx):
        self.index = idx

class DummyWidget:
    def setText(self, *_):
        pass
    def setPlainText(self, *_):
        pass
    def setValue(self, *_):
        pass
    def setStyleSheet(self, *_):
        pass
    def setCurrentIndex(self, *_):
        pass
    def setEnabled(self, *_):
        pass

class DummyEvo:
    def clear(self):
        pass
    def addTopLevelItem(self, *_):
        pass
    def columnCount(self):
        return 0
    def resizeColumnToContents(self, *_):
        pass

class DummyTab:
    def __init__(self):
        self.enabled = True
    def setEnabled(self, val):
        self.enabled = val

class InvalidDataTest(unittest.TestCase):
    def setUp(self):
        self.mw = mainwindow.MainWindow.__new__(mainwindow.MainWindow)
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
            evolutions=DummyEvo(),
            tab_pokemon_data=DummyTab(),
            starter1_species=DummyBox(),
            starter1_level=DummyWidget(),
            starter1_item=DummyBox(),
            starter2_species=DummyBox(),
            starter2_level=DummyWidget(),
            starter2_item=DummyBox(),
            starter3_species=DummyBox(),
            starter3_level=DummyWidget(),
            starter3_item=DummyBox(),
        )
        self.mw.update_gender_ratio = lambda *_: None
        self.mw.type_index_map = {"TYPE_NORMAL": 1}

    def test_invalid_items_do_not_disable_tab(self):
        def get_species_info(sp, key, form=None):
            if key in {"types", "eggGroups"}:
                return []
            if key in {"itemCommon", "itemRare"}:
                return "INVALID_ITEM"
            return 0

        def get_evolutions(sp):
            return []

        self.mw.source_data = types.SimpleNamespace(
            get_species_info=get_species_info,
            get_evolutions=get_evolutions,
            get_species_data=lambda *a, **k: 0,
            get_constant_data=lambda *a, **k: {"value": 0, "name": ""},
            get_item_data=lambda *a, **k: None,
            get_species_image_path=lambda *a, **k: None,
            get_species_ability=lambda *a, **k: 0,
            get_ability_data=lambda *a, **k: 0,
        )
        self.mw.update_data("A")
        self.assertTrue(self.mw.ui.tab_pokemon_data.enabled)

    def test_invalid_starter_does_not_disable_tab(self):
        def get_species_info(sp, key, form=None):
            if key in {"types", "eggGroups"}:
                return []
            return 0

        def get_evolutions(sp):
            return []

        self.mw.source_data = types.SimpleNamespace(
            get_species_info=get_species_info,
            get_evolutions=get_evolutions,
            get_species_data=lambda *a, **k: 0,
            get_constant_data=lambda *a, **k: {"value": 0, "name": ""},
            get_item_data=lambda *a, **k: 0,
            get_species_image_path=lambda *a, **k: None,
            get_species_ability=lambda *a, **k: 0,
            get_ability_data=lambda *a, **k: 0,
            get_pokemon_starters=lambda: [{"species": "MISSING"}],
        )
        # Simulate initial starter population
        mw = self.mw
        widgets = [
            (mw.ui.starter1_species, mw.ui.starter1_level, mw.ui.starter1_item),
            (mw.ui.starter2_species, mw.ui.starter2_level, mw.ui.starter2_item),
            (mw.ui.starter3_species, mw.ui.starter3_level, mw.ui.starter3_item),
        ]
        starters = mw.source_data.get_pokemon_starters()
        for idx, starter in enumerate(starters):
            if idx >= len(widgets):
                break
            species_box, level_spin, item_box = widgets[idx]
            species_idx = species_box.findData(starter.get("species")) if hasattr(species_box, "findData") else -1
            if species_idx == -1:
                species_idx = 0
            if hasattr(species_box, "setCurrentIndex"):
                species_box.setCurrentIndex(species_idx)
            level_spin.setValue(starter.get("level", 5))
            item_idx = item_box.findData(starter.get("item")) if hasattr(item_box, "findData") else -1
            if item_idx == -1:
                item_idx = 0
            if hasattr(item_box, "setCurrentIndex"):
                item_box.setCurrentIndex(item_idx)

        # Ensure selecting species still works
        self.mw.update_data("A")
        self.assertTrue(self.mw.ui.tab_pokemon_data.enabled)

if __name__ == "__main__":
    unittest.main()
