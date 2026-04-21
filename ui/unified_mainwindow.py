"""
Unified Main Window for PorySuite-Z

Single window containing all PorySuite (data editing) and EVENTide (map/script
editing) editors, accessible via an RPG Maker XP-style icon toolbar.

Phase 1: shell only — existing editors are hosted as-is in a QStackedWidget.
No shared data layer yet; each side loads independently from disk.
"""

import os
import sys

from PyQt6.QtCore import Qt, pyqtSignal, QSize, QSettings
from PyQt6.QtGui import (QAction, QBrush, QColor, QFont, QIcon, QKeySequence,
                         QPainter, QPixmap, QShortcut)
from PyQt6.QtWidgets import (
    QMainWindow,
    QWidget,
    QVBoxLayout,
    QHBoxLayout,
    QStackedWidget,
    QToolBar,
    QToolButton,
    QSplitter,
    QTextEdit,
    QLabel,
    QMessageBox,
    QStatusBar,
    QButtonGroup,
    QSizePolicy,
    QFrame,
)

from app_info import get_settings_path
from suppress_dialog import maybe_exec
from porymap_bridge.porymap_launcher import (
    is_porymap_installed, launch_porymap, inject_bridge_script,
    ensure_bridge_gitignored,
    _send_command,
    is_porymap_patched,
    get_installed_porymap_info,
    check_porymap_update_available,
    verify_patches_intact,
)
from porymap_bridge.bridge_watcher import BridgeWatcher
from porymap_bridge.shared_file_watcher import SharedFileWatcher


# ─── Icon path helper ────────────────────────────────────────────────────────

def _icon(name: str) -> QIcon:
    """Return a QIcon for a toolbar placeholder icon."""
    path = os.path.join(os.path.dirname(os.path.dirname(__file__)), "res", "icons", "toolbar", f"{name}.png")
    if os.path.isfile(path):
        return QIcon(path)
    return QIcon()


# ─── Toolbar separator helper ────────────────────────────────────────────────

def _add_separator(toolbar: QToolBar):
    """Add a thin vertical separator line to the toolbar."""
    sep = QFrame()
    sep.setFrameShape(QFrame.Shape.VLine)
    sep.setFrameShadow(QFrame.Shadow.Sunken)
    sep.setFixedWidth(2)
    sep.setFixedHeight(28)
    toolbar.addWidget(sep)


