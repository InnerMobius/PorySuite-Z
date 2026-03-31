"""ui/trainers_tab_widget.py — Complete trainer editor for PorySuitePyQT6."""
from __future__ import annotations

import logging
import os
import re
from collections import defaultdict
from typing import Optional

from PyQt6.QtCore import Qt, QRect, QSize, pyqtSignal
from PyQt6.QtGui import QColor, QFont, QIcon, QImage, QPainter, QPixmap
from PyQt6.QtWidgets import (
    QCheckBox, QComboBox, QFormLayout, QFrame, QGroupBox,
    QHBoxLayout, QInputDialog, QLabel, QLineEdit,
    QListWidget, QListWidgetItem, QMessageBox,
    QPushButton, QScrollArea, QSizePolicy, QSpinBox,
    QSplitter, QStyle, QStyledItemDelegate, QStyleOptionViewItem,
    QTabWidget, QVBoxLayout, QWidget,
)

log = logging.getLogger(__name__)

# ── encounter-music options ───────────────────────────────────────────────────
# ── All constant pools imported from single source of truth ───────────────────
from ui.constants import (
    ENCOUNTER_MUSIC as _ENCOUNTER_MUSIC,
    AI_FLAGS as _AI_FLAGS,
    PARTY_TYPES as _PARTY_TYPES,
    STRUCT_FOR_PARTY_TYPE as _STRUCT_FOR_TYPE,
    PARTY_TYPE_FOR_STRUCT as _TYPE_FOR_STRUCT,
)

# ── stylesheets ───────────────────────────────────────────────────────────────
_LIST_SS = """
QListWidget { background: #191919; border: none; outline: none; }
QListWidget::item { border-bottom: 1px solid #1f1f1f; }
QListWidget::item:selected { background: #1565c0; }
"""
_WARN_SS = (
    "color: #ff8a80; background: #2a1515; padding: 5px 10px; "
    "font-size: 10px; border-top: 1px solid #4a2020;"
)


# ══════════════════════════════════════════════════════════════════════════════
# Parser helpers
# ══════════════════════════════════════════════════════════════════════════════

def _parse_trainer_class_names(root: str) -> dict[str, str]:
    """Return {TRAINER_CLASS_CONST: "DISPLAY NAME"} from trainer_class_names.h."""
    path = os.path.join(root, "src", "data", "text", "trainer_class_names.h")
    if not os.path.isfile(path):
        return {}
    result: dict[str, str] = {}
    pat = re.compile(r'\[(\w+)\]\s*=\s*_\("([^"]*)"\)')
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            for line in f:
                m = pat.search(line)
                if m:
                    result[m.group(1)] = m.group(2)
    except Exception as exc:
        log.warning("_parse_trainer_class_names: %s", exc)
    return result


def _parse_trainer_pic_map(root: str) -> dict[str, str]:
    """Return {TRAINER_PIC_CONST: abs_png_path} by cross-referencing constants + graphics."""
    # Build {lowercase_suffix: abs_png_path} from gTrainerFrontPic_* entries
    path_by_suffix: dict[str, str] = {}
    gfx = os.path.join(root, "src", "data", "graphics", "trainers.h")
    if os.path.isfile(gfx):
        pat = re.compile(r'gTrainerFrontPic_\w+\[\]\s*=\s*INCBIN_U32\("([^"]+front_pic\.4bpp\.lz)"\)')
        try:
            with open(gfx, encoding="utf-8", errors="replace") as f:
                for line in f:
                    m = pat.search(line)
                    if m:
                        rel = m.group(1)
                        base = os.path.basename(rel)
                        key = base.replace("_front_pic.4bpp.lz", "")  # "aqua_leader_archie"
                        png = os.path.join(root, rel.replace(".4bpp.lz", ".png"))
                        path_by_suffix[key] = png
        except Exception as exc:
            log.warning("_parse_trainer_pic_map gfx: %s", exc)

    # Build {TRAINER_PIC_CONST: abs_png_path} via TRAINER_PIC_X → suffix match
    result: dict[str, str] = {}
    const_h = os.path.join(root, "include", "constants", "trainers.h")
    if os.path.isfile(const_h):
        pat2 = re.compile(r'#define\s+(TRAINER_PIC_\w+)\s+\d+')
        try:
            with open(const_h, encoding="utf-8", errors="replace") as f:
                for line in f:
                    m = pat2.search(line)
                    if m:
                        const = m.group(1)                          # TRAINER_PIC_AQUA_LEADER_ARCHIE
                        suffix = const[len("TRAINER_PIC_"):].lower()  # aqua_leader_archie
                        if suffix in path_by_suffix:
                            result[const] = path_by_suffix[suffix]
        except Exception as exc:
            log.warning("_parse_trainer_pic_map consts: %s", exc)
    return result


def _parse_trainer_parties(root: str) -> dict[str, dict]:
    """Parse trainer_parties.h → {sParty_Symbol: {"type": str, "members": list}}."""
    path = os.path.join(root, "src", "data", "trainer_parties.h")
    if not os.path.isfile(path):
        return {}
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            text = f.read()
    except Exception as exc:
        log.warning("_parse_trainer_parties read: %s", exc)
        return {}

    result: dict[str, dict] = {}
    decl_pat = re.compile(r'static const struct (TrainerMon\w+)\s+(\w+)\[\]\s*=')

    for match in decl_pat.finditer(text):
        struct_type = match.group(1)
        symbol      = match.group(2)
        party_type  = _TYPE_FOR_STRUCT.get(struct_type, "NO_ITEM_DEFAULT_MOVES")
        try:
            brace_start = text.index('{', match.end())
        except ValueError:
            continue
        depth, brace_end = 0, brace_start
        for i in range(brace_start, len(text)):
            if text[i] == '{':
                depth += 1
            elif text[i] == '}':
                depth -= 1
                if depth == 0:
                    brace_end = i
                    break
        members = _parse_party_members(text[brace_start + 1:brace_end])
        result[symbol] = {"type": party_type, "members": members}
    return result


def _parse_party_members(array_text: str) -> list[dict]:
    """Extract individual member blocks from the array content and parse fields."""
    members: list[dict] = []
    pos = 0
    while pos < len(array_text):
        start = array_text.find('{', pos)
        if start == -1:
            break
        depth, end = 0, start
        for i in range(start, len(array_text)):
            if array_text[i] == '{':
                depth += 1
            elif array_text[i] == '}':
                depth -= 1
                if depth == 0:
                    end = i
                    break
        member = _parse_member_fields(array_text[start + 1:end])
        if member:
            members.append(member)
        pos = end + 1
    return members


