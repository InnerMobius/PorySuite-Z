"""Trainer Class Editor — view and edit trainer class names, money multipliers,
and see associated sprites, battle music, terrain, and facility class info.
Lives as a tab alongside the Trainers editor."""

from __future__ import annotations

import logging
import os
import re
from collections import Counter
from typing import Optional

from PyQt6.QtCore import Qt, QSize, pyqtSignal
from PyQt6.QtGui import QColor, QIcon, QPixmap
from PyQt6.QtWidgets import (
    QComboBox, QDialog, QDialogButtonBox, QFormLayout, QFrame,
    QGroupBox, QHBoxLayout, QLabel, QLineEdit, QListWidget,
    QListWidgetItem, QMessageBox, QPushButton, QScrollArea,
    QSpinBox, QSplitter, QVBoxLayout, QWidget,
)

# Shared sprite-rendering + cross-tab palette propagation. Trainer pics
# are indexed 4bpp PNGs with a separate .pal file — a flat QPixmap(path)
# read shows stale colours the moment the user edits the palette.
from core.sprite_render import load_sprite_pixmap
from core.sprite_palette_bus import (
    get_bus as _get_palette_bus, CAT_TRAINER_PIC,
)

log = logging.getLogger(__name__)

# ── Dark-theme styles (match the rest of PorySuite-Z) ────────────────────────

_LIST_SS = """
QListWidget { background: #191919; border: none; outline: none; }
QListWidget::item { border-bottom: 1px solid #1f1f1f; }
QListWidget::item:selected { background: #1565c0; }
"""

_GROUP_SS = """
QGroupBox {
    color: #999;
    font-size: 10px;
    font-weight: bold;
    border: 1px solid #2e2e2e;
    border-radius: 4px;
    margin-top: 8px;
    padding: 12px 8px 8px 8px;
}
QGroupBox::title {
    subcontrol-origin: margin;
    subcontrol-position: top left;
    padding: 0 6px;
}
"""

# ── Battle music / terrain categories (from switch statements in C code) ─────

# Classes that get special victory music (MUS_VICTORY_GYM_LEADER)
_VICTORY_GYM_LEADER = {"TRAINER_CLASS_LEADER", "TRAINER_CLASS_CHAMPION"}

# Battle BGM categories (from GetBattleBGM in pokemon.c)
_BGM_CHAMPION = {"TRAINER_CLASS_CHAMPION"}
_BGM_GYM_LEADER = {"TRAINER_CLASS_LEADER", "TRAINER_CLASS_ELITE_FOUR"}
# Everything else gets MUS_VS_TRAINER

# Battle terrain overrides (from GetBattleTerrainOverride in battle_bg.c)
_TERRAIN_LEADER = {"TRAINER_CLASS_LEADER"}
_TERRAIN_CHAMPION = {"TRAINER_CLASS_CHAMPION"}

# Encounter music human-readable names
_ENCOUNTER_MUSIC_LABELS = {
    "TRAINER_ENCOUNTER_MUSIC_MALE": "Male",
    "TRAINER_ENCOUNTER_MUSIC_FEMALE": "Female",
    "TRAINER_ENCOUNTER_MUSIC_GIRL": "Girl / Tuber",
    "TRAINER_ENCOUNTER_MUSIC_SUSPICIOUS": "Suspicious",
    "TRAINER_ENCOUNTER_MUSIC_INTENSE": "Intense",
    "TRAINER_ENCOUNTER_MUSIC_COOL": "Cool",
    "TRAINER_ENCOUNTER_MUSIC_AQUA": "Team Aqua",
    "TRAINER_ENCOUNTER_MUSIC_MAGMA": "Team Magma",
    "TRAINER_ENCOUNTER_MUSIC_SWIMMER": "Swimmer",
    "TRAINER_ENCOUNTER_MUSIC_TWINS": "Twins",
    "TRAINER_ENCOUNTER_MUSIC_ELITE_FOUR": "Elite Four",
    "TRAINER_ENCOUNTER_MUSIC_HIKER": "Hiker",
    "TRAINER_ENCOUNTER_MUSIC_INTERVIEWER": "Interviewer",
    "TRAINER_ENCOUNTER_MUSIC_RICH": "Rich",
}


class _NoScrollCombo(QComboBox):
    """QComboBox that ignores wheel events when the popup isn't showing."""
    def wheelEvent(self, event):
        if self.view().isVisible():
            super().wheelEvent(event)
        else:
            event.ignore()


class _NoScrollSpin(QSpinBox):
    """QSpinBox that ignores wheel events unless it has focus."""
    def wheelEvent(self, event):
        if self.hasFocus():
            super().wheelEvent(event)
        else:
            event.ignore()


# ── Parsers ──────────────────────────────────────────────────────────────────

def _parse_trainer_classes(root: str) -> dict[str, int]:
    """Return {TRAINER_CLASS_CONST: numeric_value} from trainers.h."""
    path = os.path.join(root, "include", "constants", "trainers.h")
    result: dict[str, int] = {}
    if not os.path.isfile(path):
        return result
    pat = re.compile(r"#define\s+(TRAINER_CLASS_\w+)\s+(\d+)")
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            for line in f:
                m = pat.search(line)
                if m:
                    result[m.group(1)] = int(m.group(2))
    except Exception as exc:
        log.warning("_parse_trainer_classes: %s", exc)
    return result


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


def _parse_money_table(root: str) -> dict[str, int]:
    """Return {TRAINER_CLASS_CONST: money_multiplier} from gTrainerMoneyTable
    in battle_main.c."""
    path = os.path.join(root, "src", "battle_main.c")
    if not os.path.isfile(path):
        return {}
    result: dict[str, int] = {}
    pat = re.compile(r"\{\s*(\w+)\s*,\s*(\d+)\s*\}")
    in_table = False
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            for line in f:
                if "gTrainerMoneyTable" in line:
                    in_table = True
                    continue
                if in_table:
                    if line.strip().startswith("};"):
                        break
                    m = pat.search(line)
                    if m and m.group(1) != "0xFF":
                        result[m.group(1)] = int(m.group(2))
    except Exception as exc:
        log.warning("_parse_money_table: %s", exc)
    return result


