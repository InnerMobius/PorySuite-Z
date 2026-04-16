"""
ui/abilities_tab_widget.py
Abilities Editor — browse, edit names/descriptions, view species usage.

Left  – searchable list of all abilities
Right – detail panel: identity, description, battle info, species usage
"""

from __future__ import annotations

import os
import re

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QFont
from PyQt6.QtWidgets import (
    QComboBox, QDialog, QDialogButtonBox,
    QFormLayout, QFrame,
    QGroupBox, QHBoxLayout, QHeaderView, QLabel, QLineEdit,
    QListWidget, QListWidgetItem, QMessageBox,
    QPushButton, QScrollArea, QSizePolicy,
    QSpinBox, QSplitter, QTableWidget, QTableWidgetItem,
    QTextEdit, QVBoxLayout, QWidget,
)

from ui.dex_description_edit import DexDescriptionEdit

# ── Constants ────────────────────────────────────────────────────────────────

ABILITY_NAME_LENGTH = 12   # GBA in-game max (ABILITY_NAME_LENGTH in battle_main.h)
ABILITY_DESC_LENGTH = 51   # 52-byte buffer on summary screen minus null terminator

# Matches mainwindow.MainWindow.DIRTY_FLAG_ROLE — the role the shared
# _DirtyDelegate paints amber backgrounds from.  Duplicated here instead of
# imported to avoid a circular import (mainwindow imports this module).
DIRTY_FLAG_ROLE = Qt.ItemDataRole.UserRole + 500


def _compose_template_notes(tmpl, params: dict) -> str:
    """Build the notes text for a template, merging static `notes` with any
    dynamic param-aware warnings.

    Shared by `AddAbilityDialog` and `AbilityDetailPanel` so the guidance
    the user sees is identical whether they're creating a new ability or
    editing an existing one.  Returns an empty string if there is nothing
    worth saying.
    """
    parts: list[str] = []
    base = getattr(tmpl, "notes", "") or ""
    if base:
        parts.append(base)

    # weather_switchin with Hail — confirm the tool will synthesize the
    # missing BattleScript on Save.  ``weather`` may be either the info
    # dict (fresh pick from the combo) or the display-name string (round-
    # tripped from disk), so handle both forms — otherwise a loaded Hail
    # ability silently loses its "✓ tool will write BattleScript" hint.
    if getattr(tmpl, "id", "") == "weather_switchin":
        from core.ability_effect_templates import WEATHER_CHOICES
        weather = params.get("weather")
        if isinstance(weather, dict):
            script = weather.get("script", "")
        elif isinstance(weather, str):
            script = ""
            for display, info in WEATHER_CHOICES:
                if display == weather:
                    script = info.get("script", "")
                    break
        else:
            script = ""
        if script == "BattleScript_SnowWarningActivates":
            parts.append(
                "✓ You picked Hail — on Save, the tool will append "
                "BattleScript_SnowWarningActivates to "
                "data/battle_scripts_1.s AND add the matching extern "
                "declaration to include/battle_scripts.h (idempotent — "
                "safe to Save multiple times).  Nothing else to do."
            )

    # stat_double with STAT_SPEED — the injection site doesn't own speed.
    if getattr(tmpl, "id", "") == "stat_double":
        if params.get("stat") == "STAT_SPEED":
            parts.append(
                "⚠ STAT_SPEED was picked.  The emitted C is injected "
                "into CalculateBaseDamage which doesn't own the speed "
                "stat — the auto-generated code is effectively a no-op "
                "for speed.  You MUST hand-edit GetWhoStrikesFirst in "
                "src/battle_main.c to double this Pokemon's speed for "
                "the ability to do anything in game."
            )
    return "\n\n".join(parts)


def _name_to_constant_suffix(display_name: str) -> str:
    """Derive an ALL_CAPS_UNDERSCORE constant suffix from a display name.

    "Speed Boost" → "SPEED_BOOST"
    "Thick Skin"  → "THICK_SKIN"
    "Volt Absorb" → "VOLT_ABSORB"
    """
    text = display_name.strip()
    if not text:
        return ""
    # Replace spaces/hyphens with underscores
    text = re.sub(r"[\s\-]+", "_", text)
    # Remove apostrophes, periods, and non-alphanumeric/non-underscore
    text = re.sub(r"[^A-Za-z0-9_]", "", text)
    # Collapse runs, strip edges, uppercase
    text = re.sub(r"_+", "_", text).strip("_").upper()
    return text

# Files that contain ability battle effect code (in-combat)
_BATTLE_FILES = [
    os.path.join("src", "battle_util.c"),
    os.path.join("src", "battle_script_commands.c"),
    os.path.join("src", "battle_main.c"),
    os.path.join("src", "pokemon.c"),
]

# Files that contain ability field effect code (overworld / non-combat)
_FIELD_FILES = [
    os.path.join("src", "wild_encounter.c"),
    os.path.join("src", "field_player_avatar.c"),
    os.path.join("src", "daycare.c"),
    os.path.join("src", "party_menu.c"),
    os.path.join("src", "field_control_avatar.c"),
]

# ── Battle effect extraction ─────────────────────────────────────────────────

def _extract_case_blocks(filepath: str, ability_const: str) -> list[tuple[int, int, str]]:
    """Find all 'case ABILITY_XXX:' blocks in a C file.

    Returns list of (start_line, end_line, block_text).
    A block runs from the 'case ABILITY_XXX:' line until the next
    'case ', 'default:', or matching '}' at or below the case indent.
    """
    if not os.path.isfile(filepath):
        return []
    try:
        with open(filepath, "r", encoding="utf-8") as f:
            lines = f.readlines()
    except OSError:
        return []

    pattern = re.compile(r'^(\s*)case\s+' + re.escape(ability_const) + r'\s*:')
    blocks: list[tuple[int, int, str]] = []

    for i, line in enumerate(lines):
        m = pattern.match(line)
        if not m:
            continue
        indent_len = len(m.group(1))
        start = i
        end = i
        # Scan forward to find the end of the case block
        for j in range(i + 1, len(lines)):
            stripped = lines[j].lstrip()
            cur_indent = len(lines[j]) - len(lines[j].lstrip())
            # Next case at same or lesser indent = end of our block
            if cur_indent <= indent_len and (
                stripped.startswith("case ") or stripped.startswith("default:")
            ):
                end = j - 1
                break
            # Closing brace at the switch level
            if cur_indent <= indent_len and stripped.startswith("}"):
                end = j - 1
                break
            end = j
        # Trim trailing blank lines
        while end > start and not lines[end].strip():
            end -= 1
        block_text = "".join(lines[start:end + 1])
        blocks.append((start + 1, end + 1, block_text))  # 1-indexed

    return blocks


def _find_inline_references(filepath: str, ability_const: str) -> list[tuple[int, str]]:
    """Find lines that reference an ability constant outside of case blocks.

    Returns list of (line_number, line_text) for non-case references.
    """
    if not os.path.isfile(filepath):
        return []
    try:
        with open(filepath, "r", encoding="utf-8") as f:
            lines = f.readlines()
    except OSError:
        return []

    refs: list[tuple[int, str]] = []
    pat = re.compile(r'\b' + re.escape(ability_const) + r'\b')
    case_pat = re.compile(r'^\s*case\s+' + re.escape(ability_const) + r'\s*:')

    for i, line in enumerate(lines):
        if pat.search(line) and not case_pat.match(line):
            refs.append((i + 1, line.rstrip()))

    return refs


def scan_ability_battle_effects(project_root: str, ability_const: str) -> dict:
    """Scan the project for all battle effect code referencing an ability.

    Returns {
        'case_blocks': [(file, start, end, text), ...],
        'inline_refs': [(file, line_no, text), ...],
    }
    """
    result = {"case_blocks": [], "inline_refs": []}
    for rel_path in _BATTLE_FILES:
        full = os.path.join(project_root, rel_path)
        for start, end, text in _extract_case_blocks(full, ability_const):
            result["case_blocks"].append((rel_path, start, end, text))
        for line_no, text in _find_inline_references(full, ability_const):
            result["inline_refs"].append((rel_path, line_no, text))
    return result


def copy_battle_effects(project_root: str, source_const: str, new_const: str) -> int:
    """Copy all case blocks from source ability to new ability in battle files.

    For each 'case SOURCE_CONST:' block, inserts a duplicate
    'case NEW_CONST:' block immediately before it.

    Returns count of blocks copied.
    """
    copied = 0
    for rel_path in _BATTLE_FILES:
        full = os.path.join(project_root, rel_path)
        blocks = _extract_case_blocks(full, source_const)
        if not blocks:
            continue

        try:
            with open(full, "r", encoding="utf-8") as f:
                lines = f.readlines()
        except OSError:
            continue

        # Insert in reverse order so line numbers stay valid
        for start, _end, text in reversed(blocks):
            new_block = text.replace(source_const, new_const)
            # Insert the new case block right before the source block
            insert_idx = start - 1  # 0-indexed
            new_lines = new_block.splitlines(keepends=True)
            # Ensure trailing newline
            if new_lines and not new_lines[-1].endswith("\n"):
                new_lines[-1] += "\n"
            lines[insert_idx:insert_idx] = new_lines
            copied += 1

        try:
            with open(full, "w", encoding="utf-8", newline="\n") as f:
                f.writelines(lines)
        except OSError:
            pass

    return copied


def build_inline_ref_summary(inline_refs: list[tuple[str, int, str]]) -> str:
    """Build a readable summary of inline references that need manual attention."""
    if not inline_refs:
        return ""
    lines = ["The following inline references may also need updating:"]
    for filepath, line_no, text in inline_refs[:15]:
        lines.append(f"  {filepath}:{line_no}  {text.strip()[:80]}")
    if len(inline_refs) > 15:
        lines.append(f"  ... and {len(inline_refs) - 15} more")
    return "\n".join(lines)


# ── Field effect scanning / copying ──────────────────────────────────────────

def scan_ability_field_effects(project_root: str, ability_const: str) -> dict:
    """Scan the project for all field/overworld code referencing an ability.

    Returns {
        'case_blocks': [(file, start, end, text), ...],
        'inline_refs': [(file, line_no, text), ...],
    }
    """
    result = {"case_blocks": [], "inline_refs": []}
    for rel_path in _FIELD_FILES:
        full = os.path.join(project_root, rel_path)
        for start, end, text in _extract_case_blocks(full, ability_const):
            result["case_blocks"].append((rel_path, start, end, text))
        for line_no, text in _find_inline_references(full, ability_const):
            result["inline_refs"].append((rel_path, line_no, text))
    return result


def copy_field_effects(project_root: str, source_const: str, new_const: str) -> tuple[int, list[tuple[str, int, str]]]:
    """Copy field effect code from source ability to new ability.

    For case blocks: inserts a duplicate block with the new constant.
    For inline references (if/else-if): adds a parallel check for the new constant.

    Returns (blocks_copied, inline_refs_needing_manual_attention).
    """
    copied = 0
    remaining_inline: list[tuple[str, int, str]] = []

    for rel_path in _FIELD_FILES:
        full = os.path.join(project_root, rel_path)

        # Handle case blocks the same as battle effects
        case_blocks = _extract_case_blocks(full, source_const)
        if case_blocks:
            try:
                with open(full, "r", encoding="utf-8") as f:
                    lines = f.readlines()
            except OSError:
                continue
            for start, _end, text in reversed(case_blocks):
                new_block = text.replace(source_const, new_const)
                insert_idx = start - 1
                new_lines = new_block.splitlines(keepends=True)
                if new_lines and not new_lines[-1].endswith("\n"):
                    new_lines[-1] += "\n"
                lines[insert_idx:insert_idx] = new_lines
                copied += 1
            try:
                with open(full, "w", encoding="utf-8", newline="\n") as f:
                    f.writelines(lines)
            except OSError:
                pass
            continue  # re-read if needed for inline below

        # Handle inline references — try to auto-duplicate if/else-if patterns
        inline = _find_inline_references(full, source_const)
        if not inline:
            continue

        try:
            with open(full, "r", encoding="utf-8") as f:
                lines = f.readlines()
        except OSError:
            continue

        # Pattern: "if (ability == ABILITY_XXX)" or "else if (ability == ABILITY_XXX)"
        # We can duplicate these by adding an "|| ability == NEW_CONST" condition
        if_pat = re.compile(
            r'((?:else\s+)?if\s*\([^)]*)\b'
            + re.escape(source_const)
            + r'\b([^)]*\))'
        )
        changed = False
        for line_no, _text in reversed(inline):
            idx = line_no - 1
            if idx < 0 or idx >= len(lines):
                remaining_inline.append((rel_path, line_no, _text))
                continue
            line = lines[idx]
            m = if_pat.search(line)
            if m:
                # Add || check for the new constant alongside the old one
                # e.g. "if (ability == ABILITY_STENCH)" →
                #      "if (ability == ABILITY_STENCH || ability == ABILITY_NEW)"
                old_frag = source_const + m.group(2)
                new_frag = source_const + " || ability == " + new_const + m.group(2)
                new_line = line.replace(old_frag, new_frag, 1)
                if new_line != line:
                    lines[idx] = new_line
                    changed = True
                    copied += 1
                else:
                    remaining_inline.append((rel_path, line_no, _text))
            else:
                remaining_inline.append((rel_path, line_no, _text))

        if changed:
            try:
                with open(full, "w", encoding="utf-8", newline="\n") as f:
                    f.writelines(lines)
            except OSError:
                pass

    return copied, remaining_inline


