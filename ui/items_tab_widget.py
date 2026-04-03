"""
ui/items_tab_widget.py
Premium Items Editor
  Left  – searchable list of all items with icons
  Right – scrollable detail panel covering every field of the Item struct
          with a large icon in the header
"""

from __future__ import annotations

import os
import re

from PyQt6.QtCore import Qt, pyqtSignal, QRect, QSize
from PyQt6.QtGui import QFont, QIcon, QPixmap, QPainter, QColor
from PyQt6.QtWidgets import (
    QCheckBox, QComboBox, QFormLayout, QFrame,
    QGroupBox, QHBoxLayout, QLabel, QLineEdit,
    QListWidget, QListWidgetItem, QPlainTextEdit,
    QPushButton, QScrollArea, QSizePolicy,
    QSpinBox, QSplitter, QStyle, QStyledItemDelegate,
    QStyleOptionViewItem, QVBoxLayout, QWidget,
)

from ui.dex_description_edit import DexDescriptionEdit

# ── pocket / type lookup tables (single source of truth in ui.constants) ──────
from ui.constants import (
    POCKET_CHOICES, ITEM_TYPE_CHOICES, HOLD_EFFECT_CHOICES,
    FIELD_USE_FUNC_CHOICES, BATTLE_USE_FUNC_CHOICES,
)

# ── icon resolution ───────────────────────────────────────────────────────────

from ui.open_folder_util import open_in_folder


def _parse_item_icon_map(project_path: str) -> dict[str, str]:
    """
    Returns {ITEM_CONSTANT: abs_png_path} by chaining two C-header lookups:
      src/data/item_icon_table.h  : [ITEM_X] = {gItemIcon_Y, ...}
      src/data/graphics/items.h   : gItemIcon_Y[] = INCBIN_U32("path.4bpp.lz")
    PNG lives at the same path with .png extension.
    """
    icon_table = os.path.join(project_path, "src", "data", "item_icon_table.h")
    gfx_header  = os.path.join(project_path, "src", "data", "graphics", "items.h")

    # Step 1: ITEM_CONSTANT → gItemIcon_Symbol
    const_to_sym: dict[str, str] = {}
    try:
        text = open(icon_table, encoding="utf-8", errors="surrogateescape").read()
        for m in re.finditer(r'\[(\w+)\]\s*=\s*\{(\w+),', text):
            const_to_sym[m.group(1)] = m.group(2)
    except OSError:
        pass

    # Step 2: gItemIcon_Symbol → relative .4bpp.lz path
    sym_to_rel: dict[str, str] = {}
    try:
        text = open(gfx_header, encoding="utf-8", errors="surrogateescape").read()
        for m in re.finditer(
            r'const u32 (\w+)\[\]\s*=\s*INCBIN_U32\("([^"]+)"\)', text
        ):
            sym_to_rel[m.group(1)] = m.group(2)
    except OSError:
        pass

    # Step 3: chain → absolute PNG path (same path, .png instead of .4bpp.lz)
    result: dict[str, str] = {}
    for const, sym in const_to_sym.items():
        rel = sym_to_rel.get(sym, "")
        if not rel:
            continue
        png_rel = re.sub(r"\.4bpp\.lz$", ".png", rel)
        abs_png = os.path.join(project_path, png_rel)
        if os.path.isfile(abs_png):
            result[const] = abs_png

    return result


def _parse_item_icon_full(project_path: str):
    """Parse full item-icon data for the icon picker.

    Returns:
        item_syms : {ITEM_CONST: (icon_sym, palette_sym)}
        icon_choices : [(icon_sym, abs_png_path)] sorted by display name
        sym_to_png : {icon_sym: abs_png_path}
        icon_to_default_pal : {icon_sym: palette_sym}  (first palette seen)
    """
    icon_table = os.path.join(project_path, "src", "data", "item_icon_table.h")
    gfx_header = os.path.join(project_path, "src", "data", "graphics", "items.h")

    # Step 1: per-item icon+palette symbols
    item_syms: dict[str, tuple[str, str]] = {}
    try:
        text = open(icon_table, encoding="utf-8", errors="surrogateescape").read()
        for m in re.finditer(
            r'\[(\w+)\]\s*=\s*\{(\w+),\s*(\w+)\}', text
        ):
            item_syms[m.group(1)] = (m.group(2), m.group(3))
    except OSError:
        pass

    # Step 2: symbol → relative path (icons only)
    sym_to_rel: dict[str, str] = {}
    try:
        text = open(gfx_header, encoding="utf-8", errors="surrogateescape").read()
        for m in re.finditer(
            r'const u32 (\w+)\[\]\s*=\s*INCBIN_U32\("([^"]+)"\)', text
        ):
            sym_to_rel[m.group(1)] = m.group(2)
    except OSError:
        pass

    # Step 3: resolve icon symbols to PNGs
    sym_to_png: dict[str, str] = {}
    for sym, rel in sym_to_rel.items():
        if not sym.startswith("gItemIcon_"):
            continue
        png_rel = re.sub(r"\.4bpp\.lz$", ".png", rel)
        abs_png = os.path.join(project_path, png_rel)
        if os.path.isfile(abs_png):
            sym_to_png[sym] = abs_png

    # Step 4: build default palette mapping (first palette seen for each icon)
    icon_to_default_pal: dict[str, str] = {}
    for _item, (icon_sym, pal_sym) in item_syms.items():
        if icon_sym not in icon_to_default_pal:
            icon_to_default_pal[icon_sym] = pal_sym

    # Step 5: sorted list of (icon_sym, png_path) for the picker
    icon_choices = sorted(sym_to_png.items(), key=lambda x: x[0])

    return item_syms, icon_choices, sym_to_png, icon_to_default_pal


