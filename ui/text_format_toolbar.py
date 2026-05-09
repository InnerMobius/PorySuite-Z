"""Formatting toolbar for ``GameTextEdit`` — colors, symbols, variables, emojis,
button glyphs, and pacing tokens.

The toolbar is a horizontal two-row widget that sits above the text area in
``GameTextEdit``. Each button either:

  * **Inserts a glyph or token** at the cursor (e.g. ``{PK}``, ``{PLAYER}``,
    a Unicode emoji).
  * **Applies a foreground color** to the current selection (the color
    buttons). The color is stored as a character format on the
    QTextDocument, NOT as inline ``{COLOR}`` tokens — those are
    regenerated only when the document is serialized back to ``.inc``.

All tokens correspond to actual control codes pokefirered's text engine
recognises at runtime. Nothing is fabricated — the names are 1:1 from
``charmap.txt``.

This module owns:

  * :data:`VANILLA_COLORS` — the 10 named foreground colors the engine
    knows by default.
  * :data:`SYMBOL_BUTTONS`, :data:`BUTTON_GLYPHS`, :data:`EMOJI_GLYPHS`,
    :data:`PACING_TOKENS`, :data:`VARIABLE_TOKENS` — categorised token
    lists for the toolbar.
  * :class:`TextFormatToolbar` — the QWidget itself, holding the rows.
  * :func:`apply_color_to_selection` — pure helper that takes a
    ``QTextEdit`` and a color name and applies it to the selection.

The widget exposes a ``set_braille_mode(bool)`` method that disables every
button which can't be used inside ``.braille`` content (everything except
plain text). The render-type dropdown calls this when the user toggles
braille mode.
"""

from __future__ import annotations

from typing import Optional, Tuple

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QColor, QTextCharFormat, QTextCursor
from PyQt6.QtWidgets import (
    QHBoxLayout, QLabel, QMenu, QPushButton, QTextEdit, QToolButton,
    QVBoxLayout, QWidget,
)


# ── Color model ────────────────────────────────────────────────────────────

# Named foreground colors recognised by pokefirered's text engine. The
# (name, light_name) tuple pairs each with its complementary shadow color
# when one exists; ``None`` means there's no LIGHT_* variant and we
# don't auto-pair the shadow.
#
# The hex colors are *editor preview approximations* — the in-game
# rendering uses palette indices into the active textbox palette, not
# RGB. These hex values are calibrated against vanilla pokefirered's
# default textbox palette so the editor preview roughly matches what
# the game shows. Projects with custom textbox palettes will see
# slightly different in-game colors than the editor preview suggests.
VANILLA_COLORS: dict[str, dict] = {
    "RED":         {"hex": "#cc4444", "shadow": "LIGHT_RED",   "is_quick": True},
    "BLUE":        {"hex": "#4470cc", "shadow": "LIGHT_BLUE",  "is_quick": True},
    "GREEN":       {"hex": "#44aa44", "shadow": "LIGHT_GREEN", "is_quick": True},
    "LIGHT_RED":   {"hex": "#e89494", "shadow": None},
    "LIGHT_GREEN": {"hex": "#94d894", "shadow": None},
    "LIGHT_BLUE":  {"hex": "#94b8e8", "shadow": None},
    "WHITE":       {"hex": "#f0f0f0", "shadow": None},
    "LIGHT_GRAY":  {"hex": "#a0a0a0", "shadow": None},
    "DARK_GRAY":   {"hex": "#404040", "shadow": None},
    "TRANSPARENT": {"hex": "#888888", "shadow": None},
}

# Default reset target after a colored span. The dialogue default in
# vanilla is DARK_GRAY foreground with LIGHT_GRAY shadow.
DEFAULT_RESET_COLOR = "DARK_GRAY"
DEFAULT_RESET_SHADOW = "LIGHT_GRAY"


# ── Token catalogues ───────────────────────────────────────────────────────

