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

class DummyLabel:
    def __init__(self):
        self.text = None
    def setText(self, val):
        self.text = val

class DummyTab:
    def __init__(self):
        self.enabled = True
    def setEnabled(self, val):
        self.enabled = val

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

class DummyEvo:
    def clear(self):
        pass
    def addTopLevelItem(self, *_):
        pass
    def columnCount(self):
        return 0
    def resizeColumnToContents(self, *_):
        pass

class TabAvailabilityTest(unittest.TestCase):
    def test_update_data_keeps_tab_enabled(self):
        mw = mainwindow.MainWindow.__new__(mainwindow.MainWindow)
        mw.ui = types.SimpleNamespace(
            species_name=DummyLabel(),
            dex_num=DummyWidget(),
            species_category=DummyWidget(),
            species_description=DummyWidget(),
            base_hp=DummyWidget(),
            base_atk=DummyWidget(),
            base_def=DummyWidget(),
            base_speed=DummyWidget(),
            base_spatk=DummyWidget(),
            base_spdef=DummyWidget(),
            type1=DummyWidget(),
            type2=DummyWidget(),
            ability1=DummyWidget(),
            ability2=DummyWidget(),
            ability_hidden=DummyWidget(),
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
            egg_group_1=DummyWidget(),
            egg_group_2=DummyWidget(),
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
        )
        mw.type_index_map = {"TYPE_NORMAL": 1}
        mw.update_gender_ratio = lambda *a, **k: None
        mw.source_data = types.SimpleNamespace(
            get_species_info=lambda *a, **k: None,
            get_evolutions=lambda *a, **k: [],
            get_species_data=lambda *a, **k: None,
            get_constant_data=lambda *a, **k: {"value": 0},
            get_ability_data=lambda *a, **k: 0,
            get_species_ability=lambda *a, **k: 0,
            get_item_data=lambda *a, **k: 0,
            get_species_image_path=lambda *a, **k: None,
        )
        # Should not disable the tab when data is missing
        mw.update_data("MISSING")
        self.assertTrue(mw.ui.tab_pokemon_data.enabled)

if __name__ == "__main__":
    unittest.main()