def _parse_member_fields(member_text: str) -> dict:
    """Parse .field = value assignments, handling the .moves = {…} nested array."""
    result: dict = {}
    # Extract .moves = {M1, M2, M3, M4} first to avoid confusing the flat parser
    mv_m = re.search(r'\.moves\s*=\s*\{([^}]+)\}', member_text)
    if mv_m:
        result["moves"] = [s.strip().rstrip(',') for s in mv_m.group(1).split(',') if s.strip()]
        member_text = member_text[:mv_m.start()] + member_text[mv_m.end():]
    for fm in re.finditer(r'\.(\w+)\s*=\s*([^,\n}]+)', member_text):
        val = fm.group(2).strip().rstrip(',').strip()
        if val:
            result[fm.group(1)] = val
    return result


def _generate_party_c(symbol: str, party_type: str, members: list[dict]) -> str:
    """Generate C struct array declaration for a trainer party."""
    struct    = _STRUCT_FOR_TYPE.get(party_type, "TrainerMonNoItemDefaultMoves")
    has_item  = party_type in ("ITEM_DEFAULT_MOVES",   "ITEM_CUSTOM_MOVES")
    has_moves = party_type in ("NO_ITEM_CUSTOM_MOVES", "ITEM_CUSTOM_MOVES")
    lines = [f"static const struct {struct} {symbol}[] = {{"]
    for m in members:
        lines.append("    {")
        lines.append(f"        .iv = {m.get('iv', '0')},")
        lines.append(f"        .lvl = {m.get('lvl', '5')},")
        lines.append(f"        .species = {m.get('species', 'SPECIES_NONE')},")
        if has_item:
            lines.append(f"        .heldItem = {m.get('heldItem', 'ITEM_NONE')},")
        if has_moves:
            mv = list(m.get("moves", []))
            while len(mv) < 4:
                mv.append("MOVE_NONE")
            lines.append(f"        .moves = {{{', '.join(mv[:4])}}},")
        lines.append("    },")
    lines.append("};")
    return "\n".join(lines)


def _replace_party_declaration(text: str, symbol: str, new_code: str) -> str:
    """Replace the sParty_Symbol[] declaration in full file text, or append if absent."""
    pat = re.compile(rf'static const struct TrainerMon\w+\s+{re.escape(symbol)}\[\]\s*=')
    m = pat.search(text)
    if not m:
        # Symbol not found — don't blindly append; the file may have the
        # correct symbol under a different casing due to a stale cache.
        print(f"Warning: party symbol {symbol} not found in trainer_parties.h; skipping write")
        return text
    try:
        brace_start = text.index('{', m.end())
    except ValueError:
        return text + "\n\n" + new_code + "\n"
    depth, brace_end = 0, brace_start
    for i in range(brace_start, len(text)):
        if text[i] == '{':
            depth += 1
        elif text[i] == '}':
            depth -= 1
            if depth == 0:
                brace_end = i
                break
    semi = text.find(';', brace_end)
    if semi == -1:
        semi = brace_end
    return text[:m.start()] + new_code + text[semi + 1:]


def _find_script_refs(root: str, constant: str) -> list[str]:
    """Return list of 'relpath:lineno' for every .inc/.s/.asm referencing constant."""
    refs: list[str] = []
    exts = {'.inc', '.s', '.asm'}
    try:
        for dirpath, _, fnames in os.walk(root):
            for fname in fnames:
                if any(fname.endswith(e) for e in exts):
                    fpath = os.path.join(dirpath, fname)
                    try:
                        with open(fpath, encoding="utf-8", errors="replace") as f:
                            for lno, line in enumerate(f, 1):
                                if constant in line:
                                    refs.append(f"{os.path.relpath(fpath, root)}:{lno}")
                    except Exception:
                        pass
    except Exception as exc:
        log.warning("_find_script_refs: %s", exc)
    return refs


def _extract_party_symbol(party_macro: str) -> Optional[str]:
    """Extract sParty_X from 'NO_ITEM_DEFAULT_MOVES(sParty_X)'."""
    m = re.search(r'\((sParty_\w+)\)', party_macro)
    return m.group(1) if m else None


def _trainer_const_to_party_symbol(const: str) -> str:
    """TRAINER_AQUA_LEADER → AquaLeader  (TitleCase, no TRAINER_ prefix)."""
    stem = const[len("TRAINER_"):] if const.startswith("TRAINER_") else const
    return "".join(p.capitalize() for p in stem.split("_"))


# ══════════════════════════════════════════════════════════════════════════════
# Trainer list delegate
# ══════════════════════════════════════════════════════════════════════════════

class _TrainerListDelegate(QStyledItemDelegate):
    _ROW_H = 60
    _SPR_W = 40
    _SPR_H = 52
    _PAD   = 6

    def paint(self, painter: QPainter, option: QStyleOptionViewItem, index):
        painter.save()
        try:
            _sel = QStyle.StateFlag.State_Selected
        except AttributeError:
            _sel = QStyle.State.State_Selected  # type: ignore[attr-defined]
        selected = bool(option.state & _sel)
        painter.fillRect(option.rect, QColor("#1565c0" if selected else "#191919"))

        r = option.rect

        # Sprite icon stored as DecorationRole
        icon: QIcon = index.data(Qt.ItemDataRole.DecorationRole)
        if icon and not icon.isNull():
            icon_rect = QRect(
                r.left() + self._PAD,
                r.top() + (r.height() - self._SPR_H) // 2,
                self._SPR_W, self._SPR_H,
            )
            icon.paint(painter, icon_rect)

        tx  = r.left() + self._PAD + self._SPR_W + self._PAD
        tw  = max(r.left() + r.width() - tx - 4, 0)

        # Line 1 — "CLASS NAME"
        f1 = QFont()
        f1.setPointSize(10)
        f1.setBold(True)
        painter.setFont(f1)
        painter.setPen(QColor("#ffffff" if selected else "#e0e0e0"))
        line1 = index.data(Qt.ItemDataRole.DisplayRole) or ""
        painter.drawText(QRect(tx, r.top() + 8, tw, 18),
                         Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop, line1)

        # Line 2 — constant
        f2 = QFont("Courier New")
        f2.setPointSize(8)
        painter.setFont(f2)
        painter.setPen(QColor("#aaaaaa" if selected else "#555555"))
        line2 = index.data(Qt.ItemDataRole.UserRole) or ""
        painter.drawText(QRect(tx, r.top() + 30, tw, 14),
                         Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop, line2)

        painter.restore()

    def sizeHint(self, option, index) -> QSize:
        return QSize(0, self._ROW_H)


# ══════════════════════════════════════════════════════════════════════════════
# Party slot widget
# ══════════════════════════════════════════════════════════════════════════════