# (display_label, token_to_insert)
SYMBOL_BUTTONS: list[Tuple[str, str]] = [
    ("PK",   "{PK}"),
    ("PKMN", "{PKMN}"),
    ("LV",   "{LV}"),
    ("¥",    "¥"),         # currency — literal byte, not a token
    ("♂",    "♂"),
    ("♀",    "♀"),
    ("…",    "…"),
    ("▶",    "▶"),
]

ARROW_BUTTONS: list[Tuple[str, str]] = [
    ("↑", "{UP_ARROW}"),
    ("↓", "{DOWN_ARROW}"),
    ("←", "{LEFT_ARROW}"),
    ("→", "{RIGHT_ARROW}"),
]

BUTTON_GLYPHS: list[Tuple[str, str]] = [
    ("A",     "{A_BUTTON}"),
    ("B",     "{B_BUTTON}"),
    ("L",     "{L_BUTTON}"),
    ("R",     "{R_BUTTON}"),
    ("Start", "{START_BUTTON}"),
    ("Sel",   "{SELECT_BUTTON}"),
    ("D-Pad ↑",  "{DPAD_UP}"),
    ("D-Pad ↓",  "{DPAD_DOWN}"),
    ("D-Pad ←",  "{DPAD_LEFT}"),
    ("D-Pad →",  "{DPAD_RIGHT}"),
    ("D-Pad Any",      "{DPAD_ANY}"),
    ("D-Pad ↑↓",       "{DPAD_UPDOWN}"),
    ("D-Pad ←→",       "{DPAD_LEFTRIGHT}"),
]

# Emoji set is large; goes in a "More…" submenu rather than the main row.
EMOJI_GLYPHS: list[Tuple[str, str]] = [
    ("Heart",       "{EMOJI_HEART}"),
    ("Note",        "{EMOJI_NOTE}"),
    ("Pokeball",    "{EMOJI_BALL}"),
    ("Fire",        "{EMOJI_FIRE}"),
    ("Water",       "{EMOJI_WATER}"),
    ("Leaf",        "{EMOJI_LEAF}"),
    ("Bolt",        "{EMOJI_BOLT}"),
    ("Moon",        "{EMOJI_MOON}"),
    ("Spiral",      "{EMOJI_SPIRAL}"),
    ("Tongue",      "{EMOJI_TONGUE}"),
    ("Happy",       "{EMOJI_HAPPY}"),
    ("Big Smile",   "{EMOJI_BIGSMILE}"),
    ("Mischievous", "{EMOJI_MISCHIEVOUS}"),
    ("Surprised",   "{EMOJI_SURPRISED}"),
    ("Shocked",     "{EMOJI_SHOCKED}"),
    ("Angry",       "{EMOJI_ANGRY}"),
    ("Big Anger",   "{EMOJI_BIGANGER}"),
    ("Irritated",   "{EMOJI_IRRITATED}"),
    ("Evil",        "{EMOJI_EVIL}"),
    ("Tired",       "{EMOJI_TIRED}"),
    ("Neutral",     "{EMOJI_NEUTRAL}"),
    ("Triangle",    "{EMOJI_TRIANGLE}"),
    ("Square",      "{EMOJI_SQUARE}"),
    ("Circle",      "{EMOJI_CIRCLE}"),
    ("Sphere",      "{EMOJI_SPHERE}"),
    ("Big Wheel",   "{EMOJI_BIGWHEEL}"),
    ("Small Wheel", "{EMOJI_SMALLWHEEL}"),
    ("Left Fist",   "{EMOJI_LEFT_FIST}"),
    ("Right Fist",  "{EMOJI_RIGHT_FIST}"),
    ("Acute (´)",   "{EMOJI_ACUTE}"),
    ("Grave (`)",   "{EMOJI_GRAVE}"),
]

