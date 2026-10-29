"""Item names are limited by ENCODED BYTES, not by characters typed.

THE CORRUPTION (confirmed 2026-10-29, reproduced before this was written).
`ITEM_POKEBLOCK_CASE` is named `{POKEBLOCK} CASE`. The editor capped the name
field with `QLineEdit.setMaxLength(13)` — a CHARACTER cap — so `setText()`
silently truncated the stored name to `{POKEBLOCK} C` the moment the item was
loaded. Editing any UNRELATED field then saved that shortened name over the
user's data. Changing a price lost the name.

The count was wrong in principle, not just in size. The engine's budget is
BYTES AFTER THE CHARMAP, and this project's `charmap.txt:52` says

    POKEBLOCK   = 55 56 57 58 59        -> 5 bytes

so `{POKEBLOCK} CASE` is 5 + 1 + 4 = 10 bytes plus a terminator = 11, well
inside the 14-byte buffer. It was never too long. 16 characters was never the
right number to measure.

THE RULES THIS LOCKS IN:
  * Loading never truncates. `setMaxLength` must NOT be used as the limit.
  * An unrelated edit leaves the name byte-identical.
  * Over-limit names are REJECTED on commit, keeping the previous value —
    not silently trimmed.
  * Unknown or custom `{CODES}` are never guessed at. If a name cannot be
    measured, it is left alone rather than altered.
  * The terminator is counted; a name may use at most LIMIT-1 bytes of text.
"""