class UnifiedMainWindow(QMainWindow):
    """Single window containing all PorySuite + EVENTide editors."""

    # Signals (kept for compatibility with App cross-launch wiring)
    open_in_eventide_signal = pyqtSignal(dict)
    open_in_porysuite_signal = pyqtSignal(dict)

    def __init__(self, parent=None):
        super().__init__(parent)
        from core.app_info import VERSION
        self.setWindowTitle(f"PorySuite-Z {VERSION}[*]")
        self.setMinimumSize(1024, 768)
        self.project_info = None

        # ── Internal references to child windows ─────────────────────────────
        self._porysuite_window = None   # The original MainWindow (used as a widget)
        self._eventide_window = None    # The original EventideMainWindow (used as a widget)

        # ── Porymap bridge ──────────────────────────────────────────────────
        self._bridge_watcher = None     # BridgeWatcher instance (created on project load)
        self._shared_file_watcher = None  # SharedFileWatcher (created on project load)
        self._porymap_initiated_load = False  # Suppress sync-back when Porymap triggered the load
        self._last_porymap_sync_map = ''      # Last map we sent to Porymap — skip echo

        # ── Central layout: splitter with stacked content + log panel ────────
        central = QWidget()
        self.setCentralWidget(central)
        root_layout = QVBoxLayout(central)
        root_layout.setContentsMargins(0, 0, 0, 0)
        root_layout.setSpacing(0)

        splitter = QSplitter(Qt.Orientation.Vertical)
        root_layout.addWidget(splitter)

        # ── Stacked widget: one page per editor section ──────────────────────
        self.stack = QStackedWidget()
        splitter.addWidget(self.stack)

        # ── Log panel (hidden by default — toggle via View menu) ────────────
        self.log_panel = QTextEdit()
        self.log_panel.setReadOnly(True)
        self.log_panel.setMaximumHeight(160)
        self.log_panel.setFont(QFont("Courier New", 9))
        self.log_panel.setPlaceholderText("Log output will appear here...")
        splitter.addWidget(self.log_panel)

        # Restore log panel visibility from settings (default: hidden)
        _s = QSettings(get_settings_path(), QSettings.Format.IniFormat)
        self.log_panel.setVisible(
            _s.value("ui/show_log_panel", False, type=bool)
        )

        splitter.setStretchFactor(0, 5)
        splitter.setStretchFactor(1, 1)

        # ── Page index tracking ──────────────────────────────────────────────
        # Maps page name -> stack index.  Filled by _setup_pages().
        self._page_indices: dict[str, int] = {}
        self._page_buttons: dict[str, QToolButton] = {}
        # Base (clean) QIcon for each toolbar button — used to repaint the
        # amber dirty-dot overlay on top without losing the original art.
        self._page_base_icons: dict[str, QIcon] = {}
        # Per-page dirty section count.  Multiple logical sections share a
        # single toolbar button (e.g. "species" + "pokedex" → "pokemon").
        self._page_dirty_counts: dict[str, int] = {}
        # Section-name → toolbar-button-name mapping.
        self._section_to_page: dict[str, str] = {
            "species": "pokemon",
            "pokedex": "pokedex",
            "moves": "moves",
            "items": "items",
            "abilities": "abilities",
            "trainers": "trainers",
            "starters": "starters",
            "encounters": "trainers",
            "credits": "credits",
            "title": "settings",
            "sound": "sound",
            "overworld": "overworld",
            "trainer_graphics": "trainers",
            "events":    "events",
            "maps":      "maps",
            "regionmap": "regionmap",
            "tilesets":  "tilesets",
            "ui":        "ui",
            "config":    "config",
            "labels":    "labels",
        }

        # ── Build toolbar and menus ──────────────────────────────────────────
        self._build_toolbar()
        self._build_menus()

        # ── Status bar ───────────────────────────────────────────────────────
        self._git_bar_label = QLabel("")
        self._git_bar_label.setObjectName("git_status_bar")
        self._git_bar_label.setStyleSheet(
            "#git_status_bar { color: #888; margin-right: 8px; }")
        self._git_bar_label.setCursor(Qt.CursorShape.PointingHandCursor)
        self._git_bar_label.mousePressEvent = lambda _e: self._open_git_panel()
        self.statusBar().addPermanentWidget(self._git_bar_label)

    # ═════════════════════════════════════════════════════════════════════════
    # Toolbar
    # ═════════════════════════════════════════════════════════════════════════

    def _build_toolbar(self):
        tb = QToolBar("Main Toolbar")
        tb.setMovable(False)
        tb.setIconSize(QSize(32, 32))
        tb.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonIconOnly)
        tb.setStyleSheet("""
            QToolBar { spacing: 4px; padding: 4px; }
            QToolButton { padding: 4px; border-radius: 4px; }
            QToolButton:hover { background: palette(midlight); }
            QToolButton:checked { background: palette(highlight);
                                  color: palette(highlighted-text); }
        """)
        self.addToolBar(tb)
        self._toolbar = tb

        # ── Action buttons (Save, Make, Make Modern) ─────────────────────────
        self._save_action = QAction(_icon("save"), "Save All", self)
        self._save_action.setShortcut("Ctrl+S")
        self._save_action.setToolTip("Save all changes (Ctrl+S)")
        self._save_action.triggered.connect(lambda _checked=False: self._on_save_all())
        tb.addAction(self._save_action)

        self._make_action = QAction(_icon("make"), "Make", self)
        self._make_action.setShortcut("Ctrl+M")
        self._make_action.setToolTip("Build ROM with 'make' (Ctrl+M)")
        self._make_action.triggered.connect(lambda: self._on_make([]))
        tb.addAction(self._make_action)

        self._make_modern_action = QAction(_icon("make_modern"), "Make Modern", self)
        self._make_modern_action.setShortcut("Ctrl+Shift+M")
        self._make_modern_action.setToolTip("Build ROM with 'make modern' (Ctrl+Shift+M)")
        self._make_modern_action.triggered.connect(lambda: self._on_make(["MODERN=1"]))
        tb.addAction(self._make_modern_action)

        _add_separator(tb)

        # ── PorySuite data editor pages ──────────────────────────────────────
        self._page_button_group = QButtonGroup(self)
        self._page_button_group.setExclusive(True)

        porysuite_pages = [
            ("pokemon",    "Pokemon"),
            ("pokedex",    "Pokedex"),
            ("moves",      "Moves"),
            ("items",      "Items"),
            ("abilities",  "Abilities"),
            ("trainers",   "Trainers"),
            ("starters",   "Starters"),
            ("overworld",  "Overworld Graphics"),
            ("credits",    "Credits"),
            ("sound",      "Sound Editor"),
        ]
        for icon_name, tooltip in porysuite_pages:
            btn = self._make_page_button(icon_name, tooltip)
            tb.addWidget(btn)

        _add_separator(tb)

        # ── EVENTide map/script editor pages ─────────────────────────────────
        eventide_pages = [
            ("events",    "EVENTide"),
            ("maps",      "Maps"),
            ("regionmap", "Region Map"),
            ("labels",    "Label Manager"),
        ]
        for icon_name, tooltip in eventide_pages:
            btn = self._make_page_button(icon_name, tooltip)
            tb.addWidget(btn)

        _add_separator(tb)

        # ── Tilemap Editor ───────────────────────────────────────────────────
        btn = self._make_page_button("tilesets", "Tilemap Editor")
        tb.addWidget(btn)

        _add_separator(tb)

        # ── Settings / Info pages ────────────────────────────────────────────
        settings_pages = [
            ("diagnostics", "ROM Diagnostics"),
            ("ui",          "Text Editor"),
            ("config",      "Config"),
        ]
        for icon_name, tooltip in settings_pages:
            btn = self._make_page_button(icon_name, tooltip)
            tb.addWidget(btn)

        _add_separator(tb)

        # ── Play button (last in line) ──────────────────────────────────────
        self._play_action = QAction(_icon("play"), "Play", self)
        self._play_action.setShortcut("F9")
        self._play_action.setToolTip("Launch ROM in emulator (F9)")
        self._play_action.triggered.connect(self._on_play)
        tb.addAction(self._play_action)

        # ── Keyboard shortcut to jump to Sound Editor ──────────────────────
        _sound_shortcut = QShortcut(QKeySequence("F8"), self)
        _sound_shortcut.activated.connect(lambda: self._switch_page("sound"))
        # Update the tooltip so the user knows about the shortcut
        if "sound" in self._page_buttons:
            self._page_buttons["sound"].setToolTip("Sound Editor (F8)")

    def _make_page_button(self, icon_name: str, tooltip: str) -> QToolButton:
        """Create a checkable toolbar button that switches the stacked widget."""
        btn = QToolButton()
        base = _icon(icon_name)
        btn.setIcon(base)
        btn.setToolTip(tooltip)
        btn.setCheckable(True)
        btn.setIconSize(QSize(32, 32))
        btn.setFixedSize(40, 40)
        self._page_button_group.addButton(btn)
        self._page_buttons[icon_name] = btn
        self._page_base_icons[icon_name] = base
        btn.clicked.connect(lambda checked, name=icon_name: self._switch_page(name))
        return btn

    def _icon_with_dirty_dot(self, base: QIcon) -> QIcon:
        """Return a 32×32 QIcon with an amber 8×8 dot in the bottom-right."""
        pm = base.pixmap(32, 32)
        if pm.isNull():
            pm = QPixmap(32, 32)
            pm.fill(Qt.GlobalColor.transparent)
        else:
            pm = pm.copy()
        painter = QPainter(pm)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.setBrush(QBrush(QColor("#ffb74d")))
        painter.setPen(QColor("#000000"))
        # Bottom-right 8×8 dot.
        painter.drawEllipse(22, 22, 8, 8)
        painter.end()
        return QIcon(pm)

    def set_page_dirty(self, section: str, dirty: bool):
        """Slot for porysuite_main.sectionDirtyChanged.

        Tracks per-page dirty counts (multiple sections may map to the same
        toolbar button) and toggles the amber dot overlay accordingly.
        """
        page = self._section_to_page.get(section)
        if not page:
            return
        btn = self._page_buttons.get(page)
        base = self._page_base_icons.get(page)
        if btn is None or base is None:
            return
        prev = self._page_dirty_counts.get(section, 0)
        new = 1 if dirty else 0
        if new == prev:
            return
        self._page_dirty_counts[section] = new
        # Recompute whether ANY section sharing this page is dirty.
        any_dirty = any(
            self._page_dirty_counts.get(s, 0) > 0
            for s, p in self._section_to_page.items() if p == page)
        btn.setIcon(self._icon_with_dirty_dot(base) if any_dirty else base)

    def _switch_page(self, name: str):
        """Switch the stacked widget to the page identified by name."""
        idx = self._page_indices.get(name, -1)
        if idx >= 0:
            self.stack.setCurrentIndex(idx)
            # Lazy loading is handled by _on_stack_page_changed

    # ═════════════════════════════════════════════════════════════════════════
    # Menu bar
    # ═════════════════════════════════════════════════════════════════════════

    def _build_menus(self):
        menubar = self.menuBar()

        # ── File ─────────────────────────────────────────────────────────────
        file_menu = menubar.addMenu("&File")

        save_action = QAction("Save All", self)
        save_action.setShortcut("Ctrl+S")
        save_action.triggered.connect(lambda _checked=False: self._on_save_all())
        file_menu.addAction(save_action)

        file_menu.addSeparator()

        refresh_action = QAction("Refresh", self)
        refresh_action.setShortcut("F5")
        refresh_action.setToolTip(
            "Reload all data from disk — PorySuite data, EVENTide scripts, "
            "sprite caches, everything. Use this after saving to verify changes persisted.")
        refresh_action.triggered.connect(self._refresh_project)
        file_menu.addAction(refresh_action)

        file_menu.addSeparator()

        open_folder_action = QAction("Open Project Folder", self)
        open_folder_action.triggered.connect(self._open_project_folder)
        file_menu.addAction(open_folder_action)

        file_menu.addSeparator()

        quit_action = QAction("Quit", self)
        quit_action.setShortcut("Ctrl+Q")
        quit_action.triggered.connect(self.close)
        file_menu.addAction(quit_action)

        # ── Edit ─────────────────────────────────────────────────────────────
        edit_menu = menubar.addMenu("&Edit")

        rename_action = QAction("Rename Entity...", self)
        rename_action.triggered.connect(self._rename_entity)
        edit_menu.addAction(rename_action)

        edit_menu.addSeparator()

        decapitalize_action = QAction("Name Decapitalizer...", self)
        decapitalize_action.setToolTip(
            "Batch-convert ALL-CAPS display names (species, moves, items, "
            "trainers, trainer classes, abilities, UI labels) to Smart "
            "Title Case. Preview every change before applying.")
        decapitalize_action.triggered.connect(self._open_name_decapitalizer)
        edit_menu.addAction(decapitalize_action)

        # ── View ─────────────────────────────────────────────────────────────
        view_menu = menubar.addMenu("&View")

        # Data editors
        for icon_name, label in [
            ("pokemon", "Pokemon"), ("pokedex", "Pokedex"), ("moves", "Moves"),
            ("items", "Items"), ("trainers", "Trainers"), ("starters", "Starters"),
            ("overworld", "Overworld Graphics"),
        ]:
            act = QAction(label, self)
            act.triggered.connect(lambda checked, n=icon_name: self._switch_to_page(n))
            view_menu.addAction(act)

        view_menu.addSeparator()

        # Map/script editors
        for icon_name, label in [
            ("events", "EVENTide"), ("maps", "Maps"),
            ("regionmap", "Region Map"),
            ("labels", "Label Manager"),
        ]:
            act = QAction(label, self)
            act.triggered.connect(lambda checked, n=icon_name: self._switch_to_page(n))
            view_menu.addAction(act)

        view_menu.addSeparator()

        # Settings/config
        for icon_name, label in [("ui", "Text Editor"), ("config", "Config")]:
            act = QAction(label, self)
            act.triggered.connect(lambda checked, n=icon_name: self._switch_to_page(n))
            view_menu.addAction(act)

        view_menu.addSeparator()

        self._toggle_log_action = QAction("Show Log Panel", self)
        self._toggle_log_action.setCheckable(True)
        self._toggle_log_action.setChecked(self.log_panel.isVisible())
        self._toggle_log_action.triggered.connect(self._toggle_log_panel)
        view_menu.addAction(self._toggle_log_action)

        # ── Tools ────────────────────────────────────────────────────────────
        tools_menu = menubar.addMenu("&Tools")

        make_action = QAction("Make", self)
        make_action.setShortcut("Ctrl+M")
        make_action.triggered.connect(lambda: self._on_make([]))
        tools_menu.addAction(make_action)

        make_modern_action = QAction("Make Modern", self)
        make_modern_action.setShortcut("Ctrl+Shift+M")
        make_modern_action.triggered.connect(lambda: self._on_make(["MODERN=1"]))
        tools_menu.addAction(make_modern_action)

        play_action = QAction("Play", self)
        play_action.setShortcut("F9")
        play_action.triggered.connect(self._on_play)
        tools_menu.addAction(play_action)

        tools_menu.addSeparator()

        expand_rom_action = QAction("Expand ROM to 32 MB...", self)
        expand_rom_action.setToolTip(
            "Patch the Makefile to allow the ROM to grow up to 32 MB.\n"
            "Needed when adding new voicegroups, samples, or other data\n"
            "that pushes the ROM past the default 16 MB limit.")
        expand_rom_action.triggered.connect(self._on_expand_rom)
        tools_menu.addAction(expand_rom_action)

        tools_menu.addSeparator()

        open_terminal_action = QAction("Open Terminal", self)
        open_terminal_action.setShortcut("Ctrl+T")
        open_terminal_action.triggered.connect(self._open_terminal)
        tools_menu.addAction(open_terminal_action)

        tools_menu.addSeparator()

        settings_action = QAction("Settings...", self)
        settings_action.triggered.connect(self._open_settings)
        tools_menu.addAction(settings_action)

        tools_menu.addSeparator()

        # ── Porymap integration ──────────────────────────────────────────────
        pm_info = get_installed_porymap_info()
        if pm_info["installed"]:
            if pm_info["patched"] and not pm_info["patches_intact"]:
                install_label = "⚠ Re-patch Porymap..."
                install_tip = ("Porymap was updated outside PorySuite — "
                               "bridge patches are missing. Click to rebuild.")
            elif pm_info["built"]:
                install_label = "Update Porymap..."
                install_tip = (f"Currently installed: built {pm_info['built']}"
                               f", commit {pm_info['commit'] or 'unknown'}")
            else:
                install_label = "Update Porymap..."
                install_tip = "Reinstall or update to latest Porymap"
        else:
            install_label = "Install Porymap..."
            install_tip = ("Downloads Porymap source from GitHub, applies "
                           "PorySuite-Z bridge patches, downloads Qt SDK, "
                           "and compiles a custom build. Requires internet.")

        self._install_porymap_action = QAction(install_label, self)
        self._install_porymap_action.setToolTip(install_tip)
        self._install_porymap_action.triggered.connect(self._install_porymap)
        tools_menu.addAction(self._install_porymap_action)

        self._open_porymap_action = QAction("Open in Porymap", self)
        self._open_porymap_action.setShortcut("Ctrl+F7")
        self._open_porymap_action.triggered.connect(self._open_in_porymap)
        self._open_porymap_action.setEnabled(pm_info["installed"])
        if not pm_info["installed"]:
            self._open_porymap_action.setToolTip("Install Porymap first (Tools → Install/Update Porymap)")
        tools_menu.addAction(self._open_porymap_action)

        self._check_porymap_update_action = QAction("Check for Porymap Updates", self)
        self._check_porymap_update_action.setEnabled(pm_info["installed"])
        self._check_porymap_update_action.triggered.connect(self._check_porymap_updates)
        tools_menu.addAction(self._check_porymap_update_action)

        # ── Git ──────────────────────────────────────────────────────────────
        self._git_menu = menubar.addMenu("&Git")

        self._git_panel_action = QAction("Git Panel...", self)
        self._git_panel_action.setShortcut("Ctrl+Shift+G")
        self._git_panel_action.setToolTip(
            "Open the Git panel — pull, push, commit, branches, stash, history,\n"
            "and remote configuration, all in one window with full descriptions."
        )
        self._git_panel_action.setEnabled(False)
        self._git_panel_action.triggered.connect(self._open_git_panel)
        self._git_menu.addAction(self._git_panel_action)

        self._git_menu.addSeparator()

        self._pull_upstream_action = QAction("Pull from Upstream", self)
        self._pull_upstream_action.setShortcut("Ctrl+Shift+L")
        self._pull_upstream_action.setEnabled(False)
        self._pull_upstream_action.triggered.connect(
            lambda: self._git_pull(use_upstream=True))
        self._git_menu.addAction(self._pull_upstream_action)

        self._pull_origin_action = QAction("Pull from origin", self)
        self._pull_origin_action.setEnabled(False)
        self._pull_origin_action.triggered.connect(self._git_pull)
        self._git_menu.addAction(self._pull_origin_action)

        self._push_action = QAction("Push to origin", self)
        self._push_action.setShortcut("Ctrl+Shift+U")
        self._push_action.setEnabled(False)
        self._push_action.triggered.connect(self._git_push)
        self._git_menu.addAction(self._push_action)

        self._git_commit_action = QAction("Commit...", self)
        self._git_commit_action.setShortcut("Ctrl+Shift+K")
        self._git_commit_action.setEnabled(False)
        self._git_commit_action.triggered.connect(self._git_commit)
        self._git_menu.addAction(self._git_commit_action)

        self._git_menu.addSeparator()

        self._git_configure_remotes_action = QAction("Configure Remotes…", self)
        self._git_configure_remotes_action.setToolTip(
            "Set origin and upstream URLs, manage saved remotes.")
        self._git_configure_remotes_action.setEnabled(False)
        self._git_configure_remotes_action.triggered.connect(
            lambda: self._open_git_panel(page="remotes"))
        self._git_menu.addAction(self._git_configure_remotes_action)

        # Stubs for _git_set_all_enabled compatibility
        self._git_configure_action = QAction("", self)
        self._git_status_action = QAction("", self)
        self._git_new_branch_action = QAction("", self)
        self._git_stash_action = QAction("", self)
        self._git_pop_stash_action = QAction("", self)
        self._git_log_action = QAction("", self)
        from PyQt6.QtWidgets import QMenu
        self._pull_menu = QMenu("", self)

        # ── Help ─────────────────────────────────────────────────────────────
        help_menu = menubar.addMenu("&Help")

        update_action = QAction("Check for Updates...", self)
        update_action.triggered.connect(self._check_for_updates)
        help_menu.addAction(update_action)

        help_menu.addSeparator()

        about_action = QAction("About", self)
        about_action.triggered.connect(self._show_about)
        help_menu.addAction(about_action)

    # ═════════════════════════════════════════════════════════════════════════
    # Page setup — called after both child windows are created
    # ═════════════════════════════════════════════════════════════════════════

    def setup_pages(self, porysuite_main, eventide_main):
        """
        Extract editor widgets from the existing PorySuite and EVENTide windows
        and add them as pages in our stacked widget.

        This is called from app.py after both windows have been created and
        their data has been loaded.
        """
        self._porysuite_window = porysuite_main
        self._eventide_window = eventide_main

        # ── PorySuite pages ──────────────────────────────────────────────────
        # The PorySuite MainWindow has a tab widget (self.ui.mainTabs) with
        # several tabs.  We pull each tab's widget out and add it as a page.

        ps_ui = porysuite_main.ui

        # Map: our page name -> (tab widget reference, original tab label)
        porysuite_tabs = [
            ("pokemon",  ps_ui.tab_pokemon),
            ("pokedex",  ps_ui.tab_pokedex),
            ("items",    ps_ui.tab_items),
            ("starters", ps_ui.tab_starters),
            ("trainers", ps_ui.tab_trainers),
            ("ui",       ps_ui.tab_ui),
            ("config",   ps_ui.tab_config),
        ]

        # The Moves tab was dynamically added by MainWindow.__init__
        if hasattr(ps_ui, 'moves_widget'):
            porysuite_tabs.insert(2, ("moves", ps_ui.moves_widget))

        for name, widget in porysuite_tabs:
            idx = self.stack.addWidget(widget)
            self._page_indices[name] = idx

        # ── Abilities editor (standalone page) ──────────────────────────────
        if hasattr(porysuite_main, "abilities_tab"):
            idx = self.stack.addWidget(porysuite_main.abilities_tab)
            self._page_indices["abilities"] = idx

        # ── Overworld Graphics (standalone page pulled from MainWindow) ──────
        if hasattr(porysuite_main, "overworld_graphics_tab"):
            ow_widget = porysuite_main.overworld_graphics_tab
            idx = self.stack.addWidget(ow_widget)
            self._page_indices["overworld"] = idx

        # ── Credits editor (standalone page) ─────────────────────────────────
        try:
            from credits_editor import CreditsEditorWidget
            self._credits_editor = CreditsEditorWidget()
            idx = self.stack.addWidget(self._credits_editor)
            self._page_indices["credits"] = idx
            # Wire dirty signals to the unified dirty system.
            ps = self._porysuite_window
            if ps is not None:
                self._credits_editor.modified.connect(
                    lambda: (
                        ps.setWindowModified(True),
                        ps.sectionDirtyChanged.emit("credits", True),
                    )
                )
                self._credits_editor.saved.connect(
                    lambda: ps.sectionDirtyChanged.emit("credits", False)
                )
        except Exception as e:
            print(f"[CreditsEditor] Failed to load: {e}")
            import traceback; traceback.print_exc()

        # ── Sound Editor (standalone page) ───────────────────────────────────
        try:
            from ui.sound_editor_tab import SoundEditorTab
            self._sound_editor = SoundEditorTab()
            idx = self.stack.addWidget(self._sound_editor)
            self._page_indices["sound"] = idx
            self._sound_editor.modified.connect(
                lambda: (self.set_page_dirty("sound", True),
                         self.setWindowModified(True)))
        except Exception as e:
            print(f"[SoundEditor] Failed to load: {e}")
            import traceback
            traceback.print_exc()

        # ── EVENTide pages ───────────────────────────────────────────────────
        eventide_tabs = [
            ("events",    eventide_main.event_editor_tab),
            ("maps",      eventide_main.maps_tab),
            ("regionmap", eventide_main.region_map_tab),
        ]

        for name, widget in eventide_tabs:
            idx = self.stack.addWidget(widget)
            self._page_indices[name] = idx

        # Wire EVENTide tab dirty dots.
        eventide_main.event_editor_tab.data_changed.connect(
            lambda: self.set_page_dirty("events", True))
        eventide_main.maps_tab.data_changed.connect(
            lambda: self.set_page_dirty("maps", True))
        eventide_main.region_map_tab.data_changed.connect(
            lambda: self.set_page_dirty("regionmap", True))

        # ── Label Manager (standalone page) ─────────────────────────────────
        try:
            from label_manager import LabelManagerWidget
            self._label_manager = LabelManagerWidget()
            idx = self.stack.addWidget(self._label_manager)
            self._page_indices["labels"] = idx
            self._label_manager.modified.connect(
                lambda: (self.set_page_dirty("labels", True),
                         self.setWindowModified(True)))
            self._label_manager.labels_changed.connect(
                lambda: self.set_page_dirty("labels", False))
        except Exception as e:
            print(f"[LabelManager] Failed to load: {e}")
            import traceback; traceback.print_exc()

        # ── ROM Diagnostics ─────────────────────────────────────────────────
        try:
            from ui.diagnostics_tab import DiagnosticsTab
            self._diagnostics_tab = DiagnosticsTab()
            idx = self.stack.addWidget(self._diagnostics_tab)
            self._page_indices["diagnostics"] = idx
        except Exception as e:
            print(f"[Diagnostics] Failed to load: {e}")
            import traceback; traceback.print_exc()

        # ── Tilemap Editor ──────────────────────────────────────────────────
        try:
            from ui.tilemap_editor_tab import TilemapEditorTab
            self._tilemap_editor = TilemapEditorTab()
            idx = self.stack.addWidget(self._tilemap_editor)
            self._page_indices["tilesets"] = idx
            # Connect tilemap + tile animation editor modified signals to window dirty
            self._tilemap_editor.modified.connect(
                lambda: (self.set_page_dirty("tilesets", True),
                         self.setWindowModified(True)))
            anim_ed = getattr(self._tilemap_editor, '_anim_viewer', None)
            if anim_ed and hasattr(anim_ed, 'modified'):
                anim_ed.modified.connect(
                    lambda: (self.set_page_dirty("tilesets", True),
                             self.setWindowModified(True)))
        except Exception as e:
            print(f"[TilemapEditor] Failed to load: {e}")
            import traceback; traceback.print_exc()

        # ── Disconnect PorySuite's own tab-change handler ────────────────────
        # Pages have been reparented out of mainTabs, so PorySuite's
        # on_main_tab_changed would fire spuriously and set dirty flags.
        try:
            ps_ui.mainTabs.currentChanged.disconnect(
                porysuite_main.on_main_tab_changed)
        except (TypeError, RuntimeError):
            pass  # already disconnected or never connected

        # ── Wrap PorySuite's internal navigation handlers with dirty suppression.
        # These methods populate UI fields which trigger widget signals connected
        # to setWindowModified(True), but they aren't real user edits.
        # Because Qt signals hold direct references to the original methods,
        # setattr wrappers don't intercept signal-triggered calls.  Instead we
        # install suppression directly on the signal connections.
        porysuite_main._ps_suppress_dirty = False

        # Pokemon sub-tab switches — disconnect ALL slots, reconnect wrapped.
        # PyQt6 bound-method identity can prevent targeted disconnect, so
        # we disconnect everything and re-wire with suppression wrappers.
        try:
            ps_ui.tab_pokemon_data.currentChanged.disconnect()
        except (TypeError, RuntimeError):
            pass
        def _wrapped_pokemon_tab_changed(index):
            porysuite_main._ps_suppress_dirty = True
            try:
                porysuite_main.on_pokemon_tab_changed(index)
                porysuite_main.refresh_current_species(index)
            finally:
                porysuite_main._ps_suppress_dirty = False
        ps_ui.tab_pokemon_data.currentChanged.connect(_wrapped_pokemon_tab_changed)

        # Species tree selection changes
        try:
            ps_ui.tree_pokemon.itemSelectionChanged.disconnect()
        except (TypeError, RuntimeError):
            pass
        def _wrapped_species_changed():
            porysuite_main._ps_suppress_dirty = True
            try:
                porysuite_main.update_tree_pokemon()
            finally:
                porysuite_main._ps_suppress_dirty = False
        ps_ui.tree_pokemon.itemSelectionChanged.connect(_wrapped_species_changed)

        # ── Wire stacked widget page changes to PorySuite's save/load logic ──
        self._current_page_name = "pokemon"
        self.stack.currentChanged.connect(self._on_stack_page_changed)

        # ── Default to the Pokemon page ──────────────────────────────────────
        if "pokemon" in self._page_indices:
            self.stack.setCurrentIndex(self._page_indices["pokemon"])
            btn = self._page_buttons.get("pokemon")
            if btn:
                btn.setChecked(True)

        # ── Shared data layer ────────────────────────────────────────────────
        from shared_data import ProjectData
        self.project_data = ProjectData(
            porysuite_main.project_info or {}, parent=self)
        if porysuite_main.source_data:
            self.project_data.attach_source_data(porysuite_main.source_data)
        try:
            from eventide.backend.constants_manager import ConstantsManager
            self.project_data.attach_constants_manager(ConstantsManager)
        except Exception:
            pass
        self.project_data.attach_event_editor(eventide_main.event_editor_tab)

        # ── Wire change signals: PorySuite → EVENTide ────────────────────────
        # When trainers are saved in PorySuite, tell EVENTide to refresh its
        # trainer dropdown so the new trainer shows up immediately.
        self.project_data.trainers_changed.connect(
            self._refresh_eventide_constants)
        self.project_data.items_changed.connect(
            self._refresh_eventide_constants)
        self.project_data.species_changed.connect(
            self._refresh_eventide_constants)
        self.project_data.constants_changed.connect(
            self._refresh_eventide_constants)

        # ── Overworld → EVENTide GFX constant sync ────────────────────────
        if hasattr(porysuite_main, "overworld_graphics_tab"):
            porysuite_main.overworld_graphics_tab.gfx_constants_changed.connect(
                self._refresh_eventide_constants)

        # ── Populate Event Editor display names ─────────────────────────────
        self._update_event_editor_display_names()

        # Wire label manager changes to refresh display names
        if hasattr(self, '_label_manager'):
            self._label_manager.labels_changed.connect(
                self._update_event_editor_display_names)

        # ── Phase 3: Cross-editor navigation ────────────────────────────────
        # Trainer tab → Event Editor ("Set up battle script")
        if hasattr(porysuite_main, 'trainers_editor'):
            porysuite_main.trainers_editor.setup_battle_requested.connect(
                self._on_jump_to_event_editor)
        # Event Editor → Trainer tab (right-click "Edit Trainer Party")
        eventide_main.event_editor_tab.jump_to_trainer.connect(
            self._on_jump_to_trainer)
        # Event Editor → Items tab (right-click "Edit Item")
        eventide_main.event_editor_tab.jump_to_item.connect(
            self._on_jump_to_item)
        # Event Editor → Label Manager (right-click "Edit Label")
        eventide_main.event_editor_tab.jump_to_label.connect(
            self._on_jump_to_label)
        # Text Editor → EVENTide ("Open in EVENTide" button)
        self.open_in_eventide_signal.connect(self._on_open_in_eventide)
        # Maps tab → Event Editor (double-click a map to open it)
        eventide_main.maps_tab.map_selected.connect(
            self._on_map_selected)
        # Event Editor map loaded → auto-sync to Porymap if running
        eventide_main.event_editor_tab.map_loaded.connect(
            self._on_map_loaded_sync_porymap)

        # ── Sound Editor ↔ EVENTide integration ─────────────────────────────
        # Wire preview / open-in-editor callbacks so playbgm/playse/playfanfare
        # command widgets can talk to the Sound Editor.
        try:
            import eventide.ui.event_editor_tab as _eet_mod
            if hasattr(self, '_sound_editor'):
                _eet_mod._preview_song_cb = self._sound_preview_song
                _eet_mod._open_in_sound_editor_cb = self._sound_open_song
                _eet_mod._stop_preview_cb = self._sound_stop_preview
        except Exception:
            pass

        # ── Connect dirty tracking from both windows ─────────────────────────
        # _suppress_dirty blocks dirty propagation during flush/load operations
        # that save in-memory state but aren't genuine user edits.
        self._suppress_dirty = False

        # When either child marks itself as modified, we reflect it here.
        porysuite_main.windowModified = self._on_child_modified
        # For EVENTide, listen to its data_changed signals
        eventide_main.event_editor_tab.data_changed.connect(
            lambda: self.setWindowModified(True) if not self._suppress_dirty else None)

        # ── Connect PorySuite's log signal to our log panel ──────────────────
        porysuite_main.logSignal.connect(self.log_message)

        # ── Forward PorySuite dirty tracking ─────────────────────────────────
        # Override PorySuite's setWindowModified to also update ours.
        _original_set_modified = porysuite_main.setWindowModified
        def _unified_set_modified(modified):
            # Respect every suppression signal from the child window so the
            # unified title bar can't light up while the child's own override
            # is correctly blocking a spurious dirty mark (loading, flushing,
            # bulk combo population, etc.).
            try:
                import os, time
                log = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "diag_dirty.log")
                with open(log, "a", encoding="utf-8") as f:
                    f.write(
                        f"[{time.strftime('%H:%M:%S')}] _unified_set_modified({modified}) "
                        f"suppress={self._suppress_dirty} "
                        f"ps_suppress={getattr(porysuite_main, '_ps_suppress_dirty', False)} "
                        f"depth={getattr(porysuite_main, '_loading_depth', 0)}\n")
            except Exception:
                pass
            if modified and (self._suppress_dirty
                             or getattr(porysuite_main, '_ps_suppress_dirty', False)
                             or getattr(porysuite_main, '_loading_depth', 0) > 0):
                return
            _original_set_modified(modified)
            if modified:
                self.setWindowModified(True)

        porysuite_main.setWindowModified = _unified_set_modified

        # ── Per-section dirty indicator on the sidebar tab icons ─────────────
        if hasattr(porysuite_main, "sectionDirtyChanged"):
            porysuite_main.sectionDirtyChanged.connect(self.set_page_dirty)

        # Text editor and config dirty dots — those tabs fire setWindowModified
        # via mainwindow.py but don't emit sectionDirtyChanged.  Wire directly.
        if hasattr(porysuite_main, "ui_tab"):
            porysuite_main.ui_tab.modified.connect(
                lambda: self.set_page_dirty("ui", True))
        if hasattr(porysuite_main, "config_tab"):
            porysuite_main.config_tab.modified.connect(
                lambda: self.set_page_dirty("config", True))

    # ═════════════════════════════════════════════════════════════════════════
    # Project loading
    # ═════════════════════════════════════════════════════════════════════════

    def load_data(self, project_info: dict):
        """Store project info and update window title."""
        self.project_info = project_info
        project_name = project_info.get("name", os.path.basename(
            project_info.get("dir", "")))
        self.setWindowTitle(f"PorySuite-Z — {project_name}[*]")

        # Enable git actions
        self._git_set_all_enabled(True)

        # Load credits editor data
        project_dir = project_info.get("dir", "")
        if project_dir and hasattr(self, "_credits_editor"):
            try:
                self._credits_editor.load_project(project_dir)
            except Exception:
                pass

        # Load label manager data
        if project_dir and hasattr(self, "_label_manager"):
            try:
                self._label_manager.load_project(project_dir)
            except Exception:
                pass

        # Load Sound Editor data
        if project_dir and hasattr(self, "_sound_editor"):
            try:
                self._sound_editor.load_project(project_dir)
            except Exception:
                pass

        # Refresh Event Editor display names (species, items, trainers, etc.)
        self._update_event_editor_display_names()

        # ── Start Porymap bridge watcher ────────────────────────────────────
        self._start_bridge_watcher(project_dir)

        # ── Start shared file watcher (detects Porymap saves on disk) ──────
        self._start_shared_file_watcher(project_dir)

        # Auto-inject bridge script into project's Porymap config
        if project_dir and is_porymap_installed():
            try:
                inject_bridge_script(project_dir)
            except Exception:
                pass

        # Ensure bridge IPC files are in the project's .gitignore
        if project_dir:
            try:
                ensure_bridge_gitignored(project_dir)
            except Exception:
                pass

        # Check if Porymap patches are still intact (detects self-update)
        self._check_porymap_patch_integrity()

        self.setWindowModified(False)
        if self._porysuite_window:
            self._porysuite_window.setWindowModified(False)
        if self._eventide_window:
            self._eventide_window.setWindowModified(False)
        # Deferred loads (e.g. _deferred_load_items via QTimer.singleShot(0))
        # fire after the event loop resumes, populating widgets that emit
        # change signals through the dirty override.  Schedule a clear AFTER
        # those deferred loads have settled.
        from PyQt6.QtCore import QTimer
        def _clear_load_dirty():
            if self._porysuite_window:
                self._porysuite_window.setWindowModified(False)
            if self._eventide_window:
                self._eventide_window.setWindowModified(False)
            self.setWindowModified(False)
        QTimer.singleShot(200, _clear_load_dirty)
        self._git_refresh_status_bar()
        self.log_message(f"Loaded project: {project_name}")

    # ═════════════════════════════════════════════════════════════════════════
    # Action handlers
    # ═════════════════════════════════════════════════════════════════════════

    def _on_save_all(self):
        """Save all dirty editors and emit change signals."""
        ans = QMessageBox.question(
            self, "Save All",
            "Save all changes to disk?\n\n"
            "This will write your edits to the project's C source files, "
            "assembly files, JSON data, and any other modified files.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Cancel,
        )
        if ans != QMessageBox.StandardButton.Yes:
            return

        saved_porysuite = False
        saved_eventide = False

        # Save PorySuite data — always attempt if a project is loaded.
        # The dirty flag can fall out of sync with suppress-dirty wrappers,
        # so we rely on update_save()'s own internal checks instead of
        # gating on isWindowModified().
        if self._porysuite_window:
            try:
                self._porysuite_window.update_save()
                self._porysuite_window.setWindowModified(False)
                saved_porysuite = True
                self.set_page_dirty("ui", False)
                self.set_page_dirty("config", False)
            except Exception as e:
                self.log_message(f"Error saving PorySuite data: {e}")

        # Save EVENTide data (skip if no map is loaded — nothing to save)
        if self._eventide_window and self._eventide_window.isWindowModified():
            eet = self._eventide_window.event_editor_tab
            if getattr(eet, '_map_dir', None) and getattr(eet, '_map_data', None):
                try:
                    eet._on_save()
                    saved_eventide = True
                except Exception as e:
                    self.log_message(f"Error saving EVENTide data: {e}")
            # Always clear EVENTide dirty flag after save — even if no map
            # is loaded.  The gfx_combo refresh during save fires
            # currentIndexChanged → _mark_dirty, leaving EVENTide dirty
            # with nothing actually needing saving.
            self._eventide_window.setWindowModified(False)
            self.set_page_dirty("events", False)
            self.set_page_dirty("maps", False)
            self.set_page_dirty("regionmap", False)

        # Emit change signals so the other side picks up changes
        if saved_porysuite and hasattr(self, 'project_data'):
            self.project_data.notify_trainers_changed()
            self.project_data.notify_items_changed()
            self.project_data.notify_species_changed()
            self.project_data.notify_moves_changed()

        if saved_eventide and hasattr(self, 'project_data'):
            self.project_data.notify_texts_changed()
            self.project_data.notify_scripts_changed()

        # Save credits editor
        saved_credits = False
        if hasattr(self, "_credits_editor") and self._credits_editor.has_unsaved_changes():
            try:
                self._credits_editor._on_save()
                saved_credits = True
            except Exception as e:
                self.log_message(f"Error saving credits: {e}")

        # Save label manager
        saved_labels = False
        if hasattr(self, "_label_manager") and self._label_manager.has_unsaved_changes():
            try:
                self._label_manager._save_labels()
                saved_labels = True
            except Exception as e:
                self.log_message(f"Error saving labels: {e}")

        # Save Sound Editor data (voicegroups + song table when modified)
        saved_sound = False
        if hasattr(self, '_sound_editor'):
            se = self._sound_editor
            # Voicegroups
            vg_tab = getattr(se, '_voicegroups_tab', None)
            if vg_tab and vg_tab.has_unsaved_changes():
                try:
                    vg_tab.save_to_disk()
                    saved_sound = True
                    inst_tab = getattr(se, '_instruments_tab', None)
                    if inst_tab:
                        inst_tab.clear_dirty()
                except Exception as e:
                    self.log_message(f"Error saving voicegroups: {e}")
            # Piano roll edits (song .s files)
            pr = getattr(se, '_piano_roll_window', None)
            if pr and getattr(pr, 'has_unsaved_changes', lambda: False)():
                try:
                    path = pr.save_to_disk()
                    saved_sound = True
                    self.log_message(f"Saved piano roll edits to {path}")
                except Exception as e:
                    self.log_message(f"Error saving piano roll: {e}")

            # Song table (song_table.inc, songs.h, midi.cfg) — when modified
            st = getattr(se, '_song_table', None)
            if st and getattr(st, '_dirty', False):
                try:
                    from core.sound.song_table_manager import (
                        write_song_table, write_songs_h, write_midi_cfg)
                    write_song_table(se._project_root, st)
                    write_songs_h(se._project_root, st)
                    write_midi_cfg(se._project_root, st)
                    st._dirty = False
                    saved_sound = True
                    self.log_message("Saved song table, songs.h, and midi.cfg")
                except Exception as e:
                    self.log_message(f"Error saving song table: {e}")

            if saved_sound:
                self.set_page_dirty("sound", False)

        # Save Tilemap Editor (.bin file) and Tile Animation Editor (C source)
        saved_tilemap = False
        saved_tile_anim = False
        if hasattr(self, '_tilemap_editor'):
            # Tilemap .bin
            if hasattr(self._tilemap_editor, 'has_unsaved_changes') and self._tilemap_editor.has_unsaved_changes():
                try:
                    ok, errs = self._tilemap_editor.flush_to_disk()
                    if ok > 0:
                        saved_tilemap = True
                        self.log_message("Saved tilemap .bin")
                    if errs:
                        self.log_message(f"Tilemap save errors: {', '.join(errs)}")
                except Exception as e:
                    self.log_message(f"Error saving tilemap: {e}")
            # Tile animation properties
            anim_ed = getattr(self._tilemap_editor, '_anim_viewer', None)
            if anim_ed and hasattr(anim_ed, 'has_unsaved_changes') and anim_ed.has_unsaved_changes():
                try:
                    ok, errs = anim_ed.flush_to_disk()
                    if ok > 0:
                        saved_tile_anim = True
                        self.log_message(f"Saved {ok} tile animation property change(s)")
                    if errs:
                        self.log_message(f"Tile animation save errors: {', '.join(errs)}")
                except Exception as e:
                    self.log_message(f"Error saving tile animation properties: {e}")

        if saved_tilemap or saved_tile_anim:
            self.set_page_dirty("tilesets", False)

        if saved_labels:
            self.set_page_dirty("labels", False)

        # Always clear the dirty flag after save — even if no sub-component
        # reported changes.  The Sound Editor (and others) can emit modified()
        # for actions that are immediately persisted (e.g. .s file import
        # writes to disk, piano roll Save button), leaving the unified window
        # dirty with nothing left to actually save.
        self.setWindowModified(False)

        if (saved_porysuite or saved_eventide or saved_credits
                or saved_labels or saved_sound or saved_tile_anim
                or saved_tilemap):
            self.statusBar().showMessage("All changes saved.", 4000)
        else:
            self.statusBar().showMessage("Nothing to save.", 2000)

    def _on_make(self, extra_args: list):
        """Delegate make to PorySuite's existing _run_make method."""
        if not self._porysuite_window:
            self.log_message("Cannot build — no project loaded.")
            return
        # Pre-build safety: touch every .s file in the midi directory so they
        # are all newer than midi.cfg and their .mid placeholders.  This
        # prevents make's `%.s: %.mid midi.cfg` rule from running mid2agb
        # which would overwrite tool-edited .s files with empty skeletons.
        try:
            project_dir = self.project_info.get("dir", "")
            midi_dir = os.path.join(project_dir, "sound", "songs", "midi")
            if os.path.isdir(midi_dir):
                touched = 0
                for fn in os.listdir(midi_dir):
                    if fn.endswith('.s'):
                        try:
                            os.utime(os.path.join(midi_dir, fn))
                            touched += 1
                        except OSError:
                            pass
                if touched:
                    self.log_message(f"Pre-build: touched {touched} .s files to prevent mid2agb overwrite")
        except Exception as e:
            self.log_message(f"Pre-build .s protection warning: {e}")
        self._porysuite_window._run_make(extra_args)

    def _on_expand_rom(self):
        """Patch the project's Makefile to allow 32 MB ROMs."""
        if not self.project_info:
            QMessageBox.information(self, 'Expand ROM', 'No project is open.')
            return

        project_dir = self.project_info.get('dir', '')
        makefile_path = os.path.join(project_dir, 'Makefile')
        if not os.path.isfile(makefile_path):
            QMessageBox.warning(
                self, 'Expand ROM',
                f'Makefile not found at:\n{makefile_path}')
            return

        # Read the Makefile
        with open(makefile_path, 'r', encoding='utf-8') as f:
            content = f.read()

        # Check current state
        PAD_16MB = '--pad-to 0x9000000'
        PAD_32MB = '--pad-to 0xA000000'

        if PAD_32MB in content:
            QMessageBox.information(
                self, 'Expand ROM',
                'This project is already set to 32 MB.\n\n'
                'The Makefile already uses --pad-to 0xA000000.')
            return

        if PAD_16MB not in content:
            QMessageBox.warning(
                self, 'Expand ROM',
                'Could not find the expected --pad-to line in the Makefile.\n\n'
                f'Expected: {PAD_16MB}\n\n'
                'The Makefile may have been modified manually.')
            return

        reply = QMessageBox.question(
            self, 'Expand ROM to 32 MB?',
            'This will change the Makefile so the compiled ROM is padded\n'
            'to 32 MB instead of 16 MB.\n\n'
            'This is needed when adding new voicegroups, samples, or\n'
            'other data that pushes the ROM past 16 MB.\n\n'
            'The change is a single line in the Makefile:\n'
            f'  {PAD_16MB}  →  {PAD_32MB}\n\n'
            'Continue?',
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
        if reply != QMessageBox.StandardButton.Yes:
            return

        # Apply the patch
        new_content = content.replace(PAD_16MB, PAD_32MB, 1)
        with open(makefile_path, 'w', encoding='utf-8', newline='\n') as f:
            f.write(new_content)

        self.log_message("[ROM] Makefile patched: ROM padded to 32 MB")
        QMessageBox.information(
            self, 'ROM Expanded',
            'Done! The Makefile now pads the ROM to 32 MB.\n\n'
            'Rebuild with Make Modern to create the larger ROM.')

    def _on_play(self):
        """Launch the compiled .gba file using the configured emulator or Windows default."""
        if not self.project_info:
            QMessageBox.information(self, "Play", "No project is open.")
            return

        project_dir = self.project_info.get("dir", "")

        # Read settings for which .gba and which emulator
        settings = QSettings(get_settings_path(), QSettings.Format.IniFormat)
        gba_name = settings.value("build/gba_file", "pokefirered_modern.gba", type=str)
        emulator = settings.value("build/emulator_path", "", type=str)

        gba_path = os.path.join(project_dir, gba_name)
        if not os.path.isfile(gba_path):
            # Try the other one as fallback
            fallback = ("pokefirered.gba" if "modern" in gba_name
                        else "pokefirered_modern.gba")
            gba_path = os.path.join(project_dir, fallback)
        if not os.path.isfile(gba_path):
            QMessageBox.warning(
                self, "Play",
                "No .gba file found. Build the ROM first using Make or Make Modern.")
            return

        try:
            if emulator and os.path.isfile(emulator):
                import subprocess
                subprocess.Popen([emulator, gba_path])
            else:
                os.startfile(gba_path)
            self.log_message(f"Launched: {gba_path}")
        except Exception as e:
            QMessageBox.warning(self, "Play", f"Could not launch ROM:\n{e}")

    def _switch_to_page(self, name: str):
        """Switch to a page and update the toolbar button state."""
        self._switch_page(name)
        btn = self._page_buttons.get(name)
        if btn:
            btn.setChecked(True)

    def _on_stack_page_changed(self, new_index: int):
        """Handle page switches — flush previous PorySuite page data, lazy-load new page."""
        # Find which page name we're going to
        new_name = None
        for name, idx in self._page_indices.items():
            if idx == new_index:
                new_name = name
                break

        # Flush data from the page we're leaving (PorySuite pages).
        # Suppress dirty tracking — flushing preserves existing data, it's
        # not a new edit.
        old_name = self._current_page_name
        self._suppress_dirty = True
        try:
            self._flush_porysuite_page(old_name)
        finally:
            self._suppress_dirty = False

        self._current_page_name = new_name

        # When entering an EVENTide page from a PorySuite page, refresh the
        # ConstantsManager cache so item/flag/var/trainer rename edits that
        # hit disk are reflected in EVENTide dropdowns. Cheap: re-reads a
        # handful of header files. No-op if already current.
        _PS_PAGES = {"pokemon", "pokedex", "moves", "items", "starters",
                     "trainers", "ui", "config", "credits", "sound"}
        _EV_PAGES = {"events", "maps", "regionmap"}
        if new_name in _EV_PAGES and old_name in _PS_PAGES:
            try:
                from eventide.backend.constants_manager import ConstantsManager
                ConstantsManager.refresh()
            except Exception:
                pass

        if new_name:
            self._suppress_dirty = True
            try:
                self._trigger_lazy_load(new_name)
            finally:
                self._suppress_dirty = False

    def _flush_porysuite_page(self, page_name: str):
        """Save in-memory data from a PorySuite page before leaving it."""
        ps = self._porysuite_window
        if not ps or not ps.source_data:
            return
        try:
            if page_name == "pokemon":
                # Save current species data
                if ps.previous_selected_species is not None:
                    learnset_index = getattr(ps, "learnset_tab_index", ps.moves_tab_index)
                    if ps.ui.tab_pokemon_data.currentIndex() == learnset_index:
                        ps.save_species_learnset_table()
                    ps.save_species_data(
                        ps.previous_selected_species,
                        form=ps.previous_selected_form,
                    )
            elif page_name == "pokedex":
                ps._flush_pokedex_panel()
            elif page_name == "items":
                ps.save_items_table()
            elif page_name == "moves":
                ps.save_moves_defs_table()
            elif page_name == "trainers":
                ps._save_trainers_editor()
            elif page_name == "starters":
                # Delegate to the single flush method so both save paths
                # stay in sync — field additions only need to be in one place.
                if hasattr(ps, "_flush_starter_widgets"):
                    ps._flush_starter_widgets()
        except Exception as e:
            self.log_message(f"Warning: could not flush {page_name} data: {e}")

    def _trigger_lazy_load(self, page_name: str):
        """Trigger lazy data loading that PorySuite normally does on tab change."""
        ps = self._porysuite_window
        if not ps or not ps.source_data:
            return
        if page_name == "items":
            ps.load_items_table()
        elif page_name == "trainers":
            ps._load_trainers_editor()
        elif page_name == "moves":
            ps.load_moves_defs_table()
        elif page_name == "diagnostics":
            project_dir = (self.project_info or {}).get("dir", "")
            if project_dir and hasattr(self, '_diagnostics_tab'):
                self._diagnostics_tab.set_project(project_dir)
        elif page_name == "tilesets":
            project_dir = (self.project_info or {}).get("dir", "")
            if project_dir and hasattr(self, '_tilemap_editor'):
                self._tilemap_editor.set_project(project_dir)

    # ── Phase 3: Cross-editor navigation handlers ─────────────────────────

    def _on_jump_to_event_editor(self, trainer_const: str):
        """Switch to Event Editor when 'Set up battle script' is clicked."""
        self._switch_to_page('events')
        self.log_message(
            f'Switched to Event Editor. Open a map and add a '
            f'trainerbattle_single command for {trainer_const}.')
        self.statusBar().showMessage(
            f'Add a trainerbattle_single for {trainer_const} to an NPC.', 8000)

    def _on_jump_to_trainer(self, trainer_const: str):
        """Switch to Trainers tab and select the given trainer."""
        self._switch_to_page('trainers')
        ps = self._porysuite_window
        if ps and hasattr(ps, 'trainers_editor'):
            editor = ps.trainers_editor
            # Find and select the trainer in the list
            for i in range(editor._list.count()):
                item = editor._list.item(i)
                if item and item.data(Qt.ItemDataRole.UserRole) == trainer_const:
                    editor._list.setCurrentRow(i)
                    break
        self.log_message(f'Jumped to trainer: {trainer_const}')

    def _on_jump_to_item(self, item_const: str):
        """Switch to Items tab and select the given item."""
        self._switch_to_page('items')
        ps = self._porysuite_window
        if ps:
            # Try to find and select the item in PorySuite's items table
            try:
                table = ps.ui.items_table
                for row in range(table.rowCount()):
                    cell = table.item(row, 0)
                    if cell and cell.text() == item_const:
                        table.selectRow(row)
                        table.scrollTo(table.model().index(row, 0))
                        break
            except Exception:
                pass
        self.log_message(f'Jumped to item: {item_const}')

    def _on_jump_to_label(self, const: str):
        """Switch to Label Manager and select the given flag/var constant."""
        self._switch_to_page('labels')
        if hasattr(self, '_label_manager'):
            self._label_manager.select_constant(const)
        self.log_message(f'Jumped to label: {const}')

    def _on_open_in_eventide(self, info: dict):
        """Text Editor 'Open in EVENTide' — switch to events page and load map.

        *info* has:
          'file'       — absolute path to scripts.inc
          'text_label' — the text constant (e.g. PalletTown_Text_FatManDialogue)
        We extract the map folder name from the path, open that map in
        EVENTide, and let it search every NPC's command tree for a msgbox
        that references the text label — so it selects the right NPC
        regardless of script chain depth.
        """
        import pathlib
        file_path = info.get("file", "")
        text_label = info.get("text_label", "")
        # Extract map name from path like .../data/maps/PalletTown/scripts.inc
        p = pathlib.PurePath(file_path)
        parts = p.parts
        map_name = ""
        for i, part in enumerate(parts):
            if part == "maps" and i + 1 < len(parts):
                map_name = parts[i + 1]
                break
        if not map_name:
            from PyQt6.QtWidgets import QMessageBox
            QMessageBox.information(
                self, "Cannot Open",
                f"Could not determine map name from:\n{file_path}",
            )
            return
        self._switch_to_page('events')
        ev = self._eventide_window
        if ev and hasattr(ev, 'event_editor_tab'):
            ev.event_editor_tab.open_map_and_select(
                map_name, text_label=text_label)
        self.log_message(f'Opened map in EVENTide: {map_name}')
        if text_label:
            self.statusBar().showMessage(
                f'Jumped to NPC with {text_label} in {map_name}', 8000)

    def _on_map_selected(self, map_folder: str):
        """Double-clicking a map in Maps tab opens it in Event Editor."""
        self._switch_to_page('events')
        ev = self._eventide_window
        if ev and hasattr(ev, 'event_editor_tab'):
            ev.event_editor_tab.open_map_and_select(map_folder)
        self.log_message(f'Opened map in Event Editor: {map_folder}')

    # ── Sound Editor integration handlers ──────────────────────────────────

    def _sound_preview_song(self, constant: str) -> bool:
        """Called from EVENTide playbgm/playse/playfanfare ▶ button.

        Plays the song in the background — does NOT switch tabs.
        The user stays on whatever page they were on.
        """
        if not constant or not hasattr(self, '_sound_editor'):
            return False
        return self._sound_editor.preview_song_by_constant(constant)

    def _sound_open_song(self, constant: str):
        """Called from EVENTide 'Open in Sound Editor' button.

        Switches to the Sound Editor page and selects (but doesn't play)
        the song.
        """
        if not constant or not hasattr(self, '_sound_editor'):
            return
        self._switch_page("sound")
        self._sound_editor.select_song_by_constant(constant)

    def _sound_stop_preview(self):
        """Called from EVENTide Stop button — stops audio without switching pages."""
        if hasattr(self, '_sound_editor'):
            self._sound_editor.stop_preview()

    def _toggle_log_panel(self):
        """Show or hide the log panel.  Saves preference to settings."""
        visible = not self.log_panel.isVisible()
        self.log_panel.setVisible(visible)
        self._toggle_log_action.setChecked(visible)
        s = QSettings(get_settings_path(), QSettings.Format.IniFormat)
        s.setValue("ui/show_log_panel", visible)

    def _refresh_project(self):
        """Reload everything from disk — both PorySuite and EVENTide data."""
        if not self.project_info:
            return

        # Check for unsaved changes first
        if self._has_unsaved_changes():
            from app_util import create_unsaved_changes_dialog
            ret = create_unsaved_changes_dialog(
                self,
                "You have unsaved changes. Refreshing will discard them.\n"
                "Would you like to save first?")
            if ret == QMessageBox.StandardButton.Cancel:
                return
            if ret == QMessageBox.StandardButton.Save:
                self._on_save_all()

        # Refresh PorySuite (clears caches, re-parses headers, reloads all editors)
        if self._porysuite_window:
            try:
                self._porysuite_window._refresh_project()
            except Exception as e:
                self.log_message(f"PorySuite refresh error: {e}")

        # Refresh EVENTide (reloads all tabs from disk)
        if self._eventide_window and self._eventide_window.project_info:
            try:
                self._eventide_window.load_data(self._eventide_window.project_info)
            except Exception as e:
                self.log_message(f"EVENTide refresh error: {e}")

        # Refresh Label Manager
        project_dir = self.project_info.get("dir", "")
        if project_dir and hasattr(self, "_label_manager"):
            try:
                self._label_manager.load_project(project_dir)
            except Exception:
                pass

        # Refresh Sound Editor (Songs / Instruments / Voicegroups)
        if project_dir and hasattr(self, "_sound_editor"):
            try:
                self._sound_editor.load_project(project_dir)
            except Exception as e:
                self.log_message(f"Sound editor refresh error: {e}")

        # Refresh Credits Editor
        if project_dir and hasattr(self, "_credits_editor"):
            try:
                self._credits_editor.load_project(project_dir)
            except Exception:
                pass

        # Clear dirty flags on all sub-editors immediately AND after deferred
        # widget loads (QTimer.singleShot(0) in PorySuite's load path).
        if self._porysuite_window:
            self._porysuite_window.setWindowModified(False)
        if self._eventide_window:
            self._eventide_window.setWindowModified(False)
        self.setWindowModified(False)
        # Clear ALL toolbar dots — every section, not just PorySuite ones.
        for section in list(self._section_to_page):
            self.set_page_dirty(section, False)
        from PyQt6.QtCore import QTimer
        def _clear_refresh_dirty():
            if self._porysuite_window:
                self._porysuite_window.setWindowModified(False)
            if self._eventide_window:
                self._eventide_window.setWindowModified(False)
            self.setWindowModified(False)
            for section in list(self._section_to_page):
                self.set_page_dirty(section, False)
        QTimer.singleShot(200, _clear_refresh_dirty)
        self.statusBar().showMessage("Everything refreshed from disk.", 4000)
        self.log_message("Full refresh complete — all data reloaded from disk.")

    def _open_project_folder(self):
        """Open the project directory in the system file manager."""
        if self.project_info:
            import app_util
            app_util.reveal_directory(self.project_info.get("dir", ""))

    def _rename_entity(self):
        """Delegate to PorySuite's rename_entity."""
        if self._porysuite_window:
            self._porysuite_window.rename_entity()

    def _open_name_decapitalizer(self):
        """Open the bulk Name Decapitalizer dialog (Edit menu)."""
        from ui.name_decapitalizer import open_decapitalizer
        open_decapitalizer(self, self._porysuite_window)

    def _open_terminal(self):
        """Open a terminal in the project directory."""
        if self._porysuite_window:
            self._porysuite_window.update_action()

    def _open_settings(self):
        """Open the settings dialog."""
        from settingsdialog import SettingsDialog
        dlg = SettingsDialog(self, project_info=self.project_info)
        if dlg.exec():
            # If project name changed, update window title + projects.json
            if dlg.project_name_changed and self.project_info:
                new_name = dlg.new_project_name
                self.project_info["name"] = new_name
                self.setWindowTitle(f"PorySuite-Z — {new_name}[*]")
                self._save_project_name(new_name)

            # Reload all event editor settings (colors, tooltips, etc.)
            if (self._eventide_window
                    and hasattr(self._eventide_window, 'event_editor_tab')):
                self._eventide_window.event_editor_tab.reload_settings()

    def _save_project_name(self, new_name: str):
        """Persist project display name to projects.json and config.json."""
        import json
        from app_info import get_data_dir

        # Update projects.json
        data_dir = get_data_dir()
        pj_path = os.path.join(data_dir, "projects.json")
        try:
            with open(pj_path, encoding="utf-8") as f:
                pj = json.load(f)
            proj_dir = self.project_info.get("dir", "")
            for entry in pj.get("projects", []):
                if entry.get("dir", "") == proj_dir:
                    entry["name"] = new_name
                    break
            with open(pj_path, "w", encoding="utf-8") as f:
                json.dump(pj, f, indent=2)
        except Exception:
            pass

        # Update project config.json
        proj_dir = self.project_info.get("dir", "")
        cfg_path = os.path.join(proj_dir, "config.json")
        if os.path.isfile(cfg_path):
            try:
                with open(cfg_path, encoding="utf-8") as f:
                    cfg = json.load(f)
                cfg["name"] = new_name
                with open(cfg_path, "w", encoding="utf-8") as f:
                    json.dump(cfg, f, indent=4)
            except Exception:
                pass

    def _check_for_updates(self):
        """Manual check for updates from Help menu."""
        from PyQt6.QtWidgets import QApplication
        QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        try:
            from core.updater import check_for_update, download_and_install
            release = check_for_update()
        except Exception as e:
            QApplication.restoreOverrideCursor()
            QMessageBox.warning(self, "Update Check Failed", str(e))
            return
        QApplication.restoreOverrideCursor()

        if release is None:
            from core.app_info import VERSION
            QMessageBox.information(
                self, "Up to Date",
                f"You are running the latest version ({VERSION}).",
            )
            return

        from ui.dialogs.startup_dialogs import UpdateDialog
        dlg = UpdateDialog(release, parent=self)
        dlg.exec()
        if dlg.action == UpdateDialog.INSTALL:
            zipball = release.get("zipball_url", "")
            if not zipball:
                QMessageBox.warning(self, "Update Error", "No download URL found.")
                return
            try:
                from core.updater import launch_update_and_exit
                msg = download_and_install(zipball)
                QMessageBox.information(
                    self, "Applying Update",
                    "Update downloaded. PorySuite-Z will now close "
                    "and apply the update, then relaunch automatically.",
                )
                launch_update_and_exit()
            except Exception as e:
                QMessageBox.warning(
                    self, "Update Error", f"Failed to install:\n{e}",
                )

    def _show_about(self):
        from core.app_info import VERSION
        QMessageBox.about(
            self, "About PorySuite-Z",
            f"<h2>PorySuite-Z {VERSION}</h2>"
            "<p>Unified editor for <b>pokefirered</b> decomp projects.<br>"
            "Combines PorySuite (data editing) and EVENTide (map/script editing) "
            "into a single window.</p>"
            "<p><b>Original PorySuite</b> by jschoeny<br>"
            "<b>PorySuite-Z</b> by InnerMobius<br>"
            "Built with the assistance of AI tools</p>"
        )

    def _refresh_eventide_constants(self):
        """Reload EVENTide's constants so its dropdowns pick up PorySuite changes."""
        if not self._eventide_window or not self.project_info:
            return
        try:
            from eventide.backend.constants_manager import ConstantsManager
            ConstantsManager.load(self.project_info.get("dir", ""))
            self.log_message("EVENTide constants refreshed.")
        except Exception as e:
            self.log_message(f"Warning: could not refresh EVENTide constants: {e}")
        # Refresh the event editor's GFX dropdown
        try:
            eet = self._eventide_window.event_editor_tab
            if hasattr(eet, 'refresh_gfx_constants'):
                eet.refresh_gfx_constants()
        except Exception:
            pass
        # Also refresh display names since constants may have changed
        self._update_event_editor_display_names()

    def _update_event_editor_display_names(self):
        """Push display name data into the Event Editor's module-level resolver.

        This lets _stringize() show friendly names (e.g. 'Gym Leader Brock'
        instead of 'TRAINER_BROCK') and enables color coding by constant type.
        """
        try:
            from eventide.ui.event_editor_tab import _set_display_data

            # Get display name lists from shared data
            species = []
            items = []
            moves = []
            trainers = {}
            labels = {}

            if hasattr(self, 'project_data'):
                species = self.project_data.get_species_list()
                items = self.project_data.get_items_list()
                moves = self.project_data.get_moves_list()
                trainers = self.project_data.get_trainers()

            # Get Label Manager labels
            if hasattr(self, '_label_manager'):
                labels = self._label_manager.get_labels()

            _set_display_data(species, items, moves, trainers, labels)
            self.log_message(
                f"Display names loaded: {len(species)} species, "
                f"{len(items)} items, {len(moves)} moves, "
                f"{len(trainers)} trainers, {len(labels)} labels")

            # Re-render the currently displayed command list so items
            # already on screen pick up the new friendly names.
            if (self._eventide_window
                    and hasattr(self._eventide_window, 'event_editor_tab')):
                self._eventide_window.event_editor_tab.refresh_display_names()
        except Exception as e:
            self.log_message(f"Warning: could not update display names: {e}")
            import traceback
            self.log_message(traceback.format_exc())

    def _on_child_modified(self):
        """Called when a child window reports modifications."""
        if self._suppress_dirty:
            return
        # Also check PorySuite's navigation suppress flag
        ps = self._porysuite_window
        if ps and getattr(ps, '_ps_suppress_dirty', False):
            return
        self.setWindowModified(True)

    # ═════════════════════════════════════════════════════════════════════════
    # Unsaved changes / close
    # ═════════════════════════════════════════════════════════════════════════

    def _has_unsaved_changes(self) -> bool:
        """Check if any editor has unsaved changes."""
        if self._porysuite_window and self._porysuite_window.isWindowModified():
            return True
        if self._eventide_window and self._eventide_window.isWindowModified():
            return True
        if hasattr(self, "_credits_editor") and self._credits_editor.has_unsaved_changes():
            return True
        if hasattr(self, "_label_manager") and self._label_manager.has_unsaved_changes():
            return True
        # Sound editor voicegroups
        if hasattr(self, '_sound_editor'):
            vg_tab = getattr(self._sound_editor, '_voicegroups_tab', None)
            if vg_tab and vg_tab.has_unsaved_changes():
                return True
        return False

    def closeEvent(self, event):
        """Prompt to save unsaved changes before closing."""
        if not self._has_unsaved_changes():
            event.accept()
            return

        from app_util import create_unsaved_changes_dialog
        ret = create_unsaved_changes_dialog(
            self,
            "You have unsaved changes. Would you like to save before closing?")
        if ret == QMessageBox.StandardButton.Save:
            self._on_save_all()
            event.accept()
        elif ret == QMessageBox.StandardButton.Discard:
            event.accept()
        else:
            event.ignore()

    # ═════════════════════════════════════════════════════════════════════════
    # Logging
    # ═════════════════════════════════════════════════════════════════════════

    def log_message(self, msg: str):
        """Append a message to the log panel."""
        self.log_panel.append(msg)

    # Alias so PorySuite code that calls self.log() works when delegated
    def log(self, msg: str):
        self.log_panel.append(msg)

    # ═════════════════════════════════════════════════════════════════════════
    # Git — delegates to PorySuite or EVENTide's git methods
    # ═════════════════════════════════════════════════════════════════════════

    def _git_exe(self) -> str:
        """Find git executable."""
        for c in (
            r"C:\Program Files\Git\bin\git.exe",
            r"C:\Program Files (x86)\Git\bin\git.exe",
        ):
            if os.path.isfile(c):
                return c
        return "git"

    def _git_run(self, *args, timeout: int = 120) -> tuple:
        """Run a git command in the project directory."""
        import subprocess
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
            return False, "git not found — install Git for Windows."
        except subprocess.TimeoutExpired:
            return False, f"git timed out after {timeout}s."
        except Exception as exc:
            return False, str(exc)

    def _open_git_panel(self, page: str = ""):
        """Open the Git panel dialog."""
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

    def _git_set_all_enabled(self, enabled: bool):
        """Enable/disable all git-related actions."""
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

    def _git_pull(self, use_upstream: bool = False):
        """Delegate to the appropriate child window's git pull."""
        # Use PorySuite's implementation if available (it has the full git UI)
        target = self._porysuite_window or self._eventide_window
        if target:
            def _after_refresh():
                self._git_refresh_status_bar()
                # Also refresh EVENTide if pull was done via PorySuite
                if target is self._porysuite_window and self._eventide_window:
                    try:
                        ew = self._eventide_window
                        if ew.project_info:
                            ew.load_data(ew.project_info)
                    except Exception:
                        pass
                # Refresh Sound Editor (lives in unified window, not PorySuite)
                project_dir = self.project_info.get("dir", "") if self.project_info else ""
                if project_dir and hasattr(self, "_sound_editor"):
                    try:
                        self._sound_editor.load_project(project_dir)
                    except Exception:
                        pass
                # Refresh Credits Editor
                if project_dir and hasattr(self, "_credits_editor"):
                    try:
                        self._credits_editor.load_project(project_dir)
                    except Exception:
                        pass
            target._git_pull(use_upstream=use_upstream,
                             on_refresh_done=_after_refresh)

    def _git_push(self):
        target = self._porysuite_window or self._eventide_window
        if target:
            target._git_push()

    def _git_commit(self):
        target = self._porysuite_window or self._eventide_window
        if target:
            target._git_commit()

    def _git_stash(self):
        target = self._porysuite_window or self._eventide_window
        if target:
            target._git_stash()

    def _git_pop_stash(self):
        target = self._porysuite_window or self._eventide_window
        if target:
            target._git_pop_stash()

    def _git_new_branch(self):
        target = self._porysuite_window or self._eventide_window
        if target:
            target._git_new_branch()

    def _git_checkout_branch(self, branch: str):
        target = self._porysuite_window or self._eventide_window
        if target:
            target._git_checkout_branch(branch)

    def _git_view_log(self):
        target = self._porysuite_window or self._eventide_window
        if target:
            target._git_view_log()

    def _git_refresh_status_bar(self):
        """Show current branch in the status bar."""
        if not self.project_info:
            return
        ok, branch = self._git_run("branch", "--show-current")
        if ok and branch:
            self._git_bar_label.setText(f"  {branch}  ")
        else:
            self._git_bar_label.setText("")

    # ── Saved-remotes persistence (needed by GitPanel) ───────────────────────

    def _remotes_file(self) -> str:
        from app_info import get_data_dir
        return os.path.join(get_data_dir(), "git_remotes.json")

    def _load_saved_remotes(self) -> list:
        import json
        cwd = (self.project_info or {}).get("dir", "")
        try:
            with open(self._remotes_file(), "r", encoding="utf-8") as f:
                data = json.load(f)
            return data.get(cwd, [])
        except Exception:
            return []

    def _save_saved_remotes(self, remotes: list) -> None:
        import json
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
        import json
        cwd = (self.project_info or {}).get("dir", "")
        try:
            with open(self._remotes_file(), "r", encoding="utf-8") as f:
                data = json.load(f)
            return data.get("__upstream__", {}).get(
                cwd, "https://github.com/pret/pokefirered.git")
        except Exception:
            return "https://github.com/pret/pokefirered.git"

    def _git_save_upstream_url(self, url: str) -> None:
        import json
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

    # ═════════════════════════════════════════════════════════════════════════
    # Porymap integration
    # ═════════════════════════════════════════════════════════════════════════

    def _start_bridge_watcher(self, project_dir: str):
        """Start (or restart) the Porymap bridge watcher for a project."""
        # Stop any existing watcher
        if self._bridge_watcher:
            self._bridge_watcher.stop()
            self._bridge_watcher.deleteLater()
            self._bridge_watcher = None

        if not project_dir:
            return

        self._bridge_watcher = BridgeWatcher(project_dir, parent=self)
        self._connect_bridge_signals()
        self._bridge_watcher.start()

    def _connect_bridge_signals(self):
        """Connect bridge watcher signals to the appropriate editors."""
        bw = self._bridge_watcher
        if not bw:
            return

        # Map navigation — when Porymap opens a map, switch Event Editor there
        bw.map_opened.connect(self._on_bridge_map_opened)
        bw.sync_requested.connect(self._on_bridge_map_opened)

        # Event selection — when user clicks an event in Porymap
        bw.event_selected.connect(self._on_bridge_event_selected)
        bw.edit_requested.connect(self._on_bridge_edit_requested)

        # Map data changes — reload when Porymap saves
        bw.map_saved.connect(self._on_bridge_map_saved)

        # Event lifecycle callbacks removed — these were patched into Porymap's
        # scripting layer but never wired to actual C++ invocation sites, so they
        # never fired. Cleaned up in v0.0.55b.

    def _on_bridge_map_opened(self, map_name: str):
        """Porymap opened a map — navigate Event Editor there."""
        if not map_name:
            return
        # Switch to Event Editor page
        self._switch_to_page("events")
        # Navigate the Event Editor to this map (suppress sync-back to Porymap)
        ew = self._eventide_window
        if ew and hasattr(ew, "event_editor_tab"):
            try:
                self._porymap_initiated_load = True
                self._last_porymap_sync_map = map_name
                ew.event_editor_tab.navigate_to_map(map_name)
            except Exception:
                pass
            finally:
                self._porymap_initiated_load = False
        self.log_message(f"Porymap: opened {map_name}")

    def _on_bridge_event_selected(self, map_name: str, event_type: str,
                                   event_index: int, script_label: str,
                                   x: int, y: int):
        """Porymap user clicked an event — select it in our Event Editor."""
        # Make sure we're on the right map first
        self._switch_to_page("events")
        self._bring_to_front()
        ew = self._eventide_window
        if not ew or not hasattr(ew, "event_editor_tab"):
            return
        tab = ew.event_editor_tab
        found = False
        try:
            self._porymap_initiated_load = True
            # Navigate to map if needed
            tab.navigate_to_map(map_name)
            # Select the event by type and index
            found = bool(tab.select_event_by_bridge(
                event_type, event_index, script_label))
        except Exception:
            pass
        finally:
            self._porymap_initiated_load = False
        if found:
            self.log_message(
                f"Porymap: selected {event_type} #{event_index} "
                f"({script_label or 'no script'}) at ({x},{y})")
        else:
            self.log_message(
                f"Porymap: could not match {event_type} #{event_index} "
                f"on {map_name} — data may be out of sync")

    def _on_bridge_edit_requested(self, map_name: str, x: int, y: int):
        """Porymap user pressed Ctrl+E — find event at position and select it.

        Brings the unified window forward so the user can see the result, and
        emits clear feedback: which event was picked, or that no event was
        near the hovered tile.
        """
        self._switch_to_page("events")
        self._bring_to_front()
        ew = self._eventide_window
        if not ew or not hasattr(ew, "event_editor_tab"):
            return
        tab = ew.event_editor_tab
        found = False
        try:
            self._porymap_initiated_load = True
            tab.navigate_to_map(map_name)
            found = bool(tab.select_event_at_position(x, y))
        except Exception:
            pass
        finally:
            self._porymap_initiated_load = False
        if found:
            self.log_message(
                f"Porymap Ctrl+E: selected event near {map_name} ({x},{y})")
        else:
            self.log_message(
                f"Porymap Ctrl+E: no event within 2 tiles of "
                f"{map_name} ({x},{y})")

    def _bring_to_front(self) -> None:
        """Raise the unified window above other apps (used by bridge callbacks).
        Cheap and safe to call repeatedly — no-op if already focused.
        """
        try:
            # Restore if minimized, raise, and activate
            from PyQt6.QtCore import Qt
            st = self.windowState()
            if st & Qt.WindowState.WindowMinimized:
                self.setWindowState(st & ~Qt.WindowState.WindowMinimized)
            self.raise_()
            self.activateWindow()
        except Exception:
            pass

    def _on_bridge_map_saved(self, map_name: str):
        """Porymap saved a map — reload our data for it."""
        ew = self._eventide_window
        if not ew or not hasattr(ew, "event_editor_tab"):
            return
        try:
            self._porymap_initiated_load = True
            tab = ew.event_editor_tab
            # Only reload if we're looking at the same map
            if hasattr(tab, "_current_map") and tab._current_map == map_name:
                tab.reload_current_map()
                self.log_message(f"Porymap saved {map_name} — reloaded")
        except Exception:
            pass
        finally:
            self._porymap_initiated_load = False

    # ═════════════════════════════════════════════════════════════════════════
    # Shared file watcher — detects external edits to project files
    # ═════════════════════════════════════════════════════════════════════════

    def _start_shared_file_watcher(self, project_dir: str):
        """Start (or restart) the shared file watcher for a project."""
        if self._shared_file_watcher:
            self._shared_file_watcher.stop()
            self._shared_file_watcher.deleteLater()
            self._shared_file_watcher = None

        if not project_dir:
            return

        self._shared_file_watcher = SharedFileWatcher(project_dir, parent=self)

        # Connect signals
        sfw = self._shared_file_watcher
        sfw.map_json_changed.connect(self._on_shared_map_changed)
        sfw.scripts_changed.connect(self._on_shared_scripts_changed)
        sfw.layouts_changed.connect(self._on_shared_layouts_changed)
        sfw.map_groups_changed.connect(self._on_shared_map_groups_changed)
        sfw.file_changed.connect(
            lambda rel: self.log_message(f"External change detected: {rel}"))

        sfw.start()

    def _on_shared_map_changed(self, map_folder: str):
        """A map's map.json was modified externally (likely by Porymap)."""
        ew = self._eventide_window
        if not ew or not hasattr(ew, "event_editor_tab"):
            return
        tab = ew.event_editor_tab
        # Only auto-reload if we're looking at the changed map
        if hasattr(tab, "_current_map") and tab._current_map == map_folder:
            try:
                self._porymap_initiated_load = True
                tab.reload_current_map()
                self.log_message(
                    f"Reloaded {map_folder} (map.json changed externally)")
            except Exception:
                pass
            finally:
                self._porymap_initiated_load = False

    def _on_shared_scripts_changed(self, map_folder: str):
        """A map's scripts.inc was modified externally."""
        ew = self._eventide_window
        if not ew or not hasattr(ew, "event_editor_tab"):
            return
        tab = ew.event_editor_tab
        if hasattr(tab, "_current_map") and tab._current_map == map_folder:
            try:
                self._porymap_initiated_load = True
                tab.reload_current_map()
                self.log_message(
                    f"Reloaded {map_folder} (scripts.inc changed externally)")
            except Exception:
                pass
            finally:
                self._porymap_initiated_load = False

    def _on_shared_layouts_changed(self):
        """layouts.json was modified externally — refresh the Layouts tab."""
        ew = self._eventide_window
        if not ew or not hasattr(ew, "layouts_tab"):
            return
        try:
            if self.project_info:
                ew.layouts_tab.load_project(self.project_info)
                self.log_message("Reloaded layouts (layouts.json changed externally)")
        except Exception:
            pass

    def _on_shared_map_groups_changed(self):
        """map_groups.json was modified externally — refresh the Maps tab."""
        ew = self._eventide_window
        if not ew or not hasattr(ew, "maps_tab"):
            return
        try:
            if self.project_info:
                ew.maps_tab.load_project(self.project_info)
                self.log_message(
                    "Reloaded maps (map_groups.json changed externally)")
        except Exception:
            pass

    def _install_porymap(self):
        """Tools → Install/Update Porymap. Downloads, patches, and builds."""
        pm_info = get_installed_porymap_info()

        if pm_info["installed"]:
            built_str = f"\n\nCurrently installed: built {pm_info['built']}, commit {pm_info['commit']}" if pm_info["built"] else ""
            reply = QMessageBox.question(
                self, "Update Porymap",
                "This will pull the latest Porymap source from GitHub, "
                "re-apply PorySuite-Z bridge patches, and recompile.\n\n"
                "Requires internet access.{}\n\n"
                "Continue?".format(built_str),
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            )
        else:
            reply = QMessageBox.question(
                self, "Install Porymap",
                "This will:\n\n"
                "  1. Download Porymap source from GitHub\n"
                "  2. Apply PorySuite-Z bridge patches (adds bidirectional\n"
                "     sync between PorySuite and Porymap)\n"
                "  3. Download Qt 6 SDK (~400 MB, one-time)\n"
                "  4. Compile Porymap from source\n"
                "  5. Deploy the binary with required DLLs\n\n"
                "Requires internet access. The Qt SDK download is large\n"
                "but only happens once.\n\n"
                "Continue?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            )

        if reply != QMessageBox.StandardButton.Yes:
            return

        try:
            from porymap_bridge.porymap_installer import run_install
            run_install(self)
        except ImportError:
            QMessageBox.information(
                self, "Not Yet Available",
                "The Porymap installer module is missing.",
            )
            return

        # Refresh menu state
        installed = is_porymap_installed()
        self._open_porymap_action.setEnabled(installed)
        self._check_porymap_update_action.setEnabled(installed)
        if installed:
            new_info = get_installed_porymap_info()
            label = "Update Porymap..."
            tip = ""
            if new_info["built"]:
                tip = (f"Currently installed: built {new_info['built']}"
                       f", commit {new_info['commit'] or 'unknown'}")
            self._install_porymap_action.setText(label)
            self._install_porymap_action.setToolTip(tip)
            self._open_porymap_action.setToolTip("")
            self.log_message("Porymap installed successfully")

    def _check_porymap_updates(self):
        """Tools → Check for Porymap Updates."""
        self.statusBar().showMessage("Checking for Porymap updates...", 3000)
        try:
            has_update, local, remote = check_porymap_update_available()
        except Exception as e:
            QMessageBox.warning(self, "Update Check Failed", str(e))
            return

        # Also check patch integrity
        patch_status = verify_patches_intact()
        patch_warning = ""
        if patch_status["status"] == "patches_replaced":
            patch_warning = (
                "\n\n⚠ WARNING: Your Porymap binary has changed since "
                "PorySuite last built it. This usually means Porymap's "
                "built-in updater replaced our patched version with a "
                "stock build. Use Tools → Update Porymap to re-patch."
            )
        elif patch_status["status"] == "stock":
            patch_warning = (
                "\n\nNote: Your Porymap has no PorySuite patches. "
                "Use Tools → Install Porymap to build a patched version "
                "with bidirectional sync."
            )

        if has_update:
            QMessageBox.information(
                self, "Porymap Update Available",
                f"A newer version of Porymap is available.\n\n"
                f"  Installed: {local}\n"
                f"  Latest:    {remote}\n\n"
                f"Use Tools → Update Porymap to download the latest source, "
                f"re-apply PorySuite bridge patches, and recompile.\n\n"
                f"Important: Do NOT update Porymap from within Porymap itself — "
                f"that replaces our patched build with stock Porymap and removes "
                f"the bidirectional sync bridge.{patch_warning}"
            )
        elif not remote:
            QMessageBox.information(
                self, "Update Check",
                "Could not reach the GitHub releases API to check for updates.\n"
                "Make sure you have internet access and try again.\n\n"
                "If this keeps happening, GitHub's API rate limit may be "
                "temporarily blocking requests (resets after a few minutes)."
                f"{patch_warning}"
            )
        else:
            QMessageBox.information(
                self, "Up to Date",
                f"Your Porymap is up to date (version {local})."
                f"{patch_warning}"
            )

    def _check_porymap_patch_integrity(self):
        """Run on project load — warn if Porymap was updated outside PorySuite."""
        if not is_porymap_installed():
            return
        patch_status = verify_patches_intact()
        if patch_status["status"] == "patches_replaced":
            QMessageBox.warning(
                self, "Porymap Patches Missing",
                "It looks like Porymap was updated outside of PorySuite — "
                "the binary has changed since we last built it.\n\n"
                "This happens when you use Porymap's built-in updater, "
                "which replaces our patched version with stock Porymap.\n\n"
                "The bidirectional sync bridge (map switching, event "
                "callbacks, etc.) will NOT work until you re-patch.\n\n"
                "Go to Tools → Update Porymap to rebuild with patches."
            )

    def _open_in_porymap(self):
        """Tools → Open in Porymap. Launches Porymap at the current map."""
        if not is_porymap_installed():
            QMessageBox.information(
                self, "Porymap Not Installed",
                "Install Porymap first via Tools → Install Porymap.",
            )
            return

        project_dir = (self.project_info or {}).get("dir", "")
        if not project_dir:
            QMessageBox.warning(self, "No Project", "Open a project first.")
            return

        # Try to get current map name — check multiple sources
        map_name = ""
        ew = self._eventide_window
        if ew:
            # 1. Event editor's currently loaded map
            if hasattr(ew, "event_editor_tab"):
                tab = ew.event_editor_tab
                if hasattr(tab, "_current_map"):
                    map_name = tab._current_map or ""
            # 2. Maps tab's currently selected map (if event editor has nothing)
            if not map_name and hasattr(ew, "maps_tab"):
                try:
                    data = ew.maps_tab._selected_item_data()
                    if data and data.get("type") == "map":
                        map_name = data.get("folder", "")
                except Exception:
                    pass
        # 3. Fallback: read map_groups.json and pick the first real town/route
        if not map_name:
            map_name = self._first_map_from_project(project_dir)

        ok = launch_porymap(project_dir, map_name)
        if ok:
            self.log_message(
                f"Launched Porymap"
                + (f" at {map_name}" if map_name else ""))
        else:
            QMessageBox.warning(
                self, "Launch Failed",
                "Could not launch Porymap. Check that it's installed correctly.",
            )

    @staticmethod
    def _first_map_from_project(project_dir: str) -> str:
        """Read map_groups.json and return the first town/route map name."""
        import json as _json
        groups_path = os.path.join(project_dir, "data", "maps", "map_groups.json")
        try:
            with open(groups_path, "r", encoding="utf-8") as f:
                data = _json.load(f)
        except (OSError, ValueError):
            return ""
        # Prefer TownsAndRoutes group for a sensible default
        for group_name in data.get("group_order", []):
            if "towns" in group_name.lower() or "route" in group_name.lower():
                maps = data.get(group_name, [])
                if maps:
                    return maps[0]
        # Otherwise grab the first map from the first non-empty group
        for group_name in data.get("group_order", []):
            maps = data.get(group_name, [])
            if maps:
                return maps[0]
        return ""

    def _on_map_loaded_sync_porymap(self, map_name: str):
        """Auto-sync: when Event Editor loads a map, tell Porymap to follow.

        Suppressed when:
        - The load was triggered BY Porymap (flag set by bridge handlers)
        - The map is the same one we last sent (prevents echo loops)
        """
        if self._porymap_initiated_load:
            return
        if not map_name or not is_porymap_installed():
            return
        # Command-file sync only works against our patched Porymap.
        if not is_porymap_patched():
            return
        # Don't re-send if Porymap already has this map (prevents echo loop
        # from bridge response arriving after the flag was cleared)
        if map_name == self._last_porymap_sync_map:
            return
        project_dir = (self.project_info or {}).get("dir", "")
        if not project_dir:
            return
        self._last_porymap_sync_map = map_name
        _send_command(project_dir, {"action": "openMap", "map": map_name})