VARIABLE_TOKENS: list[Tuple[str, str]] = [
    ("{PLAYER}",    "{PLAYER}"),
    ("{RIVAL}",     "{RIVAL}"),
    ("{KUN}",       "{KUN}"),
    ("{STR_VAR_1}", "{STR_VAR_1}"),
    ("{STR_VAR_2}", "{STR_VAR_2}"),
    ("{STR_VAR_3}", "{STR_VAR_3}"),
]

PACING_TOKENS: list[Tuple[str, str]] = [
    ("Pause",         "{PAUSE 30}"),
    ("Wait Press",    "{PAUSE_UNTIL_PRESS}"),
    ("Music Pause",   "{PAUSE_MUSIC}"),
    ("Music Resume",  "{RESUME_MUSIC}"),
]


# ── Insert / wrap helpers ──────────────────────────────────────────────────


def insert_token(edit: QTextEdit, token: str) -> None:
    """Insert ``token`` at the current cursor position, replacing the
    selection if any. Restores focus to the editor afterwards.

    For emoji tokens (``{EMOJI_*}``), this consults
    :data:`EMOJI_TOKEN_TO_UNICODE` and inserts the Unicode glyph
    instead — the user sees a real emoji in the editor; the glyph is
    converted back to a token only at save time.
    """
    # Lazy import to avoid a circular dependency between
    # ``ui.game_text_edit`` and this module.
    from ui.game_text_edit import EMOJI_TOKEN_TO_UNICODE
    visible = EMOJI_TOKEN_TO_UNICODE.get(token, token)

    cursor = edit.textCursor()
    # Insert with a fresh (default) format so newly-inserted glyphs and
    # placeholder tokens never inherit a stale colour from a previous
    # edit at the cursor position.
    cursor.insertText(visible, QTextCharFormat())
    edit.setTextCursor(cursor)
    edit.setFocus(Qt.FocusReason.OtherFocusReason)


def apply_color_to_selection(
    edit: QTextEdit,
    color_name: str,
) -> None:
    """Apply the named foreground colour to the current selection.

    The colour is set as a character format on the QTextDocument; no
    ``{COLOR}`` / ``{SHADOW}`` tokens are inserted into the visible
    text. Save-time serialization (``GameTextEdit.get_inc_text``) walks
    the document's character formats and regenerates the matching
    ``{COLOR}{SHADOW}`` token pairs around any non-default-coloured
    runs.

    When nothing is selected the colour becomes the active typing
    colour — the next characters the user types are coloured until
    they choose a different colour or move the cursor into a region
    with a different format.

    ``color_name`` must be a key of :data:`VANILLA_COLORS`.
    """
    info = VANILLA_COLORS.get(color_name)
    if info is None:
        return

    fmt = QTextCharFormat()
    fmt.setForeground(QColor(info["hex"]))

    cursor = edit.textCursor()
    if cursor.hasSelection():
        cursor.mergeCharFormat(fmt)
    else:
        # No selection — set the active typing format so the next
        # characters the user types pick up the colour.
        edit.mergeCurrentCharFormat(fmt)
    edit.setFocus(Qt.FocusReason.OtherFocusReason)


# Backwards-compatible alias. The previous toolbar inserted ``{COLOR}``
# tokens into the visible text; the new model stores the colour as a
# character format and regenerates the tokens at save time. The
# ``pair_shadow`` argument is kept for API compatibility but is unused —
# shadow pairing now happens during ``.inc`` emission, not in the editor.
def wrap_selection_with_color(
    edit: QTextEdit,
    color_name: str,
    pair_shadow: bool = True,  # noqa: ARG001 — kept for API compat
) -> None:
    """Backwards-compatible wrapper. Applies ``color_name`` as a
    character format on the current selection. ``pair_shadow`` is
    accepted for API compatibility and ignored — shadow pairing
    now happens at ``.inc`` serialization time, not in the editor.
    """
    apply_color_to_selection(edit, color_name)


# ── Toolbar widget ─────────────────────────────────────────────────────────


