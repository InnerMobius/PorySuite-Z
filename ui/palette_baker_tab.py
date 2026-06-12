"""Palette Baker — manual tool for rewriting an indexed PNG's embedded
color table to match a separately-loaded palette.

Lives as a fourth sub-tab under Tilesets (alongside Tilemap Editor,
Tile Animations, GBA Image Indexer). Different responsibilities from
each:

  * Tilemap Editor edits one tilemap's palette.
  * Tile Animations edits frame-cycle palettes.
  * Image Indexer converts non-indexed sources INTO indexed form,
    picking a new palette as it goes.
  * **Palette Baker** takes an EXISTING indexed PNG whose embedded
    color table has drifted from the palette you want it to use, and
    rewrites the PNG's color table to match. Pixel indices stay
    exactly as they were — only the colour table changes. The "fix
    the stale baking" workflow.

Workflow:

  1. **Load PNG…** — pick an indexed PNG. Its current embedded
     palette appears on the left preview and in the "Currently
     baked" swatch row.
  2. **Load Palette…** — pick a ``.pal`` (JASC) or ``.gbapal``
     (binary) file. It populates the "Palette to apply" row and
     the right-side preview shows what the PNG would look like
     after a bake.
  3. **Save** — overwrites the PNG in place with the new colour
     table. Byte-equality guarded via ``export_indexed_png``, so
     a no-op bake (palette identical to baked) doesn't dirty the
     file.

The tab does NOT scan the project automatically. There is no
canonical PNG ↔ palette mapping in pokefirered — many ``.pal`` files
have a same-name PNG that doesn't actually use them (battle anims
get runtime palettes from the active battler, intro scenes have
hardcoded palettes in C, etc.). Only you know which palette belongs
with which PNG, so the tab is fully manual.

For the shared-palette case (one ``.pal`` shared by many PNGs —
typical for HUD elements), use **Bake to other PNGs…**: with a
palette loaded, this opens a multi-select file picker so you can
apply the same palette to every PNG you want, in one operation.

Lifecycle (per ``CLAUDE.md`` ``Tab load() / F5 Refresh Contract``):

  * ``load(project_root)`` resets all in-memory and visual dirty
    state. Used by F5 / project reload.
  * ``has_unsaved_changes()`` reports whether the loaded PNG has
    pending palette edits.
  * ``flush_to_disk()`` writes the current PNG if dirty (called by
    the unified save pipeline on Ctrl+S).

Bus integration (per ``CLAUDE.md`` ``Sprite Rendering Pipeline``):

  * Pushes to :class:`core.sprite_palette_bus.SpritePaletteBus` on
    every successful bake so other tabs (Pokemon Graphics, Trainer
    Graphics, Overworld GFX, etc.) invalidate their sprite caches
    without an F5.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import List, Optional, Tuple

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QColor, QImage, QPixmap
from PyQt6.QtWidgets import (
    QFileDialog, QFrame, QGroupBox, QHBoxLayout, QLabel, QMenu,
    QMessageBox, QPushButton, QStackedWidget, QToolButton, QVBoxLayout,
    QWidget,
)

Color = Tuple[int, int, int]

SWATCH_PX = 22
PREVIEW_BG = "#2a2a2a"


# ── Swatch widgets ──────────────────────────────────────────────────────────


class _SwatchCell(QFrame):
    """One swatch in a row. Read-only or editable. Editable variants
    open a colour picker on double-click and expose a Set-as-bg /
    Edit context menu.

    *stale* draws an amber 2px border around the swatch (used in the
    EDITABLE row when the slot's colour differs from the read-only
    "currently baked" row above).
    """

    color_edited = pyqtSignal(int, tuple)
    set_as_bg_requested = pyqtSignal(int)

    def __init__(self, slot: int, editable: bool, parent=None):
        super().__init__(parent)
        self._slot = slot
        self._editable = editable
        self._color: Color = (0, 0, 0)
        self._stale = False
        self.setFixedSize(SWATCH_PX, SWATCH_PX)
        self.setFrameShape(QFrame.Shape.Box)
        self._update_style()

    def slot(self) -> int:
        return self._slot

    def color(self) -> Color:
        return self._color

    def set_color(self, color: Color) -> None:
        self._color = color
        self._update_style()

    def set_stale(self, stale: bool) -> None:
        if stale != self._stale:
            self._stale = stale
            self._update_style()

    def _update_style(self) -> None:
        r, g, b = self._color
        border = "2px solid #ffb74d" if self._stale else "1px solid #555"
        self.setStyleSheet(
            f"QFrame {{ background: rgb({r}, {g}, {b}); "
            f"border: {border}; }}"
        )
        self.setToolTip(
            f"Slot {self._slot}  #{r:02X}{g:02X}{b:02X}"
            + ("  (differs from baked palette)" if self._stale else "")
        )

    def mouseDoubleClickEvent(self, event) -> None:
        if not self._editable:
            return
        from PyQt6.QtWidgets import QColorDialog
        dlg = QColorDialog(self)
        dlg.setCurrentColor(QColor(*self._color))
        if dlg.exec():
            c = dlg.selectedColor()
            r = (c.red() >> 3) << 3
            g = (c.green() >> 3) << 3
            b = (c.blue() >> 3) << 3
            self._color = (r, g, b)
            self._update_style()
            self.color_edited.emit(self._slot, self._color)

    def contextMenuEvent(self, event) -> None:
        if not self._editable:
            return
        menu = QMenu(self)
        a_bg = menu.addAction("Set as background (slot 0)")
        a_edit = menu.addAction("Edit colour…")
        chosen = menu.exec(event.globalPos())
        if chosen is a_bg:
            self.set_as_bg_requested.emit(self._slot)
        elif chosen is a_edit:
            self.mouseDoubleClickEvent(event)


class _SwatchRow(QWidget):
    """Row of N swatches. Used twice on the editor: once read-only for
    the PNG's currently-baked palette, once editable for the palette
    to apply.

    The widget auto-resizes the row to match the palette length passed
    to ``set_colors`` — for 8bpp PNGs (up to 256 slots) this avoids
    silently truncating display.
    """

    color_edited = pyqtSignal(int, tuple)
    set_as_bg_requested = pyqtSignal(int)

    def __init__(self, editable: bool = True, parent=None):
        super().__init__(parent)
        self._editable = editable
        self._cells: List[_SwatchCell] = []
        self._layout = QHBoxLayout(self)
        self._layout.setContentsMargins(0, 0, 0, 0)
        self._layout.setSpacing(2)
        self._stretch_added = False
        self._ensure_cells(16)

    def _ensure_cells(self, n: int) -> None:
        # Add cells if we don't have enough.
        while len(self._cells) < n:
            cell = _SwatchCell(len(self._cells), editable=self._editable,
                               parent=self)
            cell.color_edited.connect(self.color_edited)
            cell.set_as_bg_requested.connect(self.set_as_bg_requested)
            # Insert before the trailing stretch (if any).
            insert_at = (
                self._layout.count() - 1
                if self._stretch_added else self._layout.count()
            )
            self._layout.insertWidget(insert_at, cell)
            self._cells.append(cell)
        # Remove cells if we have too many.
        while len(self._cells) > n:
            cell = self._cells.pop()
            self._layout.removeWidget(cell)
            cell.deleteLater()
        if not self._stretch_added:
            self._layout.addStretch(1)
            self._stretch_added = True

    def set_colors(self, colors: List[Color]) -> None:
        n = max(16, len(colors))
        self._ensure_cells(n)
        for i, cell in enumerate(self._cells):
            c = colors[i] if i < len(colors) else (0, 0, 0)
            cell.set_color(c)

    def colors(self) -> List[Color]:
        return [c.color() for c in self._cells]

    def set_stale_mask(self, mask: List[bool]) -> None:
        for i, cell in enumerate(self._cells):
            cell.set_stale(mask[i] if i < len(mask) else False)


# ── Preview pane ────────────────────────────────────────────────────────────


class _PreviewPane(QLabel):
    """Renders one indexed PNG with one specific palette. The pixmap
    is rebuilt every time ``set_image`` or ``set_palette`` is called.
    Stays empty when either is missing.
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumSize(192, 192)
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.setStyleSheet(
            f"QLabel {{ background: {PREVIEW_BG}; color: #888; }}"
        )
        self._image: Optional[QImage] = None
        self._palette: List[Color] = []
        self.setText("(no image)")

    def set_image(self, img: Optional[QImage]) -> None:
        self._image = img.copy() if img is not None and not img.isNull() else None
        self._refresh()

    def set_palette(self, palette: List[Color]) -> None:
        self._palette = list(palette) if palette else []
        self._refresh()

    def clear_all(self) -> None:
        self._image = None
        self._palette = []
        self.setText("(no image)")
        self.setPixmap(QPixmap())

    def _refresh(self) -> None:
        if self._image is None:
            self.setText("(no image)")
            self.setPixmap(QPixmap())
            return
        if not self._palette:
            self.setText("")
            pm = QPixmap.fromImage(self._image).scaled(
                self.size(), Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.FastTransformation,
            )
            self.setPixmap(pm)
            return
        if self._image.format() != QImage.Format.Format_Indexed8:
            self.setText("(non-indexed PNG)")
            self.setPixmap(QPixmap())
            return
        recoloured = self._image.copy()
        ct = []
        # Detect transparent slot from the source PNG's color table so
        # we don't blanket-force slot 0 transparent (which clobbered
        # opaque slot-0 sources in the earlier draft).
        transparent_idx = -1
        try:
            src_ct = self._image.colorTable()
            for i, argb in enumerate(src_ct):
                if ((argb >> 24) & 0xFF) == 0:
                    transparent_idx = i
                    break
        except Exception:
            pass
        for i, (r, g, b) in enumerate(self._palette):
            alpha = 0 if i == transparent_idx else 255
            ct.append((alpha << 24) | (r << 16) | (g << 8) | b)
        # Pad colour table out to whatever the image used originally.
        while len(ct) < max(16, len(recoloured.colorTable())):
            ct.append(0xFF000000)
        recoloured.setColorTable(ct)
        pm = QPixmap.fromImage(recoloured).scaled(
            self.size(), Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.FastTransformation,
        )
        self.setText("")
        self.setPixmap(pm)

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._refresh()


