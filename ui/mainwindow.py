import os
import re
import sys
import json
import copy
import datetime
import subprocess
import logging
try:
    from PyQt6.QtCore import pyqtSignal, Qt, QEvent, QSignalBlocker, QTimer, QSize, QEventLoop
except Exception:
    try:
        from PyQt6.QtCore import pyqtSignal, Qt, QEvent, QSignalBlocker, QEventLoop
    except Exception:
        class pyqtSignal:
            def __init__(*a, **k):
                pass

        class Qt:
            pass

        class QEvent:
            pass

        class QSignalBlocker:
            def __init__(*a, **k):
                pass

    QTimer = None
from PyQt6.QtGui import QFont, QIcon, QKeyEvent, QKeySequence, QPixmap
try:
    from PyQt6.QtWidgets import (
        QApplication,
        QMainWindow,
        QTreeWidgetItem,
        QLabel,
        QProgressBar,
        QListWidgetItem,
        QListWidget,
        QComboBox,
        QTableWidget,
        QTableWidgetItem,
        QMessageBox,
        QPushButton,
        QSpinBox,
        QLineEdit,
        QInputDialog,
        QTabWidget,
        QWidget,
        QVBoxLayout,
        QHBoxLayout,
        QHeaderView,
        QFormLayout,
        QSizePolicy,
        QScrollArea,
        QFrame,
    )
except Exception:
    from PyQt6.QtWidgets import (
        QApplication,
        QMainWindow,
        QTreeWidgetItem,
        QLabel,
        QProgressBar,
        QListWidgetItem,
        QTableWidgetItem,
        QMessageBox,
        QPushButton,
        QInputDialog,
    )

    class _DummySignal:
        def connect(self, *args, **kwargs):
            return None

    class QComboBox:
        class InsertPolicy:
            NoInsert = 0

        def __init__(self, *args, **kwargs):
            self._items = []
            self._text = ""
            self._editable = False
            self._props = {}
            self.currentTextChanged = _DummySignal()

        def setInsertPolicy(self, *args, **kwargs):
            return None

        def setEditable(self, flag):
            self._editable = flag

        def blockSignals(self, *args, **kwargs):
            return None

        def clear(self):
            self._items.clear()
            if not self._editable:
                self._text = ""

        def addItem(self, text):
            self._items.append(str(text))

        def addItems(self, iterable):
            for item in iterable:
                self.addItem(item)

        def findText(self, text, *args, **kwargs):
            try:
                return self._items.index(str(text))
            except ValueError:
                return -1

        def insertItem(self, index, text):
            self._items.insert(index, str(text))

        def setCurrentText(self, text):
            self._text = str(text)

        def currentText(self):
            return self._text

        def setCurrentIndex(self, index):
            try:
                self._text = self._items[index]
            except Exception:
                self._text = self._items[0] if self._items else ""

        def setProperty(self, key, value):
            self._props[key] = value

        def property(self, key):
            return self._props.get(key)

        def pos(self):
            return 0

    class QSpinBox:
        def __init__(self, *args, **kwargs):
            self._value = 0
            self._props = {}
            self.valueChanged = _DummySignal()

        def setRange(self, *args, **kwargs):
            return None

        def setProperty(self, key, value):
            self._props[key] = value

        def property(self, key):
            return self._props.get(key)

        def setValue(self, value):
            try:
                self._value = int(value)
            except Exception:
                self._value = 0

        def value(self):
            return self._value

        def blockSignals(self, *args, **kwargs):
            return None

        def pos(self):
            return 0

    class QLineEdit:
        def __init__(self, *args, **kwargs):
            self._text = ""
            self._props = {}
            self.textChanged = _DummySignal()

        def setProperty(self, key, value):
            self._props[key] = value

        def property(self, key):
            return self._props.get(key)

        def setText(self, text):
            self._text = str(text)

        def text(self):
            return self._text

        def blockSignals(self, *args, **kwargs):
            return None

        def pos(self):
            return 0

try:
    from PyQt6.QtWidgets import QDialog
except Exception:  # pragma: no cover - used in tests

    class QDialog:
        class DialogCode:
            Accepted = 1
            Rejected = 0


import urllib.request

import app_util
from local_env import LocalUtil
from app_info import APP_NAME, AUTHOR, get_data_dir
import core as _core
from suppress_dialog import maybe_exec
try:
    from newproject import NewProject
except Exception:  # pragma: no cover - optional dependency in tests
    class NewProject:
        def __init__(self, *args, **kwargs):
            pass

        def show(self):
            pass

try:
    from exportingwindow import Exporting
except Exception:  # pragma: no cover - optional dependency in tests
    class Exporting:
        def __init__(self, *args, **kwargs):
            pass

        def show(self):
            pass

try:
    from ui.delegates.pokedexitemdelegate import PokedexItemDelegate
except Exception:  # pragma: no cover - optional dependency in tests
    class PokedexItemDelegate:
        def __init__(self, *args, **kwargs):
            pass

        def setEditorData(self, *args, **kwargs):
            pass

        def setModelData(self, *args, **kwargs):
            pass

from ui.ui_mainwindow import Ui_MainWindow
from ui.items_tab_widget import ItemsTabWidget
from ui.trainers_tab_widget import TrainersTabWidget as TrainersTabWidgetUI, _replace_party_declaration
from ui.trainer_class_editor import (
    TrainerClassEditor, write_trainer_class_names, write_money_table,
    write_facility_pic_mapping,
)
from ui.trainer_graphics_tab import TrainerGraphicsTab
from ui.overworld_graphics_tab import OverworldGraphicsTab
from ui.pokedex_detail_panel import PokedexDetailPanel
from ui.dex_description_edit import attach_dex_limit_ui
from ui.moves_tab_widget import MovesTabWidget
from ui.abilities_tab_widget import AbilitiesTabWidget
from ui.config_tab_widget import ConfigTabWidget
from ui.ui_tab_widget import UITabWidget



class MainWindow(QMainWindow):
    # (duplicate removed) _open_crashlogs_folder is defined earlier in this class
    loadAndSaveProjectSignal = pyqtSignal(dict)
    logSignal = pyqtSignal(str)
    open_in_eventide_signal = pyqtSignal(dict)

    def __init__(self, parent=None):
        super().__init__(parent)

        # Initialize UI
        self.ui = Ui_MainWindow()
        self.ui.setupUi(self)
        # Install diagnostics logging filter
        try:
            from diagnostics import install_global_filter

            install_global_filter()
        except Exception:
            pass
        # Make the gender slider show percent (0-100) to users instead of engine 0-255
        try:
            self.ui.gender_ratio.setMaximum(100)
            self.ui.gender_ratio.setSingleStep(1)
            self.ui.gender_ratio.setPageStep(10)
        except Exception:
            pass
        self.ui.logOutput.setFont(QFont("Source Code Pro", 11))
        # Ensure Pokémon data tabs start enabled so new data can be entered
        self.ui.tab_pokemon_data.setEnabled(True)

        # Max characters per Pokédex line; updated from header after project load
        self.description_max_chars_per_line = 48

        # Reset buttons for discarding tab-specific edits still in memory
        self.reset_species_button = QPushButton("\u21bb Reset")
        self.reset_pokedex_button = QPushButton("\u21bb Reset to Vanilla")
        self.reset_starters_button = QPushButton("\u21bb Reset to Vanilla")

        # Insert the buttons into their respective tab layouts
        self.ui.tab_pokemon_grid.addWidget(self.reset_species_button, 1, 1, 1, 1)
        self.ui.tab_pokedex_grid.addWidget(self.reset_pokedex_button, 2, 1, 1, 1)
        self.ui.tab_starters_grid.addWidget(self.reset_starters_button, 1, 0, 1, 3)

        # Connect reset buttons
        self.reset_species_button.clicked.connect(self.reset_current_species_view)
        self.reset_pokedex_button.clicked.connect(
            lambda: self.reset_to_vanilla("pokedex")
        )
        self.reset_starters_button.clicked.connect(
            lambda: self.reset_to_vanilla("starters")
        )

        # ── Premium Items Editor ──────────────────────────────────────────────
        # Remove the old spreadsheet-style QTableWidget and replace with the
        # new left-list + right-detail panel widget.
        self.ui.tab_items_grid.removeWidget(self.ui.items_table)
        self.ui.items_table.hide()
        self.ui.items_table.deleteLater()

        self.items_editor = ItemsTabWidget()
        self.ui.tab_items_grid.addWidget(self.items_editor, 0, 0, 1, 1)
        self.items_editor.item_modified.connect(lambda: self.setWindowModified(True))
        self.items_editor.reset_requested.connect(lambda: self.reset_to_vanilla("items"))
        self.items_editor.rename_requested.connect(self._on_item_rename)

        # ── Trainers editor ──────────────────────────────────────────────────
        # Remove the old spreadsheet-style QTableWidget the same way items_table
        # was removed — otherwise its header row stays visible behind the new widget.
        self.ui.tab_trainers_grid.removeWidget(self.ui.trainers_table)
        self.ui.trainers_table.hide()
        self.ui.trainers_table.deleteLater()

        self.trainers_editor = TrainersTabWidgetUI()
        self.trainers_editor.changed.connect(lambda: self.setWindowModified(True))
        self.trainers_editor.rename_requested.connect(self._on_trainer_rename_from_panel)

        self.trainer_class_editor = TrainerClassEditor()
        self.trainer_class_editor.changed.connect(lambda: self.setWindowModified(True))
        # Live-push pending class-name renames into the sibling Trainers
        # editor so the trainer list and detail panel reflect them without
        # requiring save.
        self.trainer_class_editor.class_name_edited.connect(
            self.trainers_editor.apply_class_name
        )

        self.trainer_graphics_tab = TrainerGraphicsTab()
        self.trainer_graphics_tab.modified.connect(lambda: self.setWindowModified(True))

        # Tab switcher: Trainers / Trainer Classes / Graphics
        _TRAINER_TAB_SS = """
QTabWidget::pane {
    border: 1px solid #2e2e2e;
    background: #1a1a1a;
    border-radius: 0px;
}
QTabBar::tab {
    background: #222222;
    color: #777777;
    padding: 6px 18px;
    border: 1px solid #2e2e2e;
    border-bottom: none;
    margin-right: 2px;
    font-size: 11px;
}
QTabBar::tab:selected {
    background: #1a1a1a;
    color: #dddddd;
    border-bottom: 1px solid #1a1a1a;
}
QTabBar::tab:hover:!selected {
    background: #282828;
    color: #aaaaaa;
}
"""
        self._trainers_tab_switcher = QTabWidget()
        self._trainers_tab_switcher.setStyleSheet(_TRAINER_TAB_SS)
        self._trainers_tab_switcher.setDocumentMode(True)
        self._trainers_tab_switcher.addTab(self.trainers_editor, "Trainers")
        self._trainers_tab_switcher.addTab(self.trainer_class_editor, "Trainer Classes")
        self._trainers_tab_switcher.addTab(self.trainer_graphics_tab, "Graphics")
        self.ui.tab_trainers_grid.addWidget(self._trainers_tab_switcher, 0, 0, 1, 1)

        # Initialize instance variables
        self.project_info = None
        self.source_data = None
        self.plugin = None
        self.docker_util = None
        self.previous_main_tab = self.ui.mainTabs.currentIndex()
        self.previous_selected_species = None
        self.previous_selected_form = None
        # Map type constants to their indices once the combo boxes are populated
        self.type_index_map: dict[str, int] = {}
        # Guard against re-entrant selection handlers
        self._is_updating_selection = False

        # Initialize status bar widgets
        self.statusbar_progressbar = QProgressBar()
        self.statusbar_progressbar.setMaximum(100)
        self.statusbar_progressbar.setMaximumWidth(100)
        self.statusbar_project_label = QLabel("Unknown Project Type")
        self.ui.statusbar.addPermanentWidget(self.statusbar_progressbar, 0)
        self.ui.statusbar.addPermanentWidget(self.statusbar_project_label, 0)

        # Tab indices
        self.items_tab_index = self.ui.mainTabs.indexOf(self.ui.tab_items)
        self.trainers_tab_index = self.ui.mainTabs.indexOf(self.ui.tab_trainers)
        self.moves_tab_index = self.ui.tab_pokemon_data.indexOf(
            self.ui.tab_pokemon_moves
        )
        self.previous_pokemon_tab = self.ui.tab_pokemon_data.currentIndex()

        # Add a global Moves editor main tab
        try:
            self.ui.moves_widget = MovesTabWidget()
            self.ui.moves_widget.data_changed.connect(lambda: self.setWindowModified(True))
            self.ui.moves_widget.rename_requested.connect(self._on_move_rename)
            self.ui.mainTabs.addTab(self.ui.moves_widget, "Moves")
            self.moves_main_tab_index = self.ui.mainTabs.indexOf(self.ui.moves_widget)
        except Exception:
            self.moves_main_tab_index = -1

        # Add a global Abilities editor tab
        try:
            self.abilities_tab = AbilitiesTabWidget()
            self.abilities_tab.data_changed.connect(lambda: self.setWindowModified(True))
            self.abilities_tab.data_changed.connect(self._refresh_ability_combos)
            self.abilities_tab.rename_requested.connect(self._on_ability_rename)
            self.abilities_tab.species_jump_requested.connect(self._jump_to_species)
        except Exception as e:
            self.abilities_tab = None
            print(f"[AbilitiesEditor] Failed to create: {e}")

        # ── Overworld Graphics tab (top-level) ──────────────────────────────
        try:
            self.overworld_graphics_tab = OverworldGraphicsTab()
            self.overworld_graphics_tab.modified.connect(
                lambda: self.setWindowModified(True)
            )
            self.ui.mainTabs.addTab(self.overworld_graphics_tab, "Overworld GFX")
            self.overworld_gfx_tab_index = self.ui.mainTabs.indexOf(
                self.overworld_graphics_tab
            )
        except Exception:
            self.overworld_gfx_tab_index = -1

        # ── Config tab ───────────────────────────────────────────────────────
        self.config_tab = ConfigTabWidget(self.ui.tab_config)
        self.ui.tab_config_grid.addWidget(self.config_tab, 0, 0, 1, 1)
        self.config_tab.modified.connect(lambda: self.setWindowModified(True))

        # ── UI content tab ───────────────────────────────────────────────────
        self.ui_tab = UITabWidget(self.ui.tab_ui)
        self.ui.tab_ui_grid.addWidget(self.ui_tab, 0, 0, 1, 1)
        self.ui_tab.modified.connect(lambda: self.setWindowModified(True))

        # Build the 4-tab learnset editor inside the Pokémon "Moves" sub-tab.
        self.learnset_tab_index = self.moves_tab_index
        self.learnset_methods = ["LEVEL", "TM", "HM", "TUTOR", "EGG"]
        self.learnset_move_options = []
        self.learnset_tmhm_options = []
        self.learnset_tmhm_move_map: dict = {}   # "TM06" -> "MOVE_TOXIC"
        self.learnset_tutor_moves: list = []      # all known tutor move constants
        self._learnset_cache_valid = False        # invalidated on data load
        self._build_learnset_ui()

        # ── Species info tab enhancements ─────────────────────────────────────
        self._setup_species_info_enhancements()

        # ── Pokédex tab: add detail panel ─────────────────────────────────────
        self._setup_pokedex_tab()

        # ── Graphics tab: REPLACED by GraphicsTabWidget ───────────────────────
        self._current_species_gfx_folder: str = ""
        try:
            from ui.graphics_tab_widget import GraphicsTabWidget
            # Hide the legacy sprite buttons (keep alive — other code
            # still writes stylesheets to them harmlessly).
            _sink = QWidget(self.ui.tab_pokemon_graphics)
            _sink.hide()
            for _name in ("frontPic_0", "frontPic_1", "backPic", "iconPic",
                          "footprintPic", "label_33", "label_34", "label_51",
                          "label_52"):
                _w = getattr(self.ui, _name, None)
                if _w is not None:
                    _w.setParent(_sink)
            # Also stash the animated icon label if _setup_species_info
            # replaced iconPic earlier with it.
            _anim = getattr(self, "_icon_anim_lbl", None)
            if _anim is not None:
                _anim.setParent(_sink)
            # Remove the old formLayout_2 from the grid so it doesn't overlap
            try:
                self.ui.tab_pokemon_graphics_grid.removeItem(self.ui.formLayout_2)
            except Exception:
                pass
            # Build the new Graphics widget (its own res folder)
            _res_dir = os.path.join(
                os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                "res", "images",
            )
            self.graphics_tab_widget = GraphicsTabWidget(_res_dir)
            self.graphics_tab_widget.modified.connect(
                lambda: self.setWindowModified(True)
            )
            self.ui.tab_pokemon_graphics_grid.addWidget(
                self.graphics_tab_widget, 0, 0, 1, 1
            )
        except Exception as _e:
            logging.exception("Failed to build GraphicsTabWidget: %s", _e)
            self.graphics_tab_widget = None

        # Monitor tab changes
        self.ui.mainTabs.currentChanged.connect(self.on_main_tab_changed)
        self.ui.tab_pokemon_data.currentChanged.connect(self.on_pokemon_tab_changed)
        self.ui.trainers_table.itemChanged.connect(lambda *_: self.setWindowModified(True))
        self.ui.trainers_table.cellDoubleClicked.connect(self._on_trainer_cell_double_clicked)

        # Connect signals to slots
        self.loadAndSaveProjectSignal.connect(self.load_save_data)
        self.logSignal.connect(self.log)

        # Connect File menu actions
        self.ui.actionOpen_Plugins_Folder.triggered.connect(self._open_plugins_folder)
        self.ui.action_Open.triggered.connect(self._open_project_dialog)

        # Add "Open Project Folder" to File menu
        from PyQt6.QtGui import QAction as _QAction
        self._open_project_folder_action = _QAction("Open Project Folder", self)
        self._open_project_folder_action.triggered.connect(self._open_project_folder)
        self.ui.menuFile.addSeparator()
        self.ui.menuFile.addAction(self._open_project_folder_action)

        # Add "Open in EVENTide" to File menu
        self._open_eventide_action = _QAction("Open in EVENTide", self)
        self._open_eventide_action.setToolTip(
            "Open this project in EVENTide for map, layout,\n"
            "region map, and event editing."
        )
        self._open_eventide_action.setEnabled(False)
        self._open_eventide_action.triggered.connect(self._open_in_eventide)
        self.ui.menuFile.addAction(self._open_eventide_action)

        self.ui.menuFile.addSeparator()

        # Add "Refresh" to File menu — reloads all data and clears sprite caches
        self._refresh_action = _QAction("Refresh", self)
        self._refresh_action.setShortcut("F5")
        self._refresh_action.setToolTip(
            "Reload all data and graphics from disk.\n"
            "Use this after swapping sprite/icon files outside of PorySuite."
        )
        self._refresh_action.setEnabled(False)   # enabled once a project is loaded
        self._refresh_action.triggered.connect(self._refresh_project)
        self.ui.menuFile.addAction(self._refresh_action)

        # ── Git menu ─────────────────────────────────────────────────────────
        from PyQt6.QtWidgets import QMenu as _QMenu, QLabel as _QLabel
        self._git_menu = _QMenu("&Git", self)
        self.ui.menubar.insertMenu(
            self.ui.menuTools.menuAction(), self._git_menu
        )

        # ── Git Panel (main entry point) ──────────────────────────────────────
        self._git_panel_action = _QAction("Git Panel…", self)
        self._git_panel_action.setShortcut("Ctrl+Shift+G")
        self._git_panel_action.setToolTip(
            "Open the Git panel — pull, push, commit, branches, stash, history,\n"
            "and remote configuration, all in one window with full descriptions."
        )
        self._git_panel_action.setEnabled(False)
        self._git_panel_action.triggered.connect(self._open_git_panel)
        self._git_menu.addAction(self._git_panel_action)

        self._git_menu.addSeparator()

        # ── Quick-access keyboard shortcuts (no panel needed) ─────────────────
        self._pull_upstream_action = _QAction("Pull from Upstream", self)
        self._pull_upstream_action.setShortcut("Ctrl+Shift+L")
        self._pull_upstream_action.setEnabled(False)
        self._pull_upstream_action.triggered.connect(
            lambda: self._git_pull(use_upstream=True)
        )
        self._git_menu.addAction(self._pull_upstream_action)

        self._pull_origin_action = _QAction("Pull from origin", self)
        self._pull_origin_action.setEnabled(False)
        self._pull_origin_action.triggered.connect(self._git_pull)
        self._git_menu.addAction(self._pull_origin_action)

        self._push_action = _QAction("Push to origin", self)
        self._push_action.setShortcut("Ctrl+Shift+U")
        self._push_action.setEnabled(False)
        self._push_action.triggered.connect(self._git_push)
        self._git_menu.addAction(self._push_action)

        self._git_commit_action = _QAction("Commit…", self)
        self._git_commit_action.setShortcut("Ctrl+Shift+K")
        self._git_commit_action.setEnabled(False)
        self._git_commit_action.triggered.connect(self._git_commit)
        self._git_menu.addAction(self._git_commit_action)

        self._git_menu.addSeparator()

        self._git_configure_remotes_action = _QAction("Configure Remotes…", self)
        self._git_configure_remotes_action.setToolTip(
            "Set origin and upstream URLs, manage saved remotes.")
        self._git_configure_remotes_action.setEnabled(False)
        self._git_configure_remotes_action.triggered.connect(
            lambda: self._open_git_panel(page="remotes"))
        self._git_menu.addAction(self._git_configure_remotes_action)

        # Keep these stubs so _git_set_all_enabled doesn't break
        self._git_configure_action  = _QAction("", self)
        self._git_status_action     = _QAction("", self)
        self._git_new_branch_action = _QAction("", self)
        self._git_stash_action      = _QAction("", self)
        self._git_pop_stash_action  = _QAction("", self)
        self._git_log_action        = _QAction("", self)
        self._pull_menu             = _QMenu("", self)  # kept for _populate_pull_menu_branches

        # ── Git status bar (permanent right-side label) ───────────────────────
        self._git_bar_label = _QLabel("")
        self._git_bar_label.setObjectName("git_status_bar")
        self._git_bar_label.setStyleSheet("color: #888; margin-right: 8px;")
        self._git_bar_label.setCursor(
            __import__("PyQt6.QtCore", fromlist=["Qt"]).Qt.CursorShape.PointingHandCursor
        )
        self._git_bar_label.mousePressEvent = lambda _e: self._open_git_panel()
        self.statusBar().addPermanentWidget(self._git_bar_label)

        # Remove BPS export from Project menu (not relevant for decomp projects)
        try:
            self.ui.menuProject.removeAction(self.ui.actionExport_to_Patch_bps)
        except Exception:
            pass

        # Add Make / Make Modern to Project menu
        self.ui.menuProject.addSeparator()
        self._make_action = _QAction("Make (Build ROM)", self)
        self._make_action.setShortcut("Ctrl+M")
        self._make_action.triggered.connect(lambda: self._run_make([]))
        self._make_modern_action = _QAction("Make Modern", self)
        self._make_modern_action.setShortcut("Ctrl+Shift+M")
        self._make_modern_action.triggered.connect(lambda: self._run_make(["MODERN=1"]))
        self.ui.menuProject.addAction(self._make_action)
        self.ui.menuProject.addAction(self._make_modern_action)

        # Add Settings action to Tools menu
        try:
            from settingsdialog import SettingsDialog

            self._settings_action = _QAction("Settings...", self)
            self._settings_action.triggered.connect(lambda: SettingsDialog(self).exec())
            try:
                self.ui.menuTools.addSeparator()
                self.ui.menuTools.addAction(self._settings_action)
            except Exception:
                pass
        except Exception:
            pass

        # Install event filters
        self.ui.species_description.installEventFilter(self)
        self.ui.ability1.installEventFilter(self)
        self.ui.ability2.installEventFilter(self)
        self.ui.held_item_common.installEventFilter(self)
        self.ui.held_item_rare.installEventFilter(self)
        self.ui.evo_species.installEventFilter(self)
        self.ui.starter1_species.installEventFilter(self)
        self.ui.starter1_item.installEventFilter(self)
        self.ui.starter2_species.installEventFilter(self)
        self.ui.starter2_item.installEventFilter(self)
        self.ui.starter3_species.installEventFilter(self)
        self.ui.starter3_item.installEventFilter(self)

        # Connect selection change signals
        self.ui.tree_pokemon.itemSelectionChanged.connect(self.update_tree_pokemon)
        # React to changes in species flags (e.g. Genderless)
        try:
            self.ui.species_flags.itemChanged.connect(self.on_species_flag_changed)
        except Exception:
            pass
        self.ui.list_pokedex_national.itemSelectionChanged.connect(
            self.update_pokedex_entry
        )
        self.ui.list_pokedex_regional.itemSelectionChanged.connect(
            self.update_pokedex_entry
        )
        self.ui.tab_pokemon_data.currentChanged.connect(self.refresh_current_species)
        self.ui.evolutions.itemSelectionChanged.connect(self.update_evolutions)
        self.ui.pushButton_7.clicked.connect(self.add_evolution)
        self.ui.evoDeleteButton.clicked.connect(self.delete_evolution)
        self.ui.evo_species.currentTextChanged.connect(self.edit_evolution)
        self.ui.evo_method.currentIndexChanged.connect(self.refresh_evo_param_choices)
        self.ui.evo_method.currentTextChanged.connect(self.edit_evolution)
        self.ui.evo_param.currentTextChanged.connect(self.edit_evolution)

        # ── Evolution method reference panel ──────────────────────────────────
        self._setup_evo_reference_panel()

        # ── Scroll-wheel guard ────────────────────────────────────────────────
        # Prevent accidental value changes when hovering over combo boxes or
        # spin boxes without clicking them first.
        try:
            from ui.custom_widgets.scroll_guard import install_scroll_guard_recursive
            install_scroll_guard_recursive(self)
        except Exception:
            pass

    def _configure_description_limits(self):
        """Set per-line char limit and editor width based on pokedex_text_fr.h.

        Falls back to 48 if the header is unavailable. Width is computed using
        the current font's average character advance assuming monospaced font.
        """
        import os
        from collections import Counter
        limit = 42
        try:
            proj = getattr(self, "project_info", {}) or {}
            root = proj.get("dir")
            if root:
                path = os.path.join(root, "src", "data", "pokemon", "pokedex_text_fr.h")
                if os.path.isfile(path):
                    counts = Counter()
                    with open(path, encoding="utf-8") as f:
                        for raw in f:
                            s = raw.strip()
                            if not (s.startswith('"') and '"' in s[1:]):
                                continue
                            # extract contents between the first and last quote on the line
                            try:
                                start = s.index('"') + 1
                                end = s.rindex('"')
                                body = s[start:end]
                            except ValueError:
                                continue
                            # remove trailing \n token from count if present
                            if body.endswith(r"\n"):
                                body = body[:-2]
                            if body:
                                counts[len(body)] += 1
                    if counts:
                        # Use the largest line length that appears at least 20 times.
                        # "Most common" skews too short because short continuation lines
                        # outnumber long opening lines; using the max of common lengths
                        # matches the actual ceiling used in vanilla descriptions.
                        common = [l for l, c in counts.items() if c >= 20 and 20 <= l <= 64]
                        if common:
                            length = max(common)
                            limit = length
        except Exception:
            pass
        self.description_max_chars_per_line = limit
        try:
            fm = self.ui.species_description.fontMetrics()
        except Exception:
            from PyQt6.QtGui import QFontMetrics
            fm = QFontMetrics(self.ui.species_description.font())
        # Approximate width for monospaced characters + padding for margins/scrollbar
        char_w = fm.horizontalAdvance('M')
        pad = 24
        width = char_w * self.description_max_chars_per_line + pad
        try:
            self.ui.species_description.setMinimumWidth(width)
            self.ui.species_description.setMaximumWidth(width)
        except Exception:
            pass

        # Push the detected limit to both description editors
        try:
            if hasattr(self, "_species_desc_attachment"):
                self._species_desc_attachment.update_limits(limit, 3)
        except Exception:
            pass
        try:
            if hasattr(self, "_pokedex_panel"):
                self._pokedex_panel.set_description_limits(limit, 3)
        except Exception:
            pass
        try:
            if hasattr(self, "_pokedex_panel") and self.project_info.get("dir"):
                self._pokedex_panel.set_project_root(self.project_info["dir"])
        except Exception:
            pass
        try:
            if (getattr(self, "graphics_tab_widget", None) is not None
                    and self.project_info.get("dir")):
                self.graphics_tab_widget.set_project_root(self.project_info["dir"])
        except Exception:
            pass

    def _open_crashlogs_folder(self):
        """Open the project's crashlogs folder in the host OS file browser.

        The UI connects to this method; creating it avoids crashes when the action is used.
        """
        try:
            # Crashlogs live at the app root, not the project root
            try:
                import crashlog
                path = crashlog.logs_dir()
            except Exception:
                projdir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
                path = os.path.join(projdir, 'crashlogs')
            if not os.path.isdir(path):
                QMessageBox.information(self, 'Crashlogs', f'No crashlogs folder found: {path}')
                return
            if hasattr(os, 'startfile'):
                os.startfile(os.path.normpath(path))
            else:
                try:
                    import subprocess
                    subprocess.Popen(['xdg-open', path])
                except Exception:
                    QMessageBox.information(self, 'Crashlogs', f'Open folder: {path}')
        except Exception as e:
            QMessageBox.warning(self, 'Open Crashlogs', f'Failed to open crashlogs: {e}')

    def _open_plugins_folder(self):
        app_util.open_plugins_folder()

    def _open_project_dialog(self):
        """File > Open Project — browse for a project directory and load it."""
        from PyQt6.QtWidgets import QFileDialog
        dialog = QFileDialog(self)
        dialog.setFileMode(QFileDialog.FileMode.Directory)
        dialog.setOption(QFileDialog.Option.ShowDirsOnly)
        dialog.setWindowTitle("Open Project")
        if dialog.exec() != QFileDialog.DialogCode.Accepted:
            return
        import json, datetime
        project_dir = os.path.normpath(dialog.selectedFiles()[0])

        # Locate or create a project config
        project_json = os.path.join(project_dir, "project.json")
        config_json = os.path.join(project_dir, "config.json")
        p_info = None
        for path in (project_json, config_json):
            if os.path.exists(path):
                try:
                    with open(path, "r") as f:
                        p_info = json.load(f)
                    break
                except Exception:
                    pass
        if p_info is None:
            QMessageBox.warning(
                self, "Open Project",
                "No project.json or config.json found in that directory.\n"
                "Please select a valid PorySuite project folder."
            )
            return

        required = {"project_name", "name", "version", "plugin_identifier", "plugin_version"}
        if not required.issubset(p_info.keys()):
            QMessageBox.warning(
                self, "Open Project",
                "The project config is missing required fields.\n"
                "Is this a valid PorySuite project?"
            )
            return

        # Record in projects.json so it appears in the recent list
        from app_info import get_data_dir
        data_dir = get_data_dir()
        os.makedirs(data_dir, exist_ok=True)
        projects_file = os.path.join(data_dir, "projects.json")
        try:
            with open(projects_file, "r") as f:
                projects = json.load(f)
        except Exception:
            projects = {"projects": []}
        new_entry = {
            "name": p_info.get("name", os.path.basename(project_dir)),
            "project_name": p_info.get("project_name", os.path.basename(project_dir)),
            "dir": project_dir,
            "last_opened": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        }
        projects["projects"] = [p for p in projects.get("projects", []) if p.get("dir") != project_dir]
        projects["projects"].insert(0, new_entry)
        try:
            with open(projects_file, "w") as f:
                json.dump(projects, f)
        except Exception:
            pass

        # Emit the load signal — this handles unsaved-changes prompt and loads data
        self.loadAndSaveProjectSignal.emit(new_entry | p_info)

    def _refresh_project(self):
        """
        Full project refresh — equivalent to Tools → Rebuild Caches plus the
        two things that action was missing:
          • Sprite/icon cache clearing  (so swapped PNGs are picked up)
          • Force-reload of Trainers and Moves tabs  (lazy tabs that only
            reloaded on tab-switch before)

        Re-parses C headers → rebuilds JSON caches → reloads all editors.
        No round-trip to the launch screen needed.
        """
        if not self.source_data or not self.project_info:
            return

        # Clear in-memory sprite / icon caches BEFORE rebuild so the freshly
        # written PNGs are read rather than the stale QIcons.
        if hasattr(self, "_species_icon_cache"):
            self._species_icon_cache.clear()
        try:
            self.items_editor._icon_cache.clear()
        except Exception:
            pass

        # Delegate the heavy work (delete JSON caches, re-parse C headers,
        # load_data) to the existing rebuild_caches() method so there is a
        # single implementation of that logic.
        self.rebuild_caches()

        # rebuild_caches() calls load_data() but does not reload Trainers or
        # Moves (they are lazy-loaded on tab-switch).  Force them now.
        try:
            self._load_trainers_editor()
        except Exception:
            pass
        try:
            self.load_moves_defs_table()
        except Exception:
            pass
        try:
            self.load_abilities_editor()
        except Exception:
            pass

        self.statusBar().showMessage("Project refreshed from disk.", 4000)

    def _open_project_folder(self):
        """Open the current project directory in the host OS file browser."""
        if not self.project_info:
            QMessageBox.information(self, 'Open Project Folder', 'No project is open.')
            return
        try:
            project_dir = self.project_info.get('dir', '')
            if not project_dir or not os.path.isdir(project_dir):
                QMessageBox.warning(self, 'Open Project Folder', f'Project folder not found: {project_dir}')
                return
            app_util.reveal_directory(project_dir)
        except Exception as e:
            QMessageBox.warning(self, 'Open Project Folder', f'Failed to open project folder: {e}')

    def _open_in_eventide(self):
        """Emit signal to open the current project in EVENTide."""
        if self.project_info:
            self.open_in_eventide_signal.emit(self.project_info)

    def _open_terminal_in_project(self):
        """Open an MSYS2 MINGW64 terminal in the project directory (Windows) or a native terminal."""
        if not self.project_info:
            QMessageBox.information(self, 'Open Terminal', 'No project is open.')
            return
        project_dir = self.project_info.get('dir', '')
        if not project_dir:
            return
        try:
            if sys.platform == 'win32':
                # Prefer MSYS2 for GBA development; fall back to Windows Terminal then cmd
                from programsetup import _find_bash as _ps_find_bash, _devkitpro_env_exports
                bash_for_terminal = _ps_find_bash()
                if bash_for_terminal:
                    msys_path = project_dir.replace('\\', '/').replace('C:', '/c').replace('c:', '/c')
                    bash_cmd = (
                        _devkitpro_env_exports() +
                        f'cd "{msys_path}"; exec bash'
                    )
                    env = os.environ.copy()
                    env['MSYSTEM'] = 'MINGW64'
                    env['CHERE_INVOKING'] = '1'
                    subprocess.Popen(
                        [bash_for_terminal, '--login', '-c', bash_cmd],
                        env=env,
                        creationflags=subprocess.CREATE_NEW_CONSOLE,
                    )
                else:
                    # Fall back to cmd
                    subprocess.Popen(
                        ['cmd', '/K', f'cd /d "{project_dir}"'],
                        creationflags=subprocess.CREATE_NEW_CONSOLE,
                    )
            elif sys.platform == 'darwin':
                subprocess.Popen(['open', '-a', 'Terminal', project_dir])
            else:
                subprocess.Popen(['xdg-open', project_dir])
        except Exception as e:
            QMessageBox.warning(self, 'Open Terminal', f'Failed to open terminal: {e}')

    # ── Git helpers ───────────────────────────────────────────────────────────

    def _git_exe(self) -> str:
        """Return the path to the git executable, preferring Git for Windows."""
        for c in (
            r"C:\Program Files\Git\bin\git.exe",
            r"C:\Program Files (x86)\Git\bin\git.exe",
        ):
            if os.path.isfile(c):
                return c
        return "git"   # rely on PATH

    def _git_run(self, *args, timeout: int = 120) -> tuple[bool, str]:
        """
        Run a git command in the project directory synchronously.
        Returns (success, output/error message).
        """
        cwd = (self.project_info or {}).get("dir", "")
        _no_win = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        try:
            r = subprocess.run(
                [self._git_exe(), "-C", cwd, *args],
                capture_output=True, text=True, timeout=timeout,
                creationflags=_no_win,
            )
            out = (r.stdout + r.stderr).strip()
            return r.returncode == 0, out
        except FileNotFoundError:
            return False, "git not found — install Git for Windows (https://git-scm.com)."
        except subprocess.TimeoutExpired:
            return False, f"git timed out after {timeout}s."
        except Exception as exc:
            return False, str(exc)

    def _open_git_panel(self, page: str = "") -> None:
        """Open (or bring to front) the Git Panel window.

        page: optional section name to scroll/switch to (e.g. "remotes").
        """
        from git_panel import GitPanel
        panel = getattr(self, "_git_panel_instance", None)
        if panel is None or not panel.isVisible():
            panel = GitPanel(self)
            self._git_panel_instance = panel
        panel.show()
        panel.raise_()
        panel.activateWindow()
        if page and hasattr(panel, 'switch_to_page'):
            panel.switch_to_page(page)

    def _git_set_all_enabled(self, enabled: bool) -> None:
        """Enable or disable all git menu actions at once."""
        for name in (
            "_git_panel_action", "_git_configure_remotes_action",
            "_git_configure_action", "_git_status_action",
            "_pull_upstream_action", "_pull_origin_action",
            "_push_action", "_git_commit_action", "_git_new_branch_action",
            "_git_stash_action", "_git_pop_stash_action", "_git_log_action",
        ):
            act = getattr(self, name, None)
            if act:
                act.setEnabled(enabled)
        pm = getattr(self, "_pull_menu", None)
        if pm:
            pm.setEnabled(enabled)

    # ── Saved-remotes persistence ─────────────────────────────────────────────

    def _remotes_file(self) -> str:
        """Path to the JSON file that stores saved remote lists per project."""
        from app_info import get_data_dir
        return os.path.join(get_data_dir(), "git_remotes.json")

    def _load_saved_remotes(self) -> list[dict]:
        """
        Return the saved-remotes list for the current project.
        Each entry is {"name": str, "url": str}.
        """
        cwd = (self.project_info or {}).get("dir", "")
        try:
            with open(self._remotes_file(), "r", encoding="utf-8") as f:
                data = json.load(f)
            return data.get(cwd, [])
        except Exception:
            return []

    def _save_saved_remotes(self, remotes: list[dict]) -> None:
        """Persist the saved-remotes list for the current project."""
        cwd = (self.project_info or {}).get("dir", "")
        path = self._remotes_file()
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
        except Exception:
            data = {}
        data[cwd] = remotes
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)

    def _git_upstream_url(self) -> str:
        """Return the upstream URL stored for this project.
        Defaults to pret/pokefirered if not set."""
        cwd = (self.project_info or {}).get("dir", "")
        try:
            with open(self._remotes_file(), "r", encoding="utf-8") as f:
                data = json.load(f)
            return data.get("__upstream__", {}).get(cwd,
                "https://github.com/pret/pokefirered.git")
        except Exception:
            return "https://github.com/pret/pokefirered.git"

    def _git_save_upstream_url(self, url: str) -> None:
        """Persist the upstream URL for this project."""
        cwd = (self.project_info or {}).get("dir", "")
        path = self._remotes_file()
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
        except Exception:
            data = {}
        if "__upstream__" not in data:
            data["__upstream__"] = {}
        data["__upstream__"][cwd] = url
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)

    def _git_refresh_status_bar(self) -> None:
        """Update the permanent git status label in the status bar."""
        lbl = getattr(self, "_git_bar_label", None)
        if lbl is None or not self.project_info:
            return
        _, branch = self._git_run("rev-parse", "--abbrev-ref", "HEAD", timeout=5)
        branch = (branch or "").strip()
        if not branch or branch == "HEAD":
            lbl.setText("")
            return

        # Dirty check (staged or unstaged changes)
        _, dirty_out = self._git_run("status", "--porcelain", timeout=5)
        dirty_lines = [l for l in (dirty_out or "").splitlines() if l.strip()]
        dirty_part = f"  ✎ {len(dirty_lines)}" if dirty_lines else ""

        # Ahead / behind origin
        _, ab = self._git_run(
            "rev-list", "--left-right", "--count", f"origin/{branch}...HEAD",
            timeout=5,
        )
        ab_part = ""
        if ab:
            parts = ab.strip().split()
            if len(parts) == 2:
                try:
                    behind, ahead = int(parts[0]), int(parts[1])
                    if ahead:
                        ab_part += f"  ↑{ahead}"
                    if behind:
                        ab_part += f"  ↓{behind}"
                except ValueError:
                    pass

        lbl.setText(f"⎇ {branch}{dirty_part}{ab_part}")
        lbl.setToolTip(
            f"Branch: {branch}"
            + (f"\n{len(dirty_lines)} uncommitted file(s)" if dirty_lines else "")
            + (f"\n{ahead} commit(s) ahead of origin" if ab_part and ahead else "")
            + (f"\n{behind} commit(s) behind origin" if ab_part and behind else "")
        )

    # ── Configure Remote dialog ───────────────────────────────────────────────

    def _git_configure_remote(self) -> None:
        """
        Git → Configure Remotes

        Two sections:
        - Origin: the remote you push/pull your fork to (git remote origin)
        - Upstream: the base repo you periodically pull clean updates from
          (defaults to pret/pokefirered; NOT stored as a git remote — just a URL)
        Plus a saved-remotes list for quick origin switching.
        """
        if not self.project_info:
            return
        cwd = self.project_info.get("dir", "")

        ok_url, current_origin = self._git_run("remote", "get-url", "origin", timeout=10)
        current_origin = current_origin.strip() if ok_url else ""
        _, branch = self._git_run("rev-parse", "--abbrev-ref", "HEAD", timeout=10)
        current_upstream = self._git_upstream_url()
        saved = self._load_saved_remotes()

        if current_origin and not any(r["url"] == current_origin for r in saved):
            saved.insert(0, {"name": "origin", "url": current_origin})
            self._save_saved_remotes(saved)

        from PyQt6.QtWidgets import (
            QDialog, QVBoxLayout, QHBoxLayout, QFormLayout, QGroupBox,
            QLabel, QLineEdit, QPushButton, QDialogButtonBox,
            QListWidget, QListWidgetItem,
        )

        dlg = QDialog(self)
        dlg.setWindowTitle("Configure Git Remotes")
        dlg.setMinimumWidth(620)
        outer = QVBoxLayout(dlg)
        outer.setSpacing(10)

        # ── Header ────────────────────────────────────────────────────────────
        outer.addWidget(QLabel(f"<b>Project:</b> {cwd}"))
        if branch:
            outer.addWidget(QLabel(f"<b>Current branch:</b> {branch.strip()}"))
        outer.addSpacing(4)

        # ── Origin group ──────────────────────────────────────────────────────
        grp_origin = QGroupBox("Origin  (your fork — push/pull here)")
        form_origin = QFormLayout(grp_origin)
        origin_edit = QLineEdit(current_origin)
        origin_edit.setPlaceholderText("https://github.com/yourname/pokefirered.git")
        form_origin.addRow("URL:", origin_edit)

        origin_btns = QHBoxLayout()
        btn_set_origin = QPushButton("Apply Origin")
        btn_set_origin.setToolTip("Run  git remote set-url origin <URL>")
        origin_btns.addWidget(btn_set_origin)
        origin_btns.addStretch()
        form_origin.addRow("", origin_btns)
        outer.addWidget(grp_origin)

        # ── Upstream group ────────────────────────────────────────────────────
        grp_up = QGroupBox("Upstream  (base repo — pull clean updates from here)")
        form_up = QFormLayout(grp_up)
        upstream_edit = QLineEdit(current_upstream)
        upstream_edit.setPlaceholderText("https://github.com/pret/pokefirered.git")
        form_up.addRow("URL:", upstream_edit)

        up_note = QLabel(
            "This URL is used by  Git → Pull → Pull from Upstream.\n"
            "It is NOT registered as a git remote — only used for one-time fetches."
        )
        up_note.setWordWrap(True)
        up_note.setStyleSheet("color: #888; font-size: 11px;")
        form_up.addRow("", up_note)

        up_btns = QHBoxLayout()
        btn_set_upstream = QPushButton("Save Upstream URL")
        up_btns.addWidget(btn_set_upstream)
        up_btns.addStretch()
        form_up.addRow("", up_btns)
        outer.addWidget(grp_up)

        # ── Saved remotes (quick-switch origin list) ──────────────────────────
        grp_saved = QGroupBox("Saved Remotes  (quick-switch origin)")
        saved_layout = QVBoxLayout(grp_saved)

        list_widget = QListWidget()
        list_widget.setAlternatingRowColors(True)
        list_widget.setMaximumHeight(140)

        _nc = {"origin": current_origin}  # mutable nonlocal

        def _rebuild_list():
            list_widget.clear()
            for r in saved:
                marker = "  ✓  [active origin]" if r["url"] == _nc["origin"] else ""
                item = QListWidgetItem(f"{r['name']}{marker}  —  {r['url']}")
                item.setData(256, r)
                list_widget.addItem(item)

        _rebuild_list()
        saved_layout.addWidget(list_widget)

        list_btns = QHBoxLayout()
        btn_activate = QPushButton("Set as Active Origin")
        btn_remove   = QPushButton("Remove")
        list_btns.addWidget(btn_activate)
        list_btns.addWidget(btn_remove)
        list_btns.addStretch()

        # Add-to-list form
        add_form = QFormLayout()
        name_edit2 = QLineEdit()
        name_edit2.setPlaceholderText("e.g. my fork, team repo, …")
        url_edit2  = QLineEdit()
        url_edit2.setPlaceholderText("https://github.com/user/pokefirered.git")
        add_form.addRow("Name:", name_edit2)
        add_form.addRow("URL:", url_edit2)
        btn_add = QPushButton("Add to List")

        add_row = QHBoxLayout()
        add_row.addWidget(btn_add)
        add_row.addStretch()

        saved_layout.addLayout(list_btns)
        saved_layout.addSpacing(6)
        saved_layout.addLayout(add_form)
        saved_layout.addLayout(add_row)
        outer.addWidget(grp_saved)

        # ── Status label + close ──────────────────────────────────────────────
        status_lbl = QLabel("")
        outer.addWidget(status_lbl)

        close_box = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        close_box.rejected.connect(dlg.reject)
        outer.addWidget(close_box)

        # ── Wire up actions ───────────────────────────────────────────────────

        def _on_set_origin():
            new_url = origin_edit.text().strip()
            if not new_url:
                status_lbl.setText("⚠  URL cannot be empty.")
                return
            if _nc["origin"]:
                ok2, msg2 = self._git_run("remote", "set-url", "origin", new_url, timeout=10)
            else:
                ok2, msg2 = self._git_run("remote", "add", "origin", new_url, timeout=10)
            if ok2:
                _nc["origin"] = new_url
                if not any(r["url"] == new_url for r in saved):
                    saved.insert(0, {"name": "origin", "url": new_url})
                    self._save_saved_remotes(saved)
                _rebuild_list()
                status_lbl.setText(f"✓  Origin set to: {new_url}")
                self._git_refresh_status_bar()
            else:
                status_lbl.setText(f"✗  git error: {msg2}")

        def _on_set_upstream():
            new_url = upstream_edit.text().strip()
            if not new_url:
                status_lbl.setText("⚠  URL cannot be empty.")
                return
            self._git_save_upstream_url(new_url)
            # Update the Pull menu label
            act = getattr(self, "_pull_upstream_action", None)
            if act:
                host = new_url.replace("https://github.com/", "").replace(".git", "")
                act.setText(f"⬇  Pull from Upstream  ({host})")
            status_lbl.setText(f"✓  Upstream saved: {new_url}")

        def _on_activate():
            item = list_widget.currentItem()
            if not item:
                return
            r = item.data(256)
            if r["url"] == _nc["origin"]:
                status_lbl.setText("Already the active origin.")
                return
            if _nc["origin"]:
                ok2, msg2 = self._git_run("remote", "set-url", "origin", r["url"], timeout=10)
            else:
                ok2, msg2 = self._git_run("remote", "add", "origin", r["url"], timeout=10)
            if ok2:
                _nc["origin"] = r["url"]
                origin_edit.setText(r["url"])
                _rebuild_list()
                status_lbl.setText(f"✓  Active origin: {r['url']}")
                self._git_refresh_status_bar()
            else:
                status_lbl.setText(f"✗  git error: {msg2}")

        def _on_remove():
            item = list_widget.currentItem()
            if not item:
                return
            r = item.data(256)
            saved[:] = [x for x in saved if x["url"] != r["url"]]
            self._save_saved_remotes(saved)
            _rebuild_list()

        def _on_add():
            n = name_edit2.text().strip()
            u = url_edit2.text().strip()
            if not n or not u:
                status_lbl.setText("⚠  Both name and URL are required.")
                return
            for r in saved:
                if r["url"] == u:
                    r["name"] = n
                    break
            else:
                saved.append({"name": n, "url": u})
            self._save_saved_remotes(saved)
            _rebuild_list()
            name_edit2.clear()
            url_edit2.clear()

        def _on_select():
            item = list_widget.currentItem()
            if item:
                r = item.data(256)
                url_edit2.setText(r["url"])
                name_edit2.setText(r["name"])

        btn_set_origin.clicked.connect(_on_set_origin)
        btn_set_upstream.clicked.connect(_on_set_upstream)
        btn_activate.clicked.connect(_on_activate)
        btn_remove.clicked.connect(_on_remove)
        btn_add.clicked.connect(_on_add)
        list_widget.currentItemChanged.connect(lambda *_: _on_select())

        dlg.exec()

    def _git_pull(self, override_url: str = "", use_upstream: bool = False,
                  on_refresh_done: "callable | None" = None) -> None:
        """
        Git → Pull submenu actions.

        override_url: if given (e.g. pret upstream URL), fetch directly from
        that URL and reset to FETCH_HEAD instead of using origin.
        use_upstream: if True, load the upstream URL from storage.
        """
        if not self.project_info:
            return
        project_dir = self.project_info.get("dir", "")
        if not project_dir or not os.path.isdir(project_dir):
            QMessageBox.warning(self, "Pull", "Project directory not found.")
            return

        # Resolve where we're actually pulling from
        if use_upstream and not override_url:
            override_url = self._git_upstream_url()
        is_upstream_pull = bool(override_url)
        if override_url:
            remote_label = override_url
            fetch_args  = ["fetch", override_url]
            reset_args  = ["reset", "--hard", "FETCH_HEAD"]
            fetch_label = f"git fetch {override_url}"
            reset_label = "git reset --hard FETCH_HEAD"
        else:
            _, remote_url = self._git_run("remote", "get-url", "origin", timeout=10)
            remote_label = (remote_url or "origin").strip()
            fetch_args  = ["fetch", "origin"]
            reset_args  = ["reset", "--hard", "origin/HEAD"]
            fetch_label = "git fetch origin"
            reset_label = "git reset --hard origin/HEAD"

        _, branch = self._git_run("rev-parse", "--abbrev-ref", "HEAD", timeout=10)
        branch_label = (branch or "").strip()

        # For upstream pulls, preview what untracked files would be wiped
        clean_preview = ""
        if is_upstream_pull:
            _, dry = self._git_run(
                "clean", "-fd", "--dry-run",
                "--exclude=project.json",
                "--exclude=src/data/*.json",
                "--exclude=temp",
                timeout=10,
            )
            if dry and dry.strip():
                lines = dry.strip().splitlines()
                preview_lines = lines[:12]
                if len(lines) > 12:
                    preview_lines.append(f"  … and {len(lines) - 12} more")
                clean_preview = "\n\nUntracked files that will be deleted:\n" + "\n".join(
                    f"  {l}" for l in preview_lines
                )

        confirm_text = (
            f"⚠ WARNING: Pulling will overwrite your local files.\n\n"
            f"Your local copy will be replaced with whatever is on the "
            f"remote. Any work you haven't committed will be permanently lost.\n\n"
            f"  Remote: {remote_label}\n"
            + (f"  Branch: {branch_label}\n" if branch_label else "") +
            f"\nWhat will be wiped:\n"
            f"  • All uncommitted changes to tracked files\n"
            f"  • All unsaved edits open in the editor\n"
            f"  • All queued rename operations not yet written to disk\n"
            + ("  • ALL untracked files (custom graphics, scripts, etc.)\n" if is_upstream_pull else "") +
            clean_preview +
            f"\nAre you sure you want to continue?"
        )

        ans = QMessageBox.question(
            self,
            "Pull from Remote",
            confirm_text,
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Cancel,
        )
        if ans != QMessageBox.StandardButton.Yes:
            return

        # ── Progress dialog ───────────────────────────────────────────────────
        from PyQt6.QtWidgets import QDialog, QVBoxLayout, QHBoxLayout, QLabel, QPlainTextEdit, QPushButton
        from PyQt6.QtCore import QThread, pyqtSignal as _sig
        from PyQt6.QtGui import QFont

        prog_dlg = QDialog(self)
        prog_dlg.setWindowTitle("Git Pull")
        prog_dlg.setMinimumWidth(620)
        prog_dlg.setMinimumHeight(340)
        prog_dlg.setWindowFlag(Qt.WindowType.WindowCloseButtonHint, False)
        _vlayout = QVBoxLayout(prog_dlg)
        _vlayout.setSpacing(8)

        _from_lbl = QLabel(f"<b>Pulling from:</b> {remote_label}"
                           + (f"&nbsp;&nbsp;<b>Branch:</b> {branch_label}" if branch_label else ""))
        _from_lbl.setWordWrap(True)
        _vlayout.addWidget(_from_lbl)

        _out = QPlainTextEdit()
        _out.setReadOnly(True)
        _out.setFont(QFont("Courier New", 9))
        _out.setPlaceholderText("Waiting for git output…")
        _vlayout.addWidget(_out)

        _status_lbl = QLabel("Running…")
        _vlayout.addWidget(_status_lbl)

        _btn_row = QHBoxLayout()
        _btn_row.addStretch()
        _close_btn = QPushButton("Close")
        _close_btn.setEnabled(False)
        _close_btn.clicked.connect(prog_dlg.accept)
        _btn_row.addWidget(_close_btn)
        _vlayout.addLayout(_btn_row)

        prog_dlg.show()

        self._refresh_action.setEnabled(False)
        self._git_set_all_enabled(False)

        _steps = [
            (fetch_args, 180, fetch_label),
            (reset_args,  60, reset_label),
        ]
        if is_upstream_pull:
            # Remove every untracked file/directory so the working tree is a
            # byte-for-byte match of what's on the remote.  -f = force,
            # -d = also remove untracked directories.
            # Does NOT use -x so .gitignored files (build/, toolchain, etc.)
            # are preserved — you don't want to recompile agbcc from scratch.
            #
            # Exclusions protect PorySuite's own data files that live in the
            # project folder but are NOT part of vanilla pokefirered:
            #   - project.json     : PorySuite project identity
            #   - src/data/*.json  : PorySuite data (trainers, species, moves…)
            #   - temp/            : PorySuite staging area for pending renames
            _clean_args = [
                "clean", "-fd",
                "--exclude=project.json",
                "--exclude=src/data/*.json",
                "--exclude=temp",
            ]
            _clean_label = "git clean -fd  (excluding PorySuite data files)"
            _steps.append((_clean_args, 60, _clean_label))

        class _PullWorker(QThread):
            line_out = _sig(str)
            done = _sig(bool, str)

            def __init__(self, git, cwd, steps):
                super().__init__()
                self._git   = git
                self._cwd   = cwd
                self._steps = steps

            def _run_streaming(self, args, timeout_s):
                proc = subprocess.Popen(
                    [self._git, "-C", self._cwd, *args],
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    text=True,
                    creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                )
                import threading, queue as _queue
                q = _queue.Queue()

                def _reader():
                    for ln in proc.stdout:
                        q.put(ln)
                    q.put(None)

                threading.Thread(target=_reader, daemon=True).start()
                deadline = __import__("time").monotonic() + timeout_s
                while True:
                    remaining = deadline - __import__("time").monotonic()
                    if remaining <= 0:
                        proc.kill()
                        return None, "timed out"
                    try:
                        item = q.get(timeout=min(0.1, remaining))
                    except _queue.Empty:
                        continue
                    if item is None:
                        break
                    self.line_out.emit(item.rstrip("\n"))
                proc.wait()
                return proc.returncode, None

            def run(self):
                for args, tmo, label in self._steps:
                    self.line_out.emit(f"\n$ {label}")
                    try:
                        rc, err = self._run_streaming(args, tmo)
                    except FileNotFoundError:
                        self.done.emit(False, "git not found — install Git for Windows.")
                        return
                    except Exception as exc:
                        self.done.emit(False, str(exc))
                        return
                    if err:
                        self.done.emit(False, f"{label}: {err}")
                        return
                    if rc != 0:
                        self.done.emit(False, f"{label} exited with code {rc}")
                        return
                self.done.emit(True, "Done.")

        worker = _PullWorker(self._git_exe(), project_dir, _steps)
        self._git_worker = worker

        def _append_line(ln: str):
            _out.appendPlainText(ln)

        def _on_done(ok: bool, msg: str):
            self._refresh_action.setEnabled(True)
            self._git_set_all_enabled(True)
            _status_lbl.setText(("✓ " if ok else "✗ ") + msg)
            _close_btn.setEnabled(True)
            prog_dlg.setWindowFlag(Qt.WindowType.WindowCloseButtonHint, True)
            prog_dlg.show()

            if ok:
                # Clear ALL in-memory state referencing the old repo so
                # apply_pending on next Save doesn't re-write stale renames.
                try:
                    svc = getattr(
                        getattr(self, "source_data", None), "refactor_service", None
                    )
                    if svc and hasattr(svc, "pending"):
                        svc.pending.clear()
                except Exception:
                    pass
                self.setWindowModified(False)
                try:
                    for data_obj in (self.source_data.data or {}).values():
                        data_obj.pending_changes = False
                except Exception:
                    pass

                # Delete auto-generated .h files that are NOT tracked by git.
                # These are regenerated by `make` from the JSON source files.
                # If they're stale (e.g. contain renamed constants from before
                # the pull), make will compile the old content and fail.
                # Deleting them forces make to regenerate them fresh.
                _auto_gen = [
                    "src/data/wild_encounters.h",
                    "src/data/items.h",
                    "src/data/heal_locations.h",
                    "src/data/region_map/region_map_entries.h",
                    "src/data/region_map/region_map_entry_strings.h",
                    "include/constants/heal_locations.h",
                    "include/constants/region_map_sections.h",
                    "include/constants/map_groups.h",
                    "include/constants/layouts.h",
                    "include/constants/map_event_ids.h",
                ]
                deleted = []
                for rel in _auto_gen:
                    p = os.path.join(project_dir, rel)
                    try:
                        if os.path.isfile(p):
                            os.remove(p)
                            deleted.append(rel)
                    except Exception:
                        pass
                if deleted:
                    _append_line(f"\nDeleted {len(deleted)} stale generated file(s) — make will regenerate them:")
                    for d in deleted:
                        _append_line(f"  {d}")

                _append_line("\n✓ Pull complete. Click Done to refresh project data.")
                _close_btn.setText("Done")

                # Disconnect the old Close handler and wire Done to
                # refresh-then-close so the project reloads when the
                # user clicks the button (not automatically).
                _close_btn.clicked.disconnect()

                def _done_clicked():
                    self.statusBar().showMessage("Refreshing project…", 4000)
                    prog_dlg.accept()
                    # Defer refresh so the dialog finishes closing first.
                    def _do_refresh():
                        self._refresh_project()
                        self._git_refresh_status_bar()
                        self.statusBar().showMessage("Pull complete.", 4000)
                        if on_refresh_done:
                            on_refresh_done()
                    QTimer.singleShot(50, _do_refresh)

                _close_btn.clicked.connect(_done_clicked)
            else:
                self.statusBar().showMessage("Pull failed.", 4000)

        worker.line_out.connect(_append_line)
        worker.done.connect(_on_done)
        worker.start()

    # ── Pull submenu: branch list ─────────────────────────────────────────────

    def _populate_pull_menu_branches(self) -> None:
        """
        Called every time the Pull submenu opens.
        Removes any old branch items, then appends the current local branch list.
        """
        # Update the upstream action label with the currently configured URL
        upstream_act = getattr(self, "_pull_upstream_action", None)
        if upstream_act and self.project_info:
            url = self._git_upstream_url()
            host = url.replace("https://github.com/", "").replace("https://", "").replace(".git", "")
            upstream_act.setText(f"⬇  Pull from Upstream  ({host})")
            upstream_act.setToolTip(f"Fetch from: {url}\nThen reset --hard FETCH_HEAD")

        menu = self._pull_menu
        # Remove previously added branch items
        for act in list(menu.actions()):
            if getattr(act, "_branch_item", False):
                menu.removeAction(act)

        if not self.project_info:
            return
        project_dir = self.project_info.get("dir", "")

        _, current_raw = self._git_run("rev-parse", "--abbrev-ref", "HEAD", timeout=5)
        current = (current_raw or "").strip()

        _, branches_raw = self._git_run(
            "branch", "--format=%(refname:short)", timeout=5
        )
        branches = [b.strip() for b in (branches_raw or "").splitlines() if b.strip()]

        if not branches:
            return

        # Header label (disabled, just for visual grouping)
        header = _QAction("  Local Branches", self)
        header.setEnabled(False)
        header._branch_item = True
        menu.addAction(header)

        for b in branches:
            is_current = b == current
            label = f"  {'✓' if is_current else '   '}  {b}"
            act = _QAction(label, self)
            act._branch_item = True
            act.setEnabled(not is_current)
            act.setToolTip(
                f"Switch to branch '{b}'"
                + (" (current)" if is_current else "")
            )
            act.triggered.connect(lambda checked=False, br=b: self._git_checkout_branch(br))
            menu.addAction(act)

    def _git_checkout_branch(self, branch: str) -> None:
        """Switch to a local branch and refresh the project."""
        if not self.project_info:
            return
        project_dir = self.project_info.get("dir", "")
        if not project_dir or not os.path.isdir(project_dir):
            return

        from PyQt6.QtWidgets import QMessageBox as _MB
        ans = _MB.question(
            self, "Switch Branch",
            f"Switch to branch  '{branch}'?\n\n"
            f"Unsaved changes will be lost.",
            _MB.StandardButton.Yes | _MB.StandardButton.Cancel,
        )
        if ans != _MB.StandardButton.Yes:
            return

        ok, msg = self._git_run("checkout", branch, timeout=20)
        if not ok:
            _MB.warning(self, "Switch Branch", f"git checkout failed:\n{msg}")
            return

        self.statusBar().showMessage(f"Switched to branch '{branch}' — refreshing…", 4000)
        QTimer.singleShot(50, self._refresh_project)

    def _git_show_status(self) -> None:
        """Git → Status — show branch, changed files, ahead/behind in a dialog."""
        if not self.project_info:
            return
        from PyQt6.QtWidgets import QDialog, QVBoxLayout, QLabel, QPlainTextEdit, QPushButton, QHBoxLayout
        from PyQt6.QtGui import QFont

        _, branch = self._git_run("rev-parse", "--abbrev-ref", "HEAD", timeout=5)
        branch = (branch or "HEAD").strip()

        _, status_out = self._git_run("status", "--short", timeout=10)
        _, log_ahead = self._git_run("log", "--oneline", f"origin/{branch}..HEAD", timeout=10)
        _, log_behind = self._git_run("log", "--oneline", f"HEAD..origin/{branch}", timeout=10)
        _, stash_out = self._git_run("stash", "list", timeout=5)

        dlg = QDialog(self)
        dlg.setWindowTitle("Git Status")
        dlg.setMinimumWidth(560)
        vlay = QVBoxLayout(dlg)

        vlay.addWidget(QLabel(f"<b>Branch:</b> {branch}"))

        ahead_lines  = [l for l in (log_ahead or "").splitlines() if l.strip()]
        behind_lines = [l for l in (log_behind or "").splitlines() if l.strip()]
        stash_lines  = [l for l in (stash_out or "").splitlines() if l.strip()]

        if ahead_lines:
            vlay.addWidget(QLabel(f"<b>↑ {len(ahead_lines)} commit(s) ahead of origin</b>"))
        if behind_lines:
            vlay.addWidget(QLabel(f"<b>↓ {len(behind_lines)} commit(s) behind origin</b>"))
        if stash_lines:
            vlay.addWidget(QLabel(f"<b>📦 {len(stash_lines)} stash entry(s)</b>"))

        changed = [l for l in (status_out or "").splitlines() if l.strip()]
        if changed:
            vlay.addWidget(QLabel(f"<b>Changed files ({len(changed)}):</b>"))
            txt = QPlainTextEdit()
            txt.setReadOnly(True)
            txt.setFont(QFont("Courier New", 9))
            txt.setMaximumHeight(200)
            txt.setPlainText("\n".join(changed))
            vlay.addWidget(txt)
        else:
            vlay.addWidget(QLabel("<i>Working tree is clean.</i>"))

        btn_row = QHBoxLayout()
        btn_row.addStretch()
        close_btn = QPushButton("Close")
        close_btn.clicked.connect(dlg.accept)
        btn_row.addWidget(close_btn)
        vlay.addLayout(btn_row)

        dlg.exec()

    def _git_commit(self) -> None:
        """Git → Commit — stage files and write a commit message."""
        if not self.project_info:
            return
        project_dir = self.project_info.get("dir", "")

        _, status_out = self._git_run("status", "--short", timeout=10)
        lines = [l for l in (status_out or "").splitlines() if l.strip()]

        if not lines:
            from PyQt6.QtWidgets import QMessageBox as _MB
            _MB.information(self, "Commit", "Nothing to commit — working tree is clean.")
            return

        from PyQt6.QtWidgets import (
            QDialog, QVBoxLayout, QHBoxLayout, QLabel, QListWidget,
            QListWidgetItem, QPlainTextEdit, QPushButton, QDialogButtonBox,
        )
        from PyQt6.QtCore import Qt as _Qt

        dlg = QDialog(self)
        dlg.setWindowTitle("Commit Changes")
        dlg.setMinimumWidth(560)
        dlg.setMinimumHeight(420)
        vlay = QVBoxLayout(dlg)

        vlay.addWidget(QLabel("<b>Files to commit</b>  (check to stage):"))
        file_list = QListWidget()
        file_list.setAlternatingRowColors(True)
        file_list.setMaximumHeight(180)

        for raw in lines:
            xy   = raw[:2]
            path = raw[3:].strip()
            item = QListWidgetItem(f"{xy}  {path}")
            item.setData(256, path)
            item.setCheckState(_Qt.CheckState.Checked)
            file_list.addItem(item)

        vlay.addWidget(file_list)
        vlay.addWidget(QLabel("<b>Commit message:</b>"))

        msg_edit = QPlainTextEdit()
        msg_edit.setPlaceholderText("Describe your changes…")
        msg_edit.setMaximumHeight(100)
        vlay.addWidget(msg_edit)

        status_lbl = QLabel("")
        vlay.addWidget(status_lbl)

        btns = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        btns.button(QDialogButtonBox.StandardButton.Ok).setText("Commit")
        btns.rejected.connect(dlg.reject)
        vlay.addWidget(btns)

        def _do_commit():
            msg = msg_edit.toPlainText().strip()
            if not msg:
                status_lbl.setText("⚠  Please write a commit message.")
                return
            # Stage selected files
            staged_any = False
            for i in range(file_list.count()):
                item = file_list.item(i)
                if item.checkState() == _Qt.CheckState.Checked:
                    path = item.data(256)
                    ok, out = self._git_run("add", path, timeout=10)
                    staged_any = True
            if not staged_any:
                status_lbl.setText("⚠  No files selected.")
                return
            ok, out = self._git_run("commit", "-m", msg, timeout=30)
            if ok:
                status_lbl.setText(f"✓  Committed successfully.")
                self._git_refresh_status_bar()
                dlg.accept()
            else:
                status_lbl.setText(f"✗  {out}")

        btns.accepted.connect(_do_commit)
        dlg.exec()

    def _git_new_branch(self) -> None:
        """Git → New Branch — create and switch to a new local branch."""
        if not self.project_info:
            return
        from PyQt6.QtWidgets import QInputDialog, QMessageBox as _MB
        name, ok = QInputDialog.getText(
            self, "New Branch", "Branch name:",
        )
        if not ok or not name.strip():
            return
        name = name.strip().replace(" ", "-")
        ok2, msg = self._git_run("checkout", "-b", name, timeout=15)
        if ok2:
            self.statusBar().showMessage(f"Created and switched to branch '{name}'", 4000)
            self._git_refresh_status_bar()
        else:
            _MB.warning(self, "New Branch", f"git checkout -b failed:\n\n{msg}")

    def _git_stash(self) -> None:
        """Git → Stash Changes — git stash push."""
        if not self.project_info:
            return
        _, status_out = self._git_run("status", "--short", timeout=5)
        if not (status_out or "").strip():
            from PyQt6.QtWidgets import QMessageBox as _MB
            _MB.information(self, "Stash", "Nothing to stash — working tree is clean.")
            return
        ok, msg = self._git_run("stash", "push", "--include-untracked", "-m",
                                "PorySuite stash", timeout=30)
        if ok:
            self.statusBar().showMessage("Changes stashed.", 3000)
            self._git_refresh_status_bar()
        else:
            from PyQt6.QtWidgets import QMessageBox as _MB
            _MB.warning(self, "Stash Failed", f"git stash failed:\n\n{msg}")

    def _git_pop_stash(self) -> None:
        """Git → Pop Stash — git stash pop."""
        if not self.project_info:
            return
        _, stash_list = self._git_run("stash", "list", timeout=5)
        if not (stash_list or "").strip():
            from PyQt6.QtWidgets import QMessageBox as _MB
            _MB.information(self, "Pop Stash", "No stash entries to restore.")
            return
        ok, msg = self._git_run("stash", "pop", timeout=30)
        if ok:
            self.statusBar().showMessage("Stash restored.", 3000)
            self._git_refresh_status_bar()
        else:
            from PyQt6.QtWidgets import QMessageBox as _MB
            _MB.warning(self, "Pop Stash Failed", f"git stash pop failed:\n\n{msg}")

    def _git_view_log(self) -> None:
        """Git → View Log — show last 30 commits in a scrollable dialog."""
        if not self.project_info:
            return
        from PyQt6.QtWidgets import QDialog, QVBoxLayout, QHBoxLayout, QPlainTextEdit, QPushButton
        from PyQt6.QtGui import QFont

        _, log_out = self._git_run(
            "log", "--oneline", "--format=%C(auto)%h  %ad  %s  [%an]",
            "--date=short", "-30",
            timeout=15,
        )

        dlg = QDialog(self)
        dlg.setWindowTitle("Git Log  (last 30 commits)")
        dlg.setMinimumWidth(700)
        dlg.setMinimumHeight(400)
        vlay = QVBoxLayout(dlg)

        txt = QPlainTextEdit()
        txt.setReadOnly(True)
        txt.setFont(QFont("Courier New", 9))
        txt.setPlainText(log_out or "(no commits found)")
        vlay.addWidget(txt)

        btn_row = QHBoxLayout()
        btn_row.addStretch()
        close_btn = QPushButton("Close")
        close_btn.clicked.connect(dlg.accept)
        btn_row.addWidget(close_btn)
        vlay.addLayout(btn_row)

        dlg.exec()

    def _git_push(self) -> None:
        """
        Git → Push to Remote

        git push origin <current-branch>. Runs in a background QThread.
        """
        if not self.project_info:
            return
        project_dir = self.project_info.get("dir", "")
        if not project_dir or not os.path.isdir(project_dir):
            QMessageBox.warning(self, "Push", "Project directory not found.")
            return

        _, remote_url = self._git_run("remote", "get-url", "origin", timeout=10)
        if not remote_url:
            QMessageBox.warning(
                self, "Push to Remote",
                "No remote is configured.\n\nUse Git → Configure Remote… to set one first."
            )
            return

        _, branch = self._git_run("rev-parse", "--abbrev-ref", "HEAD", timeout=10)
        if not branch:
            branch = "HEAD"

        # Show a brief log of what's ahead
        _, ahead_log = self._git_run(
            "log", "--oneline", f"origin/{branch}..HEAD",
            timeout=10,
        )
        ahead_label = f"\n\nCommits to push:\n{ahead_log}" if ahead_log else "\n\n(No commits ahead of origin — push anyway?)"

        ans = QMessageBox.question(
            self,
            "Push to Remote",
            f"⚠ WARNING: Pushing will overwrite the remote with your local commits.\n\n"
            f"Anyone else pulling from this remote will get your changes. "
            f"If you have broken or incomplete work, it will be pushed too.\n\n"
            f"  Branch: {branch}\n"
            f"  Remote: {remote_url}"
            f"{ahead_label}\n\n"
            f"Are you sure you want to push?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Cancel,
        )
        if ans != QMessageBox.StandardButton.Yes:
            return

        self.statusBar().showMessage(f"Pushing {branch} to origin…", 0)
        self._refresh_action.setEnabled(False)
        self._git_set_all_enabled(False)

        from PyQt6.QtCore import QThread, pyqtSignal as _sig

        class _PushWorker(QThread):
            done = _sig(bool, str)
            def __init__(self, git, cwd, br):
                super().__init__()
                self._git = git
                self._cwd = cwd
                self._branch = br
            def run(self):
                try:
                    r = subprocess.run(
                        [self._git, "-C", self._cwd, "push", "origin", self._branch],
                        capture_output=True, text=True, timeout=180,
                        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                    )
                    out = (r.stdout + r.stderr).strip()
                    self.done.emit(r.returncode == 0, out)
                except FileNotFoundError:
                    self.done.emit(False, "git not found — install Git for Windows.")
                except subprocess.TimeoutExpired:
                    self.done.emit(False, "git push timed out after 3 minutes.")
                except Exception as exc:
                    self.done.emit(False, str(exc))

        worker = _PushWorker(self._git_exe(), project_dir, branch)
        self._git_worker = worker

        def _on_done(ok: bool, msg: str):
            self._refresh_action.setEnabled(True)
            self._git_set_all_enabled(True)
            if ok:
                self.statusBar().showMessage(f"Push complete: {branch} → origin", 5000)
                self._git_refresh_status_bar()
            else:
                self.statusBar().showMessage("Push failed.", 4000)
                QMessageBox.critical(self, "Push Failed", f"git reported:\n\n{msg}")

        worker.done.connect(_on_done)
        worker.start()

    # ── Make ──────────────────────────────────────────────────────────────────

    def _run_make(self, extra_args: list) -> None:
        """Open an MSYS2 MINGW64 terminal and run make in the pokefirered project directory."""
        if not self.project_info:
            QMessageBox.information(self, 'Make', 'No project is open.')
            return
        project_dir = self.project_info.get('dir', '')
        if not project_dir:
            return
        if sys.platform != 'win32':
            QMessageBox.information(self, 'Make', 'Make is only supported on Windows.')
            return

        from programsetup import (
            _find_bash as _ps_find_bash,
            _find_agbcc_source,
            _devkitpro_env_exports,
            _win_path_to_msys,
            _InAppBuildDialog,
        )

        # ── Check MSYS2 is available ───────────────────────────────────────────
        bash_exe = _ps_find_bash()
        if not bash_exe:
            QMessageBox.warning(self, 'MSYS2 Required',
                'MSYS2 was not found at C:\\msys64\\usr\\bin\\bash.exe.\n\n'
                'Install MSYS2 from https://www.msys2.org/ to use this feature.\n\n'
                'You can also open Settings → Setup Wizard for guided installation.')
            return

        # ── Auto-provision agbcc if missing from the project ──────────────────
        # Under MSYS2, OS=Windows_NT the Makefile looks for agbcc.exe.
        # We provision the FULL toolchain tree (bin/, lib/, include/) so that
        # libgcc.a, libc.a and the GBA headers are also available.
        # Check for full completeness — a partial provision (e.g. bin/ only)
        # means include/ and lib/ are also needed.
        agbcc_project_root = os.path.join(project_dir, 'tools', 'agbcc')
        agbcc_bin = os.path.join(agbcc_project_root, 'bin')
        agbcc_complete = (
            os.path.isfile(os.path.join(agbcc_bin, 'agbcc.exe')) and
            os.path.isfile(os.path.join(agbcc_project_root, 'lib', 'libgcc.a')) and
            os.path.isfile(os.path.join(agbcc_project_root, 'include', 'string.h'))
        )
        if not agbcc_complete:
            src_root = _find_agbcc_source()   # returns agbcc toolchain root dir
            if src_root:
                import shutil as _shutil
                # Mirror each subdirectory using copytree so nested dirs
                # (include/machine/, include/sys/) are handled correctly.
                for subdir in ('bin', 'lib', 'include'):
                    src_sub = os.path.join(src_root, subdir)
                    dst_sub = os.path.join(agbcc_project_root, subdir)
                    if os.path.isdir(src_sub):
                        if os.path.exists(dst_sub):
                            _shutil.rmtree(dst_sub)
                        _shutil.copytree(src_sub, dst_sub)
                if os.path.isfile(os.path.join(agbcc_bin, 'agbcc.exe')):
                    self.log(f"agbcc toolchain provisioned from {src_root}")
                else:
                    QMessageBox.warning(self, 'agbcc Missing',
                        f'agbcc folder found at {src_root} but contains no binaries.\n'
                        'Open Settings → Setup Wizard and click "Build agbcc".')
                    return
            else:
                QMessageBox.warning(self, 'agbcc Not Found',
                    'agbcc.exe was not found.\n\n'
                    'Open Settings → Setup Wizard and click "Build agbcc".')
                return

        msys_path = _win_path_to_msys(project_dir)
        make_args = ' '.join(extra_args)
        make_cmd  = f'make {make_args}'.strip()

        # Build the GBA host tools (gbagfx.exe etc.) if they are missing.
        sample_tool = os.path.join(project_dir, 'tools', 'gbagfx', 'gbagfx.exe')
        needs_tools = not os.path.isfile(sample_tool)
        tools_prefix = 'make tools && ' if needs_tools else ''
        if needs_tools:
            self.log("Host tools missing — will run 'make tools' first.")

        # devkitPro env: sets DEVKITPRO, DEVKITARM, and prepends devkitARM/bin
        dkp_exports = _devkitpro_env_exports()
        if not dkp_exports:
            QMessageBox.warning(self, 'devkitPro Not Found',
                'devkitPro was not found at C:\\devkitPro.\n\n'
                'Install devkitPro (selecting GBA Development) from '
                'https://devkitpro.org/wiki/Getting_Started, then try again.')
            return

        # pokefirered's non-modern Makefile sets CPP := $(CC) -E.  GNU make
        # defaults CC to 'cc', which doesn't exist in MSYS2 — pass the ARM
        # cross-compiler explicitly so preprocessing works.
        # Always safe: modern mode uses arm-none-eabi-cpp and ignores CC.
        cc_override = 'CC=arm-none-eabi-gcc'
        if cc_override not in make_cmd:
            make_cmd = make_cmd.replace('make ', f'make {cc_override} ', 1)

        bash_cmd = (
            f'{dkp_exports}'
            # devkitPro's MSYS2 does not ship libstdc++-6.dll; it lives in
            # standalone MSYS2.  Pre-compiled tools (scaninc.exe, preproc.exe,
            # gbafix.exe) link against it dynamically, so add both mingw64/bin
            # dirs so Windows can resolve the DLL regardless of which MSYS2
            # bash is running.
            'export PATH=/c/msys64/mingw64/bin:/mingw64/bin:$PATH; '
            f'cd "{msys_path}" && '
            f'{tools_prefix}'
            f'{make_cmd}; '
            'EXIT_CODE=$?; '
            'echo ""; echo "--- build finished (exit $EXIT_CODE) ---"; '
            'exit $EXIT_CODE'
        )
        env_extra = {'MSYSTEM': 'MINGW64', 'CHERE_INVOKING': '1'}
        title = 'Make (Modern)' if extra_args else 'Make'
        dlg = _InAppBuildDialog(
            title, bash_exe, ['--login', '-c', bash_cmd],
            env_extra=env_extra, parent=self,
        )
        # Modeless — user can interact with the app while the build runs.
        dlg.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        dlg.show()

    def load_save_data(self, project_info):
        """
        Loads the save data for the specified project_info and saves the data afterwards.
        Used as a slot for the loadAndSaveProjectSignal.

        Parameters:
            project_info: The project information to load the save data for.
        """
        # Check if there are unsaved changes
        if self.isWindowModified():
            # Open dialog asking to save first
            ret = app_util.create_unsaved_changes_dialog(self)
            if ret == QMessageBox.StandardButton.Cancel:
                return
            if ret == QMessageBox.StandardButton.Save:
                self.update_save()

        # Load data for the requested project. Do not automatically persist
        # any in-memory adjustments made during load; saving must be an
        # explicit user action.
        self.load_data(project_info)

    def save_data(self, parse_headers: bool = True):
        """
        Save the source data and update project information.

        This method saves the source data, updates the date_modified field in the project_info dictionary,
        updates the projects.json file, and saves the local project info in the project.json file.
        It also updates the window title with the project name.

        Parameters:
        - None

        Returns:
        - None
        """
        # Flush Graphics tab edits (pic coords, elevation, icon palette index,
        # .pal files) before the main save pipeline runs.
        try:
            if getattr(self, "graphics_tab_widget", None) is not None:
                ok, errs = self.graphics_tab_widget.flush_to_disk()
                if ok or errs:
                    logging.info(
                        "Graphics tab: wrote %d file(s); errors: %s",
                        ok, errs or "none",
                    )
        except Exception:
            logging.exception("graphics_tab_widget.flush_to_disk failed")

        # Save the source data
        self.source_data.save()
        # Persist any generated C code (headers) back to the project's source files
        if parse_headers:
            try:
                # This calls the data manager's parse_to_c_code which regenerates headers
                self.parse_data_to_c_code()
            except Exception:
                # Don't block saving JSON if header generation fails; log instead
                try:
                    self.log("Warning: failed to write generated C headers during save")
                except Exception:
                    pass

        # Update the date_modified field
        self.project_info["date_modified"] = datetime.datetime.now().strftime(
            "%Y-%m-%d %H:%M:%S"
        )

        # Load the projects.json file
        d_dir = get_data_dir()
        p_file = os.path.join(d_dir, "projects.json")
        with open(p_file, "r") as f:
            p = json.load(f)

        # Remove the project from the list if it already exists
        for i in range(len(p["projects"])):
            if p["projects"][i]["dir"] == self.project_info["dir"]:
                p["projects"].pop(i)
                break

        # Create the project info for the projects.json file
        d_dir_project_info = {
            "name": self.project_info["name"],
            "project_name": self.project_info["project_name"],
            "dir": self.project_info["dir"],
            "last_opened": self.project_info["last_opened"],
        }

        # Insert the project info at the beginning of the list
        p["projects"].insert(0, d_dir_project_info)

        # Save the updated projects.json file
        with open(p_file, "w") as f:
            json.dump(p, f, indent=4)

        # Save the local project info in the project.json file
        with open(os.path.join(self.project_info["dir"], "project.json"), "w") as f:
            local_project_info = {
                "name": self.project_info["name"],
                "project_name": self.project_info["project_name"],
                "version": self.project_info["version"],
                "plugin_identifier": self.project_info["plugin_identifier"],
                "plugin_version": self.project_info["plugin_version"],
                "date_created": self.project_info["date_created"],
                "date_modified": self.project_info["date_modified"],
            }
            json.dump(local_project_info, f, indent=4)

        # Update the window title with the project path
        self.setWindowTitle(f"{self.project_info['dir']}[*]")

        # Reset the unsaved changes flag
        self.setWindowModified(False)

    def parse_data_to_c_code(self):
        """
        Parses the source data and generates C code.
        """
        self.source_data.parse_to_c_code()

    def load_data(self, combined_project_info):
        """
        Loads data for the main window of the PorySuite application.

        Parameters:
            combined_project_info (dict): A dictionary containing the combined project information.

        Returns:
            None
        """
        self.project_info = combined_project_info
        self.setWindowTitle(f"{self.project_info['dir']}[*]")
        self.statusbar_project_label.setText(self.project_info["plugin_identifier"])
        self.statusBar().showMessage(f"Loaded project {self.project_info['name']}")

        # Reset selection state so switching projects doesn't run save_species_data
        # against stale species from the previous project.
        self.previous_selected_species = None
        self.previous_selected_form = None

        # Invalidate learnset option cache so it rebuilds for the new project's move data.
        self._learnset_cache_valid = False

        # Configure description line limits/width based on the opened project
        self._configure_description_limits()

        # Create the data manager directly (no plugin discovery needed)
        try:
            self.source_data = _core.create_data_manager(
                combined_project_info, logger=self.log
            )
        except RuntimeError as e:
            msg = QMessageBox(self)
            msg.setIcon(QMessageBox.Icon.Warning)
            msg.setWindowTitle("Invalid Project Root")
            msg.setText("Project root validation failed.\n" + str(e))
            wizard_btn = msg.addButton(
                "Run Setup Wizard", QMessageBox.ButtonRole.AcceptRole
            )
            msg.addButton(QMessageBox.StandardButton.Cancel)
            msg.exec()
            if msg.clickedButton() is wizard_btn:
                d = NewProject(parent=self)
                d.show()
            return

        # Save selected plugin back to project.json
        project_file = os.path.join(self.project_info["dir"], "project.json")
        try:
            with open(project_file, "r") as f:
                p_data = json.load(f)
        except Exception:
            p_data = {}
        p_data.update(
            {
                "name": self.project_info.get("name"),
                "project_name": self.project_info.get("project_name"),
                "version": self.project_info.get("version"),
                "plugin_identifier": self.project_info.get("plugin_identifier"),
                "plugin_version": self.project_info.get("plugin_version"),
                "date_created": self.project_info.get("date_created"),
                "date_modified": self.project_info.get("date_modified"),
            }
        )
        with open(project_file, "w") as f:
            json.dump(p_data, f, indent=4)

        # Set item delegates and fonts for the UI elements
        self.ui.list_pokedex_national.setItemDelegate(
            PokedexItemDelegate(self.ui.list_pokedex_national)
        )
        self.ui.list_pokedex_national.setFont(QFont("Source Code Pro", 11))
        self.ui.list_pokedex_regional.setItemDelegate(
            PokedexItemDelegate(self.ui.list_pokedex_regional)
        )
        self.ui.list_pokedex_regional.setFont(QFont("Source Code Pro", 11))
        self.ui.tree_pokemon.setItemDelegate(PokedexItemDelegate(self.ui.tree_pokemon))
        self.ui.tree_pokemon.setFont(QFont("Source Code Pro", 11))
        self.ui.species_description.setFont(QFont("Source Code Pro", 11))

        # Ensure species types are authoritative from the header before UI population.
        # Some projects ship stale JSON caches; prefer the header's `.types` entries.
        try:
            def _read_species_types_from_header(root_path: str) -> dict:
                import re
                header_path = os.path.join(root_path, "src", "data", "pokemon", "species_info.h")
                mapping: dict[str, list[str]] = {}
                if not os.path.isfile(header_path):
                    return mapping
                with open(header_path, encoding="utf-8") as hf:
                    content = hf.read()
                # Find blocks like [SPECIES_BULBASAUR] = { ... }
                for m in re.finditer(r"\[(SPECIES_[A-Z0-9_]+)\]\s*=\s*\{(.*?)\},", content, re.S):
                    key = m.group(1)
                    block = m.group(2)
                    mt = re.search(r"\.types\s*=\s*\{([^}]*)\}", block)
                    if mt:
                        parts = [p.strip() for p in mt.group(1).split(",") if p.strip()]
                        cleaned = []
                        for p in parts[:2]:
                            token = re.sub(r"\(.*?\)", "", p).strip()
                            token = token.rstrip(',')
                            cleaned.append(token)
                        while len(cleaned) < 2:
                            cleaned.append("TYPE_NONE")
                        mapping[key] = cleaned
                return mapping

            proj_root = self.project_info.get("dir")
            if proj_root:
                hdr_map = _read_species_types_from_header(proj_root)
                # Iterate species in source_data and apply header types where present
                pokemon_data = self.source_data.get_pokemon_data()
                for sp in list(pokemon_data.keys()):
                    # species keys in JSON are often already 'SPECIES_...'
                    sp_const = sp if str(sp).upper().startswith("SPECIES_") else f"SPECIES_{str(sp).upper()}"
                    if sp_const in hdr_map:
                        # Directly overwrite the in-memory JSON cache to ensure the
                        # editor reads header types (bypass any set_species_info
                        # wrappers which may not update the raw JSON structure).
                        try:
                            data_dict = getattr(self.source_data, 'data', None)
                            if isinstance(data_dict, dict) and sp in data_dict:
                                si = data_dict[sp].get('species_info')
                                if isinstance(si, dict):
                                    if si.get('types') != hdr_map[sp_const]:
                                        si['types'] = hdr_map[sp_const]
                                        # mark pending and write back immediately so UI sees it
                                        try:
                                            self.source_data.pending_changes = True
                                        except Exception:
                                            pass
                            else:
                                # fallback to set_species_info if direct path unavailable
                                try:
                                    self.source_data.set_species_info(sp, 'types', hdr_map[sp_const])
                                    try:
                                        self.source_data.pending_changes = True
                                    except Exception:
                                        pass
                                except Exception:
                                    pass
                        except Exception:
                            pass
        except Exception:
            pass

        # Add Pokemon species — clear everything that add_species() populates so
        # re-loading a project doesn't accumulate duplicate entries.
        self.ui.tree_pokemon.clear()
        self.ui.evo_species.clear()
        self.ui.starter1_species.clear()
        self.ui.starter2_species.clear()
        self.ui.starter3_species.clear()
        pokemon_data = self.source_data.get_pokemon_data()
        valid_species = []
        skipped = []
        for sp in pokemon_data.keys():
            dex = self.source_data.get_species_data(sp, "dex_num")
            if isinstance(dex, int):
                valid_species.append((sp, dex))
            else:
                skipped.append(sp)
        for sp, _ in sorted(valid_species, key=lambda t: t[1]):
            self.add_species(sp, self.source_data.get_species_data(sp, "forms").keys())
        if skipped:
            print(f"Skipped species with invalid dex numbers: {', '.join(skipped)}")

        # Add abilities to ability combo boxes
        self.ui.ability1.clear()
        self.ui.ability2.clear()
        abilities = self.source_data.get_pokemon_abilities()

        # Enrich with display names + descriptions from the C text file.
        # The JSON cache only stores {name, id}; the real display names
        # live in src/data/text/abilities.h.
        try:
            root = self.source_data.docker_util.repo_root()
            self._enrich_abilities_from_text(root, abilities)
        except Exception:
            pass

        for ability in sorted(
            abilities.keys(), key=lambda x: self.source_data.get_ability_data(x, "id")
        ):
            display = (self.source_data.get_ability_data(ability, "display_name")
                        or self.source_data.get_ability_data(ability, "name"))
            self.ui.ability1.addItem(display, ability)
            self.ui.ability2.addItem(display, ability)

        # Load abilities editor
        try:
            self.load_abilities_editor()
        except Exception:
            pass

        # Add items to item combo boxes (fallback to constants when cache is empty)
        self._populate_item_comboboxes([
            getattr(self.ui, 'held_item_common', None),
            getattr(self.ui, 'held_item_rare', None),
            getattr(self.ui, 'starter1_item', None),
            getattr(self.ui, 'starter2_item', None),
            getattr(self.ui, 'starter3_item', None),
        ])

        # Add types to type combo boxes
        types = self.source_data.get_constant("types") or {}
        # Ensure a "None" option exists even if not defined in headers
        if "TYPE_NONE" not in types:
            types = {"TYPE_NONE": {"name": "None", "value": -1}, **types}
        self.type_index_map.clear()
        self.ui.type1.clear()
        self.ui.type2.clear()
        # Map human-readable type names to their constant keys so update_data
        # can resolve types stored as names (e.g. "Grass") rather than
        # constant identifiers (e.g. "TYPE_GRASS").
        self.type_name_map: dict[str, str] = {}
        sorted_types = sorted(
            types.items(),
            key=lambda item: (
                item[1].get("value", 0) if isinstance(item[1], dict) else 0
            ),
        )
        for idx, (poke_type, type_info) in enumerate(sorted_types):
            name = type_info.get("name") if isinstance(type_info, dict) else type_info
            # TODO: Add type icons
            self.ui.type1.addItem(name, poke_type)
            self.ui.type2.addItem(name, poke_type)
            # remember mapping from displayed name to constant key
            try:
                if isinstance(name, str):
                    self.type_name_map[name.lower()] = poke_type
            except Exception:
                pass
            self.type_index_map[poke_type] = idx

        # Add egg groups to egg group combo boxes
        self.ui.egg_group_1.clear()
        self.ui.egg_group_2.clear()
        egg_groups = self.source_data.get_constant("egg_groups") or {}
        for egg_group, egg_info in egg_groups.items():
            name = egg_info.get("name") if isinstance(egg_info, dict) else egg_info
            self.ui.egg_group_1.addItem(name, egg_group)
            self.ui.egg_group_2.addItem(name, egg_group)

        # Add growth rates to growth rate combo box
        self.ui.exp_growth_rate.clear()
        growth_rates = self.source_data.get_constant("growth_rates") or {}
        for growth_rate, gr_info in growth_rates.items():
            name = gr_info.get("name") if isinstance(gr_info, dict) else gr_info
            self.ui.exp_growth_rate.addItem(name, growth_rate)
        # Add evolution methods to evolution method combo box
        evo_methods = self.source_data.get_constant("evolution_types") or {}
        try:
            self.ui.evo_method.clear()
        except Exception:
            pass
        # Insert a blank placeholder
        self.ui.evo_method.addItem("", None)
        for evolution_method, evo_info in evo_methods.items():
            # EVO_MODE_* are internal engine flags used by GetEvolutionTargetSpecies
            # to decide HOW to scan the table (level-up vs trade vs item use).
            # They are not valid evolution methods — skip them.
            if evolution_method.startswith("EVO_MODE_"):
                continue
            name = evo_info.get("name") if isinstance(evo_info, dict) else evo_info
            self.ui.evo_method.addItem(name, evolution_method)
        self.ui.evo_param.setEditable(True)
        # Add species flags to the flags list
        self.ui.species_flags.clear()
        # Hard-wired flags mapping (PokéFirered uses concrete fields)
        virtual_flags = [
            ("UNBREEDABLE", "Egg Group: Undiscovered"),
            ("GENDERLESS", "Genderless"),
            ("NO_FLIP", "No Flip"),
            ("STARTER", "Starter"),
            ("IN_NATDEX", "In National Dex"),
            ("IN_REGDEX", "In Regional Dex"),
        ]
        tooltips = {
            "UNBREEDABLE": "Species cannot breed (egg group: UNDISCOVERED).",
            "GENDERLESS": "Species is genderless in-game (genderRatio = 255).",
            "NO_FLIP": "Rendering hint: do not flip sprites.",
            "STARTER": "Mark as a starter (writes src/data/starters.json).",
            "IN_NATDEX": "Managed by NATIONAL_DEX enum in include/constants/pokedex.h.",
            "IN_REGDEX": "Managed by KANTO_DEX_COUNT in include/constants/pokedex.h.",
        }
        for key, label in virtual_flags:
            item = QListWidgetItem(label)
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            item.setCheckState(Qt.CheckState.Unchecked)
            item.setData(Qt.ItemDataRole.UserRole, key)
            # Engine-managed dex flags: show state but grey out and explain where to change
            if key in ("IN_NATDEX", "IN_REGDEX"):
                try:
                    item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEnabled)
                except Exception:
                    pass
            item.setToolTip(tooltips.get(key, ""))
            self.ui.species_flags.addItem(item)

        # cache for header-derived national ordering and cutoff
        self._nat_order_cache: list[str] | None = None
        self._kanto_cutoff_const: str | None = None

        # Set starter data without warnings if fewer than three
        starters = self.source_data.get_pokemon_starters()
        widgets = [
            (
                self.ui.starter1_species,
                self.ui.starter1_level,
                self.ui.starter1_item,
            ),
            (
                self.ui.starter2_species,
                self.ui.starter2_level,
                self.ui.starter2_item,
            ),
            (
                self.ui.starter3_species,
                self.ui.starter3_level,
                self.ui.starter3_item,
            ),
        ]
        for idx, starter in enumerate(starters):
            if idx >= len(widgets):
                break
            species_box, level_spin, item_box = widgets[idx]
            species_idx = species_box.findData(starter.get("species"))
            if species_idx == -1:
                species_idx = 0
            species_box.setCurrentIndex(species_idx)
            level_spin.setValue(starter.get("level", level_spin.value()))
            item_idx = item_box.findData(starter.get("item"))
            if item_idx == -1:
                item_idx = 0
            item_box.setCurrentIndex(item_idx)

        # Add moves to move combo boxes
        self.ui.starter1_move.clear()
        self.ui.starter2_move.clear()
        self.ui.starter3_move.clear()
        moves = self.source_data.get_pokemon_moves()
        for move in sorted(
            moves.keys(), key=lambda x: (self.source_data.get_move_data(x, "id") or 0)
        ):
            self.ui.starter1_move.addItem(
                self.source_data.get_move_data(move, "name"), move
            )
            self.ui.starter2_move.addItem(
                self.source_data.get_move_data(move, "name"), move
            )
            self.ui.starter3_move.addItem(
                self.source_data.get_move_data(move, "name"), move
            )

        # Add species to national dex list
        self.ui.list_pokedex_national.clear()
        natdex = self.source_data.get_national_dex()
        for entry in natdex:
            dex_const = entry.get("dex_constant", entry)
            species   = entry.get("species")
            name = None
            if species:
                name = self.source_data.get_species_data(species, "name")
            if not name:
                name = species or dex_const
            item = QListWidgetItem(name)
            item.setData(Qt.ItemDataRole.UserRole, dex_const)
            # Species icon (frame 0 of icon sprite)
            if species:
                item.setIcon(self._species_list_icon(species))
            self.ui.list_pokedex_national.addItem(item)

        # Auto-select first entry so the detail panel is populated on load.
        # setCurrentRow fires itemSelectionChanged → update_pokedex_entry.
        if self.ui.list_pokedex_national.count() > 0:
            self.ui.list_pokedex_national.setCurrentRow(0)

        # Add species to regional dex list
        self.ui.list_pokedex_regional.clear()
        regdex = self.source_data.get_regional_dex()
        for entry in regdex:
            dex_const = entry.get("dex_constant", entry)
            nat_const = dex_const.replace("HOENN_DEX_", "NATIONAL_DEX_")
            species   = self.source_data.get_species_by_dex_constant(nat_const)
            name = None
            if species:
                name = self.source_data.get_species_data(species, "name")
            if not name:
                name = species or dex_const
            item = QListWidgetItem(name)
            item.setData(Qt.ItemDataRole.UserRole, dex_const)
            if species:
                item.setIcon(self._species_list_icon(species))
            self.ui.list_pokedex_regional.addItem(item)

        # Setup the LocalUtil
        self.docker_util = LocalUtil(self.project_info)

        # Populate the items editor after the event loop has finished the current
        # layout pass, so the QListWidget is fully visible when items are added.
        # Loading into a hidden widget can cause Qt to cache a blank first-render,
        # making all rows invisible; deferring avoids this.
        if QTimer is not None:
            QTimer.singleShot(0, self._deferred_load_items)
        else:
            self._deferred_load_items()

        # Load Config and UI content tabs from the project directory
        _project_dir = str(self.project_info.get("dir", "") or "")
        if _project_dir:
            try:
                self.config_tab.load(_project_dir)
            except Exception:
                pass
            try:
                self.ui_tab.load(_project_dir)
            except Exception:
                pass
            try:
                if hasattr(self, "overworld_graphics_tab"):
                    self.overworld_graphics_tab.load(_project_dir)
            except Exception:
                pass

        # Enable Refresh, Open in EVENTide, and all Git menu actions now that a project is loaded
        if hasattr(self, "_refresh_action"):
            self._refresh_action.setEnabled(True)
        if hasattr(self, "_open_eventide_action"):
            self._open_eventide_action.setEnabled(True)
        for _act in (
            "_git_panel_action",
            "_git_configure_action", "_git_status_action",
            "_pull_upstream_action", "_pull_origin_action",
            "_push_action", "_git_commit_action", "_git_new_branch_action",
            "_git_stash_action", "_git_pop_stash_action", "_git_log_action",
        ):
            if hasattr(self, _act):
                getattr(self, _act).setEnabled(True)
        if hasattr(self, "_pull_menu"):
            self._pull_menu.setEnabled(True)
        self._git_refresh_status_bar()

        # Clear the modified flag — UI population fires signals that incorrectly
        # mark the window as dirty before the user touches anything.
        self.setWindowModified(False)

    def _load_national_order_from_header(self) -> list[str]:
        if self._nat_order_cache is not None:
            return self._nat_order_cache
        order: list[str] = []
        try:
            root = self.project_info.get("dir")
            if not root:
                return []
            path = os.path.join(root, "include", "constants", "pokedex.h")
            with open(path, encoding="utf-8") as f:
                counting = False
                for raw in f:
                    line = raw.split("//")[0].strip()
                    if not counting and line.startswith("enum"):
                        counting = True
                        continue
                    if not counting:
                        continue
                    if line.startswith("};"):
                        break
                    if "NATIONAL_DEX_" in line:
                        const = line.split("NATIONAL_DEX_")[-1]
                        const = "NATIONAL_DEX_" + const.split(",")[0].split()[0]
                        if const != "NATIONAL_DEX_NONE":
                            order.append(const)
        except Exception:
            order = []
        self._nat_order_cache = order
        return order

    def _load_kanto_cutoff_from_header(self) -> str:
        if self._kanto_cutoff_const is not None:
            return self._kanto_cutoff_const
        cutoff = "NATIONAL_DEX_MEW"  # default 151
        try:
            root = self.project_info.get("dir")
            if root:
                path = os.path.join(root, "include", "constants", "pokedex.h")
                with open(path, encoding="utf-8") as f:
                    for raw in f:
                        line = raw.split("//")[0].strip()
                        if line.startswith("#define KANTO_DEX_COUNT"):
                            parts = line.split()
                            if len(parts) >= 3:
                                cutoff = parts[2]
                            break
        except Exception:
            pass
        self._kanto_cutoff_const = cutoff
        return cutoff

    def add_species(self, species, forms):
        """
        Adds a species and its forms to the UI tree and dropdown menus.

        Parameters:
        - species (str): The species identifier.
        - forms (list): A list of form identifiers for the species.

        Returns:
        - species_item (QTreeWidgetItem): The added species item in the UI tree.
        """
        species_name = self.source_data.get_species_data(species, "name")
        species_item = QTreeWidgetItem([species_name, species])
        species_item.setIcon(0, self._species_list_icon(species))
        self.ui.tree_pokemon.addTopLevelItem(species_item)
        self.ui.evo_species.addItem(species, species)
        self.ui.starter1_species.addItem(species, species)
        self.ui.starter2_species.addItem(species, species)
        self.ui.starter3_species.addItem(species, species)

        # Add forms as child items and to the dropdown menus
        if len(forms) > 0:
            for form in forms:
                form_name = self.source_data.get_species_data(species, "name", form)
                form_item = QTreeWidgetItem([form_name, form, species])
                form_item.setIcon(0, self._species_list_icon(species, form))
                species_item.addChild(form_item)
                self.ui.evo_species.addItem("    " + form, form, species)
                self.ui.starter1_species.addItem("    " + form, form, species)
                self.ui.starter2_species.addItem("    " + form, form, species)
                self.ui.starter3_species.addItem("    " + form, form, species)
            species_item.setExpanded(False)

        return species_item

    def update_data(self, species, form=None):
        """
        Update the data displayed in the UI for a given species and form.

        Parameters:
        - species (str): The name of the species.
        - form (str, optional): The form of the species. Defaults to None.

        Returns:
        None
        """
        # Update species information
        self.ui.species_name.setText(
            self.source_data.get_species_info(species, "speciesName", form) or ""
        )
        dex_num = self.source_data.get_species_data(species, "dex_num", form)
        if not isinstance(dex_num, int):
            dex_num = 0
        self.ui.dex_num.setText(f"{dex_num:0>4}")
        self.ui.species_category.setText(
            self.source_data.get_species_info(species, "categoryName", form) or ""
        )
        self.ui.species_description.setPlainText(
            self.source_data.get_species_info(species, "description", form) or ""
        )
        self.ui.base_hp.setValue(
            self.source_data.get_species_info(species, "baseHP", form) or 0
        )
        self.ui.base_atk.setValue(
            self.source_data.get_species_info(species, "baseAttack", form) or 0
        )
        self.ui.base_def.setValue(
            self.source_data.get_species_info(species, "baseDefense", form) or 0
        )
        self.ui.base_speed.setValue(
            self.source_data.get_species_info(species, "baseSpeed", form) or 0
        )
        self.ui.base_spatk.setValue(
            self.source_data.get_species_info(species, "baseSpAttack", form) or 0
        )
        self.ui.base_spdef.setValue(
            self.source_data.get_species_info(species, "baseSpDefense", form) or 0
        )

        # Update types
        types = self.source_data.get_species_info(species, "types", form)
        if not isinstance(types, list):
            types = []
        # Normalize: handle legacy numeric entries by mapping value->const.
        # Prefer `get_constant` when available; fall back to empty map when
        # test stubs only provide `get_constant_data`.
        try:
            const_map = self.source_data.get_constant("types") or {}
        except AttributeError:
            const_map = {}
        value_to_const = {}
        for k, v in const_map.items():
            val = v.get("value") if isinstance(v, dict) else None
            if isinstance(val, int):
                value_to_const[val] = k
        norm = []
        for t in types:
            # Accept either constant identifiers (e.g. "TYPE_GRASS"), numeric
            # values, or human-readable names (e.g. "Grass"). Prefer the
            # canonical constant name for the combo boxes.
            if isinstance(t, str):
                # already a constant like TYPE_GRASS
                if t in const_map:
                    norm.append(t)
                    continue
                # maybe a human-readable name; map to constant if possible
                try:
                    key = self.type_name_map.get(t.lower())
                except Exception:
                    key = None
                if key:
                    norm.append(key)
                    continue
                # fallback: append the original string so lookup can fail later
                norm.append(t)
            elif isinstance(t, int) and t in value_to_const:
                norm.append(value_to_const[t])
        while len(norm) < 2:
            norm.append("TYPE_NONE")
        # Avoid signal storms while programmatically updating
        # Prefer robust fallbacks in tests where combo boxes are stubs without
        # findData/findText: use type_index_map to set indices directly.
        if not hasattr(self.ui.type1, "findData") or not hasattr(self.ui.type2, "findData"):
            idx0 = 0
            idx1 = 0
            try:
                idx0 = (self.type_index_map or {}).get(norm[0], 0)
                idx1 = (self.type_index_map or {}).get(norm[1], 0)
            except Exception:
                pass
            self.ui.type1.setCurrentIndex(idx0)
            self.ui.type2.setCurrentIndex(idx1)
        else:
            try:
                from PyQt6.QtCore import QSignalBlocker
                b1 = QSignalBlocker(self.ui.type1)
                b2 = QSignalBlocker(self.ui.type2)
            except Exception:
                b1 = b2 = None
            # Try to find index by stored userData (constant), fall back to display name
            try:
                i1 = self.ui.type1.findData(norm[0], Qt.ItemDataRole.UserRole)
            except Exception:
                i1 = -1
            try:
                i2 = self.ui.type2.findData(norm[1], Qt.ItemDataRole.UserRole)
            except Exception:
                i2 = -1
            # Fallback: map constant -> display name and search by text
            try:
                if i1 < 0:
                    name0 = (
                        const_map.get(norm[0], {}).get("name")
                        if isinstance(const_map.get(norm[0]), dict)
                        else const_map.get(norm[0])
                    )
                    if not name0:
                        # maybe norm[0] is already a display name
                        name0 = norm[0]
                    i1 = self.ui.type1.findText(name0)
            except Exception:
                pass
            try:
                if i2 < 0:
                    name1 = (
                        const_map.get(norm[1], {}).get("name")
                        if isinstance(const_map.get(norm[1]), dict)
                        else const_map.get(norm[1])
                    )
                    if not name1:
                        name1 = norm[1]
                    i2 = self.ui.type2.findText(name1)
            except Exception:
                pass

            try:
                self.ui.type1.setCurrentIndex(0 if i1 < 0 else i1)
                self.ui.type2.setCurrentIndex(0 if i2 < 0 else i2)
            except Exception:
                pass
            try:
                del b1, b2
            except Exception:
                pass

        # Update abilities
        ability_constants = []
        for i in range(2):
            try:
                ability = self.source_data.get_species_ability(species, i, form)
            except IndexError:
                ability = None
            if not ability:
                ability = "ABILITY_NONE"
            ability_constants.append(ability)
        ability_boxes = [self.ui.ability1, self.ui.ability2]
        for box, ability_const in zip(ability_boxes, ability_constants):
            ability_id = self.source_data.get_ability_data(ability_const, "id")
            if not isinstance(ability_id, int):
                ability_id = 0
            box.setCurrentIndex(ability_id)
        # Hidden ability (FireRed typically has none; default to NONE/0)
        hidden_const = None
        try:
            hidden_const = self.source_data.get_species_ability(species, 2, form)
        except IndexError:
            hidden_const = None
        hidden_id = 0
        try:
            if hidden_const:
                hid = self.source_data.get_ability_data(hidden_const, "id")
                if isinstance(hid, int):
                    hidden_id = hid
        except Exception:
            pass
        try:
            self.ui.ability_hidden.setCurrentIndex(hidden_id)
        except Exception:
            pass

        # Update EVs
        self.ui.evs_hp.setValue(
            self.source_data.get_species_info(species, "evYield_HP", form) or 0
        )
        self.ui.evs_atk.setValue(
            self.source_data.get_species_info(species, "evYield_Attack", form) or 0
        )
        self.ui.evs_def.setValue(
            self.source_data.get_species_info(species, "evYield_Defense", form) or 0
        )
        self.ui.evs_speed.setValue(
            self.source_data.get_species_info(species, "evYield_Speed", form) or 0
        )
        self.ui.evs_spatk.setValue(
            self.source_data.get_species_info(species, "evYield_SpAttack", form) or 0
        )
        self.ui.evs_spdef.setValue(
            self.source_data.get_species_info(species, "evYield_SpDefense", form) or 0
        )

        # Update other attributes
        self.ui.catch_rate.setValue(
            self.source_data.get_species_info(species, "catchRate", form) or 0
        )
        self.ui.exp_yield.setValue(
            self.source_data.get_species_info(species, "expYield", form) or 0
        )
        gender_ratio = self.source_data.get_species_info(species, "genderRatio", form)
        # Normalize gender ratio: accept numbers or header macros like
        # PERCENT_FEMALE(12.5) or MON_GENDERLESS. If JSON lacks a usable
        # value, fall back to reading the header's .genderRatio entry.
        def _parse_gender_token(tok: str):
            try:
                t = str(tok).strip()
            except Exception:
                return None
            if not t:
                return None
            # numeric literal
            try:
                return int(t)
            except Exception:
                pass
            # macros
            if t == "MON_GENDERLESS":
                return 255
            if t == "MON_MALE":
                return 0
            if t == "MON_FEMALE":
                return 254
            import re
            m = re.search(r"PERCENT_FEMALE\(\s*([0-9.]+)\s*\)", t)
            if m:
                try:
                    pct = float(m.group(1))
                    return int(round(pct * 255 / 100))
                except Exception:
                    return None
            return None

        if not isinstance(gender_ratio, int):
            parsed = None
            try:
                parsed = _parse_gender_token(gender_ratio)
            except Exception:
                parsed = None
            gender_ratio = parsed

        # Gender ratio should already be synced from the header at project
        # load time.  If it's still not an int, default to 0 rather than
        # re-reading the entire 11k-line species_info.h on every click.

        if not isinstance(gender_ratio, int):
            gender_ratio = 0
        # Update the Genderless checkbox and slider enablement
        try:
            for i in range(self.ui.species_flags.count()):
                item = self.ui.species_flags.item(i)
                key = item.data(Qt.ItemDataRole.UserRole)
                if key == "GENDERLESS":
                    if gender_ratio == 255:
                        item.setCheckState(Qt.CheckState.Checked)
                        self.ui.gender_ratio.setEnabled(False)
                        self.update_gender_ratio(255)
                    else:
                        item.setCheckState(Qt.CheckState.Unchecked)
                        self.ui.gender_ratio.setEnabled(True)
                        percent = int(round((gender_ratio * 100) / 254)) if isinstance(gender_ratio, int) else 0
                        try:
                            # Force slider to percent range and set
                            try:
                                self.ui.gender_ratio.setMaximum(100)
                            except Exception:
                                pass
                            with QSignalBlocker(self.ui.gender_ratio):
                                self.ui.gender_ratio.setValue(percent)
                            pass  # slider set
                            # Re-apply on next event loop tick and again after a short delay
                            # (use QSignalBlocker so setValue doesn't trigger setWindowModified)
                            try:
                                def _reapply_slider(p=percent, g=gender_ratio):
                                    with QSignalBlocker(self.ui.gender_ratio):
                                        self.ui.gender_ratio.setValue(p)
                                    self.update_gender_ratio(g)
                                QTimer.singleShot(0, _reapply_slider)
                                QTimer.singleShot(50, _reapply_slider)
                            except Exception:
                                pass
                        except Exception:
                            pass
                        self.update_gender_ratio(gender_ratio)
                        try:
                            print(f"[GENDER-DIAG] label after update: {self.ui.gender_ratio_label.text()}")
                        except Exception:
                            pass
                    break
        except Exception:
            try:
                percent = int(round((gender_ratio * 100) / 254)) if isinstance(gender_ratio, int) else 0
                with QSignalBlocker(self.ui.gender_ratio):
                    self.ui.gender_ratio.setValue(percent)
            except Exception:
                pass
            try:
                self.update_gender_ratio(gender_ratio)
            except Exception:
                pass
        item_common = self.source_data.get_species_info(species, "itemCommon", form)
        try:
            idx_common = self.ui.held_item_common.findData(item_common)
        except Exception:
            idx_common = 0
        try:
            self.ui.held_item_common.setCurrentIndex(0 if idx_common < 0 else idx_common)
        except Exception:
            pass
        item_rare = self.source_data.get_species_info(species, "itemRare", form)
        try:
            idx_rare = self.ui.held_item_rare.findData(item_rare)
        except Exception:
            idx_rare = 0
        try:
            self.ui.held_item_rare.setCurrentIndex(0 if idx_rare < 0 else idx_rare)
        except Exception:
            pass
        self.ui.egg_cycles.setValue(
            self.source_data.get_species_info(species, "eggCycles", form) or 0
        )
        egg_groups = self.source_data.get_species_info(species, "eggGroups", form)
        if not isinstance(egg_groups, list):
            egg_groups = []
        while len(egg_groups) < 2:
            egg_groups.append("EGG_GROUP_NONE")
        # Update egg groups; fall back to numeric IDs via get_constant_data when
        # combo boxes don't support findData in tests.
        if not hasattr(self.ui.egg_group_1, "findData") or not hasattr(self.ui.egg_group_2, "findData"):
            try:
                eg1_idx = 0
                eg2_idx = 0
                try:
                    eg1 = self.source_data.get_constant_data("egg_groups", egg_groups[0])
                    eg1_idx = eg1.get("value", 0) if isinstance(eg1, dict) else int(eg1)
                except Exception:
                    pass
                try:
                    eg2 = self.source_data.get_constant_data("egg_groups", egg_groups[1])
                    eg2_idx = eg2.get("value", 0) if isinstance(eg2, dict) else int(eg2)
                except Exception:
                    pass
                self.ui.egg_group_1.setCurrentIndex(eg1_idx)
                self.ui.egg_group_2.setCurrentIndex(eg2_idx)
            except Exception:
                pass
        else:
            try:
                b3 = QSignalBlocker(self.ui.egg_group_1)
                b4 = QSignalBlocker(self.ui.egg_group_2)
            except Exception:
                b3 = b4 = None
            try:
                eg1_idx = self.ui.egg_group_1.findData(egg_groups[0])
            except Exception:
                eg1_idx = -1
            try:
                eg2_idx = self.ui.egg_group_2.findData(egg_groups[1])
            except Exception:
                eg2_idx = -1
            try:
                self.ui.egg_group_1.setCurrentIndex(0 if eg1_idx < 0 else eg1_idx)
                self.ui.egg_group_2.setCurrentIndex(0 if eg2_idx < 0 else eg2_idx)
            except Exception:
                pass
            try:
                del b3, b4
            except Exception:
                pass
        growth_rate = self.source_data.get_species_info(species, "growthRate", form)
        # Growth rate
        if not hasattr(self.ui.exp_growth_rate, "findData"):
            try:
                self.ui.exp_growth_rate.setCurrentIndex(0)
            except Exception:
                pass
        else:
            try:
                b5 = QSignalBlocker(self.ui.exp_growth_rate)
            except Exception:
                b5 = None
            try:
                gr_idx = self.ui.exp_growth_rate.findData(growth_rate)
            except Exception:
                gr_idx = -1
            try:
                self.ui.exp_growth_rate.setCurrentIndex(0 if gr_idx < 0 else gr_idx)
            except Exception:
                pass
            try:
                del b5
            except Exception:
                pass
        self.ui.base_friendship.setValue(
            self.source_data.get_species_info(species, "friendship", form) or 0
        )
        self.ui.safari_zone_flee_rate.setValue(
            self.source_data.get_species_info(species, "safariZoneFleeRate", form) or 0
        )
        # Update species flags (checkboxes)
        if hasattr(self.ui, "species_flags"):
            try:
                # Read concrete fields and set checkbox states for every species
                try:
                    starters_list = self.source_data.get_pokemon_starters() if hasattr(self.source_data, 'get_pokemon_starters') else []
                except Exception:
                    starters_list = []
                # Build quick membership sets for dex lists
                natdex_species = set()
                for d in self.source_data.get_national_dex() or []:
                    if isinstance(d, dict):
                        sp = d.get("species")
                        if sp:
                            natdex_species.add(sp)
                regdex_consts = set()
                for d in self.source_data.get_regional_dex() or []:
                    if isinstance(d, dict):
                        dc = d.get("dex_constant")
                        if dc:
                            regdex_consts.add(dc)
                    elif isinstance(d, str):
                        regdex_consts.add(d)
                nat_const = self.source_data.get_species_data(species, "dex_constant", form)
                reg_const = nat_const.replace("NATIONAL_DEX_", "HOENN_DEX_") if isinstance(nat_const, str) else None
                for i in range(self.ui.species_flags.count()):
                    item = self.ui.species_flags.item(i)
                    key = None
                    try:
                        key = item.data(Qt.ItemDataRole.UserRole)
                    except Exception:
                        pass
                    if key == "NO_FLIP":
                        orig = self.source_data.get_species_info(species, "noFlip", form)
                        item.setData(1000, orig)
                        item.setCheckState(Qt.CheckState.Checked if str(orig)=="TRUE" else Qt.CheckState.Unchecked)
                    elif key == "GENDERLESS":
                        orig = self.source_data.get_species_info(species, "genderRatio", form)
                        item.setData(1000, orig)
                        try:
                            item.setCheckState(Qt.CheckState.Checked if int(orig)==255 else Qt.CheckState.Unchecked)
                        except Exception:
                            item.setCheckState(Qt.CheckState.Unchecked)
                    elif key in ("UNBREEDABLE", "LEGENDARY"):
                        orig = self.source_data.get_species_info(species, "eggGroups", form) or []
                        item.setData(1000, orig)
                        item.setCheckState(Qt.CheckState.Checked if isinstance(orig, list) and "EGG_GROUP_UNDISCOVERED" in orig else Qt.CheckState.Unchecked)
                    elif key == "STARTER":
                        item.setData(1000, starters_list)
                        item.setCheckState(Qt.CheckState.Checked if species in starters_list else Qt.CheckState.Unchecked)
                    elif key == "IN_NATDEX":
                        item.setData(1000, None)
                        item.setCheckState(Qt.CheckState.Checked if species in natdex_species else Qt.CheckState.Unchecked)
                    elif key == "IN_REGDEX":
                        # Compute from engine header: national order up to cutoff
                        nat_order = self._load_national_order_from_header()
                        cutoff = self._load_kanto_cutoff_from_header()
                        present = False
                        if isinstance(nat_const, str) and nat_const in nat_order:
                            try:
                                idx = nat_order.index(nat_const)
                                cutoff_idx = nat_order.index(cutoff)
                                present = idx <= cutoff_idx
                            except ValueError:
                                present = False
                        item.setData(1000, None)
                        item.setCheckState(Qt.CheckState.Checked if present else Qt.CheckState.Unchecked)
                    else:
                        item.setData(1000, None)
                        item.setCheckState(Qt.CheckState.Unchecked)
            except Exception:
                pass

        # Update graphics (slot 0 = normal, slot 1 = shiny when available)
        front_pic = self.source_data.get_species_image_path(
            species, "frontPic", form=form
        )
        # Store the graphics folder for the Open Folder button
        if front_pic:
            self._current_species_gfx_folder = os.path.dirname(
                front_pic.replace("/", os.sep)
            )
        else:
            self._current_species_gfx_folder = ""
        shiny_pic = None
        try:
            shiny_pic = self.source_data.get_species_shiny_image_path(
                species, "frontPic", form=form
            )
        except AttributeError:
            shiny_pic = None
        self.ui.frontPic_0.setStyleSheet(
            ""
            if front_pic is None
            else f"background-image: url({front_pic}); background-position: top;"
        )
        other_front = shiny_pic or front_pic
        self.ui.frontPic_1.setStyleSheet(
            ""
            if other_front is None
            else f"background-image: url({other_front}); background-position: bottom;"
        )
        if front_pic:
            logging.debug(
                "Loaded image URL for %s[%s]: %s", species, "frontPic", front_pic
            )
        if shiny_pic:
            logging.debug(
                "Loaded SHINY image path for %s[%s]: %s", species, "frontPic", shiny_pic
            )

        back_pic = self.source_data.get_species_image_path(
            species, "backPic", form=form
        )
        self.ui.backPic.setStyleSheet(
            ""
            if back_pic is None
            else f"background-image: url({back_pic}); background-position: center;"
        )
        if back_pic:
            logging.debug(
                "Loaded image URL for %s[%s]: %s", species, "backPic", back_pic
            )

        icon_pic = self.source_data.get_species_image_path(
            species, "iconSprite", form=form
        )
        self.ui.iconPic.setStyleSheet(
            ""
            if icon_pic is None
            else f"background-image: url({icon_pic}); background-position: center;"
        )
        if icon_pic:
            logging.debug(
                "Loaded image URL for %s[%s]: %s", species, "iconSprite", icon_pic
            )

        footprint_pic = self.source_data.get_species_image_path(
            species, "footprint", form=form
        )
        self.ui.footprintPic.setStyleSheet(
            ""
            if footprint_pic is None
            else f"background-image: url({footprint_pic}); background-position: center;"
        )
        if footprint_pic:
            logging.debug(
                "Loaded image URL for %s[%s]: %s", species, "footprint", footprint_pic
            )

        # Update Info-tab sprite thumbnails, constant label, and animated icon
        self._update_species_info_sprites(front_pic, icon_pic, species=species)

        # Update Graphics tab widget with sprites + per-species data
        try:
            if getattr(self, "graphics_tab_widget", None) is not None:
                self.graphics_tab_widget.load_species(
                    species,
                    front_path=front_pic or "",
                    back_path=back_pic or "",
                    icon_path=icon_pic or "",
                    footprint_path=footprint_pic or "",
                )
        except Exception:
            logging.exception("graphics_tab_widget.load_species failed")
        if hasattr(self, "_icon_timer"):
            self._set_icon_animation(icon_pic)

        # Update evolutions
        self.ui.evolutions.clear()
        evo_types = {}
        getter = getattr(self.source_data, "get_constant", None)
        if callable(getter):
            evo_types = getter("evolution_types") or {}
        evolutions = self.source_data.get_evolutions(species)
        if isinstance(evolutions, list):
            for evo in evolutions:
                method_const = evo.get("method")
                info = evo_types.get(method_const)
                if info is None:
                    info = self.source_data.get_constant_data(
                        "evolution_types", method_const
                    )
                if isinstance(info, dict):
                    method_name = info.get("name", str(method_const))
                elif info is not None:
                    method_name = str(info)
                else:
                    method_name = str(method_const)
                try:
                    item = QTreeWidgetItem(
                        [
                            evo.get("targetSpecies", ""),
                            method_name,
                            str(evo.get("param", "")),
                        ]
                    )
                except Exception:
                    try:
                        item = QTreeWidgetItem()
                    except Exception:
                        item = None
                if item is not None:
                    if hasattr(item, "setData"):
                        try:
                            item.setData(1, Qt.ItemDataRole.UserRole, method_const)
                        except Exception:
                            pass
                    try:
                        self.ui.evolutions.addTopLevelItem(item)
                    except Exception:
                        pass
        try:
            self.ui.evolutions.addTopLevelItem(QTreeWidgetItem(["Add New Evolution..."]))
        except Exception:
            try:
                self.ui.evolutions.addTopLevelItem(QTreeWidgetItem())
            except Exception:
                pass
        # Ensure the widgets reflect the newly loaded data
        if hasattr(self.ui.evolutions, "selectedItems"):
            self.update_evolutions()

        # Resize all columns to fit the contents
        for i in range(self.ui.evolutions.columnCount()):
            self.ui.evolutions.resizeColumnToContents(i)

        self.load_species_learnset_table(species)
        self.ui.tab_pokemon_data.setEnabled(True)

    def update_gender_ratio(self, value):
        """
        Update the gender ratio label based on the given value.

        Args:
            value (int): The value out of 255 representing the gender ratio.

        Returns:
            None
        """
        if value == 0:
            self.ui.gender_ratio_label.setText("Male Only")
        elif value == 254:
            self.ui.gender_ratio_label.setText("Female Only")
        elif value == 255:
            self.ui.gender_ratio_label.setText("Genderless")
        else:
            # Use 254 as the denominator so 254 maps to 100% (Female Only).
            # Reserve 255 for Genderless. This gives correct percent mapping
            # for UI display where the slider represents 0-100% female.
            percent = (value / 254) * 100
            self.ui.gender_ratio_label.setText(f"{percent:.1f}% Female")

    def save_species_data(self, species, form=None):
        """
        Saves the data for a specific species.

        Parameters:
        - species (str): The name of the species.
        - form (str, optional): The form of the species. Defaults to None.

        Returns:
        - bool: True if the data was updated, False otherwise.
        """
        updated = False

        def update_if_needed(attribute, ui_value):
            nonlocal updated
            if self.source_data.get_species_info(species, attribute, form) != ui_value:
                self.source_data.set_species_info(
                    species, attribute, ui_value, form=form
                )
                updated = True

        # Basic identity fields
        def _get_text(widget):
            for attr in ("text", "toPlainText"):
                try:
                    fn = getattr(widget, attr)
                except Exception:
                    continue
                try:
                    val = fn()
                    return val if isinstance(val, str) else str(val or "")
                except Exception:
                    continue
            return ""
        name_text = _get_text(self.ui.species_name).strip()
        update_if_needed("speciesName", name_text)
        category_text = _get_text(self.ui.species_category).strip()
        try:
            desc_text = self.ui.species_description.toPlainText() or ""
        except Exception:
            desc_text = _get_text(self.ui.species_description)
        # For categoryName and description, get_species_info falls back to the
        # Pokédex cache when species_info doesn't have the key.  That fallback
        # makes update_if_needed think "no change" even when the user edited the
        # field — because the UI was loaded from the same fallback.
        #
        # Fix: compare the UI value against the fallback.  If they differ the
        # user edited it → store in species_info so parse_to_c_code writes it
        # to the C header.  If they match but species_info already has a stale
        # value (from a previous edit), update that too.
        def _dex_aware_set(attr, val):
            nonlocal updated
            fallback = self.source_data.get_species_info(species, attr, form)
            try:
                raw = self.source_data.data["species_data"].data[species]["species_info"].get(attr)
            except Exception:
                raw = None
            if val != fallback:
                # User changed it — always write
                self.source_data.set_species_info(species, attr, val, form=form)
                updated = True
            elif raw is None and val:
                # Value matches the pokedex fallback but isn't stored in
                # species_info yet — write it so parse_to_c_code can
                # persist it to the header files.
                self.source_data.set_species_info(species, attr, val, form=form)
            elif raw is not None and raw != val:
                self.source_data.set_species_info(species, attr, val, form=form)
                updated = True
        _dex_aware_set("categoryName", category_text)
        _dex_aware_set("description", desc_text)

        # Sync category and description into the pokedex data structure
        # so the Pokedex tab shows the same values as the stats page.
        try:
            natdex = self.source_data.data["pokedex"].data.get("national_dex", [])
            for entry in natdex:
                if entry.get("species") == species:
                    if category_text:
                        entry["categoryName"] = category_text
                    if desc_text:
                        entry["descriptionText"] = desc_text
                    break
        except Exception:
            pass

        # Keep pokedex panel widgets in sync so _flush_pokedex_panel
        # doesn't clobber these values with stale widget text.
        if hasattr(self, "_pokedex_panel") and hasattr(self, "_current_dex_const"):
            try:
                nat_const = "NATIONAL_DEX_" + species[len("SPECIES_"):]
                if self._current_dex_const == nat_const:
                    if category_text:
                        self._pokedex_panel.f_category.setText(category_text)
                    if desc_text:
                        self._pokedex_panel.f_description.setPlainText(desc_text)
            except Exception:
                pass

        # Check and update base stats
        update_if_needed("baseHP", self.ui.base_hp.value())
        update_if_needed("baseAttack", self.ui.base_atk.value())
        update_if_needed("baseDefense", self.ui.base_def.value())
        update_if_needed("baseSpeed", self.ui.base_speed.value())
        update_if_needed("baseSpAttack", self.ui.base_spatk.value())
        update_if_needed("baseSpDefense", self.ui.base_spdef.value())

        # Check and update types
        types = [self.ui.type1.currentData(), self.ui.type2.currentData()]
        update_if_needed("types", types)

        # Check and update abilities
        # FireRed: Only two abilities are editable; preserve existing third slot
        # from source data (typically ABILITY_NONE).
        try:
            hidden_const = self.source_data.get_species_ability(species, 2, form)
        except Exception:
            hidden_const = "ABILITY_NONE"
        if not hidden_const:
            hidden_const = "ABILITY_NONE"
        try:
            hidden_id = str(self.source_data.get_ability(hidden_const)["id"])
        except Exception:
            hidden_id = "0"
        abilities = [
            str(self.source_data.get_ability(self.ui.ability1.currentData())["id"]),
            str(self.source_data.get_ability(self.ui.ability2.currentData())["id"]),
            hidden_id,
        ]
        update_if_needed("abilities", abilities)

        # Check and update EV yields
        update_if_needed("evYield_HP", self.ui.evs_hp.value())
        update_if_needed("evYield_Attack", self.ui.evs_atk.value())
        update_if_needed("evYield_Defense", self.ui.evs_def.value())
        update_if_needed("evYield_Speed", self.ui.evs_speed.value())
        update_if_needed("evYield_SpAttack", self.ui.evs_spatk.value())
        update_if_needed("evYield_SpDefense", self.ui.evs_spdef.value())

        # Check and update other attributes
        update_if_needed("catchRate", self.ui.catch_rate.value())
        update_if_needed("expYield", self.ui.exp_yield.value())
        # Determine genderRatio to save: if Genderless flag checked -> 255,
        # otherwise convert slider percent (0-100) -> engine byte (0-255)
        try:
            genderless_checked = False
            for i in range(self.ui.species_flags.count()):
                item = self.ui.species_flags.item(i)
                key = item.data(Qt.ItemDataRole.UserRole)
                if key == "GENDERLESS":
                    genderless_checked = item.checkState() == Qt.CheckState.Checked
                    break
        except Exception:
            genderless_checked = False

        if genderless_checked:
            gr_val = 255
        else:
            try:
                pct = int(self.ui.gender_ratio.value())
            except Exception:
                pct = 0
            # Map UI percent 0-100 -> engine byte 0-254. Reserve 255 for Genderless.
            gr_val = int(round(pct * 254 / 100))
        try:
            cur = self.source_data.get_species_info(species, "genderRatio", form)
        except Exception:
            cur = None
        try:
            print(f"[GENDER-DIAG] save_species_data: species={species} computed_gr={gr_val} current_gr={cur}")
        except Exception:
            pass
        update_if_needed("genderRatio", gr_val)
        # Ensure underlying data dictionary also reflects the value so
        # JSON serialization does not miss it if set_species_info wrappers
        # behave differently. This is a defensive direct write.
        try:
            dd = getattr(self.source_data, 'data', None)
            if isinstance(dd, dict) and species in dd:
                si = dd[species].get('species_info')
                if isinstance(si, dict):
                    si['genderRatio'] = gr_val
        except Exception:
            pass
        update_if_needed("itemCommon", self.ui.held_item_common.currentData())
        update_if_needed("itemRare", self.ui.held_item_rare.currentData())
        update_if_needed("eggCycles", self.ui.egg_cycles.value())

        egg_groups = [
            self.ui.egg_group_1.currentData(),
            self.ui.egg_group_2.currentData(),
        ]
        update_if_needed("eggGroups", egg_groups)

        update_if_needed("growthRate", self.ui.exp_growth_rate.currentData())
        update_if_needed("friendship", self.ui.base_friendship.value())
                # Species flags: collect checked items and persist into concrete fields (in-memory only)
        if hasattr(self.ui, "species_flags"):
            try:
                # Update concrete fields from flag checkboxes
                try:
                    starters_list = self.source_data.get_pokemon_starters() if hasattr(self.source_data, 'get_pokemon_starters') else []
                except Exception:
                    starters_list = []
                for i in range(self.ui.species_flags.count()):
                    item = self.ui.species_flags.item(i)
                    try:
                        key = item.data(Qt.ItemDataRole.UserRole)
                    except Exception:
                        key = None
                    checked = item.checkState() == Qt.CheckState.Checked
                    try:
                        if key == "NO_FLIP":
                            val = "TRUE" if checked else "FALSE"
                            update_if_needed("noFlip", val)
                        elif key == "GENDERLESS":
                            # If checked, explicitly set genderless; if unchecked
                            # do not force male-only — the slider is authoritative
                            if checked:
                                update_if_needed("genderRatio", 255)
                        elif key in ("UNBREEDABLE",):
                            if checked:
                                update_if_needed("eggGroups", ["EGG_GROUP_UNDISCOVERED"])
                            else:
                                orig = item.data(1000)
                                if isinstance(orig, list):
                                    update_if_needed("eggGroups", orig)
                        elif key == "STARTER":
                            sd = getattr(self, 'source_data', None)
                            if sd and isinstance(getattr(sd, 'data', None), dict):
                                sl = sd.data.get('starters') or []
                                if checked and species not in sl:
                                    sl.append(species)
                                    sd.data['starters'] = sl
                                    sd.pending_changes = True
                                    updated = True
                                if (not checked) and species in sl:
                                    sl = [s for s in sl if s != species]
                                    sd.data['starters'] = sl
                                    sd.pending_changes = True
                                    updated = True
                            else:
                                item.setData(1001, checked)
                        elif key in ("IN_NATDEX", "IN_REGDEX"):
                            pd = getattr(self.source_data, 'data', {}).get('pokedex')
                            pdata = getattr(pd, 'data', {}) if pd else {}
                            if not isinstance(pdata, dict):
                                continue
                            # National Dex membership
                            if key == "IN_NATDEX":
                                lst = list(pdata.get('national_dex', []))
                                present = False
                                for entry in lst:
                                    if isinstance(entry, dict) and entry.get('species') == species:
                                        present = True
                                        if not checked:
                                            lst = [e for e in lst if e is not entry]
                                        break
                                if checked and not present:
                                    const = self.source_data.get_species_data(species, 'dex_constant')
                                    # minimal entry; update_save will normalize order/fields
                                    lst.append({
                                        'dex_num': len(lst) + 1,
                                        'species': species,
                                        'dex_constant': const,
                                        'categoryName': self.source_data.get_species_info(species, 'categoryName') or '',
                                    })
                                    # Also reflect in the UI list if not present
                                    try:
                                        # Add display text "Name - Category"
                                        name = self.source_data.get_species_data(species, 'name') or species
                                        cat = self.source_data.get_species_info(species, 'categoryName') or ''
                                        text = name if not cat else f"{name} - {cat}"
                                        it = QListWidgetItem(text)
                                        it.setData(Qt.ItemDataRole.UserRole, const)
                                        self.ui.list_pokedex_national.addItem(it)
                                    except Exception:
                                        pass
                                if not checked and present:
                                    # Remove from the UI list as well
                                    try:
                                        const = self.source_data.get_species_data(species, 'dex_constant')
                                        for i in range(self.ui.list_pokedex_national.count() - 1, -1, -1):
                                            it = self.ui.list_pokedex_national.item(i)
                                            if it.data(Qt.ItemDataRole.UserRole) == const:
                                                self.ui.list_pokedex_national.takeItem(i)
                                                break
                                    except Exception:
                                        pass
                                pdata['national_dex'] = lst
                                updated = True
                            # Regional Dex membership
                            if key == "IN_REGDEX":
                                lst = list(pdata.get('regional_dex', []))
                                nat = self.source_data.get_species_data(species, 'dex_constant')
                                reg = nat  # store national dex constants for regional list
                                present = False
                                for entry in lst:
                                    const = entry.get('dex_constant') if isinstance(entry, dict) else entry
                                    if const == reg:
                                        present = True
                                        if not checked:
                                            lst = [e for e in lst if (e.get('dex_constant') if isinstance(e, dict) else e) != reg]
                                        break
                                if checked and not present and reg:
                                    lst.append({'dex_constant': reg})
                                    # Reflect in the UI regional list
                                    try:
                                        name = self.source_data.get_species_data(species, 'name') or species
                                        cat = self.source_data.get_species_info(species, 'categoryName') or ''
                                        text = name if not cat else f"{name} - {cat}"
                                        it = QListWidgetItem(text)
                                        it.setData(Qt.ItemDataRole.UserRole, reg)
                                        self.ui.list_pokedex_regional.addItem(it)
                                    except Exception:
                                        pass
                                if not checked and present and reg:
                                    # Remove from the UI list
                                    try:
                                        for i in range(self.ui.list_pokedex_regional.count() - 1, -1, -1):
                                            it = self.ui.list_pokedex_regional.item(i)
                                            if it.data(Qt.ItemDataRole.UserRole) == reg:
                                                self.ui.list_pokedex_regional.takeItem(i)
                                                break
                                    except Exception:
                                        pass
                                pdata['regional_dex'] = lst
                                updated = True
                        else:
                            pass
                    except Exception:
                        pass
            except Exception:
                pass
        # Collect evolutions from the tree widget
        evolutions: list[dict] = []
        evo_types = {}
        getter = getattr(self.source_data, "get_constant", None)
        if callable(getter):
            evo_types = getter("evolution_types") or {}
        for i in range(self.ui.evolutions.topLevelItemCount()):
            item = self.ui.evolutions.topLevelItem(i)
            if item.text(0) == "Add New Evolution...":
                continue
            method_name = item.text(1)
            method_const = None
            for const, info in evo_types.items():
                name = info.get("name") if isinstance(info, dict) else info
                if name == method_name:
                    method_const = const
                    break
            param_txt = item.text(2)
            try:
                param_val = int(param_txt)
            except ValueError:
                param_val = param_txt if param_txt else None
            evolutions.append(
                {
                    "targetSpecies": item.text(0),
                    "method": method_const or method_name,
                    "param": param_val,
                }
            )
        current = self.source_data.get_evolutions(species)
        if evolutions != current:
            updated = True
            self.source_data.set_evolutions(species, evolutions)
        if self.save_species_learnset_table(species):
            updated = True

        if updated:
            # Do not write changes to disk automatically while browsing.
            # Mark in-memory data as modified and mark window modified so the user is prompted to save.
            try:
                self.source_data.pending_changes = True
            except Exception:
                pass
            try:
                self.setWindowModified(True)
            except Exception:
                pass

        return updated

    def update(self):
        origin = self.sender()
        if origin == self.ui.gender_ratio:
            # Slider stores percent 0-100; convert to engine byte 0-254 (255 reserved)
            try:
                pct = int(self.ui.gender_ratio.value())
            except Exception:
                pct = 0
            eng = int(round(pct * 254 / 100))
            self.update_gender_ratio(eng)
            # Persist slider changes immediately into in-memory species data
            try:
                species = getattr(self, 'previous_selected_species', None)
                if species:
                    self.source_data.set_species_info(species, 'genderRatio', eng)
                    try:
                        self.source_data.pending_changes = True
                    except Exception:
                        pass
                    try:
                        self.setWindowModified(True)
                    except Exception:
                        pass
            except Exception:
                pass

    def on_species_flag_changed(self, item):
        """Handle immediate changes when a species flag checkbox is toggled.

        We only mirror the GENDERLESS flag into `genderRatio` immediately so the
        editor reflects state and Save persists it. The change is marked as
        pending so the user must Save to persist to disk.
        """
        try:
            key = item.data(Qt.ItemDataRole.UserRole)
        except Exception:
            key = None
        if key != "GENDERLESS":
            return
        species = getattr(self, 'previous_selected_species', None)
        if not species:
            return
        checked = item.checkState() == Qt.CheckState.Checked
        if checked:
            # Mark genderless in-memory and disable slider
            try:
                self.source_data.set_species_info(species, 'genderRatio', 255)
                try:
                    self.source_data.pending_changes = True
                except Exception:
                    pass
            except Exception:
                pass
            try:
                self.ui.gender_ratio.setEnabled(False)
                self.update_gender_ratio(255)
            except Exception:
                pass
        else:
            # Unset genderless: restore slider and numeric value from data (or default)
            try:
                val = self.source_data.get_species_info(species, 'genderRatio')
            except Exception:
                val = None
            if isinstance(val, int) and val != 255:
                percent = int(round(val * 100 / 254))
            else:
                percent = 50
            try:
                self.ui.gender_ratio.setEnabled(True)
                self.ui.gender_ratio.setValue(percent)
                eng = int(round(percent * 254 / 100))
                self.source_data.set_species_info(species, 'genderRatio', eng)
                try:
                    self.source_data.pending_changes = True
                except Exception:
                    pass
                self.update_gender_ratio(eng)
                try:
                    print(f"[GENDER-DIAG] label after update (flag toggle): {self.ui.gender_ratio_label.text()}")
                except Exception:
                    pass
            except Exception:
                pass
        try:
            self.setWindowModified(True)
        except Exception:
            pass

    def update_tree_pokemon(self):
        """
        Updates the tree view with the selected Pokemon's data.

        If a single species is selected, it saves the data of the previously selected species (if it exists),
        adds "*" to the displayed name of the previously selected species, and updates the data of the selected Pokemon.
        If a form of a species is selected, it saves the data of the base species, updates the data of the base species
        with the selected form, and updates the tree view with the data of the base species.
        """
        if getattr(self, "_is_updating_selection", False):
            return
        self._is_updating_selection = True
        try:
            selected_species = self.ui.tree_pokemon.selectedItems()
            if len(selected_species) != 1:
                return
            # Save previously selected species if any
            if self.previous_selected_species is not None:
                updated = self.save_species_data(
                    self.previous_selected_species, form=self.previous_selected_form
                )
                # Do NOT persist to disk here. Only mark in-memory changes and
                # set the window modified flag so the user can Save explicitly.
                if updated:
                    # Mark previous species with '*'
                    for i in range(self.ui.tree_pokemon.topLevelItemCount()):
                        item = self.ui.tree_pokemon.topLevelItem(i)
                        if item.text(1) == self.previous_selected_species:
                            if not item.text(0).endswith("*"):
                                item.setText(0, item.text(0) + "*")
                                self.setWindowModified(True)
                            break

            # Determine selected species/form
            pokemon = selected_species[0].text(1)
            if pokemon in self.source_data.get_pokemon_data():
                self.previous_selected_species = pokemon
                self.previous_selected_form = None
                self.update_data(pokemon)
                self._select_pokedex_item(pokemon)
                self._refresh_pokedex_display(pokemon)
            else:
                base_species = selected_species[0].text(2)
                self.previous_selected_species = base_species
                self.previous_selected_form = pokemon
                self.update_data(base_species, pokemon)
                self._select_pokedex_item(base_species)
                self._refresh_pokedex_display(base_species)

            # Note: update_data() was already called above for the newly
            # selected species, so we do NOT call refresh_current_species()
            # here — that would redundantly reload the same data.
        finally:
            self._is_updating_selection = False

    def _select_species_in_tree(self, species):
        """Select the pokemon tree item corresponding to ``species``."""
        if not species:
            return
        tree = self.ui.tree_pokemon
        for i in range(tree.topLevelItemCount()):
            item = tree.topLevelItem(i)
            if item.text(1) == species:
                from PyQt6.QtCore import QSignalBlocker
                with QSignalBlocker(tree):
                    tree.setCurrentItem(item)
                break

    def _select_pokedex_item(self, species):
        """Select Pokédex list items corresponding to ``species``.

        The species' ``dex_constant`` is looked up from ``source_data``. If it
        isn't found directly, the fallback ``species_by_dex`` mapping is used.
        The resulting ``NATIONAL_DEX_*`` constant is converted to its
        ``HOENN_DEX_*`` counterpart, matching the value stored in each list
        widget. Both the national and regional Pokédex lists are scanned and the
        first item whose ``UserRole`` data matches the constant is selected.
        """
        if not self.source_data or not species:
            return

        const = self.source_data.get_species_data(species, "dex_constant")
        if not const:
            # fallback via pokedex mapping
            for nat, sp in getattr(self.source_data, "species_by_dex", {}).items():
                if sp == species:
                    const = nat
                    break
        if not const:
            return

        dex_const_nat = const
        dex_const_reg = const.replace("NATIONAL_DEX_", "HOENN_DEX_")
        for lst in (self.ui.list_pokedex_national, self.ui.list_pokedex_regional):
            if not hasattr(lst, "count"):
                continue
            for i in range(lst.count()):
                item = lst.item(i)
                data = item.data(Qt.ItemDataRole.UserRole)
                if data == dex_const_reg or data == dex_const_nat:
                    # Avoid re-entrant selection updates
                    try:
                        from PyQt6.QtCore import QSignalBlocker
                        with QSignalBlocker(lst):
                            if hasattr(lst, "setCurrentItem"):
                                lst.setCurrentItem(item)
                            else:
                                lst.setSelected(item)
                    except Exception:
                        if hasattr(lst, "setCurrentItem"):
                            lst.setCurrentItem(item)
                        else:
                            lst.setSelected(item)
                    break

    def _refresh_pokedex_display(self, species):
        """Update the Pokédex panel fields for *species* without triggering
        any selection changes, saves, or data reloads.  Used by
        ``update_tree_pokemon`` to keep the dex panel in sync cheaply."""
        if not species or not self.source_data:
            return

        # Resolve the national dex constant for this species
        dex_const = self.source_data.get_species_data(species, "dex_constant")
        if not dex_const:
            for nat, sp in getattr(self.source_data, "species_by_dex", {}).items():
                if sp == species:
                    dex_const = nat
                    break
        nat_const = (dex_const or "").replace("HOENN_DEX_", "NATIONAL_DEX_")

        # Name / dex number
        name = self.source_data.get_species_data(species, "name")
        if name is not None:
            self.ui.species_name.setText(name)
        dex_num = self.source_data.get_species_data(species, "dex_num")
        if isinstance(dex_num, int):
            self.ui.dex_num.setText(f"{dex_num:0>4}")

        # Category
        sp_cat = self.source_data.get_species_info(species, "categoryName")
        if isinstance(sp_cat, str) and sp_cat.strip():
            self.ui.species_category.setText(sp_cat)
        else:
            entry = {}
            try:
                for d in self.source_data.data["pokedex"].data.get("national_dex", []):
                    if d.get("dex_constant") == nat_const:
                        entry = d
                        break
            except Exception:
                pass
            cat = entry.get("categoryName")
            if cat is not None:
                self.ui.species_category.setText(cat)

        # Description
        sp_text = self.source_data.get_species_info(species, "description")
        if isinstance(sp_text, str) and sp_text != "":
            self.ui.species_description.setPlainText(sp_text)
        else:
            entry = {}
            try:
                for d in self.source_data.data["pokedex"].data.get("national_dex", []):
                    if d.get("dex_constant") == nat_const:
                        entry = d
                        break
            except Exception:
                pass
            text = entry.get("descriptionText")
            if text is None and entry.get("description"):
                text = (
                    self.source_data.data["pokedex"]
                    .data.get("pokedex_text", {})
                    .get(entry["description"], "")
                )
            self.ui.species_description.setPlainText(text or "")

        # Detail panel
        if hasattr(self, "_pokedex_panel"):
            pokedex_entry: dict = {}
            try:
                for d in self.source_data.data["pokedex"].data.get("national_dex", []):
                    if d.get("dex_constant") == nat_const:
                        pokedex_entry = d
                        break
            except Exception:
                pass
            species_name = self.source_data.get_species_data(species, "name") or ""
            sprite_path = self.source_data.get_species_image_path(species, "frontPic")
            self._pokedex_panel.load_entry(
                pokedex_entry,
                species_name=species_name,
                sprite_path=sprite_path,
            )
            self._current_dex_const = nat_const

    def update_pokedex_entry(self):
        """Update displayed Pokédex info when a dex entry is selected."""
        if getattr(self, "_is_updating_selection", False):
            return
        self._is_updating_selection = True
        try:
            item = None
            origin = self.sender()
            if origin == self.ui.list_pokedex_national:
                selected = self.ui.list_pokedex_national.selectedItems()
                if selected:
                    item = selected[0]
            elif origin == self.ui.list_pokedex_regional:
                selected = self.ui.list_pokedex_regional.selectedItems()
                if selected:
                    item = selected[0]
            else:
                selected = (
                    self.ui.list_pokedex_national.selectedItems()
                    or self.ui.list_pokedex_regional.selectedItems()
                )
                if selected:
                    item = selected[0]

            if not item or not self.source_data:
                return

            dex_const = item.data(Qt.ItemDataRole.UserRole)
            nat_const = dex_const.replace("HOENN_DEX_", "NATIONAL_DEX_")
            species = self.source_data.get_species_by_dex_constant(nat_const)
            if not species:
                return

            # Sync pokemon tree to match pokedex selection
            self._select_species_in_tree(species)

            # Keep previous_selected_species in sync so that switching back
            # to the pokemon tab doesn't save the wrong species' data.
            if self.previous_selected_species and self.previous_selected_species != species:
                self.save_species_data(
                    self.previous_selected_species, form=self.previous_selected_form
                )
            self.previous_selected_species = species
            self.previous_selected_form = None

            # Flush edits for the previously selected entry before loading new one
            self._flush_pokedex_panel()

            # Refresh all displayed data for the selected species
            self.update_data(species)

            # Basic species info
            name = self.source_data.get_species_data(species, "name")
            if name is not None:
                self.ui.species_name.setText(name)

            dex_num = self.source_data.get_species_data(species, "dex_num")
            if isinstance(dex_num, int):
                self.ui.dex_num.setText(f"{dex_num:0>4}")

            # Prefer species_info values when present; fall back to Pokédex cache
            # to avoid UI reverting after a Save that updates headers/JSONs.
            # Category
            sp_cat = self.source_data.get_species_info(species, "categoryName")
            if isinstance(sp_cat, str) and sp_cat.strip():
                self.ui.species_category.setText(sp_cat)
            else:
                entry = None
                for d in self.source_data.data["pokedex"].data.get("national_dex", []):
                    if d.get("dex_constant") == nat_const:
                        entry = d
                        break
                if not entry:
                    entry = {}
                category = entry.get("categoryName")
                if category is not None:
                    self.ui.species_category.setText(category)

            # Description
            sp_text = self.source_data.get_species_info(species, "description")
            if isinstance(sp_text, str) and sp_text != "":
                self.ui.species_description.setPlainText(sp_text)
            else:
                # Use cached Pokédex entry text if available
                entry = None
                for d in self.source_data.data["pokedex"].data.get("national_dex", []):
                    if d.get("dex_constant") == nat_const:
                        entry = d
                        break
                if not entry:
                    entry = {}
                text = entry.get("descriptionText")
                if text is None and entry.get("description"):
                    text = (
                        self.source_data.data["pokedex"]
                        .data.get("pokedex_text", {})
                        .get(entry["description"], "")
                    )
                self.ui.species_description.setPlainText(text or "")

            # ── Populate the detail panel ──────────────────────────────────
            if hasattr(self, "_pokedex_panel"):
                # Find the full entry dict from national_dex
                pokedex_entry: dict = {}
                try:
                    for d in self.source_data.data["pokedex"].data.get(
                        "national_dex", []
                    ):
                        if d.get("dex_constant") == nat_const:
                            pokedex_entry = d
                            break
                except Exception:
                    pass

                species_name = self.source_data.get_species_data(species, "name") or ""
                sprite_path  = self.source_data.get_species_image_path(
                    species, "frontPic"
                )
                self._pokedex_panel.load_entry(
                    pokedex_entry,
                    species_name=species_name,
                    sprite_path=sprite_path,
                )
                self._current_dex_const = nat_const

        finally:
            self._is_updating_selection = False

    def update_gender_ratio_minus1(self):
        """
        Decreases the gender ratio value by 1 and updates the gender ratio.

        This method retrieves the current value of the gender ratio from the UI,
        substracts 1 from it, and sets the updated value back to the UI. It then calls
        the `update_gender_ratio` method to update the gender ratio label.
        """
        value = max(0, self.ui.gender_ratio.value() - 1)
        self.ui.gender_ratio.setValue(value)
        # convert percent -> engine and update label
        eng = int(round(value * 254 / 100))
        self.update_gender_ratio(eng)
        self.setWindowModified(True)

    def update_gender_ratio_plus1(self):
        """
        Increases the gender ratio value by 1 and updates the gender ratio.

        This method retrieves the current value of the gender ratio from the UI,
        adds 1 to it, and sets the updated value back to the UI. It then calls
        the `update_gender_ratio` method to update the gender ratio label.
        """
        value = min(100, self.ui.gender_ratio.value() + 1)
        self.ui.gender_ratio.setValue(value)
        eng = int(round(value * 254 / 100))
        self.update_gender_ratio(eng)
        self.setWindowModified(True)

    def update_evolutions(self):
        """
        Sync the evolution widgets with the currently selected tree item.
        """
        selected = self.ui.evolutions.selectedItems()
        self.ui.pushButton_7.setEnabled(False)
        if len(selected) != 1:
            self.ui.evo_species.setEnabled(False)
            self.ui.evo_method.setEnabled(False)
            self.ui.evo_param.setEnabled(False)
            with QSignalBlocker(self.ui.evo_species), QSignalBlocker(
                self.ui.evo_method
            ), QSignalBlocker(self.ui.evo_param):
                self.ui.evo_species.setCurrentIndex(0)
                self.ui.evo_method.setCurrentIndex(0)
                self.ui.evo_param.setCurrentIndex(0)
            self.ui.evoDeleteButton.setEnabled(False)
            return

        item = selected[0]
        is_add = item.text(0) == "Add New Evolution..."
        self.ui.evo_species.setEnabled(True)
        self.ui.evo_method.setEnabled(True)
        self.ui.evo_param.setEnabled(not is_add)
        self.ui.evoDeleteButton.setEnabled(not is_add)
        self.ui.pushButton_7.setEnabled(is_add)

        with QSignalBlocker(self.ui.evo_species), QSignalBlocker(
            self.ui.evo_method
        ):
            if is_add:
                self.ui.evo_species.setCurrentIndex(0)
                self.ui.evo_method.setCurrentIndex(0)
            else:
                self.ui.evo_species.setCurrentIndex(
                    self.ui.evo_species.findText(item.text(0))
                )
                const_val = (
                    item.data(1, Qt.ItemDataRole.UserRole)
                    if hasattr(item, "data")
                    else None
                )
                self.ui.evo_method.setCurrentIndex(
                    self.ui.evo_method.findData(const_val)
                )
        self.refresh_evo_param_choices()
        with QSignalBlocker(self.ui.evo_param):
            if is_add:
                self.ui.evo_param.setCurrentIndex(0)
            elif not self.ui.evo_param.isEnabled():
                # No-param method (friendship/trade) — leave as-is
                pass
            elif self.ui.evo_param.isEditable():
                # Level / beauty / numeric — paste the raw text value
                self.ui.evo_param.setEditText(item.text(2))
            else:
                # Item dropdown — locate by stored item constant
                param_const = item.text(2)
                idx = self.ui.evo_param.findData(param_const)
                if idx == -1:
                    # Fallback: search by display text
                    idx = self.ui.evo_param.findText(param_const)
                if idx != -1:
                    self.ui.evo_param.setCurrentIndex(idx)

    # ── Evolution method reference panel ────────────────────────────────────

    _EVO_METHOD_DESCRIPTIONS: dict[str, tuple[str, str]] = {
        "EVO_FRIENDSHIP": (
            "Friendship",
            "Evolves when leveled up with high friendship (220+). "
            "Works any time of day. Parameter is ignored.",
        ),
        "EVO_FRIENDSHIP_DAY": (
            "Friendship (Day)",
            "Evolves when leveled up with high friendship (220+) "
            "during the daytime only. Parameter is ignored.\n\n"
            "Note: FireRed has no day/night cycle by default, "
            "so this behaves the same as regular Friendship "
            "unless a day/night system has been added.",
        ),
        "EVO_FRIENDSHIP_NIGHT": (
            "Friendship (Night)",
            "Evolves when leveled up with high friendship (220+) "
            "during nighttime only. Parameter is ignored.\n\n"
            "Note: FireRed has no day/night cycle by default, "
            "so this will NEVER trigger unless a day/night "
            "system has been added.",
        ),
        "EVO_LEVEL": (
            "Level Up",
            "Evolves when the Pokémon reaches the specified level. "
            "Parameter = the minimum level required.\n\n"
            "Example: Set to 16 and the Pokémon evolves when it "
            "hits level 16 or above.",
        ),
        "EVO_TRADE": (
            "Trade",
            "Evolves when traded to another player. No item needed. "
            "Parameter is ignored.\n\n"
            "The evolution happens immediately when the trade completes.",
        ),
        "EVO_TRADE_ITEM": (
            "Trade w/ Item",
            "Evolves when traded while HOLDING a specific item. "
            "Parameter = the held item required.\n\n"
            "The item is consumed during the evolution. "
            "Example: Onix holding Metal Coat → Steelix.",
        ),
        "EVO_ITEM": (
            "Use Item",
            "Evolves when a specific item is USED ON the Pokémon "
            "from the Bag (like a stone). Parameter = the item.\n\n"
            "This is NOT 'hold the item and level up' — the player "
            "must open the Bag, select the item, and use it directly "
            "on the Pokémon.\n\n"
            "Example: Use a Moon Stone on Nidorina → Nidoqueen.",
        ),
        "EVO_LEVEL_ATK_GT_DEF": (
            "Level (Atk > Def)",
            "Evolves at the specified level, but ONLY if the Pokémon's "
            "Attack stat is higher than its Defense stat at that moment.\n\n"
            "Used by: Tyrogue → Hitmonlee.",
        ),
        "EVO_LEVEL_ATK_EQ_DEF": (
            "Level (Atk = Def)",
            "Evolves at the specified level, but ONLY if the Pokémon's "
            "Attack stat equals its Defense stat at that moment.\n\n"
            "Used by: Tyrogue → Hitmontop.",
        ),
        "EVO_LEVEL_ATK_LT_DEF": (
            "Level (Atk < Def)",
            "Evolves at the specified level, but ONLY if the Pokémon's "
            "Defense stat is higher than its Attack stat at that moment.\n\n"
            "Used by: Tyrogue → Hitmonchan.",
        ),
        "EVO_LEVEL_SILCOON": (
            "Level (Silcoon PV)",
            "Evolves at the specified level based on the Pokémon's "
            "hidden personality value (PV). This is a coin-flip "
            "the game decides when the Pokémon is generated.\n\n"
            "Used by: Wurmple → Silcoon (50% chance at level 7).",
        ),
        "EVO_LEVEL_CASCOON": (
            "Level (Cascoon PV)",
            "Evolves at the specified level based on the Pokémon's "
            "hidden personality value (PV) — the opposite coin-flip "
            "from the Silcoon path.\n\n"
            "Used by: Wurmple → Cascoon (50% chance at level 7).",
        ),
        "EVO_LEVEL_NINJASK": (
            "Level (Ninjask)",
            "Normal level-up evolution. Parameter = level.\n\n"
            "Used by: Nincada → Ninjask at level 20. "
            "If the player also has an empty party slot AND a "
            "spare Poké Ball, a Shedinja appears too.",
        ),
        "EVO_LEVEL_SHEDINJA": (
            "Level (Shedinja)",
            "Special: this evolution does NOT happen on its own. "
            "When Nincada evolves into Ninjask, the game checks "
            "for an empty party slot + spare Poké Ball and creates "
            "a Shedinja automatically.\n\n"
            "You generally don't need to use this method manually.",
        ),
        "EVO_BEAUTY": (
            "Beauty",
            "Evolves when leveled up with a Beauty contest stat "
            "at or above the specified value.\n\n"
            "Used by: Feebas → Milotic (Beauty ≥ 170).\n\n"
            "Note: FireRed has no contest stats by default. "
            "This method won't trigger unless contest stats "
            "have been implemented.",
        ),
    }

    def _setup_evo_reference_panel(self):
        """Add a scrollable method-reference column to the right side of the evolutions tab."""
        from PyQt6.QtWidgets import QScrollArea, QLabel, QFrame, QVBoxLayout, QWidget
        from PyQt6.QtCore import Qt

        ref_scroll = QScrollArea()
        ref_scroll.setWidgetResizable(True)
        ref_scroll.setFrameShape(QFrame.Shape.NoFrame)
        ref_scroll.setMinimumWidth(220)
        ref_scroll.setMaximumWidth(280)

        inner = QWidget()
        lay = QVBoxLayout(inner)
        lay.setContentsMargins(8, 8, 8, 4)
        lay.setSpacing(12)

        title = QLabel("Method Reference")
        title.setStyleSheet(
            "font-size: 13px; font-weight: bold; color: #e0e0e0; "
            "border-bottom: 1px solid #444; padding-bottom: 4px;"
        )
        lay.addWidget(title)

        for const, (short_name, desc) in self._EVO_METHOD_DESCRIPTIONS.items():
            name_lbl = QLabel(short_name)
            name_lbl.setStyleSheet(
                "font-weight: bold; font-size: 11px; color: #58a6ff; margin-top: 2px;"
            )
            name_lbl.setWordWrap(True)
            lay.addWidget(name_lbl)

            const_lbl = QLabel(const)
            const_lbl.setStyleSheet(
                "font-family: 'Courier New'; font-size: 9px; color: #666;"
            )
            lay.addWidget(const_lbl)

            desc_lbl = QLabel(desc)
            desc_lbl.setStyleSheet("font-size: 10px; color: #aaa;")
            desc_lbl.setWordWrap(True)
            lay.addWidget(desc_lbl)

        lay.addStretch()
        ref_scroll.setWidget(inner)

        # Insert into the evolutions tab grid at column 1
        self.ui.tab_pokemon_evos_grid.addWidget(ref_scroll, 0, 1, 1, 1)

    # Evolution methods whose param is an item constant → show item dropdown
    _EVO_ITEM_METHODS = frozenset({
        "EVO_ITEM", "EVO_TRADE_ITEM",
    })
    # Evolution methods that need no meaningful param → disable field
    _EVO_NO_PARAM_METHODS = frozenset({
        "EVO_FRIENDSHIP", "EVO_FRIENDSHIP_DAY", "EVO_FRIENDSHIP_NIGHT", "EVO_TRADE",
    })

    def refresh_evo_param_choices(self, *_):
        """Update ``evo_param`` items/mode based on the selected method constant."""
        method_const = self.ui.evo_method.currentData()  # e.g. "EVO_ITEM", "EVO_LEVEL", None

        items_data: dict = {}
        if self.source_data:
            try:
                items_data = getattr(self.source_data, "get_pokemon_items", lambda: {})() or {}
            except Exception:
                items_data = {}

        # Snapshot the current param value so we can restore it after repopulation.
        # Block signals while repopulating so that clearing/adding items doesn't
        # fire edit_evolution and overwrite the tree item with stale values.
        old_data = self.ui.evo_param.currentData()
        old_text = self.ui.evo_param.currentText()

        self.ui.evo_param.blockSignals(True)

        if method_const in self._EVO_ITEM_METHODS:
            # Item dropdown – non-editable combo
            self.ui.evo_param.setEditable(False)
            self.ui.evo_param.setEnabled(True)
            self.ui.evo_param.clear()
            for const_key, info in sorted(items_data.items()):
                name = info.get("english") or info.get("name") or const_key
                self.ui.evo_param.addItem(name, const_key)
            # Try to restore the previously selected item constant
            restore_key = old_data if old_data else old_text
            if restore_key:
                idx = self.ui.evo_param.findData(restore_key)
                if idx == -1:
                    idx = self.ui.evo_param.findData(str(restore_key))
                if idx != -1:
                    self.ui.evo_param.setCurrentIndex(idx)

        elif method_const in self._EVO_NO_PARAM_METHODS:
            # No meaningful parameter for this method
            self.ui.evo_param.setEditable(False)
            self.ui.evo_param.clear()
            self.ui.evo_param.addItem("N/A", 0)
            self.ui.evo_param.setCurrentIndex(0)
            self.ui.evo_param.setEnabled(False)

        else:
            # Level, beauty, or unknown – plain numeric/text editable field
            self.ui.evo_param.setEnabled(True)
            self.ui.evo_param.setEditable(True)
            self.ui.evo_param.clear()
            if old_text:
                self.ui.evo_param.setEditText(old_text)

        self.ui.evo_param.blockSignals(False)

    def edit_evolution(self, *_):
        """Update the selected tree item when any evolution field changes."""
        selected = self.ui.evolutions.selectedItems()
        if len(selected) != 1:
            return
        item = selected[0]
        if item.text(0) == "Add New Evolution...":
            return
        item.setText(0, self.ui.evo_species.currentText())
        item.setText(1, self.ui.evo_method.currentText())
        if hasattr(item, "setData"):
            item.setData(1, Qt.ItemDataRole.UserRole, self.ui.evo_method.currentData())
        param = self._read_evo_param()
        item.setText(2, str(param))
        self.setWindowModified(True)

    def _read_evo_param(self):
        """Return the current evo_param value for saving to the tree/data."""
        # Use the method constant directly so we don't depend on widget enabled-state
        # (which can lag behind signal handlers in test environments).
        method_const = self.ui.evo_method.currentData()
        if method_const in self._EVO_NO_PARAM_METHODS:
            return 0
        if not self.ui.evo_param.isEditable():
            # Item dropdown → return the stored item constant
            return self.ui.evo_param.currentData() or ""
        return self.ui.evo_param.currentText()

    def add_evolution(self):
        """Insert a new evolution row above the placeholder entry."""
        param = self._read_evo_param()
        new_item = QTreeWidgetItem(
            [
                self.ui.evo_species.currentText(),
                self.ui.evo_method.currentText(),
                str(param),
            ]
        )
        if hasattr(new_item, "setData"):
            new_item.setData(
                1, Qt.ItemDataRole.UserRole, self.ui.evo_method.currentData()
            )
        count = self.ui.evolutions.topLevelItemCount()
        if count:
            self.ui.evolutions.insertTopLevelItem(count - 1, new_item)
        else:
            self.ui.evolutions.addTopLevelItem(new_item)
        self.ui.evolutions.setCurrentItem(new_item)
        self.setWindowModified(True)

    def delete_evolution(self):
        """Remove the selected evolution from the tree."""
        items = self.ui.evolutions.selectedItems()
        for item in items:
            index = self.ui.evolutions.indexOfTopLevelItem(item)
            if index != -1:
                self.ui.evolutions.takeTopLevelItem(index)
        self.update_evolutions()
        self.setWindowModified(True)

    def refresh_current_species(self, *_):
        """Reload the data for the currently selected species/form."""
        try:
            items = self.ui.tree_pokemon.selectedItems()
        except AttributeError:
            items = []

        if items:
            item = items[0]
            species = item.text(1)
            form = None
            if self.source_data and species not in self.source_data.get_pokemon_data():
                form = species
                species = item.text(2)
            self.update_data(species, form)
        elif self.previous_selected_species is not None:
            self.update_data(
                self.previous_selected_species, self.previous_selected_form
            )

    def update_main_tabs(self):
        """
        Updates source data based on the previous main tab.
        """
        try:
            if self.previous_main_tab == 0:  # Pokedex
                self._flush_pokedex_panel()
            elif self.previous_main_tab == 1:  # Pokemon
                learnset_index = getattr(self, "learnset_tab_index", self.moves_tab_index)
                if self.previous_pokemon_tab == learnset_index:
                    self.save_species_learnset_table()
                # Skip if update_save already captured species data before
                # processEvents could clobber the widgets.
                if getattr(self, "_species_already_captured", False):
                    pass
                elif self.previous_selected_species is not None:
                    self.save_species_data(
                        self.previous_selected_species,
                        form=self.previous_selected_form,
                    )
            elif getattr(self, "moves_main_tab_index", -1) == self.previous_main_tab:
                self.save_moves_defs_table()
            elif self.previous_main_tab == 2:  # Items
                self.save_items_table()
                self.items_editor.save_icon_changes()
            elif self.previous_main_tab == 3:  # Starters
                # Update starter data for each starter
                self.source_data.set_starter_data(
                    0, "species", self.ui.starter1_species.currentData()
                )
                self.source_data.set_starter_data(
                    0, "level", self.ui.starter1_level.value()
                )
                self.source_data.set_starter_data(
                    0, "item", self.ui.starter1_item.currentData()
                )
                self.source_data.set_starter_data(
                    0, "custom_move", self.ui.starter1_move.currentData()
                )

                self.source_data.set_starter_data(
                    1, "species", self.ui.starter2_species.currentData()
                )
                self.source_data.set_starter_data(
                    1, "level", self.ui.starter2_level.value()
                )
                self.source_data.set_starter_data(
                    1, "item", self.ui.starter2_item.currentData()
                )
                self.source_data.set_starter_data(
                    1, "custom_move", self.ui.starter2_move.currentData()
                )

                self.source_data.set_starter_data(
                    2, "species", self.ui.starter3_species.currentData()
                )
                self.source_data.set_starter_data(
                    2, "level", self.ui.starter3_level.value()
                )
                self.source_data.set_starter_data(
                    2, "item", self.ui.starter3_item.currentData()
                )
                self.source_data.set_starter_data(
                    2, "custom_move", self.ui.starter3_move.currentData()
                )
            elif self.previous_main_tab == 4:  # Trainers
                self._save_trainers_editor()
                self._save_trainer_classes()
                self._save_trainer_graphics()
            elif self.previous_main_tab == 5:  # UI
                pass
            elif self.previous_main_tab == 6:  # Config
                pass
        except AttributeError:
            pass

        # Ensure the current species data is reloaded so the Graphics tab repaints
        self.refresh_current_species()

        # Update the previous main tab
        self.previous_main_tab = self.ui.mainTabs.currentIndex()

    # ── Species info tab enhancements ────────────────────────────────────────

    def _setup_species_info_enhancements(self):
        """
        Restructure the species Info tab to show:
          • Front sprite + icon thumbnail on the right
          • Species constant label (SPECIES_*) below Dex #
          • Name field made read-only
          • Rename… button beside the name field
        All existing widget names (species_name, dex_num, …) remain intact so
        the rest of mainwindow.py needs no changes.
        """
        try:
            _counter_style = "color: #888888; font-size: 10px; font-family: 'Courier New';"

            def _make_lineedit_counter(line_edit, max_chars):
                """
                Return a QLabel counter wired to *line_edit*.
                Enforces setMaxLength and updates 'X/N' on every keystroke.
                Turns red when at the limit.
                """
                line_edit.setMaxLength(max_chars)
                lbl = QLabel("{0}/{1}".format(len(line_edit.text()), max_chars))
                lbl.setStyleSheet(_counter_style)
                lbl.setToolTip(
                    "Characters used / character limit.\n"
                    "The GBA engine cannot display more than {0} characters here.".format(max_chars)
                )
                def _update(_text=""):
                    used = len(line_edit.text())
                    lbl.setText("{0}/{1}".format(used, max_chars))
                    lbl.setStyleSheet(
                        _counter_style + " color: #cc3333;" if used >= max_chars
                        else _counter_style
                    )
                line_edit.textChanged.connect(_update)
                return lbl

            # ── 1. Make species_name read-only (rename via dedicated button) ───
            #       POKEMON_NAME_LENGTH = 10  (include/constants/global.h)
            self.ui.species_name.setReadOnly(True)

            # ── 2. Wrap species_name + counter + rename button in an HBox ──────
            name_container = QWidget(self.ui.tab_pokemon_info)
            name_hbox = QHBoxLayout(name_container)
            name_hbox.setContentsMargins(0, 0, 0, 0)
            name_hbox.setSpacing(6)
            name_hbox.addWidget(self.ui.species_name)

            # Counter sits between the field and the Rename button
            self._species_name_counter = _make_lineedit_counter(self.ui.species_name, 10)
            name_hbox.addWidget(self._species_name_counter)

            self._species_rename_btn = QPushButton("Rename...")
            self._species_rename_btn.setFixedWidth(70)
            self._species_rename_btn.setToolTip(
                "Rename this species constant and display name across the whole project.\n"
                "Changes are staged in memory and written to disk on File -> Save."
            )
            self._species_rename_btn.clicked.connect(
                lambda: self.rename_entity(
                    species=getattr(self, "previous_selected_species", None)
                )
            )
            name_hbox.addWidget(self._species_rename_btn)

            # Replace the FieldRole widget at row 0 with our container
            self.ui.formLayout.setWidget(
                0, QFormLayout.ItemRole.FieldRole, name_container
            )

            # ── 3. Insert a "Constant" row after dex_num (row 1) ──────────────
            self._species_const_lbl = QLabel("—")
            self._species_const_lbl.setStyleSheet(
                "color: #888888; font-family: 'Courier New'; font-size: 10px;"
            )
            const_row_lbl = QLabel("Constant")
            self.ui.formLayout.insertRow(2, const_row_lbl, self._species_const_lbl)

            # ── 3b. Enforce category limit + add inline counter ────────────────
            #        PokedexEntry.categoryName is u8[12] (null-terminated) = 11 usable chars
            #        The generated UI incorrectly had setMaxLength(12); fix it here.
            self._species_cat_counter = _make_lineedit_counter(self.ui.species_category, 11)
            self.ui.species_category.textChanged.connect(lambda *_: self.setWindowModified(True))
            # Wrap category field + counter in an HBox so the counter sits inline
            cat_container = QWidget(self.ui.tab_pokemon_info)
            cat_hbox = QHBoxLayout(cat_container)
            cat_hbox.setContentsMargins(0, 0, 0, 0)
            cat_hbox.setSpacing(6)
            cat_hbox.addWidget(self.ui.species_category)
            cat_hbox.addWidget(self._species_cat_counter)
            # species_category is at form row 2 in the original UI; after inserting the
            # Constant row above it shifts to row 3.
            self.ui.formLayout.setWidget(
                3, QFormLayout.ItemRole.FieldRole, cat_container
            )

            # ── 4. Add sprite panel at column 2 of tab_pokemon_info_grid ──────
            sprite_panel = QWidget(self.ui.tab_pokemon_info)
            sprite_panel.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
            sprite_panel.setFixedWidth(80)
            sp_vbox = QVBoxLayout(sprite_panel)
            sp_vbox.setContentsMargins(4, 4, 4, 4)
            sp_vbox.setSpacing(8)
            sp_vbox.setAlignment(
                Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignHCenter
            )

            # Front sprite — native 64×64, transparent (alpha composited over tab bg)
            self._info_front_lbl = QLabel()
            self._info_front_lbl.setFixedSize(64, 64)
            self._info_front_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
            self._info_front_lbl.setStyleSheet(
                "background: transparent; border: none;"
            )
            sp_vbox.addWidget(self._info_front_lbl)

            # Icon sprite — 64×64 display, animated (same as Graphics tab)
            self._info_icon_lbl = QLabel()
            self._info_icon_lbl.setFixedSize(64, 64)
            self._info_icon_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
            self._info_icon_lbl.setStyleSheet(
                "background: transparent; border: none;"
            )
            sp_vbox.addWidget(
                self._info_icon_lbl, 0, Qt.AlignmentFlag.AlignHCenter
            )

            # Play Cry button — sits directly under the sprites on the Info tab
            self.play_cry_button = QPushButton("\u25B6 Play Cry")
            self.play_cry_button.setToolTip(
                "Play this species' cry sample\n"
                "(sound/direct_sound_samples/cries/*.wav)"
            )
            self.play_cry_button.setFixedWidth(80)
            self.play_cry_button.clicked.connect(self._on_play_current_cry)
            sp_vbox.addWidget(
                self.play_cry_button, 0, Qt.AlignmentFlag.AlignHCenter
            )
            sp_vbox.addStretch(1)

            self.ui.tab_pokemon_info_grid.addWidget(sprite_panel, 0, 2, 1, 1)

            # ── 5. Animated icon: replace iconPic button with an animated QLabel ──
            self._icon_anim_lbl = QLabel(self.ui.tab_pokemon_graphics)
            self._icon_anim_lbl.setFixedSize(64, 64)
            self._icon_anim_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
            self._icon_anim_lbl.setStyleSheet(
                "background: transparent; border: none;"
            )
            self.ui.horizontalLayout_11.replaceWidget(
                self.ui.iconPic, self._icon_anim_lbl
            )
            self.ui.iconPic.hide()
            self._icon_anim_lbl.show()

            self._icon_anim_path  = None
            self._icon_anim_frame = 0
            self._icon_timer = QTimer(self)
            self._icon_timer.setInterval(400)   # ~2.5 fps
            self._icon_timer.timeout.connect(self._tick_icon_animation)

            # ── 6. Species tree: icon size + pre-warm cache dict ─────────────────
            self.ui.tree_pokemon.setIconSize(QSize(32, 32))
            self._species_icon_cache: dict[str, QIcon] = {}

            # ── 7. Description character counter below species_description ───────
            self._species_desc_counter = QLabel("")
            self._species_desc_counter.setStyleSheet(
                "color: #555555; font-size: 10px; font-family: 'Courier New';"
            )
            # Expand the description box a little so 3 lines fit comfortably
            try:
                self.ui.species_description.setMinimumSize(330, 90)
                self.ui.species_description.setMaximumSize(16777215, 110)
            except Exception:
                pass
            # Add counter as the last row of the form (no label column)
            self.ui.formLayout.addRow(self._species_desc_counter)
            # Wire up limit enforcement — default 42 cpl / 3 lines;
            # _configure_description_limits will call _apply_description_limits
            # with the real per-project value after load.
            self._species_desc_attachment = attach_dex_limit_ui(
                self.ui.species_description,
                self._species_desc_counter,
                max_chars_per_line=42,
                max_lines=3,
            )
            self.ui.species_description.textChanged.connect(lambda: self.setWindowModified(True))

        except Exception as exc:
            # Non-fatal — don't crash if UI structure differs
            import logging
            logging.warning("_setup_species_info_enhancements failed: %s", exc)

    # ── Animated icon helpers ─────────────────────────────────────────────────

    def _set_icon_animation(self, png_path: str | None):
        """Start or stop the icon sprite animation for a new species."""
        self._icon_timer.stop()
        self._icon_anim_path  = png_path
        self._icon_anim_frame = 0
        if png_path and os.path.isfile(png_path):
            self._tick_icon_animation()   # show frame 0 immediately
            self._icon_timer.start()
        else:
            blank = QPixmap()
            if hasattr(self, "_icon_anim_lbl"):
                self._icon_anim_lbl.setPixmap(blank)
            if hasattr(self, "_info_icon_lbl"):
                self._info_icon_lbl.setPixmap(blank)

    def _tick_icon_animation(self):
        """Advance one frame of the icon sprite animation.
        Updates both the Graphics-tab label and the Info-tab label.
        """
        path = getattr(self, "_icon_anim_path", None)
        if not path or not os.path.isfile(path):
            self._icon_timer.stop()
            return
        pm = QPixmap(path)
        if pm.isNull() or pm.height() < 32:
            self._icon_timer.stop()
            return
        # Each frame is the top or bottom 32×32 of the 32×64 sprite sheet
        y = 0 if self._icon_anim_frame == 0 else 32
        frame = pm.copy(0, y, 32, 32).scaled(
            64, 64,
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.FastTransformation,
        )
        if hasattr(self, "_icon_anim_lbl"):
            self._icon_anim_lbl.setPixmap(frame)
        if hasattr(self, "_info_icon_lbl"):
            self._info_icon_lbl.setPixmap(frame)
        self._icon_anim_frame ^= 1   # toggle 0 ↔ 1

    # ── Species tree icon helpers ─────────────────────────────────────────────

    def _species_list_icon(self, species: str, form: str | None = None) -> QIcon:
        """
        Returns a QIcon showing frame 0 of the species icon sprite.
        Results are cached so each PNG is only loaded once per session.
        """
        cache_key = f"{species}:{form or ''}"
        cache = getattr(self, "_species_icon_cache", {})
        if cache_key in cache:
            return cache[cache_key]
        icon = QIcon()
        try:
            if self.source_data:
                path = self.source_data.get_species_image_path(
                    species, "iconSprite", form=form
                )
                if path and os.path.isfile(path):
                    pm = QPixmap(path)
                    if not pm.isNull() and pm.height() >= 32:
                        icon = QIcon(pm.copy(0, 0, 32, 32))
        except Exception:
            pass
        cache[cache_key] = icon
        if hasattr(self, "_species_icon_cache"):
            self._species_icon_cache[cache_key] = icon
        return icon

    def _open_species_gfx_folder(self):
        """Open the graphics folder for the currently selected species."""
        from ui.open_folder_util import open_folder
        if self._current_species_gfx_folder:
            open_folder(self._current_species_gfx_folder)

    def _update_species_info_sprites(self, front_pic: str | None, icon_pic: str | None,
                                     species: str = ""):
        """
        Update the sprite labels on the Info tab.
        Called from update_data() after graphics paths are resolved.
        """
        try:
            if hasattr(self, "_species_const_lbl"):
                self._species_const_lbl.setText(species or "—")

            if hasattr(self, "_info_front_lbl"):
                if front_pic:
                    # Load at native 64×64 — no scaling, alpha channel composited
                    # transparently over the tab background
                    pm = QPixmap(front_pic)
                    self._info_front_lbl.setPixmap(pm)
                else:
                    self._info_front_lbl.setPixmap(QPixmap())

            # _info_icon_lbl is driven by _tick_icon_animation via the shared
            # timer — no static pixmap assignment needed here
        except Exception:
            pass

    # ── Pokédex tab setup ─────────────────────────────────────────────────────

    def _setup_pokedex_tab(self):
        """Replace two-column dex lists with a QTabWidget; add detail panel."""
        try:
            # ── Hide original column headers (no longer needed) ───────────────
            self.ui.label_2.hide()   # "National Dex"
            self.ui.label_3.hide()   # "Regional Dex"

            # ── Remove reset button from its grid slot (goes into Regional tab) ─
            self.ui.tab_pokedex_grid.removeWidget(self.reset_pokedex_button)

            # ── Styled tab widget ────────────────────────────────────────────────
            _TAB_SS = """
QTabWidget::pane {
    border: 1px solid #2e2e2e;
    background: #1a1a1a;
    border-radius: 0px;
}
QTabBar::tab {
    background: #222222;
    color: #777777;
    padding: 6px 18px;
    border: 1px solid #2e2e2e;
    border-bottom: none;
    margin-right: 2px;
    font-size: 11px;
}
QTabBar::tab:selected {
    background: #1a1a1a;
    color: #dddddd;
    border-bottom: 1px solid #1a1a1a;
}
QTabBar::tab:hover:!selected {
    background: #282828;
    color: #aaaaaa;
}
"""
            list_tabs = QTabWidget()
            list_tabs.setStyleSheet(_TAB_SS)
            list_tabs.setDocumentMode(True)

            # Icon sizes
            self.ui.list_pokedex_national.setIconSize(QSize(32, 32))
            self.ui.list_pokedex_regional.setIconSize(QSize(32, 32))

            # ── National Dex tab ─────────────────────────────────────────────────
            nat_page = QWidget()
            nat_page.setStyleSheet("background: transparent;")
            nat_vbox = QVBoxLayout(nat_page)
            nat_vbox.setContentsMargins(4, 4, 4, 4)
            nat_vbox.setSpacing(0)
            nat_vbox.addWidget(self.ui.list_pokedex_national)
            list_tabs.addTab(nat_page, "National Dex")

            # ── Regional Dex tab ─────────────────────────────────────────────────
            reg_page = QWidget()
            reg_page.setStyleSheet("background: transparent;")
            reg_vbox = QVBoxLayout(reg_page)
            reg_vbox.setContentsMargins(4, 4, 4, 4)
            reg_vbox.setSpacing(4)
            reg_vbox.addWidget(self.ui.list_pokedex_regional)
            reg_vbox.addWidget(self.ui.pushButton_4)    # "Add"
            reg_vbox.addWidget(self.ui.pushButton_5)    # "Remove"
            reg_vbox.addWidget(self.reset_pokedex_button)
            list_tabs.addTab(reg_page, "Regional Dex")

            self._dex_list_tabs = list_tabs

            # ── Detail panel ─────────────────────────────────────────────────────
            dex_scroll = QScrollArea()
            dex_scroll.setWidgetResizable(True)
            dex_scroll.setFrameShape(QFrame.Shape.NoFrame)
            dex_scroll.setStyleSheet(
                "QScrollArea { background: #1a1a1a; border: none; }"
                "QScrollBar:vertical { background: #1a1a1a; width: 8px; border: none; }"
                "QScrollBar::handle:vertical { background: #444; border-radius: 4px; min-height: 20px; }"
                "QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; }"
            )

            self._pokedex_panel = PokedexDetailPanel()
            self._pokedex_panel.setStyleSheet(
                "PokedexDetailPanel { background-color: #1a1a1a; }"
            )
            self._pokedex_panel.changed.connect(
                lambda: self.setWindowModified(True)
            )
            self._pokedex_panel.play_cry_requested.connect(
                self._on_play_current_pokedex_cry
            )
            dex_scroll.setWidget(self._pokedex_panel)

            # ── Splitter: list tabs | detail panel ───────────────────────────────
            from PyQt6.QtWidgets import QSplitter
            dex_splitter = QSplitter(Qt.Orientation.Horizontal)
            dex_splitter.setObjectName("dex_splitter")
            list_tabs.setMinimumWidth(180)
            dex_splitter.addWidget(list_tabs)
            dex_splitter.addWidget(dex_scroll)
            dex_splitter.setStretchFactor(0, 0)
            dex_splitter.setStretchFactor(1, 1)
            self.ui.tab_pokedex_grid.addWidget(dex_splitter, 0, 0, 3, 3)

            # ── Connect Add / Remove buttons for regional dex ────────────────────
            self.ui.pushButton_4.clicked.connect(self._regional_dex_add)
            self.ui.pushButton_5.clicked.connect(self._regional_dex_remove)

            # Track which dex constant is currently in the panel
            self._current_dex_const: str | None = None

        except Exception as exc:
            import logging
            logging.warning("_setup_pokedex_tab failed: %s", exc)

    def _regional_dex_add(self):
        """Add a species to the regional dex via a picker dialog."""
        try:
            from PyQt6.QtWidgets import QDialog, QDialogButtonBox, QVBoxLayout, QListWidget, QListWidgetItem, QLineEdit, QLabel
            natdex = self.source_data.get_national_dex()
            regdex = self.source_data.get_regional_dex()
            in_reg = {e.get("dex_constant") for e in regdex}

            dlg = QDialog(self)
            dlg.setWindowTitle("Add to Regional Dex")
            dlg.resize(300, 500)
            layout = QVBoxLayout(dlg)
            layout.addWidget(QLabel("Select a Pokémon to add:"))
            search = QLineEdit()
            search.setPlaceholderText("Search…")
            layout.addWidget(search)
            lst = QListWidget()
            for entry in natdex:
                dc = entry.get("dex_constant", "")
                if dc in in_reg:
                    continue
                sp = entry.get("species")
                name = self.source_data.get_species_data(sp, "name") if sp else None
                label = name or sp or dc
                it = QListWidgetItem(label)
                it.setData(Qt.ItemDataRole.UserRole, entry)
                if sp:
                    it.setIcon(self._species_list_icon(sp))
                lst.addItem(it)
            layout.addWidget(lst)
            search.textChanged.connect(lambda t: [
                lst.item(i).setHidden(t.lower() not in lst.item(i).text().lower())
                for i in range(lst.count())
            ])
            btns = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
            btns.accepted.connect(dlg.accept)
            btns.rejected.connect(dlg.reject)
            layout.addWidget(btns)
            lst.itemDoubleClicked.connect(lambda _: dlg.accept())

            if dlg.exec() != QDialog.DialogCode.Accepted or not lst.currentItem():
                return

            nat_entry = lst.currentItem().data(Qt.ItemDataRole.UserRole)
            dc = nat_entry.get("dex_constant", "")
            sp = nat_entry.get("species")
            new_entry = {"species": sp, "dex_constant": dc}
            self.source_data.data["pokedex"].data.setdefault("regional_dex", []).append(new_entry)

            name = self.source_data.get_species_data(sp, "name") if sp else None
            it = QListWidgetItem(name or sp or dc)
            it.setData(Qt.ItemDataRole.UserRole, dc)
            if sp:
                it.setIcon(self._species_list_icon(sp))
            self.ui.list_pokedex_regional.addItem(it)
            self.setWindowModified(True)
        except Exception:
            import logging
            logging.exception("_regional_dex_add failed")

    def _regional_dex_remove(self):
        """Remove the selected entry from the regional dex."""
        try:
            row = self.ui.list_pokedex_regional.currentRow()
            if row < 0:
                return
            item = self.ui.list_pokedex_regional.item(row)
            dc = item.data(Qt.ItemDataRole.UserRole) if item else None
            regdex = self.source_data.data["pokedex"].data.get("regional_dex", [])
            self.source_data.data["pokedex"].data["regional_dex"] = [
                e for e in regdex if e.get("dex_constant") != dc
            ]
            self.ui.list_pokedex_regional.takeItem(row)
            self.setWindowModified(True)
        except Exception:
            import logging
            logging.exception("_regional_dex_remove failed")

    def _flush_pokedex_panel(self):
        """Write panel edits back into the in-memory pokedex data,
        and sync category/description into species_info so the stats
        page and C header writer see the same values."""
        try:
            if not (hasattr(self, "_pokedex_panel") and
                    hasattr(self, "_current_dex_const") and
                    self._current_dex_const and
                    self.source_data):
                return
            natdex = self.source_data.data["pokedex"].data.get("national_dex", [])
            for i, entry in enumerate(natdex):
                if entry.get("dex_constant") == self._current_dex_const:
                    updated = self._pokedex_panel.collect(entry)
                    natdex[i] = updated
                    # Sync category and description back to species_info
                    sp = updated.get("species")
                    if sp:
                        cat = updated.get("categoryName")
                        if cat:
                            self.source_data.set_species_info(sp, "categoryName", cat)
                        desc = updated.get("descriptionText")
                        if desc:
                            self.source_data.set_species_info(sp, "description", desc)
                    break
        except Exception:
            pass

    def on_main_tab_changed(self, index: int):
        """Handle switching between main tabs without auto-persisting edits."""
        # Do not call update_main_tabs() here; consolidation happens on Save.
        if index == self.items_tab_index:
            self.load_items_table()
        elif index == self.trainers_tab_index:
            self._load_trainers_editor()
        elif getattr(self, "moves_main_tab_index", -1) == index:
            self.load_moves_defs_table()

    def _deferred_load_items(self):
        """Called via QTimer.singleShot after load_data so the QListWidget
        already has valid geometry when items are inserted — avoids the Qt
        rendering bug where setUniformItemSizes caches a blank first-render."""
        try:
            self.load_items_table()
        except Exception:
            pass

    def load_items_table(self):
        """Populate the items editor with item data from source_data."""
        if not self.source_data:
            return
        raw = self.source_data.get_pokemon_items()
        # Normalise list → dict keyed by constant
        if isinstance(raw, list):
            items: dict = {}
            for entry in raw:
                const = entry.get("itemId") or entry.get("constant") or ""
                if const:
                    items[const] = entry
        else:
            items = raw or {}
        # Resolve project root so icons can be found in graphics/items/icons/
        project_path = ""
        try:
            project_path = str(self.project_info.get("dir", "") or "")
        except Exception:
            pass
        self.items_editor.load_items(items, project_path=project_path)

    def save_items_table(self):
        """Flush items editor edits back into source_data."""
        if not self.source_data:
            return
        updated = self.items_editor.collect_all()
        raw = self.source_data.get_pokemon_items()
        if isinstance(raw, list):
            # Preserve list format — update in-place by constant
            for entry in raw:
                const = entry.get("itemId") or entry.get("constant") or ""
                if const in updated:
                    entry.update(updated[const])
            self.source_data.data["pokemon_items"].data = raw
        else:
            self.source_data.data["pokemon_items"].data = updated

    def load_moves_defs_table(self):
        """Populate the global moves widget."""
        if not self.source_data or not hasattr(self.ui, "moves_widget"):
            return
        moves = self.source_data.get_pokemon_moves() or {}
        descriptions = {}
        for mv in moves:
            desc = self.source_data.get_move_description(mv)
            if not desc:
                # For new moves, the description lives in the move data dict
                # (written there by set_move_data) but not yet in the
                # move_descriptions overlay or C file.
                desc = (moves[mv].get("description") or "")
            if desc:
                descriptions[mv] = desc

        # Extract animation labels and per-move animation mapping from
        # battle_anim_scripts.s so the detail panel can show/edit them.
        anim_labels, move_anims = self._extract_move_animations()
        for mv, anim in move_anims.items():
            if mv in moves:
                moves[mv]["animation"] = anim

        self.ui.moves_widget.load_moves(moves, descriptions)
        if anim_labels:
            self.ui.moves_widget.populate_animations(anim_labels)

    def save_moves_defs_table(self):
        """Flush the moves widget edits back to the data manager."""
        if not self.source_data or not hasattr(self.ui, "moves_widget"):
            return
        # Save the currently-displayed move before reading data
        self.ui.moves_widget.save_current()
        moves_data = self.ui.moves_widget.get_moves_data()
        descriptions = self.ui.moves_widget.get_descriptions()

        # If the moves widget was never loaded (user never visited the tab),
        # moves_data will be empty — skip to avoid wiping data.
        if not moves_data:
            return

        int_fields = {"power", "accuracy", "pp", "priority", "secondaryEffectChance"}
        for const, data in moves_data.items():
            for key, val in data.items():
                if key in int_fields:
                    try:
                        val = int(val)
                    except (TypeError, ValueError):
                        pass
                self.source_data.set_move_data(const, key, val)

        # Write descriptions back (only if we have data to write)
        if descriptions:
            try:
                pm = self.source_data.data.get("pokemon_moves")
                if pm and pm.data:
                    pm.data.setdefault("move_descriptions", {}).update(descriptions)
            except Exception:
                pass

    # ── Abilities editor load / save ────────────────────────────────────────

    def load_abilities_editor(self):
        """Populate the abilities editor with ability data + species cross-refs."""
        if not self.source_data or not hasattr(self, "abilities_tab") or not self.abilities_tab:
            return
        abilities = self.source_data.get_pokemon_abilities() or {}

        # The JSON cache only has {name, id} — read display names and
        # descriptions directly from src/data/text/abilities.h (the same
        # file the name decapitalizer reads/writes).
        root = ""
        try:
            root = self.source_data.docker_util.repo_root()
        except Exception:
            pass
        if root:
            self._enrich_abilities_from_text(root, abilities)

        # Build species data dict for cross-reference.
        # get_species_ability() resolves numeric IDs to ABILITY_* constants.
        species_data = {}
        pokemon = self.source_data.get_pokemon_data() or {}
        for sp_const in pokemon:
            resolved = []
            for i in range(2):
                try:
                    ab = self.source_data.get_species_ability(sp_const, i)
                except Exception:
                    ab = "ABILITY_NONE"
                resolved.append(ab or "ABILITY_NONE")
            sp_name = self.source_data.get_species_data(sp_const, "name") or sp_const
            species_data[sp_const] = {
                "abilities": resolved,
                "name": sp_name,
            }

        self.abilities_tab.load_abilities(abilities, species_data, project_root=root)

    @staticmethod
    def _enrich_abilities_from_text(root: str, abilities: dict):
        """Read display names + descriptions from src/data/text/abilities.h."""
        import re as _re
        text_path = os.path.join(root, "src", "data", "text", "abilities.h")
        if not os.path.isfile(text_path):
            return
        try:
            with open(text_path, encoding="utf-8") as f:
                content = f.read()
        except OSError:
            return

        # Display names from gAbilityNames:  [ABILITY_XXX] = _("Display Name"),
        name_rx = _re.compile(
            r'\[(ABILITY_[A-Z0-9_]+)\]\s*=\s*_\("([^"]*)"\)'
        )
        # Description strings:  static const u8 sXxxDescription[] = _("...");
        desc_var_rx = _re.compile(
            r'static\s+const\s+u8\s+(s\w+Description)\[\]\s*=\s*_\("([^"]*)"\)\s*;'
        )
        # Pointer table:  [ABILITY_XXX] = sXxxDescription,
        desc_ptr_rx = _re.compile(
            r'\[(ABILITY_[A-Z0-9_]+)\]\s*=\s*(s\w+Description)\s*,'
        )

        # Variable → text mapping
        desc_vars: dict[str, str] = {}
        for m in desc_var_rx.finditer(content):
            desc_vars[m.group(1)] = m.group(2)

        # Constant → description via pointer table
        for m in desc_ptr_rx.finditer(content):
            const, var = m.group(1), m.group(2)
            if var in desc_vars and const in abilities:
                abilities[const]["description"] = desc_vars[var]

        # Display names — only from the gAbilityNames array
        names_start = content.find("gAbilityNames")
        for m in name_rx.finditer(content, names_start if names_start >= 0 else 0):
            const, display_name = m.group(1), m.group(2)
            if const in abilities:
                abilities[const]["display_name"] = display_name

    def save_abilities_editor(self):
        """Flush abilities editor edits back to source data and write files."""
        if not self.source_data or not hasattr(self, "abilities_tab") or not self.abilities_tab:
            return
        self.abilities_tab.save_current()
        abilities = self.abilities_tab.get_abilities_data()
        if not abilities:
            return

        # Update in-memory data layer
        for const, data in abilities.items():
            self.source_data.set_ability_data(const, "display_name",
                                              data.get("display_name", ""))
            self.source_data.set_ability_data(const, "description",
                                              data.get("description", ""))

        # Write to disk: abilities.h (constants) + src/data/text/abilities.h (names + descriptions)
        root = self.source_data.data.get("repo_root") or ""
        if not root:
            try:
                root = self.source_data.docker_util.repo_root()
            except Exception:
                pass
        if not root:
            return
        self._write_abilities_constants(root, abilities)
        self._write_abilities_text(root, abilities)

        # Write battle/field effect code changes to C source files
        try:
            effect_msgs = self.abilities_tab.apply_effect_changes()
            if effect_msgs:
                self.log(f"Abilities: wrote effect code — "
                         + "; ".join(effect_msgs))
        except Exception as e:
            self.log(f"Abilities: error writing effect code — {e}")

    def _write_abilities_constants(self, root: str, abilities: dict):
        """Write include/constants/abilities.h."""
        path = os.path.join(root, "include", "constants", "abilities.h")
        if not os.path.isfile(path):
            return
        sorted_abs = sorted(abilities.items(), key=lambda kv: kv[1].get("id", 0))
        max_id = max((d.get("id", 0) for d in abilities.values()), default=0)

        lines = [
            "#ifndef GUARD_CONSTANTS_ABILITIES_H\n",
            "#define GUARD_CONSTANTS_ABILITIES_H\n",
            "\n",
        ]
        for const, data in sorted_abs:
            lines.append(f"#define {const} {data.get('id', 0)}\n")
        lines.append(f"\n#define ABILITIES_COUNT {max_id + 1}\n")
        lines.append("\n#endif  // GUARD_CONSTANTS_ABILITIES_H\n")

        try:
            with open(path, "w", encoding="utf-8") as f:
                f.writelines(lines)
        except OSError as e:
            print(f"[Abilities] Failed to write {path}: {e}")

    def _write_abilities_text(self, root: str, abilities: dict):
        """Write src/data/text/abilities.h (names + descriptions)."""
        path = os.path.join(root, "src", "data", "text", "abilities.h")
        if not os.path.isfile(path):
            return
        sorted_abs = sorted(abilities.items(), key=lambda kv: kv[1].get("id", 0))

        lines = []

        # Description strings
        for const, data in sorted_abs:
            # Generate a variable name from the constant: ABILITY_SPEED_BOOST → sSpeedBoostDescription
            suffix = const.replace("ABILITY_", "")
            parts = suffix.split("_")
            var_name = "s" + "".join(p.capitalize() for p in parts) + "Description"
            desc = data.get("description", "No description.")
            lines.append(f'static const u8 {var_name}[] = _("{desc}");\n')

        lines.append("\n")

        # Description pointer table
        lines.append("const u8 *const gAbilityDescriptionPointers[ABILITIES_COUNT] =\n")
        lines.append("{\n")
        for const, data in sorted_abs:
            suffix = const.replace("ABILITY_", "")
            parts = suffix.split("_")
            var_name = "s" + "".join(p.capitalize() for p in parts) + "Description"
            lines.append(f"    [{const}] = {var_name},\n")
        lines.append("};\n")

        lines.append("\n")

        # Name table
        lines.append("const u8 gAbilityNames[ABILITIES_COUNT][ABILITY_NAME_LENGTH + 1] =\n")
        lines.append("{\n")
        for const, data in sorted_abs:
            display = data.get("display_name", data.get("name", "-------"))
            lines.append(f'    [{const}] = _("{display}"),\n')
        lines.append("};\n")

        try:
            with open(path, "w", encoding="utf-8") as f:
                f.writelines(lines)
        except OSError as e:
            print(f"[Abilities] Failed to write {path}: {e}")

    def _refresh_ability_combos(self):
        """Repopulate ability dropdowns on the Pokemon tab after abilities change."""
        if not self.source_data:
            return
        # Preserve current selections
        cur1 = self.ui.ability1.currentData()
        cur2 = self.ui.ability2.currentData()

        self.ui.ability1.blockSignals(True)
        self.ui.ability2.blockSignals(True)
        self.ui.ability1.clear()
        self.ui.ability2.clear()

        abilities = self.source_data.get_pokemon_abilities()
        # Abilities data is already enriched with display_name at this point
        for ability in sorted(
            abilities.keys(),
            key=lambda x: self.source_data.get_ability_data(x, "id"),
        ):
            display = (abilities.get(ability, {}).get("display_name")
                        or abilities.get(ability, {}).get("name", ability))
            self.ui.ability1.addItem(display, ability)
            self.ui.ability2.addItem(display, ability)

        # Restore selections
        if cur1:
            idx = self.ui.ability1.findData(cur1)
            if idx >= 0:
                self.ui.ability1.setCurrentIndex(idx)
        if cur2:
            idx = self.ui.ability2.findData(cur2)
            if idx >= 0:
                self.ui.ability2.setCurrentIndex(idx)

        self.ui.ability1.blockSignals(False)
        self.ui.ability2.blockSignals(False)

    def _on_ability_rename(self, old_const: str):
        """Rename an ability constant across the whole project."""
        if not self.source_data or not old_const:
            return
        try:
            from ui.custom_widgets.rename_dialog import RenameDialog
            dlg = RenameDialog(self, prefix="ABILITY_", entity_type="Ability", show_display=True)
            dlg.set_old_constant(old_const)
            # Pre-populate with the actual display name
            ab_name = ""
            try:
                abilities = self.source_data.get_pokemon_abilities() or {}
                ab_info = abilities.get(old_const, {})
                ab_name = (ab_info.get("display_name") or ab_info.get("name") or "").strip()
            except Exception:
                pass
            if not ab_name:
                base = old_const[len("ABILITY_"):] if old_const.startswith("ABILITY_") else old_const
                ab_name = base.replace("_", " ")
            dlg.set_display_name(ab_name)
            # Live preview
            def _preview():
                _, new_const, display_name = dlg.get_values()
                if new_const and new_const != old_const:
                    try:
                        svc = getattr(getattr(self, "source_data", None), "refactor_service", None)
                        if svc:
                            previews = svc.rename_ability(old_const, new_const, display_name=display_name or "", preview=True)
                            dlg.set_preview(previews or [])
                    except Exception:
                        pass
            dlg.suffix_edit.textChanged.connect(_preview)
            try:
                dlg.display_edit.textChanged.connect(_preview)
            except Exception:
                pass
            if dlg.exec() != QDialog.DialogCode.Accepted:
                return
            _, new_const, display_name = dlg.get_values()
            if not new_const:
                return

            const_changed = new_const != old_const
            old_display = ""
            try:
                abilities = self.source_data.get_pokemon_abilities() or {}
                old_display = (abilities.get(old_const, {}).get("display_name") or "").strip()
            except Exception:
                pass
            display_changed = display_name != old_display

            if not const_changed and not display_changed:
                return

            previews = []
            if const_changed:
                svc = getattr(getattr(self, "source_data", None), "refactor_service", None)
                if svc is None:
                    QMessageBox.warning(self, "Rename", "Refactor service unavailable.")
                    return
                previews = svc.rename_ability(old_const, new_const, display_name=display_name or "")

            # Update widget in-memory data
            if hasattr(self, "abilities_tab") and self.abilities_tab:
                self.abilities_tab.save_current()
                if const_changed:
                    self.abilities_tab.rename_ability_key(old_const, new_const)
                # Update display name
                target = new_const if const_changed else old_const
                ab_data = self.abilities_tab._abilities_data
                if target in ab_data:
                    ab_data[target]["display_name"] = display_name

            # Update source_data in-memory dict
            try:
                abilities = self.source_data.get_pokemon_abilities() or {}
                if const_changed and old_const in abilities:
                    abilities[new_const] = abilities.pop(old_const)
                target = new_const if const_changed else old_const
                if target in abilities:
                    abilities[target]["display_name"] = display_name
            except Exception:
                pass

            # If only display name changed, refresh the list
            if not const_changed and display_changed:
                if hasattr(self, "abilities_tab") and self.abilities_tab:
                    self.abilities_tab._rebuild_list()
                    for i in range(self.abilities_tab._list.count()):
                        item = self.abilities_tab._list.item(i)
                        if item and item.data(Qt.ItemDataRole.UserRole) == old_const:
                            self.abilities_tab._list.setCurrentRow(i)
                            break

            self.setWindowModified(True)
            self._refresh_ability_combos()

            if const_changed:
                n = len(previews)
                maybe_exec(
                    key="rename_queued_ability",
                    parent=self,
                    title="Rename Queued",
                    text=(
                        f"Ability rename  {old_const}  \u2192  {new_const}  staged.\n"
                        f"{n} reference(s) found in source files.\n\n"
                        "Changes will be written to disk on File \u2192 Save."
                    ),
                )
            else:
                maybe_exec(
                    key="rename_queued_ability",
                    parent=self,
                    title="Name Updated",
                    text=f"Display name changed to \"{display_name}\".",
                )
        except Exception:
            import traceback
            traceback.print_exc()

    def _jump_to_species(self, species_const: str):
        """Jump to a species in the Pokemon tab."""
        try:
            # Find the species in the species list and select it
            for i in range(self.ui.speciesList.count()):
                item = self.ui.speciesList.item(i)
                if item and item.data(Qt.ItemDataRole.UserRole) == species_const:
                    self.ui.speciesList.setCurrentItem(item)
                    # Switch to Pokemon page in unified window
                    parent = self.parent()
                    while parent:
                        if hasattr(parent, '_switch_page'):
                            parent._switch_page("pokemon")
                            break
                        parent = parent.parent()
                    return
        except Exception as e:
            print(f"[Abilities] Jump to species failed: {e}")

    def on_pokemon_tab_changed(self, index: int):
        """Handle switching between Pokémon sub-tabs, saving edits first."""
        previous = getattr(self, "previous_pokemon_tab", -1)

        # Save whatever was on the previous sub-tab before switching away.
        if previous == getattr(self, "learnset_tab_index", self.moves_tab_index):
            self.save_species_learnset_table()

        # Always save species stats/info when leaving any sub-tab so edits
        # aren't lost when refresh_current_species reloads the data.
        if self.previous_selected_species is not None:
            try:
                self.save_species_data(
                    self.previous_selected_species,
                    form=self.previous_selected_form,
                )
            except Exception:
                pass

        if index == getattr(self, "learnset_tab_index", self.moves_tab_index):
            self.load_species_learnset_table()
        self.previous_pokemon_tab = index

    def _get_species_moves_table(self):
        """Return the table widget used for per-species learnsets."""

        table = getattr(self.ui, "species_moves_table", None)
        if table is None:
            table = getattr(self.ui, "moves_table", None)
        return table


    def _format_item_display(self, const: str, info: object) -> str:
        # Always show a clean label for the "no item" sentinel regardless of
        # what the game data says (pokefirered stores it as "????????").
        if const == "ITEM_NONE":
            return "— None —"
        if isinstance(info, dict):
            for key in ("english", "name", "desc"):
                value = info.get(key)
                if isinstance(value, str) and value.strip():
                    return value.strip()
        if isinstance(info, str) and info.strip():
            return info.strip()
        base = const
        if isinstance(base, str) and base.startswith("ITEM_"):
            base = base[len("ITEM_") :]
        base = str(base).replace("_", " " ).title()
        base = base.replace("Tm", "TM").replace("Hm", "HM")
        return base

    def _fallback_item_choices(self) -> list[tuple[str, str, int]]:
        try:
            root = LocalUtil(self.project_info).repo_root()
        except Exception:
            root = self.project_info.get("dir")
        if not root:
            return [("None", "ITEM_NONE", 0)]
        header = os.path.join(root, "include", "constants", "items.h")
        choices: list[tuple[str, str, int]] = []
        try:
            with open(header, encoding="utf-8", errors="ignore") as fh:
                for line in fh:
                    line = line.strip()
                    if not line.startswith("#define ITEM_"):
                        continue
                    parts = line.split()
                    if len(parts) < 3:
                        continue
                    const = parts[1]
                    if const.endswith(('_COUNT', '_LAST', '_FIRST', '_MIN', '_MAX')):
                        continue
                    try:
                        value = int(parts[2], 0)
                    except Exception:
                        value = 9999
                    choices.append((self._format_item_display(const, None), const, value))
        except OSError:
            return [("None", "ITEM_NONE", 0)]
        if not choices:
            return [("None", "ITEM_NONE", 0)]
        seen: dict[str, tuple[str, str, int]] = {}
        for name, const, value in choices:
            if const not in seen:
                seen[const] = (name, const, value)
        ordered = sorted(seen.values(), key=lambda item: (item[2], item[1]))
        # Always pin — None — to the top regardless of sort position
        ordered = [t for t in ordered if t[1] != "ITEM_NONE"]
        ordered.insert(0, ("— None —", "ITEM_NONE", 0))
        return ordered

    def _get_item_choices(self) -> list[tuple[str, str, int]]:
        items = self.source_data.get_pokemon_items()
        choices: list[tuple[str, str, int]] = []
        if isinstance(items, dict) and items:
            def sort_key(item):
                const, info = item
                value = info.get("id") if isinstance(info, dict) else None
                try:
                    value = int(value)
                except Exception:
                    value = 9999
                return (value, const)
            for const, info in sorted(items.items(), key=sort_key):
                value = sort_key((const, info))[0]
                choices.append((self._format_item_display(const, info), const, value))
        elif isinstance(items, list) and items:
            for entry in items:
                if isinstance(entry, dict):
                    const = entry.get("itemId") or entry.get("constant")
                    if not const:
                        continue
                    try:
                        value = int(entry.get("id", 9999))
                    except Exception:
                        value = 9999
                    choices.append((self._format_item_display(const, entry), const, value))
                elif isinstance(entry, str):
                    choices.append((self._format_item_display(entry, entry), entry, 9999))
        if not choices:
            return self._fallback_item_choices()
        seen: dict[str, tuple[str, str, int]] = {}
        for name, const, value in choices:
            if const not in seen:
                seen[const] = (name, const, value)
        ordered = sorted(seen.values(), key=lambda item: (item[2], item[1]))
        # Always pin — None — to the top regardless of sort position
        ordered = [t for t in ordered if t[1] != "ITEM_NONE"]
        ordered.insert(0, ("— None —", "ITEM_NONE", 0))
        return ordered

    def _populate_item_comboboxes(self, combos: list) -> None:
        combos = [combo for combo in combos if combo is not None]
        if not combos:
            return
        choices = self._get_item_choices()
        for combo in combos:
            try:
                combo.clear()
            except Exception:
                pass
        for name, const, _ in choices:
            for combo in combos:
                try:
                    combo.addItem(name, const)
                except Exception:
                    pass

    def _build_learnset_ui(self) -> None:
        """Replace the flat moves_table with a 4-tab learnset editor."""
        # Always initialize attributes so load/save guards can use hasattr checks.
        self._learnset_tabs = None
        self._level_up_table = None
        self._tmhm_table = None
        self._tutor_table = None
        self._egg_table = None
        grid = getattr(self.ui, "tab_pokemon_moves_grid", None)
        if grid is None:
            return
        # Hide the old flat table
        try:
            grid.removeWidget(self.ui.moves_table)
            self.ui.moves_table.hide()
        except Exception:
            pass

        self._learnset_tabs = QTabWidget(self.ui.tab_pokemon_moves)
        grid.addWidget(self._learnset_tabs, 0, 0, 1, 1)

        # ── Tab 1: Level-Up ──────────────────────────────────────────
        tab_level = QWidget()
        vbox = QVBoxLayout(tab_level)
        self._level_up_table = QTableWidget(0, 2)
        self._level_up_table.setHorizontalHeaderLabels(["Level", "Move"])
        self._level_up_table.horizontalHeader().setSectionResizeMode(
            0, QHeaderView.ResizeMode.Interactive)
        self._level_up_table.horizontalHeader().setSectionResizeMode(
            1, QHeaderView.ResizeMode.Stretch)
        self._level_up_table.horizontalHeader().setDefaultSectionSize(80)
        self._level_up_table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        vbox.addWidget(self._level_up_table)
        hbox = QHBoxLayout()
        btn_add = QPushButton("+ Add Move")
        btn_rem = QPushButton("− Remove")
        btn_add.clicked.connect(self._level_up_add_row)
        btn_rem.clicked.connect(self._level_up_remove_row)
        hbox.addWidget(btn_add)
        hbox.addWidget(btn_rem)
        hbox.addStretch()
        vbox.addLayout(hbox)
        self._learnset_tabs.addTab(tab_level, "Level-Up")

        # ── Tab 2: TM / HM ───────────────────────────────────────────
        tab_tmhm = QWidget()
        vbox2 = QVBoxLayout(tab_tmhm)
        self._tmhm_table = QTableWidget(0, 3)
        self._tmhm_table.setHorizontalHeaderLabels(["", "TM/HM", "Move"])
        # Use Interactive so resizes don't scan all rows on every insertRow()
        self._tmhm_table.horizontalHeader().setSectionResizeMode(
            0, QHeaderView.ResizeMode.Interactive)
        self._tmhm_table.horizontalHeader().setSectionResizeMode(
            1, QHeaderView.ResizeMode.Interactive)
        self._tmhm_table.horizontalHeader().setSectionResizeMode(
            2, QHeaderView.ResizeMode.Stretch)
        self._tmhm_table.horizontalHeader().setDefaultSectionSize(80)
        self._tmhm_table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self._tmhm_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self._tmhm_table.itemChanged.connect(self._tmhm_item_changed)
        vbox2.addWidget(self._tmhm_table)
        self._learnset_tabs.addTab(tab_tmhm, "TM / HM")

        # ── Tab 3: Tutor ─────────────────────────────────────────────
        tab_tutor = QWidget()
        vbox3 = QVBoxLayout(tab_tutor)
        self._tutor_table = QTableWidget(0, 1)
        self._tutor_table.setHorizontalHeaderLabels(["Move"])
        self._tutor_table.horizontalHeader().setSectionResizeMode(
            0, QHeaderView.ResizeMode.Stretch)
        self._tutor_table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        vbox3.addWidget(self._tutor_table)
        hbox3 = QHBoxLayout()
        btn_tutor_add = QPushButton("+ Add Move")
        btn_tutor_rem = QPushButton("− Remove")
        btn_tutor_add.clicked.connect(self._tutor_add_row)
        btn_tutor_rem.clicked.connect(self._tutor_remove_row)
        hbox3.addWidget(btn_tutor_add)
        hbox3.addWidget(btn_tutor_rem)
        hbox3.addStretch()
        vbox3.addLayout(hbox3)
        self._learnset_tabs.addTab(tab_tutor, "Tutor")

        # ── Tab 4: Egg Moves ─────────────────────────────────────────
        tab_egg = QWidget()
        vbox4 = QVBoxLayout(tab_egg)
        self._egg_table = QTableWidget(0, 1)
        self._egg_table.setHorizontalHeaderLabels(["Move"])
        self._egg_table.horizontalHeader().setSectionResizeMode(
            0, QHeaderView.ResizeMode.Stretch)
        self._egg_table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        vbox4.addWidget(self._egg_table)
        hbox4 = QHBoxLayout()
        btn_egg_add = QPushButton("+ Add Move")
        btn_egg_rem = QPushButton("− Remove")
        btn_egg_add.clicked.connect(self._egg_add_row)
        btn_egg_rem.clicked.connect(self._egg_remove_row)
        hbox4.addWidget(btn_egg_add)
        hbox4.addWidget(btn_egg_rem)
        hbox4.addStretch()
        vbox4.addLayout(hbox4)
        self._learnset_tabs.addTab(tab_egg, "Egg Moves")

    def _learnset_set_cell_widget(self, table, row: int, col: int, widget) -> None:
        """setCellWidget wrapper — sets _table_row property for focus-based removal."""
        widget.setProperty("_table_row", row)
        table.setCellWidget(row, col, widget)

    def _level_up_add_row(self) -> None:
        table = self._level_up_table
        row = table.rowCount()
        table.insertRow(row)
        spin = QSpinBox()
        spin.setRange(0, 100)
        spin.setValue(1)
        spin.valueChanged.connect(lambda *_: self.setWindowModified(True))
        self._learnset_set_cell_widget(table, row, 0, spin)
        combo = QComboBox()
        combo.setEditable(True)
        combo.setInsertPolicy(QComboBox.InsertPolicy.NoInsert)
        for m in self.learnset_move_options:
            combo.addItem(self._move_display_name(m), m)
        idx = combo.findData("MOVE_NONE")
        combo.setCurrentIndex(idx if idx >= 0 else 0)
        combo.currentTextChanged.connect(lambda *_: self.setWindowModified(True))
        self._learnset_set_cell_widget(table, row, 1, combo)
        table.scrollToBottom()
        self.setWindowModified(True)

    @staticmethod
    def _focused_row(table) -> int:
        """Return the row that currently has focus via a cell widget, or -1."""
        # First check normal table selection
        sel_rows = sorted({idx.row() for idx in table.selectedIndexes()}, reverse=True)
        if sel_rows:
            return sel_rows[0]
        # Fall back: find which cell widget has focus via _table_row property
        try:
            app = QApplication.instance()
            if app is None:
                return -1
            fw = app.focusWidget()
            # Walk up from the focus widget — it might be a child of the cell widget
            # (e.g. the line-edit inside a QComboBox)
            w = fw
            while w is not None:
                row = w.property("_table_row")
                if row is not None:
                    return int(row)
                w = w.parent()
        except Exception:
            pass
        return -1

    def _level_up_remove_row(self) -> None:
        table = self._level_up_table
        row = self._focused_row(table)
        if row >= 0:
            table.removeRow(row)
            self.setWindowModified(True)

    def _egg_add_row(self) -> None:
        table = self._egg_table
        row = table.rowCount()
        table.insertRow(row)
        combo = QComboBox()
        combo.setEditable(True)
        combo.setInsertPolicy(QComboBox.InsertPolicy.NoInsert)
        for m in self.learnset_move_options:
            combo.addItem(self._move_display_name(m), m)
        idx = combo.findData("MOVE_NONE")
        combo.setCurrentIndex(idx if idx >= 0 else 0)
        combo.currentTextChanged.connect(lambda *_: self.setWindowModified(True))
        self._learnset_set_cell_widget(table, row, 0, combo)
        table.scrollToBottom()
        self.setWindowModified(True)

    def _egg_remove_row(self) -> None:
        table = self._egg_table
        row = self._focused_row(table)
        if row >= 0:
            table.removeRow(row)
            self.setWindowModified(True)

    def _tutor_add_row(self) -> None:
        table = self._tutor_table
        row = table.rowCount()
        table.insertRow(row)
        combo = QComboBox()
        combo.setEditable(True)
        combo.setInsertPolicy(QComboBox.InsertPolicy.NoInsert)
        for m in self.learnset_move_options:
            combo.addItem(self._move_display_name(m), m)
        idx = combo.findData("MOVE_NONE")
        combo.setCurrentIndex(idx if idx >= 0 else 0)
        combo.currentTextChanged.connect(lambda *_: self.setWindowModified(True))
        self._learnset_set_cell_widget(table, row, 0, combo)
        table.scrollToBottom()
        self.setWindowModified(True)

    def _tutor_remove_row(self) -> None:
        table = self._tutor_table
        row = self._focused_row(table)
        if row >= 0:
            table.removeRow(row)
            self.setWindowModified(True)

    def _tmhm_item_changed(self, item) -> None:
        if item.column() == 0:
            self.setWindowModified(True)

    def _refresh_learnset_option_cache(self) -> None:
        """Build option lists for all 4 learnset editor tabs.

        Results are cached until _learnset_cache_valid is False (set on every
        new data load) so repeated species switches don't re-scan all moves.
        """
        if not self.source_data:
            self.learnset_move_options = []
            self.learnset_tmhm_options = []
            self.learnset_tmhm_move_map = {}
            self.learnset_tutor_moves = []
            self._learnset_cache_valid = False
            return
        if getattr(self, "_learnset_cache_valid", False):
            return  # cache is still fresh

        try:
            moves_map = self.source_data.get_pokemon_moves() or {}
        except Exception:
            moves_map = {}
        move_keys = sorted(moves_map.keys()) if isinstance(moves_map, dict) else []
        if "MOVE_NONE" not in move_keys:
            move_keys.insert(0, "MOVE_NONE")
        self.learnset_move_options = move_keys
        self.learnset_moves_map = moves_map  # kept for display-name lookup

        tmhm_map: dict = {}   # "TM06" → "MOVE_TOXIC"
        tutor_set: set = set()
        try:
            pm = getattr(self.source_data, "data", {}).get("pokemon_moves")
            if pm and getattr(pm, "data", None):
                species_map = pm.data.get("species_moves") or {}
                for entries in species_map.values():
                    for entry in entries or []:
                        method = str(entry.get("method") or "").upper()
                        move = str(entry.get("move") or "").strip()
                        value = str(entry.get("value") or "").strip().upper()
                        if method in {"TM", "HM"} and value and move:
                            if value not in tmhm_map:
                                tmhm_map[value] = move
                        elif method == "TUTOR" and move:
                            tutor_set.add(move)
        except Exception:
            pass
        if not tmhm_map:
            for i in range(1, 51):
                tmhm_map[f"TM{i:02d}"] = ""
            for i in range(1, 9):
                tmhm_map[f"HM{i:02d}"] = ""

        def sort_key(code: str) -> tuple:
            code = (code or "").upper()
            prefix = 0 if code.startswith("TM") else (1 if code.startswith("HM") else 2)
            digits = ''.join(ch for ch in code[2:] if ch.isdigit())
            try:
                number = int(digits)
            except Exception:
                number = 999
            return (prefix, number, code)

        self.learnset_tmhm_options = sorted(tmhm_map.keys(), key=sort_key)
        self.learnset_tmhm_move_map = tmhm_map
        self.learnset_tutor_moves = sorted(tutor_set)
        self._learnset_cache_valid = True

    def _move_display_name(self, const: str) -> str:
        """Return a human-readable name for a move constant (e.g. 'Tackle' for 'MOVE_TACKLE')."""
        data = getattr(self, "learnset_moves_map", {}).get(const, {})
        name = (data.get("name") or "").strip()
        if name:
            return name
        return const.replace("MOVE_", "").replace("_", " ").title()

    def _populate_combo(self, combo: QComboBox, options: list[str], current: str, *, uppercase: bool = False, editable: bool | None = None) -> None:
        """Populate a combo box while preserving the current value."""

        if editable is not None:
            combo.setEditable(editable)
        combo.blockSignals(True)
        combo.clear()
        for option in options:
            combo.addItem(option)
        cur_text = (current or "").strip()
        if uppercase:
            cur_text = cur_text.upper()
        found_index = -1
        if cur_text:
            match_flag = getattr(getattr(Qt, "MatchFlag", None), "MatchFixedString", None)
            try:
                if match_flag is not None:
                    found_index = combo.findText(cur_text, match_flag)
                else:
                    found_index = combo.findText(cur_text)
            except TypeError:
                try:
                    found_index = combo.findText(cur_text)
                except Exception:
                    found_index = -1
            except AttributeError:
                found_index = -1
            if found_index == -1:
                combo.insertItem(0, cur_text)
        if cur_text:
            combo.setCurrentText(cur_text)
        elif options:
            combo.setCurrentIndex(0)
        combo.blockSignals(False)

    def _sync_learnset_item(self, row: int, column: int, text: str | None = None) -> None:
        """Ensure the underlying table item mirrors the widget's value."""

        table = self._get_species_moves_table()
        if table is None:
            return
        if text is None:
            text = self._get_learnset_cell_text(table, row, column)
        if text is None:
            text = ""
        item = table.item(row, column)
        if item is None:
            item = QTableWidgetItem(str(text))
            table.setItem(row, column, item)
        else:
            item.setText(str(text))

    def _get_learnset_cell_text(self, table, row: int, column: int) -> str:
        widget_getter = getattr(table, "cellWidget", None)
        widget = widget_getter(row, column) if callable(widget_getter) else None
        if isinstance(widget, QComboBox):
            return widget.currentText().strip()
        if isinstance(widget, QSpinBox):
            return str(widget.value())
        if isinstance(widget, QLineEdit):
            return widget.text().strip()
        item = table.item(row, column)
        return item.text().strip() if item else ""

    def _learnset_combo_changed(self, combo: QComboBox) -> None:
        table = self._get_species_moves_table()
        if table is None:
            return
        row = combo.property("_learnset_row")
        column = combo.property("_learnset_column")
        uppercase = bool(combo.property("_learnset_uppercase"))
        if row is None or column is None:
            index = table.indexAt(combo.pos())
            if not index.isValid():
                return
            row = index.row()
            column = index.column()
        text = combo.currentText().strip()
        if uppercase:
            text = text.upper()
            if combo.currentText() != text:
                combo.blockSignals(True)
                combo.setCurrentText(text)
                combo.blockSignals(False)
        self._sync_learnset_item(int(row), int(column), text)
        try:
            self.setWindowModified(True)
        except Exception:
            pass
        if int(column) == 1:
            self._update_learnset_value_widget(int(row), text)

    def _learnset_value_changed(self, widget) -> None:
        table = self._get_species_moves_table()
        if table is None:
            return
        row = widget.property("_learnset_row")
        column = widget.property("_learnset_column")
        uppercase = bool(getattr(widget, "property", lambda *_: None)("_learnset_uppercase")) if hasattr(widget, "property") else False
        if row is None or column is None:
            index = table.indexAt(widget.pos())
            if not index.isValid():
                return
            row = index.row()
            column = index.column()
        if isinstance(widget, QSpinBox):
            text = str(widget.value())
        elif isinstance(widget, QComboBox):
            text = widget.currentText().strip()
            if uppercase:
                text = text.upper()
                if widget.currentText() != text:
                    widget.blockSignals(True)
                    widget.setCurrentText(text)
                    widget.blockSignals(False)
        elif isinstance(widget, QLineEdit):
            text = widget.text().strip()
        else:
            text = ""
        self._sync_learnset_item(int(row), int(column), text)
        try:
            self.setWindowModified(True)
        except Exception:
            pass

    def _update_learnset_value_widget(self, row: int, method: str | None = None, value: str | None = None) -> None:
        table = self._get_species_moves_table()
        if table is None:
            return
        method_text = (method or self._get_learnset_cell_text(table, row, 1) or "LEVEL").upper()
        current_value = value if value is not None else self._get_learnset_cell_text(table, row, 2)
        if not hasattr(table, "setCellWidget") or not hasattr(table, "cellWidget"):
            self._sync_learnset_item(row, 1, method_text)
            self._sync_learnset_item(row, 2, str(current_value or ""))
            return
        widget = table.cellWidget(row, 2)

        if method_text == "LEVEL":
            if not isinstance(widget, QSpinBox):
                widget = QSpinBox(table)
                widget.setRange(0, 255)
                widget.setProperty("_learnset_column", 2)
                widget.valueChanged.connect(lambda _, w=widget: self._learnset_value_changed(w))
                table.setCellWidget(row, 2, widget)
            widget.setProperty("_learnset_row", row)
            try:
                level_value = int(current_value)
            except Exception:
                level_value = 0
            widget.blockSignals(True)
            widget.setValue(level_value)
            widget.blockSignals(False)
            self._sync_learnset_item(row, 2, str(widget.value()))
        elif method_text in {"TM", "HM"}:
            if not isinstance(widget, QComboBox):
                widget = QComboBox(table)
                widget.setInsertPolicy(QComboBox.InsertPolicy.NoInsert)
                widget.setProperty("_learnset_column", 2)
                widget.setProperty("_learnset_uppercase", True)
                widget.currentTextChanged.connect(lambda *_: self._learnset_value_changed(widget))
                table.setCellWidget(row, 2, widget)
            widget.setEditable(True)
            widget.setProperty("_learnset_row", row)
            self._populate_combo(widget, self.learnset_tmhm_options, (current_value or "").upper(), uppercase=True)
            self._sync_learnset_item(row, 2, widget.currentText().strip())
        else:
            if not isinstance(widget, QLineEdit):
                widget = QLineEdit(table)
                widget.setProperty("_learnset_column", 2)
                widget.textChanged.connect(lambda *_: self._learnset_value_changed(widget))
                table.setCellWidget(row, 2, widget)
            widget.setProperty("_learnset_row", row)
            widget.blockSignals(True)
            widget.setText(current_value or "")
            widget.blockSignals(False)
            self._sync_learnset_item(row, 2, widget.text().strip())

    def _ensure_learnset_row_widgets(self, row: int) -> None:
        table = self._get_species_moves_table()
        if table is None:
            return
        if not hasattr(table, "setCellWidget") or not hasattr(table, "cellWidget"):
            return

        move_text = self._get_learnset_cell_text(table, row, 0) or "MOVE_NONE"
        move_combo = table.cellWidget(row, 0)
        if not isinstance(move_combo, QComboBox):
            move_combo = QComboBox(table)
            move_combo.setInsertPolicy(QComboBox.InsertPolicy.NoInsert)
            move_combo.setProperty("_learnset_column", 0)
            move_combo.setProperty("_learnset_uppercase", True)
            move_combo.currentTextChanged.connect(lambda *_: self._learnset_combo_changed(move_combo))
            table.setCellWidget(row, 0, move_combo)
        move_combo.setProperty("_learnset_row", row)
        self._populate_combo(move_combo, self.learnset_move_options, move_text, uppercase=True, editable=True)
        self._sync_learnset_item(row, 0, move_combo.currentText().strip())

        method_text = (self._get_learnset_cell_text(table, row, 1) or "LEVEL").upper()
        method_combo = table.cellWidget(row, 1)
        if not isinstance(method_combo, QComboBox):
            method_combo = QComboBox(table)
            method_combo.setProperty("_learnset_column", 1)
            method_combo.setProperty("_learnset_uppercase", True)
            method_combo.currentTextChanged.connect(lambda *_: self._learnset_combo_changed(method_combo))
            table.setCellWidget(row, 1, method_combo)
        method_combo.setEditable(False)
        method_combo.setProperty("_learnset_row", row)
        self._populate_combo(method_combo, self.learnset_methods, method_text, uppercase=True)
        self._sync_learnset_item(row, 1, method_combo.currentText().strip().upper())

        value_text = self._get_learnset_cell_text(table, row, 2)
        self._update_learnset_value_widget(row, method_combo.currentText(), value_text)

    def load_species_learnset_table(self, species=None):
        """Populate all 4 learnset tabs for the selected species."""
        if not self.source_data:
            return

        if species is None:
            species = getattr(self, "previous_selected_species", None)
            if not species:
                try:
                    sel = self.ui.tree_pokemon.selectedItems()
                    if sel:
                        candidate = sel[0].text(1)
                        if candidate and candidate.startswith("SPECIES_"):
                            species = candidate
                        else:
                            base = sel[0].text(2)
                            if base and base.startswith("SPECIES_"):
                                species = base
                except Exception:
                    species = None
        if not species:
            return
        if getattr(self, "_level_up_table", None) is None:
            return  # UI not built (test environment or __new__ bypass)

        try:
            moves = self.source_data.get_species_moves(species) or []
        except Exception:
            moves = []
        self._refresh_learnset_option_cache()

        # Split moves by method
        level_moves = sorted(
            [e for e in moves if str(e.get("method", "")).upper() == "LEVEL"],
            key=lambda e: int(e["value"]) if str(e.get("value", "")).lstrip('-').isdigit() else 0
        )
        tmhm_checked = {
            str(e.get("value", "")).strip().upper()
            for e in moves if str(e.get("method", "")).upper() in {"TM", "HM"}
        }
        tutor_checked = {
            str(e.get("move", ""))
            for e in moves if str(e.get("method", "")).upper() == "TUTOR"
        }
        egg_moves = [e for e in moves if str(e.get("method", "")).upper() == "EGG"]

        # ── Level-Up tab ────────────────────────────────────────────
        table = self._level_up_table
        with QSignalBlocker(table):
            table.setRowCount(0)
            for entry in level_moves:
                row = table.rowCount()
                table.insertRow(row)
                level_val = 0
                try:
                    level_val = int(entry.get("value", 0))
                except Exception:
                    pass
                spin = QSpinBox()
                spin.setRange(0, 100)
                spin.setValue(level_val)
                spin.valueChanged.connect(lambda *_: self.setWindowModified(True))
                self._learnset_set_cell_widget(table, row, 0, spin)
                combo = QComboBox()
                combo.setEditable(True)
                combo.setInsertPolicy(QComboBox.InsertPolicy.NoInsert)
                for m in self.learnset_move_options:
                    combo.addItem(self._move_display_name(m), m)
                move_const = str(entry.get("move", "MOVE_NONE"))
                idx = combo.findData(move_const)
                combo.setCurrentIndex(idx if idx >= 0 else 0)
                combo.currentTextChanged.connect(lambda *_: self.setWindowModified(True))
                self._learnset_set_cell_widget(table, row, 1, combo)
        table.resizeColumnToContents(0)

        # ── TM/HM tab ───────────────────────────────────────────────
        tm_table = self._tmhm_table
        with QSignalBlocker(tm_table):
            tm_table.setRowCount(0)
            for code in self.learnset_tmhm_options:
                row = tm_table.rowCount()
                tm_table.insertRow(row)
                check_item = QTableWidgetItem()
                check_item.setFlags(Qt.ItemFlag.ItemIsUserCheckable | Qt.ItemFlag.ItemIsEnabled)
                check_item.setCheckState(
                    Qt.CheckState.Checked if code in tmhm_checked else Qt.CheckState.Unchecked)
                check_item.setData(Qt.ItemDataRole.UserRole, code)
                tm_table.setItem(row, 0, check_item)
                code_item = QTableWidgetItem(code)
                code_item.setFlags(code_item.flags() & ~Qt.ItemFlag.ItemIsEditable)
                tm_table.setItem(row, 1, code_item)
                move_const = self.learnset_tmhm_move_map.get(code, "")
                move_display = self._move_display_name(move_const) if move_const else ""
                move_item = QTableWidgetItem(move_display)
                move_item.setData(Qt.ItemDataRole.UserRole, move_const)
                move_item.setFlags(move_item.flags() & ~Qt.ItemFlag.ItemIsEditable)
                tm_table.setItem(row, 2, move_item)
        tm_table.resizeColumnToContents(0)
        tm_table.resizeColumnToContents(1)

        # ── Tutor tab ────────────────────────────────────────────────
        tutor_table = self._tutor_table
        with QSignalBlocker(tutor_table):
            tutor_table.setRowCount(0)
            for move_const in sorted(tutor_checked):
                row = tutor_table.rowCount()
                tutor_table.insertRow(row)
                combo = QComboBox()
                combo.setEditable(True)
                combo.setInsertPolicy(QComboBox.InsertPolicy.NoInsert)
                for m in self.learnset_move_options:
                    combo.addItem(self._move_display_name(m), m)
                idx = combo.findData(move_const)
                combo.setCurrentIndex(idx if idx >= 0 else 0)
                combo.currentTextChanged.connect(lambda *_: self.setWindowModified(True))
                self._learnset_set_cell_widget(tutor_table, row, 0, combo)

        # ── Egg Moves tab ────────────────────────────────────────────
        egg_table = self._egg_table
        with QSignalBlocker(egg_table):
            egg_table.setRowCount(0)
            for entry in egg_moves:
                row = egg_table.rowCount()
                egg_table.insertRow(row)
                combo = QComboBox()
                combo.setEditable(True)
                combo.setInsertPolicy(QComboBox.InsertPolicy.NoInsert)
                for m in self.learnset_move_options:
                    combo.addItem(self._move_display_name(m), m)
                move_const = str(entry.get("move", "MOVE_NONE"))
                idx = combo.findData(move_const)
                combo.setCurrentIndex(idx if idx >= 0 else 0)
                combo.currentTextChanged.connect(lambda *_: self.setWindowModified(True))
                self._learnset_set_cell_widget(egg_table, row, 0, combo)

    def load_moves_table(self):
        """Compat wrapper for legacy call sites/tests."""

        self.load_species_learnset_table()

    def save_species_learnset_table(self, species=None):
        """Collect all 4 learnset tabs and persist to source_data."""
        if not self.source_data:
            return False
        if getattr(self, "_level_up_table", None) is None:
            return False  # UI not built (test environment or __new__ bypass)
        if species is None:
            species = getattr(self, "previous_selected_species", None)
        if not species:
            return False

        moves: list = []

        # Level-up moves (sorted by level)
        table = self._level_up_table
        level_rows = []
        for row in range(table.rowCount()):
            spin = table.cellWidget(row, 0)
            combo = table.cellWidget(row, 1)
            level = spin.value() if isinstance(spin, QSpinBox) else 0
            move = (combo.currentData() or combo.currentText()).strip() if isinstance(combo, QComboBox) else ""
            if move and move != "MOVE_NONE":
                level_rows.append({"move": move, "method": "LEVEL", "value": level})
        level_rows.sort(key=lambda e: e["value"])
        moves.extend(level_rows)

        # TM/HM moves — checked rows only; derive method from code prefix
        tm_table = self._tmhm_table
        for row in range(tm_table.rowCount()):
            check_item = tm_table.item(row, 0)
            if check_item and check_item.checkState() == Qt.CheckState.Checked:
                code = str(check_item.data(Qt.ItemDataRole.UserRole) or "")
                move_item = tm_table.item(row, 2)
                move = (move_item.data(Qt.ItemDataRole.UserRole) or move_item.text()).strip() if move_item else ""
                if code and move:
                    method = "HM" if code.upper().startswith("HM") else "TM"
                    moves.append({"move": move, "method": method, "value": code})

        # Tutor moves
        tutor_table = self._tutor_table
        for row in range(tutor_table.rowCount()):
            combo = tutor_table.cellWidget(row, 0)
            move = (combo.currentData() or combo.currentText()).strip() if isinstance(combo, QComboBox) else ""
            if move and move != "MOVE_NONE":
                moves.append({"move": move, "method": "TUTOR", "value": ""})

        # Egg moves
        egg_table = self._egg_table
        for row in range(egg_table.rowCount()):
            combo = egg_table.cellWidget(row, 0)
            move = (combo.currentData() or combo.currentText()).strip() if isinstance(combo, QComboBox) else ""
            if move and move != "MOVE_NONE":
                moves.append({"move": move, "method": "EGG", "value": ""})

        try:
            current = self.source_data.get_species_moves(species) or []
        except Exception:
            current = []
        if current != moves:
            try:
                self.source_data.set_species_moves(species, moves)
            except Exception:
                return False
            try:
                self.source_data.pending_changes = True
            except Exception:
                pass
            try:
                self.setWindowModified(True)
            except Exception:
                pass
            return True
        return False

    def save_moves_table(self):
        """Compat wrapper for legacy call sites/tests."""

        return self.save_species_learnset_table()

    def _reset_data_objects(self, keys: list[str] | None = None) -> None:
        """Restore the specified data objects to their original cache state."""

        if not self.source_data:
            return

        data_map = getattr(self.source_data, "data", {}) or {}
        targets = keys or list(data_map.keys())

        for key in targets:
            data_obj = data_map.get(key)
            if not data_obj:
                continue
            original = getattr(data_obj, "original_data", None)
            if original is None:
                continue
            try:
                data_obj.data = json.loads(json.dumps(original))
            except Exception:
                data_obj.data = copy.deepcopy(original)
            try:
                data_obj.pending_changes = False
            except Exception:
                pass

    def _on_play_current_cry(self) -> None:
        """Play the cry for the species currently shown in the Pokemon tab."""
        species = getattr(self, "previous_selected_species", None)
        if not species:
            return
        try:
            from ui.audio_player import get_audio_player
            player = get_audio_player()
            root = (self.project_info or {}).get("dir")
            if root:
                player.set_project_root(root)
            if not player.play_cry(species):
                QMessageBox.information(
                    self, "Play Cry",
                    f"No cry sample found for {species}.\n"
                    f"Expected: sound/direct_sound_samples/cries/"
                    f"{species[len('SPECIES_'):].lower()}.wav",
                )
        except Exception as e:
            QMessageBox.warning(self, "Play Cry", f"Could not play cry: {e}")

    def _on_play_current_pokedex_cry(self) -> None:
        """Play the cry for the species currently shown in the Pokedex tab."""
        self._on_play_current_cry()

    def reset_current_species_view(self) -> None:
        """Discard unsaved edits for the currently selected species."""

        if not self.source_data:
            return

        species = getattr(self, "previous_selected_species", None)
        form = getattr(self, "previous_selected_form", None)

        self._reset_data_objects(
            [
                "species_data",
                "species_graphics",
                "pokemon_evolutions",
                "pokemon_moves",
            ]
        )

        # Clear FireRed learnset overlays so tables rebuild from the restored cache.
        if hasattr(self.source_data, "_fr_species_moves_overlay"):
            try:
                self.source_data._fr_species_moves_overlay.clear()
            except Exception:
                pass
        if hasattr(self.source_data, "_fr_move_desc_overlay"):
            try:
                self.source_data._fr_move_desc_overlay.clear()
            except Exception:
                pass
        if hasattr(self.source_data, "_fr_move_desc_ready"):
            self.source_data._fr_move_desc_ready = False

        # Reload UI for the active species/sub-tab.
        self.refresh_current_species()
        if species:
            self.load_species_learnset_table(species)
        else:
            self.load_species_learnset_table()
        try:
            self.setWindowModified(False)
        except Exception:
            pass

    # AI flags — single source of truth in ui.constants
    from ui.constants import AI_FLAGS as _AI_SCRIPT_FLAGS

    def _on_trainer_rename_double_clicked(self, row: int) -> None:
        """Open a simple rename dialog for a trainer constant (col 0 double-click)."""
        if not self.source_data:
            return
        const_item = self.ui.trainers_table.item(row, 0)
        if not const_item:
            return
        old_const = const_item.text().strip()
        if not old_const:
            return

        try:
            from ui.custom_widgets.rename_dialog import RenameDialog
            dlg = RenameDialog(self, prefix="TRAINER_", entity_type="Trainer", show_display=False)
            dlg.set_old_constant(old_const)
            def _preview_trainer():
                _, new_const, _ = dlg.get_values()
                if new_const and new_const != old_const:
                    try:
                        svc = getattr(getattr(self, "source_data", None), "refactor_service", None)
                        if svc:
                            previews = svc.rename_trainer(old_const, new_const, preview=True)
                            dlg.set_preview(previews or [])
                    except Exception:
                        pass
            dlg.suffix_edit.textChanged.connect(_preview_trainer)
            if dlg.exec() != QDialog.DialogCode.Accepted:
                return
            _, new_const, _ = dlg.get_values()
            if not new_const or new_const == old_const:
                return

            # Queue the rename — actual file writes happen on File > Save
            svc = getattr(getattr(self, "source_data", None), "refactor_service", None)
            if svc is None:
                from PyQt6.QtWidgets import QMessageBox
                QMessageBox.warning(self, "Rename", "Refactor service unavailable.")
                return

            previews = svc.rename_trainer(old_const, new_const, preview=True)
            svc.pending.append({
                "op": "rename_trainer",
                "old": old_const,
                "new": new_const,
                "old_party": svc._trainer_to_party_symbol(old_const),
                "new_party": svc._trainer_to_party_symbol(new_const),
            })

            # Update the table cell immediately so the UI reflects the pending rename
            const_item.setText(new_const)
            # Also update trainers data in memory so save_trainers_table writes the new key
            trainers = self.source_data.get_pokemon_trainers()
            if old_const in trainers:
                trainers[new_const] = trainers.pop(old_const)
                self.source_data.data["pokemon_trainers"].data = trainers

            self.setWindowModified(True)
            n = len(previews)
            maybe_exec(
                key="rename_queued_trainer",
                parent=self,
                title="Rename Queued",
                text=(
                    f"Trainer rename {old_const} → {new_const} staged.\n"
                    f"{n} reference(s) found in source files.\n\n"
                    "Changes will be written to disk on File → Save."
                ),
            )
        except Exception:
            import traceback
            traceback.print_exc()

    def _on_trainer_cell_double_clicked(self, row: int, col: int) -> None:
        """Handle trainer table double-clicks.

        - Col 0 (constant): open rename dialog → queues a rename_trainer op
        - Col 5 (AI flags): open checklist dialog to toggle AI script flags
        """
        _CONST_COL = 0
        _AI_FLAGS_COL = 5

        if col == _CONST_COL:
            self._on_trainer_rename_double_clicked(row)
            return

        if col != _AI_FLAGS_COL:
            return
        item = self.ui.trainers_table.item(row, col)
        current_raw = item.text() if item else ""
        active = {f.strip() for f in current_raw.split("|")} if current_raw.strip() else set()

        try:
            from PyQt6.QtWidgets import QDialog, QDialogButtonBox, QScrollArea
            dlg = QDialog(self)
            dlg.setWindowTitle("Edit AI Flags")
            dlg_layout = QVBoxLayout(dlg)
            dlg_layout.setSpacing(4)

            scroll = QScrollArea()
            scroll.setWidgetResizable(True)
            container = QWidget()
            vbox = QVBoxLayout(container)
            vbox.setSpacing(2)

            checkboxes: list = []
            for flag_const, flag_desc in self._AI_SCRIPT_FLAGS:
                from PyQt6.QtWidgets import QCheckBox
                cb = QCheckBox(flag_desc)
                cb.setChecked(flag_const in active)
                cb.setProperty("flagConst", flag_const)
                vbox.addWidget(cb)
                checkboxes.append(cb)

            scroll.setWidget(container)
            dlg_layout.addWidget(scroll)

            btns = QDialogButtonBox(
                QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
            )
            btns.accepted.connect(dlg.accept)
            btns.rejected.connect(dlg.reject)
            dlg_layout.addWidget(btns)

            dlg.resize(480, 400)
            if dlg.exec() != QDialog.DialogCode.Accepted:
                return

            selected = [
                cb.property("flagConst")
                for cb in checkboxes
                if cb.isChecked()
            ]
            new_val = " | ".join(selected) if selected else ""
            if item:
                item.setText(new_val)
            self.setWindowModified(True)
        except Exception:
            pass  # Fallback: let the user edit the text cell directly

    # ── New trainer editor (TrainersTabWidget) ────────────────────────────────

    def _load_trainers_editor(self):
        """Load trainer data into the new TrainersTabWidget editor."""
        if not self.source_data:
            return
        try:
            trainers = self.source_data.get_pokemon_trainers() or {}
            root     = self.project_info.get("dir", "") if self.project_info else ""

            # Build species list
            species_list = [("SPECIES_NONE", "None")]
            try:
                for k, v in self.source_data.get_pokemon_data().items():
                    name = v.get("name") or k.replace("SPECIES_", "").replace("_", " ").title()
                    species_list.append((k, name))
            except Exception:
                pass

            # Build items list
            items_list = [("ITEM_NONE", "None")]
            try:
                for k, v in self.source_data.get_pokemon_items().items():
                    name = v.get("english") or v.get("name") or k
                    items_list.append((k, name))
            except Exception:
                pass

            # Build moves list
            moves_list = [("MOVE_NONE", "None")]
            try:
                raw_moves = self.source_data.get_pokemon_moves() or {}
                for k in sorted(raw_moves.keys(),
                                 key=lambda x: (self.source_data.get_move_data(x, "id") or 0)):
                    v    = raw_moves[k]
                    name = v.get("name") or k.replace("MOVE_", "").replace("_", " ").title()
                    moves_list.append((k, name))
            except Exception:
                pass

            self.trainers_editor.load(
                trainers, root, species_list, items_list, moves_list,
                species_icon_fn=self._species_list_icon,
            )

            # Load the trainer graphics tab with the same pic map
            try:
                pic_map = getattr(self.trainers_editor, "_pic_map", {})
                self.trainer_graphics_tab.load(root, pic_map)
            except Exception:
                pass

            # Also load the trainer class editor, but ONLY if it has no
            # unsaved edits — otherwise re-running load() would silently
            # discard the user's dirty name/money/pic changes.
            try:
                tce = self.trainer_class_editor
                if not (getattr(tce, "_loaded", False) and tce.has_edits()):
                    tce.load(root, trainers)
            except Exception as exc2:
                logging.getLogger(__name__).warning(
                    "_load_trainers_editor class editor: %s", exc2
                )
        except Exception as exc:
            import logging
            logging.getLogger(__name__).warning("_load_trainers_editor: %s", exc)

    def _save_trainer_classes(self):
        """Flush TrainerClassEditor edits and write to C headers."""
        if not self.trainer_class_editor.has_edits():
            return
        try:
            root = self.project_info.get("dir", "") if self.project_info else ""
            edits = self.trainer_class_editor.flush()
            write_trainer_class_names(root, edits.get("names", {}))
            write_money_table(root, edits.get("money", {}))
            write_facility_pic_mapping(
                root,
                edits.get("pics", {}),
                self.trainer_class_editor.get_class_to_fac(),
            )
            self.trainer_class_editor.clear_dirty()
        except Exception as exc:
            import logging
            logging.getLogger(__name__).warning("_save_trainer_classes: %s", exc)

    def _save_trainers_editor(self):
        """Flush TrainersTabWidget edits back to source_data and write party C code."""
        if not self.source_data:
            return
        try:
            updated = self.trainers_editor.flush()
            # Only push back if the editor was actually loaded with data;
            # an empty flush means the tab was never visited.
            if not updated:
                return
            # Push updated trainer dicts back to source_data
            self.source_data.data["pokemon_trainers"].data = updated

            # Write modified party declarations to trainer_parties.h
            pending = self.trainers_editor.get_pending_party_writes()
            if pending:
                root = self.project_info.get("dir", "") if self.project_info else ""
                import os
                parties_path = os.path.join(root, "src", "data", "trainer_parties.h")
                if os.path.isfile(parties_path):
                    try:
                        with open(parties_path, encoding="utf-8", errors="replace") as f:
                            text = f.read()
                        for symbol, new_code in pending.items():
                            text = _replace_party_declaration(text, symbol, new_code)
                        with open(parties_path, "w", encoding="utf-8", newline="\n") as f:
                            f.write(text)
                        self.trainers_editor.clear_pending_party_writes()
                    except Exception as exc:
                        import logging
                        logging.getLogger(__name__).warning(
                            "_save_trainers_editor party write: %s", exc
                        )

            # Write edited dialogue text back to text.inc files
            try:
                if self.trainers_editor.save_dialogue_edits():
                    self.log("Trainer dialogue text saved to text.inc files.")
            except Exception as exc:
                import logging
                logging.getLogger(__name__).warning(
                    "_save_trainers_editor dialogue write: %s", exc
                )
        except Exception as exc:
            import logging
            logging.getLogger(__name__).warning("_save_trainers_editor: %s", exc)

    def _save_trainer_graphics(self):
        """Flush TrainerGraphicsTab palette edits to .pal files on disk."""
        if not self.trainer_graphics_tab.has_unsaved_changes():
            return
        try:
            ok, errs = self.trainer_graphics_tab.flush_to_disk()
            if errs:
                import logging
                logging.getLogger(__name__).warning(
                    "_save_trainer_graphics errors: %s", errs
                )
        except Exception as exc:
            import logging
            logging.getLogger(__name__).warning("_save_trainer_graphics: %s", exc)

    def _save_overworld_graphics(self):
        """Flush OverworldGraphicsTab palette edits to .pal files on disk."""
        if not hasattr(self, "overworld_graphics_tab"):
            return
        if not self.overworld_graphics_tab.has_unsaved_changes():
            return
        try:
            ok, errs = self.overworld_graphics_tab.flush_to_disk()
            if errs:
                import logging
                logging.getLogger(__name__).warning(
                    "_save_overworld_graphics errors: %s", errs
                )
        except Exception as exc:
            import logging
            logging.getLogger(__name__).warning("_save_overworld_graphics: %s", exc)

    def _on_trainer_rename_from_panel(self, old_const: str):
        """Handle rename_requested from TrainersTabWidget."""
        if not self.source_data or not old_const:
            return
        try:
            from ui.custom_widgets.rename_dialog import RenameDialog
            dlg = RenameDialog(self, prefix="TRAINER_", entity_type="Trainer", show_display=False)
            dlg.set_old_constant(old_const)
            def _preview_trainer_panel():
                _, new_const, _ = dlg.get_values()
                if new_const and new_const != old_const:
                    try:
                        svc = getattr(getattr(self, "source_data", None), "refactor_service", None)
                        if svc:
                            previews = svc.rename_trainer(old_const, new_const, preview=True)
                            dlg.set_preview(previews or [])
                    except Exception:
                        pass
            dlg.suffix_edit.textChanged.connect(_preview_trainer_panel)
            if dlg.exec() != QDialog.DialogCode.Accepted:
                return
            _, new_const, _ = dlg.get_values()
            if not new_const or new_const == old_const:
                return

            svc = getattr(getattr(self, "source_data", None), "refactor_service", None)
            if svc is None:
                from PyQt6.QtWidgets import QMessageBox
                QMessageBox.warning(self, "Rename", "Refactor service unavailable.")
                return

            previews = svc.rename_trainer(old_const, new_const, preview=True)
            svc.pending.append({
                "op": "rename_trainer",
                "old": old_const,
                "new": new_const,
                "old_party": svc._trainer_to_party_symbol(old_const),
                "new_party": svc._trainer_to_party_symbol(new_const),
            })
            # Update in-memory trainer dict key
            trainers = self.source_data.get_pokemon_trainers()
            if old_const in trainers:
                trainers[new_const] = trainers.pop(old_const)
                self.source_data.data["pokemon_trainers"].data = trainers
            self.setWindowModified(True)
            # Reload the editor so the list reflects the new constant name
            self._load_trainers_editor()
            # Show script file warnings
            self.trainers_editor.show_script_warnings(old_const)

            n = len(previews)
            maybe_exec(
                key="rename_queued_trainer",
                parent=self,
                title="Rename Queued",
                text=(
                    f"Trainer rename {old_const} → {new_const} staged.\n"
                    f"{n} reference(s) found in source files.\n\n"
                    "Changes will be written to disk on File → Save."
                ),
            )
        except Exception:
            import traceback
            traceback.print_exc()

    # ── Move rename ───────────────────────────────────────────────────────────

    def _on_move_rename(self, old_const: str) -> None:
        """Rename a move constant across the whole project."""
        if not self.source_data or not old_const:
            return
        try:
            from ui.custom_widgets.rename_dialog import RenameDialog
            dlg = RenameDialog(self, prefix="MOVE_", entity_type="Move", show_display=True)
            dlg.set_old_constant(old_const)
            # Pre-populate with the actual in-game name
            move_name = ""
            try:
                moves_data = self.source_data.get_pokemon_moves() or {}
                move_info = moves_data.get(old_const, {})
                move_name = (move_info.get("name") or "").strip()
            except Exception:
                pass
            if not move_name:
                base = old_const[len("MOVE_"):] if old_const.startswith("MOVE_") else old_const
                move_name = base.replace("_", " ")
            dlg.set_display_name(move_name)
            # Live preview
            def _preview():
                _, new_const, display_name = dlg.get_values()
                if new_const and new_const != old_const:
                    try:
                        svc = getattr(getattr(self, "source_data", None), "refactor_service", None)
                        if svc:
                            previews = svc.rename_move(old_const, new_const, display_name=display_name or "", preview=True)
                            dlg.set_preview(previews or [])
                    except Exception:
                        pass
            dlg.suffix_edit.textChanged.connect(_preview)
            try:
                dlg.display_edit.textChanged.connect(_preview)
            except Exception:
                pass
            if dlg.exec() != QDialog.DialogCode.Accepted:
                return
            _, new_const, display_name = dlg.get_values()
            if not new_const:
                return

            const_changed = new_const != old_const
            # Check if display name actually changed (case-sensitive —
            # "POUND" → "Pound" IS a real change even though the constant
            # stays MOVE_POUND).
            old_display = ""
            try:
                sd_moves = self.source_data.get_pokemon_moves() or {}
                old_display = (sd_moves.get(old_const, {}).get("name") or "").strip()
            except Exception:
                pass
            display_changed = display_name != old_display

            if not const_changed and not display_changed:
                return  # truly nothing changed

            previews = []
            if const_changed:
                svc = getattr(getattr(self, "source_data", None), "refactor_service", None)
                if svc is None:
                    QMessageBox.warning(self, "Rename", "Refactor service unavailable.")
                    return
                # Queue the rename (also returns preview list of source-file hits)
                previews = svc.rename_move(old_const, new_const, display_name=display_name or "")

            # Update the moves widget in-memory data
            if hasattr(self.ui, "moves_widget"):
                self.ui.moves_widget.save_current()
                if const_changed:
                    self.ui.moves_widget.rename_move_key(old_const, new_const)

            # Update source_data in-memory dict
            try:
                sd_moves = self.source_data.get_pokemon_moves() or {}
                if const_changed and old_const in sd_moves:
                    sd_moves[new_const] = sd_moves.pop(old_const)
                    self.source_data.data["pokemon_moves"].data = sd_moves
                # Always store display name (handles case-only changes)
                target = new_const if const_changed else old_const
                if target in sd_moves:
                    sd_moves[target]["name"] = display_name
            except Exception:
                pass

            # If only display name changed, update the list right away
            if not const_changed and display_changed:
                if hasattr(self.ui, "moves_widget"):
                    mw = self.ui.moves_widget
                    if old_const in mw._moves_data:
                        mw._moves_data[old_const]["name"] = display_name
                    mw._rebuild_list()
                    # Re-select
                    for i in range(mw._list.count()):
                        item = mw._list.item(i)
                        if item and item.data(Qt.ItemDataRole.UserRole) == old_const:
                            mw._list.setCurrentRow(i)
                            break

            self.setWindowModified(True)
            if const_changed:
                n = len(previews)
                maybe_exec(
                    key="rename_queued_move",
                    parent=self,
                    title="Rename Queued",
                    text=(
                        f"Move rename  {old_const}  →  {new_const}  staged.\n"
                        f"{n} reference(s) found in source files.\n\n"
                        "Changes will be written to disk on File → Save."
                    ),
                )
            else:
                maybe_exec(
                    key="rename_queued_move",
                    parent=self,
                    title="Name Updated",
                    text=f"Display name changed to \"{display_name}\".",
                )
        except Exception:
            import traceback
            traceback.print_exc()

    # ── Item rename ───────────────────────────────────────────────────────────

    def _on_item_rename(self, old_const: str) -> None:
        """Rename an item constant across the whole project."""
        if not self.source_data or not old_const:
            return
        try:
            from ui.custom_widgets.rename_dialog import RenameDialog
            dlg = RenameDialog(self, prefix="ITEM_", entity_type="Item", show_display=False)
            dlg.set_old_constant(old_const)
            def _preview():
                _, new_const, _ = dlg.get_values()
                if new_const and new_const != old_const:
                    try:
                        svc = getattr(getattr(self, "source_data", None), "refactor_service", None)
                        if svc:
                            previews = svc.rename_item(old_const, new_const, display_name="", preview_only=True)
                            dlg.set_preview(previews or [])
                    except Exception:
                        pass
            dlg.suffix_edit.textChanged.connect(_preview)
            if dlg.exec() != QDialog.DialogCode.Accepted:
                return
            _, new_const, _ = dlg.get_values()
            if not new_const or new_const == old_const:
                return

            svc = getattr(getattr(self, "source_data", None), "refactor_service", None)
            if svc is None:
                QMessageBox.warning(self, "Rename", "Refactor service unavailable.")
                return

            # Queue the rename (also returns preview list of source-file hits)
            previews = svc.rename_item(old_const, new_const, display_name="")

            # Update the items editor in-memory data
            self.items_editor.rename_item_key(old_const, new_const)

            # Update source_data in-memory list/dict so save_items_table picks it up
            try:
                raw = self.source_data.get_pokemon_items()
                if isinstance(raw, list):
                    for entry in raw:
                        if (entry.get("itemId") or entry.get("constant")) == old_const:
                            if "itemId" in entry:
                                entry["itemId"] = new_const
                            if "constant" in entry:
                                entry["constant"] = new_const
                            break
                elif isinstance(raw, dict) and old_const in raw:
                    raw[new_const] = raw.pop(old_const)
                self.source_data.data["pokemon_items"].data = raw
            except Exception:
                pass

            self.setWindowModified(True)
            n = len(previews)
            maybe_exec(
                key="rename_queued_item",
                parent=self,
                title="Rename Queued",
                text=(
                    f"Item rename  {old_const}  →  {new_const}  staged.\n"
                    f"{n} reference(s) found in source files.\n\n"
                    "Changes will be written to disk on File → Save."
                ),
            )
        except Exception:
            import traceback
            traceback.print_exc()

    # ── Legacy table-based trainer methods (kept for fallback compatibility) ──

    def load_trainers_table(self):
        """Populate the trainers table with trainer data."""
        if not self.source_data:
            return
        trainers = self.source_data.get_pokemon_trainers()
        ordered = sorted(trainers.keys())
        self.ui.trainers_table.setRowCount(len(ordered))
        for row, const in enumerate(ordered):
            info = trainers.get(const, {})
            values = [
                const,
                info.get("trainerClass", ""),
                info.get("trainerName", ""),
                info.get("items", ""),
                info.get("doubleBattle", ""),
                info.get("aiFlags", ""),
                info.get("party", ""),
            ]
            for col, val in enumerate(values):
                item = QTableWidgetItem(str(val))
                if col == 0:
                    try:
                        item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEditable)
                    except AttributeError:
                        item.setFlags(item.flags())
                if col == 5:
                    # AI Flags — make non-editable via keyboard (dialog opened on double-click)
                    try:
                        item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEditable)
                    except AttributeError:
                        pass
                    # Build tooltip showing what each active flag does
                    raw_flags = str(val)
                    active = {f.strip() for f in raw_flags.split("|") if f.strip()}
                    tip_lines = []
                    for flag_const, flag_desc in self._AI_SCRIPT_FLAGS:
                        if flag_const in active:
                            tip_lines.append(f"• {flag_desc}")
                    if tip_lines:
                        item.setToolTip("\n".join(tip_lines))
                    else:
                        item.setToolTip("No AI flags set — double-click to edit")
                self.ui.trainers_table.setItem(row, col, item)

    def save_trainers_table(self):
        """Save edits from the trainers table back to the data manager."""
        if not self.source_data:
            return
        trainers = self.source_data.get_pokemon_trainers()
        for row in range(self.ui.trainers_table.rowCount()):
            const = self.ui.trainers_table.item(row, 0).text()
            cls = self.ui.trainers_table.item(row, 1).text()
            name = self.ui.trainers_table.item(row, 2).text()
            items = self.ui.trainers_table.item(row, 3).text()
            dbl = self.ui.trainers_table.item(row, 4).text()
            ai = self.ui.trainers_table.item(row, 5).text()
            party = self.ui.trainers_table.item(row, 6).text()
            entry = trainers.setdefault(const, {})
            entry.update(
                {
                    "trainerClass": cls,
                    "trainerName": name,
                    "items": items,
                    "doubleBattle": dbl,
                    "aiFlags": ai,
                    "party": party,
                }
            )
        self.source_data.data["pokemon_trainers"].data = trainers

    def update_action(self):
        """
        Performs an action based on the ui action.
        """
        origin = self.sender()
        if origin == self.ui.actionNew_Project:
            # Open dialog asking to save first
            qm = QMessageBox
            ret = qm.question(
                self,
                "Save Project",
                "Would you like to save your current project before creating a new one?",
                qm.StandardButton.Yes | qm.StandardButton.No,
            )
            if ret == qm.StandardButton.Yes:
                self.update_save()
            d = NewProject(parent=self)
            d.show()
        elif origin == self.ui.actionOpen_in_Terminal:
            self._open_terminal_in_project()
        elif origin == self.ui.actionChange_Plugin:
            self.change_plugin()
        elif origin == self.ui.actionRename_Entity:
            self.rename_entity()

    def change_plugin(self):
        """No-op — plugin system removed. Only pokefirered is supported."""
        pass

    def rename_entity(self, species: str | None = None):
        """
        Interactive species rename flow.

        If *species* is provided (e.g. called from the Rename button on the
        info panel while a species is already selected) the species-picker
        dropdown is skipped entirely.  When called from Tools → Rename Entity
        with no argument, the picker is shown so the user can choose any species.

        Queues the rename; actual file changes occur on Save.
        """
        if not self.source_data:
            return
        from ui.custom_widgets.rename_dialog import RenameDialog

        chosen_species = species

        if chosen_species is None:
            # ── Species picker (only when not already on a specific species) ──
            from PyQt6.QtWidgets import QInputDialog
            species_data = self.source_data.get_pokemon_data() or {}
            dex = self.source_data.get_national_dex() or []
            ordered = [d.get("species") for d in dex if isinstance(d, dict) and d.get("species") in species_data]
            if not ordered:
                ordered = sorted(species_data.keys())

            current_species = getattr(self, "previous_selected_species", None)
            try:
                sel_idx = ordered.index(current_species) if current_species in ordered else 0
            except Exception:
                sel_idx = 0

            labels = []
            for sp in ordered:
                name = self.source_data.get_species_info(sp, "speciesName") or sp
                labels.append(f"{name} ({sp})")
            choice, ok = QInputDialog.getItem(self, "Select Species to Rename", "Species:", labels, sel_idx, False)
            if not ok:
                return
            try:
                chosen_species = ordered[labels.index(choice)]
            except Exception:
                chosen_species = ordered[sel_idx]

        # ── Rename dialog ──────────────────────────────────────────────────
        old_const = chosen_species
        display_name = self.source_data.get_species_info(chosen_species, "speciesName")
        if not display_name:
            base = old_const[len("SPECIES_"):] if old_const.startswith("SPECIES_") else old_const
            display_name = base.replace("_", " ").title()

        dialog = RenameDialog(self, prefix="SPECIES_", entity_type="Species", show_display=True)
        dialog.set_old_constant(old_const)
        dialog.set_display_name(display_name)

        # Live preview function
        def _update_preview():
            try:
                new_const = dialog.new_edit.text().strip() or old_const
                disp = dialog.display_edit.text().strip() or display_name
                previews = self.source_data.refactor_service.preview_patch_plan(old_const, new_const, disp)
                dialog.set_preview(previews)
            except Exception:
                pass
        try:
            dialog.new_edit.textChanged.connect(_update_preview)
            dialog.display_edit.textChanged.connect(_update_preview)
        except Exception:
            pass
        _update_preview()

        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        old, new, display = dialog.get_values()
        if not old or not new or not display:
            return

        # Update current tree item text and visible field only (no in-memory data mutation)
        try:
            items = self.ui.tree_pokemon.selectedItems()
            if items:
                items[0].setText(0, display)
        except Exception:
            pass
        try:
            self.ui.species_name.setText(display[:10])
        except Exception:
            pass

        preview = self.source_data.refactor_service.preview_patch_plan(
            old, new, display
        )

        if not preview:
            QMessageBox.information(self, "Rename Entity", "No occurrences found")
            return

        msg = "\n".join(f"{p[0]}:{p[1]}  {p[2]!r} → {p[3]!r}" for p in preview[:10])
        more = f"\n…and {len(preview) - 10} more" if len(preview) > 10 else ""
        proceed = maybe_exec(
            key="rename_apply_confirm",
            parent=self,
            title="Apply Rename?",
            text=(
                f"Found {len(preview)} changes in name/Pokédex/cache files.\n"
                f"Token sweeps across src/ and include/ will also apply on Save.\n\n"
                + msg + more
            ),
            icon=QMessageBox.Icon.Question,
            buttons=QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            default_button=QMessageBox.StandardButton.Yes,
        )
        if proceed != QMessageBox.StandardButton.Yes:
            return

        # Queue rename (staged in memory) with the last plan. Actual file changes happen on Save.
        try:
            plan = self.source_data.refactor_service._build_patch_plan(old, new, display)  # type: ignore
        except Exception:
            plan = None
        self.source_data.refactor_service.queue_species_rename(old, new, display, plan)
        # Do not change selection constant until after Save; keep UI on old constant
        maybe_exec(
            key="rename_complete",
            parent=self,
            title="Rename Complete",
            text="Rename queued. Changes will be applied on File > Save.",
        )
        # Mark window dirty so user knows to Save
        self.setWindowModified(True)

    # ── Save progress dialog ─────────────────────────────────────────────────

    def _make_save_dialog(self) -> "QDialog":
        """Build and return a modal, uncloseable save-progress dialog.

        Each call to dlg.step(text) marks the *previous* step done (green check)
        and adds the new step as the active row (yellow arrow).  dlg.finish()
        marks the final step done and auto-closes after a short delay.
        dlg.log_line(text) appends a sub-line under the current step.
        """
        from PyQt6.QtWidgets import QDialog, QListWidget, QListWidgetItem, QSizePolicy
        from PyQt6.QtGui import QColor

        # Parent to the top-level window so the dialog is visible when
        # PorySuite is embedded inside the UnifiedMainWindow.
        top = self
        while top.parentWidget():
            top = top.parentWidget()
        dlg = QDialog(top)
        dlg.setWindowTitle("Saving…")
        dlg.setWindowFlags(
            Qt.WindowType.Dialog |
            Qt.WindowType.CustomizeWindowHint |
            Qt.WindowType.WindowTitleHint
        )
        dlg.setModal(True)
        dlg.setMinimumWidth(420)

        layout = QVBoxLayout(dlg)
        layout.setContentsMargins(16, 14, 16, 14)
        layout.setSpacing(8)

        title_lbl = QLabel("Saving project…")
        title_lbl.setStyleSheet("font-size: 13px; font-weight: bold; color: #cdd6f4;")
        layout.addWidget(title_lbl)

        lst = QListWidget()
        lst.setFont(QFont("Consolas", 9))
        lst.setStyleSheet(
            "QListWidget { background:#1e1e1e; border:1px solid #3a3a3a;"
            " border-radius:4px; color:#d4d4d4; }"
            "QListWidget::item { padding: 3px 6px; }"
        )
        lst.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        lst.setMinimumHeight(200)
        layout.addWidget(lst, 1)

        # Tracks the index of the currently-active step item
        _state = {"active_idx": -1}

        def _pump():
            QApplication.processEvents(QEventLoop.ProcessEventsFlag.ExcludeUserInputEvents)

        def _mark_done(idx: int) -> None:
            """Rewrite item at idx to show a done checkmark."""
            item = lst.item(idx)
            if item is None:
                return
            raw = item.data(Qt.ItemDataRole.UserRole) or item.text()
            item.setText(f"[done] {raw}")
            item.setForeground(QColor("#a6e3a1"))

        def _step(text: str) -> None:
            # Mark previous step done
            if _state["active_idx"] >= 0:
                _mark_done(_state["active_idx"])
            # Add new active step
            item = QListWidgetItem(f"  >>  {text}…")
            item.setData(Qt.ItemDataRole.UserRole, text)
            item.setForeground(QColor("#f9e2af"))
            lst.addItem(item)
            lst.scrollToBottom()
            _state["active_idx"] = lst.count() - 1
            title_lbl.setText(f"Saving… ({text})")
            _pump()

        def _log_line(text: str) -> None:
            item = QListWidgetItem(f"       {text}")
            item.setForeground(QColor("#6c7086"))
            lst.addItem(item)
            lst.scrollToBottom()
            _pump()

        def _finish_ok() -> None:
            if _state["active_idx"] >= 0:
                _mark_done(_state["active_idx"])
            title_lbl.setText("Saved successfully")
            title_lbl.setStyleSheet("font-size: 13px; font-weight: bold; color: #a6e3a1;")
            done_item = QListWidgetItem("All changes written.")
            done_item.setForeground(QColor("#a6e3a1"))
            lst.addItem(done_item)
            lst.scrollToBottom()
            _pump()
            if QTimer:
                QTimer.singleShot(800, dlg.accept)
            else:
                dlg.accept()

        dlg.step     = _step
        dlg.log_line = _log_line
        dlg.finish   = _finish_ok
        return dlg

    def update_save(self):
        """
        Updates the save data for the previously selected species, saves general data,
        parses data to C code, and removes "*" from the names of modified Pokémon.
        """
        # Guard against re-entrant saves (e.g. from processEvents during the dialog)
        if getattr(self, "_save_in_progress", False):
            return
        self._save_in_progress = True

        # Capture the current species edits IMMEDIATELY, before any dialog or
        # processEvents call.  processEvents pumps queued QTimer callbacks
        # (e.g. _reapply_slider from update_data) that overwrite the inline
        # widgets with fallback values, clobbering the user's edits.
        if self.previous_selected_species is not None:
            self.save_species_data(self.previous_selected_species)
        # Flag: species data was already captured above. update_main_tabs must
        # NOT call save_species_data again — by then processEvents will have
        # clobbered the widgets back to stale/fallback values.
        self._species_already_captured = True

        # Open the progress dialog — this is the only visible thing during save.
        # The central widget's updates are suppressed so tree/list repopulation
        # during load_data() does not produce flicker.
        dlg = self._make_save_dialog()
        # show() then immediately processEvents so the dialog renders before we
        # start the heavy work. We do NOT call exec() here — the dialog is driven
        # by step() / finish() which each call processEvents internally.
        dlg.show()
        dlg.raise_()
        QApplication.processEvents(QEventLoop.ProcessEventsFlag.ExcludeUserInputEvents)

        central = self.centralWidget()
        if central:
            central.setUpdatesEnabled(False)

        try:
            dlg.step("Collecting edits")

            # Flush ALL editors unconditionally. update_main_tabs() relies
            # on previous_main_tab which is broken in the unified window
            # (tabs are reparented into a stack, mainTabs index never changes).
            # Species data was already captured at line ~6995 above.
            try:
                self._flush_pokedex_panel()
            except Exception:
                pass
            try:
                self.save_items_table()
                self.items_editor.save_icon_changes()
            except Exception:
                pass
            try:
                self.save_moves_defs_table()
            except Exception:
                pass
            try:
                self._save_trainers_editor()
            except Exception:
                pass
            try:
                self._save_trainer_classes()
            except Exception:
                pass
            try:
                self._save_trainer_graphics()
            except Exception:
                pass
            try:
                self._save_overworld_graphics()
            except Exception:
                pass
            try:
                self.save_abilities_editor()
            except Exception:
                pass
            # Learnset table (only if on that sub-tab)
            try:
                learnset_index = getattr(self, "learnset_tab_index", self.moves_tab_index)
                if self.ui.tab_pokemon_data.currentIndex() == learnset_index:
                    self.save_species_learnset_table()
            except Exception:
                pass

            # Save general data
            if hasattr(self, "source_data") and self.source_data is not None:
                # Optional review of C header write-backs for safety
                confirm_headers = True
                try:
                    pmoves = self.source_data.data.get("pokemon_moves")
                    preview = pmoves.plan_writebacks() if pmoves and hasattr(pmoves, "plan_writebacks") else {}
                    if preview:
                        lines = ["The following headers will be updated:"]
                        for path, species in preview.items():
                            if not species:
                                continue
                            lst = ", ".join(species[:5]) + ("..." if len(species) > 5 else "")
                            lines.append(f"- {path}: {len(species)} species ({lst})")
                        lines.append("\nProceed with writing these changes?")
                        msg = "\n".join(lines)
                        # Re-enable central widget while showing the confirmation dialog
                        if central:
                            central.setUpdatesEnabled(True)
                        ret = maybe_exec(
                            key="save_header_confirm",
                            parent=self,
                            title="Apply C Header Changes",
                            text=msg,
                            icon=QMessageBox.Icon.Question,
                            buttons=QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                            default_button=QMessageBox.StandardButton.Yes,
                        )
                        confirm_headers = ret == QMessageBox.StandardButton.Yes
                        if central:
                            central.setUpdatesEnabled(False)
                except Exception:
                    pass

                # If there are staged renames, apply them first and reload, then avoid
                # reassigning dex numbers this save to preserve ordering.
                rename_pending = 0
                try:
                    rename_pending = len(self.source_data.refactor_service.pending)
                except Exception:
                    pass

                # Pokedex panel was already flushed above in the "Collecting edits" block.

                if rename_pending == 0:
                    # Update Pokédex ordering from the UI
                    dex_list = []
                    current_entries = {
                        d["dex_constant"]: d for d in self.source_data.get_national_dex()
                    }
                    for i in range(self.ui.list_pokedex_national.count()):
                        item = self.ui.list_pokedex_national.item(i)
                        const = item.data(Qt.ItemDataRole.UserRole)
                        entry = current_entries.get(const, {"dex_constant": const})
                        entry["dex_num"] = i + 1
                        dex_list.append(entry)
                        species = entry.get("species")
                        if species:
                            sdata = self.source_data.get_pokemon_data().get(species)
                            if sdata is not None:
                                sdata["dex_num"] = i + 1
                                sdata["dex_constant"] = const
                    self.source_data.data["pokedex"].data["national_dex"] = dex_list

                # Consolidate global Moves table edits before save
                dlg.step("Writing moves")
                try:
                    self.save_moves_defs_table()
                except Exception:
                    pass

                # Flush trainer editor edits and write any modified party C code
                dlg.step("Writing trainers")
                try:
                    self._save_trainers_editor()
                except Exception:
                    pass
                try:
                    self._save_trainer_classes()
                except Exception:
                    pass
                try:
                    self._save_trainer_graphics()
                except Exception:
                    pass
                try:
                    self._save_overworld_graphics()
                except Exception:
                    pass

                # Flush abilities editor and write abilities.h files
                dlg.step("Writing abilities")
                try:
                    self.save_abilities_editor()
                except Exception:
                    pass

                # Save Config tab (config.mk + include/config.h) if dirty
                dlg.step("Writing config")
                try:
                    if hasattr(self, "config_tab") and self.config_tab.has_changes():
                        self.config_tab.save()
                        dlg.log_line("config.mk / include/config.h updated")
                except Exception:
                    pass

                # Save UI content tab (names, location names, strings) if dirty
                dlg.step("Writing UI strings")
                try:
                    if hasattr(self, "ui_tab") and self.ui_tab.has_changes():
                        self.ui_tab.save()
                        dlg.log_line("Name pools / location names / key strings updated")
                except Exception:
                    pass

                # Save JSON + write C headers BEFORE renames, so that fields
                # like categoryName/description are written into species_info.h
                # first.  Rename operations modify those headers in place, and the
                # subsequent reload re-extracts from them — so the new fields
                # survive the round-trip.
                dlg.step("Writing JSON data")
                self.save_data(parse_headers=False)
                dlg.log_line("Project JSON saved")

                dlg.step("Writing C headers")
                if confirm_headers:
                    # Write species stats directly to species_info.h — this
                    # bypasses the broken plugin parse_to_c_code pipeline and
                    # mirrors the approach used by the evolution editor.
                    n_patched = self._write_species_info_header()
                    if n_patched >= 0:
                        dlg.log_line(f"species_info.h updated ({n_patched} species patched)")
                    else:
                        dlg.log_line("species_info.h: skipped (file not found or error)")

                    # Write pokedex entries (category) and description text
                    n_dex = self._write_pokedex_entries_header()
                    if n_dex and n_dex > 0:
                        dlg.log_line(f"pokedex_entries.h updated ({n_dex} categories)")
                    n_desc = self._write_pokedex_text_header()
                    if n_desc and n_desc > 0:
                        dlg.log_line(f"pokedex_text_fr.h updated ({n_desc} descriptions)")

                    # Write moves/learnsets directly to C headers
                    n_moves = self._write_moves_headers()
                    if n_moves >= 0:
                        dlg.log_line(f"Learnset headers updated ({n_moves} species patched)")
                    else:
                        dlg.log_line("Learnset headers: skipped (error)")

                    # Regenerate items.h from items.json
                    n_items = self._write_items_header()
                    if n_items >= 0:
                        dlg.log_line(f"items.h regenerated ({n_items} items)")
                    else:
                        dlg.log_line("items.h: skipped (file not found or error)")

                    # Write new move entries to header files if any were added
                    try:
                        new_moves_set = self.ui.moves_widget.get_new_moves() if hasattr(self.ui, "moves_widget") else set()
                        if new_moves_set:
                            all_moves = self.source_data.get_pokemon_moves() or {}
                            all_descs = {}
                            try:
                                pm = self.source_data.data.get("pokemon_moves")
                                if pm and pm.data:
                                    all_descs = pm.data.get("move_descriptions", {})
                            except Exception:
                                pass
                            new_moves_data = {k: all_moves[k] for k in new_moves_set if k in all_moves}
                            dlg.step("Writing new move headers")
                            nc = self._write_new_move_constants(new_moves_data)
                            if nc > 0:
                                dlg.log_line(f"moves.h: {nc} new constants added")
                            nn = self._write_new_move_names(new_moves_data)
                            if nn > 0:
                                dlg.log_line(f"move_names.h: {nn} new names added")
                            nd = self._write_new_move_descriptions(new_moves_data, all_descs)
                            if nd > 0:
                                dlg.log_line(f"move_descriptions.c: {nd} new descriptions added")
                            na = self._write_new_move_animations(new_moves_data)
                            if na > 0:
                                dlg.log_line(f"battle_anim_scripts.s: {na} new animation entries added")
                            self.ui.moves_widget.clear_new_moves()
                    except Exception:
                        pass

                    # Let the plugin pipeline handle remaining headers
                    # (trainers, battle_moves, move_names, move_descriptions, etc.)
                    # Mark plugins whose files were already written by direct
                    # writers above so they don't double-write.
                    for key in ("species_data", "pokemon_items", "pokemon_moves"):
                        plugin = self.source_data.data.get(key)
                        if plugin:
                            plugin._skip_parse_to_c = True
                    try:
                        self.source_data.parse_to_c_code()
                    finally:
                        for key in ("species_data", "pokemon_items", "pokemon_moves"):
                            plugin = self.source_data.data.get(key)
                            if plugin:
                                plugin._skip_parse_to_c = False
                    dlg.log_line("trainers.h, battle_moves.h updated")

                # Re-touch JSON caches so their mtime is newer than headers,
                # preventing unnecessary re-extraction on next startup.
                try:
                    for dk in ("species_data", "pokedex"):
                        dobj = self.source_data.data.get(dk)
                        if dobj and getattr(dobj, "DATA_FILE", None):
                            jpath = os.path.join(
                                self.project_info["dir"], "src", "data", dobj.DATA_FILE
                            )
                            if os.path.isfile(jpath):
                                os.utime(jpath)
                except Exception:
                    pass

                # Apply any queued refactor/rename operations now that headers are up to date
                dlg.step("Applying renames")
                try:
                    applied_ops = self.source_data.refactor_service.apply_pending(logger=self.log) or []
                    for op in applied_ops:
                        dlg.log_line(f"Renamed: {op.get('old')} -> {op.get('new')}")
                except Exception:
                    applied_ops = []

                # Only reload from disk when rename operations actually changed source files.
                # Skipping this for a plain save avoids re-running all extractors unnecessarily.
                if applied_ops:
                    dlg.step("Reloading project")
                    self.load_data(self.project_info)
                    self._restore_species_edits()
                    try:
                        self._load_trainers_editor()
                    except Exception:
                        pass
                    try:
                        self.load_moves_defs_table()
                    except Exception:
                        pass
                    # Reselect renamed species if applicable
                    try:
                        for op in applied_ops:
                            if op.get("op") == "rename_species":
                                new_const = op.get("new")
                                for i in range(self.ui.tree_pokemon.topLevelItemCount()):
                                    item = self.ui.tree_pokemon.topLevelItem(i)
                                    if item.text(1) == new_const:
                                        self.ui.tree_pokemon.setCurrentItem(item)
                                        break
                                break
                    except Exception:
                        pass

            dlg.finish()

        except Exception as _exc:
            # Re-enable updates even if something went wrong
            if central:
                central.setUpdatesEnabled(True)
            self.update()
            self._save_in_progress = False
            raise

        finally:
            # Re-enable painting and do one clean repaint
            if central:
                central.setUpdatesEnabled(True)
            self.update()
            self._save_in_progress = False
            self._species_already_captured = False

        # Remove "*" from the names of modified Pokemon
        for i in range(self.ui.tree_pokemon.topLevelItemCount()):
            item = self.ui.tree_pokemon.topLevelItem(i)
            if item.text(0).endswith("*"):
                item.setText(0, item.text(0)[0:-1])

        # Brief status bar confirmation
        self.statusBar().showMessage("Saved ✓", 3000)

    def reset_to_vanilla(self, data_file: str):
        """Download and replace a data JSON with its vanilla version."""
        qm = QMessageBox
        msg = f"Overwrite src/data/{data_file}.json with vanilla data?"
        ret = qm.question(
            self,
            "Reset to Vanilla",
            msg,
            qm.StandardButton.Yes | qm.StandardButton.No,
        )
        if ret != qm.StandardButton.Yes:
            return

        url = (
            "https://raw.githubusercontent.com/pret/pokefirered/master/src/data/"
            f"{data_file}.json"
        )
        try:
            with urllib.request.urlopen(url) as resp:
                text = resp.read().decode("utf-8")
        except Exception as e:
            qm.critical(self, "Download Failed", str(e))
            return

        try:
            path = os.path.join(
                self.project_info["dir"], "src", "data", f"{data_file}.json"
            )
            with open(path, "w", encoding="utf-8", newline="\n") as f:
                f.write(text)
        except Exception as e:
            qm.critical(self, "Write Failed", str(e))
            return

        self.load_data(self.project_info)

    # ── Direct species_info.h writer ─────────────────────────────────
    # Bypasses the plugin parse_to_c_code pipeline entirely.
    # Reads the header, patches each species block with in-memory data,
    # writes it back.  Same approach the evolution writer uses.

    def _write_species_info_header(self) -> int:
        """Patch species_info.h with current in-memory species data.

        Returns the number of species blocks updated, or -1 on error.
        """
        root = self.project_info.get("dir", "")
        path = os.path.join(root, "src", "data", "pokemon", "species_info.h")
        if not os.path.isfile(path):
            return -1

        try:
            sp_data = self.source_data.data["species_data"]
        except Exception:
            return -1

        # Read
        try:
            with open(path, "r", encoding="utf-8") as f:
                lines = f.readlines()
        except Exception:
            return -1

        if not lines:
            return -1

        def _gender_ratio_str(val):
            """Convert integer gender ratio back to C macro."""
            if val == 255:
                return "MON_GENDERLESS"
            if val == 0:
                return "MON_MALE"
            if val == 254:
                return "MON_FEMALE"
            # Convert back to PERCENT_FEMALE(x) — reverse of
            # int(round(x * 255 / 100))
            pct = round(val * 100 / 255, 1)
            # Use integer if it's a whole number
            if pct == int(pct):
                pct = int(pct)
            return f"PERCENT_FEMALE({pct})"

        def _set_field(block, field, value_str):
            """Replace .field = ... in a block, or insert before closing }."""
            pat = f".{field} ="
            for i, ln in enumerate(block):
                if pat in ln:
                    indent = ln.split(pat)[0]
                    block[i] = f"{indent}{pat} {value_str},\n"
                    return block
            # Not found — insert before closing brace
            for j in range(len(block) - 1, -1, -1):
                if block[j].strip().startswith('}'):
                    indent = "        "
                    for t in range(j - 1, max(j - 10, -1), -1):
                        stripped = block[t].lstrip()
                        if stripped and stripped[0] == '.':
                            indent = block[t][: block[t].find('.')]
                            break
                    block.insert(j, f"{indent}.{field} = {value_str},\n")
                    break
            return block

        # Walk through lines, find [SPECIES_XXX] = blocks, patch them
        out = []
        i = 0
        total = 0
        while i < len(lines):
            ln = lines[i]
            out.append(ln)

            if '[SPECIES_' in ln and '] =' in ln:
                try:
                    species_const = ln[ln.index('[') + 1 : ln.index(']')].strip()
                except ValueError:
                    species_const = None

                # Skip single-line entries like [SPECIES_NONE] = {0},
                if '{' in ln and '}' in ln:
                    i += 1
                    continue

                # Accumulate the block until closing },
                block = []
                j = i + 1
                depth = 0
                while j < len(lines):
                    block.append(lines[j])
                    depth += lines[j].count('{') - lines[j].count('}')
                    if depth <= 0 and '}' in lines[j]:
                        break
                    j += 1

                # Patch if we have data for this species
                if species_const and species_const in sp_data.data:
                    info = sp_data.data[species_const].get("species_info", {})
                    before = list(block)

                    # Integer fields (may be stored as int or str)
                    for key in ("baseHP", "baseAttack", "baseDefense",
                                "baseSpeed", "baseSpAttack", "baseSpDefense",
                                "catchRate", "expYield",
                                "evYield_HP", "evYield_Attack", "evYield_Defense",
                                "evYield_Speed", "evYield_SpAttack", "evYield_SpDefense",
                                "eggCycles", "friendship",
                                "safariZoneFleeRate"):
                        v = info.get(key)
                        if isinstance(v, int):
                            block = _set_field(block, key, str(v))
                        elif isinstance(v, str) and v.isdigit():
                            block = _set_field(block, key, v)

                    # genderRatio needs special handling — convert int back
                    # to C macro (PERCENT_FEMALE / MON_GENDERLESS / etc.)
                    gr = info.get("genderRatio")
                    if isinstance(gr, int):
                        block = _set_field(block, "genderRatio",
                                           _gender_ratio_str(gr))
                    elif isinstance(gr, str) and gr.isdigit():
                        block = _set_field(block, "genderRatio",
                                           _gender_ratio_str(int(gr)))

                    # String-constant fields
                    for key in ("growthRate", "bodyColor", "itemCommon", "itemRare"):
                        v = info.get(key)
                        if isinstance(v, str) and v:
                            block = _set_field(block, key, v)

                    # noFlip
                    nf = info.get("noFlip")
                    if nf in ("TRUE", "FALSE"):
                        block = _set_field(block, "noFlip", nf)

                    # Paired fields: types, eggGroups, abilities
                    types = info.get("types")
                    if isinstance(types, list) and len(types) >= 2:
                        block = _set_field(block, "types",
                                           f'{{ {types[0]}, {types[1]} }}')

                    eg = info.get("eggGroups")
                    if isinstance(eg, list) and len(eg) >= 1:
                        eg0 = eg[0] or "EGG_GROUP_NONE"
                        eg1 = eg[1] if len(eg) >= 2 else eg0
                        eg1 = eg1 or eg0
                        block = _set_field(block, "eggGroups",
                                           f'{{ {eg0}, {eg1} }}')

                    ab = info.get("abilities")
                    if isinstance(ab, list) and len(ab) >= 2:
                        # save_species_data stores abilities as numeric ID
                        # strings (e.g. "65"); convert back to C constants
                        def _ability_const(val):
                            if not val:
                                return "ABILITY_NONE"
                            try:
                                aid = int(val)
                                c = self.source_data.get_ability_by_id(aid)
                                return c if c else "ABILITY_NONE"
                            except (ValueError, TypeError):
                                # Already a constant string
                                return val
                        a0 = _ability_const(ab[0])
                        a1 = _ability_const(ab[1])
                        block = _set_field(block, "abilities",
                                           f'{{ {a0}, {a1} }}')

                    if block != before:
                        total += 1

                out[-1:] = [lines[i]]  # keep header line
                out.extend(block)
                i = j
            i += 1

        # Write
        try:
            with open(path, "w", encoding="utf-8", newline="\n") as f:
                f.writelines(out)
        except Exception:
            return -1

        return total

    # ── Direct pokedex header writers ────────────────────────────────
    # Category lives in pokedex_entries.h, description in pokedex_text_fr.h.
    # Both bypass the broken plugin pipeline.

    def _write_pokedex_entries_header(self) -> int:
        """Patch .categoryName in pokedex_entries.h from in-memory species data.

        Reads category from species_info (the authoritative source that
        save_species_data writes to) rather than natdex, which can fall
        out of sync.

        Returns number of entries updated, or -1 on error.
        """
        import re
        root = self.project_info.get("dir", "")
        path = os.path.join(root, "src", "data", "pokemon", "pokedex_entries.h")
        if not os.path.isfile(path):
            return -1

        try:
            sp_data = self.source_data.data["species_data"]
        except Exception:
            return -1

        # Build lookup: NATIONAL_DEX_XXX -> categoryName from species_info
        # species_info is the authoritative source — it's what the UI writes to.
        cat_map: dict[str, str] = {}
        for species_const, sdata in sp_data.data.items():
            if not species_const.startswith("SPECIES_"):
                continue
            info = sdata.get("species_info", {})
            cat = info.get("categoryName")
            if not cat:
                continue
            # Derive NATIONAL_DEX_XXX from SPECIES_XXX
            nat_const = "NATIONAL_DEX_" + species_const[len("SPECIES_"):]
            cat_map[nat_const] = cat

        if not cat_map:
            return 0

        try:
            with open(path, "r", encoding="utf-8") as f:
                text = f.read()
        except Exception:
            return -1

        total = 0
        for dc, cat in cat_map.items():
            # Find the [NATIONAL_DEX_XXX] block and replace .categoryName
            block_pat = re.compile(
                rf"(\[{re.escape(dc)}\]\s*=\s*\{{[^}}]*?\.categoryName\s*=\s*_\(\")([^\"]*)(\")",
                re.S,
            )
            new_text = block_pat.sub(lambda m: m.group(1) + cat + m.group(3), text)
            if new_text != text:
                text = new_text
                total += 1

        try:
            with open(path, "w", encoding="utf-8", newline="\n") as f:
                f.write(text)
        except Exception:
            return -1

        return total

    def _write_pokedex_text_header(self) -> int:
        """Patch description text in pokedex_text_fr.h from in-memory pokedex data.

        Returns number of entries updated, or -1 on error.
        """
        import re
        root = self.project_info.get("dir", "")
        path = os.path.join(root, "src", "data", "pokemon", "pokedex_text_fr.h")
        if not os.path.isfile(path):
            return -1

        try:
            natdex = self.source_data.data["pokedex"].data.get("national_dex", [])
        except Exception:
            return -1

        # Build lookup: description symbol -> descriptionText
        desc_map = {}
        for entry in natdex:
            sym = entry.get("description")  # e.g. "gBlobbasaurPokedexText"
            desc = entry.get("descriptionText")
            if sym and desc:
                desc_map[sym] = desc

        if not desc_map:
            return 0

        try:
            with open(path, "r", encoding="utf-8") as f:
                text = f.read()
        except Exception:
            return -1

        total = 0
        for sym, desc in desc_map.items():
            # Format description as C string literal lines:
            # const u8 gXxxPokedexText[] = _(
            #     "line1\n"
            #     "line2\n"
            #     "line3");
            lines = desc.split("\n")
            c_lines = []
            for i, line in enumerate(lines):
                escaped = line.replace("\\", "\\\\").replace('"', '\\"')
                if i < len(lines) - 1:
                    c_lines.append(f'    "{escaped}\\n"')
                else:
                    c_lines.append(f'    "{escaped}"')
            new_body = "\n".join(c_lines)

            # Match existing block: const u8 gXxxPokedexText[] = _(\n    "...");
            pat = re.compile(
                rf"(const\s+u8\s+{re.escape(sym)}\[\]\s*=\s*_\(\n)(.*?)((?:\);|\)))",
                re.S,
            )
            m = pat.search(text)
            if m:
                replacement = m.group(1) + new_body + m.group(3)
                if replacement != m.group(0):
                    text = text[:m.start()] + replacement + text[m.end():]
                    total += 1

        try:
            with open(path, "w", encoding="utf-8", newline="\n") as f:
                f.write(text)
        except Exception:
            return -1

        return total

    # ── Animation extractor ──────────────────────────────────────────

    def _extract_move_animations(self) -> tuple[list[str], dict[str, str]]:
        """Parse battle_anim_scripts.s to extract animation labels and per-move mapping.

        Returns (sorted_label_list, {MOVE_CONSTANT: "Move_Label"}).
        """
        anim_labels: list[str] = []
        move_anims: dict[str, str] = {}  # MOVE_XXX -> Move_Label
        try:
            root = self.project_info.get("dir", "")
            anim_path = os.path.join(root, "data", "battle_anim_scripts.s")
            if not os.path.isfile(anim_path):
                return anim_labels, move_anims
            with open(anim_path, "r", encoding="utf-8", errors="replace") as f:
                lines = f.readlines()

            # Pass 1: Collect all Move_XXX labels defined in the file
            label_set: set[str] = set()
            for ln in lines:
                m = re.match(r"^(Move_[A-Za-z0-9_]+)\s*:", ln)
                if m:
                    label_set.add(m.group(1))
            anim_labels = sorted(label_set)

            # Pass 2: Parse the gBattleAnims_Moves pointer table
            # Each .4byte entry corresponds to a move ID in order
            in_table = False
            move_index = 0
            moves_data = (self.source_data.get_pokemon_moves() or {}) if self.source_data else {}
            # Build reverse map: move_id -> MOVE_CONSTANT
            # The JSON stores 1-based IDs but the animation table is 0-indexed,
            # so subtract 1 to align them.
            id_to_const: dict[int, str] = {}
            for const, info in moves_data.items():
                mid = int(info.get("id", 0) or 0) - 1
                if mid >= 0:
                    id_to_const[mid] = const

            for ln in lines:
                stripped = ln.strip()
                if "gBattleAnims_Moves" in stripped and "::" in stripped:
                    in_table = True
                    move_index = 0
                    continue
                if in_table:
                    if stripped.startswith(".4byte"):
                        label = stripped.split(".4byte")[-1].strip().split()[0].strip()
                        # Remove any trailing comment
                        if "@" in label:
                            label = label.split("@")[0].strip()
                        # Skip the Move_COUNT sentinel — it's a placeholder,
                        # not an actual move, so don't count it as an index.
                        if label == "Move_COUNT":
                            continue
                        const = id_to_const.get(move_index, "")
                        if const:
                            move_anims[const] = label
                        move_index += 1
                    elif stripped and not stripped.startswith("@") and not stripped.startswith("."):
                        # Hit a non-.4byte non-comment line — end of table
                        if move_index > 0:
                            in_table = False
        except Exception:
            pass
        return anim_labels, move_anims

    # ── New-move header writers ──────────────────────────────────────
    # These write entries into files that the old pipeline never touched,
    # needed when the user adds a brand-new move via the Moves tab.

    def _write_new_move_constants(self, new_moves: dict[str, dict]) -> int:
        """Patch include/constants/moves.h — add new #define lines and bump MOVES_COUNT.

        Returns number of constants added, or -1 on error.
        """
        if not new_moves:
            return 0
        try:
            root = self.project_info.get("dir", "")
            path = os.path.join(root, "include", "constants", "moves.h")
            if not os.path.isfile(path):
                return -1
            with open(path, "r", encoding="utf-8", errors="replace") as f:
                text = f.read()

            # Find current MOVES_COUNT
            m = re.search(r"#define\s+MOVES_COUNT\s+(\d+)", text)
            if not m:
                return -1
            old_count = int(m.group(1))

            # Build new #define lines sorted by ID.
            # JSON IDs are 1-based but C #defines are 0-based, so subtract 1.
            new_lines = []
            max_c_id = old_count - 1  # highest existing 0-based ID
            for const, info in sorted(new_moves.items(), key=lambda x: x[1].get("id", 0)):
                mid = int(info.get("id", 0) or 0)
                c_id = mid - 1  # convert 1-based JSON ID to 0-based C ID
                new_lines.append(f"#define {const} {c_id}")
                if c_id > max_c_id:
                    max_c_id = c_id

            # Insert before the MOVES_COUNT line
            insert_block = "\n".join(new_lines) + "\n"
            text = text.replace(m.group(0), insert_block + "\n" + m.group(0))

            # MOVES_COUNT must be highest C ID + 1
            new_count = max_c_id + 1
            text = text.replace(f"#define MOVES_COUNT {old_count}", f"#define MOVES_COUNT {new_count}")

            with open(path, "w", encoding="utf-8", newline="\n") as f:
                f.write(text)
            return len(new_moves)
        except Exception:
            return -1

    def _write_new_move_names(self, new_moves: dict[str, dict]) -> int:
        """Append new move name entries to src/data/text/move_names.h.

        Returns number of names added, or -1 on error.
        """
        if not new_moves:
            return 0
        try:
            root = self.project_info.get("dir", "")
            path = os.path.join(root, "src", "data", "text", "move_names.h")
            if not os.path.isfile(path):
                return -1
            with open(path, "r", encoding="utf-8", errors="replace") as f:
                text = f.read()

            # Find the closing brace of the array
            # The file ends with };\n
            close_idx = text.rfind("};")
            if close_idx < 0:
                return -1

            # Ensure the last existing entry has a trailing comma
            before = text[:close_idx].rstrip()
            if before and before[-1] == ')':
                before += ','
            before += '\n'

            # Build new entries
            new_entries = []
            for const, info in sorted(new_moves.items(), key=lambda x: x[1].get("id", 0)):
                name = (info.get("name") or const.replace("MOVE_", "").replace("_", " "))[:12]  # MOVE_NAME_LENGTH
                new_entries.append(f'    [{const}] = _("{name}"),')

            insert = "\n".join(new_entries) + "\n"
            text = before + insert + text[close_idx:]

            with open(path, "w", encoding="utf-8", newline="\n") as f:
                f.write(text)
            return len(new_moves)
        except Exception:
            return -1

    def _write_new_move_descriptions(self, new_moves: dict[str, dict], descriptions: dict[str, str]) -> int:
        """Add new move description consts and pointer entries to src/move_descriptions.c.

        Returns number of descriptions added, or -1 on error.
        """
        if not new_moves:
            return 0
        try:
            root = self.project_info.get("dir", "")
            path = os.path.join(root, "src", "move_descriptions.c")
            if not os.path.isfile(path):
                return -1
            with open(path, "r", encoding="utf-8", errors="replace") as f:
                text = f.read()

            # Part 1: Add const u8 gMoveDescription_X[] lines
            # Insert them before the pointer table
            ptr_match = re.search(r"const\s+u8\s+\*\s*(?:const\s+)?gMoveDescription\w*\s*\[", text)
            if not ptr_match:
                return -1
            insert_pos = ptr_match.start()

            desc_consts = []
            ptr_entries = []
            for const, info in sorted(new_moves.items(), key=lambda x: x[1].get("id", 0)):
                # Derive the C variable name: MOVE_SHADOW_RUSH -> ShadowRush
                suffix = const.replace("MOVE_", "")
                # Convert to PascalCase: SHADOW_RUSH -> ShadowRush
                var_name = "".join(part.capitalize() for part in suffix.split("_"))
                c_var = f"gMoveDescription_{var_name}"

                desc_text = descriptions.get(const, "")
                # Convert real newlines to C \n escapes
                c_desc = desc_text.replace("\n", "\\n") if desc_text else ""
                desc_consts.append(f'const u8 {c_var}[] = _("{c_desc}");')
                ptr_entries.append(f"    [{const} - 1] = {c_var},")

            # Insert description const lines before the pointer table
            const_block = "\n".join(desc_consts) + "\n\n"
            text = text[:insert_pos] + const_block + text[insert_pos:]

            # Part 2: Add pointer entries inside the pointer array
            # Find it again (position shifted after insertion above)
            ptr_match2 = re.search(r"const\s+u8\s+\*\s*(?:const\s+)?gMoveDescription\w*\s*\[", text)
            if not ptr_match2:
                return -1
            # Find the }; that closes this array
            close_idx = text.find("};", ptr_match2.end())
            if close_idx < 0:
                return -1

            # Ensure the last existing entry has a trailing comma
            before = text[:close_idx].rstrip()
            if before and before[-1] != ',':
                before += ','
            before += '\n'

            ptr_block = "\n".join(ptr_entries) + "\n"
            text = before + ptr_block + text[close_idx:]

            with open(path, "w", encoding="utf-8", newline="\n") as f:
                f.write(text)
            return len(new_moves)
        except Exception:
            return -1

    def _write_new_move_animations(self, new_moves: dict[str, dict]) -> int:
        """Append .4byte entries to gBattleAnims_Moves in battle_anim_scripts.s.

        Returns number of entries added, or -1 on error.
        """
        if not new_moves:
            return 0
        try:
            root = self.project_info.get("dir", "")
            path = os.path.join(root, "data", "battle_anim_scripts.s")
            if not os.path.isfile(path):
                return -1
            with open(path, "r", encoding="utf-8", errors="replace") as f:
                lines = f.readlines()

            # Find the end of gBattleAnims_Moves table
            # The table ends when we hit either:
            # - A line that starts with a label (not .4byte, not blank, not comment)
            # - Another global label like gBattleAnims_StatusConditions::
            # If the last .4byte is a Move_COUNT sentinel, insert BEFORE it
            # so new move IDs line up correctly (the table is 0-indexed).
            in_table = False
            last_4byte_idx = -1
            sentinel_idx = -1  # Track Move_COUNT sentinel position
            for i, ln in enumerate(lines):
                stripped = ln.strip()
                if "gBattleAnims_Moves" in stripped and "::" in stripped:
                    in_table = True
                    continue
                if in_table:
                    if stripped.startswith(".4byte"):
                        last_4byte_idx = i
                        if "Move_COUNT" in stripped:
                            sentinel_idx = i
                    elif stripped and not stripped.startswith("@") and not stripped.startswith("."):
                        if last_4byte_idx >= 0:
                            break

            if last_4byte_idx < 0:
                return -1

            # Build new .4byte lines
            new_entries = []
            for const, info in sorted(new_moves.items(), key=lambda x: x[1].get("id", 0)):
                anim = info.get("animation", "Move_POUND")
                if not anim:
                    anim = "Move_POUND"
                new_entries.append(f"\t.4byte {anim}\n")

            # Insert before the Move_COUNT sentinel if present, otherwise
            # after the last .4byte line.
            insert_idx = sentinel_idx if sentinel_idx >= 0 else last_4byte_idx + 1
            for entry in reversed(new_entries):
                lines.insert(insert_idx, entry)

            with open(path, "w", encoding="utf-8", newline="\n") as f:
                f.writelines(lines)
            return len(new_moves)
        except Exception:
            return -1

    # ── Direct moves header writer ───────────────────────────────────
    # Bypasses the broken ReadSourceFile/WriteSourceFile pipeline.
    # Reads each learnset header directly, patches species blocks from
    # the in-memory moves data, writes back.

    def _write_moves_headers(self) -> int:
        """Patch all learnset C headers with current in-memory moves data.

        Returns total number of species updated across all files, or -1 on error.
        """
        import re

        root = self.project_info.get("dir", "")
        if not root:
            return -1

        try:
            pm = self.source_data.data["pokemon_moves"]
            species_moves = pm.data.get("species_moves") or {}
        except Exception:
            return -1

        if not species_moves:
            return 0

        def _method(entry):
            return str(entry.get("method") or "").upper()

        def _camel(base):
            parts = base.lower().split("_")
            return "".join(p.capitalize() for p in parts if p)

        def _species_base(spec):
            return spec[len("SPECIES_"):] if spec.startswith("SPECIES_") else spec

        total = 0

        # ── Level-up learnsets ──────────────────────────────────────
        lvl_path = os.path.join(root, "src", "data", "pokemon", "level_up_learnsets.h")
        ptr_path = os.path.join(root, "src", "data", "pokemon", "level_up_learnset_pointers.h")
        if os.path.isfile(lvl_path) and os.path.isfile(ptr_path):
            try:
                with open(lvl_path, "r", encoding="utf-8") as f:
                    lvl_text = f.read()
                with open(ptr_path, "r", encoding="utf-8") as f:
                    ptr_text = f.read()

                # Map species -> symbol from pointers
                ptr_pat = re.compile(r"\[(SPECIES_[A-Z0-9_]+)\]\s*=\s*(s\w+LevelUpLearnset)")
                sp_to_sym = {m.group(1): m.group(2) for m in ptr_pat.finditer(ptr_text)}

                for sp, entries in species_moves.items():
                    lvl_entries = [e for e in entries if _method(e) == "LEVEL"]
                    if not lvl_entries:
                        continue
                    sym = sp_to_sym.get(sp)
                    if not sym:
                        base = _species_base(sp)
                        sym = f"s{_camel(base)}LevelUpLearnset"
                        # Ensure pointer table references the symbol
                        def _rep_ptr(m, _sp=sp, _sym=sym):
                            return f"[{m.group(1)}] = {_sym}" if m.group(1) == _sp else m.group(0)
                        ptr_text = re.sub(r"\[(SPECIES_[A-Z0-9_]+)\]\s*=\s*(\w+)", _rep_ptr, ptr_text)

                    # Build new array body
                    body_lines = []
                    for ent in sorted(lvl_entries, key=lambda d: int(d.get("value") or 0)):
                        lv = int(ent.get("value") or 0)
                        mv = ent.get("move") or "MOVE_NONE"
                        body_lines.append(f"    LEVEL_UP_MOVE({lv}, {mv}),")
                    body_lines.append("    LEVEL_UP_END")
                    new_block = (
                        f"static const u16 {sym}[] = {{\n"
                        + "\n".join(body_lines) + "\n};"
                    )
                    # Replace existing block
                    arr_pat = re.compile(
                        rf"static\s+const\s+(?:struct\s+LevelUpMove|u16)\s+{re.escape(sym)}\[\]\s*=\s*\{{.*?\}};",
                        re.S,
                    )
                    if arr_pat.search(lvl_text):
                        lvl_text = arr_pat.sub(new_block, lvl_text)
                        total += 1
                    else:
                        lvl_text = lvl_text.rstrip() + "\n\n" + new_block + "\n"
                        total += 1

                with open(lvl_path, "w", encoding="utf-8", newline="\n") as f:
                    f.write(lvl_text)
                with open(ptr_path, "w", encoding="utf-8", newline="\n") as f:
                    f.write(ptr_text)
            except Exception:
                pass

        # ── TM/HM learnsets ─────────────────────────────────────────
        tm_path = os.path.join(root, "src", "data", "pokemon", "tmhm_learnsets.h")
        if os.path.isfile(tm_path):
            try:
                with open(tm_path, "r", encoding="utf-8") as f:
                    tm_text = f.read()

                def _rebuild_tm_expr(sp):
                    entries = [e for e in species_moves.get(sp, []) if _method(e) in ("TM", "HM")]
                    if not entries:
                        return None
                    tokens = []
                    for e in entries:
                        kind = str(e.get("value") or "").strip().upper()
                        mv = str(e.get("move") or "MOVE_NONE")
                        base = mv[len("MOVE_"):] if mv.startswith("MOVE_") else mv
                        if kind:
                            tokens.append(f"TMHM({kind}_{base})")
                    return ("TMHM_LEARNSET(" +
                            "\n                                        | ".join(sorted(tokens)) +
                            ")") if tokens else None

                def _tm_sub(m):
                    sp = m.group(1)
                    expr = _rebuild_tm_expr(sp)
                    if expr:
                        nonlocal total
                        total += 1
                        return f"[{sp}]        = {expr},"
                    return m.group(0)

                tm_text = re.sub(
                    r"\[(SPECIES_[A-Z0-9_]+)\]\s*=\s*TMHM_LEARNSET\((?:.|\n)*?\)\,",
                    _tm_sub,
                    tm_text,
                )
                with open(tm_path, "w", encoding="utf-8", newline="\n") as f:
                    f.write(tm_text)
            except Exception:
                pass

        # ── Tutor learnsets ─────────────────────────────────────────
        tut_path = os.path.join(root, "src", "data", "pokemon", "tutor_learnsets.h")
        if os.path.isfile(tut_path):
            try:
                with open(tut_path, "r", encoding="utf-8") as f:
                    tut_text = f.read()

                # Only patch the sTutorLearnsets array, not sTutorMoves
                # Split at the array declaration
                tut_arr_start = tut_text.find("sTutorLearnsets[]")
                if tut_arr_start != -1:
                    tut_header = tut_text[:tut_arr_start]
                    tut_body = tut_text[tut_arr_start:]

                    def _rebuild_tutor_line(sp):
                        entries = [e for e in species_moves.get(sp, []) if _method(e) == "TUTOR"]
                        if not entries:
                            return None
                        tokens = [f"TUTOR({e.get('move')})" for e in entries if e.get("move")]
                        return (" " + "\n                         | ".join(sorted(tokens))) if tokens else None

                    def _tut_sub(m):
                        sp = m.group(1)
                        repl = _rebuild_tutor_line(sp)
                        if repl:
                            nonlocal total
                            total += 1
                            return f"[{sp}] ={repl},"
                        return m.group(0)

                    # Match multi-line tutor entries: [SPECIES_X] = TUTOR(...)
                    #                                              | TUTOR(...),
                    tut_body = re.sub(
                        r"\[(SPECIES_[A-Z0-9_]+)\]\s*=\s*(?:TUTOR\([^)]*\)\s*\|?\s*)+,",
                        _tut_sub,
                        tut_body,
                    )
                    tut_text = tut_header + tut_body

                with open(tut_path, "w", encoding="utf-8", newline="\n") as f:
                    f.write(tut_text)
            except Exception:
                pass

        # ── Egg moves ───────────────────────────────────────────────
        egg_path = os.path.join(root, "src", "data", "pokemon", "egg_moves.h")
        if os.path.isfile(egg_path):
            try:
                with open(egg_path, "r", encoding="utf-8") as f:
                    egg_text = f.read()

                egg_map = {}
                for sp, entries in species_moves.items():
                    base = _species_base(sp)
                    ml = [e.get("move") for e in entries if _method(e) == "EGG" and e.get("move")]
                    if ml:
                        egg_map[base] = sorted(set(ml))

                for base, mv_list in egg_map.items():
                    lines = [f"egg_moves({base},"]
                    for mv in mv_list:
                        lines.append(f"              {mv},")
                    if len(lines) > 1:
                        lines[-1] = lines[-1].rstrip(",")
                    lines.append(")")
                    new_block = "\n".join(lines)
                    pat = re.compile(rf"egg_moves\(\s*{re.escape(base)}\s*,[\s\S]*?\)")
                    if pat.search(egg_text):
                        egg_text = pat.sub(new_block, egg_text)
                        total += 1
                    else:
                        idx = egg_text.rfind("EGG_MOVES_TERMINATOR")
                        if idx == -1:
                            idx = egg_text.rfind("};")
                        if idx != -1:
                            egg_text = egg_text[:idx] + new_block + ",\n\n    " + egg_text[idx:]
                        else:
                            egg_text = egg_text.rstrip() + "\n" + new_block + ",\n"
                        total += 1

                with open(egg_path, "w", encoding="utf-8", newline="\n") as f:
                    f.write(egg_text)
            except Exception:
                pass

        return total

    def _write_items_header(self) -> int:
        """Regenerate src/data/items.h from items.json, bypassing the plugin pipeline.

        Returns the number of items written, or -1 on error.
        """
        import json as _json

        proj_dir = self.source_data.project_info.get("dir", "")
        json_path = os.path.join(proj_dir, "src", "data", "items.json")
        header_path = os.path.join(proj_dir, "src", "data", "items.h")

        if not os.path.isfile(json_path):
            return -1

        try:
            with open(json_path, "r", encoding="utf-8") as f:
                raw = _json.load(f)
        except Exception:
            return -1

        items = raw if isinstance(raw, list) else raw.get("items", [])
        if not items:
            return -1

        # Build description lines and gItems[] body following the Inja template
        desc_parts: list[str] = []
        body_parts: list[str] = []

        for item in items:
            item_id = item.get("itemId", "ITEM_NONE")
            pocket = item.get("pocket", "")
            move_id = item.get("moveId", "")

            # --- description string declarations ---
            if pocket == "POCKET_TM_CASE" and move_id:
                desc_parts.append(f"extern const u8 gMoveDescription_{move_id}[];")
            if item_id != "ITEM_NONE":
                desc_raw = item.get("description_english", "?????")
                # The JSON stores \n as literal backslash-n; keep as-is for _()
                desc_parts.append(
                    f'const u8 gItemDescription_{item_id}[] = _("{desc_raw}");'
                )

        # ITEM_NONE sentinel description at the end
        desc_parts.append('const u8 gItemDescription_ITEM_NONE[] = _("?????");')

        for item in items:
            item_id = item.get("itemId", "ITEM_NONE")
            pocket = item.get("pocket", "")
            move_id = item.get("moveId", "")

            # --- description reference for gItems[] ---
            if pocket == "POCKET_TM_CASE" and move_id:
                desc_ref = f"gMoveDescription_{move_id}"
            else:
                desc_ref = f"gItemDescription_{item_id}"

            block = (
                f"    {{\n"
                f'        .name = _("{item.get("english", "????????")}"),\n'
                f"        .itemId = {item_id},\n"
                f"        .price = {item.get('price', 0)},\n"
                f"        .holdEffect = {item.get('holdEffect', 'HOLD_EFFECT_NONE')},\n"
                f"        .holdEffectParam = {item.get('holdEffectParam', 0)},\n"
                f"        .description = {desc_ref},\n"
                f"        .importance = {item.get('importance', 0)},\n"
                f"        .registrability = {item.get('registrability', 0)},\n"
                f"        .pocket = {pocket or 'POCKET_ITEMS'},\n"
                f"        .type = {item.get('type', 'ITEM_TYPE_BAG_MENU')},\n"
                f"        .fieldUseFunc = {item.get('fieldUseFunc', 'NULL')},\n"
                f"        .battleUsage = {item.get('battleUsage', 0)},\n"
                f"        .battleUseFunc = {item.get('battleUseFunc', 'NULL')},\n"
                f"        .secondaryId = {item.get('secondaryId', 0)}\n"
                f"    }}"
            )
            body_parts.append(block)

        header_comment = (
            "//\n"
            "// DO NOT MODIFY THIS FILE! It is auto-generated from "
            "src/data/items.json and Inja template src/data/items.json.txt\n"
            "//\n\n"
        )

        desc_line = "".join(desc_parts)
        items_body = ", ".join(body_parts)

        out = (
            header_comment
            + desc_line + "\n\n"
            + "const struct Item gItems[] = {\n"
            + items_body + ", };\n"
        )

        try:
            with open(header_path, "w", encoding="utf-8", newline="\n") as f:
                f.write(out)
        except Exception:
            return -1

        return len(items)

    def rebuild_caches(self):
        """Re-extract all data files and reload the project.

        Also clears sprite/icon caches and force-reloads the Trainers and Moves
        tabs so all editors reflect the rebuilt data immediately.
        """
        if not self.source_data:
            return
        self.log("Rebuilding caches...")

        # Clear in-memory sprite/icon caches
        if hasattr(self, "_species_icon_cache"):
            self._species_icon_cache.clear()
        try:
            self.items_editor._icon_cache.clear()
        except Exception:
            pass

        original_clear = getattr(self.source_data, "clear_caches", None)
        self._clear_plugin_cache_files()
        try:
            self.source_data.clear_caches = lambda: self._clear_plugin_cache_files()
        except Exception:
            original_clear = None
        try:
            self.source_data.rebuild_caches(self.log)
        finally:
            if original_clear is not None:
                try:
                    self.source_data.clear_caches = original_clear
                except Exception:
                    pass
            else:
                try:
                    delattr(self.source_data, "clear_caches")
                except Exception:
                    pass
        self.load_data(self.project_info)

        # Restore any stashed species edits that the extractor could not
        # recover from the header files alone (safety net).
        self._restore_species_edits()

        # Force-reload lazy tabs (normally only load on tab-switch)
        try:
            self._load_trainers_editor()
        except Exception:
            pass
        try:
            self.load_moves_defs_table()
        except Exception:
            pass

        self.statusBar().showMessage("Caches rebuilt", 5000)


    def _clear_plugin_cache_files(self, skip: set[str] | None = None) -> None:
        """Remove cache JSON files managed by the plugin, skipping protected entries.

        The entire species_info dict for every species is stashed before
        deletion.  After ``load_data`` re-extracts from headers,
        ``_restore_species_edits`` compares stashed values against the fresh
        extraction and restores any user edits that differ from vanilla.
        """
        if not self.source_data:
            return
        try:
            root = LocalUtil(self.project_info).repo_root()
        except Exception:
            root = self.project_info.get("dir")
        if not root:
            return

        # ── Stash the full species_info dict for every species ──
        self._stashed_species_edits = {}
        try:
            import json as _json, copy as _copy
            sp_path = os.path.join(root, "src", "data", "species.json")
            if os.path.isfile(sp_path):
                with open(sp_path, encoding="utf-8") as _f:
                    _sp = _json.load(_f)
                if isinstance(_sp, dict):
                    for sp, sp_data in _sp.items():
                        si = sp_data.get("species_info", {}) if isinstance(sp_data, dict) else {}
                        if si:
                            self._stashed_species_edits[sp] = _copy.deepcopy(si)
        except Exception:
            pass

        data_map = getattr(self.source_data, "data", {}) or {}
        protected = {"items.json"}
        if skip:
            protected.update(skip)
        targets: set[str] = set()
        for data_obj in data_map.values():
            data_file = getattr(data_obj, "DATA_FILE", None)
            if not data_file or data_file in protected:
                continue
            targets.add(os.path.join(root, "src", "data", data_file))
        for path in targets:
            try:
                os.remove(path)
            except OSError:
                pass

    def _restore_species_edits(self) -> None:
        """Re-apply stashed species edits after a full data reload.

        The stash is populated by ``_clear_plugin_cache_files`` before it
        deletes species.json.  After ``load_data`` re-runs (from either
        rebuild_caches or a rename reload), this method compares each stashed
        species_info dict against the freshly extracted one and restores any
        fields where the user's value differs from vanilla (meaning the user
        edited it).
        """
        stash = getattr(self, "_stashed_species_edits", None)
        if not stash:
            return
        try:
            sp_data_obj = self.source_data.data.get("species_data")
            if sp_data_obj is None:
                return
            restored = 0
            for sp, stashed_si in stash.items():
                fresh_si = sp_data_obj.data.get(sp, {}).get("species_info")
                if fresh_si is None:
                    continue
                for key, stashed_val in stashed_si.items():
                    fresh_val = fresh_si.get(key)
                    # Restore if the stashed value differs from what the
                    # extractor pulled from headers — that means the user
                    # changed it.  Skip None/empty stashed values.
                    if stashed_val and stashed_val != fresh_val:
                        fresh_si[key] = stashed_val
                        restored += 1
            if restored:
                # Flush to disk so subsequent reloads don't lose the values.
                try:
                    sp_data_obj.save()
                except Exception:
                    pass
                print(f"Restored {restored} stashed species edits")
        except Exception:
            pass
        finally:
            self._stashed_species_edits = {}

    def eventFilter(self, watched, event):
        """
        Filters and handles events for the specified watched object.

        Parameters:
            watched: The object being watched for events.
            event: The event being filtered.

        Returns:
            bool: True if the event is handled, False otherwise.
        """
        # Handle events on species_description object to limit the number of lines and characters per line
        if watched == self.ui.species_description:
            # Check if the event is a Paste KeySequence
            if (
                event.type() == QEvent.Type.KeyPress
                or event.type() == QEvent.Type.ShortcutOverride
            ):
                if not isinstance(event, QKeyEvent):
                    return super().eventFilter(watched, event)
                key_event = event
                key_text = key_event.text()
                clipboard = QApplication.clipboard().text()
                clipboard_newline = "\n" in clipboard or "\r" in clipboard
                # Check if the key is Enter, Return, or Paste with newline characters in clipboard
                if (
                    key_event.key() == Qt.Key.Key_Enter
                    or key_event.key() == Qt.Key.Key_Return
                    or (
                        key_event.matches(QKeySequence.StandardKey.Paste)
                        and clipboard_newline
                    )
                ):
                    if self.ui.species_description.blockCount() == 4:
                        return True
                # Check if the key is a letter, number, space, or letter with an accent
                elif key_text.isprintable() or key_event.matches(
                    QKeySequence.StandardKey.Paste
                ):
                    # Get selection length
                    cursor = self.ui.species_description.textCursor()
                    selection_length = cursor.selectionEnd() - cursor.selectionStart()
                    # Check if there is no selection and the key is not Backspace or Delete
                    if (
                        selection_length == 0
                        and not key_event.key() == Qt.Key.Key_Backspace
                        and not key_event.key() == Qt.Key.Key_Delete
                    ):
                        # Get the current line from the text cursor
                        text = cursor.block().text()
                        # Check if the line is already at the maximum length
                        if len(text) >= getattr(self, 'description_max_chars_per_line', 48):
                            if key_event.matches(QKeySequence.StandardKey.Paste):
                                self.ui.statusbar.showMessage(
                                    "Contents of clipboard are too long to paste.", 5000
                                )
                            return True
                        # Check if pasting the clipboard content will exceed the maximum length
                        elif (
                            key_event.matches(QKeySequence.StandardKey.Paste)
                            and len(text) + len(clipboard) > getattr(self, 'description_max_chars_per_line', 48)
                        ):
                            self.ui.statusbar.showMessage(
                                "Contents of clipboard are too long to paste.", 5000
                            )
                            return True
        # Handle events on other watched objects
        elif (
            watched == self.ui.ability1
            or watched == self.ui.ability2
            or watched == self.ui.held_item_common
            or watched == self.ui.held_item_rare
            or watched == self.ui.evo_species
            or watched == self.ui.starter1_species
            or watched == self.ui.starter2_species
            or watched == self.ui.starter3_species
            or watched == self.ui.starter1_item
            or watched == self.ui.starter2_item
            or watched == self.ui.starter3_item
        ):
            if (
                event.type() == QEvent.Type.KeyPress
                or event.type() == QEvent.Type.ShortcutOverride
            ):
                if not isinstance(event, QKeyEvent):
                    return super().eventFilter(watched, event)
                key_event = event
                # Check if the key is Enter or Return
                if (
                    key_event.key() == Qt.Key.Key_Enter
                    or key_event.key() == Qt.Key.Key_Return
                ):
                    # Find closest match to the text in the combo box
                    text = watched.currentText()
                    for i in range(watched.count()):
                        if text.lower() in watched.itemText(i).strip().lower():
                            watched.setCurrentIndex(i)
                            watched.setEditText(watched.itemText(i))
                            break
                    return True
            elif event.type() == QEvent.Type.FocusIn:
                if watched == self.ui.evo_species and watched.currentIndex() == 0:
                    self.ui.evo_species.clearEditText()
        return super().eventFilter(watched, event)

    def log(self, message):
        """
        Appends the given message to the log output widget.

        Parameters:
            message (str): The message to be logged.
        """
        self.ui.logOutput.append(message)

    def try_save_before_closing(self):
        """
        Saves the project before closing the application.

        This method opens a dialog asking to save first, and then closes the application if the user chooses to do so.
        """
        if self.isWindowModified():
            # Open dialog asking to save first
            ret = app_util.create_unsaved_changes_dialog(
                self,
                "You have unsaved changes. Would you like to save before exiting?",
            )
            if ret == QMessageBox.StandardButton.Save:
                self.update_save()
            return ret != QMessageBox.StandardButton.Cancel
        return True

    def closeEvent(self, event):
        """
        Closes the application.

        This method is called when the application is about to close.
        It calls the save_before_closing method to save the project before closing the application.
        """
        if self.try_save_before_closing():
            event.accept()
        else:
            event.ignore()