def write_item_icon_entry(
    project_path: str, item_const: str, icon_sym: str, palette_sym: str
) -> bool:
    """Update a single item's icon entry in item_icon_table.h.
    Returns True on success."""
    icon_table = os.path.join(project_path, "src", "data", "item_icon_table.h")
    try:
        text = open(icon_table, encoding="utf-8", errors="surrogateescape").read()
    except OSError:
        return False

    # Match the line:  [ITEM_CONST] = {gItemIcon_X, gItemIconPalette_Y},
    pattern = re.compile(
        r'(\[\s*' + re.escape(item_const) + r'\s*\]\s*=\s*\{)\w+,\s*\w+(\})'
    )
    new_text, count = pattern.subn(
        r'\g<1>' + icon_sym + ', ' + palette_sym + r'\2', text
    )
    if count == 0:
        return False
    try:
        with open(icon_table, "w", encoding="utf-8", newline="\n") as f:
            f.write(new_text)
        return True
    except OSError:
        return False


# ── styling ───────────────────────────────────────────────────────────────────

_CARD_SS = """
QGroupBox {
    font-weight: bold;
    font-size: 10px;
    border: 1px solid #383838;
    border-radius: 6px;
    margin-top: 10px;
    padding-top: 6px;
    background-color: #252525;
    color: #cccccc;
}
QGroupBox::title {
    subcontrol-origin: margin;
    subcontrol-position: top left;
    left: 10px;
    padding: 0 5px;
    color: #777777;
    font-size: 9px;
    text-transform: uppercase;
    letter-spacing: 1px;
}
"""

_FIELD_SS = """
QLineEdit, QSpinBox, QComboBox, QPlainTextEdit {
    background-color: #1e1e1e;
    border: 1px solid #3a3a3a;
    border-radius: 4px;
    padding: 4px 7px;
    color: #e0e0e0;
    font-size: 12px;
    selection-background-color: #1565c0;
}
QLineEdit:focus, QSpinBox:focus, QComboBox:focus, QPlainTextEdit:focus {
    border: 1px solid #1976d2;
}
QComboBox::drop-down {
    border: none; padding-right: 4px;
    subcontrol-position: center right;
    width: 18px;
}
QComboBox::down-arrow {
    image: none;
    border-left: 4px solid transparent;
    border-right: 4px solid transparent;
    border-top: 5px solid #aaa;
    margin-right: 6px;
}
QSpinBox::up-button, QSpinBox::down-button {
    background-color: #333333; border: none; width: 16px;
}
QCheckBox { color: #cccccc; font-size: 12px; spacing: 6px; }
QCheckBox::indicator {
    width: 14px; height: 14px;
    border: 1px solid #555555;
    border-radius: 3px;
    background-color: #1e1e1e;
}
QCheckBox::indicator:checked {
    background-color: #1976d2;
    border-color: #1976d2;
}
"""

_LIST_SS = """
QListWidget {
    background-color: #191919;
    border: none;
    outline: 0;
    font-size: 12px;
}
QListWidget::item {
    padding: 5px 8px;
    border-bottom: 1px solid #1f1f1f;
    color: #cccccc;
}
QListWidget::item:selected {
    background-color: #1565c0;
    color: #ffffff;
    border-bottom: 1px solid #1565c0;
}
QListWidget::item:hover:!selected {
    background-color: #232323;
}
"""

_SEARCH_SS = """
QLineEdit {
    background-color: #1e1e1e;
    border: none;
    border-bottom: 1px solid #383838;
    padding: 6px 10px;
    color: #cccccc;
    font-size: 12px;
    border-radius: 0px;
}
QLineEdit:focus { border-bottom: 1px solid #1976d2; }
"""

_SCROLL_SS = """
QScrollArea { background-color: #1a1a1a; border: none; }
QScrollBar:vertical {
    background-color: #1a1a1a; width: 8px; border: none;
}
QScrollBar::handle:vertical {
    background-color: #444444; border-radius: 4px; min-height: 20px;
}
QScrollBar::handle:vertical:hover { background-color: #555555; }
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0px; }
"""

_PLACEHOLDER_ICON_SS = """
QLabel {
    background-color: #252525;
    border: 1px solid #383838;
    border-radius: 4px;
    color: #555555;
    font-size: 9px;
}
"""


# ── Two-line list delegate ────────────────────────────────────────────────────