def _parse_facility_lookups(root: str) -> tuple[dict, dict]:
    """Parse trainer_class_lookups.h and return:
    (fac_to_pic: {FAC_CONST: PIC_CONST}, fac_to_class: {FAC_CONST: CLASS_CONST})
    """
    path = os.path.join(root, "src", "data", "pokemon", "trainer_class_lookups.h")
    if not os.path.isfile(path):
        return {}, {}

    fac_to_pic: dict[str, str] = {}
    fac_to_class: dict[str, str] = {}
    pat = re.compile(r"\[(\w+)\]\s*=\s*(\w+)")

    in_pic = in_class = False
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            for line in f:
                stripped = line.strip()
                if stripped.startswith("#"):
                    continue
                if "gFacilityClassToPicIndex" in line and "=" in line:
                    in_pic, in_class = True, False
                    continue
                if "gFacilityClassToTrainerClass" in line and "=" in line:
                    in_class, in_pic = True, False
                    continue
                if stripped.startswith("};"):
                    in_pic = in_class = False
                    continue
                m = pat.search(line)
                if m:
                    if in_pic:
                        fac_to_pic[m.group(1)] = m.group(2)
                    elif in_class:
                        fac_to_class[m.group(1)] = m.group(2)
    except Exception as exc:
        log.warning("_parse_facility_lookups: %s", exc)

    return fac_to_pic, fac_to_class


def _build_class_to_pic(fac_to_pic: dict, fac_to_class: dict) -> dict[str, str]:
    """Build {TRAINER_CLASS_CONST: TRAINER_PIC_CONST} from facility lookups."""
    class_to_pic: dict[str, str] = {}
    for fac, tc in fac_to_class.items():
        if tc not in class_to_pic and fac in fac_to_pic:
            class_to_pic[tc] = fac_to_pic[fac]
    return class_to_pic


def _build_class_to_facility(fac_to_class: dict) -> dict[str, list[str]]:
    """Build {TRAINER_CLASS_CONST: [FACILITY_CLASS_CONST, ...]}."""
    result: dict[str, list[str]] = {}
    for fac, tc in fac_to_class.items():
        result.setdefault(tc, []).append(fac)
    return result


def _parse_trainer_pic_paths(root: str) -> dict[str, str]:
    """Return {TRAINER_PIC_CONST: abs_png_path}.

    Bridging via the ``gTrainerFrontPic_<Symbol>`` C symbol, not the filename —
    the filename uses its own snake_case convention that doesn't line up with
    the constant for compound-word classes. Example:
    ``TRAINER_PIC_COOLTRAINER_M`` → symbol ``CooltrainerM`` → file
    ``cool_trainer_m_front_pic.png``. Matching the constant suffix against the
    filename stem misses this pairing. Normalising both the constant suffix
    and the symbol to ``lowercased-and-underscore-stripped`` collapses them to
    the same key (``cooltrainerm``) and they line up reliably.
    """
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
                        symbol = m.group(1)                    # CooltrainerM
                        rel    = m.group(2)
                        key    = symbol.replace("_", "").lower()   # cooltrainerm
                        path_by_symbol[key] = os.path.join(
                            root, rel.replace(".4bpp.lz", ".png")
                        )
        except Exception as exc:
            log.warning("_parse_trainer_pic_paths gfx: %s", exc)

    result: dict[str, str] = {}
    const_h = os.path.join(root, "include", "constants", "trainers.h")
    if os.path.isfile(const_h):
        pat2 = re.compile(r"#define\s+(TRAINER_PIC_\w+)\s+\d+")
        try:
            with open(const_h, encoding="utf-8", errors="replace") as f:
                for line in f:
                    m = pat2.search(line)
                    if m:
                        const  = m.group(1)
                        suffix = const[len("TRAINER_PIC_"):]
                        key    = suffix.replace("_", "").lower()
                        if key in path_by_symbol:
                            result[const] = path_by_symbol[key]
        except Exception as exc:
            log.warning("_parse_trainer_pic_paths consts: %s", exc)
    return result


# ── Header writers ───────────────────────────────────────────────────────────

def write_trainer_class_names(root: str, edits: dict[str, str]) -> bool:
    """Patch trainer_class_names.h with updated display names.
    Returns True if file was modified."""
    if not edits:
        return False
    path = os.path.join(root, "src", "data", "text", "trainer_class_names.h")
    if not os.path.isfile(path):
        return False
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            text = f.read()
        changed = False
        for const, new_name in edits.items():
            pat = re.compile(
                r'(\[' + re.escape(const) + r'\]\s*=\s*_\(")([^"]*)("\))'
            )
            new_text, n = pat.subn(rf"\g<1>{new_name}\3", text)
            if n:
                text = new_text
                changed = True
        if changed:
            with open(path, "w", encoding="utf-8", newline="\n") as f:
                f.write(text)
        return changed
    except Exception as exc:
        log.warning("write_trainer_class_names: %s", exc)
        return False


def write_money_table(root: str, edits: dict[str, int]) -> bool:
    """Patch gTrainerMoneyTable in battle_main.c with updated multipliers.
    Returns True if file was modified."""
    if not edits:
        return False
    path = os.path.join(root, "src", "battle_main.c")
    if not os.path.isfile(path):
        return False
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            text = f.read()
        changed = False
        for const, new_val in edits.items():
            pat = re.compile(
                r"(\{\s*" + re.escape(const) + r"\s*,\s*)\d+(\s*\})"
            )
            new_text, n = pat.subn(rf"\g<1>{new_val}\2", text)
            if n:
                text = new_text
                changed = True
        if changed:
            with open(path, "w", encoding="utf-8", newline="\n") as f:
                f.write(text)
        return changed
    except Exception as exc:
        log.warning("write_money_table: %s", exc)
        return False


def write_facility_pic_mapping(
    root: str, edits: dict[str, str], class_to_fac: dict[str, list[str]]
) -> bool:
    """Patch gFacilityClassToPicIndex in trainer_class_lookups.h.
    *edits* is {TRAINER_CLASS_CONST: new_TRAINER_PIC_CONST}.
    Updates ALL facility classes that map to each edited trainer class."""
    if not edits:
        return False
    path = os.path.join(root, "src", "data", "pokemon", "trainer_class_lookups.h")
    if not os.path.isfile(path):
        return False
    # Build {FACILITY_CLASS: new_PIC} from edits.
    # Skip entries whose new_pic is empty ("" from the "(none)" option in
    # the combo) — otherwise the writer would emit "[FAC_X] = ," which is
    # a C syntax error and breaks the build. Empty == "don't change this
    # class's pic mapping".
    fac_edits: dict[str, str] = {}
    for tc, new_pic in edits.items():
        if not new_pic:
            continue
        for fac in class_to_fac.get(tc, []):
            fac_edits[fac] = new_pic
    if not fac_edits:
        return False
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            text = f.read()
        changed = False
        for fac_const, new_pic in fac_edits.items():
            pat = re.compile(
                r"(\[" + re.escape(fac_const) + r"\]\s*=\s*)(\w+)"
            )
            new_text, n = pat.subn(rf"\g<1>{new_pic}", text)
            if n:
                text = new_text
                changed = True
        if changed:
            with open(path, "w", encoding="utf-8", newline="\n") as f:
                f.write(text)
        return changed
    except Exception as exc:
        log.warning("write_facility_pic_mapping: %s", exc)
        return False