_BTN_SS_BASE = (
    "QPushButton, QToolButton {"
    "  background: #2b2b2b; color: #ddd;"
    "  border: 1px solid #444; border-radius: 3px;"
    "  padding: 2px 6px; font-size: 11px;"
    "}"
    "QPushButton:hover, QToolButton:hover {"
    "  background: #3a3a3a; border-color: #666;"
    "}"
    "QPushButton:disabled, QToolButton:disabled {"
    "  background: #1f1f1f; color: #555; border-color: #2a2a2a;"
    "}"
)


class TextFormatToolbar(QWidget):
    """Two-row toolbar holding all formatting buttons.

    Constructor takes the target ``QPlainTextEdit`` so each button can
    operate on it directly without signal wiring. Row 1 is colors +
    variables (semantic / human-readable inserts). Row 2 is symbols +
    button glyphs + emoji submenu + pacing (visual / glyph inserts).
    """

    def __init__(self, edit: QPlainTextEdit, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self._edit = edit
        self._buttons_disable_in_braille: list[QPushButton | QToolButton] = []

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(2)

        # ── Row 1 — Colors + Variables ───────────────────────────────
        row1 = QHBoxLayout()
        row1.setContentsMargins(0, 0, 0, 0)
        row1.setSpacing(3)

        row1.addWidget(self._small_label("Colors:"))
        for cname, info in VANILLA_COLORS.items():
            if not info.get("is_quick"):
                continue
            btn = QPushButton(cname.title())
            btn.setStyleSheet(
                _BTN_SS_BASE
                + f"QPushButton {{ color: {info['hex']}; font-weight: bold; }}"
            )
            btn.setToolTip(
                f"Wrap selection with {{COLOR {cname}}} (paired with "
                f"{{SHADOW {info['shadow']}}}). Resets to {DEFAULT_RESET_COLOR} "
                f"after the span."
            )
            btn.clicked.connect(
                lambda _checked, c=cname: wrap_selection_with_color(
                    self._edit, c, pair_shadow=True))
            self._buttons_disable_in_braille.append(btn)
            row1.addWidget(btn)

        # "More…" color dropdown — every named color the engine knows,
        # including DYNAMIC_COLOR and the LIGHT_X variants.
        more_btn = QToolButton()
        more_btn.setText("More…")
        more_btn.setStyleSheet(_BTN_SS_BASE)
        more_btn.setPopupMode(
            QToolButton.ToolButtonPopupMode.InstantPopup)
        more_btn.setToolTip(
            "Pick from the full list of named colors, including "
            "LIGHT_* shadow tones, DYNAMIC_COLOR slots, WHITE, "
            "TRANSPARENT, etc.")
        more_menu = QMenu(more_btn)
        more_menu.setStyleSheet(
            "QMenu { background: #2b2b2b; color: #ddd; }"
            "QMenu::item:selected { background: #1565c0; }"
        )
        for cname, info in VANILLA_COLORS.items():
            act = more_menu.addAction(cname)
            act.triggered.connect(
                lambda _checked, c=cname:
                wrap_selection_with_color(self._edit, c, pair_shadow=False))
        more_btn.setMenu(more_menu)
        self._buttons_disable_in_braille.append(more_btn)
        row1.addWidget(more_btn)

        row1.addSpacing(12)
        row1.addWidget(self._small_label("Insert:"))
        for label, token in VARIABLE_TOKENS:
            btn = QPushButton(label)
            btn.setStyleSheet(_BTN_SS_BASE)
            btn.setToolTip(f"Insert {token} at cursor")
            btn.clicked.connect(
                lambda _checked, t=token: insert_token(self._edit, t))
            self._buttons_disable_in_braille.append(btn)
            row1.addWidget(btn)

        row1.addStretch(1)
        outer.addLayout(row1)

        # ── Row 2 — Symbols + arrows + buttons + emojis + pacing ─────
        row2 = QHBoxLayout()
        row2.setContentsMargins(0, 0, 0, 0)
        row2.setSpacing(3)

        for label, token in SYMBOL_BUTTONS + ARROW_BUTTONS:
            btn = QPushButton(label)
            btn.setStyleSheet(_BTN_SS_BASE)
            btn.setFixedHeight(22)
            btn.setMinimumWidth(28)
            btn.setToolTip(f"Insert {token!r} at cursor")
            btn.clicked.connect(
                lambda _checked, t=token: insert_token(self._edit, t))
            self._buttons_disable_in_braille.append(btn)
            row2.addWidget(btn)

        # Buttons / D-pad submenu
        gba_btn = QToolButton()
        gba_btn.setText("GBA Buttons…")
        gba_btn.setStyleSheet(_BTN_SS_BASE)
        gba_btn.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        gba_btn.setToolTip(
            "Insert a GBA button glyph (A, B, L, R, Start, Select, D-pad).")
        gba_menu = QMenu(gba_btn)
        gba_menu.setStyleSheet(
            "QMenu { background: #2b2b2b; color: #ddd; }"
            "QMenu::item:selected { background: #1565c0; }"
        )
        for label, token in BUTTON_GLYPHS:
            act = gba_menu.addAction(f"{label}   {token}")
            act.triggered.connect(
                lambda _checked, t=token: insert_token(self._edit, t))
        gba_btn.setMenu(gba_menu)
        self._buttons_disable_in_braille.append(gba_btn)
        row2.addWidget(gba_btn)

        # Emoji submenu
        em_btn = QToolButton()
        em_btn.setText("Emojis…")
        em_btn.setStyleSheet(_BTN_SS_BASE)
        em_btn.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        em_btn.setToolTip(
            "Insert one of pokefirered's font emoji glyphs (heart, "
            "fire, music note, faces, etc.).")
        em_menu = QMenu(em_btn)
        em_menu.setStyleSheet(
            "QMenu { background: #2b2b2b; color: #ddd; }"
            "QMenu::item:selected { background: #1565c0; }"
        )
        for label, token in EMOJI_GLYPHS:
            act = em_menu.addAction(f"{label}   {token}")
            act.triggered.connect(
                lambda _checked, t=token: insert_token(self._edit, t))
        em_btn.setMenu(em_menu)
        self._buttons_disable_in_braille.append(em_btn)
        row2.addWidget(em_btn)

        # Pacing submenu
        pace_btn = QToolButton()
        pace_btn.setText("Pacing…")
        pace_btn.setStyleSheet(_BTN_SS_BASE)
        pace_btn.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        pace_btn.setToolTip(
            "Insert a timing token: a fixed-frame pause, "
            "wait-for-button-press, or pause/resume music.")
        pace_menu = QMenu(pace_btn)
        pace_menu.setStyleSheet(
            "QMenu { background: #2b2b2b; color: #ddd; }"
            "QMenu::item:selected { background: #1565c0; }"
        )
        for label, token in PACING_TOKENS:
            act = pace_menu.addAction(f"{label}   {token}")
            act.triggered.connect(
                lambda _checked, t=token: insert_token(self._edit, t))
        pace_btn.setMenu(pace_menu)
        self._buttons_disable_in_braille.append(pace_btn)
        row2.addWidget(pace_btn)

        row2.addStretch(1)
        outer.addLayout(row2)

    @staticmethod
    def _small_label(text: str) -> QLabel:
        lbl = QLabel(text)
        lbl.setStyleSheet("color: #888; font-size: 10px;")
        return lbl

    def set_braille_mode(self, braille: bool) -> None:
        """Disable every button that doesn't apply in braille content.

        Braille messages compile via the ``.braille`` directive which
        only accepts plain letters, spaces, and basic punctuation —
        every other token is invalid and would either fail to assemble
        or render as garbage. The toolbar greys out all non-applicable
        buttons when braille mode is on so the user can't accidentally
        produce broken output.
        """
        enabled = not braille
        for btn in self._buttons_disable_in_braille:
            btn.setEnabled(enabled)