class _TwoLineDelegate(QStyledItemDelegate):
    """Renders each list row as two lines: display name (top) + constant ID (bottom)."""

    _ROW_H    = 44
    _ICON_SZ  = 24
    _PAD_LEFT = 8
    _PAD_ICON = 6   # gap between icon right edge and text

    def paint(self, painter: QPainter, option: QStyleOptionViewItem, index):
        painter.save()

        # ── background ──────────────────────────────────────────────────────
        # PyQt6 renamed individual state flags to QStyle.StateFlag.*
        try:
            _sel_flag  = QStyle.StateFlag.State_Selected
            _over_flag = QStyle.StateFlag.State_MouseOver
        except AttributeError:
            # Older PyQt6 / PyQt5 compat
            _sel_flag  = QStyle.State.State_Selected   # type: ignore[attr-defined]
            _over_flag = QStyle.State.State_MouseOver  # type: ignore[attr-defined]

        selected = bool(option.state & _sel_flag)
        hovered  = bool(option.state & _over_flag)
        if selected:
            painter.fillRect(option.rect, QColor("#1565c0"))
        elif hovered:
            painter.fillRect(option.rect, QColor("#232323"))
        else:
            painter.fillRect(option.rect, QColor("#191919"))

        r   = option.rect
        ix  = r.left() + self._PAD_LEFT
        iy  = r.top() + (r.height() - self._ICON_SZ) // 2
        # Available text width: from icon-right-edge to widget right minus padding.
        # Use r.width() (not r.right()) to avoid the inclusive-coordinate pitfall.
        text_w = r.left() + r.width() - (ix + self._ICON_SZ + self._PAD_ICON) - 4

        # ── icon ────────────────────────────────────────────────────────────
        icon = index.data(Qt.ItemDataRole.DecorationRole)
        if icon and not icon.isNull():
            # PyQt6: QIcon.paint() requires a QRect, not individual ints
            icon_rect = QRect(ix, iy, self._ICON_SZ, self._ICON_SZ)
            icon.paint(painter, icon_rect)

        tx = ix + self._ICON_SZ + self._PAD_ICON

        # ── display name ─────────────────────────────────────────────────────
        name_font = QFont()
        name_font.setPointSize(11)
        painter.setFont(name_font)
        painter.setPen(QColor("#ffffff") if selected else QColor("#d0d0d0"))
        name_rect = QRect(tx, r.top() + 5, max(text_w, 0), 17)
        name = index.data(Qt.ItemDataRole.DisplayRole) or ""
        painter.drawText(name_rect,
                         Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop,
                         name)

        # ── constant ID ───────────────────────────────────────────────────────
        id_font = QFont("Courier New")
        id_font.setPointSize(8)
        painter.setFont(id_font)
        painter.setPen(QColor("#aaaaaa") if selected else QColor("#555555"))
        id_rect = QRect(tx, r.top() + 24, max(text_w, 0), 14)
        const = index.data(Qt.ItemDataRole.UserRole) or ""
        painter.drawText(id_rect,
                         Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop,
                         const)

        painter.restore()

    def sizeHint(self, option, index) -> QSize:
        return QSize(0, self._ROW_H)


def _card(title: str) -> tuple[QGroupBox, QFormLayout]:
    box = QGroupBox(title)
    box.setStyleSheet(_CARD_SS)
    form = QFormLayout()
    form.setLabelAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
    form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.ExpandingFieldsGrow)
    form.setContentsMargins(12, 6, 12, 12)
    form.setHorizontalSpacing(14)
    form.setVerticalSpacing(9)
    box.setLayout(form)
    return box, form


def _lbl(text: str) -> QLabel:
    l = QLabel(text)
    l.setStyleSheet("color: #777777; font-size: 11px;")
    return l


# ── Detail panel ─────────────────────────────────────────────────────────────