# ── Stylesheet fragments (matching moves/items editor style) ────────────────

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

_BTN_SS = """
    QPushButton {
        background-color: #2a2a2a; color: #aaaaaa;
        border: 1px solid #3a3a3a; border-radius: 4px;
        font-size: 11px; padding: 4px 10px;
    }
    QPushButton:hover  { background-color: #333333; color: #cccccc; }
    QPushButton:pressed { background-color: #222222; }
    QPushButton:disabled { color: #555555; border-color: #2a2a2a; }
"""

_GROUP_SS = """
QGroupBox {
    color: #aaaaaa; font-size: 11px; font-weight: bold;
    border: 1px solid #2a2a2a; border-radius: 4px;
    margin-top: 8px; padding: 12px 8px 8px 8px;
}
QGroupBox::title {
    subcontrol-origin: margin; left: 10px; padding: 0 4px;
}
"""

_TABLE_SS = """
QTableWidget {
    background-color: #1a1a1a; color: #cccccc;
    border: none; font-size: 11px; gridline-color: #2a2a2a;
}
QTableWidget::item { padding: 3px 6px; }
QTableWidget::item:selected { background-color: #1565c0; color: #ffffff; }
QHeaderView::section {
    background-color: #222222; color: #aaaaaa;
    border: none; border-bottom: 1px solid #2a2a2a;
    padding: 4px 6px; font-size: 10px;
}
"""


# ── Helpers ──────────────────────────────────────────────────────────────────

def _lbl(text: str) -> QLabel:
    lbl = QLabel(text)
    lbl.setStyleSheet("color: #888888; font-size: 11px;")
    return lbl


def _val_lbl(text: str = "") -> QLabel:
    lbl = QLabel(text)
    lbl.setStyleSheet("color: #cccccc; font-size: 12px;")
    lbl.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
    return lbl


# ── Battle effect category parser ────────────────────────────────────────────

# Maps ability constants to their battle effect categories based on where they
# appear in AbilityBattleEffects() in battle_util.c.  This is read-only info.
_BATTLE_CATEGORIES: dict[str, str] = {
    # On Switch-In (case 0)
    "ABILITY_DRIZZLE": "On Switch-In: Summons rain",
    "ABILITY_SAND_STREAM": "On Switch-In: Summons sandstorm",
    "ABILITY_DROUGHT": "On Switch-In: Summons sunlight",
    "ABILITY_INTIMIDATE": "On Switch-In: Lowers foe's Attack",
    "ABILITY_TRACE": "On Switch-In: Copies foe's ability",
    "ABILITY_CLOUD_NINE": "On Switch-In: Suppresses weather",
    "ABILITY_AIR_LOCK": "On Switch-In: Suppresses weather",
    "ABILITY_FORECAST": "On Switch-In: Changes form with weather",
    # End of Turn (case 1)
    "ABILITY_RAIN_DISH": "End of Turn: Heals in rain",
    "ABILITY_SHED_SKIN": "End of Turn: May cure status",
    "ABILITY_SPEED_BOOST": "End of Turn: Raises Speed",
    "ABILITY_TRUANT": "End of Turn: Loafs every other turn",
    # Move Blocking (case 2)
    "ABILITY_SOUNDPROOF": "Blocks: Sound-based moves",
    "ABILITY_CACOPHONY": "Blocks: Sound-based moves",
    # Absorbing (case 3)
    "ABILITY_VOLT_ABSORB": "Absorbs: Electric moves → HP",
    "ABILITY_WATER_ABSORB": "Absorbs: Water moves → HP",
    "ABILITY_FLASH_FIRE": "Absorbs: Fire moves → power boost",
    # On Damage Received (case 4)
    "ABILITY_COLOR_CHANGE": "On Damage: Changes type to attacker's move",
    "ABILITY_ROUGH_SKIN": "On Damage: Hurts attacker on contact",
    "ABILITY_EFFECT_SPORE": "On Contact: May paralyze/poison/sleep",
    "ABILITY_POISON_POINT": "On Contact: May poison attacker",
    "ABILITY_STATIC": "On Contact: May paralyze attacker",
    "ABILITY_FLAME_BODY": "On Contact: May burn attacker",
    "ABILITY_CUTE_CHARM": "On Contact: May infatuate attacker",
    # Immunity (case 5)
    "ABILITY_IMMUNITY": "Immune: Poison",
    "ABILITY_OWN_TEMPO": "Immune: Confusion",
    "ABILITY_LIMBER": "Immune: Paralysis",
    "ABILITY_INSOMNIA": "Immune: Sleep",
    "ABILITY_VITAL_SPIRIT": "Immune: Sleep",
    "ABILITY_WATER_VEIL": "Immune: Burns",
    "ABILITY_MAGMA_ARMOR": "Immune: Freeze",
    "ABILITY_OBLIVIOUS": "Immune: Attract",
    # Synchronize (case 7-8)
    "ABILITY_SYNCHRONIZE": "Status: Passes poison/burn/paralysis to attacker",
    # Other battle effects
    "ABILITY_LEVITATE": "Immune: Ground-type moves",
    "ABILITY_WONDER_GUARD": "Immune: Non-super-effective moves",
    "ABILITY_BATTLE_ARMOR": "Blocks: Critical hits",
    "ABILITY_SHELL_ARMOR": "Blocks: Critical hits",
    "ABILITY_CLEAR_BODY": "Blocks: Stat reduction",
    "ABILITY_WHITE_SMOKE": "Blocks: Stat reduction",
    "ABILITY_INNER_FOCUS": "Blocks: Flinching",
    "ABILITY_SHADOW_TAG": "Traps: Prevents foe from fleeing",
    "ABILITY_ARENA_TRAP": "Traps: Grounded foes can't flee",
    "ABILITY_MAGNET_PULL": "Traps: Steel-types can't flee",
    "ABILITY_SUCTION_CUPS": "Blocks: Forced switching",
    "ABILITY_STICKY_HOLD": "Blocks: Item theft",
    "ABILITY_SHIELD_DUST": "Blocks: Secondary move effects",
    "ABILITY_NATURAL_CURE": "On Switch-Out: Cures status",
    "ABILITY_LIGHTNING_ROD": "Redirects: Electric moves to self",
    "ABILITY_SERENE_GRACE": "Boost: Doubles secondary effect chance",
    "ABILITY_COMPOUND_EYES": "Boost: Raises accuracy",
    "ABILITY_HUSTLE": "Boost: Raises Attack, lowers accuracy",
    "ABILITY_HUGE_POWER": "Boost: Doubles Attack",
    "ABILITY_PURE_POWER": "Boost: Doubles Attack",
    "ABILITY_GUTS": "Boost: Raises Attack when statused",
    "ABILITY_MARVEL_SCALE": "Boost: Raises Defense when statused",
    "ABILITY_SWIFT_SWIM": "Boost: Doubles Speed in rain",
    "ABILITY_CHLOROPHYLL": "Boost: Doubles Speed in sun",
    "ABILITY_SAND_VEIL": "Boost: Raises evasion in sandstorm",
    "ABILITY_THICK_FAT": "Resist: Halves Fire/Ice damage",
    "ABILITY_KEEN_EYE": "Blocks: Accuracy reduction",
    "ABILITY_HYPER_CUTTER": "Blocks: Attack reduction",
    "ABILITY_EARLY_BIRD": "Status: Wakes up from sleep faster",
    "ABILITY_ROCK_HEAD": "Immune: Recoil damage",
    "ABILITY_PRESSURE": "Cost: Foe uses 2 PP per move",
    "ABILITY_LIQUID_OOZE": "Punish: Draining moves damage attacker",
    "ABILITY_OVERGROW": "Pinch: Grass moves ×1.5 at low HP",
    "ABILITY_BLAZE": "Pinch: Fire moves ×1.5 at low HP",
    "ABILITY_TORRENT": "Pinch: Water moves ×1.5 at low HP",
    "ABILITY_SWARM": "Pinch: Bug moves ×1.5 at low HP",
    "ABILITY_PLUS": "Combo: Boosts Sp.Atk with Minus ally",
    "ABILITY_MINUS": "Combo: Boosts Sp.Atk with Plus ally",
    "ABILITY_DAMP": "Blocks: Self-destruct / Explosion",
    "ABILITY_STURDY": "Blocks: One-hit KO moves",
    "ABILITY_STENCH": "On Contact: 10% chance to flinch (Gen 5+)",
}

# Extended categories for user-created abilities (Pixilate family etc.)
# These get added if any project defines them
_EXTENDED_BATTLE_CATEGORIES: dict[str, str] = {
    "type_change_boost": "Converts moves of one type to another + power boost",
    "intimidate_dual": "Lowers two of the opponent's stats on switch-in",
    "switchin_field_effect": "Sets a field condition (Trick Room/Tailwind) on switch-in",
    "multi_type_resist": "Halves damage from two specific types",
}

_FIELD_EFFECTS: dict[str, str] = {
    "ABILITY_STENCH": "Field: Reduces wild encounter rate",
    "ABILITY_ILLUMINATE": "Field: Increases wild encounter rate",
    "ABILITY_PICKUP": "Field: May find items after battle",
    "ABILITY_RUN_AWAY": "Field: Guaranteed escape from wild battles",
    "ABILITY_MAGMA_ARMOR": "Field: Halves egg hatch time",
    "ABILITY_FLAME_BODY": "Field: Halves egg hatch time",
    "ABILITY_SYNCHRONIZE": "Field: Wild encounters match lead's nature (50%)",
    "ABILITY_CUTE_CHARM": "Field: Wild encounters favor opposite gender",
    "ABILITY_MAGNET_PULL": "Field: Increases Steel-type encounter rate",
    "ABILITY_STATIC": "Field: Increases Electric-type encounter rate",
}


# ── AbilityDetailPanel ───────────────────────────────────────────────────────