class _PartySlotWidget(QWidget):
    changed          = pyqtSignal()
    remove_requested = pyqtSignal(object)   # emits self

    def __init__(self, species_list: list, items_list: list, moves_list: list,
                 icon_fn=None, parent=None):
        super().__init__(parent)
        self._species_list = species_list
        self._items_list   = items_list
        self._moves_list   = moves_list
        self._icon_fn      = icon_fn   # Optional Callable[[str], QIcon]
        self._build()
        # Prevent scroll-wheel from changing combos/spins unless clicked
        try:
            from ui.custom_widgets.scroll_guard import install_scroll_guard_recursive
            install_scroll_guard_recursive(self)
        except Exception:
            pass

    # ── build ─────────────────────────────────────────────────────────────────
    def _build(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(4, 4, 4, 0)
        root.setSpacing(3)

        # Header row: sprite · species · Lv · IV · ✕
        hdr = QHBoxLayout()
        hdr.setSpacing(5)

        self._sprite_lbl = QLabel()
        self._sprite_lbl.setFixedSize(32, 32)
        self._sprite_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._sprite_lbl.setStyleSheet("background: #111; border-radius: 2px;")
        hdr.addWidget(self._sprite_lbl)

        self._species_cb = QComboBox()
        self._species_cb.setEditable(True)
        self._species_cb.setMinimumWidth(140)
        self._species_cb.setMaximumWidth(220)
        self._species_cb.setInsertPolicy(QComboBox.InsertPolicy.NoInsert)
        for const, name in self._species_list:
            self._species_cb.addItem(name, const)
        self._species_cb.currentIndexChanged.connect(self._on_species_changed)
        hdr.addWidget(self._species_cb)

        hdr.addSpacing(8)
        hdr.addWidget(QLabel("Lv"))
        self._lvl_spin = QSpinBox()
        self._lvl_spin.setRange(1, 100)
        self._lvl_spin.setFixedWidth(60)
        self._lvl_spin.valueChanged.connect(lambda: self.changed.emit())
        hdr.addWidget(self._lvl_spin)

        hdr.addSpacing(8)
        hdr.addWidget(QLabel("IV"))
        self._iv_spin = QSpinBox()
        self._iv_spin.setRange(0, 255)
        self._iv_spin.setFixedWidth(60)
        self._iv_spin.setToolTip("0 = no IVs, 255 = all IVs perfect (all stats share one value)")
        self._iv_spin.valueChanged.connect(lambda: self.changed.emit())
        hdr.addWidget(self._iv_spin)

        hdr.addSpacing(4)
        rm_btn = QPushButton("✕")
        rm_btn.setFixedSize(22, 22)
        rm_btn.setStyleSheet("color: #e55; border: none; font-weight: bold; background: transparent;")
        rm_btn.setToolTip("Remove this Pokémon from the party")
        rm_btn.clicked.connect(lambda: self.remove_requested.emit(self))
        hdr.addWidget(rm_btn)
        hdr.addStretch()
        root.addLayout(hdr)

        # Held item row (hidden unless ITEM_* type)
        item_row = QHBoxLayout()
        item_row.setSpacing(5)
        item_row.addWidget(QLabel("Held Item:"))
        self._item_cb = QComboBox()
        self._item_cb.setEditable(True)
        self._item_cb.setMinimumWidth(180)
        self._item_cb.setInsertPolicy(QComboBox.InsertPolicy.NoInsert)
        for const, name in self._items_list:
            self._item_cb.addItem(name, const)
        self._item_cb.currentIndexChanged.connect(lambda: self.changed.emit())
        item_row.addWidget(self._item_cb)
        item_row.addStretch()
        self._item_row_w = QWidget()
        self._item_row_w.setLayout(item_row)
        self._item_row_w.hide()
        root.addWidget(self._item_row_w)

        # Moves grid (hidden unless CUSTOM_MOVES type) — 2×2 layout
        moves_outer = QVBoxLayout()
        moves_outer.setSpacing(3)
        self._move_cbs: list[QComboBox] = []
        for row_i in range(2):
            row_layout = QHBoxLayout()
            row_layout.setSpacing(5)
            for col_i in range(2):
                slot_num = row_i * 2 + col_i + 1
                row_layout.addWidget(QLabel(f"Move {slot_num}:"))
                cb = QComboBox()
                cb.setEditable(True)
                cb.setMinimumWidth(160)
                cb.setInsertPolicy(QComboBox.InsertPolicy.NoInsert)
                for const, name in self._moves_list:
                    cb.addItem(name, const)
                cb.currentIndexChanged.connect(lambda: self.changed.emit())
                self._move_cbs.append(cb)
                row_layout.addWidget(cb)
            moves_outer.addLayout(row_layout)
        self._moves_row_w = QWidget()
        self._moves_row_w.setLayout(moves_outer)
        self._moves_row_w.hide()
        root.addWidget(self._moves_row_w)

        sep = QFrame()
        sep.setFrameShape(QFrame.Shape.HLine)
        sep.setStyleSheet("color: #2a2a2a;")
        root.addWidget(sep)

    # ── public API ────────────────────────────────────────────────────────────
    def set_party_type(self, party_type: str):
        self._item_row_w.setVisible(party_type  in ("ITEM_DEFAULT_MOVES",   "ITEM_CUSTOM_MOVES"))
        self._moves_row_w.setVisible(party_type in ("NO_ITEM_CUSTOM_MOVES", "ITEM_CUSTOM_MOVES"))

    def load(self, member: dict):
        species = member.get("species", "SPECIES_NONE")
        idx = self._species_cb.findData(species)
        if idx >= 0:
            self._species_cb.blockSignals(True)
            self._species_cb.setCurrentIndex(idx)
            self._species_cb.blockSignals(False)
        else:
            self._species_cb.setCurrentText(species)
        try:
            self._lvl_spin.setValue(int(member.get("lvl", 5)))
        except (ValueError, TypeError):
            self._lvl_spin.setValue(5)
        try:
            self._iv_spin.setValue(int(member.get("iv", 0)))
        except (ValueError, TypeError):
            self._iv_spin.setValue(0)
        item = member.get("heldItem", "ITEM_NONE")
        idx = self._item_cb.findData(item)
        if idx >= 0:
            self._item_cb.blockSignals(True)
            self._item_cb.setCurrentIndex(idx)
            self._item_cb.blockSignals(False)
        else:
            self._item_cb.setCurrentText(item)
        moves = member.get("moves", [])
        for i, cb in enumerate(self._move_cbs):
            mv = moves[i] if i < len(moves) else "MOVE_NONE"
            idx = cb.findData(mv)
            cb.blockSignals(True)
            if idx >= 0:
                cb.setCurrentIndex(idx)
            else:
                cb.setCurrentText(mv)
            cb.blockSignals(False)
        # Populate sprite now that species is set (signals were blocked above)
        self._on_species_changed()

    def collect(self) -> dict:
        result: dict = {
            "species": self._species_cb.currentData() or self._species_cb.currentText(),
            "lvl":     str(self._lvl_spin.value()),
            "iv":      str(self._iv_spin.value()),
        }
        if self._item_row_w.isVisible():
            result["heldItem"] = self._item_cb.currentData() or self._item_cb.currentText() or "ITEM_NONE"
        if self._moves_row_w.isVisible():
            result["moves"] = [
                (cb.currentData() or cb.currentText() or "MOVE_NONE")
                for cb in self._move_cbs
            ]
        return result

    def _on_species_changed(self):
        const = self._species_cb.currentData() or self._species_cb.currentText()
        if self._icon_fn and const:
            try:
                icon = self._icon_fn(const)
                if icon and not icon.isNull():
                    self._sprite_lbl.setPixmap(icon.pixmap(32, 32))
                else:
                    self._sprite_lbl.clear()
            except Exception:
                self._sprite_lbl.clear()
        else:
            self._sprite_lbl.clear()
        self.changed.emit()


# ══════════════════════════════════════════════════════════════════════════════
# Trainer detail panel
# ══════════════════════════════════════════════════════════════════════════════

class _TrainerDetailPanel(QWidget):
    changed          = pyqtSignal()
    rename_requested = pyqtSignal(str)   # emits current const

    def __init__(
        self,
        class_names: dict,
        pic_map: dict,
        trainer_pic_consts: list,
        species_list: list,
        items_list: list,
        moves_list: list,
        species_icon_fn=None,
        parent=None,
    ):
        super().__init__(parent)
        self._class_names        = class_names
        self._pic_map            = pic_map
        self._trainer_pic_consts = trainer_pic_consts
        self._species_list       = species_list
        self._items_list         = items_list
        self._moves_list         = moves_list
        self._species_icon_fn    = species_icon_fn   # Callable[[str], QIcon] | None
        self._current_const: Optional[str] = None
        self._has_female_flag: bool = False
        self._party_slots: list[_PartySlotWidget] = []
        self._build()

    # ── build ─────────────────────────────────────────────────────────────────
    def _build(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(12, 8, 12, 8)
        root.setSpacing(8)

        # Header: large sprite + const label + rename button
        hdr = QHBoxLayout()
        hdr.setSpacing(12)

        self._sprite_lbl = QLabel()
        self._sprite_lbl.setFixedSize(80, 100)
        self._sprite_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._sprite_lbl.setStyleSheet(
            "background: #111; border-radius: 6px; border: 1px solid #333;"
        )
        hdr.addWidget(self._sprite_lbl)

        info_col = QVBoxLayout()
        info_col.setSpacing(4)
        self._display_lbl = QLabel("—")
        self._display_lbl.setStyleSheet("font-size: 15px; font-weight: bold; color: #e0e0e0;")
        info_col.addWidget(self._display_lbl)
        self._const_lbl = QLabel("")
        self._const_lbl.setStyleSheet(
            "font-family: 'Courier New'; font-size: 10px; color: #777;"
        )
        info_col.addWidget(self._const_lbl)
        rename_btn = QPushButton("Rename Constant…")
        rename_btn.setFixedWidth(160)
        rename_btn.clicked.connect(
            lambda: self.rename_requested.emit(self._current_const or "")
        )
        info_col.addWidget(rename_btn)
        info_col.addStretch()
        hdr.addLayout(info_col)
        hdr.addStretch()
        root.addLayout(hdr)

        # Sub-tabs
        self._tabs = QTabWidget()
        self._tabs.setDocumentMode(True)
        root.addWidget(self._tabs)

        self._tabs.addTab(self._build_identity_tab(), "Identity")
        self._tabs.addTab(self._build_ai_tab(),       "AI")
        self._tabs.addTab(self._build_bag_tab(),       "Bag")
        self._tabs.addTab(self._build_party_tab(),     "Party")

    # ── Identity tab ──────────────────────────────────────────────────────────
    def _build_identity_tab(self) -> QWidget:
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        inner = QWidget()
        form = QFormLayout(inner)
        form.setLabelAlignment(Qt.AlignmentFlag.AlignRight)
        form.setSpacing(8)

        self._name_edit = QLineEdit()
        self._name_edit.setPlaceholderText("e.g. MISTY")
        self._name_edit.setMaxLength(11)
        self._name_edit.textChanged.connect(self._refresh_header)
        form.addRow("Name:", self._name_edit)

        self._class_cb = QComboBox()
        self._class_cb.setEditable(True)
        self._class_cb.setInsertPolicy(QComboBox.InsertPolicy.NoInsert)
        self._class_cb.currentIndexChanged.connect(self._refresh_header)
        form.addRow("Class:", self._class_cb)

        # Trainer Pic — combo + inline thumbnail
        pic_row = QHBoxLayout()
        self._pic_cb = QComboBox()
        self._pic_cb.setEditable(True)
        self._pic_cb.setInsertPolicy(QComboBox.InsertPolicy.NoInsert)
        self._pic_cb.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self._pic_cb.currentIndexChanged.connect(self._on_pic_changed)
        pic_row.addWidget(self._pic_cb)
        self._pic_thumb = QLabel()
        self._pic_thumb.setFixedSize(32, 40)
        self._pic_thumb.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._pic_thumb.setStyleSheet("background: #111; border-radius: 2px;")
        pic_row.addWidget(self._pic_thumb)
        pic_w = QWidget()
        pic_w.setLayout(pic_row)
        form.addRow("Trainer Pic:", pic_w)

        self._music_cb = QComboBox()
        for const, label in _ENCOUNTER_MUSIC:
            self._music_cb.addItem(label, const)
        self._music_cb.currentIndexChanged.connect(lambda: self.changed.emit())
        form.addRow("Encounter Music:", self._music_cb)

        self._double_cb = QCheckBox("Double Battle")
        self._double_cb.stateChanged.connect(lambda: self.changed.emit())
        form.addRow("", self._double_cb)

        scroll.setWidget(inner)
        return scroll

    # ── AI tab ────────────────────────────────────────────────────────────────
    def _build_ai_tab(self) -> QWidget:
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        inner = QWidget()
        layout = QVBoxLayout(inner)
        layout.setSpacing(5)
        layout.setContentsMargins(8, 8, 8, 8)
        self._ai_checks: dict[str, QCheckBox] = {}
        for const, desc in _AI_FLAGS:
            cb = QCheckBox(desc)
            cb.stateChanged.connect(lambda: self.changed.emit())
            self._ai_checks[const] = cb
            layout.addWidget(cb)
        layout.addStretch()
        scroll.setWidget(inner)
        return scroll

    # ── Bag tab ───────────────────────────────────────────────────────────────
    def _build_bag_tab(self) -> QWidget:
        w = QWidget()
        layout = QVBoxLayout(w)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(8)
        note = QLabel("Up to 4 items the trainer can use mid-battle (Potions, X items, etc.)")
        note.setStyleSheet("color: #888; font-size: 10px;")
        note.setWordWrap(True)
        layout.addWidget(note)
        self._bag_cbs: list[QComboBox] = []
        for i in range(4):
            row = QHBoxLayout()
            row.addWidget(QLabel(f"Slot {i + 1}:"))
            cb = QComboBox()
            cb.setEditable(True)
            cb.setMinimumWidth(200)
            cb.setInsertPolicy(QComboBox.InsertPolicy.NoInsert)
            for const, name in self._items_list:
                cb.addItem(name, const)
            cb.currentIndexChanged.connect(lambda: self.changed.emit())
            self._bag_cbs.append(cb)
            row.addWidget(cb)
            row.addStretch()
            layout.addLayout(row)
        layout.addStretch()
        return w

    # ── Party tab ─────────────────────────────────────────────────────────────
    def _build_party_tab(self) -> QWidget:
        w = QWidget()
        layout = QVBoxLayout(w)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(6)

        type_row = QHBoxLayout()
        type_row.addWidget(QLabel("Party type:"))
        self._party_type_cb = QComboBox()
        for const, label in _PARTY_TYPES:
            self._party_type_cb.addItem(label, const)
        self._party_type_cb.currentIndexChanged.connect(self._on_party_type_changed)
        type_row.addWidget(self._party_type_cb)
        type_row.addStretch()
        layout.addLayout(type_row)

        slots_scroll = QScrollArea()
        slots_scroll.setWidgetResizable(True)
        slots_scroll.setFrameShape(QFrame.Shape.NoFrame)
        self._slots_container = QWidget()
        self._slots_layout = QVBoxLayout(self._slots_container)
        self._slots_layout.setSpacing(2)
        self._slots_layout.setContentsMargins(0, 0, 0, 0)
        self._slots_layout.addStretch()
        slots_scroll.setWidget(self._slots_container)
        layout.addWidget(slots_scroll, 1)

        add_btn = QPushButton("+ Add Pokémon")
        add_btn.setStyleSheet(
            "background: #1a3a1a; color: #aaffaa; border: none; padding: 5px; border-radius: 3px;"
        )
        add_btn.clicked.connect(self._add_party_slot)
        layout.addWidget(add_btn)

        self._party_count_lbl = QLabel("0 / 6")
        self._party_count_lbl.setStyleSheet("color: #777; font-size: 10px;")
        layout.addWidget(self._party_count_lbl)

        return w

    # ── helpers ───────────────────────────────────────────────────────────────
    def _populate_class_combo(self):
        self._class_cb.blockSignals(True)
        self._class_cb.clear()
        for const, display in sorted(self._class_names.items(), key=lambda kv: kv[1]):
            self._class_cb.addItem(f"{display}  ({const})", const)
        self._class_cb.blockSignals(False)

    def _populate_pic_combo(self):
        self._pic_cb.blockSignals(True)
        self._pic_cb.clear()
        for const in sorted(self._trainer_pic_consts):
            label = const[len("TRAINER_PIC_"):] if const.startswith("TRAINER_PIC_") else const
            self._pic_cb.addItem(label, const)
        self._pic_cb.blockSignals(False)

    def _refresh_header(self, *_):
        """Update the display label in the header from current form values (live)."""
        cls_const   = self._class_cb.currentData() or ""
        cls_display = self._class_names.get(
            cls_const,
            cls_const.replace("TRAINER_CLASS_", "").replace("_", " "),
        )
        name_str = self._name_edit.text().strip()
        self._display_lbl.setText(f"{cls_display} {name_str}".strip() or "—")
        self.changed.emit()

    def _on_pic_changed(self):
        const = self._pic_cb.currentData()
        if const and const in self._pic_map:
            path = self._pic_map[const]
            if os.path.isfile(path):
                pix = QPixmap(path)
                if not pix.isNull():
                    self._pic_thumb.setPixmap(
                        pix.scaled(32, 40,
                                   Qt.AspectRatioMode.KeepAspectRatio,
                                   Qt.TransformationMode.SmoothTransformation)
                    )
                    # Also update the large header sprite
                    self._sprite_lbl.setPixmap(
                        pix.scaled(80, 100,
                                   Qt.AspectRatioMode.KeepAspectRatio,
                                   Qt.TransformationMode.SmoothTransformation)
                    )
                    self.changed.emit()
                    return
        self._sprite_lbl.clear()
        self._sprite_lbl.setText("?")
        self.changed.emit()

    def _load_sprite(self, pic_const: str):
        if pic_const and pic_const in self._pic_map:
            path = self._pic_map[pic_const]
            if os.path.isfile(path):
                pix = QPixmap(path)
                if not pix.isNull():
                    self._sprite_lbl.setPixmap(
                        pix.scaled(80, 100,
                                   Qt.AspectRatioMode.KeepAspectRatio,
                                   Qt.TransformationMode.SmoothTransformation)
                    )
                    return
        self._sprite_lbl.clear()
        self._sprite_lbl.setText("?")

    def _on_party_type_changed(self):
        ptype = self._party_type_cb.currentData() or "NO_ITEM_DEFAULT_MOVES"
        for slot in self._party_slots:
            slot.set_party_type(ptype)
        self.changed.emit()

    def _add_party_slot(self):
        if len(self._party_slots) >= 6:
            return
        slot = _PartySlotWidget(self._species_list, self._items_list, self._moves_list,
                                icon_fn=self._species_icon_fn, parent=self)
        ptype = self._party_type_cb.currentData() or "NO_ITEM_DEFAULT_MOVES"
        slot.set_party_type(ptype)
        slot.changed.connect(lambda: self.changed.emit())
        slot.remove_requested.connect(self._remove_party_slot)
        self._party_slots.append(slot)
        self._slots_layout.insertWidget(self._slots_layout.count() - 1, slot)
        self._update_party_count()
        self.changed.emit()

    def _remove_party_slot(self, slot: "_PartySlotWidget"):
        if slot in self._party_slots:
            self._party_slots.remove(slot)
            self._slots_layout.removeWidget(slot)
            slot.deleteLater()
            self._update_party_count()
            self.changed.emit()

    def _clear_party_slots(self):
        for slot in list(self._party_slots):
            self._slots_layout.removeWidget(slot)
            slot.deleteLater()
        self._party_slots.clear()
        self._update_party_count()

    def _update_party_count(self):
        n = len(self._party_slots)
        self._party_count_lbl.setText(f"{n} / 6")
        self._party_count_lbl.setStyleSheet(
            f"color: {'#ff8a80' if n > 6 else '#aaa'}; font-size: 10px;"
        )

    # ── public API ────────────────────────────────────────────────────────────
    def load(self, const: str, trainer: dict, party: Optional[dict]):
        """Populate all panel fields from trainer dict + optional party dict."""
        self._current_const = const
        self._const_lbl.setText(const)

        # Resolve display name for header
        cls_const   = trainer.get("trainerClass", "")
        cls_display = self._class_names.get(cls_const, cls_const.replace("TRAINER_CLASS_", "").replace("_", " "))
        raw_name    = trainer.get("trainerName", "")
        nm          = re.search(r'_\("([^"]*)"\)', raw_name)
        name_str    = nm.group(1) if nm else raw_name
        self._display_lbl.setText(f"{cls_display} {name_str}".strip())

        # Large sprite
        self._load_sprite(trainer.get("trainerPic", ""))

        # Identity tab
        self._name_edit.blockSignals(True)
        self._name_edit.setText(name_str)
        self._name_edit.blockSignals(False)

        if not self._class_cb.count():
            self._populate_class_combo()
        idx = self._class_cb.findData(cls_const)
        self._class_cb.blockSignals(True)
        if idx >= 0:
            self._class_cb.setCurrentIndex(idx)
        else:
            self._class_cb.setCurrentText(cls_const)   # preserve unknown as text
        self._class_cb.blockSignals(False)

        if not self._pic_cb.count():
            self._populate_pic_combo()
        pic_const = trainer.get("trainerPic", "")
        idx = self._pic_cb.findData(pic_const)
        self._pic_cb.blockSignals(True)
        if idx >= 0:
            self._pic_cb.setCurrentIndex(idx)
        else:
            self._pic_cb.setCurrentText(pic_const)     # preserve unknown as text
        self._pic_cb.blockSignals(False)
        self._on_pic_changed()

        music_raw = trainer.get("encounterMusic_gender", "")
        # Preserve the F_TRAINER_FEMALE flag separately so collect() can restore it
        parts = [p.strip() for p in music_raw.split("|")]
        music_clean = parts[0]
        self._has_female_flag = any(p == "F_TRAINER_FEMALE" for p in parts[1:])
        idx = self._music_cb.findData(music_clean)
        self._music_cb.blockSignals(True)
        if idx >= 0:
            self._music_cb.setCurrentIndex(idx)
        else:
            self._music_cb.setCurrentText(music_clean)  # preserve unknown as text
        self._music_cb.blockSignals(False)

        self._double_cb.blockSignals(True)
        self._double_cb.setChecked(trainer.get("doubleBattle", "FALSE").upper() == "TRUE")
        self._double_cb.blockSignals(False)

        # AI tab
        active_flags = {f.strip() for f in trainer.get("aiFlags", "").split("|") if f.strip()}
        for fconst, cb in self._ai_checks.items():
            cb.blockSignals(True)
            cb.setChecked(fconst in active_flags)
            cb.blockSignals(False)

        # Bag tab
        raw_items = trainer.get("items", "{}").strip("{}")
        bag_items = [s.strip() for s in raw_items.split(",") if s.strip() and s.strip() not in ("", "0")]
        for i, cb in enumerate(self._bag_cbs):
            cb.blockSignals(True)
            item_val = bag_items[i] if i < len(bag_items) else "ITEM_NONE"
            idx = cb.findData(item_val)
            cb.setCurrentIndex(idx if idx >= 0 else 0)
            cb.blockSignals(False)

        # Party tab
        self._clear_party_slots()
        ptype = "NO_ITEM_DEFAULT_MOVES"
        for macro_key in _STRUCT_FOR_TYPE:
            if macro_key in trainer.get("party", ""):
                ptype = macro_key
                break
        idx = self._party_type_cb.findData(ptype)
        self._party_type_cb.blockSignals(True)
        self._party_type_cb.setCurrentIndex(idx if idx >= 0 else 0)
        self._party_type_cb.blockSignals(False)

        if party:
            for member in party.get("members", []):
                self._add_slot_with_data(ptype, member)
        self._update_party_count()

    def _add_slot_with_data(self, ptype: str, member: dict):
        slot = _PartySlotWidget(self._species_list, self._items_list, self._moves_list,
                                icon_fn=self._species_icon_fn, parent=self)
        slot.set_party_type(ptype)
        slot.load(member)
        slot.changed.connect(lambda: self.changed.emit())
        slot.remove_requested.connect(self._remove_party_slot)
        self._party_slots.append(slot)
        self._slots_layout.insertWidget(self._slots_layout.count() - 1, slot)

    def collect(self) -> tuple[dict, dict]:
        """Return (trainer_dict_updates, party_dict)."""
        trainer: dict = {}
        trainer["trainerName"]          = f'_("{self._name_edit.text()}")'
        trainer["trainerClass"]         = (
            self._class_cb.currentData() or self._class_cb.currentText()
        )
        trainer["trainerPic"]           = (
            self._pic_cb.currentData() or self._pic_cb.currentText()
        )
        music_val = self._music_cb.currentData() or self._music_cb.currentText()
        if getattr(self, "_has_female_flag", False):
            music_val = music_val + " | F_TRAINER_FEMALE"
        trainer["encounterMusic_gender"] = music_val
        trainer["doubleBattle"] = "TRUE" if self._double_cb.isChecked() else "FALSE"

        active_ai = [c for c, cb in self._ai_checks.items() if cb.isChecked()]
        trainer["aiFlags"] = " | ".join(active_ai) if active_ai else "0"

        bag: list[str] = []
        for cb in self._bag_cbs:
            v = cb.currentData() or cb.currentText() or ""
            if v and v not in ("ITEM_NONE", "0", ""):
                bag.append(v)
        trainer["items"] = "{" + ", ".join(bag) + "}" if bag else "{}"

        ptype = self._party_type_cb.currentData() or "NO_ITEM_DEFAULT_MOVES"
        party_symbol = f"sParty_{_trainer_const_to_party_symbol(self._current_const or '')}"
        trainer["party"] = f"{ptype}({party_symbol})"

        members = [slot.collect() for slot in self._party_slots]
        party   = {"type": ptype, "members": members}
        return trainer, party


# ══════════════════════════════════════════════════════════════════════════════
# Main tab widget
# ══════════════════════════════════════════════════════════════════════════════

class TrainersTabWidget(QWidget):
    """Full trainer editor — searchable list on left, detail panel on right."""

    changed          = pyqtSignal()
    rename_requested = pyqtSignal(str)   # old_const → mainwindow drives RefactorService

    def __init__(self, parent=None):
        super().__init__(parent)
        self._trainers: dict       = {}
        self._parties: dict        = {}        # {sParty_Symbol: {"type":…, "members":[…]}}
        self._class_names: dict    = {}
        self._pic_map: dict        = {}
        self._order: list[str]     = []
        self._project_root: str    = ""
        self._current_const: Optional[str] = None
        self._pending_party_writes: dict   = {}   # {symbol: new_c_code}
        self._species_list: list   = [("SPECIES_NONE", "NONE")]
        self._items_list: list     = [("ITEM_NONE", "None")]
        self._moves_list: list     = [("MOVE_NONE", "None")]
        self._detail_panel: Optional[_TrainerDetailPanel] = None
        self._build()

    # ── build ─────────────────────────────────────────────────────────────────
    def _build(self):
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        # Toolbar
        bar = QHBoxLayout()
        bar.setContentsMargins(8, 6, 8, 4)
        title_lbl = QLabel("Trainers")
        title_lbl.setStyleSheet("font-size: 15px; font-weight: bold; color: #e0e0e0;")
        bar.addWidget(title_lbl)
        bar.addStretch()
        outer.addLayout(bar)

        # Warning bar (hidden until a warning is set)
        self._warn_lbl = QLabel()
        self._warn_lbl.setStyleSheet(_WARN_SS)
        self._warn_lbl.setWordWrap(True)
        self._warn_lbl.hide()
        outer.addWidget(self._warn_lbl)

        # Splitter: left list | right detail
        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.setHandleWidth(2)
        splitter.setStyleSheet("QSplitter::handle { background: #2e2e2e; }")

        # ── left panel ────────────────────────────────────────────────────────
        left = QWidget()
        left.setStyleSheet("background: #191919;")
        left.setMinimumWidth(160)
        left_v = QVBoxLayout(left)
        left_v.setContentsMargins(0, 0, 0, 0)
        left_v.setSpacing(0)

        self._search = QLineEdit()
        self._search.setPlaceholderText("Search trainers…")
        self._search.setStyleSheet(
            "background: #222; border: none; border-bottom: 1px solid #2a2a2a; "
            "padding: 6px; color: #ccc;"
        )
        self._search.textChanged.connect(self._rebuild_list)
        left_v.addWidget(self._search)

        self._list = QListWidget()
        self._list.setStyleSheet(_LIST_SS)
        self._list.setItemDelegate(_TrainerListDelegate(self._list))
        self._list.setUniformItemSizes(False)
        self._list.setIconSize(QSize(_TrainerListDelegate._SPR_W,
                                     _TrainerListDelegate._SPR_H))
        self._list.currentItemChanged.connect(self._on_selection_changed)
        left_v.addWidget(self._list)

        add_btn = QPushButton("+ Add Trainer")
        add_btn.setStyleSheet(
            "background: #1a3a1a; color: #aaffaa; border: none; padding: 7px; "
            "border-top: 1px solid #2a2a2a;"
        )
        add_btn.clicked.connect(self._add_trainer)
        left_v.addWidget(add_btn)

        splitter.addWidget(left)

        # ── right panel (placeholder until load()) ────────────────────────────
        self._detail_scroll = QScrollArea()
        self._detail_scroll.setWidgetResizable(True)
        self._detail_scroll.setFrameShape(QFrame.Shape.NoFrame)
        placeholder = QLabel("Open a project to edit trainers.")
        placeholder.setAlignment(Qt.AlignmentFlag.AlignCenter)
        placeholder.setStyleSheet("color: #555;")
        self._detail_scroll.setWidget(placeholder)
        splitter.addWidget(self._detail_scroll)

        splitter.setSizes([230, 900])
        outer.addWidget(splitter, 1)

    # ── public API ────────────────────────────────────────────────────────────

    def load(
        self,
        trainers: dict,
        project_root: str,
        species_list:    Optional[list] = None,
        items_list:      Optional[list] = None,
        moves_list:      Optional[list] = None,
        species_icon_fn = None,
    ):
        """Load all trainer data. Call whenever a project is opened."""
        self._trainers          = dict(trainers)
        self._project_root      = project_root
        self._species_list      = species_list or [("SPECIES_NONE", "NONE")]
        self._items_list        = items_list   or [("ITEM_NONE",    "None")]
        self._moves_list        = moves_list   or [("MOVE_NONE",    "None")]
        self._species_icon_fn   = species_icon_fn

        self._class_names = _parse_trainer_class_names(project_root)
        self._pic_map     = _parse_trainer_pic_map(project_root)
        self._parties     = _parse_trainer_parties(project_root)
        self._order       = self._load_trainer_order(project_root)

        # Reset current selection — the old panel is being replaced, so
        # _flush_current must not collect stale data from the new empty panel.
        self._current_const = None

        # Build a fresh detail panel with the new lists
        self._detail_panel = _TrainerDetailPanel(
            self._class_names,
            self._pic_map,
            list(self._pic_map.keys()),
            self._species_list,
            self._items_list,
            self._moves_list,
            species_icon_fn=species_icon_fn,
            parent=self,
        )
        self._detail_panel.changed.connect(lambda: self.changed.emit())
        self._detail_panel.rename_requested.connect(self.rename_requested.emit)
        self._detail_scroll.setWidget(self._detail_panel)

        self._rebuild_list()
        if self._list.count() > 0:
            self._list.setCurrentRow(0)

    def flush(self) -> dict:
        """Flush panel edits to internal dict and return updated trainers."""
        self._flush_current()
        return dict(self._trainers)

    def get_pending_party_writes(self) -> dict:
        """Return {sParty_symbol: c_code} for parties that need writing to disk."""
        return dict(self._pending_party_writes)

    def clear_pending_party_writes(self):
        self._pending_party_writes.clear()

    def show_script_warnings(self, const: str):
        """Scan script files for references to const and display warning."""
        refs = _find_script_refs(self._project_root, const)
        if refs:
            self._show_warn(
                f"⚠ The following script files still reference {const} and need manual updates:\n"
                + "\n".join(f"  • {r}" for r in refs[:25])
                + ("\n  …and more." if len(refs) > 25 else "")
            )

    # ── internals ─────────────────────────────────────────────────────────────

    def _load_trainer_order(self, root: str) -> list[str]:
        """Read opponents.h and return constants sorted by numeric ID."""
        path = os.path.join(root, "include", "constants", "opponents.h")
        order: dict[int, str] = {}
        if os.path.isfile(path):
            pat = re.compile(r'#define\s+(TRAINER_\w+)\s+(\d+)')
            try:
                with open(path, encoding="utf-8", errors="replace") as f:
                    for line in f:
                        m = pat.search(line)
                        if m and not m.group(1).startswith("NUM_") and not m.group(1).startswith("MAX_"):
                            order[int(m.group(2))] = m.group(1)
            except Exception as exc:
                log.warning("_load_trainer_order: %s", exc)
        if not order:
            return list(self._trainers.keys())
        return [v for _, v in sorted(order.items())]

    def _display_name(self, const: str) -> str:
        t           = self._trainers.get(const, {})
        cls_const   = t.get("trainerClass", "")
        cls_display = self._class_names.get(
            cls_const,
            cls_const.replace("TRAINER_CLASS_", "").replace("_", " "),
        )
        raw_name = t.get("trainerName", "")
        nm       = re.search(r'_\("([^"]*)"\)', raw_name)
        name_str = nm.group(1) if nm else ""
        return f"{cls_display} {name_str}".strip() if name_str else cls_display

    def _trainer_pixmap(self, const: str) -> QPixmap:
        pic_const = self._trainers.get(const, {}).get("trainerPic", "")
        if pic_const and pic_const in self._pic_map:
            path = self._pic_map[pic_const]
            if os.path.isfile(path):
                pix = QPixmap(path)
                if not pix.isNull():
                    return pix
        return QPixmap()

    def _rebuild_list(self, needle: str = ""):
        if not isinstance(needle, str):
            needle = self._search.text()
        needle_lc = needle.lower()

        self._list.blockSignals(True)
        self._list.clear()

        groups: dict[str, list[str]] = defaultdict(list)
        for const in self._order:
            if const not in self._trainers or const == "TRAINER_NONE":
                continue
            display = self._display_name(const)
            if needle_lc and needle_lc not in display.lower() and needle_lc not in const.lower():
                continue
            t         = self._trainers[const]
            cls_const = t.get("trainerClass", "")
            cls_label = self._class_names.get(cls_const, cls_const)
            groups[cls_label].append(const)

        for cls_label in sorted(groups.keys()):
            for const in groups[cls_label]:
                display = self._display_name(const)
                item    = QListWidgetItem(display)
                item.setData(Qt.ItemDataRole.UserRole, const)
                pix = self._trainer_pixmap(const)
                if not pix.isNull():
                    item.setIcon(QIcon(pix))
                item.setToolTip(const)
                item.setSizeHint(QSize(0, _TrainerListDelegate._ROW_H))
                self._list.addItem(item)

        self._list.blockSignals(False)

    def _on_selection_changed(self, current, _previous):
        self._flush_current()
        if current is None:
            return
        const = current.data(Qt.ItemDataRole.UserRole)
        if not const:
            return
        self._current_const = const
        trainer     = self._trainers.get(const, {})
        party_sym   = _extract_party_symbol(trainer.get("party", ""))
        party       = self._parties.get(party_sym) if party_sym else None
        if self._detail_panel:
            self._detail_panel.load(const, trainer, party)
        self._warn_lbl.hide()

    def _flush_current(self):
        if not self._detail_panel or not self._current_const:
            return
        try:
            trainer_updates, party_update = self._detail_panel.collect()
        except Exception as exc:
            log.warning("_flush_current collect: %s", exc)
            return

        # Guard: if the panel returned empty critical fields, it wasn't
        # properly loaded (e.g. combos not populated yet).  Don't overwrite
        # the real trainer data with blanks.
        if not trainer_updates.get("trainerClass") and not trainer_updates.get("trainerPic"):
            existing = self._trainers.get(self._current_const, {})
            if existing.get("trainerClass") or existing.get("trainerPic"):
                # The existing data has real values but collect gave us nothing
                # — skip the update to avoid wiping the trainer.
                return

        existing = self._trainers.get(self._current_const, {})
        existing.update(trainer_updates)
        self._trainers[self._current_const] = existing

        # Track party dirtiness
        party_sym = _extract_party_symbol(existing.get("party", ""))
        if party_sym:
            old = self._parties.get(party_sym)
            if old != party_update:
                self._parties[party_sym] = party_update
                self._pending_party_writes[party_sym] = _generate_party_c(
                    party_sym, party_update["type"], party_update["members"]
                )

    def _add_trainer(self):
        const, ok = QInputDialog.getText(
            self, "Add Trainer",
            "Trainer constant name (TRAINER_ prefix will be added if missing):",
        )
        if not ok or not const.strip():
            return
        const = const.strip().upper()
        if not const.startswith("TRAINER_"):
            const = "TRAINER_" + const
        if const in self._trainers:
            QMessageBox.warning(self, "Duplicate", f"{const} already exists.")
            return

        next_id    = self._next_trainer_id()
        party_sym  = f"sParty_{_trainer_const_to_party_symbol(const)}"
        first_cls  = next(iter(self._class_names.keys()), "")
        first_pic  = next(iter(self._pic_map.keys()), "")

        self._trainers[const] = {
            "trainerClass":          first_cls,
            "encounterMusic_gender": "TRAINER_ENCOUNTER_MUSIC_MALE",
            "trainerPic":            first_pic,
            "trainerName":           '_("")',
            "items":                 "{}",
            "doubleBattle":          "FALSE",
            "aiFlags":               "AI_SCRIPT_CHECK_BAD_MOVE",
            "party":                 f"NO_ITEM_DEFAULT_MOVES({party_sym})",
        }
        self._parties[party_sym] = {"type": "NO_ITEM_DEFAULT_MOVES", "members": []}
        self._order.append(const)
        self._pending_party_writes[party_sym] = _generate_party_c(
            party_sym, "NO_ITEM_DEFAULT_MOVES", []
        )

        self._rebuild_list()
        for i in range(self._list.count()):
            item = self._list.item(i)
            if item and item.data(Qt.ItemDataRole.UserRole) == const:
                self._list.setCurrentRow(i)
                break

        self.changed.emit()
        self._show_warn(
            f"✚ {const} created (ID {next_id}). "
            f"You must manually add  #define {const}  {next_id}  to "
            f"include/constants/opponents.h before building."
        )

    def _next_trainer_id(self) -> int:
        path = os.path.join(self._project_root, "include", "constants", "opponents.h")
        max_id = 0
        if os.path.isfile(path):
            pat = re.compile(r'#define\s+TRAINER_\w+\s+(\d+)')
            try:
                with open(path, encoding="utf-8", errors="replace") as f:
                    for line in f:
                        m = pat.search(line)
                        if m:
                            n = int(m.group(1))
                            if n > max_id:
                                max_id = n
            except Exception:
                pass
        return max_id + 1

    def _show_warn(self, msg: str):
        self._warn_lbl.setText(msg)
        self._warn_lbl.show()