import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _p in (_ROOT, os.path.join(_ROOT, "core"), os.path.join(_ROOT, "ui")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import pytest  # noqa: E402

try:
    from PyQt6.QtWidgets import QApplication
    _QT = True
except Exception:
    _QT = False

if _QT:
    _APP = QApplication.instance() or QApplication([])

PROJECT = os.path.join(_ROOT, "pokefirered")
_HAS_PROJECT = os.path.isfile(os.path.join(PROJECT, "charmap.txt"))


# ── 1. the measurer ─────────────────────────────────────────────────────────

@pytest.mark.skipif(not _HAS_PROJECT, reason="game project not present")
class TestEncodedLength:

    def _m(self):
        from core.gba_text_bytes import load_text_bytes
        return load_text_bytes(PROJECT)

    def test_plain_ascii_is_one_byte_per_character(self):
        m = self._m()
        assert m.encoded_len("POTION") == 6
        assert m.encoded_len("") == 0

    def test_a_multi_byte_control_code_counts_its_real_bytes(self):
        """charmap.txt:52 — POKEBLOCK = 55 56 57 58 59."""
        m = self._m()
        assert m.encoded_len("{POKEBLOCK}") == 5

    def test_the_real_item_name_fits_the_engine_buffer(self):
        """The whole point: this name was never too long."""
        m = self._m()
        n = m.encoded_len("{POKEBLOCK} CASE")
        assert n == 10, f"expected 5+1+4=10 bytes, measured {n}"
        assert n < m.name_limit(), (
            f"{n} bytes must fit the {m.name_limit()}-byte buffer")

    def test_two_byte_and_one_byte_codes(self):
        m = self._m()
        assert m.encoded_len("{PKMN}") == 2      # 53 54
        assert m.encoded_len("{PK}") == 1        # 53

    def test_characters_counted_is_not_bytes_counted(self):
        """Guard the premise: the two numbers genuinely disagree here."""
        m = self._m()
        s = "{POKEBLOCK} CASE"
        assert len(s) == 16
        assert m.encoded_len(s) == 10

    def test_an_unknown_code_is_unmeasurable_not_guessed(self):
        m = self._m()
        assert m.encoded_len("{NOT_A_REAL_CODE}") is None
        assert m.encoded_len("A{NOT_A_REAL_CODE}B") is None

    def test_fits_is_conservative_about_what_it_cannot_measure(self):
        m = self._m()
        # Unmeasurable must never be reported as over-limit, because the
        # consequence of "over" is rejecting the user's text.
        assert m.fits("{NOT_A_REAL_CODE}") is True

    def test_the_terminator_is_accounted_for(self):
        m = self._m()
        cap = m.name_limit()                 # the BUFFER size
        assert m.fits("A" * (cap - 1)) is True
        assert m.fits("A" * cap) is False, (
            "a name filling the whole buffer leaves no room for the terminator")

    def test_name_limit_comes_from_the_project(self):
        m = self._m()
        assert isinstance(m.name_limit(), int) and m.name_limit() > 1


# ── 2. the editor must not corrupt the name ─────────────────────────────────

@pytest.mark.skipif(not _QT or not _HAS_PROJECT, reason="needs Qt + project")
class TestItemPanelDoesNotTruncate:

    def _panel(self):
        from ui.items_tab_widget import ItemDetailPanel
        p = ItemDetailPanel()
        p.set_name_limit_for_project(PROJECT) \
            if hasattr(p, "set_name_limit_for_project") else None
        return p

    def _item(self, name):
        return {"itemId": "ITEM_TEST", "english": name, "price": 100,
                "description_english": "A test item.",
                "holdEffect": "HOLD_EFFECT_NONE", "holdEffectParam": 0,
                "importance": 0, "registrability": 0,
                "pocket": "POCKET_ITEMS", "type": 0,
                "fieldUseFunc": "NULL", "battleUsage": 0,
                "battleUseFunc": "NULL", "secondaryId": 0}

    def test_loading_a_control_code_name_does_not_truncate_it(self):
        """THE DEFECT: '{POKEBLOCK} CASE' came back as '{POKEBLOCK} C'."""
        p = self._panel()
        p.load_item("ITEM_TEST", self._item("{POKEBLOCK} CASE"))
        assert p.f_name.text() == "{POKEBLOCK} CASE", (
            f"the name was altered on load: {p.f_name.text()!r}")

    def test_editing_an_unrelated_field_preserves_the_name(self):
        """THE DAMAGE PATH: change a price, lose the name."""
        p = self._panel()
        original = "{POKEBLOCK} CASE"
        rec = self._item(original)
        p.load_item("ITEM_TEST", rec)
        p.f_price.setValue(999) if hasattr(p, "f_price") else None
        out = p.collect(rec)
        assert out["english"] == original, (
            f"an unrelated edit changed the name to {out['english']!r}")

    def test_a_plain_name_still_round_trips(self):
        p = self._panel()
        rec = self._item("Potion")
        p.load_item("ITEM_TEST", rec)
        assert p.f_name.text() == "Potion"
        assert p.collect(rec)["english"] == "Potion"

    def test_the_user_can_still_edit_the_name(self):
        p = self._panel()
        rec = self._item("Potion")
        p.load_item("ITEM_TEST", rec)
        p.f_name.setText("Super Potion")
        assert p.collect(rec)["english"] == "Super Potion"

    def test_a_control_code_name_remains_editable(self):
        p = self._panel()
        rec = self._item("{POKEBLOCK} CASE")
        p.load_item("ITEM_TEST", rec)
        p.f_name.setText("{POKEBLOCK} BOX")
        assert p.collect(rec)["english"] == "{POKEBLOCK} BOX"

    def test_an_exact_limit_name_is_accepted(self):
        from core.gba_text_bytes import load_text_bytes
        m = load_text_bytes(PROJECT)
        p = self._panel()
        rec = self._item("Potion")
        p.load_item("ITEM_TEST", rec)
        exact = "A" * (m.name_limit() - 1)
        p.f_name.setText(exact)
        assert p.collect(rec)["english"] == exact

    def test_an_over_limit_name_is_rejected_and_the_old_value_kept(self):
        from core.gba_text_bytes import load_text_bytes
        m = load_text_bytes(PROJECT)
        p = self._panel()
        rec = self._item("Potion")
        p.load_item("ITEM_TEST", rec)
        p.f_name.setText("A" * (m.name_limit() + 20))
        out = p.collect(rec)
        assert out["english"] == "Potion", (
            f"an over-limit name must be refused and the previous value kept, "
            f"not trimmed; got {out['english']!r}")

    def test_rejection_does_not_silently_trim(self):
        from core.gba_text_bytes import load_text_bytes
        m = load_text_bytes(PROJECT)
        p = self._panel()
        rec = self._item("Potion")
        p.load_item("ITEM_TEST", rec)
        too_long = "B" * (m.name_limit() + 5)
        p.f_name.setText(too_long)
        out = p.collect(rec)
        assert out["english"] != too_long[:m.name_limit() - 1], (
            "a truncated version must never be written")

    def test_an_unknown_code_name_is_left_alone(self):
        """Never guess at a custom code — pass the text through untouched."""
        p = self._panel()
        weird = "{MY_CUSTOM} X"
        rec = self._item(weird)
        p.load_item("ITEM_TEST", rec)
        assert p.f_name.text() == weird
        assert p.collect(rec)["english"] == weird

    def test_opening_and_collecting_does_not_reformat(self):
        """No normalisation, no case change, no whitespace tidying."""
        p = self._panel()
        for name in ("{POKEBLOCK} CASE", "Potion", "x  y", " lead ",
                     "{PKMN} FOOD"):
            rec = self._item(name)
            p.load_item("ITEM_TEST", rec)
            assert p.collect(rec)["english"] == name, f"reformatted {name!r}"

    def test_max_length_is_not_used_as_the_limit(self):
        """The mechanism itself: a character cap truncates on setText()."""
        p = self._panel()
        p.load_item("ITEM_TEST", self._item("{POKEBLOCK} CASE"))
        ml = p.f_name.maxLength()
        assert ml >= len("{POKEBLOCK} CASE"), (
            f"maxLength={ml} still truncates stored names on load; the byte "
            f"limit must be enforced on commit, not by the widget")