class AbilityDetailPanel(QWidget):
    """Right-side detail panel for one ability."""

    changed = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._loading = False
        self._build_ui()

    def _build_ui(self):
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setStyleSheet(_SCROLL_SS)
        scroll.setFrameShape(QFrame.Shape.NoFrame)

        inner = QWidget()
        v = QVBoxLayout(inner)
        v.setContentsMargins(12, 8, 12, 8)
        v.setSpacing(6)

        # ── Identity group ──────────────────────────────────────────────────
        grp_id = QGroupBox("Identity")
        grp_id.setStyleSheet(_GROUP_SS)
        form = QFormLayout(grp_id)
        form.setLabelAlignment(Qt.AlignmentFlag.AlignRight)
        form.setSpacing(6)

        self.lbl_id = _val_lbl("—")
        form.addRow(_lbl("ID"), self.lbl_id)

        self.edit_const = QLineEdit()
        self.edit_const.setPlaceholderText("ABILITY_CONSTANT_NAME")
        self.edit_const.setReadOnly(True)
        self.edit_const.setStyleSheet(
            "background-color: #161616; color: #999999; border: 1px solid #3a3a3a;"
            " border-radius: 3px; padding: 4px 6px; font-size: 12px;"
        )
        self.edit_const.setToolTip("Use the Rename button to change the constant name")
        form.addRow(_lbl("Constant"), self.edit_const)

        # Display name with character counter
        name_row = QHBoxLayout()
        self.edit_name = QLineEdit()
        self.edit_name.setMaxLength(ABILITY_NAME_LENGTH)
        self.edit_name.setPlaceholderText("Display Name")
        self.edit_name.setStyleSheet(
            "background-color: #1e1e1e; color: #cccccc; border: 1px solid #3a3a3a;"
            " border-radius: 3px; padding: 4px 6px; font-size: 12px;"
        )
        self.edit_name.textChanged.connect(self._on_edit)
        self.edit_name.textChanged.connect(self._update_name_counter)
        name_row.addWidget(self.edit_name)

        self.lbl_name_counter = QLabel("0/12")
        self.lbl_name_counter.setStyleSheet("color: #555555; font-size: 10px;")
        self.lbl_name_counter.setFixedWidth(40)
        self.lbl_name_counter.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        name_row.addWidget(self.lbl_name_counter)

        form.addRow(_lbl("Display Name"), name_row)

        v.addWidget(grp_id)

        # ── Description group ───────────────────────────────────────────────
        grp_desc = QGroupBox("Description (Summary Screen)")
        grp_desc.setStyleSheet(_GROUP_SS)
        desc_v = QVBoxLayout(grp_desc)

        self.edit_desc = DexDescriptionEdit(
            max_chars_per_line=ABILITY_DESC_LENGTH,
            max_lines=1,
        )
        self.edit_desc.setFixedHeight(36)
        self.edit_desc.setStyleSheet(
            "background-color: #1e1e1e; color: #cccccc; border: 1px solid #3a3a3a;"
            " border-radius: 3px; padding: 4px 6px; font-size: 12px;"
        )
        self.edit_desc.textChanged.connect(self._on_edit)

        self.lbl_desc_counter = QLabel("0/51")
        self.lbl_desc_counter.setStyleSheet("color: #555555; font-size: 10px;")
        self.edit_desc.set_counter_label(self.lbl_desc_counter)

        desc_v.addWidget(self.edit_desc)
        desc_v.addWidget(self.lbl_desc_counter)

        v.addWidget(grp_desc)

        # ── Battle Effect Editor ────────────────────────────────────────────
        grp_battle = QGroupBox("Battle Effect")
        grp_battle.setStyleSheet(_GROUP_SS)
        battle_v = QVBoxLayout(grp_battle)
        battle_v.setSpacing(4)

        self.cmb_battle_template = QComboBox()
        self.cmb_battle_template.wheelEvent = lambda e: e.ignore()
        self.cmb_battle_template.addItem("(none)", "")
        from core.ability_effect_templates import BATTLE_TEMPLATES
        for tmpl in BATTLE_TEMPLATES:
            self.cmb_battle_template.addItem(tmpl.name, tmpl.id)
        self.cmb_battle_template.setToolTip(
            "Choose a battle effect category.\n"
            "The editor writes C code to the correct source files on save."
        )
        self.cmb_battle_template.currentIndexChanged.connect(
            self._on_battle_template_changed)
        battle_v.addWidget(self.cmb_battle_template)

        # Dynamic parameter area for battle effect
        self._battle_params_widget = QWidget()
        self._battle_params_layout = QFormLayout(self._battle_params_widget)
        self._battle_params_layout.setContentsMargins(0, 0, 0, 0)
        self._battle_params_layout.setSpacing(4)
        self._battle_params_layout.setLabelAlignment(
            Qt.AlignmentFlag.AlignRight)
        battle_v.addWidget(self._battle_params_widget)
        self._battle_param_combos: dict[str, QComboBox] = {}

        # Known effect label (shown when detection has no editable template
        # but the hardcoded category knows what this ability does)
        self.lbl_battle_known = QLabel("")
        self.lbl_battle_known.setWordWrap(True)
        self.lbl_battle_known.setStyleSheet(
            "color: #b0b0b0; font-size: 11px; padding: 4px 6px;"
            " background-color: #1a2a1a; border: 1px solid #2a4a2a;"
            " border-radius: 3px;"
        )
        self.lbl_battle_known.setVisible(False)
        battle_v.addWidget(self.lbl_battle_known)

        # Code preview
        self.lbl_battle_preview = QLabel("")
        self.lbl_battle_preview.setWordWrap(True)
        self.lbl_battle_preview.setStyleSheet(
            "color: #888888; font-size: 9px; font-family: 'Courier New';"
            " padding: 2px 4px; background-color: #161616;"
            " border: 1px solid #2a2a2a; border-radius: 3px;"
        )
        self.lbl_battle_preview.setVisible(False)
        battle_v.addWidget(self.lbl_battle_preview)

        # Implementation notes / prerequisites for the picked template.
        # Mirrors the amber info box shown in the Add Ability dialog so the
        # guidance stays consistent whether the user is creating a new
        # ability or editing an existing one.
        self.lbl_battle_notes = QLabel("")
        self.lbl_battle_notes.setWordWrap(True)
        self.lbl_battle_notes.setStyleSheet(
            "color: #e8a838; font-size: 10px;"
            " padding: 6px 8px; background-color: #1f1810;"
            " border: 1px solid #3a2f18; border-radius: 3px;"
            " margin-top: 4px;"
        )
        self.lbl_battle_notes.setVisible(False)
        battle_v.addWidget(self.lbl_battle_notes)

        v.addWidget(grp_battle)

        # ── Field Effect Editor ────────────────────────────────────────────
        grp_field = QGroupBox("Field Effect")
        grp_field.setStyleSheet(_GROUP_SS)
        field_v = QVBoxLayout(grp_field)
        field_v.setSpacing(4)

        self.cmb_field_template = QComboBox()
        self.cmb_field_template.wheelEvent = lambda e: e.ignore()
        self.cmb_field_template.addItem("(none)", "")
        from core.ability_effect_templates import FIELD_TEMPLATES
        for tmpl in FIELD_TEMPLATES:
            self.cmb_field_template.addItem(tmpl.name, tmpl.id)
        self.cmb_field_template.setToolTip(
            "Choose a field/overworld effect.\n"
            "The editor writes C code to wild_encounter.c on save."
        )
        self.cmb_field_template.currentIndexChanged.connect(
            self._on_field_template_changed)
        field_v.addWidget(self.cmb_field_template)

        # Dynamic parameter area for field effect
        self._field_params_widget = QWidget()
        self._field_params_layout = QFormLayout(self._field_params_widget)
        self._field_params_layout.setContentsMargins(0, 0, 0, 0)
        self._field_params_layout.setSpacing(4)
        self._field_params_layout.setLabelAlignment(
            Qt.AlignmentFlag.AlignRight)
        field_v.addWidget(self._field_params_widget)
        self._field_param_combos: dict[str, QComboBox] = {}

        # Known effect label for field effects
        self.lbl_field_known = QLabel("")
        self.lbl_field_known.setWordWrap(True)
        self.lbl_field_known.setStyleSheet(
            "color: #b0b0b0; font-size: 11px; padding: 4px 6px;"
            " background-color: #1a2a1a; border: 1px solid #2a4a2a;"
            " border-radius: 3px;"
        )
        self.lbl_field_known.setVisible(False)
        field_v.addWidget(self.lbl_field_known)

        # Code preview
        self.lbl_field_preview = QLabel("")
        self.lbl_field_preview.setWordWrap(True)
        self.lbl_field_preview.setStyleSheet(
            "color: #888888; font-size: 9px; font-family: 'Courier New';"
            " padding: 2px 4px; background-color: #161616;"
            " border: 1px solid #2a2a2a; border-radius: 3px;"
        )
        self.lbl_field_preview.setVisible(False)
        field_v.addWidget(self.lbl_field_preview)

        # Implementation notes / prerequisites for the picked template
        # (mirrors the Add Ability dialog for consistency).
        self.lbl_field_notes = QLabel("")
        self.lbl_field_notes.setWordWrap(True)
        self.lbl_field_notes.setStyleSheet(
            "color: #e8a838; font-size: 10px;"
            " padding: 6px 8px; background-color: #1f1810;"
            " border: 1px solid #3a2f18; border-radius: 3px;"
            " margin-top: 4px;"
        )
        self.lbl_field_notes.setVisible(False)
        field_v.addWidget(self.lbl_field_notes)

        hint = QLabel(
            "Effect changes are written to C source files on save. "
            "Some advanced effects may need manual code review."
        )
        hint.setWordWrap(True)
        hint.setStyleSheet("color: #606060; font-size: 9px; padding-top: 4px;")
        field_v.addWidget(hint)

        v.addWidget(grp_field)

        # ── Species Usage group ─────────────────────────────────────────────
        grp_species = QGroupBox("Species Usage")
        grp_species.setStyleSheet(_GROUP_SS)
        species_v = QVBoxLayout(grp_species)

        self.lbl_usage_count = _val_lbl("—")
        species_v.addWidget(self.lbl_usage_count)

        self.tbl_species = QTableWidget(0, 2)
        self.tbl_species.setHorizontalHeaderLabels(["Species", "Slot"])
        self.tbl_species.setStyleSheet(_TABLE_SS)
        self.tbl_species.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.tbl_species.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.tbl_species.verticalHeader().setVisible(False)
        hdr = self.tbl_species.horizontalHeader()
        hdr.setStretchLastSection(True)
        hdr.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        hdr.setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        self.tbl_species.setMinimumHeight(120)
        species_v.addWidget(self.tbl_species)

        v.addWidget(grp_species)
        v.addStretch(1)

        scroll.setWidget(inner)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(scroll)

    # ── public API ────────────────────────────────────────────────────────────

    def load(self, const: str, data: dict,
             species_usage: list[tuple[str, str, str]],
             project_root: str = ""):
        """
        Populate the panel with one ability's data.
        species_usage: list of (species_display_name, "Primary"|"Secondary", species_const)
        """
        self._loading = True
        self._current_const = const
        self._project_root = project_root
        self._species_constants = []  # parallel to table rows for jump-to
        try:
            self.lbl_id.setText(str(data.get("id", "?")))
            self.edit_const.setText(const)
            self.edit_name.setText(data.get("display_name", data.get("name", "")))
            self._update_name_counter()

            desc = data.get("description", "")
            self.edit_desc.blockSignals(True)
            self.edit_desc.setPlainText(desc)
            self.edit_desc.blockSignals(False)

            # ── Detect and load battle/field effects ───────────────────────
            # Pass the ability's own data dict in: if the user has edited this
            # ability earlier in the session, `save_current` has already
            # stashed the (template_id, params) tuple into `data` under
            # `_battle_effect`/`_field_effect`. We must prefer those values
            # over re-detecting from disk — otherwise navigating to another
            # ability and coming back silently reverts the edit.
            self._load_effect_editors(const, project_root, data)

            # Species usage table
            self.tbl_species.setRowCount(len(species_usage))
            self._species_constants = []
            primary = sum(1 for _, s, _ in species_usage if s == "Primary")
            secondary = len(species_usage) - primary
            self.lbl_usage_count.setText(
                f"Used by {len(species_usage)} species "
                f"({primary} primary, {secondary} secondary)"
            )
            if not species_usage:
                self.lbl_usage_count.setStyleSheet(
                    "color: #e8a838; font-size: 12px;"
                )
                self.lbl_usage_count.setText("Not assigned to any species")
            else:
                self.lbl_usage_count.setStyleSheet(
                    "color: #cccccc; font-size: 12px;"
                )

            for row, (sp_name, slot, sp_const) in enumerate(species_usage):
                self.tbl_species.setItem(row, 0, QTableWidgetItem(sp_name))
                self.tbl_species.setItem(row, 1, QTableWidgetItem(slot))
                self._species_constants.append(sp_const)
        finally:
            self._loading = False

    def collect(self) -> tuple[str, str, str]:
        """Return (constant, display_name, description) from current edits."""
        return (
            self.edit_const.text().strip(),
            self.edit_name.text().strip(),
            self.edit_desc.toPlainText().strip(),
        )

    def clear(self):
        self._loading = True
        self.lbl_id.setText("—")
        self.edit_const.clear()
        self.edit_name.clear()
        self.edit_desc.setPlainText("")
        self.cmb_battle_template.setCurrentIndex(0)
        self.cmb_field_template.setCurrentIndex(0)
        self._clear_param_combos(self._battle_params_layout,
                                 self._battle_param_combos)
        self._clear_param_combos(self._field_params_layout,
                                 self._field_param_combos)
        self.lbl_battle_known.setVisible(False)
        self.lbl_battle_preview.setVisible(False)
        self.lbl_battle_notes.setVisible(False)
        self.lbl_field_known.setVisible(False)
        self.lbl_field_preview.setVisible(False)
        self.lbl_field_notes.setVisible(False)
        self.lbl_usage_count.setText("—")
        self.tbl_species.setRowCount(0)
        self._current_const = ""
        self._project_root = ""
        self._loading = False

    # ── internal ─────────────────────────────────────────────────────────────

    def _on_edit(self):
        if not self._loading:
            self.changed.emit()

    def _update_name_counter(self):
        n = len(self.edit_name.text())
        mx = ABILITY_NAME_LENGTH
        if n > mx:
            col = "#e57373"
        elif n > int(mx * 0.85):
            col = "#ffb74d"
        else:
            col = "#555555"
        self.lbl_name_counter.setText(f"{n}/{mx}")
        self.lbl_name_counter.setStyleSheet(f"color: {col}; font-size: 10px;")

    # ── Effect editor methods ────────────────────────────────────────────────

    def _load_effect_editors(self, const: str, project_root: str,
                             data: dict | None = None):
        """Detect current effects and populate the template combos.

        If `data` contains session-edited `_battle_effect` / `_field_effect`
        tuples (stashed by `AbilitiesTabWidget.save_current` when the user
        navigated away from this ability), those take precedence over
        re-detecting from the C source on disk. This is what makes a mid-
        session edit survive a row switch-and-return.
        """
        from core.ability_effect_templates import (
            detect_all_effects, BATTLE_TEMPLATE_MAP, FIELD_TEMPLATE_MAP,
        )

        battle_result, field_result = None, None
        if project_root:
            try:
                battle_result, field_result = detect_all_effects(
                    project_root, const)
            except Exception as e:
                import logging
                logging.getLogger(__name__).warning(
                    "Effect detection failed for %s: %s", const, e)

        # Session-edited values override disk detection. The tuple shape
        # matches what `detect_all_effects` returns: (template_id, params).
        # Empty template_id ("" ) means the user cleared the effect — which
        # still needs to override the on-disk "yes there is an effect"
        # result, otherwise a clear-and-navigate-away is also lost.
        if data is not None:
            if "_battle_effect" in data:
                btid, bparams = data["_battle_effect"]
                battle_result = (btid, bparams) if btid else None
            if "_field_effect" in data:
                ftid, fparams = data["_field_effect"]
                field_result = (ftid, fparams) if ftid else None

        # ── Battle effect ──
        self.cmb_battle_template.blockSignals(True)
        self.lbl_battle_known.setVisible(False)
        if battle_result:
            tid, params = battle_result
            idx = self.cmb_battle_template.findData(tid)
            if idx >= 0:
                self.cmb_battle_template.setCurrentIndex(idx)
                self._rebuild_battle_params(tid, params)
            else:
                # Template detected but not in combo — add it on the fly
                from core.ability_effect_templates import BATTLE_TEMPLATE_MAP
                tmpl = BATTLE_TEMPLATE_MAP.get(tid)
                if tmpl:
                    self.cmb_battle_template.addItem(tmpl.name, tmpl.id)
                    new_idx = self.cmb_battle_template.findData(tid)
                    if new_idx >= 0:
                        self.cmb_battle_template.setCurrentIndex(new_idx)
                        self._rebuild_battle_params(tid, params)
                    else:
                        self.cmb_battle_template.setCurrentIndex(0)
                        self._clear_param_combos(self._battle_params_layout,
                                                 self._battle_param_combos)
                else:
                    self.cmb_battle_template.setCurrentIndex(0)
                    self._clear_param_combos(self._battle_params_layout,
                                             self._battle_param_combos)
        else:
            self.cmb_battle_template.setCurrentIndex(0)
            self._clear_param_combos(self._battle_params_layout,
                                     self._battle_param_combos)
            # Fallback: show known effect from hardcoded category
            known = _BATTLE_CATEGORIES.get(const)
            if known:
                self.lbl_battle_known.setText(
                    f"Detected: {known}\n(No editable template — "
                    f"effect is implemented as inline C code)")
                self.lbl_battle_known.setVisible(True)
        self.cmb_battle_template.blockSignals(False)
        self._update_battle_preview()

        # ── Field effect ──
        self.cmb_field_template.blockSignals(True)
        self.lbl_field_known.setVisible(False)
        if field_result:
            tid, params = field_result
            idx = self.cmb_field_template.findData(tid)
            if idx >= 0:
                self.cmb_field_template.setCurrentIndex(idx)
                self._rebuild_field_params(tid, params)
            else:
                # Template detected but not in combo — add it on the fly
                from core.ability_effect_templates import FIELD_TEMPLATE_MAP
                tmpl = FIELD_TEMPLATE_MAP.get(tid)
                if tmpl:
                    self.cmb_field_template.addItem(tmpl.name, tmpl.id)
                    new_idx = self.cmb_field_template.findData(tid)
                    if new_idx >= 0:
                        self.cmb_field_template.setCurrentIndex(new_idx)
                        self._rebuild_field_params(tid, params)
                    else:
                        self.cmb_field_template.setCurrentIndex(0)
                        self._clear_param_combos(self._field_params_layout,
                                                 self._field_param_combos)
                else:
                    self.cmb_field_template.setCurrentIndex(0)
                    self._clear_param_combos(self._field_params_layout,
                                             self._field_param_combos)
        else:
            self.cmb_field_template.setCurrentIndex(0)
            self._clear_param_combos(self._field_params_layout,
                                     self._field_param_combos)
            # Fallback: show known field effect
            known = _FIELD_EFFECTS.get(const)
            if known:
                self.lbl_field_known.setText(
                    f"Detected: {known}\n(No editable template — "
                    f"effect is implemented as inline C code)")
                self.lbl_field_known.setVisible(True)
        self.cmb_field_template.blockSignals(False)
        self._update_field_preview()

    def _clear_param_combos(self, layout: QFormLayout,
                            combos: dict[str, QComboBox]):
        """Remove all parameter widgets from a form layout."""
        while layout.rowCount() > 0:
            layout.removeRow(0)
        combos.clear()

    def _rebuild_battle_params(self, template_id: str,
                               current_params: dict = None):
        """Rebuild the parameter widgets for a battle effect template."""
        from core.ability_effect_templates import BATTLE_TEMPLATE_MAP
        self._clear_param_combos(self._battle_params_layout,
                                 self._battle_param_combos)
        tmpl = BATTLE_TEMPLATE_MAP.get(template_id)
        if not tmpl or not tmpl.params:
            return
        if current_params is None:
            current_params = {}
        for param in tmpl.params:
            cmb = QComboBox()
            cmb.wheelEvent = lambda e: e.ignore()
            for display, value in param.choices:
                cmb.addItem(display, value)
            # Select current value
            cur_val = current_params.get(param.id)
            if cur_val is not None:
                # Match by value (could be a string, int, or dict)
                for i in range(cmb.count()):
                    item_data = cmb.itemData(i)
                    if item_data == cur_val:
                        cmb.setCurrentIndex(i)
                        break
                    # Also check by display name match
                    if isinstance(cur_val, str) and cmb.itemText(i) == cur_val:
                        cmb.setCurrentIndex(i)
                        break
            cmb.currentIndexChanged.connect(self._on_battle_param_changed)
            self._battle_params_layout.addRow(
                _lbl(param.label), cmb)
            self._battle_param_combos[param.id] = cmb

    def _rebuild_field_params(self, template_id: str,
                              current_params: dict = None):
        """Rebuild the parameter widgets for a field effect template."""
        from core.ability_effect_templates import FIELD_TEMPLATE_MAP
        self._clear_param_combos(self._field_params_layout,
                                 self._field_param_combos)
        tmpl = FIELD_TEMPLATE_MAP.get(template_id)
        if not tmpl or not tmpl.params:
            return
        if current_params is None:
            current_params = {}
        for param in tmpl.params:
            cmb = QComboBox()
            cmb.wheelEvent = lambda e: e.ignore()
            for display, value in param.choices:
                cmb.addItem(display, value)
            cur_val = current_params.get(param.id)
            if cur_val is not None:
                for i in range(cmb.count()):
                    if cmb.itemData(i) == cur_val:
                        cmb.setCurrentIndex(i)
                        break
            cmb.currentIndexChanged.connect(self._on_field_param_changed)
            self._field_params_layout.addRow(
                _lbl(param.label), cmb)
            self._field_param_combos[param.id] = cmb

    def _on_battle_template_changed(self, _idx: int):
        """Category dropdown changed — rebuild params and preview."""
        tid = self.cmb_battle_template.currentData()
        self._rebuild_battle_params(tid if tid else "")
        self._update_battle_preview()
        if not self._loading:
            self.changed.emit()

    def _on_field_template_changed(self, _idx: int):
        tid = self.cmb_field_template.currentData()
        self._rebuild_field_params(tid if tid else "")
        self._update_field_preview()
        if not self._loading:
            self.changed.emit()

    def _on_battle_param_changed(self):
        self._update_battle_preview()
        if not self._loading:
            self.changed.emit()

    def _on_field_param_changed(self):
        self._update_field_preview()
        if not self._loading:
            self.changed.emit()

    def _collect_battle_params(self) -> dict:
        """Collect current parameter values from battle param combos."""
        return {pid: cmb.currentData()
                for pid, cmb in self._battle_param_combos.items()}

    def _collect_field_params(self) -> dict:
        return {pid: cmb.currentData()
                for pid, cmb in self._field_param_combos.items()}

    def _update_battle_preview(self):
        """Update the code preview label for battle effects."""
        from core.ability_effect_templates import (
            generate_battle_code, BATTLE_TEMPLATE_MAP,
        )
        tid = self.cmb_battle_template.currentData()
        if not tid:
            self.lbl_battle_preview.setVisible(False)
            self.lbl_battle_notes.clear()
            self.lbl_battle_notes.setVisible(False)
            return
        const = getattr(self, "_current_const", "") or "ABILITY_NEW"
        params = self._collect_battle_params()
        code_blocks = generate_battle_code(tid, const, params)
        if code_blocks:
            # Show first block's code, truncated
            preview = code_blocks[0][1]
            lines = preview.split("\n")
            if len(lines) > 8:
                preview = "\n".join(lines[:8]) + "\n  ..."
            self.lbl_battle_preview.setText(preview)
            self.lbl_battle_preview.setVisible(True)
        else:
            self.lbl_battle_preview.setVisible(False)
        # Notes — same guidance as the Add Ability dialog.
        tmpl = BATTLE_TEMPLATE_MAP.get(tid)
        note_text = _compose_template_notes(tmpl, params) if tmpl else ""
        if note_text:
            self.lbl_battle_notes.setText(note_text)
            self.lbl_battle_notes.setVisible(True)
        else:
            self.lbl_battle_notes.clear()
            self.lbl_battle_notes.setVisible(False)

    def _update_field_preview(self):
        from core.ability_effect_templates import (
            generate_field_code, FIELD_TEMPLATE_MAP,
        )
        tid = self.cmb_field_template.currentData()
        if not tid:
            self.lbl_field_preview.setVisible(False)
            self.lbl_field_notes.clear()
            self.lbl_field_notes.setVisible(False)
            return
        const = getattr(self, "_current_const", "") or "ABILITY_NEW"
        params = self._collect_field_params()
        code_blocks = generate_field_code(tid, const, params)
        if code_blocks:
            self.lbl_field_preview.setText(code_blocks[0][1])
            self.lbl_field_preview.setVisible(True)
        else:
            self.lbl_field_preview.setVisible(False)
        tmpl = FIELD_TEMPLATE_MAP.get(tid)
        note_text = _compose_template_notes(tmpl, params) if tmpl else ""
        if note_text:
            self.lbl_field_notes.setText(note_text)
            self.lbl_field_notes.setVisible(True)
        else:
            self.lbl_field_notes.clear()
            self.lbl_field_notes.setVisible(False)

    def get_battle_effect(self) -> tuple[str, dict]:
        """Return (template_id, params) for the current battle effect."""
        tid = self.cmb_battle_template.currentData() or ""
        return tid, self._collect_battle_params()

    def get_field_effect(self) -> tuple[str, dict]:
        """Return (template_id, params) for the current field effect."""
        tid = self.cmb_field_template.currentData() or ""
        return tid, self._collect_field_params()