class ItemDetailPanel(QWidget):
    """Full-detail editor for a single item."""

    changed = pyqtSignal()

    # Scaled sizes for icon display
    _ICON_SIZE = 56    # pixels displayed in header

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self._loading = False
        self._dirty = False
        self._sym_to_png: dict[str, str] = {}
        self._icon_to_default_pal: dict[str, str] = {}
        self._current_icon_path: str = ""
        self.setStyleSheet(_FIELD_SS)
        self._build()

    # ── build ────────────────────────────────────────────────────────────────

    def _build(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(16, 16, 16, 20)
        root.setSpacing(12)

        # ── Header: icon + name + constant ───────────────────────────────────
        hdr = QHBoxLayout()
        hdr.setSpacing(14)

        # Icon placeholder / actual icon
        self._icon_lbl = QLabel()
        self._icon_lbl.setFixedSize(self._ICON_SIZE, self._ICON_SIZE)
        self._icon_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._icon_lbl.setStyleSheet(_PLACEHOLDER_ICON_SS)
        self._icon_lbl.setText("?")
        self._icon_lbl.setToolTip("Item icon (from graphics/items/icons/)")
        hdr.addWidget(self._icon_lbl)

        name_block = QVBoxLayout()
        name_block.setSpacing(3)

        self._hdr_name = QLabel("—")
        f = QFont()
        f.setPointSize(15)
        f.setBold(True)
        self._hdr_name.setFont(f)
        self._hdr_name.setStyleSheet("color: #ffffff; background: transparent;")
        name_block.addWidget(self._hdr_name)

        self._hdr_const = QLabel("")
        self._hdr_const.setStyleSheet(
            "color: #555555; font-family: 'Courier New'; font-size: 10px; background: transparent;"
        )
        name_block.addWidget(self._hdr_const)
        name_block.addStretch(1)

        hdr.addLayout(name_block)
        hdr.addStretch(1)
        root.addLayout(hdr)

        sep = QFrame()
        sep.setFrameShape(QFrame.Shape.HLine)
        sep.setStyleSheet("background-color: #2e2e2e; border: none; max-height: 1px;")
        root.addWidget(sep)

        # ── Icon card (picker + open folder) ──────────────────────────────────
        icon_card, icon_form = _card("Icon")

        self.f_icon_combo = QComboBox()
        self.f_icon_combo.setIconSize(QSize(24, 24))
        self.f_icon_combo.setMinimumHeight(32)
        self.f_icon_combo.setEditable(True)
        self.f_icon_combo.setInsertPolicy(QComboBox.InsertPolicy.NoInsert)
        self.f_icon_combo.setToolTip("Choose which sprite icon this item displays")
        icon_form.addRow(_lbl("Sprite"), self.f_icon_combo)

        icon_btn_row = QHBoxLayout()
        icon_btn_row.setContentsMargins(0, 0, 0, 0)
        icon_btn_row.setSpacing(8)
        self._open_icon_btn = QPushButton("Open Icon in Folder")
        self._open_icon_btn.setFixedHeight(26)
        self._open_icon_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._open_icon_btn.setStyleSheet("""
            QPushButton {
                background-color: #2a2a2a; color: #aaaaaa;
                border: 1px solid #3a3a3a; border-radius: 4px;
                padding: 0 12px; font-size: 11px;
            }
            QPushButton:hover  { background-color: #333333; color: #cccccc; }
            QPushButton:pressed { background-color: #222222; }
        """)
        self._open_icon_btn.clicked.connect(self._on_open_icon)
        icon_btn_row.addWidget(self._open_icon_btn)
        icon_btn_row.addStretch(1)
        icon_form.addRow(icon_btn_row)

        root.addWidget(icon_card)

        # ── Identity card ─────────────────────────────────────────────────────
        id_card, id_form = _card("Identity")

        self.f_name = QLineEdit()
        self.f_name.setMaxLength(14)   # ITEM_NAME_LENGTH = 14 (include/constants/global.h)
        self.f_name.setPlaceholderText("In-game display name")
        self._name_counter = QLabel("0/14")
        self._name_counter.setStyleSheet("color: #888888; font-size: 10px; font-family: 'Courier New';")
        self._name_counter.setToolTip("Characters used / character limit (ITEM_NAME_LENGTH = 14)")
        def _update_name_counter(text):
            used = len(text)
            self._name_counter.setText("{0}/14".format(used))
            self._name_counter.setStyleSheet(
                "color: #cc3333; font-size: 10px; font-family: 'Courier New';" if used >= 14
                else "color: #888888; font-size: 10px; font-family: 'Courier New';"
            )
        self.f_name.textChanged.connect(_update_name_counter)
        name_row = QHBoxLayout()
        name_row.setContentsMargins(0, 0, 0, 0)
        name_row.setSpacing(6)
        name_row.addWidget(self.f_name)
        name_row.addWidget(self._name_counter)
        id_form.addRow(_lbl("Name"), name_row)

        self.f_pocket = QComboBox()
        for display, val in POCKET_CHOICES:
            self.f_pocket.addItem(display, val)
        id_form.addRow(_lbl("Pocket"), self.f_pocket)

        self.f_price = QSpinBox()
        self.f_price.setRange(0, 999_999)
        self.f_price.setSingleStep(100)
        self.f_price.setGroupSeparatorShown(True)
        id_form.addRow(_lbl("Price"), self.f_price)

        root.addWidget(id_card)

        # ── Description card ──────────────────────────────────────────────────
        desc_card, desc_form = _card("Description")

        self.f_description = DexDescriptionEdit(max_chars_per_line=36, max_lines=3)
        self.f_description.setMinimumHeight(70)
        self.f_description.setMaximumHeight(110)
        self.f_description.setFont(QFont("Courier New", 10))
        self.f_description.setPlaceholderText("Bag description shown in-game…")
        self._desc_counter = QLabel()
        self._desc_counter.setTextFormat(Qt.TextFormat.RichText)
        self.f_description.set_counter_label(self._desc_counter)
        desc_form.addRow(self.f_description)
        desc_form.addRow(self._desc_counter)

        root.addWidget(desc_card)

        # ── Hold effect card ──────────────────────────────────────────────────
        hold_card, hold_form = _card("Hold Effect")

        self.f_holdEffect = QComboBox()
        self.f_holdEffect.setEditable(True)
        self.f_holdEffect.setInsertPolicy(QComboBox.InsertPolicy.NoInsert)
        for he in HOLD_EFFECT_CHOICES:
            self.f_holdEffect.addItem(he)
        hold_form.addRow(_lbl("Effect"), self.f_holdEffect)

        self.f_holdEffectParam = QSpinBox()
        self.f_holdEffectParam.setRange(0, 255)
        self.f_holdEffectParam.setToolTip(
            "Effect-specific parameter (e.g. HP threshold for RESTORE_HP)"
        )
        hold_form.addRow(_lbl("Param"), self.f_holdEffectParam)

        root.addWidget(hold_card)

        # ── Use functions card ─────────────────────────────────────────────────
        use_card, use_form = _card("Use Functions")

        self.f_fieldUseFunc = QComboBox()
        self.f_fieldUseFunc.setEditable(True)
        self.f_fieldUseFunc.setInsertPolicy(QComboBox.InsertPolicy.NoInsert)
        for fn in FIELD_USE_FUNC_CHOICES:
            self.f_fieldUseFunc.addItem(fn)
        self.f_fieldUseFunc.setToolTip(
            "What happens when the player uses this item from the Bag.\n"
            "EvoItem = evolution stone, Medicine = heal HP, etc."
        )
        use_form.addRow(_lbl("Field Use"), self.f_fieldUseFunc)

        self.f_battleUsage = QSpinBox()
        self.f_battleUsage.setRange(0, 255)
        self.f_battleUsage.setToolTip("0 = not usable  |  1 = usable  |  2 = Poké Ball")
        use_form.addRow(_lbl("Battle Usage"), self.f_battleUsage)

        self.f_battleUseFunc = QComboBox()
        self.f_battleUseFunc.setEditable(True)
        self.f_battleUseFunc.setInsertPolicy(QComboBox.InsertPolicy.NoInsert)
        for fn in BATTLE_USE_FUNC_CHOICES:
            self.f_battleUseFunc.addItem(fn)
        self.f_battleUseFunc.setToolTip(
            "What happens when the player uses this item during battle.\n"
            "Medicine = heal, PokeBallEtc = catch, StatBooster = X Attack/etc."
        )
        use_form.addRow(_lbl("Battle Use Func"), self.f_battleUseFunc)

        root.addWidget(use_card)

        # ── Properties card ────────────────────────────────────────────────────
        prop_card, prop_form = _card("Properties")

        self.f_type = QComboBox()
        for display, val in ITEM_TYPE_CHOICES:
            self.f_type.addItem(display, val)
        prop_form.addRow(_lbl("Item Type"), self.f_type)

        self.f_importance = QCheckBox("Important item  (Key Item behaviour)")
        prop_form.addRow(self.f_importance)

        self.f_registrability = QCheckBox("Registerable via Select button")
        prop_form.addRow(self.f_registrability)

        self.f_secondaryId = QSpinBox()
        self.f_secondaryId.setRange(0, 255)
        self.f_secondaryId.setToolTip("Secondary ID (rod tier, etc.)")
        prop_form.addRow(_lbl("Secondary ID"), self.f_secondaryId)

        root.addWidget(prop_card)
        root.addStretch(1)

        # ── signals ───────────────────────────────────────────────────────────
        self.f_name.textChanged.connect(self._emit)
        self.f_description.textChanged.connect(self._emit)
        self.f_fieldUseFunc.currentTextChanged.connect(self._emit)
        self.f_battleUseFunc.currentTextChanged.connect(self._emit)
        self.f_holdEffect.currentTextChanged.connect(self._emit)
        for w in (self.f_price, self.f_holdEffectParam, self.f_battleUsage, self.f_secondaryId):
            w.valueChanged.connect(self._emit)
        for w in (self.f_pocket, self.f_type):
            w.currentIndexChanged.connect(self._emit)
        for w in (self.f_importance, self.f_registrability):
            w.checkStateChanged.connect(self._emit)
        self.f_icon_combo.currentIndexChanged.connect(self._on_icon_changed)

    def _emit(self, *_):
        if not self._loading:
            self._dirty = True
            # Live-update the header name as the user types
            if self.sender() is self.f_name:
                self._hdr_name.setText(self.f_name.text() or "—")
            self.changed.emit()

    def _on_icon_changed(self, idx):
        """User picked a different icon from the combo."""
        if self._loading or idx < 0:
            return
        sym = self.f_icon_combo.itemData(Qt.ItemDataRole.UserRole)
        if sym is None:
            sym = self.f_icon_combo.itemData(idx, Qt.ItemDataRole.UserRole)
        if not sym:
            return
        png = self._sym_to_png.get(sym, "")
        self.set_icon(png)
        self._current_icon_path = png
        self._dirty = True
        self.changed.emit()

    def _on_open_icon(self):
        path = getattr(self, "_current_icon_path", "")
        if path:
            open_in_folder(path)

    # ── public API ────────────────────────────────────────────────────────────

    def set_icon_choices(self, icon_choices: list[tuple[str, str]],
                         sym_to_png: dict[str, str],
                         icon_to_default_pal: dict[str, str]):
        """Populate the icon picker combo.
        icon_choices: [(gItemIcon_Symbol, abs_png_path)]
        """
        self._sym_to_png = sym_to_png
        self._icon_to_default_pal = icon_to_default_pal
        self.f_icon_combo.blockSignals(True)
        self.f_icon_combo.clear()
        for sym, png_path in icon_choices:
            # Display name: strip "gItemIcon_" prefix
            display = sym.replace("gItemIcon_", "")
            icon = QIcon()
            if png_path and os.path.isfile(png_path):
                pm = QPixmap(png_path).scaled(
                    24, 24,
                    Qt.AspectRatioMode.KeepAspectRatio,
                    Qt.TransformationMode.FastTransformation,
                )
                icon = QIcon(pm)
            self.f_icon_combo.addItem(icon, display, sym)
        self.f_icon_combo.blockSignals(False)

    def set_icon(self, png_path: str | None):
        """Display the item icon from a PNG path, or a placeholder if None."""
        if png_path and os.path.isfile(png_path):
            pm = QPixmap(png_path).scaled(
                self._ICON_SIZE, self._ICON_SIZE,
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.FastTransformation,
            )
            self._icon_lbl.setPixmap(pm)
            self._icon_lbl.setStyleSheet(
                "QLabel { background: transparent; border: none; }"
            )
            self._icon_lbl.setText("")
        else:
            self._icon_lbl.setPixmap(QPixmap())
            self._icon_lbl.setStyleSheet(_PLACEHOLDER_ICON_SS)
            self._icon_lbl.setText("?")

    def load_item(self, const: str, data: dict, icon_path: str | None = None,
                  icon_sym: str | None = None):
        self._loading = True
        try:
            name = data.get("english") or data.get("name") or ""
            self._hdr_name.setText(name or const)
            self._hdr_const.setText(const)
            self.set_icon(icon_path)
            self._current_icon_path = icon_path or ""

            # Select icon in combo
            if icon_sym:
                idx = self.f_icon_combo.findData(icon_sym, Qt.ItemDataRole.UserRole)
                if idx >= 0:
                    self.f_icon_combo.setCurrentIndex(idx)
                else:
                    self.f_icon_combo.setCurrentText(
                        icon_sym.replace("gItemIcon_", "")
                    )

            self.f_name.setText(name)

            try:
                self.f_price.setValue(int(data.get("price", 0)))
            except (TypeError, ValueError):
                self.f_price.setValue(0)

            pocket_val = str(data.get("pocket", "POCKET_ITEMS"))
            pocket_idx = next(
                (i for i, (_, v) in enumerate(POCKET_CHOICES) if v == pocket_val), 0
            )
            self.f_pocket.setCurrentIndex(pocket_idx)

            desc = data.get("description_english") or data.get("description") or ""
            # stored with literal \n; convert to real newlines for the editor
            self.f_description.setPlainText(desc.replace("\\n", "\n"))

            he = str(data.get("holdEffect", "HOLD_EFFECT_NONE"))
            he_idx = self.f_holdEffect.findText(he)
            if he_idx >= 0:
                self.f_holdEffect.setCurrentIndex(he_idx)
            else:
                self.f_holdEffect.setCurrentText(he)

            try:
                self.f_holdEffectParam.setValue(int(data.get("holdEffectParam", 0)))
            except (TypeError, ValueError):
                self.f_holdEffectParam.setValue(0)

            field_fn = str(data.get("fieldUseFunc") or "NULL")
            fi = self.f_fieldUseFunc.findText(field_fn)
            if fi >= 0:
                self.f_fieldUseFunc.setCurrentIndex(fi)
            else:
                self.f_fieldUseFunc.setCurrentText(field_fn)

            battle_fn = str(data.get("battleUseFunc") or "NULL")
            bi = self.f_battleUseFunc.findText(battle_fn)
            if bi >= 0:
                self.f_battleUseFunc.setCurrentIndex(bi)
            else:
                self.f_battleUseFunc.setCurrentText(battle_fn)

            try:
                self.f_battleUsage.setValue(int(data.get("battleUsage", 0)))
            except (TypeError, ValueError):
                self.f_battleUsage.setValue(0)

            type_val = str(data.get("type", "0"))
            type_idx = next(
                (i for i, (_, v) in enumerate(ITEM_TYPE_CHOICES) if v == type_val), 0
            )
            self.f_type.setCurrentIndex(type_idx)

            self.f_importance.setChecked(bool(data.get("importance", 0)))
            self.f_registrability.setChecked(bool(data.get("registrability", 0)))

            try:
                self.f_secondaryId.setValue(int(data.get("secondaryId", 0)))
            except (TypeError, ValueError):
                self.f_secondaryId.setValue(0)

        finally:
            self._loading = False
            self._dirty = False

    def get_selected_icon_sym(self) -> str | None:
        """Return the currently selected icon symbol, or None if unchanged."""
        idx = self.f_icon_combo.currentIndex()
        if idx >= 0:
            return self.f_icon_combo.itemData(idx, Qt.ItemDataRole.UserRole)
        return None

    def get_default_palette(self, icon_sym: str) -> str:
        """Return a suitable palette for *icon_sym*."""
        pal_map = getattr(self, "_icon_to_default_pal", {})
        return pal_map.get(icon_sym, "gItemIconPalette_QuestionMark")

    def collect(self, base: dict) -> dict:
        d = dict(base)
        d["english"] = self.f_name.text()
        d["price"] = self.f_price.value()
        d["pocket"] = self.f_pocket.currentData()
        # convert real newlines back to the GBA literal \n separator
        d["description_english"] = self.f_description.toPlainText().replace("\n", "\\n")
        d["holdEffect"] = self.f_holdEffect.currentText()
        d["holdEffectParam"] = self.f_holdEffectParam.value()
        d["fieldUseFunc"] = self.f_fieldUseFunc.currentText() or "NULL"
        d["battleUsage"] = self.f_battleUsage.value()
        d["battleUseFunc"] = self.f_battleUseFunc.currentText() or "NULL"
        d["type"] = int(self.f_type.currentData() or "0")
        d["importance"] = int(self.f_importance.isChecked())
        d["registrability"] = int(self.f_registrability.isChecked())
        d["secondaryId"] = self.f_secondaryId.value()
        return d

    def clear(self):
        self._loading = True
        try:
            self._hdr_name.setText("—")
            self._hdr_const.setText("")
            self.set_icon(None)
            self._current_icon_path = ""
            if self.f_icon_combo.count():
                self.f_icon_combo.setCurrentIndex(0)
            self.f_name.clear()
            self.f_price.setValue(0)
            self.f_pocket.setCurrentIndex(0)
            self.f_description.clear()
            self.f_holdEffect.setCurrentIndex(0)
            self.f_holdEffectParam.setValue(0)
            self.f_fieldUseFunc.setCurrentIndex(0)
            self.f_battleUsage.setValue(0)
            self.f_battleUseFunc.setCurrentIndex(0)
            self.f_type.setCurrentIndex(0)
            self.f_importance.setChecked(False)
            self.f_registrability.setChecked(False)
            self.f_secondaryId.setValue(0)
        finally:
            self._loading = False
            self._dirty = False


# ── Main tab widget ───────────────────────────────────────────────────────────

class ItemsTabWidget(QWidget):
    """
    Premium Items Editor:
      Left  — searchable list of all items, each showing its in-game icon
      Right — scrollable form with every Item struct field + large icon header

    Signals:
      item_modified    — any field edit (for dirty-flag in MainWindow)
      reset_requested  — Reset to Vanilla button clicked
    """

    item_modified    = pyqtSignal()
    reset_requested  = pyqtSignal()
    rename_requested = pyqtSignal(str)   # emits the current item constant

    _LIST_ICON = 24     # px for list-row icons
    _LIST_ROW_H = 34    # forced row height for consistent icon display

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self._items:    dict[str, dict] = {}   # const → data
        self._order:    list[str]       = []   # display order
        self._icons:    dict[str, str]  = {}   # const → abs png path
        self._current:  str | None      = None
        self._icon_cache: dict[str, QIcon] = {}
        self._item_syms:  dict[str, tuple[str, str]] = {}  # const → (icon_sym, pal_sym)
        self._project_path: str = ""
        self._icon_dirty: dict[str, tuple[str, str]] = {}  # const → (new_icon, new_pal)
        self._build()
        # Prevent scroll-wheel from changing combos/spins unless clicked
        try:
            from ui.custom_widgets.scroll_guard import install_scroll_guard_recursive
            install_scroll_guard_recursive(self)
        except Exception:
            pass

    # ── build ────────────────────────────────────────────────────────────────

    def _build(self):
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        # ── top toolbar ───────────────────────────────────────────────────────
        toolbar = QFrame()
        toolbar.setFixedHeight(38)
        toolbar.setStyleSheet(
            "QFrame { background-color: #1f1f1f; border-bottom: 1px solid #2e2e2e; }"
        )
        tb = QHBoxLayout(toolbar)
        tb.setContentsMargins(10, 0, 10, 0)
        tb.setSpacing(8)

        title_lbl = QLabel("Items")
        title_lbl.setStyleSheet(
            "color: #999999; font-size: 11px; font-weight: bold;"
        )
        tb.addWidget(title_lbl)
        tb.addStretch(1)

        self._reset_btn = QPushButton("↺  Reset to Vanilla")
        self._reset_btn.setFixedHeight(26)
        self._reset_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._reset_btn.setStyleSheet("""
            QPushButton {
                background-color: #2a2a2a; color: #999999;
                border: 1px solid #3a3a3a; border-radius: 4px;
                padding: 0 12px; font-size: 11px;
            }
            QPushButton:hover  { background-color: #333333; color: #cccccc; }
            QPushButton:pressed { background-color: #222222; }
        """)
        self._reset_btn.clicked.connect(self.reset_requested)
        tb.addWidget(self._reset_btn)

        self._rename_btn = QPushButton("✎  Rename…")
        self._rename_btn.setFixedHeight(26)
        self._rename_btn.setEnabled(False)
        self._rename_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._rename_btn.setToolTip("Rename this item constant across the whole project")
        self._rename_btn.setStyleSheet("""
            QPushButton {
                background-color: #2a2a2a; color: #aaaaaa;
                border: 1px solid #3a3a3a; border-radius: 4px;
                padding: 0 12px; font-size: 11px;
            }
            QPushButton:hover  { background-color: #333333; color: #cccccc; }
            QPushButton:pressed { background-color: #222222; }
            QPushButton:disabled { color: #555555; border-color: #2a2a2a; }
        """)
        self._rename_btn.clicked.connect(
            lambda: self.rename_requested.emit(self._current or "")
        )
        tb.addWidget(self._rename_btn)

        outer.addWidget(toolbar)

        # ── splitter ──────────────────────────────────────────────────────────
        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.setHandleWidth(1)
        splitter.setStyleSheet("QSplitter::handle { background-color: #2e2e2e; }")

        # Left panel
        left = QWidget()
        left.setMinimumWidth(190)
        left.setMaximumWidth(300)
        left.setStyleSheet("background-color: #191919;")
        lv = QVBoxLayout(left)
        lv.setContentsMargins(0, 0, 0, 0)
        lv.setSpacing(0)

        self._search = QLineEdit()
        self._search.setPlaceholderText("  Search items…")
        self._search.setClearButtonEnabled(True)
        self._search.setStyleSheet(_SEARCH_SS)
        self._search.setFixedHeight(34)
        self._search.textChanged.connect(self._filter)
        lv.addWidget(self._search)

        self._list = QListWidget()
        self._list.setStyleSheet(_LIST_SS)
        self._list.setIconSize(QSize(self._LIST_ICON, self._LIST_ICON))
        self._list.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._list.setUniformItemSizes(False)
        self._list.setItemDelegate(_TwoLineDelegate(self._list))
        self._list.currentItemChanged.connect(self._on_selected)
        lv.addWidget(self._list)

        # Right panel
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setStyleSheet(_SCROLL_SS)

        self._detail = ItemDetailPanel()
        self._detail.setStyleSheet(
            "ItemDetailPanel { background-color: #1a1a1a; }" + _FIELD_SS
        )
        self._detail.changed.connect(self._on_changed)
        scroll.setWidget(self._detail)

        splitter.addWidget(left)
        splitter.addWidget(scroll)
        splitter.setSizes([230, 800])

        outer.addWidget(splitter)

    # ── public API ────────────────────────────────────────────────────────────

    def load_items(self, items: dict | list, project_path: str = ""):
        """Populate from a dict {const: data} or list of data dicts.
        Pass *project_path* to resolve item icons from the ROM source tree.
        """
        self._items.clear()
        self._order.clear()
        self._current = None
        self._icon_cache.clear()
        self._icon_dirty.clear()
        self._project_path = project_path

        if isinstance(items, list):
            for entry in items:
                const = entry.get("itemId") or entry.get("constant") or ""
                if const:
                    self._items[const] = dict(entry)
                    self._order.append(const)
        else:
            for const, entry in items.items():
                self._items[const] = dict(entry)
                self._order.append(const)

        # Resolve icons (simple map for list thumbnails)
        self._icons = _parse_item_icon_map(project_path) if project_path else {}

        # Full icon data for the picker
        if project_path:
            (self._item_syms, icon_choices,
             sym_to_png, icon_to_default_pal) = _parse_item_icon_full(project_path)
            self._detail.set_icon_choices(
                icon_choices, sym_to_png, icon_to_default_pal
            )
        else:
            self._item_syms = {}

        self._rebuild_list()
        self._detail.clear()

    def collect_all(self) -> dict:
        """Flush current panel → return full {const: data}."""
        self._flush()
        return dict(self._items)

    # ── internal ──────────────────────────────────────────────────────────────

    def _icon_for(self, const: str) -> QIcon:
        if const in self._icon_cache:
            return self._icon_cache[const]
        png = self._icons.get(const, "")
        if png and os.path.isfile(png):
            pm = QPixmap(png).scaled(
                self._LIST_ICON, self._LIST_ICON,
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.FastTransformation,
            )
            icon = QIcon(pm)
        else:
            icon = QIcon()
        self._icon_cache[const] = icon
        return icon

    def _rebuild_list(self, needle: str = ""):
        self._list.blockSignals(True)
        self._list.clear()
        needle_lc = needle.lower()
        for const in self._order:
            if const == "ITEM_NONE":
                continue
            data   = self._items[const]
            name   = data.get("english") or data.get("name") or const
            if needle_lc and needle_lc not in name.lower() and needle_lc not in const.lower():
                continue
            row = QListWidgetItem(self._icon_for(const), name)
            row.setData(Qt.ItemDataRole.UserRole, const)
            row.setToolTip(const)
            row.setSizeHint(QSize(0, _TwoLineDelegate._ROW_H))
            self._list.addItem(row)
        self._list.blockSignals(False)

        # Re-select current if still visible
        if self._current:
            for i in range(self._list.count()):
                if self._list.item(i).data(Qt.ItemDataRole.UserRole) == self._current:
                    self._list.setCurrentRow(i)
                    return
        if self._list.count():
            self._list.setCurrentRow(0)

    def _filter(self, text: str):
        self._flush()
        self._rebuild_list(text)

    def _on_selected(self, current: QListWidgetItem | None, _prev):
        if current is None:
            self._rename_btn.setEnabled(False)
            return
        self._flush()
        const = current.data(Qt.ItemDataRole.UserRole)
        self._current = const
        data = self._items.get(const, {})
        icon_sym = self._item_syms.get(const, (None, None))[0]
        self._detail.load_item(
            const, data,
            icon_path=self._icons.get(const),
            icon_sym=icon_sym,
        )
        self._rename_btn.setEnabled(bool(const))

    def _on_changed(self):
        # Sync list item text with edited name
        if self._current:
            self._flush()
            name = self._items[self._current].get("english") or self._current
            for i in range(self._list.count()):
                itm = self._list.item(i)
                if itm and itm.data(Qt.ItemDataRole.UserRole) == self._current:
                    itm.setText(name)
                    break
        self.item_modified.emit()

    def rename_item_key(self, old_const: str, new_const: str) -> None:
        """Update the in-memory item data when an item constant is renamed."""
        if old_const in self._items:
            data = self._items.pop(old_const)
            # Keep itemId field in sync
            data["itemId"] = new_const
            self._items[new_const] = data
        if old_const in self._icons:
            self._icons[new_const] = self._icons.pop(old_const)
        if old_const in self._icon_cache:
            self._icon_cache[new_const] = self._icon_cache.pop(old_const)
        if self._current == old_const:
            self._current = new_const
        # Update the order list
        try:
            idx = self._order.index(old_const)
            self._order[idx] = new_const
        except ValueError:
            pass
        # Update the list widget item
        for i in range(self._list.count()):
            item = self._list.item(i)
            if item and item.data(Qt.ItemDataRole.UserRole) == old_const:
                item.setData(Qt.ItemDataRole.UserRole, new_const)
                break

    def _flush(self):
        if self._current and self._current in self._items and self._detail._dirty:
            self._items[self._current] = self._detail.collect(
                self._items[self._current]
            )
            # Track icon changes for writing back to item_icon_table.h
            new_sym = self._detail.get_selected_icon_sym()
            if new_sym:
                orig = self._item_syms.get(self._current, (None, None))
                if new_sym != orig[0]:
                    pal = self._detail.get_default_palette(new_sym)
                    self._icon_dirty[self._current] = (new_sym, pal)
                    # Update internal tracking
                    self._item_syms[self._current] = (new_sym, pal)
                    # Refresh the icon in the list sidebar
                    png = getattr(self._detail, "_sym_to_png", {}).get(new_sym, "")
                    if png:
                        self._icons[self._current] = png
                        self._icon_cache.pop(self._current, None)

    def save_icon_changes(self) -> bool:
        """Write any pending icon changes to item_icon_table.h.
        Returns True if all writes succeeded."""
        if not self._icon_dirty or not self._project_path:
            return True
        self._flush()
        ok = True
        for item_const, (icon_sym, pal_sym) in self._icon_dirty.items():
            if not write_item_icon_entry(
                self._project_path, item_const, icon_sym, pal_sym
            ):
                ok = False
        if ok:
            self._icon_dirty.clear()
        return ok

    def has_icon_changes(self) -> bool:
        """Return True if there are pending icon changes."""
        return bool(self._icon_dirty)
