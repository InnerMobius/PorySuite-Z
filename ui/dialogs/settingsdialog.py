"""settingsdialog.py — PorySuite-Z Settings dialog.

Sidebar-based layout: categories on the left, settings pages on the right.
All settings persisted to data/settings.ini via QSettings (INI format).
"""

import os
import textwrap
from collections import defaultdict

from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QCheckBox, QGroupBox, QScrollArea, QWidget, QSizePolicy,
    QListWidget, QListWidgetItem, QStackedWidget, QLineEdit,
    QSpinBox, QFormLayout, QTextEdit, QFileDialog, QComboBox,
    QFrame,
)
from PyQt6.QtCore import Qt, QSettings, QSize
from PyQt6.QtGui import QFont

from app_info import get_settings_path
from suppress_dialog import SUPPRESSIBLE, suppress, is_suppressed, clear_all


class SettingsDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Settings — PorySuite-Z")
        self.setModal(True)
        self.resize(720, 560)
        self.setMinimumSize(640, 480)

        os.makedirs(os.path.dirname(get_settings_path()), exist_ok=True)
        self.settings = QSettings(get_settings_path(), QSettings.Format.IniFormat)

        # ── Main layout: sidebar + stacked pages ────────────────────────────
        root = QVBoxLayout(self)
        root.setSpacing(8)

        body = QHBoxLayout()
        body.setSpacing(0)
        root.addLayout(body, 1)

        # ── Left sidebar ────────────────────────────────────────────────────
        self._sidebar = QListWidget()
        self._sidebar.setFixedWidth(170)
        self._sidebar.setFont(QFont("Segoe UI", 10))
        self._sidebar.setStyleSheet("""
            QListWidget {
                border: none;
                border-right: 1px solid palette(mid);
                background: palette(window);
                outline: none;
            }
            QListWidget::item {
                padding: 8px 12px;
                border: none;
            }
            QListWidget::item:selected {
                background: palette(highlight);
                color: palette(highlighted-text);
            }
        """)
        body.addWidget(self._sidebar)

        # ── Right content area ──────────────────────────────────────────────
        self._pages = QStackedWidget()
        body.addWidget(self._pages, 1)

        self._sidebar.currentRowChanged.connect(self._pages.setCurrentIndex)

        # ── Build each settings page ────────────────────────────────────────
        self._build_general_page()
        self._build_build_page()
        self._build_trainer_defaults_page()
        self._build_editor_page()
        self._build_event_colors_page()
        self._build_notifications_page()

        # Select first page
        self._sidebar.setCurrentRow(0)

        # ── Dialog buttons ──────────────────────────────────────────────────
        btn_line = QFrame()
        btn_line.setFrameShape(QFrame.Shape.HLine)
        btn_line.setFrameShadow(QFrame.Shadow.Sunken)
        root.addWidget(btn_line)

        btn_layout = QHBoxLayout()
        btn_layout.addStretch(1)
        ok = QPushButton("OK")
        cancel = QPushButton("Cancel")
        ok.setDefault(True)
        ok.clicked.connect(self.accept)
        cancel.clicked.connect(self.reject)
        btn_layout.addWidget(ok)
        btn_layout.addWidget(cancel)
        root.addLayout(btn_layout)

    # ═════════════════════════════════════════════════════════════════════════
    # Helper: add a page with sidebar entry
    # ═════════════════════════════════════════════════════════════════════════

    def _add_page(self, title: str, widget: QWidget):
        """Add a page to the stacked widget and a corresponding sidebar entry."""
        item = QListWidgetItem(title)
        item.setSizeHint(QSize(160, 36))
        self._sidebar.addItem(item)
        self._pages.addWidget(widget)

    def _make_page_scroll(self) -> tuple:
        """Create a scrollable page container. Returns (scroll_widget, inner_layout)."""
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        inner = QWidget()
        layout = QVBoxLayout(inner)
        layout.setSpacing(14)
        layout.setContentsMargins(16, 12, 16, 12)
        scroll.setWidget(inner)
        return scroll, layout

    # ═════════════════════════════════════════════════════════════════════════
    # Page: General
    # ═════════════════════════════════════════════════════════════════════════

    def _build_general_page(self):
        scroll, layout = self._make_page_scroll()

        # ── Diagnostics ─────────────────────────────────────────────────────
        diag_box = QGroupBox("Advanced Diagnostics")
        diag_lay = QVBoxLayout(diag_box)
        diag_lay.addWidget(QLabel(
            "Show verbose internal diagnostic messages (types/gender parsing).\n"
            "These are noisy and intended for debugging. Enable only when requested."
        ))
        self.adv_checkbox = QCheckBox("Enable Advanced Diagnostics")
        self.adv_checkbox.setChecked(
            bool(self.settings.value("advanced_diagnostics", False, type=bool))
        )
        diag_lay.addWidget(self.adv_checkbox)
        layout.addWidget(diag_box)

        # ── Crashlog Retention ───────────────────────────────────────────────
        log_box = QGroupBox("Crashlog Retention")
        log_lay = QFormLayout(log_box)
        log_lay.addRow(QLabel(
            "Crashlogs are auto-purged every time the app starts.\n"
            "Old files beyond the retention period are deleted first,\n"
            "then if total size still exceeds the cap, the oldest are removed."
        ))

        self.crashlog_days_combo = QComboBox()
        self._crashlog_day_values = [1, 3, 7, 14, 30]
        for d in self._crashlog_day_values:
            self.crashlog_days_combo.addItem(f"{d} day{'s' if d != 1 else ''}")
        saved_days = self.settings.value("crashlog/keep_days", 3, type=int)
        idx = self._crashlog_day_values.index(saved_days) if saved_days in self._crashlog_day_values else 1
        self.crashlog_days_combo.setCurrentIndex(idx)
        log_lay.addRow("Keep logs for:", self.crashlog_days_combo)

        self.crashlog_size_combo = QComboBox()
        self._crashlog_size_values = [50, 100, 250, 500, 0]
        self._crashlog_size_labels = ["50 MB", "100 MB", "250 MB", "500 MB", "Unlimited"]
        self.crashlog_size_combo.addItems(self._crashlog_size_labels)
        saved_mb = self.settings.value("crashlog/max_size_mb", 100, type=int)
        if saved_mb in self._crashlog_size_values:
            self.crashlog_size_combo.setCurrentIndex(self._crashlog_size_values.index(saved_mb))
        else:
            self.crashlog_size_combo.setCurrentIndex(1)  # default 100 MB
        log_lay.addRow("Max total size:", self.crashlog_size_combo)

        layout.addWidget(log_box)

        # ── Autosave ────────────────────────────────────────────────────────
        auto_box = QGroupBox("Autosave (Experimental)")
        auto_lay = QVBoxLayout(auto_box)
        auto_lay.addWidget(QLabel(
            "Automatically save project changes.\n"
            "THIS IS EXPERIMENTAL — keep backups before enabling."
        ))
        self.auto_checkbox = QCheckBox("Enable Autosave (experimental)")
        self.auto_checkbox.setChecked(
            bool(self.settings.value("autosave_enabled", False, type=bool))
        )
        auto_lay.addWidget(self.auto_checkbox)
        layout.addWidget(auto_box)

        layout.addStretch(1)
        self._add_page("General", scroll)

    # ═════════════════════════════════════════════════════════════════════════
    # Page: Build & Play
    # ═════════════════════════════════════════════════════════════════════════

    def _build_build_page(self):
        scroll, layout = self._make_page_scroll()

        # ── Build commands ──────────────────────────────────────────────────
        build_box = QGroupBox("Build Commands")
        build_lay = QFormLayout(build_box)

        build_lay.addRow(QLabel(
            "Commands used by the Make and Make Modern toolbar buttons.\n"
            "These run inside MSYS2. Change only if you know what you're doing."
        ))

        self.make_cmd_edit = QLineEdit(
            self.settings.value("build/make_command", "make", type=str))
        self.make_cmd_edit.setPlaceholderText("make")
        build_lay.addRow("Make command:", self.make_cmd_edit)

        self.make_modern_cmd_edit = QLineEdit(
            self.settings.value("build/make_modern_command", "make MODERN=1", type=str))
        self.make_modern_cmd_edit.setPlaceholderText("make MODERN=1")
        build_lay.addRow("Make Modern command:", self.make_modern_cmd_edit)

        layout.addWidget(build_box)

        # ── Play / Launch ───────────────────────────────────────────────────
        play_box = QGroupBox("Play / Launch")
        play_lay = QFormLayout(play_box)

        play_lay.addRow(QLabel(
            "Which .gba file to launch when you press the Play button,\n"
            "and which emulator to open it with."
        ))

        self.gba_combo = QComboBox()
        self.gba_combo.addItems([
            "pokefirered_modern.gba (Make Modern output)",
            "pokefirered.gba (Make output)",
        ])
        saved_gba = self.settings.value("build/gba_file", "pokefirered_modern.gba", type=str)
        if "modern" not in saved_gba:
            self.gba_combo.setCurrentIndex(1)
        play_lay.addRow("ROM file:", self.gba_combo)

        emu_row = QHBoxLayout()
        self.emulator_edit = QLineEdit(
            self.settings.value("build/emulator_path", "", type=str))
        self.emulator_edit.setPlaceholderText("(use Windows default program)")
        emu_row.addWidget(self.emulator_edit)
        emu_browse = QPushButton("Browse...")
        emu_browse.clicked.connect(self._browse_emulator)
        emu_row.addWidget(emu_browse)
        play_lay.addRow("Emulator:", emu_row)

        layout.addWidget(play_box)

        # ── Build Environment ───────────────────────────────────────────────
        setup_box = QGroupBox("Build Environment")
        setup_lay = QVBoxLayout(setup_box)
        setup_lay.addWidget(QLabel(
            "Check and install the tools required to build GBA ROMs:\n"
            "MSYS2, devkitPro, agbcc, and more."
        ))
        btn_setup = QPushButton("Open Setup Wizard...")
        btn_setup.clicked.connect(self._open_setup)
        setup_lay.addWidget(btn_setup)
        layout.addWidget(setup_box)

        layout.addStretch(1)
        self._add_page("Build && Play", scroll)

    # ═════════════════════════════════════════════════════════════════════════
    # Page: Trainer Defaults
    # ═════════════════════════════════════════════════════════════════════════

    def _build_trainer_defaults_page(self):
        scroll, layout = self._make_page_scroll()

        info_label = QLabel(
            "Default text for new trainers. When you create a trainer,\n"
            "these are filled in automatically so the game doesn't crash\n"
            "from missing text labels. You can change them per-trainer later."
        )
        layout.addWidget(info_label)

        # ── Default dialogue ────────────────────────────────────────────────
        text_box = QGroupBox("Default Battle Dialogue")
        text_lay = QFormLayout(text_box)

        self.default_intro = QLineEdit(
            self.settings.value("trainer_defaults/intro_text",
                                "Let's battle!$", type=str))
        self.default_intro.setPlaceholderText("Let's battle!$")
        text_lay.addRow("Intro text:", self.default_intro)

        self.default_defeat = QLineEdit(
            self.settings.value("trainer_defaults/defeat_text",
                                "I lost...$", type=str))
        self.default_defeat.setPlaceholderText("I lost...$")
        text_lay.addRow("Defeat text:", self.default_defeat)

        self.default_post_battle = QLineEdit(
            self.settings.value("trainer_defaults/post_battle_text",
                                "Good fight.$", type=str))
        self.default_post_battle.setPlaceholderText("Good fight.$")
        text_lay.addRow("Post-battle text:", self.default_post_battle)

        text_lay.addRow(QLabel(
            "The $ at the end closes the text box in-game.\n"
            "Use \\n for line breaks and \\p for paragraph breaks."
        ))

        layout.addWidget(text_box)

        # ── Default money ───────────────────────────────────────────────────
        money_box = QGroupBox("Prize Money")
        money_lay = QFormLayout(money_box)

        money_lay.addRow(QLabel(
            "Base prize money multiplier for new trainers.\n"
            "Actual payout = base * trainer's last Pokemon's level."
        ))

        self.default_money = QSpinBox()
        self.default_money.setRange(0, 255)
        self.default_money.setValue(
            self.settings.value("trainer_defaults/money_multiplier", 20, type=int))
        money_lay.addRow("Base multiplier:", self.default_money)

        layout.addWidget(money_box)

        layout.addStretch(1)
        self._add_page("Trainer Defaults", scroll)

    # ═════════════════════════════════════════════════════════════════════════
    # Page: Editor Preferences
    # ═════════════════════════════════════════════════════════════════════════

    def _build_editor_page(self):
        scroll, layout = self._make_page_scroll()

        # ── Startup ─────────────────────────────────────────────────────────
        startup_box = QGroupBox("Startup")
        startup_lay = QFormLayout(startup_box)

        self.startup_page_combo = QComboBox()
        pages = [
            "Pokemon", "Pokedex", "Moves", "Items", "Trainers", "Starters",
            "Event Editor", "Maps", "Layouts & Tilesets", "Region Map",
            "UI Settings", "Config",
        ]
        self.startup_page_combo.addItems(pages)
        saved_page = self.settings.value("editor/startup_page", "Pokemon", type=str)
        idx = self.startup_page_combo.findText(saved_page)
        if idx >= 0:
            self.startup_page_combo.setCurrentIndex(idx)
        startup_lay.addRow("Open to this page on launch:", self.startup_page_combo)

        layout.addWidget(startup_box)

        # ── Log panel ───────────────────────────────────────────────────────
        log_box = QGroupBox("Log Panel")
        log_lay = QVBoxLayout(log_box)

        self.log_visible_checkbox = QCheckBox("Show log panel on startup")
        self.log_visible_checkbox.setChecked(
            bool(self.settings.value("editor/log_visible", True, type=bool))
        )
        log_lay.addWidget(self.log_visible_checkbox)

        layout.addWidget(log_box)

        # ── Event Editor Tooltips ───────────────────────────────────────────
        tooltip_box = QGroupBox("Event Editor Tooltips")
        tooltip_lay = QVBoxLayout(tooltip_box)
        tooltip_lay.addWidget(QLabel(
            "Show descriptive tooltips on Event Editor controls,\n"
            "command palette buttons, and edit dialog fields."
        ))

        self.event_tooltips_checkbox = QCheckBox("Show Event Editor tooltips")
        self.event_tooltips_checkbox.setChecked(
            bool(self.settings.value("editor/event_tooltips", True, type=bool))
        )
        tooltip_lay.addWidget(self.event_tooltips_checkbox)

        layout.addWidget(tooltip_box)

        # ── Paths ───────────────────────────────────────────────────────────
        paths_box = QGroupBox("External Tools")
        paths_lay = QFormLayout(paths_box)

        porymap_row = QHBoxLayout()
        self.porymap_edit = QLineEdit(
            self.settings.value("editor/porymap_path", "", type=str))
        self.porymap_edit.setPlaceholderText("(not set — needed for Phase 4 integration)")
        porymap_row.addWidget(self.porymap_edit)
        porymap_browse = QPushButton("Browse...")
        porymap_browse.clicked.connect(self._browse_porymap)
        porymap_row.addWidget(porymap_browse)
        paths_lay.addRow("Porymap path:", porymap_row)

        layout.addWidget(paths_box)

        layout.addStretch(1)
        self._add_page("Editor", scroll)

    # ═════════════════════════════════════════════════════════════════════════
    # Page: Notifications
    # ═════════════════════════════════════════════════════════════════════════

    def _build_notifications_page(self):
        scroll, layout = self._make_page_scroll()

        layout.addWidget(QLabel(
            "Dialogs marked \"Don't show again\" are listed below.\n"
            "Re-check a box to re-enable that confirmation."
        ))

        self._notif_checks: dict[str, QCheckBox] = {}

        # Group by category
        by_cat: dict[str, list[tuple[str, str]]] = defaultdict(list)
        for key, (label, cat) in SUPPRESSIBLE.items():
            by_cat[cat].append((key, label))

        for cat, entries in sorted(by_cat.items()):
            cat_box = QGroupBox(cat)
            cat_lay = QVBoxLayout(cat_box)
            for key, label in entries:
                cb = QCheckBox(label)
                cb.setChecked(not is_suppressed(key))
                self._notif_checks[key] = cb
                cat_lay.addWidget(cb)
            layout.addWidget(cat_box)

        btn_reset = QPushButton("Re-enable All Notifications")
        btn_reset.clicked.connect(self._reset_all_notifications)
        layout.addWidget(btn_reset)

        layout.addStretch(1)
        self._add_page("Notifications", scroll)

    # ═════════════════════════════════════════════════════════════════════════
    # Page: Event Colors
    # ═════════════════════════════════════════════════════════════════════════

    def _build_event_colors_page(self):
        scroll, layout = self._make_page_scroll()

        layout.addWidget(QLabel(
            "Customize colors used in the Event Editor command list.\n"
            "Each constant type has its own color so flags, vars, trainers,\n"
            "items, species, and moves stand out at a glance."
        ))

        # ── Constant type colors ────────────────────────────────────────────
        type_box = QGroupBox("Constant Type Colors")
        type_lay = QFormLayout(type_box)

        # Default colors — must match _TYPE_DISPLAY_COLORS in event_editor_tab.py
        self._color_defaults = {
            'flag':    '#2ecc71',
            'var':     '#3498db',
            'trainer': '#e74c3c',
            'item':    '#f39c12',
            'species': '#9b59b6',
            'move':    '#1abc9c',
        }
        self._color_buttons: dict[str, QPushButton] = {}

        for key, default in self._color_defaults.items():
            saved = self.settings.value(f"event_colors/{key}", default)
            btn = QPushButton(f'  {key.title()}  ')
            btn.setStyleSheet(
                f'background: {saved}; color: white; font-weight: bold; '
                f'border: 1px solid #555; border-radius: 3px; padding: 4px 12px;')
            btn.setProperty('color_key', key)
            btn.setProperty('current_color', saved)
            btn.clicked.connect(lambda _, b=btn: self._pick_color(b))
            self._color_buttons[key] = btn
            label = {'flag': 'Flags', 'var': 'Variables', 'trainer': 'Trainers',
                     'item': 'Items', 'species': 'Species', 'move': 'Moves'}
            type_lay.addRow(f'{label.get(key, key)}:', btn)

        layout.addWidget(type_box)

        # ── Command category colors ─────────────────────────────────────────
        cat_box = QGroupBox("Command Category Colors")
        cat_lay = QFormLayout(cat_box)

        self._cat_color_defaults = {
            'dialogue':  '#2980b9',
            'flag_var':  '#8e44ad',
            'flow':      '#c0392b',
            'movement':  '#8b2252',
            'sound':     '#d35400',
            'screen':    '#16a085',
            'battle':    '#e74c3c',
            'pokemon':   '#f39c12',
            'item_cmd':  '#2ecc71',
            'system':    '#7f8c8d',
        }
        self._cat_color_buttons: dict[str, QPushButton] = {}

        cat_labels = {
            'dialogue': 'Dialogue & Text',
            'flag_var': 'Flags & Variables',
            'flow': 'Flow Control',
            'movement': 'Movement & Warps',
            'sound': 'Sound & Music',
            'screen': 'Screen Effects',
            'battle': 'Battles',
            'pokemon': 'Pokemon',
            'item_cmd': 'Items & Money',
            'system': 'System & Misc',
        }

        for key, default in self._cat_color_defaults.items():
            saved = self.settings.value(f"event_cat_colors/{key}", default)
            btn = QPushButton(f'  {cat_labels.get(key, key)}  ')
            btn.setStyleSheet(
                f'background: {saved}; color: white; font-weight: bold; '
                f'border: 1px solid #555; border-radius: 3px; padding: 4px 12px;')
            btn.setProperty('color_key', key)
            btn.setProperty('current_color', saved)
            btn.clicked.connect(lambda _, b=btn: self._pick_cat_color(b))
            self._cat_color_buttons[key] = btn
            cat_lay.addRow(f'{cat_labels.get(key, key)}:', btn)

        layout.addWidget(cat_box)

        # Reset button
        btn_reset = QPushButton("Reset All to Defaults")
        btn_reset.clicked.connect(self._reset_event_colors)
        layout.addWidget(btn_reset)

        layout.addStretch(1)
        self._add_page("Event Colors", scroll)

    def _pick_color(self, btn):
        from PyQt6.QtWidgets import QColorDialog
        current = btn.property('current_color') or '#ffffff'
        from PyQt6.QtGui import QColor
        color = QColorDialog.getColor(QColor(current), self, "Pick Color")
        if color.isValid():
            hex_color = color.name()
            btn.setProperty('current_color', hex_color)
            btn.setStyleSheet(
                f'background: {hex_color}; color: white; font-weight: bold; '
                f'border: 1px solid #555; border-radius: 3px; padding: 4px 12px;')

    def _pick_cat_color(self, btn):
        from PyQt6.QtWidgets import QColorDialog
        current = btn.property('current_color') or '#ffffff'
        from PyQt6.QtGui import QColor
        color = QColorDialog.getColor(QColor(current), self, "Pick Color")
        if color.isValid():
            hex_color = color.name()
            btn.setProperty('current_color', hex_color)
            btn.setStyleSheet(
                f'background: {hex_color}; color: white; font-weight: bold; '
                f'border: 1px solid #555; border-radius: 3px; padding: 4px 12px;')

    def _reset_event_colors(self):
        for key, default in self._color_defaults.items():
            btn = self._color_buttons[key]
            btn.setProperty('current_color', default)
            btn.setStyleSheet(
                f'background: {default}; color: white; font-weight: bold; '
                f'border: 1px solid #555; border-radius: 3px; padding: 4px 12px;')
        for key, default in self._cat_color_defaults.items():
            btn = self._cat_color_buttons[key]
            btn.setProperty('current_color', default)
            btn.setStyleSheet(
                f'background: {default}; color: white; font-weight: bold; '
                f'border: 1px solid #555; border-radius: 3px; padding: 4px 12px;')

    # ═════════════════════════════════════════════════════════════════════════
    # Actions
    # ═════════════════════════════════════════════════════════════════════════

    def _open_setup(self):
        from programsetup import ProgramSetup
        dlg = ProgramSetup(self)
        dlg.exec()

    def _browse_emulator(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "Select Emulator", "",
            "Executables (*.exe);;All Files (*)")
        if path:
            self.emulator_edit.setText(path)

    def _browse_porymap(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "Select Porymap", "",
            "Executables (*.exe);;All Files (*)")
        if path:
            self.porymap_edit.setText(path)

    def _reset_all_notifications(self):
        clear_all()
        for cb in self._notif_checks.values():
            cb.setChecked(True)

    # ═════════════════════════════════════════════════════════════════════════
    # Save
    # ═════════════════════════════════════════════════════════════════════════

    def accept(self) -> None:
        # General
        self.settings.setValue("advanced_diagnostics", bool(self.adv_checkbox.isChecked()))
        self.settings.setValue("autosave_enabled", bool(self.auto_checkbox.isChecked()))
        self.settings.setValue("crashlog/keep_days",
                               self._crashlog_day_values[self.crashlog_days_combo.currentIndex()])
        self.settings.setValue("crashlog/max_size_mb",
                               self._crashlog_size_values[self.crashlog_size_combo.currentIndex()])

        # Build & Play
        self.settings.setValue("build/make_command", self.make_cmd_edit.text().strip() or "make")
        self.settings.setValue("build/make_modern_command",
                               self.make_modern_cmd_edit.text().strip() or "make MODERN=1")
        gba_file = ("pokefirered_modern.gba" if self.gba_combo.currentIndex() == 0
                     else "pokefirered.gba")
        self.settings.setValue("build/gba_file", gba_file)
        self.settings.setValue("build/emulator_path", self.emulator_edit.text().strip())

        # Trainer Defaults
        self.settings.setValue("trainer_defaults/intro_text",
                               self.default_intro.text().strip() or "Let's battle!$")
        self.settings.setValue("trainer_defaults/defeat_text",
                               self.default_defeat.text().strip() or "I lost...$")
        self.settings.setValue("trainer_defaults/post_battle_text",
                               self.default_post_battle.text().strip() or "Good fight.$")
        self.settings.setValue("trainer_defaults/money_multiplier",
                               self.default_money.value())

        # Editor
        self.settings.setValue("editor/startup_page",
                               self.startup_page_combo.currentText())
        self.settings.setValue("editor/log_visible",
                               bool(self.log_visible_checkbox.isChecked()))
        self.settings.setValue("editor/porymap_path",
                               self.porymap_edit.text().strip())
        self.settings.setValue("editor/event_tooltips",
                               bool(self.event_tooltips_checkbox.isChecked()))

        # Event Colors
        for key, btn in self._color_buttons.items():
            self.settings.setValue(f"event_colors/{key}",
                                   btn.property('current_color'))
        for key, btn in self._cat_color_buttons.items():
            self.settings.setValue(f"event_cat_colors/{key}",
                                   btn.property('current_color'))

        self.settings.sync()

        # Notifications
        for key, cb in self._notif_checks.items():
            suppress(key, not cb.isChecked())

        super().accept()
