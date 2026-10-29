"""Trainers tab: the name limit and the full-party button, tested as behaviour.

These drive the REAL widgets and assert on what they actually do — typing into
the real QLineEdit, calling the real load/add/remove methods — rather than
searching the source for particular words.

TWO DEFECTS, both found in the 2026-10-29 Trainers audit:

1. THE NAME LIMIT DISAGREED WITH ITSELF. `_TrainerDetailPanel` (editing an
   existing trainer) allowed 11 characters; `_AddTrainerDialog` (creating one)
   allowed 10. So a name that was legal to set was illegal to create.

   11 IS THE CORRECT LIMIT AND IS VERIFIED AGAINST THE ENGINE, NOT GUESSED:
   `include/battle.h` declares `u8 trainerName[12]` — 11 characters plus the
   EOS terminator. Every engine read scans to EOS
   (`battle_main.c:1550`, `battle_message.c:2138`, `fame_checker.c:1558`) and
   there is no fixed destination buffer smaller than 12 bytes. Do NOT raise
   this to 12: the terminator would have nowhere to go and the EOS scan in
   battle_main.c would run off the end of the struct field.

2. "+ Add Pokemon" SILENTLY DID NOTHING AT SIX. `_add_party_slot` returned
   early at the cap and the button was a LOCAL variable, so it could never be
   disabled and the user got no indication why the click did nothing.

   The button is now kept as `_add_mon_btn` and its state is driven from
   `_update_party_count()`. That method is deliberately the single place this
   happens, because every path that changes the party already calls it: load,
   add, remove, clear and rematch-tier switch. DO NOT move the enable/disable
   to the individual call sites — that is how a path gets missed.

The six-Pokemon limit itself is unchanged.
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

pytestmark = pytest.mark.skipif(not _QT, reason="PyQt6 not available")

# The QApplication must exist at MODULE IMPORT time. Creating it inside a test
# and then building widgets from it crashes this Qt build.
if _QT:
    _APP = QApplication.instance() or QApplication([])

ENGINE_NAME_CHARS = 11      # u8 trainerName[12] = 11 chars + EOS
PARTY_CAP = 6               # PARTY_SIZE in include/constants/global.h


def _panel():
    """A real _TrainerDetailPanel with the minimum data it needs.

    Deliberately NOT a mock — the point is to exercise the actual widget.
    """
    from ui.trainers_tab_widget import _TrainerDetailPanel
    return _TrainerDetailPanel(
        {"TRAINER_CLASS_YOUNGSTER": "Youngster"},   # class_names
        {},                                          # pic_map
        ["TRAINER_PIC_YOUNGSTER"],                   # trainer_pic_consts
        [("SPECIES_NONE", "None")],                  # species_list
        [("ITEM_NONE", "None")],                     # items_list
        [("MOVE_NONE", "None")],                     # moves_list
    )


def _trainer(name='_("ASH")'):
    return {
        "trainerClass": "TRAINER_CLASS_YOUNGSTER",
        "trainerName": name,
        "trainerPic": "TRAINER_PIC_YOUNGSTER",
        "items": "{}",
        "doubleBattle": "FALSE",
        "aiFlags": "AI_SCRIPT_CHECK_BAD_MOVE",
        "party": "NO_ITEM_DEFAULT_MOVES(sParty_Test)",
    }


def _party(n):
    return {"members": [{"lvl": "5", "species": "SPECIES_NONE"}
                        for _ in range(n)]}


# ── 1. the name limit ───────────────────────────────────────────────────────

def test_engine_really_allows_eleven_characters():
    """Guard the premise itself, read from THIS project's engine header."""
    import re
    hdr = os.path.join(_ROOT, "pokefirered", "include", "battle.h")
    if not os.path.isfile(hdr):
        pytest.skip("game project not present")
    m = re.search(r"u8\s+trainerName\[(\d+)\]",
                  open(hdr, encoding="utf-8", errors="replace").read())
    assert m, "trainerName[] not found in include/battle.h"
    assert int(m.group(1)) - 1 == ENGINE_NAME_CHARS, (
        f"the engine's name buffer changed to {m.group(1)} bytes; the editor "
        f"limits must be re-derived, not left at {ENGINE_NAME_CHARS}"
    )


def test_edit_panel_accepts_eleven_characters():
    p = _panel()
    p._name_edit.setText("")
    p._name_edit.insert("A" * 20)
    assert len(p._name_edit.text()) == ENGINE_NAME_CHARS


def test_new_trainer_dialog_accepts_eleven_characters():
    """THE DEFECT: this used to keep only 10."""
    from ui.trainers_tab_widget import _AddTrainerDialog
    dlg = _AddTrainerDialog({"TRAINER_CLASS_YOUNGSTER": "Youngster"}, {})
    dlg._name_edit.setText("")
    dlg._name_edit.insert("ABCDEFGHIJK")          # exactly 11
    assert dlg._name_edit.text() == "ABCDEFGHIJK", (
        f"an 11-character name can be set when editing a trainer, so it must "
        f"be settable when creating one; kept {dlg._name_edit.text()!r}"
    )


