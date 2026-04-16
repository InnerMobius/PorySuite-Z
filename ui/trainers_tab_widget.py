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
    QCheckBox, QComboBox, QCompleter, QDialog, QDialogButtonBox,
    QFormLayout, QFrame, QGroupBox,
    QHBoxLayout, QInputDialog, QLabel, QLineEdit,
    QListWidget, QListWidgetItem, QMessageBox,
    QPlainTextEdit, QPushButton, QScrollArea, QSizePolicy, QSpinBox,
    QSplitter, QStyle, QStyledItemDelegate, QStyleOptionViewItem,
    QTabWidget, QVBoxLayout, QWidget,
)

from ui.game_text_edit import GameTextEdit, inc_to_display, display_to_inc

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

# ── No-scroll combo box ──────────────────────────────────────────────────────
# Dropdown must never change value on mouse wheel unless the popup is open.
# User scrolls via Chrome Remote Desktop — accidental hover + wheel = data loss.

class _NoScrollCombo(QComboBox):
    """QComboBox that ignores wheel events when the popup isn't showing."""
    def wheelEvent(self, event):
        if self.view().isVisible():
            super().wheelEvent(event)
        else:
            event.ignore()  # pass to parent for page scrolling


class _NoScrollSpin(QSpinBox):
    """QSpinBox that ignores wheel events unless it has focus."""
    def wheelEvent(self, event):
        if self.hasFocus():
            super().wheelEvent(event)
        else:
            event.ignore()


# ── Searchable, non-free-text combo box ──────────────────────────────────────
# A QComboBox that:
#   • Permits type-to-search via an auto-completer popup (case-insensitive,
#     contains-mode) — users still get fast filtering from the keyboard.
#   • Refuses to save free text. If the user types something that does not
#     match an existing item, the combo snaps back to the last valid selection
#     on focus-out (or return-key). This prevents users from accidentally
#     saving nonsense constants into trainer data files.
#   • Still inherits the no-scroll-wheel-when-closed behaviour from
#     `_NoScrollCombo`.
#
# Use `set_const(value)` to load a value. Unknown constants are preserved by
# adding them as extra items so that `currentData()` round-trips correctly
# through save → load cycles and `collect()` never needs a text fallback.

