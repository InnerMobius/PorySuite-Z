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
    def __init__(self):
        self.style = None
    def setText(self, *_):
        pass
    def setPlainText(self, *_):
        pass
    def setValue(self, *_):
        pass
    def setStyleSheet(self, style):
        self.style = style
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

class SpriteClearTest(unittest.TestCase):
    def setUp(self):
        self.mw = mainwindow.MainWindow.__new__(mainwindow.MainWindow)
        self.mw.update_gender_ratio = lambda *_: None
        self.front0 = DummyWidget()
        self.front1 = DummyWidget()
        self.back = DummyWidget()
        self.icon = DummyWidget()
        self.foot = DummyWidget()
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
            frontPic_0=self.front0,
            frontPic_1=self.front1,
            backPic=self.back,
            iconPic=self.icon,
            footprintPic=self.foot,
            evolutions=DummyEvo(),
            tab_pokemon_data=DummyWidget(),
        )
        self.mw.type_index_map = {"TYPE_NORMAL": 1}

        def get_species_info(sp, key, form=None):
            if key in {"types", "eggGroups"}:
                return []
            return 0

        def get_evolutions(sp):
            return []

        self.images = {"A": "path"}

        def get_species_image_path(sp, key, form=None):
            return self.images.get(sp)

        self.mw.source_data = types.SimpleNamespace(
            get_species_info=get_species_info,
            get_evolutions=get_evolutions,
            get_species_data=lambda *a, **k: 0,
            get_constant_data=lambda *a, **k: {"value": 0, "name": ""},
            get_item_data=lambda *a, **k: 0,
            get_species_image_path=get_species_image_path,
            get_species_ability=lambda *a, **k: "ABILITY_NONE",
            get_ability_data=lambda *a, **k: 0,
        )

    def test_labels_cleared_when_no_image(self):
        self.mw.update_data("A")
        self.assertIn("url", self.front0.style)
        self.assertIn("url", self.front1.style)
        self.assertIn("url", self.back.style)
        self.assertIn("url", self.icon.style)
        self.assertIn("url", self.foot.style)

        self.images.pop("A")
        self.mw.update_data("B")
        self.assertEqual(self.front0.style, "")
        self.assertEqual(self.front1.style, "")
        self.assertEqual(self.back.style, "")
        self.assertEqual(self.icon.style, "")
        self.assertEqual(self.foot.style, "")

if __name__ == "__main__":
    unittest.main()
