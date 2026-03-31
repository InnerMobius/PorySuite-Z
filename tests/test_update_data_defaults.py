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

class UpdateDataDefaultsTest(unittest.TestCase):
    def setUp(self):
        self.mw = mainwindow.MainWindow.__new__(mainwindow.MainWindow)
        self.mw.update_gender_ratio = lambda *_: None
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
            tab_pokemon_data=DummyWidget(),
        )
        self.mw.type_index_map = {"TYPE_NONE": 0, "TYPE_NORMAL": 1, "TYPE_FIRE": 2}

        species_types = {
            "A": ["TYPE_FIRE", "TYPE_NONE"],
            "B": ["TYPE_FIRE", "TYPE_NORMAL"],
        }
        species_eggs = {
            "A": ["EGG_GROUP_FIELD"],
            "B": ["EGG_GROUP_FIELD", "EGG_GROUP_FIELD"],
        }
        species_abilities = {
            "A": ["ABILITY_TEST"],
            "B": ["ABILITY_TEST", "ABILITY_NONE"],
        }

        def get_species_info(sp, key, form=None):
            if key == "types":
                return species_types[sp]
            if key == "eggGroups":
                return species_eggs[sp]
            return 0

        def get_species_ability(sp, idx, form=None):
            abil = species_abilities[sp]
            if idx < len(abil):
                return abil[idx]
            raise IndexError

        def get_constant_data(cat, const):
            mapping = {
                ("types", "TYPE_FIRE"): {"value": 2},
                ("types", "TYPE_NORMAL"): {"value": 1},
                ("types", "TYPE_NONE"): {"value": 0},
                ("egg_groups", "EGG_GROUP_FIELD"): {"value": 4},
                ("egg_groups", "EGG_GROUP_NONE"): {"value": 3},
            }
            return mapping.get((cat, const), {"value": 0})

        def get_ability_data(const, key):
            mapping = {"ABILITY_TEST": 1, "ABILITY_NONE": 0}
            return mapping[const]

        self.mw.source_data = types.SimpleNamespace(
            get_species_info=get_species_info,
            get_species_data=lambda *a, **k: 0,
            get_constant_data=get_constant_data,
            get_item_data=lambda *a, **k: 0,
            get_species_image_path=lambda *a, **k: None,
            get_species_ability=get_species_ability,
            get_ability_data=get_ability_data,
            get_evolutions=lambda *_: [],
        )

    def test_fields_update_with_defaults(self):
        self.mw.update_data("A")
        self.assertEqual(self.mw.ui.type1.index, 2)
        self.assertEqual(self.mw.ui.type2.index, 0)
        self.assertEqual(self.mw.ui.egg_group_1.index, 4)
        self.assertEqual(self.mw.ui.egg_group_2.index, 3)
        self.assertEqual(self.mw.ui.ability1.index, 1)
        self.assertEqual(self.mw.ui.ability2.index, 0)
        self.assertEqual(self.mw.ui.ability_hidden.index, 0)

        self.mw.update_data("B")
        self.assertEqual(self.mw.ui.type1.index, 2)
        self.assertEqual(self.mw.ui.type2.index, 1)
        self.assertEqual(self.mw.ui.egg_group_1.index, 4)
        self.assertEqual(self.mw.ui.egg_group_2.index, 4)
        self.assertEqual(self.mw.ui.ability1.index, 1)
        self.assertEqual(self.mw.ui.ability2.index, 0)
        self.assertEqual(self.mw.ui.ability_hidden.index, 0)

if __name__ == "__main__":
    unittest.main()