class _SearchableConstCombo(_NoScrollCombo):
    """Editable-for-search, locked-for-save combo box."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setEditable(True)
        self.setInsertPolicy(QComboBox.InsertPolicy.NoInsert)
        self._last_valid_index = 0
        try:
            comp = self.completer()
            if comp is not None:
                comp.setCaseSensitivity(Qt.CaseSensitivity.CaseInsensitive)
                comp.setFilterMode(Qt.MatchFlag.MatchContains)
                comp.setCompletionMode(
                    QCompleter.CompletionMode.PopupCompletion)
        except Exception:
            pass
        self.currentIndexChanged.connect(self._remember_index)
        try:
            le = self.lineEdit()
            if le is not None:
                le.editingFinished.connect(self._snap_back_if_invalid)
        except Exception:
            pass

    def _remember_index(self, idx: int):
        if idx >= 0:
            self._last_valid_index = idx

    def _snap_back_if_invalid(self):
        txt = self.currentText().strip()
        # Exact match on visible label OR on stored data (constant).
        for i in range(self.count()):
            if self.itemText(i).strip() == txt or self.itemData(i) == txt:
                self.setCurrentIndex(i)
                self._last_valid_index = i
                return
        # Fall back to case-insensitive label match.
        lo = txt.lower()
        for i in range(self.count()):
            if self.itemText(i).strip().lower() == lo:
                self.setCurrentIndex(i)
                self._last_valid_index = i
                return
        # Nothing matches — revert to last valid selection.
        self.setCurrentIndex(self._last_valid_index)

    def set_const(self, const: str):
        """Select by data (constant). Unknown values are appended as new
        items so ``currentData()`` always returns the selected constant —
        no text-fallback needed in ``collect()``.
        """
        if not const:
            if self.count() > 0:
                self.blockSignals(True)
                self.setCurrentIndex(0)
                self.blockSignals(False)
                self._last_valid_index = 0
            return
        idx = self.findData(const)
        if idx < 0:
            # Preserve unknown consts from disk so they round-trip.
            self.addItem(const, const)
            idx = self.findData(const)
        self.blockSignals(True)
        self.setCurrentIndex(idx)
        self.blockSignals(False)
        self._last_valid_index = idx


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
    """Return {TRAINER_PIC_CONST: abs_png_path} by cross-referencing constants + graphics.

    The bridge between the ``TRAINER_PIC_*`` constant and the on-disk PNG goes
    through the ``gTrainerFrontPic_<Symbol>`` C symbol, NOT the filename. The
    filename follows its own snake_case convention that doesn't always line up
    with the constant — e.g. ``TRAINER_PIC_COOLTRAINER_M`` has the symbol
    ``CooltrainerM`` but the PNG is named ``cool_trainer_m_front_pic.png``.
    Matching constant suffix against filename directly would miss every
    compound-word class (cooltrainer, pokemaniac, etc.).

    Algorithm: lowercase-and-strip-underscores both the constant's suffix AND
    the C symbol. Those two forms agree on every case we've seen.
    """
    # Build {symbol_key: abs_png_path} from gTrainerFrontPic_<Symbol> entries.
    # symbol_key = symbol lowercased with underscores stripped, so that
    # compound-word symbols (CooltrainerM, AquaLeaderArchie, RSCooltrainerM)
    # collapse to a form the constant side can match.
    path_by_symbol: dict[str, str] = {}
    gfx = os.path.join(root, "src", "data", "graphics", "trainers.h")
    if os.path.isfile(gfx):
        pat = re.compile(
            r'gTrainerFrontPic_(\w+)\[\]\s*=\s*INCBIN_U32\("([^"]+front_pic\.4bpp\.lz)"\)'
        )
        try:
            with open(gfx, encoding="utf-8", errors="replace") as f:
                for line in f:
                    m = pat.search(line)
                    if m:
                        symbol = m.group(1)                       # CooltrainerM
                        rel    = m.group(2)
                        key    = symbol.replace("_", "").lower()   # cooltrainerm
                        png    = os.path.join(root, rel.replace(".4bpp.lz", ".png"))
                        path_by_symbol[key] = png
        except Exception as exc:
            log.warning("_parse_trainer_pic_map gfx: %s", exc)

    # Build {TRAINER_PIC_CONST: abs_png_path} via symbol-key match
    result: dict[str, str] = {}
    const_h = os.path.join(root, "include", "constants", "trainers.h")
    if os.path.isfile(const_h):
        pat2 = re.compile(r'#define\s+(TRAINER_PIC_\w+)\s+\d+')
        try:
            with open(const_h, encoding="utf-8", errors="replace") as f:
                for line in f:
                    m = pat2.search(line)
                    if m:
                        const = m.group(1)                                   # TRAINER_PIC_COOLTRAINER_M
                        suffix = const[len("TRAINER_PIC_"):]                 # COOLTRAINER_M
                        key = suffix.replace("_", "").lower()                # cooltrainerm
                        if key in path_by_symbol:
                            result[const] = path_by_symbol[key]
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
        # Symbol not found — this is a brand new trainer.  Append the
        # new party declaration at the end of the file.
        return text.rstrip() + "\n\n" + new_code + "\n"
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
# Rematch table parser / writer  (vs_seeker.c :: sRematches[])
# ══════════════════════════════════════════════════════════════════════════════

# Progression gates for each position in the sRematches[] trainerIdxs array.
# Position 0 is the original battle (always available).
_DEFAULT_TIER_LABELS = [
    "First Battle",
    "Rematch 1",
    "Rematch 2",
    "Rematch 3",
    "Rematch 4",
    "Rematch 5",
]

_SKIP = "SKIP"  # 0xFFFF marker in the C source


def _parse_max_rematch_parties(root: str) -> int:
    """Read MAX_REMATCH_PARTIES from vs_seeker.c. Returns 6 if not found."""
    path = os.path.join(root, "src", "vs_seeker.c")
    if not os.path.isfile(path):
        return 6
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            for line in f:
                m = re.match(r'#define\s+MAX_REMATCH_PARTIES\s+(\d+)', line.strip())
                if m:
                    return int(m.group(1))
    except Exception:
        pass
    return 6


def _parse_tier_gate_flags(root: str) -> list[str]:
    """Parse the actual tier gate flags from TryGetRematchTrainerIdGivenGameState().

    Returns a list of N flag names where N = MAX_REMATCH_PARTIES.
    Index 0 is always "" (first battle, no gate).
    """
    max_tiers = _parse_max_rematch_parties(root)
    path = os.path.join(root, "src", "vs_seeker.c")
    if not os.path.isfile(path):
        return [""] * max_tiers
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            text = f.read()
    except Exception:
        return [""] * max_tiers

    # Find the function DEFINITION (not just any mention in a comment).
    func_def = re.search(
        r'void\s+TryGetRematchTrainerIdGivenGameState\s*\([^)]*\)\s*\{',
        text)
    if not func_def:
        return [""] * max_tiers
    brace = func_def.end() - 1
    if brace < 0:
        return [""] * max_tiers
    # Find matching closing brace (depth counting)
    depth, end = 0, brace
    for i in range(brace, len(text)):
        if text[i] == "{":
            depth += 1
        elif text[i] == "}":
            depth -= 1
            if depth == 0:
                end = i
                break
    func_body = text[brace:end]
    # Split on case boundaries to avoid cross-case matching
    flags = [""] * max_tiers
    cases = re.split(r'(?=\bcase\s+\d+\s*:)', func_body)
    for block in cases:
        case_m = re.match(r'case\s+(\d+)\s*:', block)
        if not case_m:
            continue
        idx = int(case_m.group(1))
        flag_m = re.search(r'FlagGet\((\w+)\)', block)
        if flag_m and 0 <= idx < max_tiers:
            flags[idx] = flag_m.group(1)
    return flags


def _flag_to_label(flag: str) -> str:
    """Convert a flag constant to a readable label.

    FLAG_WORLD_MAP_CELADON_CITY → Celadon City
    FLAG_SYS_GAME_CLEAR → Game Clear
    FLAG_GOT_VS_SEEKER → VS Seeker
    """
    if not flag:
        return ""
    label = flag
    for prefix in ("FLAG_WORLD_MAP_", "FLAG_SYS_", "FLAG_GOT_", "FLAG_"):
        if label.startswith(prefix):
            label = label[len(prefix):]
            break
    return label.replace("_", " ").title()


def _build_tier_labels(gate_flags: list[str]) -> list[str]:
    """Build human-readable tier labels from parsed gate flags."""
    labels = []
    for i in range(len(gate_flags)):
        if i == 0:
            labels.append("First Battle")
        elif i < len(gate_flags) and gate_flags[i]:
            labels.append(f"Rematch {i} — {_flag_to_label(gate_flags[i])}")
        else:
            labels.append(f"Rematch {i}")
    return labels


def _parse_rematch_table(root: str) -> tuple[list[dict], list[str]]:
    """Parse vs_seeker.c → list of rematch entries.

    Returns
    -------
    entries : list[dict]
        Each entry: {
            "trainers": [str, ...]  — up to 6 trainer constants (or "SKIP")
            "map": str              — e.g. "MAP_ROUTE3"
        }
    raw_lines : list[str]
        The raw lines of vs_seeker.c for write-back.
    """
    path = os.path.join(root, "src", "vs_seeker.c")
    if not os.path.isfile(path):
        return [], []
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            raw_lines = f.readlines()
    except Exception as exc:
        log.warning("_parse_rematch_table read: %s", exc)
        return [], []

    # Read MAX_REMATCH_PARTIES for correct padding
    max_tiers = _parse_max_rematch_parties(root)

    # Find the sRematches[] array
    entries: list[dict] = []
    in_table = False
    buf = ""
    for line in raw_lines:
        stripped = line.strip()
        if not in_table:
            if "sRematches[]" in stripped and "=" in stripped:
                in_table = True
            continue
        if stripped == "};":
            break
        buf += " " + stripped
        # Each entry ends with "}," after the MAP() — detect complete entries
        if "}," in buf and "MAP(" in buf:
            entry = _parse_rematch_entry(buf)
            if entry:
                # Pad trainers list to MAX_REMATCH_PARTIES
                while len(entry["trainers"]) < max_tiers:
                    entry["trainers"].append("")
                entries.append(entry)
            buf = ""

    return entries, raw_lines


def _parse_rematch_entry(text: str) -> Optional[dict]:
    """Parse one sRematches[] entry like:
    { {TRAINER_X, TRAINER_X_2, SKIP, TRAINER_X_3}, MAP(MAP_ROUTE3) },
    """
    # Extract the inner brace content: { {trainers...}, MAP(...) }
    m = re.search(r'\{\s*\{([^}]+)\}\s*,\s*MAP\((\w+)\)', text)
    if not m:
        return None
    trainers_raw = m.group(1)
    map_name = m.group(2)
    trainers = [t.strip() for t in trainers_raw.split(",") if t.strip()]
    return {"trainers": trainers, "map": map_name}


def _build_rematch_map(entries: list[dict]) -> tuple[dict, dict, set]:
    """Build rematch lookups from parsed sRematches[] entries.

    Returns:
        base_map:  {TRAINER_X: {"tiers": [str,...], "map": str, "entry_idx": int}}
        any_map:   {ANY_TIER_CONST: same info dict}  (reverse lookup from any tier)
        variants:  set of constants that are rematch variants (tiers[1:]), to hide
                   from the trainer list since they're accessible via tier dropdown.
    """
    base_map: dict[str, dict] = {}
    any_map: dict[str, dict] = {}
    variants: set[str] = set()
    for idx, entry in enumerate(entries):
        base = entry["trainers"][0]
        if not base or base == _SKIP:
            continue
        info = {
            "tiers": list(entry["trainers"]),
            "map": entry["map"],
            "entry_idx": idx,
        }
        base_map[base] = info
        # Reverse lookup: every non-empty tier constant maps to this info
        for tier_const in entry["trainers"]:
            if tier_const and tier_const != _SKIP:
                any_map[tier_const] = info
        # Variant set: tiers[1:] are rematch variants to hide from the list
        for tier_const in entry["trainers"][1:]:
            if tier_const and tier_const != _SKIP:
                variants.add(tier_const)
    return base_map, any_map, variants


def _parse_all_flags(root: str) -> list[str]:
    """Parse all FLAG_* constants from flags.h for the tier gate picker."""
    path = os.path.join(root, "include", "constants", "flags.h")
    if not os.path.isfile(path):
        return []
    flags: list[str] = []
    pat = re.compile(r'#define\s+(FLAG_\w+)')
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            for line in f:
                m = pat.match(line)
                if m:
                    flags.append(m.group(1))
    except Exception:
        pass
    return flags


def _rewrite_vs_seeker_tier_gates(raw_lines: list[str], gate_flags: list[str],
                                   new_max: int) -> list[str]:
    """Rewrite vs_seeker.c with updated MAX_REMATCH_PARTIES and tier gate function.

    gate_flags: list of flag names, index 0 = "" (first battle, no gate).
    new_max: the new MAX_REMATCH_PARTIES value.
    """
    out: list[str] = []
    in_func = False
    func_depth = 0
    skipped_func = False

    for line in raw_lines:
        stripped = line.strip()

        # Replace MAX_REMATCH_PARTIES define
        if stripped.startswith("#define MAX_REMATCH_PARTIES"):
            out.append(f"#define MAX_REMATCH_PARTIES {new_max}\n")
            continue

        # Replace TryGetRematchTrainerIdGivenGameState function body
        if not in_func and "void TryGetRematchTrainerIdGivenGameState" in stripped and "{" not in stripped:
            # Signature line — keep it, the { will be on the next line or same line
            out.append(line)
            continue

        if not in_func and "void TryGetRematchTrainerIdGivenGameState" in stripped and "{" in stripped:
            # Signature and opening brace on same line
            in_func = True
            func_depth = stripped.count("{") - stripped.count("}")
            continue

        if not in_func and stripped == "{" and len(out) > 0:
            prev = out[-1].strip()
            if "TryGetRematchTrainerIdGivenGameState" in prev:
                in_func = True
                func_depth = 1
                continue

        if in_func:
            func_depth += stripped.count("{") - stripped.count("}")
            if func_depth <= 0:
                # Write replacement function
                out.append("{\n")
                out.append("    switch (*rematchIdx_p)\n")
                out.append("    {\n")
                for i in range(new_max):
                    if i == 0:
                        out.append("     case 0:\n")
                        out.append("         break;\n")
                    else:
                        flag = gate_flags[i] if i < len(gate_flags) else ""
                        if flag:
                            out.append(f"     case {i}:\n")
                            out.append(f"         if (!FlagGet({flag}))\n")
                            out.append(f"             *rematchIdx_p = GetRematchTrainerIdGivenGameState(trainerIdxs, *rematchIdx_p);\n")
                            out.append(f"         break;\n")
                        else:
                            out.append(f"     case {i}:\n")
                            out.append(f"         break;\n")
                out.append("    }\n")
                out.append("}\n")
                in_func = False
                skipped_func = True
                continue
            # Skip original function body lines
            continue

        out.append(line)

    return out


def _pad_rematch_entries(entries: list[dict], new_max: int) -> list[dict]:
    """Pad or trim all rematch entries to the new tier count."""
    result = []
    for entry in entries:
        trainers = list(entry["trainers"])
        # Trim
        while len(trainers) > new_max:
            trainers.pop()
        # Pad with empty
        while len(trainers) < new_max:
            trainers.append("")
        result.append({"trainers": trainers, "map": entry["map"]})
    return result


def _write_rematch_table(raw_lines: list[str], entries: list[dict]) -> list[str]:
    """Rebuild vs_seeker.c with an updated sRematches[] table.

    Replaces only the array content between the opening { and closing };
    Preserves everything else in the file.
    """
    out: list[str] = []
    in_table = False
    wrote_replacement = False

    for line in raw_lines:
        stripped = line.strip()
        if not in_table:
            out.append(line)
            if "sRematches[]" in stripped and "=" in stripped:
                in_table = True
            continue
        # We're inside the table — skip all original lines until };
        if stripped == "};":
            if not wrote_replacement:
                for entry in entries:
                    trainers = entry["trainers"]
                    # Trim trailing empty/SKIP slots
                    last = 0
                    for i in range(len(trainers)):
                        if trainers[i] and trainers[i] != "":
                            last = i
                    trimmed = trainers[:last + 1]
                    t_str = ", ".join(trimmed)
                    map_name = entry["map"]
                    out.append(f"   {{ {{{t_str}}},\n")
                    out.append(f"      MAP({map_name}) }},\n")
                wrote_replacement = True
            out.append(line)  # the closing };
            in_table = False
            continue
        # Skip original table content — we're replacing it
    return out


# ══════════════════════════════════════════════════════════════════════════════
# Rematch Settings dialog
# ══════════════════════════════════════════════════════════════════════════════

def _load_disk_flag_labels(project_root: str) -> dict[str, str]:
    """Return ``{FLAG_CONST: user_label}`` read from ``porysuite_labels.json``.

    Accepts both the canonical versioned wrapper
    (``{"version": 1, "labels": {const: {label, notes}}}``) and a bare flat
    dict for legacy / imported files.

    Silently returns an empty dict on any I/O / JSON error — callers fall
    back to displaying bare constants, which is a valid UX state.
    """
    out: dict[str, str] = {}
    if not project_root:
        return out
    path = os.path.join(project_root, "porysuite_labels.json")
    if not os.path.isfile(path):
        return out
    try:
        import json as _json
        with open(path, encoding="utf-8") as f:
            raw = _json.load(f) or {}
    except Exception:
        return out
    if isinstance(raw, dict) and "labels" in raw and isinstance(raw["labels"], dict):
        raw = raw["labels"]
    if not isinstance(raw, dict):
        return out
    for const, data in raw.items():
        if isinstance(data, dict):
            lbl = (data.get("label") or "").strip()
            if lbl:
                out[const] = lbl
    return out


class _RematchSettingsDialog(QDialog):
    """Dialog to edit VS Seeker tier count and gate flags.

    Writes changes to vs_seeker.c: MAX_REMATCH_PARTIES, the switch statement
    in TryGetRematchTrainerIdGivenGameState, and pads/trims sRematches[].
    """

    def __init__(self, gate_flags: list[str], all_flags: list[str],
                 rematch_entries: list[dict], raw_lines: list[str],
                 project_root: str, parent=None):
        super().__init__(parent)
        self.setWindowTitle("VS Seeker Rematch Settings")
        self.setMinimumWidth(600)
        self._gate_flags = list(gate_flags)
        self._all_flags = all_flags
        self._rematch_entries = rematch_entries
        self._raw_lines = raw_lines
        self._project_root = project_root
        self._tier_rows: list[QComboBox] = []
        # Load any user-set flag labels from porysuite_labels.json. When
        # a label is present the dropdown shows "Beat Misty  (FLAG_*)"
        # instead of the bare constant. If no labels are saved to disk,
        # the dropdown falls through to bare constants — that is the
        # expected behaviour, not a bug.
        self._flag_labels: dict[str, str] = _load_disk_flag_labels(project_root)
        self._build()

    def _build(self):
        layout = QVBoxLayout(self)
        layout.setSpacing(8)

        # Explanation
        info = QLabel(
            "Each rematch tier is gated by a story progression flag. "
            "When the player uses the VS Seeker, the game checks which flags "
            "are set and picks the highest available tier for each trainer.\n\n"
            "Tier 0 is always the first battle (no gate). "
            "Add more tiers for additional progression stages. "
            "All changes write directly to vs_seeker.c."
        )
        info.setWordWrap(True)
        info.setStyleSheet("color: #aaa; font-size: 11px; padding: 4px;")
        layout.addWidget(info)

        # Tier count
        count_row = QHBoxLayout()
        count_row.addWidget(QLabel("Number of tiers:"))
        self._count_spin = _NoScrollSpin()
        self._count_spin.setRange(2, 20)
        self._count_spin.setValue(len(self._gate_flags))
        self._count_spin.setToolTip(
            "Total number of battle tiers including the first battle.\n"
            "Vanilla pokefirered has 6. Increase for more progression stages."
        )
        self._count_spin.valueChanged.connect(self._on_count_changed)
        count_row.addWidget(self._count_spin)
        count_row.addStretch()
        layout.addLayout(count_row)

        # Tier gate list
        self._tiers_container = QWidget()
        self._tiers_layout = QVBoxLayout(self._tiers_container)
        self._tiers_layout.setSpacing(4)
        self._tiers_layout.setContentsMargins(0, 0, 0, 0)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setWidget(self._tiers_container)
        layout.addWidget(scroll, 1)

        self._rebuild_tier_rows()

        # Buttons
        btn_box = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save |
            QDialogButtonBox.StandardButton.Cancel
        )
        btn_box.accepted.connect(self._on_save)
        btn_box.rejected.connect(self.reject)
        layout.addWidget(btn_box)

    def _rebuild_tier_rows(self):
        # Clear existing
        while self._tiers_layout.count() > 0:
            item = self._tiers_layout.takeAt(0)
            w = item.widget()
            if w:
                w.deleteLater()
        self._tier_rows.clear()

        count = self._count_spin.value()
        # Ensure gate_flags list matches
        while len(self._gate_flags) < count:
            self._gate_flags.append("")
        while len(self._gate_flags) > count:
            self._gate_flags.pop()

        for i in range(count):
            row = QHBoxLayout()
            if i == 0:
                label = QLabel(f"Tier 0  (First Battle):")
                label.setMinimumWidth(180)
                row.addWidget(label)
                note = QLabel("No gate — always available")
                note.setStyleSheet("color: #888;")
                row.addWidget(note, 1)
                self._tier_rows.append(None)  # No combo for tier 0
            else:
                label = QLabel(f"Tier {i}  gate flag:")
                label.setMinimumWidth(180)
                row.addWidget(label)
                cb = _NoScrollCombo()
                cb.addItem("(none — always available)", "")
                for flag in self._all_flags:
                    # Show the user's friendly label from the Label Manager
                    # if one exists, e.g. "Beat Misty  (FLAG_BADGE02_GET)".
                    # Bare constants remain for flags with no label set.
                    lbl = self._flag_labels.get(flag, "")
                    if lbl:
                        cb.addItem(f"{lbl}  ({flag})", flag)
                    else:
                        cb.addItem(flag, flag)
                # Set current value — match by data (the raw FLAG_ constant),
                # never by text, so friendly-label formatting doesn't break
                # round-tripping edits.
                current = self._gate_flags[i] if i < len(self._gate_flags) else ""
                if current:
                    idx = cb.findData(current)
                    if idx >= 0:
                        cb.setCurrentIndex(idx)
                    else:
                        cb.setCurrentText(current)
                row.addWidget(cb, 1)
                self._tier_rows.append(cb)

            container = QWidget()
            container.setLayout(row)
            self._tiers_layout.addWidget(container)

        self._tiers_layout.addStretch()

    def _on_count_changed(self, value: int):
        self._rebuild_tier_rows()

    def _collect_flags(self) -> list[str]:
        """Collect the current flag settings from the UI."""
        flags = []
        for i, cb in enumerate(self._tier_rows):
            if i == 0 or cb is None:
                flags.append("")
            else:
                val = cb.currentData()
                if val is None:
                    val = cb.currentText().strip()
                flags.append(val if val else "")
        return flags

    def _on_save(self):
        """Write changes to vs_seeker.c."""
        new_flags = self._collect_flags()
        new_max = len(new_flags)

        # Step 1: Rewrite the tier gate function and MAX_REMATCH_PARTIES
        updated_lines = _rewrite_vs_seeker_tier_gates(
            self._raw_lines, new_flags, new_max)

        # Step 2: Pad/trim all sRematches[] entries to new tier count
        padded_entries = _pad_rematch_entries(self._rematch_entries, new_max)

        # Step 3: Rewrite the sRematches[] table
        final_lines = _write_rematch_table(updated_lines, padded_entries)

        # Step 4: Write to disk
        path = os.path.join(self._project_root, "src", "vs_seeker.c")
        try:
            with open(path, "w", encoding="utf-8", newline="\n") as f:
                f.writelines(final_lines)
            log.info("Rematch settings saved: %d tiers → %s", new_max, path)
            QMessageBox.information(
                self, "Saved",
                f"VS Seeker rematch settings saved to vs_seeker.c.\n\n"
                f"Tiers: {new_max}\n"
                f"Entries: {len(padded_entries)}\n\n"
                f"You may need to rebuild the ROM for changes to take effect."
            )
            self.accept()
        except Exception as exc:
            QMessageBox.critical(
                self, "Save Error",
                f"Failed to write vs_seeker.c:\n{exc}")

    def get_updated_flags(self) -> list[str]:
        return self._collect_flags()


# ══════════════════════════════════════════════════════════════════════════════
# Trainer list delegate
# ══════════════════════════════════════════════════════════════════════════════

class _TrainerListDelegate(QStyledItemDelegate):
    _ROW_H = 60
    _SPR_W = 40
    _SPR_H = 52
    _PAD   = 6
    # Shared dirty-role slot — matches MainWindow.DIRTY_FLAG_ROLE.
    # The trainer list uses a custom delegate (sprite+name+const rendering)
    # so the stock _DirtyDelegate from mainwindow can't be stacked on top.
    # Instead this delegate bakes in the same amber-tint logic so dirty
    # rows visually match other tabs (Moves, Items, Abilities).
    _DIRTY_ROLE = Qt.ItemDataRole.UserRole + 500

    def paint(self, painter: QPainter, option: QStyleOptionViewItem, index):
        painter.save()
        try:
            _sel = QStyle.StateFlag.State_Selected
        except AttributeError:
            _sel = QStyle.State.State_Selected  # type: ignore[attr-defined]
        selected = bool(option.state & _sel)
        painter.fillRect(option.rect, QColor("#1565c0" if selected else "#191919"))
        # Amber overlay for dirty (edited-but-unsaved) rows. Same 90-alpha
        # tint the stock _DirtyDelegate uses in other tabs so visual style
        # stays consistent across the app.
        if index.data(self._DIRTY_ROLE):
            painter.fillRect(option.rect, QColor(255, 183, 77, 90))

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

        self._species_cb = _SearchableConstCombo()
        self._species_cb.setMinimumWidth(140)
        self._species_cb.setMaximumWidth(220)
        for const, name in self._species_list:
            self._species_cb.addItem(name, const)
        self._species_cb.currentIndexChanged.connect(self._on_species_changed)
        hdr.addWidget(self._species_cb)

        hdr.addSpacing(8)
        hdr.addWidget(QLabel("Lv"))
        self._lvl_spin = _NoScrollSpin()
        self._lvl_spin.setRange(1, 100)
        self._lvl_spin.setMinimumWidth(75)
        self._lvl_spin.valueChanged.connect(lambda: self.changed.emit())
        hdr.addWidget(self._lvl_spin)

        hdr.addSpacing(8)
        hdr.addWidget(QLabel("IV"))
        self._iv_spin = _NoScrollSpin()
        self._iv_spin.setRange(0, 255)
        self._iv_spin.setMinimumWidth(75)
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
        self._item_cb = _SearchableConstCombo()
        self._item_cb.setMinimumWidth(180)
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
                cb = _SearchableConstCombo()
                cb.setMinimumWidth(160)
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
        # _SearchableConstCombo.set_const preserves unknown constants from
        # disk by appending them as extra items — so currentData() always
        # returns the selected constant, letting collect() skip the text
        # fallback that previously let free-typed nonsense get saved.
        self._species_cb.set_const(member.get("species", "SPECIES_NONE"))
        try:
            self._lvl_spin.setValue(int(member.get("lvl", 5)))
        except (ValueError, TypeError):
            self._lvl_spin.setValue(5)
        try:
            self._iv_spin.setValue(int(member.get("iv", 0)))
        except (ValueError, TypeError):
            self._iv_spin.setValue(0)
        self._item_cb.set_const(member.get("heldItem", "ITEM_NONE"))
        moves = member.get("moves", [])
        for i, cb in enumerate(self._move_cbs):
            cb.set_const(moves[i] if i < len(moves) else "MOVE_NONE")
        # Populate sprite now that species is set (signals were blocked above)
        self._on_species_changed()

    def collect(self) -> dict:
        # currentData() is authoritative — the _SearchableConstCombo snap-back
        # logic + set_const on load guarantee currentData() returns a real
        # constant, never None. No text fallback means no nonsense strings
        # ever reach the save pipeline.
        species = self._species_cb.currentData() or "SPECIES_NONE"
        result: dict = {
            "species": species,
            "lvl":     str(self._lvl_spin.value()),
            "iv":      str(self._iv_spin.value()),
        }
        if self._item_row_w.isVisible():
            result["heldItem"] = self._item_cb.currentData() or "ITEM_NONE"
        if self._moves_row_w.isVisible():
            result["moves"] = [
                (cb.currentData() or "MOVE_NONE")
                for cb in self._move_cbs
            ]
        return result

    def _on_species_changed(self):
        const = self._species_cb.currentData()
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
    setup_battle_requested = pyqtSignal()  # emitted when "Set up battle" button clicked
    edit_tier_gates_requested = pyqtSignal()  # open rematch settings dialog
    add_to_rematch_requested = pyqtSignal(str)  # emits trainer const to add
    # Emitted when the user is about to switch away from a rematch-tier party
    # view and we've just flushed that tier's slot-widget edits to the shared
    # parties dict. Parent listens to queue a pending .c write for the
    # affected sParty symbol so Save picks it up even if no trainer switch
    # happens afterwards. Signature: (sParty_symbol, party_dict).
    tier_party_modified = pyqtSignal(str, dict)
    _loading         = False  # True while populating fields — suppresses changed signal

    def __init__(
        self,
        class_names: dict,
        pic_map: dict,
        trainer_pic_consts: list,
        species_list: list,
        items_list: list,
        moves_list: list,
        species_icon_fn=None,
        project_root: str = "",
        rematch_map: Optional[dict] = None,
        all_trainers: Optional[dict] = None,
        all_parties: Optional[dict] = None,
        tier_labels: Optional[list] = None,
        gate_flags: Optional[list] = None,
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
        self._project_root       = project_root
        self._current_const: Optional[str] = None
        self._has_female_flag: bool = False
        self._party_slots: list[_PartySlotWidget] = []
        self._dialogue_labels: dict = {}   # {map_name: {type: (label, text)}}
        # Pending dialogue for newly-added trainers that aren't on a map yet.
        # {trainer_const: {type: (label, text)}} — held in RAM until the
        # trainer gets placed on a map via Event Editor.
        self._pending_dialogue: dict = {}
        self._rematch_map_data   = rematch_map or {}
        self._all_trainers_data  = all_trainers or {}
        self._all_parties_data   = all_parties or {}
        self._tier_labels        = tier_labels or _DEFAULT_TIER_LABELS
        self._gate_flags         = gate_flags or [""] * 6
        self._build()

    # ── build ─────────────────────────────────────────────────────────────────
    def _build(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(8, 4, 8, 4)
        root.setSpacing(4)

        # Header: compact sprite + name + const + rename
        hdr = QHBoxLayout()
        hdr.setSpacing(8)

        self._sprite_lbl = QLabel()
        self._sprite_lbl.setFixedSize(64, 64)
        self._sprite_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._sprite_lbl.setStyleSheet(
            "background: #111; border-radius: 4px; border: 1px solid #333;"
        )
        hdr.addWidget(self._sprite_lbl)

        info_col = QVBoxLayout()
        info_col.setSpacing(2)
        info_col.setContentsMargins(0, 0, 0, 0)
        self._display_lbl = QLabel("—")
        self._display_lbl.setStyleSheet("font-size: 14px; font-weight: bold; color: #e0e0e0;")
        info_col.addWidget(self._display_lbl)
        self._const_lbl = QLabel("")
        self._const_lbl.setStyleSheet(
            "font-family: 'Courier New'; font-size: 10px; color: #777;"
        )
        info_col.addWidget(self._const_lbl)
        rename_btn = QPushButton("Rename Constant…")
        rename_btn.setFixedWidth(140)
        rename_btn.setFixedHeight(22)
        rename_btn.setStyleSheet("font-size: 10px;")
        rename_btn.clicked.connect(
            lambda: self.rename_requested.emit(self._current_const or "")
        )
        info_col.addWidget(rename_btn)
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
        self._tabs.addTab(self._build_dialogue_tab(),  "Dialogue")

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

        self._class_cb = _SearchableConstCombo()
        self._class_cb.currentIndexChanged.connect(self._refresh_header)
        form.addRow("Class:", self._class_cb)

        # Trainer Pic — combo + inline thumbnail
        pic_row = QHBoxLayout()
        self._pic_cb = _SearchableConstCombo()
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

        self._music_cb = _NoScrollCombo()
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
            cb = _SearchableConstCombo()
            cb.setMinimumWidth(200)
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

        # ── Rematch tier selector (hidden for non-rematchable trainers) ──
        self._tier_frame = QFrame()
        self._tier_frame.setStyleSheet(
            "QFrame { background: #1a2a1a; border: 1px solid #2a3a2a; "
            "border-radius: 4px; padding: 4px; }")
        self._tier_frame.setToolTip(
            "VS Seeker rematches let the player re-fight trainers with\n"
            "progressively stronger teams. Each tier is a separate trainer\n"
            "entry gated by a story progression flag.\n\n"
            "Tier gates are defined in vs_seeker.c — the flag names shown\n"
            "here are read directly from your project's source code.\n\n"
            "SKIP means no party upgrade at that stage — the game uses\n"
            "the previous tier's party instead.")
        tier_layout = QVBoxLayout(self._tier_frame)
        tier_layout.setContentsMargins(6, 4, 6, 4)
        tier_layout.setSpacing(4)

        tier_header = QHBoxLayout()
        title_lbl = QLabel("VS Seeker Rematch Tiers")
        title_lbl.setStyleSheet("font-weight: bold; color: #aaffaa;")
        tier_header.addWidget(title_lbl)
        self._tier_settings_btn = QPushButton("Edit Tier Gates…")
        self._tier_settings_btn.setFixedHeight(20)
        self._tier_settings_btn.setStyleSheet("font-size: 10px; padding: 2px 8px;")
        self._tier_settings_btn.setToolTip(
            "Edit which story flags gate each rematch tier,\n"
            "and add or remove tiers.")
        self._tier_settings_btn.clicked.connect(self._on_edit_tier_gates)
        tier_header.addWidget(self._tier_settings_btn)
        self._tier_map_lbl = QLabel("")
        self._tier_map_lbl.setStyleSheet("color: #888; font-size: 10px;")
        tier_header.addStretch()
        tier_header.addWidget(self._tier_map_lbl)
        tier_layout.addLayout(tier_header)

        tier_info = QLabel(
            "Each tier is a separate trainer entry with its own party, "
            "gated by story flags defined in vs_seeker.c. "
            "Select a tier to view/edit that party. "
            "SKIP = no upgrade, uses previous tier's party.")
        tier_info.setStyleSheet("color: #779977; font-size: 10px;")
        tier_info.setWordWrap(True)
        tier_layout.addWidget(tier_info)

        tier_row = QHBoxLayout()
        tier_row.addWidget(QLabel("Battle tier:"))
        self._tier_combo = _NoScrollCombo()
        self._tier_combo.currentIndexChanged.connect(self._on_tier_changed)
        tier_row.addWidget(self._tier_combo, 1)
        tier_layout.addLayout(tier_row)

        # Tier summary line — shows constant + gate flag for selected tier
        self._tier_summary_lbl = QLabel("")
        self._tier_summary_lbl.setStyleSheet("color: #aaa; font-size: 10px;")
        self._tier_summary_lbl.setWordWrap(True)
        tier_layout.addWidget(self._tier_summary_lbl)

        self._tier_frame.setVisible(False)
        layout.addWidget(self._tier_frame)

        # ── "Add to Rematch Table" button (visible when NOT in rematch table) ──
        self._add_rematch_btn = QPushButton("Add to VS Seeker Rematch Table")
        self._add_rematch_btn.setToolTip(
            "Add this trainer to the VS Seeker rematch system.\n"
            "Creates an entry in sRematches[] in vs_seeker.c with\n"
            "empty rematch tiers that you can fill in.")
        self._add_rematch_btn.setStyleSheet(
            "QPushButton { background: #1a2a1a; color: #aaffaa; "
            "border: 1px solid #2a3a2a; border-radius: 4px; "
            "padding: 4px 12px; font-weight: bold; }"
            "QPushButton:hover { background: #2a3a2a; }")
        self._add_rematch_btn.clicked.connect(self._on_add_to_rematch)
        self._add_rematch_btn.setVisible(False)
        layout.addWidget(self._add_rematch_btn)

        # ── Party type selector ──────────────────────────────────────────
        type_row = QHBoxLayout()
        type_row.addWidget(QLabel("Party type:"))
        self._party_type_cb = _NoScrollCombo()
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

        # Internal rematch state
        self._rematch_info: Optional[dict] = None
        self._rematch_tiers: list[str] = []
        self._viewing_tier_idx: int = -1  # which tier's party is currently displayed

        return w

    # ── Dialogue tab ────────────────────────────────────────────────────────────
    def _build_dialogue_tab(self) -> QWidget:
        """Battle dialogue and prize money — reads from/writes to text.inc files."""
        w = QWidget()
        layout = QVBoxLayout(w)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(8)

        # Info label
        info = QLabel(
            "Battle dialogue text for this trainer. These are stored in the\n"
            "map's text.inc file and shown during trainer battles in-game.\n"
            "Edit the text here — it saves back to the correct text.inc on Save."
        )
        info.setStyleSheet("color: #888; font-size: 10px;")
        layout.addWidget(info)

        # Prize money
        money_row = QHBoxLayout()
        money_row.addWidget(QLabel("Prize money base:"))
        self._money_spin = _NoScrollSpin()
        self._money_spin.setRange(0, 255)
        self._money_spin.setToolTip(
            "Base prize money multiplier.\n"
            "Actual payout = this value × last Pokemon's level."
        )
        self._money_spin.valueChanged.connect(lambda: self.changed.emit())
        money_row.addWidget(self._money_spin)
        money_row.addStretch()
        layout.addLayout(money_row)

        sep = QFrame()
        sep.setFrameShape(QFrame.Shape.HLine)
        sep.setFrameShadow(QFrame.Shadow.Sunken)
        layout.addWidget(sep)

        # Scroll area for dialogue sections (one per map the trainer appears on)
        self._dialogue_scroll = QScrollArea()
        self._dialogue_scroll.setWidgetResizable(True)
        self._dialogue_scroll.setFrameShape(QFrame.Shape.NoFrame)
        self._dialogue_container = QWidget()
        self._dialogue_layout = QVBoxLayout(self._dialogue_container)
        self._dialogue_layout.setSpacing(10)
        self._dialogue_layout.setContentsMargins(0, 0, 0, 0)

        # Placeholder shown when no dialogue is found
        self._no_dialogue_label = QLabel(
            "No battle dialogue found for this trainer.\n\n"
            "Dialogue is created when the trainer is wired to a battle script\n"
            "on a map via the Event Editor. Use 'Set up battle script' below\n"
            "to create one, or the text will appear here once the trainer\n"
            "is placed on a map with a trainerbattle command."
        )
        self._no_dialogue_label.setStyleSheet("color: #666; padding: 20px;")
        self._no_dialogue_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._dialogue_layout.addWidget(self._no_dialogue_label)

        self._dialogue_layout.addStretch()
        self._dialogue_scroll.setWidget(self._dialogue_container)
        layout.addWidget(self._dialogue_scroll, 1)

        # "Set up battle script" button
        self._setup_battle_btn = QPushButton("Set up battle script in Event Editor")
        self._setup_battle_btn.setToolTip(
            "Jump to the Event Editor to wire this trainer to an NPC on a map.\n"
            "Creates a trainerbattle_single command with this trainer's constant."
        )
        self._setup_battle_btn.clicked.connect(self.setup_battle_requested.emit)
        layout.addWidget(self._setup_battle_btn)

        # Dictionary to hold text edit widgets keyed by (map, type)
        self._dialogue_edits: dict[tuple[str, str], GameTextEdit] = {}

        return w

    def _populate_dialogue_tab(self, trainer_const: str):
        """Search text.inc files for this trainer's dialogue and display it."""
        # Harvest any edited pending dialogue for the previously-shown trainer
        # so the user's edits aren't lost when switching trainers.
        self._harvest_pending_dialogue()
        # Clear old dialogue widgets
        self._dialogue_edits.clear()
        while self._dialogue_layout.count() > 0:
            item = self._dialogue_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

        self._dialogue_labels = {}

        if not self._project_root or not trainer_const:
            self._no_dialogue_label = QLabel("No project loaded.")
            self._dialogue_layout.addWidget(self._no_dialogue_label)
            self._dialogue_layout.addStretch()
            return

        # Search maps for trainerbattle commands referencing this trainer
        maps_dir = os.path.join(self._project_root, "data", "maps")
        if not os.path.isdir(maps_dir):
            self._no_dialogue_label = QLabel("Maps directory not found.")
            self._dialogue_layout.addWidget(self._no_dialogue_label)
            self._dialogue_layout.addStretch()
            return

        # Get clean name for label matching
        clean_name = trainer_const.replace("TRAINER_", "")
        parts = clean_name.split("_")
        camel_name = "".join(p.capitalize() for p in parts)

        found_any = False

        # Grab Event Editor's live in-RAM state (if any map is open there).
        # The user may have placed this trainer on a map and/or edited its
        # dialogue in the Event Editor's Trainer Battle dialog without
        # saving yet — we need to show those live edits here so the two
        # editors stay in sync.
        live_map, live_scripts_src, live_texts = \
            self._get_live_event_editor_state()

        for map_name in sorted(os.listdir(maps_dir)):
            map_dir = os.path.join(maps_dir, map_name)
            scripts_path = os.path.join(map_dir, "scripts.inc")
            text_path = os.path.join(map_dir, "text.inc")

            if not os.path.isfile(scripts_path):
                continue

            # Prefer live Event Editor state when it's the same map — user
            # may have added the trainer here without saving yet.
            is_live = (map_name == live_map)
            if is_live:
                scripts_content = live_scripts_src
                texts = dict(live_texts)
            else:
                try:
                    with open(scripts_path, "r", encoding="utf-8") as f:
                        scripts_content = f.read()
                except Exception:
                    continue

            if trainer_const not in scripts_content:
                continue

            # This map uses this trainer. Load its text.inc if we didn't
            # already pull texts from the live event editor state.
            if not is_live:
                texts = {}
                if os.path.isfile(text_path):
                    try:
                        from pathlib import Path
                        from eventide.backend.eventide_utils import parse_text_inc
                        texts = dict(parse_text_inc(Path(text_path)))
                    except Exception:
                        pass

            # Find text labels related to this trainer
            map_texts = {}
            for label, content in texts.items():
                label_lower = label.lower()
                camel_lower = camel_name.lower()
                if camel_lower not in label_lower:
                    continue
                if "intro" in label_lower:
                    map_texts["intro"] = (label, content)
                elif "defeat" in label_lower:
                    map_texts["defeat"] = (label, content)
                elif "postbattle" in label_lower or "post" in label_lower:
                    map_texts["post"] = (label, content)

            if not map_texts:
                # Trainer referenced in scripts but no matching text labels found.
                # Try to find text labels from the trainerbattle command arguments.
                map_texts = self._extract_dialogue_from_script(
                    scripts_content, trainer_const, texts)

            if map_texts:
                found_any = True
                self._dialogue_labels[map_name] = map_texts
                display_name = (f"{map_name}  (live — unsaved edits)"
                                if is_live else None)
                self._add_dialogue_group(
                    map_name, map_texts, text_path, display_name)

        # Also show pending dialogue for trainers not yet placed on a map.
        # Stored in RAM only — will migrate to text.inc when the trainer is
        # wired to a trainerbattle command on a map.
        # Once the trainer has been placed (disk or live), drop the pending
        # entry so the user doesn't see two editors for the same dialogue.
        if found_any and trainer_const in self._pending_dialogue:
            self._pending_dialogue.pop(trainer_const, None)
        pending = self._pending_dialogue.get(trainer_const)
        if pending:
            found_any = True
            self._dialogue_labels['__pending__'] = pending
            self._add_dialogue_group(
                '(Pending — not yet placed on a map)', pending, '')

        if not found_any:
            self._no_dialogue_label = QLabel(
                "No battle dialogue found for this trainer.\n\n"
                "This trainer hasn't been placed on any map yet,\n"
                "or the text labels don't follow the standard naming pattern.\n"
                "Dialogue will appear here once the trainer has a\n"
                "trainerbattle command on a map."
            )
            self._no_dialogue_label.setStyleSheet("color: #666; padding: 20px;")
            self._no_dialogue_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
            self._dialogue_layout.addWidget(self._no_dialogue_label)

        self._dialogue_layout.addStretch()

    def _get_live_event_editor_state(self) -> tuple[str, str, dict]:
        """Peek at the Event Editor's in-RAM scripts and texts for the
        currently-open map.

        Returns ``(map_name, synthetic_scripts_content, texts_dict)``.
        ``map_name`` matches a folder name under ``data/maps``. If the user
        has placed a trainer on that map via the Event Editor (without
        saving yet), the synthetic scripts content will contain the
        trainerbattle command so this tab can discover it just like it
        would from disk. Returns ``('', '', {})`` if no map is loaded.
        """
        try:
            from eventide.ui.event_editor_tab import _ALL_SCRIPTS
        except Exception:
            return ('', '', {})

        live_map = _ALL_SCRIPTS.get('__texts_map__') or ''
        live_texts = _ALL_SCRIPTS.get('__texts__') or {}
        if not live_map:
            return ('', '', {})

        # Flatten live scripts dict into a pseudo scripts.inc format.
        # We only need the trainerbattle lines + their args to be
        # recognizable to _extract_dialogue_from_script and the
        # "trainer_const in scripts_content" substring check.
        lines = []
        for label, cmds in _ALL_SCRIPTS.items():
            if label.startswith('__') or not isinstance(cmds, list):
                continue
            lines.append(f'{label}:')
            for cmd in cmds:
                if not cmd or not isinstance(cmd, tuple):
                    continue
                name = cmd[0]
                rest = cmd[1:]
                if rest:
                    args = ', '.join(str(a) for a in rest)
                    lines.append(f'\t{name} {args}')
                else:
                    lines.append(f'\t{name}')
        return (live_map, '\n'.join(lines), dict(live_texts))

    def _extract_dialogue_from_script(self, scripts_content: str,
                                       trainer_const: str,
                                       texts: dict) -> dict:
        """Parse trainerbattle command args to find text label references."""
        result = {}
        for line in scripts_content.splitlines():
            stripped = line.strip()
            if not stripped.startswith("trainerbattle") or trainer_const not in stripped:
                continue

            # Parse: trainerbattle_single TRAINER_X, IntroLabel, DefeatLabel [, ContinueLabel]
            # or:    trainerbattle_no_intro TRAINER_X, DefeatLabel
            parts_after_cmd = stripped.split(None, 1)
            if len(parts_after_cmd) < 2:
                continue
            cmd = parts_after_cmd[0]
            args = [a.strip() for a in parts_after_cmd[1].split(",")]

            if "no_intro" in cmd and len(args) >= 2:
                defeat_label = args[1]
                if defeat_label in texts:
                    result["defeat"] = (defeat_label, texts[defeat_label])
            elif len(args) >= 3:
                intro_label = args[1]
                defeat_label = args[2]
                if intro_label in texts:
                    result["intro"] = (intro_label, texts[intro_label])
                if defeat_label in texts:
                    result["defeat"] = (defeat_label, texts[defeat_label])
            break

        return result

    def _add_dialogue_group(self, map_name: str, map_texts: dict, text_path: str,
                            display_name: str | None = None):
        """Add a group box for one map's dialogue to the dialogue tab.

        ``map_name`` is the canonical key used for _dialogue_edits lookups
        (must match what's in _dialogue_labels so save can find it).
        ``display_name`` is the human-friendly title shown in the group
        header — defaults to ``map_name`` when not given.
        """
        shown = display_name if display_name is not None else map_name
        group = QGroupBox(f"Map: {shown}")
        group.setStyleSheet("QGroupBox { font-weight: bold; }")
        group_layout = QVBoxLayout(group)
        group_layout.setSpacing(6)

        # Store text_path so we can write back on save
        group.setProperty("text_path", text_path)

        type_labels = {
            "intro": "Intro (before battle):",
            "defeat": "Defeat (trainer loses):",
            "post": "Post-battle (talk after winning):",
        }

        # Max lines — generous limit; GBA trainer text can be longer
        # than expected (Sabrina has 9+ display lines with page breaks)
        type_max_lines = {
            "intro": 20,
            "defeat": 20,
            "post": 20,
        }

        for text_type in ("intro", "defeat", "post"):
            if text_type not in map_texts:
                continue

            label_name, content = map_texts[text_type]

            header = QHBoxLayout()
            header.addWidget(QLabel(type_labels.get(text_type, text_type)))
            label_tag = QLabel(f"[{label_name}]")
            label_tag.setStyleSheet("color: #777; font-size: 10px;")
            header.addWidget(label_tag)
            header.addStretch()
            group_layout.addLayout(header)

            edit = GameTextEdit(
                max_chars_per_line=36,
                max_lines=type_max_lines.get(text_type, 8),
            )
            edit.set_inc_text(content or "")
            edit.setMaximumHeight(100)
            edit.setPlaceholderText(f"(empty {text_type} text)")
            edit.connectChanged(lambda: self.changed.emit())
            group_layout.addWidget(edit)

            # Track this edit widget so we can collect the text on save
            self._dialogue_edits[(map_name, text_type)] = edit

        self._dialogue_layout.addWidget(group)

    def _harvest_pending_dialogue(self) -> None:
        """Read edited text from the pending dialogue widgets and store it
        back into self._pending_dialogue so edits survive trainer switches."""
        if not self._current_const:
            return
        pending = self._pending_dialogue.get(self._current_const)
        if not pending:
            return
        pending_map_key = '(Pending — not yet placed on a map)'
        for (map_name, text_type), edit in list(self._dialogue_edits.items()):
            if map_name != pending_map_key:
                continue
            if text_type in pending:
                label = pending[text_type][0]
                pending[text_type] = (label, edit.get_inc_text())

    def set_pending_dialogue(self, trainer_const: str,
                             intro_text: str, defeat_text: str,
                             post_text: str) -> None:
        """Create an in-RAM pending dialogue entry for a newly-added trainer.

        The labels follow the standard map_text naming pattern and will be
        picked up automatically when the trainer gets placed on a map.
        """
        clean = trainer_const.replace('TRAINER_', '')
        parts = clean.split('_')
        camel = ''.join(p.capitalize() for p in parts)
        self._pending_dialogue[trainer_const] = {
            'intro':  (f'Text_{camel}_Intro', intro_text),
            'defeat': (f'Text_{camel}_Defeat', defeat_text),
            'post':   (f'Text_{camel}_PostBattle', post_text),
        }

    def clear_pending_dialogue(self, trainer_const: str) -> None:
        """Drop the pending entry once the trainer is placed on a real map."""
        self._pending_dialogue.pop(trainer_const, None)

    def collect_dialogue(self) -> dict:
        """Collect edited dialogue text. Returns {(map, type): (label, new_text)}."""
        # Keep pending dialogue in RAM in sync with the editor widgets so
        # edits don't get lost at save-time or when switching trainers.
        self._harvest_pending_dialogue()
        result = {}
        for (map_name, text_type), edit in self._dialogue_edits.items():
            if map_name in self._dialogue_labels:
                map_texts = self._dialogue_labels[map_name]
                if text_type in map_texts:
                    label_name = map_texts[text_type][0]
                    result[(map_name, text_type)] = (label_name, edit.get_inc_text())
        return result

    # ── Rematch tier support (integrated into Party tab) ────────────────────

    def _populate_party_rematch_info(self, trainer_const: str):
        """Update the tier dropdown in the Party tab for this trainer."""
        rematch_map = getattr(self, '_rematch_map_data', {})
        info = rematch_map.get(trainer_const)
        self._rematch_info = info

        has_rematches = info is not None
        self._tier_frame.setVisible(has_rematches)
        # Show "Add to Rematch Table" button when trainer is NOT in the table
        self._add_rematch_btn.setVisible(not has_rematches)

        if not has_rematches:
            self._rematch_tiers = []
            self._viewing_tier_idx = -1
            return

        tiers = info["tiers"]
        self._rematch_tiers = tiers
        map_name = info["map"].replace("MAP_", "").replace("_", " ").title()
        self._tier_map_lbl.setText(f"Map: {map_name}")

        # Populate tier dropdown with summaries
        self._tier_combo.blockSignals(True)
        self._tier_combo.clear()
        all_trainers = getattr(self, '_all_trainers_data', {})
        all_parties = getattr(self, '_all_parties_data', {})
        for i, const in enumerate(tiers):
            tier_name = self._tier_labels[i] if i < len(self._tier_labels) else f"Tier {i}"
            if not const or const == _SKIP or const == "":
                self._tier_combo.addItem(f"{tier_name}  —  (same as previous tier)", i)
            else:
                summary = self._tier_party_summary(const, all_trainers, all_parties)
                self._tier_combo.addItem(f"{tier_name}  —  {summary}", i)
        self._tier_combo.setCurrentIndex(0)
        self._tier_combo.blockSignals(False)
        self._viewing_tier_idx = 0
        self._update_tier_summary(0)

    def _tier_party_summary(self, trainer_const: str,
                            all_trainers: dict, all_parties: dict) -> str:
        """Build a compact party summary string like 'Raticate L48, Arbok L48'."""
        trainer = all_trainers.get(trainer_const, {})
        party_macro = trainer.get("party", "")
        party_sym = _extract_party_symbol(party_macro)
        party = all_parties.get(party_sym) if party_sym else None
        if not party:
            return "(no party data)"
        members = party.get("members", [])
        if not members:
            return "(empty party)"
        parts = []
        for m in members:
            species = m.get("species", "???").replace("SPECIES_", "")
            species = species.replace("_", " ").title()
            lvl = m.get("lvl", "?")
            parts.append(f"{species} L{lvl}")
        return ", ".join(parts)

    def _update_tier_summary(self, index: int):
        """Update the summary label below the tier dropdown."""
        if not self._rematch_tiers or index < 0 or index >= len(self._rematch_tiers):
            self._tier_summary_lbl.setText("")
            return
        const = self._rematch_tiers[index]
        gate = self._tier_labels[index] if index < len(self._tier_labels) else "?"
        flag = self._gate_flags[index] if index < len(self._gate_flags) else ""
        if not const or const == _SKIP or const == "":
            self._tier_summary_lbl.setText(
                f"SKIP — no party upgrade at this stage. Uses the previous tier's party."
                + (f"  (Gate: {flag})" if flag else ""))
        else:
            self._tier_summary_lbl.setText(
                f"{const}  ·  Gate: {gate}"
                + (f"  ({flag})" if flag else ""))

    def _flush_viewing_tier_party(self) -> None:
        """Push the currently-displayed tier's party from slot widgets into
        the shared parties dict BEFORE we clear the slots to load a different
        tier. Without this, switching tiers away and back silently discards
        the user's edits — the slot widgets get deleted and nothing has yet
        written their state to ``self._parties`` (which normally only happens
        on trainer-list switch, via ``TrainersTabWidget._flush_current``).

        Handles tier 0 (base party) and tiers >0 (rematch variants) the same
        way — just computes a different sParty symbol. Does nothing during
        initial load or when no tier is currently being viewed.
        """
        if self._loading:
            return
        if not self._rematch_tiers:
            return
        tier_idx = getattr(self, "_viewing_tier_idx", -1)
        if tier_idx < 0 or tier_idx >= len(self._rematch_tiers):
            return
        tier_const = self._rematch_tiers[tier_idx]
        if not tier_const or tier_const == _SKIP or tier_const == "":
            return

        # Tier 0 writes to the base trainer's sParty symbol; tier >0 writes
        # to the variant's (e.g. sParty_BirdKeeperBennyR3). Both paths just
        # use the tier const itself — _trainer_const_to_party_symbol converts
        # TRAINER_X → X's camel form.
        tier_sym = f"sParty_{_trainer_const_to_party_symbol(tier_const)}"
        ptype = self._party_type_cb.currentData() or "NO_ITEM_DEFAULT_MOVES"
        members = [slot.collect() for slot in self._party_slots]
        party_update = {"type": ptype, "members": members}

        # Short-circuit if nothing actually changed. Keeps save-path noise
        # down and prevents flipping dirty flags on incidental tier switches.
        old = self._all_parties_data.get(tier_sym)
        if old == party_update:
            return

        # _all_parties_data is the SAME dict reference as the parent's
        # self._parties — writing here updates both. Parent still needs to
        # queue a pending .c write via the signal below.
        self._all_parties_data[tier_sym] = party_update
        self.tier_party_modified.emit(tier_sym, party_update)

        # Refresh the dropdown's label for the tier we just flushed so the
        # "Rematch 3 — Fearow L50" summary reflects the new mons. Without
        # this, switching tiers away and back shows the new party in slots
        # but the dropdown still advertises the OLD species.
        self._refresh_tier_combo_item(tier_idx)

    def _refresh_tier_combo_item(self, tier_idx: int) -> None:
        """Rebuild the display text of a single tier combo entry so the
        party summary shown in the dropdown matches the current party data.
        Called after a flush writes edited slot widgets back to the shared
        parties dict. blockSignals prevents re-entrancy into _on_tier_changed.
        """
        if tier_idx < 0 or tier_idx >= self._tier_combo.count():
            return
        if tier_idx >= len(self._rematch_tiers):
            return
        const = self._rematch_tiers[tier_idx]
        tier_name = (self._tier_labels[tier_idx]
                     if tier_idx < len(self._tier_labels)
                     else f"Tier {tier_idx}")
        if not const or const == _SKIP or const == "":
            new_text = f"{tier_name}  —  (same as previous tier)"
        else:
            all_trainers = getattr(self, '_all_trainers_data', {})
            all_parties = getattr(self, '_all_parties_data', {})
            summary = self._tier_party_summary(const, all_trainers, all_parties)
            new_text = f"{tier_name}  —  {summary}"
        self._tier_combo.blockSignals(True)
        self._tier_combo.setItemText(tier_idx, new_text)
        self._tier_combo.blockSignals(False)

    def _on_tier_changed(self, index: int):
        """Switch the party display to show the selected rematch tier's party."""
        if self._loading:
            return
        if not self._rematch_tiers or index < 0 or index >= len(self._rematch_tiers):
            return

        # Save the outgoing tier's party BEFORE we clear its slot widgets.
        # _viewing_tier_idx still points to the tier the user is leaving.
        self._flush_viewing_tier_party()

        self._update_tier_summary(index)
        const = self._rematch_tiers[index]

        if not const or const == _SKIP or const == "":
            # Empty tier — clear party display
            self._clear_party_slots()
            self._update_party_count()
            self._viewing_tier_idx = index
            return

        # Load the tier trainer's party into the party slots
        all_trainers = getattr(self, '_all_trainers_data', {})
        all_parties = getattr(self, '_all_parties_data', {})
        trainer = all_trainers.get(const, {})
        party_sym = _extract_party_symbol(trainer.get("party", ""))
        party = all_parties.get(party_sym) if party_sym else None

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
        self._viewing_tier_idx = index

    def _on_edit_tier_gates(self):
        """Open the rematch settings dialog."""
        self.edit_tier_gates_requested.emit()

    def _on_add_to_rematch(self):
        """Request adding the current trainer to the VS Seeker rematch table."""
        if self._current_const:
            self.add_to_rematch_requested.emit(self._current_const)

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
                    # Also update the large header sprite. Scale target is
                    # clamped to the label's actual fixed size (64x64) so the
                    # pixmap can't overflow and get clipped — earlier code
                    # scaled to 80x100 and the clipped result read as empty
                    # space next to the header text for some trainers.
                    self._sprite_lbl.setPixmap(
                        pix.scaled(64, 64,
                                   Qt.AspectRatioMode.KeepAspectRatio,
                                   Qt.TransformationMode.SmoothTransformation)
                    )
                    if not self._loading:
                        self.changed.emit()
                    return
        # Fallback: clear BOTH the thumb and the header sprite. If only
        # _sprite_lbl were cleared, the thumb would keep the previous
        # trainer's sprite visible and users would think this trainer has
        # the wrong pic assigned when really the pic-map lookup failed.
        self._pic_thumb.clear()
        self._sprite_lbl.clear()
        self._sprite_lbl.setText("?")
        if not self._loading:
            self.changed.emit()

    def _load_sprite(self, pic_const: str):
        if pic_const and pic_const in self._pic_map:
            path = self._pic_map[pic_const]
            if os.path.isfile(path):
                pix = QPixmap(path)
                if not pix.isNull():
                    # Match _sprite_lbl's 64x64 fixed size — scaling larger
                    # (e.g. 80x100) overflows and gets clipped, which reads
                    # as an empty black box to the user.
                    self._sprite_lbl.setPixmap(
                        pix.scaled(64, 64,
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
        self._loading = True
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
        # _SearchableConstCombo.set_const: unknown consts get appended as
        # items so currentData() always returns the loaded value — no
        # currentText() fallback needed in collect().
        self._class_cb.set_const(cls_const)

        if not self._pic_cb.count():
            self._populate_pic_combo()
        self._pic_cb.set_const(trainer.get("trainerPic", ""))
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
            item_val = bag_items[i] if i < len(bag_items) else "ITEM_NONE"
            cb.set_const(item_val)

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

        # Dialogue tab — search text.inc files for this trainer's battle text
        self._populate_dialogue_tab(const)

        # Rematch tiers — populate tier dropdown in Party tab
        self._populate_party_rematch_info(const)

        # Prize money — stored in trainer class definitions (not per-trainer in firered)
        # For now, just show the setting default; per-trainer money comes with Phase 3
        try:
            from PyQt6.QtCore import QSettings as _QS
            from app_info import get_settings_path as _gsp
            _s = _QS(_gsp(), _QS.Format.IniFormat)
            self._money_spin.setValue(
                _s.value("trainer_defaults/money_multiplier", 20, type=int))
        except Exception:
            self._money_spin.setValue(20)
        self._loading = False

    def _add_slot_with_data(self, ptype: str, member: dict):
        slot = _PartySlotWidget(self._species_list, self._items_list, self._moves_list,
                                icon_fn=self._species_icon_fn, parent=self)
        slot.set_party_type(ptype)
        slot.load(member)
        slot.changed.connect(lambda: self.changed.emit())
        slot.remove_requested.connect(self._remove_party_slot)
        self._party_slots.append(slot)
        self._slots_layout.insertWidget(self._slots_layout.count() - 1, slot)

    def collect(self) -> tuple[dict, dict, Optional[str]]:
        """Return (trainer_dict_updates, party_dict, tier_party_symbol_override).

        * When the panel is viewing a rematch *tier* rather than the base
          trainer, the party slots belong to the tier variant. In that case
          the third return value is the tier's sParty symbol so the caller
          can write the party dict under the correct symbol, and the "party"
          field is OMITTED from trainer_updates (so the base trainer's party
          pointer is not clobbered with the tier's symbol).
        * When viewing the base trainer, the third return value is None.
        """
        trainer: dict = {}
        trainer["trainerName"] = f'_("{self._name_edit.text()}")'
        # currentData() is authoritative — _SearchableConstCombo snap-back +
        # set_const on load guarantee it returns a real constant, never None
        # and never free-typed text.
        trainer["trainerClass"] = self._class_cb.currentData() or ""
        trainer["trainerPic"]   = self._pic_cb.currentData() or ""
        music_val = self._music_cb.currentData() or self._music_cb.currentText()
        if getattr(self, "_has_female_flag", False):
            music_val = music_val + " | F_TRAINER_FEMALE"
        trainer["encounterMusic_gender"] = music_val
        trainer["doubleBattle"] = "TRUE" if self._double_cb.isChecked() else "FALSE"

        active_ai = [c for c, cb in self._ai_checks.items() if cb.isChecked()]
        trainer["aiFlags"] = " | ".join(active_ai) if active_ai else "0"

        bag: list[str] = []
        for cb in self._bag_cbs:
            v = cb.currentData() or ""
            if v and v not in ("ITEM_NONE", "0", ""):
                bag.append(v)
        trainer["items"] = "{" + ", ".join(bag) + "}" if bag else "{}"

        ptype = self._party_type_cb.currentData() or "NO_ITEM_DEFAULT_MOVES"

        # Rematch-tier aliasing: if the user opened the Party-tab tier dropdown
        # and is now editing tier 2's party, the slots below belong to the
        # TIER variant trainer — NOT the base. Writing the party to the base
        # trainer's sParty symbol would corrupt the base party with the tier's
        # team. Detect tier-view and route the party write to the tier.
        tier_sym_override: Optional[str] = None
        rematch_tiers = getattr(self, "_rematch_tiers", None) or []
        viewing_tier = getattr(self, "_viewing_tier_idx", 0)
        if rematch_tiers and 0 < viewing_tier < len(rematch_tiers):
            tier_const = rematch_tiers[viewing_tier]
            if tier_const and tier_const != _SKIP:
                # Don't let trainer_updates overwrite the base trainer's
                # "party" field — it must still point to the base sParty.
                # The identity/AI/bag fields have not been re-loaded for the
                # tier variant, so they don't apply either. Strip every field
                # except the ones that belong to the base trainer, which is
                # nothing meaningful while a tier is being viewed — return
                # just an empty trainer update + the tier's party dict.
                tier_sym_override = (
                    f"sParty_{_trainer_const_to_party_symbol(tier_const)}"
                )
                # The tier view only re-renders the party slots, not identity/
                # AI/bag — so we must NOT write those back to the base. Replace
                # the trainer dict with just the already-loaded base values (a
                # no-op update) plus leave "party" alone.
                trainer = {}
        else:
            party_symbol = f"sParty_{_trainer_const_to_party_symbol(self._current_const or '')}"
            trainer["party"] = f"{ptype}({party_symbol})"

        members = [slot.collect() for slot in self._party_slots]
        party   = {"type": ptype, "members": members}
        return trainer, party, tier_sym_override


# ══════════════════════════════════════════════════════════════════════════════
# Add Trainer dialog
# ══════════════════════════════════════════════════════════════════════════════

class _AddTrainerDialog(QDialog):
    """Dialog for creating a new trainer — pick a class, enter a name.

    The class dropdown shows all available trainer classes sorted by
    display name.  When the user picks a class and enters a name, the
    caller uses the class's template trainer (the blank-named entry for
    that class) to pre-fill encounter music, trainer pic, AI flags, etc.
    """

    def __init__(
        self,
        class_names: dict[str, str],
        existing_trainers: dict,
        parent=None,
    ):
        super().__init__(parent)
        self.setWindowTitle("Add Trainer")
        self.setMinimumWidth(400)

        layout = QVBoxLayout(self)
        layout.setSpacing(12)

        # Trainer class dropdown
        form = QFormLayout()
        form.setSpacing(8)

        self._class_cb = _NoScrollCombo()
        self._class_cb.setEditable(False)
        self._class_cb.setMaxVisibleItems(20)
        # Sort by display name, show "DISPLAY NAME  (CONSTANT)"
        sorted_classes = sorted(class_names.items(), key=lambda kv: kv[1])
        for const, display in sorted_classes:
            self._class_cb.addItem(f"{display}  ({const})", const)
        form.addRow("Class:", self._class_cb)

        # Trainer name field
        self._name_edit = QLineEdit()
        self._name_edit.setPlaceholderText("e.g. BOB, LISA, MARCOS")
        self._name_edit.setMaxLength(10)  # GBA trainer names max ~10 chars
        form.addRow("Name:", self._name_edit)

        # Preview of the constant that will be created
        self._preview_lbl = QLabel()
        self._preview_lbl.setStyleSheet("color: #888; font-size: 10px;")
        form.addRow("Constant:", self._preview_lbl)

        layout.addLayout(form)

        # Info text
        info = QLabel(
            "The new trainer will inherit the default encounter music,\n"
            "trainer pic, and AI flags from the class template.\n"
            "You can change everything after creation."
        )
        info.setStyleSheet("color: #666; font-size: 10px;")
        layout.addWidget(info)

        # Buttons
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok
            | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self._validate_and_accept)
        buttons.rejected.connect(self.reject)
        self._ok_btn = buttons.button(QDialogButtonBox.StandardButton.Ok)
        self._ok_btn.setEnabled(False)
        layout.addWidget(buttons)

        # Wire up preview updates
        self._class_cb.currentIndexChanged.connect(self._update_preview)
        self._name_edit.textChanged.connect(self._update_preview)
        self._existing = existing_trainers
        self._update_preview()

    def _update_preview(self):
        cls_const = self._class_cb.currentData()
        name = self._name_edit.text().strip().upper().replace(" ", "_")
        if cls_const and name:
            cls_suffix = cls_const.replace("TRAINER_CLASS_", "")
            const = f"TRAINER_{cls_suffix}_{name}"
            if const in self._existing:
                self._preview_lbl.setText(
                    f'<span style="color:#e57373">{const} (already exists!)</span>'
                )
                self._ok_btn.setEnabled(False)
            else:
                self._preview_lbl.setText(const)
                self._ok_btn.setEnabled(True)
        else:
            self._preview_lbl.setText("(enter a name)")
            self._ok_btn.setEnabled(False)

    def _validate_and_accept(self):
        name = self._name_edit.text().strip()
        if not name:
            QMessageBox.warning(self, "Name Required", "Please enter a trainer name.")
            return
        cls_const = self._class_cb.currentData()
        if not cls_const:
            QMessageBox.warning(self, "Class Required", "Please select a trainer class.")
            return
        # Check for duplicate one more time
        cls_suffix = cls_const.replace("TRAINER_CLASS_", "")
        const = f"TRAINER_{cls_suffix}_{name.upper().replace(' ', '_')}"
        if const in self._existing:
            QMessageBox.warning(self, "Duplicate", f"{const} already exists.")
            return
        self.accept()

    def selected_class(self) -> str:
        """Return the selected TRAINER_CLASS_* constant."""
        return self._class_cb.currentData() or ""

    def trainer_name(self) -> str:
        """Return the entered name (stripped)."""
        return self._name_edit.text().strip()


# ══════════════════════════════════════════════════════════════════════════════
# Main tab widget
# ══════════════════════════════════════════════════════════════════════════════

class TrainersTabWidget(QWidget):
    """Full trainer editor — searchable list on left, detail panel on right."""

    changed          = pyqtSignal()
    rename_requested = pyqtSignal(str)   # old_const → mainwindow drives RefactorService
    # Phase 3: jump to Event Editor with this trainer pre-selected
    setup_battle_requested = pyqtSignal(str)  # trainer constant

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
        # Dirty-tracking set — survives _rebuild_list() (search filter), which
        # wipes every QListWidgetItem and recreates them fresh. Without an
        # external backing store, edited-but-unsaved amber tints would vanish
        # the moment the user types into the search box. Keyed by TRAINER_*.
        self._dirty_consts: set[str] = set()
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

        # Parse VS Seeker rematch table and tier gate flags
        rematch_entries, self._rematch_raw_lines = _parse_rematch_table(project_root)
        self._rematch_entries = rematch_entries
        self._rematch_base_map, self._rematch_any_map, self._rematch_variants = \
            _build_rematch_map(rematch_entries)
        gate_flags = _parse_tier_gate_flags(project_root)
        self._gate_flags = gate_flags
        tier_labels = _build_tier_labels(gate_flags)

        # Reset current selection — the old panel is being replaced, so
        # _flush_current must not collect stale data from the new empty panel.
        self._current_const = None
        # Reload wipes amber dirty tints — the incoming data is the new
        # baseline, so any previous "edited but unsaved" markers no longer
        # apply to the freshly-loaded rows.
        self._dirty_consts.clear()

        # Build a fresh detail panel with the new lists
        self._detail_panel = _TrainerDetailPanel(
            self._class_names,
            self._pic_map,
            list(self._pic_map.keys()),
            self._species_list,
            self._items_list,
            self._moves_list,
            species_icon_fn=species_icon_fn,
            project_root=project_root,
            rematch_map=self._rematch_any_map,
            all_trainers=self._trainers,
            all_parties=self._parties,
            tier_labels=tier_labels,
            gate_flags=gate_flags,
            parent=self,
        )
        self._detail_panel.changed.connect(lambda: self.changed.emit())
        self._detail_panel.rename_requested.connect(self.rename_requested.emit)
        self._detail_panel.setup_battle_requested.connect(self._on_setup_battle)
        self._detail_panel.edit_tier_gates_requested.connect(self._on_edit_tier_gates)
        self._detail_panel.add_to_rematch_requested.connect(self._on_add_to_rematch)
        # Tier-switch flushes need a pending .c write queued on the parent so
        # Save picks them up even when the user never leaves this trainer.
        # _parties is already updated by the panel; we just queue the
        # write-string and mark the base-trainer row dirty.
        self._detail_panel.tier_party_modified.connect(self._on_tier_party_modified)
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

    # ── dirty-tracking API (called from MainWindow) ───────────────────────────
    def current_const(self) -> Optional[str]:
        """Which trainer constant is currently open in the detail panel."""
        return self._current_const

    def mark_dirty(self, const: str) -> None:
        """Paint the given trainer's row amber (edited-but-unsaved).

        Writes to the external `_dirty_consts` set AND to the current
        QListWidgetItem's DIRTY role so the delegate repaints immediately.
        The external set lets `_rebuild_list` re-assert the tint after a
        search filter wipes all items.
        """
        if not const:
            return
        self._dirty_consts.add(const)
        for i in range(self._list.count()):
            item = self._list.item(i)
            if item and item.data(Qt.ItemDataRole.UserRole) == const:
                item.setData(_TrainerListDelegate._DIRTY_ROLE, True)
                break

    def clear_all_dirty(self) -> None:
        """Remove amber tint from every trainer row (post-save)."""
        self._dirty_consts.clear()
        for i in range(self._list.count()):
            item = self._list.item(i)
            if item is not None:
                item.setData(_TrainerListDelegate._DIRTY_ROLE, None)

    def clear_pending_party_writes(self):
        self._pending_party_writes.clear()

    def _on_tier_party_modified(self, party_sym: str, party_update: dict):
        """Panel flushed a tier's party edits just before switching tiers.
        Queue the .c write-string so Save picks it up, and mark the base
        trainer's row dirty (the tier variant is keyed under the base in
        the list UI, not its own row)."""
        if not party_sym or not party_update:
            return
        self._pending_party_writes[party_sym] = _generate_party_c(
            party_sym, party_update["type"], party_update["members"]
        )
        if self._current_const:
            self.mark_dirty(self._current_const)
        # Propagate so the mainwindow sidebar dot + title-bar asterisk light
        # up for this edit just like every other edit.
        self.changed.emit()

    def save_dialogue_edits(self) -> bool:
        """Write edited dialogue text back to the correct text.inc files.

        Returns True if any files were modified.
        """
        if not self._detail_panel:
            return False

        dialogue_data = self._detail_panel.collect_dialogue()
        if not dialogue_data:
            return False

        # Group edits by text.inc file path
        from collections import defaultdict as _dd
        edits_by_file: dict[str, dict[str, str]] = _dd(dict)

        for (map_name, text_type), (label, new_text) in dialogue_data.items():
            text_path = os.path.join(
                self._project_root, "data", "maps", map_name, "text.inc")
            edits_by_file[text_path][label] = new_text

        modified = False
        for text_path, label_updates in edits_by_file.items():
            if not os.path.isfile(text_path):
                continue
            try:
                from pathlib import Path
                from eventide.backend.eventide_utils import parse_text_inc, write_text_inc
                texts = parse_text_inc(Path(text_path))
                changed = False
                for label, new_text in label_updates.items():
                    if label in texts and texts[label] != new_text:
                        texts[label] = new_text
                        changed = True
                if changed:
                    write_text_inc(texts, Path(text_path))
                    modified = True
            except Exception as exc:
                log.warning("save_dialogue_edits write %s: %s", text_path, exc)

        return modified

    def show_script_warnings(self, const: str):
        """Scan script files for references to const and display warning."""
        refs = _find_script_refs(self._project_root, const)
        if refs:
            self._show_warn(
                f"⚠ The following script files still reference {const} and need manual updates:\n"
                + "\n".join(f"  • {r}" for r in refs[:25])
                + ("\n  …and more." if len(refs) > 25 else "")
            )

    def apply_class_name(self, const: str, new_name: str) -> None:
        """Live-apply a trainer-class display name rename from the sibling
        Trainer Class editor. Updates in-memory mapping and refreshes
        visible widgets — no disk write, no save required."""
        if not const:
            return
        self._class_names[const] = new_name
        # Push into the detail panel too (it holds its own reference — the
        # initial load passed the dict in by reference, but defensively
        # update in place to ensure both match).
        panel = getattr(self, "_detail_panel", None)
        if panel is not None and hasattr(panel, "_class_names"):
            panel._class_names[const] = new_name
            # Refresh the class combobox so the new name shows in the dropdown
            try:
                panel._populate_class_combo()
            except Exception:
                pass
            # Refresh the header label if this class is currently displayed
            try:
                panel._refresh_header()
            except Exception:
                pass
        # Rebuild the list so class-grouping labels and display names update
        try:
            self._rebuild_list()
        except Exception:
            pass

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
        rematch_variants = getattr(self, '_rematch_variants', set())
        for const in self._order:
            if const not in self._trainers or const == "TRAINER_NONE":
                continue
            # Hide rematch variant constants — they're accessible via
            # the tier dropdown in the base trainer's Party tab
            if const in rematch_variants:
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
                # Re-apply dirty tint if this trainer has unsaved edits.
                # _rebuild_list wipes and recreates all items (search filter,
                # rename, etc.), so amber must be re-asserted from the
                # external _dirty_consts set — the QListWidgetItem itself
                # doesn't persist across rebuilds.
                if const in self._dirty_consts:
                    item.setData(_TrainerListDelegate._DIRTY_ROLE, True)
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
            trainer_updates, party_update, tier_sym_override = (
                self._detail_panel.collect()
            )
        except Exception as exc:
            log.warning("_flush_current collect: %s", exc)
            return

        existing = self._trainers.get(self._current_const, {})

        if tier_sym_override:
            # We're viewing a rematch tier — the identity/AI/bag have NOT
            # been re-rendered for the tier, so trainer_updates is empty.
            # Only the party slots belong to the tier variant. Route the
            # party dict to the tier's sParty symbol so editing tier 2's
            # team doesn't overwrite the base trainer's party.
            party_sym = tier_sym_override
        else:
            # Guard: if collect() returned empty critical fields, the panel
            # wasn't properly loaded (combos not populated yet). Don't
            # overwrite real trainer data with blanks. Merge field-by-field
            # instead of dropping the entire update — a single empty field
            # must not erase others.
            merged_updates: dict = {}
            for k, v in trainer_updates.items():
                if v in ("", None) and existing.get(k):
                    # Skip blank overwrite — existing has a real value.
                    continue
                merged_updates[k] = v
            existing.update(merged_updates)
            self._trainers[self._current_const] = existing
            party_sym = _extract_party_symbol(existing.get("party", ""))

        # Track party dirtiness
        if party_sym:
            old = self._parties.get(party_sym)
            if old != party_update:
                self._parties[party_sym] = party_update
                self._pending_party_writes[party_sym] = _generate_party_c(
                    party_sym, party_update["type"], party_update["members"]
                )

    def _add_trainer(self):
        """Open the Add Trainer dialog — pick a class, enter a name, get defaults."""
        dlg = _AddTrainerDialog(
            self._class_names,
            self._trainers,
            parent=self,
        )
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return

        cls_const = dlg.selected_class()
        name = dlg.trainer_name()

        if not cls_const or not name:
            return

        # Build the constant: TRAINER_HIKER_BOB
        cls_suffix = cls_const.replace("TRAINER_CLASS_", "")
        name_upper = name.strip().upper().replace(" ", "_")
        const = f"TRAINER_{cls_suffix}_{name_upper}"

        if const in self._trainers:
            QMessageBox.warning(self, "Duplicate", f"{const} already exists.")
            return

        # Find a template trainer for this class (empty-name entry with matching class)
        template = self._find_class_template(cls_const)

        next_id = self._next_trainer_id()
        party_sym = f"sParty_{_trainer_const_to_party_symbol(const)}"

        self._trainers[const] = {
            "trainerClass":          cls_const,
            "encounterMusic_gender": template.get("encounterMusic_gender",
                                                   "TRAINER_ENCOUNTER_MUSIC_MALE"),
            "trainerPic":            template.get("trainerPic",
                                                   next(iter(self._pic_map.keys()), "")),
            "trainerName":           f'_("{name}")',
            "items":                 template.get("items", "{}"),
            "doubleBattle":          template.get("doubleBattle", "FALSE"),
            "aiFlags":               template.get("aiFlags", "AI_SCRIPT_CHECK_BAD_MOVE"),
            "party":                 f"NO_ITEM_DEFAULT_MOVES({party_sym})",
        }
        self._parties[party_sym] = {"type": "NO_ITEM_DEFAULT_MOVES", "members": []}
        self._order.append(const)
        self._pending_party_writes[party_sym] = _generate_party_c(
            party_sym, "NO_ITEM_DEFAULT_MOVES", []
        )

        # Live-refresh: make the new trainer immediately available to any
        # ConstantPicker (e.g. Event Editor's Trainer Battle dialog) without
        # requiring a full Save + project reload.
        try:
            from eventide.backend.constants_manager import ConstantsManager
            if const not in ConstantsManager.TRAINERS:
                ConstantsManager.TRAINERS.append(const)
        except Exception:
            pass  # ConstantsManager not available — ignore silently

        # Seed default battle dialogue in RAM using the Settings templates.
        # Must happen BEFORE _rebuild_list() — otherwise setCurrentRow fires
        # _populate_dialogue_tab before the pending entry exists, leaving
        # the Dialogue tab showing "No battle dialogue found" until the user
        # switches trainers and comes back.
        try:
            from PyQt6.QtCore import QSettings as _QS
            from app_info import get_settings_path as _gsp
            _s = _QS(_gsp(), _QS.Format.IniFormat)
            intro_text = _s.value(
                'trainer_defaults/intro_text', "Let's battle!$", type=str)
            defeat_text = _s.value(
                'trainer_defaults/defeat_text', "I lost...$", type=str)
            post_text = _s.value(
                'trainer_defaults/post_battle_text', "Good fight.$", type=str)
        except Exception:
            intro_text = "Let's battle!$"
            defeat_text = "I lost...$"
            post_text = "Good fight.$"
        if self._detail_panel:
            self._detail_panel.set_pending_dialogue(
                const, intro_text, defeat_text, post_text)

        self._rebuild_list()
        for i in range(self._list.count()):
            item = self._list.item(i)
            if item and item.data(Qt.ItemDataRole.UserRole) == const:
                self._list.setCurrentRow(i)
                break

        # Auto-add #define to opponents.h
        define_ok = self._add_trainer_define(const, next_id)

        self.changed.emit()
        if define_ok:
            self._show_warn(
                f"✚ {const} created (ID {next_id}). "
                f"Added #define to opponents.h automatically."
            )
        else:
            self._show_warn(
                f"✚ {const} created (ID {next_id}). "
                f"Could not write opponents.h — you must manually add  "
                f"#define {const}  {next_id}  to "
                f"include/constants/opponents.h before building."
            )

    def _find_class_template(self, cls_const: str) -> dict:
        """Find a template trainer for a class to copy defaults from.

        Priority:
        1. A blank-named entry for that class (the unused RS_ templates)
        2. Any existing trainer of that class (they all share the same
           encounter music, trainer pic, etc. within a class)
        3. Empty dict — caller uses hardcoded defaults
        """
        first_match = None
        for const, data in self._trainers.items():
            if data.get("trainerClass") != cls_const:
                continue
            name = data.get("trainerName", "")
            # Prefer blank-named template: _("") or _('')
            if name in ('_("")', "_('')", ''):
                return data
            # Remember the first named trainer as fallback
            if first_match is None:
                first_match = data
        return first_match or {}

    def _add_trainer_define(self, const: str, trainer_id: int) -> bool:
        """Append a #define for the new trainer to opponents.h.
        Returns True on success, False if the file couldn't be written.
        Checks for duplicates — won't write if the constant already exists."""
        path = os.path.join(self._project_root, "include", "constants", "opponents.h")
        if not os.path.isfile(path):
            return False
        try:
            with open(path, "r", encoding="utf-8", errors="replace") as f:
                content = f.read()
            # Check if this constant already exists — don't write a duplicate
            if re.search(r'#define\s+' + re.escape(const) + r'\s', content):
                return True  # already there, nothing to do
            # Find the last #define TRAINER_* line and insert after it
            lines = content.splitlines(keepends=True)
            last_define_idx = -1
            for i, line in enumerate(lines):
                if line.strip().startswith("#define TRAINER_"):
                    last_define_idx = i
            if last_define_idx < 0:
                return False
            # Insert new #define right after the last one
            new_line = f"#define {const:<40s} {trainer_id}\n"
            lines.insert(last_define_idx + 1, new_line)
            # Update NUM_TRAINERS to reflect the new count
            num_pat = re.compile(r'(#define\s+NUM_TRAINERS\s+)\d+')
            for i, line in enumerate(lines):
                m = num_pat.match(line)
                if m:
                    lines[i] = f"{m.group(1)}{trainer_id + 1}\n"
                    break
            with open(path, "w", encoding="utf-8", newline="\n") as f:
                f.writelines(lines)
            return True
        except Exception as exc:
            log.warning("_add_trainer_define: %s", exc)
            return False

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

    def _on_setup_battle(self):
        """Emit signal to jump to Event Editor with this trainer pre-selected."""
        if self._current_const:
            self.setup_battle_requested.emit(self._current_const)

    def _on_edit_tier_gates(self):
        """Open the rematch tier settings dialog."""
        all_flags = _parse_all_flags(self._project_root)
        dlg = _RematchSettingsDialog(
            gate_flags=list(getattr(self, '_gate_flags', [""] * 6)),
            all_flags=all_flags,
            rematch_entries=list(self._rematch_entries),
            raw_lines=list(self._rematch_raw_lines),
            project_root=self._project_root,
            parent=self,
        )
        if dlg.exec() == QDialog.DialogCode.Accepted:
            # Reload everything — the file changed on disk
            self._reload_after_rematch_edit()

    def _find_trainer_map(self, trainer_const: str) -> str:
        """Find which map contains this trainer's trainerbattle command.
        Searches all data/maps/*/scripts.inc for the trainer constant."""
        maps_dir = os.path.join(self._project_root, "data", "maps")
        if not os.path.isdir(maps_dir):
            return ""
        for map_name in sorted(os.listdir(maps_dir)):
            scripts_path = os.path.join(maps_dir, map_name, "scripts.inc")
            if not os.path.isfile(scripts_path):
                continue
            try:
                with open(scripts_path, encoding="utf-8", errors="replace") as f:
                    if trainer_const in f.read():
                        return f"MAP_{map_name.upper()}"
            except Exception:
                continue
        return ""

    def _on_add_to_rematch(self, trainer_const: str):
        """Add a trainer to the VS Seeker rematch table in vs_seeker.c."""
        if not trainer_const or not self._project_root:
            return

        # Check if already in the table
        if trainer_const in self._rematch_any_map:
            QMessageBox.information(self, "Already in Rematch Table",
                f"{trainer_const} is already in the VS Seeker rematch table.")
            return

        # Auto-detect which map the trainer is on by searching scripts
        map_name = self._find_trainer_map(trainer_const)
        if not map_name:
            QMessageBox.warning(self, "Map Not Found",
                f"Could not find {trainer_const} in any map's scripts.inc.\n\n"
                f"The trainer needs to be placed on a map with a trainerbattle\n"
                f"command before it can be added to the rematch table.")
            return

        friendly_map = map_name.replace("MAP_", "").replace("_", " ").title()
        reply = QMessageBox.question(
            self, "Add to Rematch Table",
            f"Add {trainer_const} to the VS Seeker rematch table?\n\n"
            f"Map: {friendly_map}\n\n"
            f"This creates a new entry in sRematches[] in vs_seeker.c\n"
            f"with empty rematch tiers you can fill in.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
        if reply != QMessageBox.StandardButton.Yes:
            return

        # Create new entry with the trainer as base and empty tiers
        max_tiers = len(self._gate_flags) if self._gate_flags else 6
        trainers = [trainer_const] + [""] * (max_tiers - 1)
        new_entry = {"trainers": trainers, "map": map_name}
        self._rematch_entries.append(new_entry)

        # Write updated table to vs_seeker.c
        final_lines = _write_rematch_table(self._rematch_raw_lines,
                                            self._rematch_entries)
        path = os.path.join(self._project_root, "src", "vs_seeker.c")
        try:
            with open(path, "w", encoding="utf-8", newline="\n") as f:
                f.writelines(final_lines)
        except Exception as e:
            QMessageBox.warning(self, "Write Error",
                f"Failed to write vs_seeker.c:\n{e}")
            self._rematch_entries.pop()  # revert in-memory
            return

        # Reload rematch data from disk
        self._reload_after_rematch_edit()
        self.changed.emit()

    def _reload_after_rematch_edit(self):
        """Reload rematch data after the settings dialog saved changes."""
        rematch_entries, self._rematch_raw_lines = _parse_rematch_table(self._project_root)
        self._rematch_entries = rematch_entries
        self._rematch_base_map, self._rematch_any_map, self._rematch_variants = \
            _build_rematch_map(rematch_entries)
        gate_flags = _parse_tier_gate_flags(self._project_root)
        self._gate_flags = gate_flags
        tier_labels = _build_tier_labels(gate_flags)

        # Update the detail panel's data refs
        if self._detail_panel:
            self._detail_panel._rematch_map_data = self._rematch_any_map
            self._detail_panel._tier_labels = tier_labels
            self._detail_panel._gate_flags = gate_flags
            # Re-populate the tier dropdown for the current trainer
            if self._current_const:
                self._detail_panel._populate_party_rematch_info(self._current_const)

        # Rebuild the trainer list to reflect any changes in variant filtering
        self._rebuild_list()

    def _show_warn(self, msg: str):
        self._warn_lbl.setText(msg)
        self._warn_lbl.show()