# ── Add New Ability Dialog ───────────────────────────────────────────────────

class AddAbilityDialog(QDialog):
    """Dialog for creating a new ability.

    Offers two parallel ways to give the new ability behavior:

      1. Pick a **template** from the audited library (Stat Double, Type
         Resist, Type Encounter, Nature Sync, etc.) with inline parameter
         fields — fully covered by the test harness in
         `C:\\tmp\\porysuite-audit\\`.  This is the clean, build-safe path
         for a brand-new ability.
      2. **Copy from an existing ability** — replicates that ability's
         current C code under the new constant.  Useful for legacy / inline
         references that don't map to any template.

    A side with a template picked greys out the copy-from combo on that
    side (and vice versa), so the choice is unambiguous.  Both paths are
    optional — leave everything blank and you get a bare
    constant + name + description entry that can be customized later via
    the right-hand editor.

    The dialog itself NEVER writes to disk.  Template choices are stashed
    into `data["_battle_effect"]`/`data["_field_effect"]` so the normal
    save-abilities pipeline can inject C on the user's Save action.
    Copy-from still writes to disk immediately on OK (pre-existing
    behavior, unchanged).
    """

    def __init__(self, next_id: int, existing_constants: set[str],
                 abilities_data: dict | None = None, project_root: str = "",
                 parent=None):
        super().__init__(parent)
        self.setWindowTitle("Add New Ability")
        # Two-column body needs more horizontal room; cap vertical to the
        # user's screen so the OK/Cancel row can never slide off-screen on
        # a small monitor.  Content scrolls inside a QScrollArea below.
        self.setMinimumWidth(880)
        try:
            screen_h = self.screen().availableGeometry().height()
            self.setMaximumHeight(max(600, int(screen_h * 0.92)))
        except Exception:
            pass
        self._existing = existing_constants
        self._project_root = project_root
        self._battle_param_combos: dict[str, QComboBox] = {}
        self._field_param_combos: dict[str, QComboBox] = {}
        self._loading = True   # suppresses preview churn during __init__

        # Top-level dialog layout: scrollable body on top, button row pinned
        # at the bottom so OK/Cancel are always reachable no matter how tall
        # the content grows (long notes, large previews, etc.).
        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(6)

        # Scrollable content host — everything except the button box lives
        # inside here.
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        content = QWidget()
        content_layout = QVBoxLayout(content)
        content_layout.setContentsMargins(4, 4, 4, 4)
        content_layout.setSpacing(8)

        form = QFormLayout()
        form.setSpacing(8)

        # ── Display name (PRIMARY input — drives constant) ──────────────────
        name_row = QHBoxLayout()
        self.edit_name = QLineEdit()
        self.edit_name.setMaxLength(ABILITY_NAME_LENGTH)
        self.edit_name.setPlaceholderText("e.g. Thick Skin")
        name_row.addWidget(self.edit_name)
        self.lbl_counter = QLabel(f"0/{ABILITY_NAME_LENGTH}")
        self.lbl_counter.setStyleSheet("color: #555555; font-size: 10px;")
        name_row.addWidget(self.lbl_counter)
        self.edit_name.textChanged.connect(self._on_name_changed)
        form.addRow("Display Name:", name_row)

        # ── Auto-derived constant (read-only label, shows live preview) ─────
        self.lbl_const = QLabel("ABILITY_")
        self.lbl_const.setStyleSheet(
            "color: #999999; font-size: 11px; font-family: 'Courier New';"
            " padding: 2px 4px;"
        )
        self.lbl_const.setToolTip(
            "Auto-generated from the display name.\n"
            "Spaces become underscores, result is uppercased.\n"
            "e.g. 'Thick Skin' → ABILITY_THICK_SKIN"
        )
        form.addRow("Constant:", self.lbl_const)

        self.edit_desc = DexDescriptionEdit(
            max_chars_per_line=ABILITY_DESC_LENGTH,
            max_lines=1,
        )
        self.edit_desc.setFixedHeight(36)
        # QPlainTextEdit defaults to Expanding vertical policy, which makes
        # the form row soak up any extra dialog height when the template
        # preview below shrinks.  Pin it to Fixed so the row height stays
        # constant regardless of what happens elsewhere in the dialog.
        self.edit_desc.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.edit_desc.setFont(QFont("Courier New", 10))
        self.edit_desc.setPlaceholderText("Short description for summary screen")
        self.lbl_desc_counter = QLabel("0/%d" % ABILITY_DESC_LENGTH)
        self.lbl_desc_counter.setStyleSheet("color: #555555; font-size: 10px;")
        self.edit_desc.set_counter_label(self.lbl_desc_counter)
        desc_col = QVBoxLayout()
        desc_col.setContentsMargins(0, 0, 0, 0)
        desc_col.setSpacing(2)
        desc_col.addWidget(self.edit_desc)
        desc_col.addWidget(self.lbl_desc_counter)
        form.addRow("Description:", desc_col)

        self.lbl_id = QLabel(str(next_id))
        form.addRow("ID (auto):", self.lbl_id)

        content_layout.addLayout(form)

        # ── Battle + Field side-by-side columns ──────────────────────────────
        # The two groupboxes used to stack vertically, which made the dialog
        # taller than many screens once templates with long notes were
        # picked.  Side-by-side halves the height and keeps the OK/Cancel
        # button box reachable without scrolling on typical monitors.
        columns = QHBoxLayout()
        columns.setSpacing(10)

        from core.ability_effect_templates import (
            BATTLE_TEMPLATES, FIELD_TEMPLATES,
        )

        grp_battle = QGroupBox("Battle Effect (optional)")
        b_v = QVBoxLayout(grp_battle)
        b_v.setSpacing(4)

        b_tmpl_row = QHBoxLayout()
        b_tmpl_row.addWidget(QLabel("Template:"))
        self.cmb_battle_template = QComboBox()
        self.cmb_battle_template.wheelEvent = lambda e: e.ignore()
        self.cmb_battle_template.addItem("(none)", "")
        for tmpl in BATTLE_TEMPLATES:
            self.cmb_battle_template.addItem(tmpl.name, tmpl.id)
        self.cmb_battle_template.setToolTip(
            "Pick a battle effect template.  Code is only written when "
            "you Save the project — nothing touches disk right now."
        )
        self.cmb_battle_template.currentIndexChanged.connect(
            self._on_battle_template_changed)
        b_tmpl_row.addWidget(self.cmb_battle_template, 1)
        b_v.addLayout(b_tmpl_row)

        self._battle_params_widget = QWidget()
        self._battle_params_widget.setSizePolicy(
            QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed)
        self._battle_params_layout = QFormLayout(self._battle_params_widget)
        self._battle_params_layout.setContentsMargins(12, 0, 0, 0)
        self._battle_params_layout.setSpacing(3)
        self._battle_params_layout.setLabelAlignment(
            Qt.AlignmentFlag.AlignRight)
        b_v.addWidget(self._battle_params_widget)

        self.lbl_battle_tmpl_preview = QLabel("")
        self.lbl_battle_tmpl_preview.setWordWrap(True)
        self.lbl_battle_tmpl_preview.setSizePolicy(
            QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed)
        self.lbl_battle_tmpl_preview.setStyleSheet(
            "color: #888888; font-size: 9px; font-family: 'Courier New';"
            " padding: 2px 4px; background-color: #161616;"
            " border: 1px solid #2a2a2a; border-radius: 3px;"
        )
        self.lbl_battle_tmpl_preview.setVisible(False)
        b_v.addWidget(self.lbl_battle_tmpl_preview)

        # Implementation notes / prerequisites for the picked template.
        # Populated from EffectTemplate.notes + any param-specific warnings.
        self.lbl_battle_tmpl_notes = QLabel("")
        self.lbl_battle_tmpl_notes.setWordWrap(True)
        self.lbl_battle_tmpl_notes.setSizePolicy(
            QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed)
        self.lbl_battle_tmpl_notes.setStyleSheet(
            "color: #e8a838; font-size: 10px;"
            " padding: 6px 8px; background-color: #1f1810;"
            " border: 1px solid #3a2f18; border-radius: 3px;"
            " margin-top: 4px;"
        )
        self.lbl_battle_tmpl_notes.setVisible(False)
        b_v.addWidget(self.lbl_battle_tmpl_notes)

        b_or = QLabel("— or —")
        b_or.setAlignment(Qt.AlignmentFlag.AlignCenter)
        b_or.setStyleSheet("color: #666666; font-size: 10px; padding: 2px 0;")
        b_v.addWidget(b_or)

        b_copy_row = QHBoxLayout()
        b_copy_row.addWidget(QLabel("Copy from:"))
        self.cmb_battle = QComboBox()
        self.cmb_battle.wheelEvent = lambda e: e.ignore()
        self.cmb_battle.addItem("(none — no battle effect)", "")
        sorted_abs: list = []
        if abilities_data:
            sorted_abs = sorted(
                abilities_data.items(),
                key=lambda kv: kv[1].get("id", 0),
            )
            for const, data in sorted_abs:
                label = _BATTLE_CATEGORIES.get(const)
                if label:
                    display = data.get("display_name", data.get("name", const))
                    self.cmb_battle.addItem(
                        f"{display}  —  {label}", const
                    )
        self.cmb_battle.setToolTip(
            "Copy battle effect code (case blocks in battle_util.c, etc.)\n"
            "from an existing ability. Only abilities with known battle\n"
            "effects are listed. Use this for legacy/custom abilities that\n"
            "don't map to one of the templates above."
        )
        self.cmb_battle.currentIndexChanged.connect(self._update_battle_preview)
        self.cmb_battle.currentIndexChanged.connect(
            self._on_battle_copy_changed)
        b_copy_row.addWidget(self.cmb_battle, 1)
        b_v.addLayout(b_copy_row)

        self.lbl_battle_preview = QLabel("")
        self.lbl_battle_preview.setWordWrap(True)
        self.lbl_battle_preview.setStyleSheet(
            "color: #888888; font-size: 10px; padding: 0 0 4px 0;"
        )
        b_v.addWidget(self.lbl_battle_preview)
        b_v.addStretch(1)

        columns.addWidget(grp_battle, 1)

        # ── Field Effect group (template OR copy-from) ───────────────────────
        grp_field = QGroupBox("Field Effect (optional)")
        f_v = QVBoxLayout(grp_field)
        f_v.setSpacing(4)

        f_tmpl_row = QHBoxLayout()
        f_tmpl_row.addWidget(QLabel("Template:"))
        self.cmb_field_template = QComboBox()
        self.cmb_field_template.wheelEvent = lambda e: e.ignore()
        self.cmb_field_template.addItem("(none)", "")
        for tmpl in FIELD_TEMPLATES:
            self.cmb_field_template.addItem(tmpl.name, tmpl.id)
        self.cmb_field_template.setToolTip(
            "Pick a field/overworld effect template.  Code is only written "
            "when you Save the project — nothing touches disk right now."
        )
        self.cmb_field_template.currentIndexChanged.connect(
            self._on_field_template_changed)
        f_tmpl_row.addWidget(self.cmb_field_template, 1)
        f_v.addLayout(f_tmpl_row)

        self._field_params_widget = QWidget()
        self._field_params_widget.setSizePolicy(
            QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed)
        self._field_params_layout = QFormLayout(self._field_params_widget)
        self._field_params_layout.setContentsMargins(12, 0, 0, 0)
        self._field_params_layout.setSpacing(3)
        self._field_params_layout.setLabelAlignment(
            Qt.AlignmentFlag.AlignRight)
        f_v.addWidget(self._field_params_widget)

        self.lbl_field_tmpl_preview = QLabel("")
        self.lbl_field_tmpl_preview.setWordWrap(True)
        self.lbl_field_tmpl_preview.setSizePolicy(
            QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed)
        self.lbl_field_tmpl_preview.setStyleSheet(
            "color: #888888; font-size: 9px; font-family: 'Courier New';"
            " padding: 2px 4px; background-color: #161616;"
            " border: 1px solid #2a2a2a; border-radius: 3px;"
        )
        self.lbl_field_tmpl_preview.setVisible(False)
        f_v.addWidget(self.lbl_field_tmpl_preview)

        self.lbl_field_tmpl_notes = QLabel("")
        self.lbl_field_tmpl_notes.setWordWrap(True)
        self.lbl_field_tmpl_notes.setSizePolicy(
            QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed)
        self.lbl_field_tmpl_notes.setStyleSheet(
            "color: #e8a838; font-size: 10px;"
            " padding: 6px 8px; background-color: #1f1810;"
            " border: 1px solid #3a2f18; border-radius: 3px;"
            " margin-top: 4px;"
        )
        self.lbl_field_tmpl_notes.setVisible(False)
        f_v.addWidget(self.lbl_field_tmpl_notes)

        f_or = QLabel("— or —")
        f_or.setAlignment(Qt.AlignmentFlag.AlignCenter)
        f_or.setStyleSheet("color: #666666; font-size: 10px; padding: 2px 0;")
        f_v.addWidget(f_or)

        f_copy_row = QHBoxLayout()
        f_copy_row.addWidget(QLabel("Copy from:"))
        self.cmb_field = QComboBox()
        self.cmb_field.wheelEvent = lambda e: e.ignore()
        self.cmb_field.addItem("(none — no field effect)", "")
        if abilities_data:
            for const, data in sorted_abs:
                label = _FIELD_EFFECTS.get(const)
                if label:
                    display = data.get("display_name", data.get("name", const))
                    self.cmb_field.addItem(
                        f"{display}  —  {label}", const
                    )
        self.cmb_field.setToolTip(
            "Copy field/overworld effect code (encounter rate, egg hatching,\n"
            "etc.) from an existing ability.  Use this for abilities whose\n"
            "behavior isn't captured by one of the templates above."
        )
        self.cmb_field.currentIndexChanged.connect(self._update_field_preview)
        self.cmb_field.currentIndexChanged.connect(
            self._on_field_copy_changed)
        f_copy_row.addWidget(self.cmb_field, 1)
        f_v.addLayout(f_copy_row)

        self.lbl_field_preview = QLabel("")
        self.lbl_field_preview.setWordWrap(True)
        self.lbl_field_preview.setStyleSheet(
            "color: #888888; font-size: 10px; padding: 0 0 4px 0;"
        )
        f_v.addWidget(self.lbl_field_preview)
        f_v.addStretch(1)

        columns.addWidget(grp_field, 1)
        content_layout.addLayout(columns)

        self.lbl_hint = QLabel(
            "Template effects write C on Save (not now).  Copy-from writes "
            "C immediately on OK.  Leave everything (none) for a bare "
            "ability you'll customize later from the right-hand editor."
        )
        self.lbl_hint.setWordWrap(True)
        self.lbl_hint.setStyleSheet("color: #e8a838; font-size: 10px; padding: 6px 0;")
        content_layout.addWidget(self.lbl_hint)

        # Mount the content into the scroll area and the scroll area into
        # the dialog.  Button box goes OUTSIDE the scroll area so Ok/Cancel
        # are always visible regardless of content height.
        scroll.setWidget(content)
        layout.addWidget(scroll, 1)

        # Finish loading — now live previews can run.
        self._loading = False

        btns = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        btns.accepted.connect(self._validate_and_accept)
        btns.rejected.connect(self.reject)
        layout.addWidget(btns)

    def _on_name_changed(self, text: str):
        """Update the counter and auto-derive the constant from the name."""
        n = len(text)
        mx = ABILITY_NAME_LENGTH
        self.lbl_counter.setText(f"{n}/{mx}")
        self.lbl_counter.setStyleSheet(
            "color: #cc3333; font-size: 10px;" if n >= mx
            else "color: #555555; font-size: 10px;"
        )
        # Derive constant: "Thick Skin" → ABILITY_THICK_SKIN
        suffix = _name_to_constant_suffix(text)
        const = f"ABILITY_{suffix}" if suffix else "ABILITY_"
        self.lbl_const.setText(const)
        # Colour-code: red if duplicate, grey if empty, green if valid
        if not suffix:
            self.lbl_const.setStyleSheet(
                "color: #999999; font-size: 11px; font-family: 'Courier New'; padding: 2px 4px;"
            )
        elif const in self._existing:
            self.lbl_const.setStyleSheet(
                "color: #e57373; font-size: 11px; font-family: 'Courier New'; padding: 2px 4px;"
            )
        else:
            self.lbl_const.setStyleSheet(
                "color: #81c784; font-size: 11px; font-family: 'Courier New'; padding: 2px 4px;"
            )

    def _validate_and_accept(self):
        name = self.edit_name.text().strip()
        if not name:
            QMessageBox.warning(self, "Missing Fields",
                                "Display name is required.")
            return
        const = self.get_constant()
        if not const:
            QMessageBox.warning(self, "Invalid Name",
                                "Could not derive a constant from the name.")
            return
        if const in self._existing:
            QMessageBox.warning(self, "Duplicate",
                                f"{const} already exists. Choose a different name.")
            return
        self.accept()

    def get_constant(self) -> str:
        suffix = _name_to_constant_suffix(self.edit_name.text())
        if not suffix:
            return ""
        return "ABILITY_" + suffix

    def get_name(self) -> str:
        return self.edit_name.text().strip()

    def get_description(self) -> str:
        return self.edit_desc.toPlainText().strip()

    def get_id(self) -> int:
        return int(self.lbl_id.text())

    def get_battle_source(self) -> str:
        """Return the ABILITY_* constant to copy battle effects from, or ''."""
        return self.cmb_battle.currentData() or ""

    def get_field_source(self) -> str:
        """Return the ABILITY_* constant to copy field effects from, or ''."""
        return self.cmb_field.currentData() or ""

    def _update_battle_preview(self):
        """Show a preview of battle code for the selected source."""
        source = self.cmb_battle.currentData() or ""
        if not source or not self._project_root:
            self.lbl_battle_preview.setText("")
            return
        effects = scan_ability_battle_effects(self._project_root, source)
        n_case = len(effects["case_blocks"])
        n_inline = len(effects["inline_refs"])
        parts = []
        if n_case:
            files = sorted(set(f for f, *_ in effects["case_blocks"]))
            parts.append(f"{n_case} case block(s) in {', '.join(files)}")
        if n_inline:
            parts.append(f"{n_inline} inline ref(s)")
        self.lbl_battle_preview.setText("Will copy: " + "; ".join(parts) if parts else "No battle code found")

    def _update_field_preview(self):
        """Show a preview of field code for the selected source."""
        source = self.cmb_field.currentData() or ""
        if not source or not self._project_root:
            self.lbl_field_preview.setText("")
            return
        effects = scan_ability_field_effects(self._project_root, source)
        n_case = len(effects["case_blocks"])
        n_inline = len(effects["inline_refs"])
        parts = []
        if n_case:
            files = sorted(set(f for f, *_ in effects["case_blocks"]))
            parts.append(f"{n_case} case block(s) in {', '.join(files)}")
        if n_inline:
            files = sorted(set(f for f, *_ in effects["inline_refs"]))
            parts.append(f"{n_inline} inline check(s) in {', '.join(files)}")
        self.lbl_field_preview.setText("Will copy: " + "; ".join(parts) if parts else "No field code found")

    # ── template-picker plumbing ─────────────────────────────────────────────

    def _clear_param_combos(self, layout: QFormLayout,
                            combos: dict[str, QComboBox]):
        while layout.rowCount() > 0:
            layout.removeRow(0)
        combos.clear()

    def _rebuild_battle_tmpl_params(self, template_id: str):
        from core.ability_effect_templates import BATTLE_TEMPLATE_MAP
        self._clear_param_combos(
            self._battle_params_layout, self._battle_param_combos)
        tmpl = BATTLE_TEMPLATE_MAP.get(template_id)
        if not tmpl or not tmpl.params:
            return
        for param in tmpl.params:
            cmb = QComboBox()
            cmb.wheelEvent = lambda e: e.ignore()
            for display, value in param.choices:
                cmb.addItem(display, value)
            cmb.currentIndexChanged.connect(self._update_battle_tmpl_preview)
            self._battle_params_layout.addRow(param.label + ":", cmb)
            self._battle_param_combos[param.id] = cmb

    def _rebuild_field_tmpl_params(self, template_id: str):
        from core.ability_effect_templates import FIELD_TEMPLATE_MAP
        self._clear_param_combos(
            self._field_params_layout, self._field_param_combos)
        tmpl = FIELD_TEMPLATE_MAP.get(template_id)
        if not tmpl or not tmpl.params:
            return
        for param in tmpl.params:
            cmb = QComboBox()
            cmb.wheelEvent = lambda e: e.ignore()
            for display, value in param.choices:
                cmb.addItem(display, value)
            cmb.currentIndexChanged.connect(self._update_field_tmpl_preview)
            self._field_params_layout.addRow(param.label + ":", cmb)
            self._field_param_combos[param.id] = cmb

    def _collect_battle_tmpl_params(self) -> dict:
        return {pid: cmb.currentData()
                for pid, cmb in self._battle_param_combos.items()}

    def _collect_field_tmpl_params(self) -> dict:
        return {pid: cmb.currentData()
                for pid, cmb in self._field_param_combos.items()}

    def _update_battle_tmpl_preview(self):
        from core.ability_effect_templates import (
            generate_battle_code, BATTLE_TEMPLATE_MAP,
        )
        tid = self.cmb_battle_template.currentData()
        if not tid:
            self.lbl_battle_tmpl_preview.clear()
            self.lbl_battle_tmpl_preview.setVisible(False)
            self.lbl_battle_tmpl_notes.clear()
            self.lbl_battle_tmpl_notes.setVisible(False)
            return
        const = self.get_constant() or "ABILITY_NEW"
        params = self._collect_battle_tmpl_params()
        blocks = generate_battle_code(tid, const, params)
        if blocks:
            preview = blocks[0][1]
            lines = preview.split("\n")
            if len(lines) > 8:
                preview = "\n".join(lines[:8]) + "\n  ..."
            self.lbl_battle_tmpl_preview.setText(preview)
            self.lbl_battle_tmpl_preview.setVisible(True)
        else:
            self.lbl_battle_tmpl_preview.clear()
            self.lbl_battle_tmpl_preview.setVisible(False)
        # Notes (implementation guidance for this template / params).
        tmpl = BATTLE_TEMPLATE_MAP.get(tid)
        note_text = self._compose_template_notes(tmpl, params) if tmpl else ""
        if note_text:
            self.lbl_battle_tmpl_notes.setText(note_text)
            self.lbl_battle_tmpl_notes.setVisible(True)
        else:
            self.lbl_battle_tmpl_notes.clear()
            self.lbl_battle_tmpl_notes.setVisible(False)

    def _update_field_tmpl_preview(self):
        from core.ability_effect_templates import (
            generate_field_code, FIELD_TEMPLATE_MAP,
        )
        tid = self.cmb_field_template.currentData()
        if not tid:
            self.lbl_field_tmpl_preview.clear()
            self.lbl_field_tmpl_preview.setVisible(False)
            self.lbl_field_tmpl_notes.clear()
            self.lbl_field_tmpl_notes.setVisible(False)
            return
        const = self.get_constant() or "ABILITY_NEW"
        params = self._collect_field_tmpl_params()
        blocks = generate_field_code(tid, const, params)
        if blocks:
            preview = blocks[0][1]
            lines = preview.split("\n")
            if len(lines) > 8:
                preview = "\n".join(lines[:8]) + "\n  ..."
            self.lbl_field_tmpl_preview.setText(preview)
            self.lbl_field_tmpl_preview.setVisible(True)
        else:
            self.lbl_field_tmpl_preview.clear()
            self.lbl_field_tmpl_preview.setVisible(False)
        tmpl = FIELD_TEMPLATE_MAP.get(tid)
        note_text = self._compose_template_notes(tmpl, params) if tmpl else ""
        if note_text:
            self.lbl_field_tmpl_notes.setText(note_text)
            self.lbl_field_tmpl_notes.setVisible(True)
        else:
            self.lbl_field_tmpl_notes.clear()
            self.lbl_field_tmpl_notes.setVisible(False)

    def _compose_template_notes(self, tmpl, params: dict) -> str:
        """Thin wrapper around the module-level helper so the Add dialog and
        the right-hand editor show identical guidance."""
        return _compose_template_notes(tmpl, params)

    def _resize_to_content(self):
        """No-op now that the dialog body lives inside a QScrollArea.

        Previously this method would clamp the dialog height to the layout
        sizeHint so the description row didn't stretch when the preview
        label shrank.  With the scroll-area + two-column refactor the
        dialog height is already bounded (screen-height cap set in
        __init__) and the scroll area absorbs any extra content, so no
        manual resizing is needed.  Kept as a hook in case future template
        changes need to trigger a geometry update.
        """
        return

    def _on_battle_template_changed(self, _idx: int):
        tid = self.cmb_battle_template.currentData() or ""
        self._rebuild_battle_tmpl_params(tid)
        self._update_battle_tmpl_preview()
        # Mutual exclusion: if a template is picked, grey out the copy-from
        # combo so the user can't accidentally double-up.
        self._sync_battle_mutual_exclusion()
        self._resize_to_content()

    def _on_field_template_changed(self, _idx: int):
        tid = self.cmb_field_template.currentData() or ""
        self._rebuild_field_tmpl_params(tid)
        self._update_field_tmpl_preview()
        self._sync_field_mutual_exclusion()
        self._resize_to_content()

    def _on_battle_copy_changed(self, _idx: int):
        self._sync_battle_mutual_exclusion()

    def _on_field_copy_changed(self, _idx: int):
        self._sync_field_mutual_exclusion()

    def _sync_battle_mutual_exclusion(self):
        """Enable/disable battle template vs copy-from based on which has a
        non-empty selection.  The two paths are mutually exclusive."""
        if self._loading:
            return
        tmpl_active = bool(self.cmb_battle_template.currentData())
        copy_active = bool(self.cmb_battle.currentData())
        self.cmb_battle.setEnabled(not tmpl_active)
        self.cmb_battle_template.setEnabled(not copy_active)
        self._battle_params_widget.setEnabled(tmpl_active and not copy_active)

    def _sync_field_mutual_exclusion(self):
        if self._loading:
            return
        tmpl_active = bool(self.cmb_field_template.currentData())
        copy_active = bool(self.cmb_field.currentData())
        self.cmb_field.setEnabled(not tmpl_active)
        self.cmb_field_template.setEnabled(not copy_active)
        self._field_params_widget.setEnabled(tmpl_active and not copy_active)

    def get_battle_template(self) -> tuple[str, dict]:
        """Return (template_id, params) for the battle-effect template the
        user picked, or ('', {}) if they didn't.  Never writes to disk —
        the caller stashes this into data["_battle_effect"] and the
        save-abilities pipeline applies it on the user's Save action."""
        tid = self.cmb_battle_template.currentData() or ""
        if not tid:
            return "", {}
        return tid, self._collect_battle_tmpl_params()

    def get_field_template(self) -> tuple[str, dict]:
        tid = self.cmb_field_template.currentData() or ""
        if not tid:
            return "", {}
        return tid, self._collect_field_tmpl_params()