def add_new_trainer_class(
    root: str, const_name: str, display_name: str, money: int = 5
) -> bool:
    """Add a brand-new trainer class to the project.
    Writes to trainers.h, trainer_class_names.h, and battle_main.c.
    Returns True on success."""
    # 1. Find next available ID in trainers.h
    trainers_h = os.path.join(root, "include", "constants", "trainers.h")
    if not os.path.isfile(trainers_h):
        return False
    try:
        with open(trainers_h, encoding="utf-8", errors="replace") as f:
            text_h = f.read()

        # Find all existing TRAINER_CLASS IDs
        existing = {}
        for m in re.finditer(r"#define\s+(TRAINER_CLASS_\w+)\s+(\d+)", text_h):
            existing[m.group(1)] = int(m.group(2))

        if const_name in existing:
            log.warning("add_new_trainer_class: %s already exists", const_name)
            return False

        next_id = max(existing.values()) + 1 if existing else 0

        # Find the last TRAINER_CLASS define line and insert after it
        last_pos = -1
        for m in re.finditer(r"#define\s+TRAINER_CLASS_\w+\s+\d+[^\n]*\n", text_h):
            last_pos = m.end()

        if last_pos < 0:
            return False

        new_define = f"#define {const_name:<40s} {next_id}\n"
        text_h = text_h[:last_pos] + new_define + text_h[last_pos:]

        with open(trainers_h, "w", encoding="utf-8", newline="\n") as f:
            f.write(text_h)

        # 2. Add entry to trainer_class_names.h
        names_h = os.path.join(root, "src", "data", "text", "trainer_class_names.h")
        if os.path.isfile(names_h):
            with open(names_h, encoding="utf-8", errors="replace") as f:
                text_n = f.read()
            # Insert before the closing };
            insert_line = f'    [{const_name}] = _("{display_name}"),\n'
            close_pos = text_n.rfind("};")
            if close_pos >= 0:
                text_n = text_n[:close_pos] + insert_line + text_n[close_pos:]
                with open(names_h, "w", encoding="utf-8", newline="\n") as f:
                    f.write(text_n)

        # 3. Add entry to gTrainerMoneyTable in battle_main.c
        battle_c = os.path.join(root, "src", "battle_main.c")
        if os.path.isfile(battle_c):
            with open(battle_c, encoding="utf-8", errors="replace") as f:
                text_b = f.read()
            # Insert before the sentinel {0xFF, 5}
            sentinel_pat = re.compile(r"(\s*\{\s*0xFF\s*,\s*\d+\s*\})")
            m = sentinel_pat.search(text_b)
            if m:
                insert = f"    {{{const_name}, {money}}},\n"
                text_b = text_b[:m.start()] + "\n" + insert + text_b[m.start():]
                with open(battle_c, "w", encoding="utf-8", newline="\n") as f:
                    f.write(text_b)

        return True
    except Exception as exc:
        log.warning("add_new_trainer_class: %s", exc)
        return False


# ── Helpers ──────────────────────────────────────────────────────────────────

def _get_battle_bgm(const: str) -> str:
    """Return human-readable battle BGM for a trainer class constant."""
    if const in _BGM_CHAMPION:
        return "VS Champion"
    if const in _BGM_GYM_LEADER:
        return "VS Gym Leader"
    return "VS Trainer"


def _get_victory_music(const: str) -> str:
    if const in _VICTORY_GYM_LEADER:
        return "Gym Leader Victory"
    return "Normal Victory"


def _get_battle_terrain(const: str) -> str:
    if const in _TERRAIN_LEADER:
        return "Gym Leader Arena"
    if const in _TERRAIN_CHAMPION:
        return "Champion Arena"
    return "Map Default"


def _get_common_encounter_music(trainers: dict, class_const: str) -> str:
    """Find the most common encounter music among trainers of this class."""
    music_counts: Counter = Counter()
    for _tconst, tdata in trainers.items():
        if not isinstance(tdata, dict):
            continue
        if tdata.get("trainerClass") != class_const:
            continue
        raw = tdata.get("encounterMusic_gender", "")
        if not raw:
            continue
        # Strip F_TRAINER_FEMALE flag
        music = raw.replace("| F_TRAINER_FEMALE", "").replace("|F_TRAINER_FEMALE", "").strip()
        if music:
            music_counts[music] += 1
    if not music_counts:
        return "(none)"
    most_common = music_counts.most_common(1)[0][0]
    label = _ENCOUNTER_MUSIC_LABELS.get(most_common, most_common)
    total = sum(music_counts.values())
    count = music_counts[most_common]
    if count == total:
        return label
    return f"{label} ({count}/{total})"


# ── Widget ───────────────────────────────────────────────────────────────────