# ── Editor state ────────────────────────────────────────────────────────────


@dataclass
class _EditorState:
    """Holds the currently-loaded PNG + palette for the right-panel
    editor."""
    png_path: str = ""
    pal_path: str = ""
    image: Optional[QImage] = None
    baked: List[Color] = field(default_factory=list)
    edited: List[Color] = field(default_factory=list)
    is_dirty: bool = False


# ── Main tab widget ────────────────────────────────────────────────────────


class PaletteBakerTab(QWidget):
    """Sub-tab under Tilesets. See module docstring."""

    modified = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._project_root = ""
        self._editor = _EditorState()
        self._loading = False
        self._build_ui()

    # ── UI construction ─────────────────────────────────────────────────────

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(8, 8, 8, 8)
        root.setSpacing(6)

        # Source group: file labels + Load buttons.
        ctx = QGroupBox("Source")
        ctx_layout = QVBoxLayout(ctx)
        ctx_layout.setSpacing(2)
        self._lbl_png = QLabel("(no PNG loaded)")
        self._lbl_png.setStyleSheet("QLabel { color: #ccc; }")
        self._lbl_png.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse)
        ctx_layout.addWidget(self._lbl_png)
        self._lbl_pal = QLabel("(no palette loaded)")
        self._lbl_pal.setStyleSheet("QLabel { color: #888; font-size: 11px; }")
        self._lbl_pal.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse)
        ctx_layout.addWidget(self._lbl_pal)

        ctx_btns = QHBoxLayout()
        ctx_btns.setSpacing(4)
        self._btn_load_png = QPushButton("Load PNG…")
        self._btn_load_png.clicked.connect(self._on_load_png_clicked)
        ctx_btns.addWidget(self._btn_load_png)
        self._btn_open_pal = QPushButton("Open Palette File…")
        self._btn_open_pal.setToolTip(
            "Open a .pal or .gbapal directly to view + edit its colours, then "
            "Save writes back to that file (no PNG needed).")
        self._btn_open_pal.clicked.connect(self._on_open_palette_file)
        ctx_btns.addWidget(self._btn_open_pal)
        self._btn_load_pal = QPushButton("Load Palette…")
        self._btn_load_pal.clicked.connect(self._on_load_pal_clicked)
        self._btn_load_pal.setEnabled(False)
        ctx_btns.addWidget(self._btn_load_pal)
        # Palette dropdown for export / reset.
        self._btn_pal_menu = QToolButton()
        self._btn_pal_menu.setText("Palette ▾")
        self._btn_pal_menu.setPopupMode(
            QToolButton.ToolButtonPopupMode.InstantPopup)
        pal_menu = QMenu(self._btn_pal_menu)
        pal_menu.addAction("Export current palette as .pal…",
                           self._on_export_pal_clicked)
        pal_menu.addAction("Reset to baked",
                           self._on_reset_to_baked)
        self._btn_pal_menu.setMenu(pal_menu)
        self._btn_pal_menu.setEnabled(False)
        ctx_btns.addWidget(self._btn_pal_menu)
        ctx_btns.addStretch(1)
        ctx_layout.addLayout(ctx_btns)
        root.addWidget(ctx)

        # Main area is a stack: empty-state vs editor.
        self._stack = QStackedWidget()
        root.addWidget(self._stack, 1)

        # Page 0 — empty state.
        empty = QLabel(
            "Nothing loaded.\n\n"
            "<b>Open Palette File…</b> — edit a .pal or .gbapal directly and "
            "save the colours back to it.\n\n"
            "<b>Load PNG…</b> — bake a palette into an indexed PNG's colour "
            "table (then Load Palette… to pick the palette to apply; pixel "
            "indices are never changed).")
        empty.setAlignment(Qt.AlignmentFlag.AlignCenter)
        empty.setWordWrap(True)
        empty.setStyleSheet("QLabel { color: #888; padding: 40px; }")
        self._stack.addWidget(empty)

        # Page 1 — editor body.
        self._stack.addWidget(self._build_editor_body())
        self._stack.setCurrentIndex(0)

        # Bottom action row.
        actions = QHBoxLayout()
        actions.setSpacing(4)
        self._btn_bake_others = QPushButton("Bake to other PNGs…")
        self._btn_bake_others.setToolTip(
            "Apply the currently-loaded palette to other PNG files "
            "you pick. Useful when one .pal is shared across many "
            "PNGs (typical HUD case).")
        self._btn_bake_others.setEnabled(False)
        self._btn_bake_others.clicked.connect(self._on_bake_others_clicked)
        actions.addWidget(self._btn_bake_others)
        actions.addStretch(1)
        self._btn_revert = QPushButton("Revert")
        self._btn_revert.setEnabled(False)
        self._btn_revert.clicked.connect(self._on_revert_clicked)
        actions.addWidget(self._btn_revert)
        self._btn_save = QPushButton("Save")
        self._btn_save.setEnabled(False)
        self._btn_save.clicked.connect(self._on_save_clicked)
        actions.addWidget(self._btn_save)
        root.addLayout(actions)

    def _build_editor_body(self) -> QWidget:
        wrap = QWidget()
        layout = QVBoxLayout(wrap)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)

        # Side-by-side previews (hidden in palette-file mode — no PNG).
        prev_row = QHBoxLayout()
        prev_row.setSpacing(8)
        self._prev_left = QGroupBox("As baked (PNG's own color table)")
        ll = QVBoxLayout(self._prev_left)
        self._preview_baked = _PreviewPane()
        ll.addWidget(self._preview_baked)
        prev_row.addWidget(self._prev_left, 1)

        self._prev_right = QGroupBox("With palette to apply")
        rl = QVBoxLayout(self._prev_right)
        self._preview_applied = _PreviewPane()
        rl.addWidget(self._preview_applied)
        prev_row.addWidget(self._prev_right, 1)
        layout.addLayout(prev_row, 1)

        # Status line.
        self._status_label = QLabel("")
        self._status_label.setStyleSheet(
            "QLabel { color: #aaa; font-size: 11px; }")
        layout.addWidget(self._status_label)

        # Currently-baked / on-disk palette row (read-only).
        self._lbl_baked_row = QLabel("Currently baked:")
        layout.addWidget(self._lbl_baked_row)
        self._row_baked = _SwatchRow(editable=False)
        layout.addWidget(self._row_baked)

        # Editable palette row.
        self._lbl_edit_row = QLabel("Palette to apply:")
        layout.addWidget(self._lbl_edit_row)
        self._row_edit = _SwatchRow(editable=True)
        self._row_edit.color_edited.connect(self._on_swatch_edited)
        self._row_edit.set_as_bg_requested.connect(self._on_set_as_bg)
        layout.addWidget(self._row_edit)

        return wrap

    # ── Lifecycle (load / F5) ───────────────────────────────────────────────

    def load(self, project_root: str) -> None:
        """Project-load entry point. Resets all in-memory and visual
        state. Implements the F5 contract: drop the editor, reset
        labels, swatches, previews, and action buttons. The
        ``_loading`` guard prevents signals fired during the reset
        from re-marking dirty state.
        """
        self._loading = True
        try:
            self._editor = _EditorState()
            self._project_root = project_root or ""
            self._lbl_png.setText("(no PNG loaded)")
            self._lbl_pal.setText("(no palette loaded)")
            self._row_baked.set_colors([(0, 0, 0)] * 16)
            self._row_edit.set_colors([(0, 0, 0)] * 16)
            self._row_edit.set_stale_mask([False] * 16)
            self._preview_baked.clear_all()
            self._preview_applied.clear_all()
            self._status_label.setText("")
            self._stack.setCurrentIndex(0)
            self._btn_load_pal.setEnabled(False)
            self._btn_pal_menu.setEnabled(False)
            self._btn_save.setEnabled(False)
            self._btn_revert.setEnabled(False)
            self._btn_bake_others.setEnabled(False)
        finally:
            self._loading = False

    # ── Load PNG / palette ──────────────────────────────────────────────────

    def _on_load_png_clicked(self) -> None:
        # If the editor has unsaved edits, ask before swapping.
        if self._editor.is_dirty:
            ret = QMessageBox.question(
                self, "Discard edits?",
                "You have unsaved palette edits. Discard them and "
                "load a different PNG?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            if ret == QMessageBox.StandardButton.No:
                return
        start = (
            os.path.dirname(self._editor.png_path)
            if self._editor.png_path
            else (self._project_root or "")
        )
        png_path, _ = QFileDialog.getOpenFileName(
            self, "Load PNG", start, "PNG Images (*.png)"
        )
        if not png_path:
            return
        from core.palette_bake_audit import read_png_color_table
        baked = read_png_color_table(png_path)
        if baked is None:
            QMessageBox.warning(
                self, "Palette Baker",
                "This PNG isn't an indexed (palette) image. Use the "
                "Image Indexer tab to convert non-indexed sources first.")
            return
        img = QImage(png_path)
        # Convert Mono / MonoLSB up to Indexed8 so the rest of the
        # pipeline only deals with one format.
        if img.format() in (
            QImage.Format.Format_Mono,
            QImage.Format.Format_MonoLSB,
        ):
            img = img.convertToFormat(QImage.Format.Format_Indexed8)
        if img.isNull() or img.format() != QImage.Format.Format_Indexed8:
            QMessageBox.warning(
                self, "Palette Baker",
                f"Couldn't load {os.path.basename(png_path)} as "
                "an indexed PNG.")
            return
        self._editor = _EditorState(
            png_path=png_path,
            pal_path="",
            image=img,
            baked=list(baked),
            edited=list(baked),  # default: edit-target = currently-baked
            is_dirty=False,
        )
        self._refresh_editor_views()
        self._stack.setCurrentIndex(1)
        self._btn_load_pal.setEnabled(True)
        self._btn_pal_menu.setEnabled(True)
        self._btn_save.setEnabled(False)
        self._btn_revert.setEnabled(False)
        # Bake-to-others is enabled the moment a palette is loaded.
        self._btn_bake_others.setEnabled(False)

    def _on_load_pal_clicked(self) -> None:
        if not self._editor.png_path:
            return
        start = (
            os.path.dirname(self._editor.pal_path)
            if self._editor.pal_path
            else os.path.dirname(self._editor.png_path)
        )
        pal_path, _ = QFileDialog.getOpenFileName(
            self, "Load palette", start,
            "Palette files (*.pal *.gbapal);;JASC palette (*.pal);;"
            "GBA palette (*.gbapal);;All files (*)",
        )
        if not pal_path:
            return
        new_pal = self._read_palette_file(pal_path)
        if new_pal is None:
            QMessageBox.warning(
                self, "Palette Baker",
                f"Couldn't read palette from\n{pal_path}")
            return
        self._editor.pal_path = pal_path
        self._editor.edited = list(new_pal)
        # Loading a palette is a deliberate action — mark dirty so
        # Save / Revert light up immediately.
        self._editor.is_dirty = (new_pal != self._editor.baked)
        self._refresh_editor_views()
        self._btn_save.setEnabled(self._editor.is_dirty)
        self._btn_revert.setEnabled(self._editor.is_dirty)
        self._btn_bake_others.setEnabled(True)
        self.modified.emit()

    @staticmethod
    def _read_palette_file(path: str) -> Optional[List[Color]]:
        """Read a palette file. Supports JASC ``.pal`` (text) and
        binary ``.gbapal`` (one or more 16-color sub-palettes packed
        as raw 5-bit-per-channel BGR555).
        """
        if not path or not os.path.isfile(path):
            return None
        ext = os.path.splitext(path)[1].lower()
        if ext == ".gbapal":
            try:
                with open(path, "rb") as f:
                    raw = f.read()
            except OSError:
                return None
            # Parse two-byte little-endian BGR555 entries.
            colors: List[Color] = []
            for i in range(0, len(raw) - 1, 2):
                v = raw[i] | (raw[i + 1] << 8)
                r5 = v & 0x1F
                g5 = (v >> 5) & 0x1F
                b5 = (v >> 10) & 0x1F
                # 5-bit -> 8-bit by shift-and-or-self (matches
                # gbagfx / Porymap convention).
                r = (r5 << 3) | (r5 >> 2)
                g = (g5 << 3) | (g5 >> 2)
                b = (b5 << 3) | (b5 >> 2)
                colors.append((r, g, b))
            return colors or None
        # JASC .pal — defer to ui.palette_utils.
        try:
            from ui.palette_utils import read_jasc_pal
            colors = read_jasc_pal(path, max_colors=0) or []
        except Exception:
            return None
        return colors or None

    def _on_open_palette_file(self) -> None:
        """Open a .pal / .gbapal directly for editing (no PNG). Save writes the
        edited colours straight back to that file."""
        if self._editor.is_dirty:
            ret = QMessageBox.question(
                self, "Discard edits?",
                "You have unsaved palette edits. Discard them and open a "
                "different palette file?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No)
            if ret == QMessageBox.StandardButton.No:
                return
        start = (os.path.dirname(self._editor.pal_path)
                 if self._editor.pal_path else (self._project_root or ""))
        path, _ = QFileDialog.getOpenFileName(
            self, "Open palette file", start,
            "Palette files (*.pal *.gbapal);;JASC palette (*.pal);;"
            "GBA palette (*.gbapal);;All files (*)")
        if not path:
            return
        pal = self._read_palette_file(path)
        if pal is None:
            QMessageBox.warning(self, "Palette Editor",
                                f"Couldn't read a palette from\n{path}")
            return
        self._editor = _EditorState(
            png_path="", pal_path=path, image=None,
            baked=list(pal), edited=list(pal), is_dirty=False)
        self._refresh_editor_views()
        self._stack.setCurrentIndex(1)
        self._btn_load_pal.setEnabled(False)    # no PNG to bake into
        self._btn_pal_menu.setEnabled(True)      # export-as-.pal still works
        self._btn_bake_others.setEnabled(True)   # can still bake into PNGs
        self._btn_save.setEnabled(False)         # nothing edited yet
        self._btn_revert.setEnabled(False)
        self.modified.emit()

    @staticmethod
    def _write_palette_file(path: str, colors: List[Color]):
        """Write a palette back to its .pal (JASC text) or .gbapal (binary
        BGR555) file. Returns (ok, error_message)."""
        ext = os.path.splitext(path)[1].lower()
        try:
            if ext == ".gbapal":
                import struct
                data = bytearray()
                for (r, g, b) in colors:
                    v = ((b >> 3) << 10) | ((g >> 3) << 5) | (r >> 3)
                    data += struct.pack("<H", v & 0x7FFF)
                with open(path, "wb") as f:
                    f.write(bytes(data))
            else:
                from ui.palette_utils import write_jasc_pal
                write_jasc_pal(path, colors)
            return True, ""
        except Exception as exc:
            return False, str(exc)

    def _on_export_pal_clicked(self) -> None:
        if not self._editor.edited:
            return
        start = (
            os.path.dirname(self._editor.pal_path)
            if self._editor.pal_path
            else os.path.dirname(self._editor.png_path)
        )
        pal_path, _ = QFileDialog.getSaveFileName(
            self, "Export palette", start, "JASC Palette (*.pal)"
        )
        if not pal_path:
            return
        if not pal_path.lower().endswith(".pal"):
            pal_path += ".pal"
        from ui.palette_utils import write_jasc_pal
        try:
            write_jasc_pal(pal_path, self._editor.edited)
            QMessageBox.information(
                self, "Palette Baker",
                f"Wrote {os.path.basename(pal_path)}.")
        except Exception as exc:
            QMessageBox.warning(
                self, "Palette Baker", f"Write failed:\n{exc}")

    def _on_reset_to_baked(self) -> None:
        if not self._editor.baked:
            return
        self._editor.edited = list(self._editor.baked)
        self._editor.is_dirty = False
        self._refresh_editor_views()
        self._update_dirty_buttons()
        self.modified.emit()

    # ── View refresh ────────────────────────────────────────────────────────

    def _refresh_editor_views(self) -> None:
        pal_mode = (not self._editor.png_path) and bool(self._editor.pal_path)
        # Source labels.
        if self._editor.png_path:
            rel_png = (
                os.path.relpath(self._editor.png_path, self._project_root)
                if self._project_root else self._editor.png_path)
            self._lbl_png.setText(f"<b>{rel_png}</b>")
        elif pal_mode:
            self._lbl_png.setText(
                "<i>(editing a palette file directly — no PNG)</i>")
        else:
            self._lbl_png.setText("(no PNG loaded)")
        if self._editor.pal_path:
            rel_pal = (
                os.path.relpath(self._editor.pal_path, self._project_root)
                if self._project_root else self._editor.pal_path)
            self._lbl_pal.setText(
                f"<b>{rel_pal}</b>" if pal_mode else f"Palette: {rel_pal}")
        else:
            self._lbl_pal.setText(
                "<i style='color:#888;'>(no palette loaded — "
                "click Load Palette… to pick one)</i>"
            )
        # Previews — hidden when editing a palette file directly (no image).
        self._prev_left.setVisible(not pal_mode)
        self._prev_right.setVisible(not pal_mode)
        self._preview_baked.set_image(self._editor.image)
        self._preview_baked.set_palette(self._editor.baked)
        self._preview_applied.set_image(self._editor.image)
        self._preview_applied.set_palette(self._editor.edited)
        # Swatch rows (relabelled in palette-file mode).
        self._lbl_baked_row.setText(
            "On disk:" if pal_mode else "Currently baked:")
        self._lbl_edit_row.setText(
            "Edit colours:" if pal_mode else "Palette to apply:")
        self._row_baked.set_colors(self._editor.baked)
        self._row_edit.set_colors(self._editor.edited)
        self._refresh_stale_view()

    def _refresh_stale_view(self) -> None:
        """Update the amber stale-mask + status line based on edited
        vs baked colours, and refresh the right preview."""
        n = max(len(self._editor.baked), len(self._editor.edited), 16)
        mask = []
        diffs = 0
        for i in range(n):
            b = self._editor.baked[i] if i < len(self._editor.baked) else None
            e = self._editor.edited[i] if i < len(self._editor.edited) else None
            differs = (b != e) and (b is not None) and (e is not None)
            mask.append(differs)
            if differs:
                diffs += 1
        self._row_edit.set_stale_mask(mask)
        pal_mode = (not self._editor.png_path) and bool(self._editor.pal_path)
        if not self._editor.pal_path and not self._editor.is_dirty:
            self._status_label.setText(
                "Load a palette to see what would change.")
        elif diffs == 0:
            self._status_label.setText(
                "<span style='color:#7c7;'>● Matches</span> — the colours "
                "equal what's on disk; saving would be a no-op."
            )
        elif pal_mode:
            self._status_label.setText(
                f"<span style='color:#ffb74d;'>● {diffs} of {n} slots "
                "edited</span> — Save writes these colours back to the "
                "palette file."
            )
        else:
            self._status_label.setText(
                f"<span style='color:#ffb74d;'>● {diffs} of {n} slots "
                "differ</span> — Save to bake the new palette into "
                "this PNG."
            )
        self._preview_applied.set_palette(self._editor.edited)

    def _update_dirty_buttons(self) -> None:
        self._btn_save.setEnabled(self._editor.is_dirty)
        self._btn_revert.setEnabled(self._editor.is_dirty)

    # ── Swatch edits ────────────────────────────────────────────────────────

    def _on_swatch_edited(self, slot: int, color: tuple) -> None:
        if self._loading:
            return
        if slot < len(self._editor.edited):
            self._editor.edited[slot] = color
        else:
            while len(self._editor.edited) <= slot:
                self._editor.edited.append((0, 0, 0))
            self._editor.edited[slot] = color
        self._editor.is_dirty = True
        self._refresh_stale_view()
        self._update_dirty_buttons()
        # Bake-to-others becomes available the moment we have an
        # editable palette in hand, even if it was hand-edited rather
        # than loaded from a file.
        self._btn_bake_others.setEnabled(True)
        self.modified.emit()

    def _on_set_as_bg(self, slot: int) -> None:
        if slot == 0 or slot >= len(self._editor.edited):
            return
        ed = self._editor.edited
        ed[0], ed[slot] = ed[slot], ed[0]
        self._row_edit.set_colors(ed)
        self._editor.is_dirty = True
        self._refresh_stale_view()
        self._update_dirty_buttons()
        self.modified.emit()

    def _on_revert_clicked(self) -> None:
        if not self._editor.baked:
            return
        self._editor.edited = list(self._editor.baked)
        self._editor.is_dirty = False
        self._refresh_editor_views()
        self._update_dirty_buttons()
        self.modified.emit()

    # ── Save (single) ───────────────────────────────────────────────────────

    def _on_save_clicked(self) -> None:
        if not self._editor.edited:
            return
        # Palette-file mode: write the colours back to the .pal / .gbapal.
        if not self._editor.png_path and self._editor.pal_path:
            ok, err = self._write_palette_file(
                self._editor.pal_path, self._editor.edited)
            if not ok:
                QMessageBox.warning(
                    self, "Palette Editor",
                    f"Failed to write\n{self._editor.pal_path}\n\n{err}")
                return
            self._editor.baked = list(self._editor.edited)
            self._editor.is_dirty = False
            self._row_baked.set_colors(self._editor.baked)
            self._refresh_stale_view()
            self._update_dirty_buttons()
            self.modified.emit()
            return
        # PNG-bake mode.
        if not self._editor.png_path:
            return
        from core.palette_bake_audit import bake_palette_into_png
        ok = bake_palette_into_png(
            self._editor.png_path, self._editor.edited
        )
        if not ok:
            QMessageBox.warning(
                self, "Palette Editor",
                f"Failed to write\n{self._editor.png_path}")
            return
        # The PNG's baked palette is now the edited palette.
        self._editor.baked = list(self._editor.edited)
        self._editor.is_dirty = False
        self._row_baked.set_colors(self._editor.baked)
        self._refresh_stale_view()
        self._update_dirty_buttons()
        self._push_to_bus(self._editor.png_path, self._editor.edited)
        self.modified.emit()

    # ── Bake to other PNGs (manual multi-select) ───────────────────────────

    def _on_bake_others_clicked(self) -> None:
        """Apply the loaded palette to other PNG files the user picks.

        Opens a multi-select file picker defaulting to the loaded
        PNG's folder. The user controls exactly which PNGs are
        affected — the tool does NOT auto-resolve any "family." This
        is the right primitive for the shared-palette case (one .pal
        used by many PNGs that don't share its name).
        """
        if not self._editor.edited:
            return
        start = (
            os.path.dirname(self._editor.png_path)
            if self._editor.png_path
            else self._project_root or ""
        )
        paths, _ = QFileDialog.getOpenFileNames(
            self, "Bake this palette into which PNGs?",
            start, "PNG Images (*.png)"
        )
        if not paths:
            return
        ret = QMessageBox.question(
            self, "Bake to other PNGs?",
            f"This will rewrite the colour table in {len(paths)} "
            "PNG(s) using the currently-loaded palette. Pixel "
            "indices stay unchanged.<br><br>"
            "Continue?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.Yes,
        )
        if ret != QMessageBox.StandardButton.Yes:
            return
        from core.palette_bake_audit import bake_palette_into_png
        n_ok = 0
        n_skip = 0
        for png in paths:
            ok = bake_palette_into_png(png, self._editor.edited)
            if ok:
                n_ok += 1
                self._push_to_bus(png, self._editor.edited)
            else:
                n_skip += 1
        msg = f"Baked {n_ok} of {len(paths)} PNG(s)."
        if n_skip:
            msg += (
                f"<br><br>{n_skip} skipped (likely non-indexed — use "
                "the Image Indexer to convert first)."
            )
        QMessageBox.information(self, "Palette Baker", msg)

    # ── Bus integration ─────────────────────────────────────────────────────

    def _push_to_bus(self, png_path: str, palette: List[Color]) -> None:
        """Notify the sprite-palette bus that this PNG's palette
        changed. Best-effort category detection from path. Other tabs
        subscribe to the bus and invalidate their sprite caches when
        a category they care about updates.
        """
        try:
            from core.sprite_palette_bus import (
                get_bus, CAT_TRAINER_PIC, CAT_OVERWORLD,
                CAT_POKEMON, CAT_ITEM_ICON,
            )
        except Exception:
            return
        bus = get_bus()
        rel = os.path.relpath(png_path, self._project_root or "/")
        rel_lower = rel.replace("\\", "/").lower()
        if "trainers/" in rel_lower:
            bus.set_palette(CAT_TRAINER_PIC, png_path, palette)
        elif "object_events/" in rel_lower or "field_effects/" in rel_lower \
                or "overworld" in rel_lower:
            bus.set_palette(CAT_OVERWORLD, png_path, palette)
        elif "pokemon/" in rel_lower:
            bus.set_palette(CAT_POKEMON, png_path, palette)
        elif "items/" in rel_lower or "item_icons" in rel_lower:
            bus.set_palette(CAT_ITEM_ICON, png_path, palette)
        else:
            bus.set_palette("unknown", png_path, palette)

    # ── Save-pipeline integration ───────────────────────────────────────────

    def has_unsaved_changes(self) -> bool:
        return self._editor.is_dirty

    def flush_to_disk(self) -> Tuple[int, List[str]]:
        """Called by the unified save pipeline (Ctrl+S). Saves the
        editor's current PNG if dirty.
        """
        if not self._editor.is_dirty:
            return 0, []
        # Palette-file mode: write the .pal / .gbapal back.
        if not self._editor.png_path and self._editor.pal_path:
            ok, _err = self._write_palette_file(
                self._editor.pal_path, self._editor.edited)
            if ok:
                self._editor.baked = list(self._editor.edited)
                self._editor.is_dirty = False
                self._row_baked.set_colors(self._editor.baked)
                self._refresh_stale_view()
                self._update_dirty_buttons()
                return 1, []
            return 0, [self._editor.pal_path]
        # PNG-bake mode.
        from core.palette_bake_audit import bake_palette_into_png
        ok = bake_palette_into_png(
            self._editor.png_path, self._editor.edited)
        if ok:
            self._editor.baked = list(self._editor.edited)
            self._editor.is_dirty = False
            self._row_baked.set_colors(self._editor.baked)
            self._refresh_stale_view()
            self._update_dirty_buttons()
            self._push_to_bus(self._editor.png_path, self._editor.edited)
            return 1, []
        return 0, [self._editor.png_path]