# ── AbilitiesTabWidget (main widget) ────────────────────────────────────────

class AbilitiesTabWidget(QWidget):
    """
    Full abilities tab: searchable list on the left, detail panel on the right.
    Signals `data_changed` whenever the user edits a field.
    """

    data_changed = pyqtSignal()
    rename_requested = pyqtSignal(str)         # old_const → opens RenameDialog in mainwindow
    species_jump_requested = pyqtSignal(str)  # species constant → jump to Pokemon tab

    def __init__(self, parent=None):
        super().__init__(parent)
        self._abilities_data: dict = {}       # const → {id, name, display_name, description}
        self._species_data: dict = {}         # species_const → {abilities: [...], name: ...}
        self._project_root: str = ""
        self._current_ability: str | None = None
        self._dirty = False
        self._loading = False
        self._new_abilities: set = set()
        self._deleted_abilities: set = set()
        # Ability constants whose row should paint amber in the list.
        # Survives `_rebuild_list`'s `self._list.clear()` — the rebuild
        # copies each const's flag back onto the fresh QListWidgetItem so
        # newly-added rows don't lose their amber tint after the add path
        # triggers a rebuild.  Cleared on save via `clear_all_dirty`.
        self._dirty_consts: set[str] = set()
        self._build_ui()

    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        splitter = QSplitter(Qt.Orientation.Horizontal)
        layout.addWidget(splitter)

        # ── Left: list panel ────────────────────────────────────────────────
        left = QWidget()
        left_v = QVBoxLayout(left)
        left_v.setContentsMargins(0, 0, 0, 0)
        left_v.setSpacing(0)

        self._search = QLineEdit()
        self._search.setPlaceholderText("Search abilities...")
        self._search.setClearButtonEnabled(True)
        self._search.setStyleSheet(_SEARCH_SS)
        self._search.textChanged.connect(self._filter_list)
        left_v.addWidget(self._search)

        # Add / Delete buttons
        btn_row = QHBoxLayout()
        btn_row.setContentsMargins(4, 3, 4, 3)
        btn_row.setSpacing(4)

        self.btn_add = QPushButton("+ Add")
        self.btn_add.setStyleSheet(_BTN_SS)
        self.btn_add.setToolTip("Create a new ability")
        self.btn_add.clicked.connect(self._on_add)
        btn_row.addWidget(self.btn_add)

        self.btn_rename = QPushButton("\u270e Rename\u2026")
        self.btn_rename.setStyleSheet(_BTN_SS)
        self.btn_rename.setToolTip("Rename this ability's constant and display name across the project")
        self.btn_rename.clicked.connect(self._on_rename)
        btn_row.addWidget(self.btn_rename)

        self.btn_duplicate = QPushButton("Duplicate")
        self.btn_duplicate.setStyleSheet(_BTN_SS)
        self.btn_duplicate.setToolTip("Copy the selected ability into a new one (including battle effects)")
        self.btn_duplicate.clicked.connect(self._on_duplicate)
        btn_row.addWidget(self.btn_duplicate)

        self.btn_delete = QPushButton("Delete")
        self.btn_delete.setStyleSheet(_BTN_SS)
        self.btn_delete.setToolTip("Delete the selected ability (if unused)")
        self.btn_delete.clicked.connect(self._on_delete)
        btn_row.addWidget(self.btn_delete)

        self._count_lbl = QLabel()
        self._count_lbl.setStyleSheet(
            "color: #555555; font-size: 10px; padding: 0 4px;"
        )
        self._count_lbl.setAlignment(
            Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
        )
        btn_row.addWidget(self._count_lbl)

        left_v.addLayout(btn_row)

        self._list = QListWidget()
        self._list.setStyleSheet(_LIST_SS)
        self._list.setMinimumWidth(220)
        self._list.currentRowChanged.connect(self._on_row_changed)
        left_v.addWidget(self._list)

        splitter.addWidget(left)

        # ── Right: detail panel ─────────────────────────────────────────────
        self._detail = AbilityDetailPanel()
        self._detail.changed.connect(self._on_detail_changed)
        self._detail.tbl_species.cellDoubleClicked.connect(self._on_species_dblclick)
        splitter.addWidget(self._detail)

        splitter.setStretchFactor(0, 0)
        splitter.setStretchFactor(1, 1)

    # ── public API ────────────────────────────────────────────────────────────

    def load_abilities(self, abilities: dict, species_data: dict = None,
                       project_root: str = "") -> None:
        """
        Populate the widget.
        abilities    — {ABILITY_CONST: {id, name, display_name, description}}
        species_data — {SPECIES_CONST: {abilities: [ABILITY_X, ABILITY_Y], ...}}
        project_root — path to pokefirered repo root (for battle effect scanning)
        """
        self._abilities_data = abilities
        self._species_data = species_data or {}
        self._project_root = project_root or ""
        self._rebuild_list()
        if self._list.count():
            self._list.setCurrentRow(0)

    def save_current(self) -> None:
        """Flush current detail panel edits back to internal dict."""
        if not self._dirty or self._current_ability is None:
            return
        _const, name, desc = self._detail.collect()
        orig_const = self._current_ability
        if orig_const in self._abilities_data:
            self._abilities_data[orig_const]["display_name"] = name
            self._abilities_data[orig_const]["description"] = desc

            # Store effect configuration
            btid, bparams = self._detail.get_battle_effect()
            ftid, fparams = self._detail.get_field_effect()
            self._abilities_data[orig_const]["_battle_effect"] = (btid, bparams)
            self._abilities_data[orig_const]["_field_effect"] = (ftid, fparams)

        self._dirty = False

    def get_abilities_data(self) -> dict:
        return self._abilities_data

    def get_new_abilities(self) -> set:
        return set(self._new_abilities)

    def get_deleted_abilities(self) -> set:
        return set(self._deleted_abilities)

    def clear_new_abilities(self) -> None:
        self._new_abilities.clear()

    def clear_deleted_abilities(self) -> None:
        self._deleted_abilities.clear()

    def clear_all_dirty(self) -> None:
        """Wipe every amber-row marker and the tracking set.

        Called by the main window's `_clear_all_dirty_markers` after a
        successful save.  The shared `_mark_list_item_dirty` helper also
        clears the role on existing items, but we clear the tracking set
        here too so a later `_rebuild_list` (e.g. after an add/delete)
        doesn't re-tint rows that were just saved.
        """
        self._dirty_consts.clear()
        try:
            for i in range(self._list.count()):
                item = self._list.item(i)
                if item is not None:
                    item.setData(DIRTY_FLAG_ROLE, None)
        except Exception:
            pass

    def apply_effect_changes(self) -> list[str]:
        """Write all pending effect changes to C source files.

        Called by the main window save pipeline after save_current().
        Returns a list of summary messages about what was written.
        """
        from core.ability_effect_templates import (
            apply_battle_effect, apply_field_effect,
            remove_battle_effect, remove_field_effect,
            detect_all_effects,
        )
        if not self._project_root:
            return []

        messages: list[str] = []
        for const, data in self._abilities_data.items():
            battle_cfg = data.get("_battle_effect")
            field_cfg = data.get("_field_effect")
            if battle_cfg is None and field_cfg is None:
                continue  # Not edited in this session

            # Detect what's currently in the source
            current_battle, current_field = detect_all_effects(
                self._project_root, const)
            cur_btid = current_battle[0] if current_battle else ""
            cur_ftid = current_field[0] if current_field else ""

            # ── Battle effect ──
            if battle_cfg is not None:
                new_btid, new_bparams = battle_cfg
                cur_bparams = current_battle[1] if current_battle else {}
                # Trigger write if template changed, params changed, or
                # user selected a template (even same one — ensures code
                # is written for abilities that had no effect before).
                battle_changed = (new_btid != cur_btid
                                  or new_bparams != cur_bparams)
                if battle_changed or (new_btid and not cur_btid):
                    # Remove old effect before writing new one
                    if cur_btid:
                        remove_battle_effect(self._project_root, const)
                    # Apply new
                    if new_btid:
                        n = apply_battle_effect(
                            self._project_root, new_btid, const, new_bparams)
                        if n > 0:
                            messages.append(
                                f"{const}: wrote {n} battle effect block(s)")
                    elif cur_btid:
                        messages.append(
                            f"{const}: removed battle effect")

            # ── Field effect ──
            if field_cfg is not None:
                new_ftid, new_fparams = field_cfg
                cur_fparams = current_field[1] if current_field else {}
                field_changed = (new_ftid != cur_ftid
                                 or new_fparams != cur_fparams)
                if field_changed or (new_ftid and not cur_ftid):
                    if cur_ftid:
                        remove_field_effect(self._project_root, const)
                    if new_ftid:
                        n = apply_field_effect(
                            self._project_root, new_ftid, const, new_fparams)
                        if n > 0:
                            messages.append(
                                f"{const}: wrote {n} field effect block(s)")
                    elif cur_ftid:
                        messages.append(
                            f"{const}: removed field effect")

            # Clear the stored config so we don't re-apply next save
            data.pop("_battle_effect", None)
            data.pop("_field_effect", None)

        return messages

    def has_unsaved_changes(self) -> bool:
        return self._dirty

    # ── list management ──────────────────────────────────────────────────────

    def _rebuild_list(self):
        self._list.blockSignals(True)
        self._list.clear()
        sorted_abilities = sorted(
            self._abilities_data.items(),
            key=lambda kv: kv[1].get("id", 0),
        )
        for const, data in sorted_abilities:
            # ABILITY_NONE is the sentinel "no ability" slot used throughout
            # the source and by the species editor's "empty" dropdown option.
            # It must stay in `_abilities_data` (the save path writes it as
            # #define ABILITY_NONE 0) but should NOT appear in the editable
            # list — there's nothing for the user to edit on it and the
            # species-usage pane for "NONE" would list every ability-less
            # species in the game.
            if const == "ABILITY_NONE" or data.get("id", 0) == 0:
                continue
            display = data.get("display_name", data.get("name", const))
            aid = data.get("id", "?")
            item = QListWidgetItem(f"{aid:>3}  {display}")
            item.setData(Qt.ItemDataRole.UserRole, const)
            # Re-apply the amber dirty role if this const was dirty before
            # the rebuild — otherwise clear() would wipe the tint.  Matches
            # the role the shared _DirtyDelegate in mainwindow paints.
            if const in self._dirty_consts:
                item.setData(DIRTY_FLAG_ROLE, True)
            self._list.addItem(item)
        self._count_lbl.setText(f"{self._list.count()} abilities")
        self._list.blockSignals(False)

    def _filter_list(self, text: str = ""):
        needle = text.lower()
        for i in range(self._list.count()):
            item = self._list.item(i)
            const = item.data(Qt.ItemDataRole.UserRole) or ""
            display = item.text().lower()
            visible = needle in display or needle in const.lower()
            item.setHidden(not visible)

    def _on_row_changed(self, row: int):
        if self._loading:
            return
        # Save previous
        if self._dirty and self._current_ability is not None:
            self.save_current()

        item = self._list.item(row)
        if item is None:
            self._detail.clear()
            self._current_ability = None
            return

        const = item.data(Qt.ItemDataRole.UserRole)
        data = self._abilities_data.get(const, {})
        self._current_ability = const

        # Build species usage list
        usage = self._get_species_usage(const)
        self._detail.load(const, data, usage,
                          project_root=self._project_root)

    def _get_species_usage(self, ability_const: str) -> list[tuple[str, str, str]]:
        """Return list of (species_display_name, slot_label, species_const) for this ability."""
        usage: list[tuple[str, str, str]] = []
        for sp_const, sp_data in self._species_data.items():
            abilities = sp_data.get("abilities", [])
            sp_name = sp_data.get("name", sp_const)
            for i, ab in enumerate(abilities):
                if ab == ability_const:
                    slot = "Primary" if i == 0 else "Secondary"
                    usage.append((sp_name, slot, sp_const))
        usage.sort(key=lambda t: t[0])
        return usage

    # ── add / delete ─────────────────────────────────────────────────────────

    def _next_id(self) -> int:
        if not self._abilities_data:
            return 0
        return max(d.get("id", 0) for d in self._abilities_data.values()) + 1

    def _on_add(self):
        next_id = self._next_id()
        dlg = AddAbilityDialog(
            next_id,
            set(self._abilities_data.keys()),
            abilities_data=self._abilities_data,
            project_root=self._project_root,
            parent=self,
        )
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return

        const = dlg.get_constant()
        name = dlg.get_name()
        desc = dlg.get_description()
        aid = dlg.get_id()
        battle_src = dlg.get_battle_source()
        field_src = dlg.get_field_source()
        btid, bparams = dlg.get_battle_template()
        ftid, fparams = dlg.get_field_template()

        self._abilities_data[const] = {
            "name": const[len("ABILITY_"):] if const.startswith("ABILITY_") else const,
            "id": aid,
            "display_name": name,
            "description": desc,
        }
        # RAM-only stash for template-based effects.  Actual C injection
        # happens on Save in `apply_effect_changes`; nothing touches disk
        # now.  Only set the key if a template was picked — the absence of
        # a key means "no pending change" to the save pipeline, which is
        # the correct signal for a brand-new ability with no effect.
        if btid:
            self._abilities_data[const]["_battle_effect"] = (btid, bparams)
        if ftid:
            self._abilities_data[const]["_field_effect"] = (ftid, fparams)
        self._new_abilities.add(const)
        # Mark the new ability's row dirty so the amber tint shows up.
        # _dirty_consts survives `_rebuild_list` (which wipes items and
        # their roles); the rebuild path re-reads it and sets the role on
        # every fresh item whose const is in the set.
        self._dirty_consts.add(const)

        # Copy-from path.  Previously this wrote C files to disk the moment
        # the user clicked OK on the Add dialog — inconsistent with every
        # other editor in the tool, which defers to the toolbar Save button.
        # Now we just stash the source constant on the ability dict and let
        # the Save pipeline (`apply_effect_changes`) run the real copy.
        # Template takes precedence over copy-from; the dialog's mutual
        # exclusion greys out the other combo but we re-enforce it here as
        # a safety net so a template+copy combo never double-applies.
        effective_battle_src = "" if btid else battle_src
        effective_field_src = "" if ftid else field_src
        if effective_battle_src:
            self._abilities_data[const]["_battle_copy_from"] = effective_battle_src
        if effective_field_src:
            self._abilities_data[const]["_field_copy_from"] = effective_field_src

        self._dirty = True
        self.data_changed.emit()
        self._rebuild_list()

        # Select the new ability
        for i in range(self._list.count()):
            if self._list.item(i).data(Qt.ItemDataRole.UserRole) == const:
                self._list.setCurrentRow(i)
                break

    def _on_duplicate(self):
        """Duplicate the currently selected ability into a new one."""
        if self._current_ability is None:
            return
        src_const = self._current_ability
        src_data = self._abilities_data.get(src_const, {})

        next_id = self._next_id()
        dlg = AddAbilityDialog(
            next_id,
            set(self._abilities_data.keys()),
            abilities_data=self._abilities_data,
            project_root=self._project_root,
            parent=self,
        )
        # Pre-fill from source — name drives the constant automatically
        src_name = src_data.get("display_name", src_data.get("name", ""))
        dlg.edit_name.setText(src_name + " Copy" if src_name else "")
        dlg.edit_desc.setText(src_data.get("description", ""))
        # Pre-select the source ability in the battle/field dropdowns if applicable
        if src_const in _BATTLE_CATEGORIES:
            for i in range(dlg.cmb_battle.count()):
                if dlg.cmb_battle.itemData(i) == src_const:
                    dlg.cmb_battle.setCurrentIndex(i)
                    break
        if src_const in _FIELD_EFFECTS:
            for i in range(dlg.cmb_field.count()):
                if dlg.cmb_field.itemData(i) == src_const:
                    dlg.cmb_field.setCurrentIndex(i)
                    break

        if dlg.exec() != QDialog.DialogCode.Accepted:
            return

        const = dlg.get_constant()
        name = dlg.get_name()
        desc = dlg.get_description()
        aid = dlg.get_id()
        battle_src = dlg.get_battle_source()
        field_src = dlg.get_field_source()
        btid, bparams = dlg.get_battle_template()
        ftid, fparams = dlg.get_field_template()

        self._abilities_data[const] = {
            "name": const[len("ABILITY_"):] if const.startswith("ABILITY_") else const,
            "id": aid,
            "display_name": name,
            "description": desc,
        }
        if btid:
            self._abilities_data[const]["_battle_effect"] = (btid, bparams)
        if ftid:
            self._abilities_data[const]["_field_effect"] = (ftid, fparams)
        self._new_abilities.add(const)
        self._dirty_consts.add(const)

        effective_battle_src = "" if btid else battle_src
        effective_field_src = "" if ftid else field_src
        if effective_battle_src or effective_field_src:
            self._apply_effect_copy(
                const, effective_battle_src, effective_field_src)

        self._dirty = True
        self.data_changed.emit()
        self._rebuild_list()

        for i in range(self._list.count()):
            if self._list.item(i).data(Qt.ItemDataRole.UserRole) == const:
                self._list.setCurrentRow(i)
                break

    def _apply_effect_copy(self, new_const: str, battle_src: str, field_src: str):
        """Copy battle and/or field effects from source abilities."""
        if not self._project_root:
            return
        if not battle_src and not field_src:
            return

        msg_parts = []

        # ── Battle effects ──
        if battle_src:
            copied = copy_battle_effects(self._project_root, battle_src, new_const)
            effects = scan_ability_battle_effects(self._project_root, battle_src)
            inline_refs = effects.get("inline_refs", [])

            if copied > 0:
                msg_parts.append(
                    f"Battle: Copied {copied} case block(s) from {battle_src}."
                )
            else:
                msg_parts.append(
                    f"Battle: No case blocks found for {battle_src}."
                )
            if inline_refs:
                msg_parts.append(
                    f"  {len(inline_refs)} inline battle reference(s) may need "
                    "manual duplication."
                )

        # ── Field effects ──
        if field_src:
            copied, remaining = copy_field_effects(
                self._project_root, field_src, new_const
            )
            if copied > 0:
                msg_parts.append(
                    f"\nField: Copied {copied} field effect(s) from {field_src}."
                )
            else:
                msg_parts.append(
                    f"\nField: No field effect code found for {field_src}."
                )
            if remaining:
                msg_parts.append(
                    f"  {len(remaining)} inline field reference(s) may need "
                    "manual review:\n" + build_inline_ref_summary(remaining)
                )

        QMessageBox.information(
            self, "Effect Copying Results",
            "\n".join(msg_parts),
        )

    def _on_delete(self):
        if self._current_ability is None:
            return
        const = self._current_ability

        # Safety: check species usage
        usage = self._get_species_usage(const)
        if usage:
            species_list = ", ".join(name for name, *_ in usage[:10])
            extra = f" and {len(usage) - 10} more" if len(usage) > 10 else ""
            QMessageBox.warning(
                self, "Cannot Delete",
                f"{const} is used by {len(usage)} species:\n"
                f"{species_list}{extra}\n\n"
                "Remove it from all species first."
            )
            return

        ret = QMessageBox.question(
            self, "Delete Ability",
            f"Delete {const}?\n\n"
            "This removes the constant, display name, and description. "
            "You may also need to remove references in battle_util.c manually.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if ret != QMessageBox.StandardButton.Yes:
            return

        self._abilities_data.pop(const, None)
        self._deleted_abilities.add(const)
        self._new_abilities.discard(const)
        self._dirty_consts.discard(const)
        self._current_ability = None
        self._dirty = True
        self.data_changed.emit()
        self._rebuild_list()
        if self._list.count():
            self._list.setCurrentRow(0)

    def _on_rename(self):
        """Emit rename signal for the currently selected ability."""
        if self._current_ability is None:
            return
        # Flush any pending edits first
        if self._dirty:
            self.save_current()
        self.rename_requested.emit(self._current_ability)

    def rename_ability_key(self, old_const: str, new_const: str):
        """Rename an ability key in the internal data dict (called after refactor)."""
        if old_const in self._abilities_data and old_const != new_const:
            self._abilities_data[new_const] = self._abilities_data.pop(old_const)
            if self._current_ability == old_const:
                self._current_ability = new_const
            # Keep the dirty set in sync with the rekey so rebuild still
            # paints amber on a renamed-but-unsaved ability.
            if old_const in self._dirty_consts:
                self._dirty_consts.discard(old_const)
                self._dirty_consts.add(new_const)
            if old_const in self._new_abilities:
                self._new_abilities.discard(old_const)
                self._new_abilities.add(new_const)
            self._rebuild_list()
            # Re-select the renamed ability
            for i in range(self._list.count()):
                item = self._list.item(i)
                if item and item.data(Qt.ItemDataRole.UserRole) == new_const:
                    self._list.setCurrentRow(i)
                    break

    # ── detail edits ─────────────────────────────────────────────────────────

    def _on_detail_changed(self):
        if not self._loading:
            self._dirty = True
            if self._current_ability:
                # Keep the dirty set in sync with edits to existing
                # abilities so any future `_rebuild_list` preserves the
                # amber tint.  The shared `_mark_list_item_dirty` in
                # mainwindow still handles the immediate paint.
                self._dirty_consts.add(self._current_ability)
            self.data_changed.emit()

    def _on_species_dblclick(self, row: int, _col: int):
        """Double-click a species row → request jump to Pokemon tab."""
        consts = getattr(self._detail, "_species_constants", [])
        if 0 <= row < len(consts):
            self.species_jump_requested.emit(consts[row])