class TrainerClassEditor(QWidget):
    """Editor for trainer class properties: display name, money multiplier,
    battle music, terrain, facility classes, and sprite preview."""

    changed = pyqtSignal()
    # Emitted on every keystroke in the name field: (const, new_display_name).
    # Used to push pending name renames to the sibling Trainers editor live,
    # so the trainer list/detail reflect pending class name edits without save.
    class_name_edited = pyqtSignal(str, str)
    # Emitted when the user clicks the Rename button — mainwindow opens
    # the shared RenameDialog and routes the rename through refactor_service
    # so the constant gets renamed across every source file at once.
    rename_class_requested = pyqtSignal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._classes: dict[str, int] = {}
        self._names: dict[str, str] = {}
        self._money: dict[str, int] = {}
        self._class_to_pic: dict[str, str] = {}
        self._class_to_fac: dict[str, list[str]] = {}
        self._pic_paths: dict[str, str] = {}
        self._all_pic_consts: list[str] = []  # ordered list of TRAINER_PIC_*
        self._trainers: dict = {}
        self._root: str = ""
        self._current_class: str | None = None
        self._dirty_names: dict[str, str] = {}
        self._dirty_money: dict[str, int] = {}
        self._dirty_pics: dict[str, str] = {}  # class → new TRAINER_PIC
        self._loaded = False
        self._build()
        # Subscribe to the cross-tab palette bus so live Trainer Graphics
        # edits show up on the list rows + the detail preview without a
        # save.
        try:
            _get_palette_bus().palette_changed.connect(
                self._on_trainer_palette_changed
            )
        except Exception:
            pass

    # ── Bus-aware sprite helper ─────────────────────────────────────────────

    def _load_trainer_pixmap(self, png_path: str,
                             pic_const: str = "") -> Optional[QPixmap]:
        """Load a trainer-pic pixmap re-indexed through the live palette.

        *pic_const* is optional but preferred — it lets the bus cache
        keyed edits from the Trainer Graphics tab hit first. Falls
        back to disk (or a flat load) on any failure.
        """
        if not png_path or not os.path.isfile(png_path):
            return None
        pal = _get_palette_bus().ensure_trainer_palette_from_png(
            png_path, pic_const=pic_const,
        )
        return load_sprite_pixmap(png_path, pal)

    def _on_trainer_palette_changed(self, category: str, key: str) -> None:
        """Refresh visible trainer thumbnails on any trainer-pic edit."""
        if category != CAT_TRAINER_PIC:
            return
        try:
            # Rebuild both the list row icons and the detail preview by
            # re-selecting the current class. Cheap enough — this runs
            # at most every few keystrokes of a palette edit.
            self._populate_list()
            if self._current_class:
                pic_const = self._dirty_pics.get(
                    self._current_class,
                    self._class_to_pic.get(self._current_class, ""),
                )
                self._update_sprite_preview(pic_const)
        except Exception:
            pass

    # ── UI construction ─────────────────────────────────────────────────────

    def _build(self):
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        # Title bar
        bar = QHBoxLayout()
        bar.setContentsMargins(8, 6, 8, 4)
        title_lbl = QLabel("Trainer Classes")
        title_lbl.setStyleSheet(
            "font-size: 15px; font-weight: bold; color: #e0e0e0;"
        )
        bar.addWidget(title_lbl)
        bar.addStretch()
        self._count_lbl = QLabel()
        self._count_lbl.setStyleSheet("color: #777; font-size: 11px;")
        bar.addWidget(self._count_lbl)
        outer.addLayout(bar)

        # Splitter: left list | right detail
        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.setHandleWidth(2)
        splitter.setStyleSheet("QSplitter::handle { background: #2e2e2e; }")

        # ── Left panel: searchable class list ────────────────────────────────
        left = QWidget()
        left.setStyleSheet("background: #191919;")
        left.setMinimumWidth(160)
        left_v = QVBoxLayout(left)
        left_v.setContentsMargins(0, 0, 0, 0)
        left_v.setSpacing(0)

        self._search = QLineEdit()
        self._search.setPlaceholderText("Search classes\u2026")
        self._search.setStyleSheet(
            "background: #222; border: none; border-bottom: 1px solid #2a2a2a; "
            "padding: 6px; color: #ccc;"
        )
        self._search.textChanged.connect(self._filter_list)
        left_v.addWidget(self._search)

        self._list = QListWidget()
        self._list.setStyleSheet(_LIST_SS)
        self._list.setIconSize(QSize(32, 40))
        self._list.currentItemChanged.connect(self._on_selection)
        left_v.addWidget(self._list)

        add_btn = QPushButton("+ Add Trainer Class")
        add_btn.setStyleSheet(
            "background: #1a3a1a; color: #aaffaa; border: none; padding: 7px; "
            "border-top: 1px solid #2a2a2a;"
        )
        add_btn.clicked.connect(self._add_class)
        left_v.addWidget(add_btn)

        splitter.addWidget(left)

        # ── Right panel: detail editor ───────────────────────────────────────
        self._detail_scroll = QScrollArea()
        self._detail_scroll.setWidgetResizable(True)
        self._detail_scroll.setFrameShape(QFrame.Shape.NoFrame)
        self._detail_scroll.setStyleSheet(
            "QScrollArea { background: #1a1a1a; border: none; }"
            "QScrollBar:vertical { background: #1a1a1a; width: 8px; border: none; }"
            "QScrollBar::handle:vertical { background: #444; border-radius: 4px; "
            "min-height: 20px; }"
            "QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical "
            "{ height: 0; }"
        )

        detail = QWidget()
        detail.setStyleSheet("background: #1a1a1a;")
        dl = QVBoxLayout(detail)
        dl.setContentsMargins(16, 12, 16, 12)
        dl.setSpacing(10)

        # Sprite preview
        self._sprite_lbl = QLabel()
        self._sprite_lbl.setFixedSize(96, 120)
        self._sprite_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._sprite_lbl.setStyleSheet(
            "background: #111; border: 1px solid #2e2e2e; border-radius: 4px;"
        )
        sprite_row = QHBoxLayout()
        sprite_row.addStretch()
        sprite_row.addWidget(self._sprite_lbl)
        sprite_row.addStretch()
        dl.addLayout(sprite_row)

        # Class constant (read-only)
        self._const_lbl = QLabel()
        self._const_lbl.setStyleSheet(
            "color: #888; font-size: 10px; font-family: monospace;"
        )
        self._const_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        dl.addWidget(self._const_lbl)

        # ── Editable fields ──────────────────────────────────────────────────
        _fs = "color: #aaa; font-size: 11px;"
        _input_ss = (
            "background: #222; border: 1px solid #333; padding: 4px 8px; "
            "color: #e0e0e0; border-radius: 3px;"
        )

        edit_group = QGroupBox("Editable Properties")
        edit_group.setStyleSheet(_GROUP_SS)
        edit_form = QFormLayout()
        edit_form.setLabelAlignment(Qt.AlignmentFlag.AlignRight)
        edit_form.setSpacing(8)

        # Display name + Rename button
        name_row = QHBoxLayout()
        name_row.setContentsMargins(0, 0, 0, 0)
        name_row.setSpacing(6)
        self._name_edit = QLineEdit()
        self._name_edit.setMaxLength(12)
        self._name_edit.setStyleSheet(_input_ss)
        self._name_edit.setToolTip(
            "Updates only the in-game display string for this class.\n"
            "Does NOT rename the TRAINER_CLASS_* constant — use the\n"
            "Rename... button for that (updates all source files)."
        )
        self._name_edit.textChanged.connect(self._on_name_changed)
        name_row.addWidget(self._name_edit, 1)
        self._rename_btn = QPushButton("Rename...")
        self._rename_btn.setFixedWidth(80)
        self._rename_btn.setToolTip(
            "Rename the TRAINER_CLASS_* constant across every source file\n"
            "(opponents.h, trainer_class_names.h, battle_main.c, trainers.json,\n"
            "scripts, maps, etc.). Display name and constant suffix update\n"
            "together, like the Pokémon / Item / Move / Ability rename flow."
        )
        self._rename_btn.setStyleSheet(
            "background: #2a2a3a; color: #aac; border: 1px solid #3a3a4a; "
            "padding: 3px 8px; border-radius: 3px; font-size: 10px;"
        )
        self._rename_btn.clicked.connect(self._on_rename_clicked)
        name_row.addWidget(self._rename_btn)
        lbl = QLabel("Display Name:")
        lbl.setStyleSheet(_fs)
        edit_form.addRow(lbl, name_row)

        self._name_counter = QLabel("0/12")
        self._name_counter.setStyleSheet("color: #666; font-size: 9px;")
        self._name_counter.setAlignment(Qt.AlignmentFlag.AlignRight)
        edit_form.addRow("", self._name_counter)

        # Money multiplier
        self._money_spin = _NoScrollSpin()
        self._money_spin.setRange(0, 255)
        self._money_spin.setStyleSheet(_input_ss)
        self._money_spin.valueChanged.connect(self._on_money_changed)
        lbl2 = QLabel("Money Multiplier:")
        lbl2.setStyleSheet(_fs)
        edit_form.addRow(lbl2, self._money_spin)

        note = QLabel("Base prize = level \u00d7 multiplier \u00d7 4")
        note.setStyleSheet("color: #555; font-size: 9px; font-style: italic;")
        edit_form.addRow("", note)

        edit_group.setLayout(edit_form)
        dl.addWidget(edit_group)

        # ── Sprite / pic selector (right after editable props) ───────────────
        pic_group = QGroupBox("Sprite")
        pic_group.setStyleSheet(_GROUP_SS)
        pic_form = QFormLayout()
        pic_form.setLabelAlignment(Qt.AlignmentFlag.AlignRight)
        pic_form.setSpacing(6)

        # Sprite picker combo with thumbnails
        self._pic_cb = _NoScrollCombo()
        self._pic_cb.setStyleSheet(_input_ss)
        self._pic_cb.setIconSize(QSize(24, 32))
        self._pic_cb.currentIndexChanged.connect(self._on_pic_changed)
        lbl_pic = QLabel("Trainer Pic:")
        lbl_pic.setStyleSheet(_fs)
        pic_form.addRow(lbl_pic, self._pic_cb)

        self._pic_path_lbl = QLabel()
        self._pic_path_lbl.setStyleSheet("color: #666; font-size: 9px;")
        self._pic_path_lbl.setWordWrap(True)
        lbl_path = QLabel("PNG Path:")
        lbl_path.setStyleSheet(_fs)
        pic_form.addRow(lbl_path, self._pic_path_lbl)

        self._open_folder_btn = QPushButton("Open File in Folder")
        self._open_folder_btn.setStyleSheet(
            "background: #2a2a3a; color: #aac; border: 1px solid #3a3a4a; "
            "padding: 4px 12px; border-radius: 3px; font-size: 10px;"
        )
        self._open_folder_btn.clicked.connect(self._open_sprite_folder)
        pic_form.addRow("", self._open_folder_btn)

        pic_note = QLabel(
            "Class-level sprite used ONLY in Battle Tower, Trainer Tower,\n"
            "and Union Room matches — where the opponent is generated from\n"
            "a class, not a specific trainer. Regular trainer battles use\n"
            "the per-trainer pic set on the Trainers tab, not this one.\n"
            "Set to (none) if this class is never used in a facility."
        )
        pic_note.setStyleSheet(
            "color: #888; font-size: 10px; font-style: italic;"
        )
        pic_note.setWordWrap(True)
        pic_form.addRow("", pic_note)

        pic_group.setLayout(pic_form)
        dl.addWidget(pic_group)

        # ── Battle properties (read-only, derived from switch statements) ────
        battle_group = QGroupBox("Battle Properties")
        battle_group.setStyleSheet(_GROUP_SS)
        battle_form = QFormLayout()
        battle_form.setLabelAlignment(Qt.AlignmentFlag.AlignRight)
        battle_form.setSpacing(6)

        _info_ss = "color: #ccc; font-size: 11px;"

        self._battle_bgm_lbl = QLabel()
        self._battle_bgm_lbl.setStyleSheet(_info_ss)
        lbl_bgm = QLabel("Battle BGM:")
        lbl_bgm.setStyleSheet(_fs)
        battle_form.addRow(lbl_bgm, self._battle_bgm_lbl)

        self._victory_music_lbl = QLabel()
        self._victory_music_lbl.setStyleSheet(_info_ss)
        lbl_vic = QLabel("Victory Music:")
        lbl_vic.setStyleSheet(_fs)
        battle_form.addRow(lbl_vic, self._victory_music_lbl)

        self._terrain_lbl = QLabel()
        self._terrain_lbl.setStyleSheet(_info_ss)
        lbl_ter = QLabel("Battle Terrain:")
        lbl_ter.setStyleSheet(_fs)
        battle_form.addRow(lbl_ter, self._terrain_lbl)

        self._enc_music_lbl = QLabel()
        self._enc_music_lbl.setStyleSheet(_info_ss)
        self._enc_music_lbl.setWordWrap(True)
        lbl_enc = QLabel("Encounter Music:")
        lbl_enc.setStyleSheet(_fs)
        battle_form.addRow(lbl_enc, self._enc_music_lbl)

        battle_note = QLabel(
            "Battle BGM, victory music, and terrain are set by switch\n"
            "statements in C code, not a data table. Encounter music\n"
            "is per-trainer; this shows the most common for this class."
        )
        battle_note.setStyleSheet(
            "color: #555; font-size: 9px; font-style: italic;"
        )
        battle_note.setWordWrap(True)
        battle_form.addRow("", battle_note)

        battle_group.setLayout(battle_form)
        dl.addWidget(battle_group)

        # ── Facility classes ─────────────────────────────────────────────────
        fac_group = QGroupBox("Facility Classes")
        fac_group.setStyleSheet(_GROUP_SS)
        fac_vbox = QVBoxLayout()
        fac_vbox.setSpacing(4)

        self._fac_list_lbl = QLabel()
        self._fac_list_lbl.setStyleSheet(
            "color: #aaa; font-size: 10px; font-family: monospace;"
        )
        self._fac_list_lbl.setWordWrap(True)
        fac_vbox.addWidget(self._fac_list_lbl)

        fac_note = QLabel(
            "Facility classes map this trainer class to sprites\n"
            "and behavior in Battle Tower, Trainer Tower, and\n"
            "Union Room."
        )
        fac_note.setStyleSheet(
            "color: #555; font-size: 9px; font-style: italic;"
        )
        fac_note.setWordWrap(True)
        fac_vbox.addWidget(fac_note)

        fac_group.setLayout(fac_vbox)
        dl.addWidget(fac_group)

        # ── Usage info ───────────────────────────────────────────────────────
        self._usage_lbl = QLabel()
        self._usage_lbl.setStyleSheet(
            "color: #666; font-size: 10px; margin-top: 4px;"
        )
        self._usage_lbl.setWordWrap(True)
        dl.addWidget(self._usage_lbl)

        dl.addStretch()
        self._detail_scroll.setWidget(detail)
        splitter.addWidget(self._detail_scroll)

        splitter.setSizes([230, 900])
        outer.addWidget(splitter, 1)

    # ── Public API ──────────────────────────────────────────────────────────

    def load(self, root: str, trainers: dict | None = None):
        """Parse all trainer class data from the project at *root*."""
        self._root = root
        self._classes = _parse_trainer_classes(root)
        self._names = _parse_trainer_class_names(root)
        self._money = _parse_money_table(root)

        fac_to_pic, fac_to_class = _parse_facility_lookups(root)
        self._class_to_pic = _build_class_to_pic(fac_to_pic, fac_to_class)
        self._class_to_fac = _build_class_to_facility(fac_to_class)

        self._pic_paths = _parse_trainer_pic_paths(root)
        self._trainers = trainers or {}
        self._dirty_names.clear()
        self._dirty_money.clear()
        self._dirty_pics.clear()
        self._loaded = True
        self._count_lbl.setText(f"{len(self._classes)} classes")
        self._populate_pic_combo()
        self._populate_list()

    def flush(self) -> dict:
        """Return pending edits as dict with names, money, and pics."""
        return {
            "names": dict(self._dirty_names),
            "money": dict(self._dirty_money),
            "pics": dict(self._dirty_pics),
        }

    def has_edits(self) -> bool:
        return bool(self._dirty_names) or bool(self._dirty_money) or bool(self._dirty_pics)

    def get_class_to_fac(self) -> dict[str, list[str]]:
        """Return the class→facility mapping (needed by pic writer)."""
        return self._class_to_fac

    def clear_dirty(self):
        """Call after a successful save to reset the dirty tracking."""
        for k, v in self._dirty_names.items():
            self._names[k] = v
        for k, v in self._dirty_money.items():
            self._money[k] = v
        for k, v in self._dirty_pics.items():
            self._class_to_pic[k] = v
        self._dirty_names.clear()
        self._dirty_money.clear()
        self._dirty_pics.clear()

    # ── Pic combo ──────────────────────────────────────────────────────────

    def _populate_pic_combo(self):
        """Fill the sprite picker dropdown with all TRAINER_PIC constants.

        The leading "(none)" entry is kept so classes with no mapping yet
        render something in the dropdown, but selecting it does NOT write
        an empty pic to disk — ``write_facility_pic_mapping`` treats empty
        values as "leave alone" to avoid emitting invalid C (``[FAC] = ,``).
        """
        self._pic_cb.blockSignals(True)
        self._pic_cb.clear()
        self._pic_cb.addItem("(none)", "")
        # Sort pic consts by their suffix for readability
        sorted_pics = sorted(self._pic_paths.keys(),
                             key=lambda c: c.replace("TRAINER_PIC_", ""))
        self._all_pic_consts = sorted_pics
        for pic_const in sorted_pics:
            # Human-readable label
            label = pic_const.replace("TRAINER_PIC_", "").replace("_", " ").title()
            png = self._pic_paths.get(pic_const, "")
            icon = QIcon()
            if png and os.path.isfile(png):
                pix = self._load_trainer_pixmap(png, pic_const=pic_const)
                if pix is not None and not pix.isNull():
                    pix = pix.scaled(
                        24, 32,
                        Qt.AspectRatioMode.KeepAspectRatio,
                        Qt.TransformationMode.SmoothTransformation,
                    )
                    icon = QIcon(pix)
            self._pic_cb.addItem(icon, label, pic_const)
        self._pic_cb.blockSignals(False)

    # ── List ────────────────────────────────────────────────────────────────

    def _populate_list(self):
        self._list.blockSignals(True)
        self._list.clear()
        for const, num_id in sorted(self._classes.items(), key=lambda x: x[1]):
            name = self._names.get(
                const,
                const.replace("TRAINER_CLASS_", "").replace("_", " ").title(),
            )
            item = QListWidgetItem()
            item.setData(Qt.ItemDataRole.UserRole, const)
            item.setText(name)
            item.setToolTip(f"{const}  ({num_id})")
            item.setForeground(QColor("#e0e0e0"))

            pic_const = self._class_to_pic.get(const)
            if pic_const and pic_const in self._pic_paths:
                png = self._pic_paths[pic_const]
                if os.path.isfile(png):
                    pix = self._load_trainer_pixmap(png, pic_const=pic_const)
                    if pix is not None and not pix.isNull():
                        pix = pix.scaled(
                            32, 40,
                            Qt.AspectRatioMode.KeepAspectRatio,
                            Qt.TransformationMode.SmoothTransformation,
                        )
                        item.setIcon(QIcon(pix))

            self._list.addItem(item)
        self._list.blockSignals(False)
        if self._list.count():
            self._list.setCurrentRow(0)

    def _filter_list(self, text: str):
        t = text.lower()
        for i in range(self._list.count()):
            item = self._list.item(i)
            const = item.data(Qt.ItemDataRole.UserRole) or ""
            name = self._names.get(const, "")
            item.setHidden(t not in const.lower() and t not in name.lower())

    # ── Selection / detail ──────────────────────────────────────────────────

    def _on_selection(self, current, _previous):
        if not current:
            return
        const = current.data(Qt.ItemDataRole.UserRole)
        self._current_class = const
        self._update_detail(const)

    def _update_detail(self, const: str):
        self._name_edit.blockSignals(True)
        self._money_spin.blockSignals(True)
        self._pic_cb.blockSignals(True)

        self._const_lbl.setText(const)

        # ── Editable fields ──────────────────────────────────────────────
        name = self._dirty_names.get(const, self._names.get(const, ""))
        self._name_edit.setText(name)
        self._update_name_counter(name)

        money = self._dirty_money.get(const, self._money.get(const, 0))
        self._money_spin.setValue(money)

        # ── Sprite picker ────────────────────────────────────────────────
        pic_const = self._dirty_pics.get(const, self._class_to_pic.get(const, ""))
        idx = self._pic_cb.findData(pic_const)
        self._pic_cb.setCurrentIndex(max(idx, 0))
        self._update_sprite_preview(pic_const)

        # ── Battle properties ────────────────────────────────────────────
        self._battle_bgm_lbl.setText(_get_battle_bgm(const))
        self._victory_music_lbl.setText(_get_victory_music(const))
        self._terrain_lbl.setText(_get_battle_terrain(const))
        self._enc_music_lbl.setText(
            _get_common_encounter_music(self._trainers, const)
        )

        # Color-code special battle properties
        if const in _BGM_CHAMPION:
            self._battle_bgm_lbl.setStyleSheet("color: #ffcc00; font-size: 11px;")
        elif const in _BGM_GYM_LEADER:
            self._battle_bgm_lbl.setStyleSheet("color: #66bbff; font-size: 11px;")
        else:
            self._battle_bgm_lbl.setStyleSheet("color: #ccc; font-size: 11px;")

        if const in _VICTORY_GYM_LEADER:
            self._victory_music_lbl.setStyleSheet(
                "color: #66bbff; font-size: 11px;"
            )
        else:
            self._victory_music_lbl.setStyleSheet("color: #ccc; font-size: 11px;")

        if const in _TERRAIN_LEADER or const in _TERRAIN_CHAMPION:
            self._terrain_lbl.setStyleSheet("color: #66bbff; font-size: 11px;")
        else:
            self._terrain_lbl.setStyleSheet("color: #ccc; font-size: 11px;")

        # ── Facility classes ─────────────────────────────────────────────
        facs = self._class_to_fac.get(const, [])
        if facs:
            self._fac_list_lbl.setText("\n".join(sorted(facs)))
        else:
            self._fac_list_lbl.setText("(none)")

        # ── Usage ────────────────────────────────────────────────────────
        count = 0
        users: list[str] = []
        for tconst, tdata in self._trainers.items():
            if isinstance(tdata, dict) and tdata.get("trainerClass") == const:
                count += 1
                if len(users) < 5:
                    users.append(tconst)
        txt = f"Used by {count} trainer(s)"
        if users:
            txt += ": " + ", ".join(users)
            if count > 5:
                txt += f" (+{count - 5} more)"
        self._usage_lbl.setText(txt)

        self._name_edit.blockSignals(False)
        self._money_spin.blockSignals(False)
        self._pic_cb.blockSignals(False)

    def _update_sprite_preview(self, pic_const: str):
        """Update the large sprite preview and path label."""
        self._sprite_lbl.clear()
        png_path = ""
        if pic_const and pic_const in self._pic_paths:
            png_path = self._pic_paths[pic_const]
            if os.path.isfile(png_path):
                pix = self._load_trainer_pixmap(
                    png_path, pic_const=pic_const,
                )
                if pix is not None and not pix.isNull():
                    pix = pix.scaled(
                        88, 112,
                        Qt.AspectRatioMode.KeepAspectRatio,
                        Qt.TransformationMode.SmoothTransformation,
                    )
                    self._sprite_lbl.setPixmap(pix)
        if png_path and self._root:
            try:
                rel = os.path.relpath(png_path, self._root)
                self._pic_path_lbl.setText(rel.replace("\\", "/"))
            except Exception:
                self._pic_path_lbl.setText(png_path)
        else:
            self._pic_path_lbl.setText("(none)")

    # ── Edit handlers ───────────────────────────────────────────────────────

    def _update_name_counter(self, text: str):
        n = len(text)
        self._name_counter.setText(f"{n}/12")
        if n > 12:
            self._name_counter.setStyleSheet("color: #ff5555; font-size: 9px;")
        elif n > 10:
            self._name_counter.setStyleSheet("color: #ffaa55; font-size: 9px;")
        else:
            self._name_counter.setStyleSheet("color: #666; font-size: 9px;")

    def _on_name_changed(self, text: str):
        if not self._current_class:
            return
        self._update_name_counter(text)

        original = self._names.get(self._current_class, "")
        if text != original:
            self._dirty_names[self._current_class] = text
        elif self._current_class in self._dirty_names:
            del self._dirty_names[self._current_class]

        # Keep list item text in sync
        for i in range(self._list.count()):
            item = self._list.item(i)
            if item.data(Qt.ItemDataRole.UserRole) == self._current_class:
                item.setText(text or self._current_class)
                break

        # Push to sibling Trainers editor live — pending rename is visible
        # without save.
        effective = text or self._names.get(self._current_class, "")
        self.class_name_edited.emit(self._current_class, effective)

        self.changed.emit()

    def _on_rename_clicked(self):
        """User clicked the Rename... button — delegate to mainwindow so it
        can open the shared RenameDialog and drive the cross-project rename
        through refactor_service. Mainwindow calls back via
        ``rename_class_key`` to update this widget's in-memory data."""
        if not self._current_class:
            return
        self.rename_class_requested.emit(self._current_class)

    def rename_class_key(self, old_const: str, new_const: str, display_name: str) -> None:
        """Re-key every in-memory dict from old_const to new_const and swap
        the selected trainer-class constant. Called by mainwindow after the
        refactor service has queued (or applied) the global rename, so the
        editor's list + widgets stay in sync without a full reload.

        display_name is written into self._names under the NEW key so the
        list item text / summary reflect the combined rename+displayname.
        """
        if not old_const or not new_const or old_const == new_const:
            # Display-only change — just update the name caches.
            if old_const in self._names:
                self._names[old_const] = display_name
            if self._current_class == old_const:
                self._name_edit.blockSignals(True)
                self._name_edit.setText(display_name)
                self._name_edit.blockSignals(False)
                self._update_name_counter(display_name)
            return

        def _rekey(d: dict):
            if old_const in d:
                d[new_const] = d.pop(old_const)

        _rekey(self._classes)
        _rekey(self._names)
        _rekey(self._money)
        _rekey(self._class_to_pic)
        _rekey(self._class_to_fac)
        _rekey(self._dirty_names)
        _rekey(self._dirty_money)
        _rekey(self._dirty_pics)
        # display_name overrides whatever was in _names — refactor_service
        # has already queued the name write, keep caches consistent.
        self._names[new_const] = display_name

        # Swap the list item's stored const + visible text.
        for i in range(self._list.count()):
            item = self._list.item(i)
            if item and item.data(Qt.ItemDataRole.UserRole) == old_const:
                item.setData(Qt.ItemDataRole.UserRole, new_const)
                item.setText(display_name or new_const)
                break

        if self._current_class == old_const:
            self._current_class = new_const
            self._name_edit.blockSignals(True)
            self._name_edit.setText(display_name)
            self._name_edit.blockSignals(False)
            self._update_name_counter(display_name)
            # The identity label at the top of the detail panel also needs
            # to refresh. Not all builds of this editor have _const_lbl, so
            # guard the access.
            const_lbl = getattr(self, "_const_lbl", None)
            if const_lbl is not None:
                try:
                    const_lbl.setText(new_const)
                except Exception:
                    pass

    def _on_money_changed(self, value: int):
        if not self._current_class:
            return
        original = self._money.get(self._current_class, 0)
        if value != original:
            self._dirty_money[self._current_class] = value
        elif self._current_class in self._dirty_money:
            del self._dirty_money[self._current_class]
        self.changed.emit()

    def _on_pic_changed(self, _index: int):
        if not self._current_class:
            return
        new_pic = self._pic_cb.currentData() or ""
        original = self._class_to_pic.get(self._current_class, "")
        if new_pic != original:
            self._dirty_pics[self._current_class] = new_pic
        elif self._current_class in self._dirty_pics:
            del self._dirty_pics[self._current_class]

        # Update the large preview
        self._update_sprite_preview(new_pic)

        # Update the list item icon
        for i in range(self._list.count()):
            item = self._list.item(i)
            if item.data(Qt.ItemDataRole.UserRole) == self._current_class:
                png = self._pic_paths.get(new_pic, "")
                if png and os.path.isfile(png):
                    pix = self._load_trainer_pixmap(png, pic_const=new_pic)
                    if pix is not None and not pix.isNull():
                        pix = pix.scaled(
                            32, 40,
                            Qt.AspectRatioMode.KeepAspectRatio,
                            Qt.TransformationMode.SmoothTransformation,
                        )
                        item.setIcon(QIcon(pix))
                else:
                    item.setIcon(QIcon())
                break

        self.changed.emit()

    def _open_sprite_folder(self):
        """Open the folder containing the current sprite PNG."""
        if not self._current_class:
            return
        from ui.open_folder_util import open_in_folder
        pic_const = self._dirty_pics.get(
            self._current_class, self._class_to_pic.get(self._current_class, "")
        )
        png = self._pic_paths.get(pic_const, "")
        open_in_folder(png)

    # ── Add new class ───────────────────────────────────────────────────────

    def _add_class(self):
        """Open a dialog to create a new trainer class."""
        if not self._root:
            QMessageBox.warning(self, "No Project", "Load a project first.")
            return

        dlg = QDialog(self)
        dlg.setWindowTitle("Add Trainer Class")
        dlg.setStyleSheet("background: #1e1e1e; color: #e0e0e0;")
        layout = QVBoxLayout(dlg)

        _input_ss = (
            "background: #222; border: 1px solid #333; padding: 4px 8px; "
            "color: #e0e0e0; border-radius: 3px;"
        )

        form = QFormLayout()
        form.setSpacing(8)

        const_edit = QLineEdit()
        const_edit.setPlaceholderText("e.g. TRAINER_CLASS_MY_CLASS")
        const_edit.setStyleSheet(_input_ss)
        # Hard-restrict input to A-Z, 0-9, and underscore so the user
        # physically cannot type lowercase, spaces, or punctuation.
        from PyQt6.QtGui import QRegularExpressionValidator
        from PyQt6.QtCore import QRegularExpression
        const_edit.setValidator(
            QRegularExpressionValidator(
                QRegularExpression(r"^[A-Z][A-Z0-9_]*$"), const_edit)
        )
        form.addRow("Constant:", const_edit)

        name_edit = QLineEdit()
        name_edit.setMaxLength(12)
        name_edit.setPlaceholderText("e.g. MY CLASS")
        name_edit.setStyleSheet(_input_ss)
        form.addRow("Display Name:", name_edit)

        money_spin = _NoScrollSpin()
        money_spin.setRange(0, 255)
        money_spin.setValue(5)
        money_spin.setStyleSheet(_input_ss)
        form.addRow("Money Multiplier:", money_spin)

        layout.addLayout(form)

        # Auto-format constant name as user types display name
        def _auto_const():
            txt = name_edit.text().strip().upper().replace(" ", "_")
            if txt and not const_edit.isModified():
                const_edit.setText(f"TRAINER_CLASS_{txt}")
        name_edit.textChanged.connect(_auto_const)

        btns = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok
            | QDialogButtonBox.StandardButton.Cancel
        )
        btns.accepted.connect(dlg.accept)
        btns.rejected.connect(dlg.reject)
        layout.addWidget(btns)

        dlg.resize(400, 200)
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return

        const_name = const_edit.text().strip()
        display_name = name_edit.text().strip()
        money = money_spin.value()

        if not const_name or not display_name:
            QMessageBox.warning(self, "Missing Info", "Both constant and display name are required.")
            return

        if not const_name.startswith("TRAINER_CLASS_"):
            const_name = "TRAINER_CLASS_" + const_name

        # Belt-and-suspenders regex check — the QLineEdit validator already
        # prevents illegal characters, but if this code ever runs before the
        # validator is attached (tests, future refactor), fail loudly.
        if not re.fullmatch(r"TRAINER_CLASS_[A-Z][A-Z0-9_]*", const_name):
            QMessageBox.warning(
                self, "Invalid Constant",
                "Trainer class constants must be ALL-CAPS with only\n"
                "letters, digits, and underscores, starting with a letter\n"
                "after the TRAINER_CLASS_ prefix.\n\n"
                f"Got: {const_name}"
            )
            return

        if const_name in self._classes:
            QMessageBox.warning(self, "Already Exists", f"{const_name} already exists.")
            return

        ok = add_new_trainer_class(self._root, const_name, display_name, money)
        if ok:
            # Reload everything
            self.load(self._root, self._trainers)
            # Select the new class
            for i in range(self._list.count()):
                item = self._list.item(i)
                if item.data(Qt.ItemDataRole.UserRole) == const_name:
                    self._list.setCurrentItem(item)
                    break
            self.changed.emit()
        else:
            QMessageBox.warning(
                self, "Failed",
                f"Could not add {const_name}. Check the log for details."
            )