def test_neither_field_accepts_a_twelfth_character():
    """12 would leave no room for the terminator."""
    from ui.trainers_tab_widget import _AddTrainerDialog
    p = _panel()
    dlg = _AddTrainerDialog({"TRAINER_CLASS_YOUNGSTER": "Youngster"}, {})
    for field in (p._name_edit, dlg._name_edit):
        field.setText("")
        field.insert("A" * 12)
        assert len(field.text()) == ENGINE_NAME_CHARS


def test_both_name_fields_agree():
    from ui.trainers_tab_widget import _AddTrainerDialog
    p = _panel()
    dlg = _AddTrainerDialog({"TRAINER_CLASS_YOUNGSTER": "Youngster"}, {})
    assert p._name_edit.maxLength() == dlg._name_edit.maxLength() \
        == ENGINE_NAME_CHARS


def test_an_eleven_character_name_survives_load():
    """Loading a trainer whose name already uses all 11 must not truncate."""
    p = _panel()
    p.load("TRAINER_TEST", _trainer('_("ABCDEFGHIJK")'), _party(1))
    assert "ABCDEFGHIJK" in p._display_lbl.text()
    assert p._name_edit.text() == "ABCDEFGHIJK"


# ── 2. the full-party button ────────────────────────────────────────────────

def test_button_is_enabled_when_the_party_has_room():
    p = _panel()
    p.load("TRAINER_TEST", _trainer(), _party(3))
    assert len(p._party_slots) == 3
    assert p._add_mon_btn.isEnabled()


def test_button_is_disabled_once_the_party_is_full_via_load():
    """Switching to an already-full trainer must disable it too."""
    p = _panel()
    p.load("TRAINER_TEST", _trainer(), _party(PARTY_CAP))
    assert len(p._party_slots) == PARTY_CAP
    assert not p._add_mon_btn.isEnabled(), (
        "loading a trainer that already has six must disable the button")
    assert p._add_mon_btn.toolTip(), (
        "a disabled button must say why it is disabled")


def test_button_disables_when_adding_reaches_the_cap():
    p = _panel()
    p.load("TRAINER_TEST", _trainer(), _party(5))
    assert p._add_mon_btn.isEnabled()
    p._add_party_slot()
    assert len(p._party_slots) == PARTY_CAP
    assert not p._add_mon_btn.isEnabled()


def test_button_re_enables_when_a_party_member_is_removed():
    """The whole point of disabling: it must come back."""
    p = _panel()
    p.load("TRAINER_TEST", _trainer(), _party(PARTY_CAP))
    assert not p._add_mon_btn.isEnabled()
    p._remove_party_slot(p._party_slots[0])
    assert len(p._party_slots) == PARTY_CAP - 1
    assert p._add_mon_btn.isEnabled(), (
        "removing a Pokemon must make the button usable again")
    assert p._add_mon_btn.toolTip() == ""


def test_the_six_limit_itself_is_unchanged():
    p = _panel()
    p.load("TRAINER_TEST", _trainer(), _party(PARTY_CAP))
    for _ in range(4):
        p._add_party_slot()
    assert len(p._party_slots) == PARTY_CAP, (
        "the cap must still hold even if _add_party_slot is called directly")


def test_counter_still_shows_the_count():
    """The existing 6 / 6 counter must be preserved."""
    p = _panel()
    p.load("TRAINER_TEST", _trainer(), _party(PARTY_CAP))
    assert p._party_count_lbl.text() == f"{PARTY_CAP} / {PARTY_CAP}"
    p._remove_party_slot(p._party_slots[0])
    assert p._party_count_lbl.text() == f"{PARTY_CAP - 1} / {PARTY_CAP}"


def test_switching_between_trainers_updates_the_button_each_time():
    """load() is called repeatedly as the user clicks down the list."""
    p = _panel()
    for n, expect_enabled in ((PARTY_CAP, False), (2, True),
                              (PARTY_CAP, False), (0, True), (6, False)):
        p.load("TRAINER_TEST", _trainer(), _party(n))
        assert len(p._party_slots) == n
        assert p._add_mon_btn.isEnabled() is expect_enabled, (
            f"with {n} party members the button should be "
            f"{'enabled' if expect_enabled else 'disabled'}")


def test_clearing_the_party_re_enables_the_button():
    p = _panel()
    p.load("TRAINER_TEST", _trainer(), _party(PARTY_CAP))
    assert not p._add_mon_btn.isEnabled()
    p._clear_party_slots()
    assert p._party_slots == []
    assert p._add_mon_btn.isEnabled()
