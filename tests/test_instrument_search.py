"""The Piano Roll's instrument picker can be searched by typing.

A voicegroup has up to 128 slots and a real one is densely filled, with names
like "Sc88Pro Nylon Str Guitar" and "Zelda3 Z3 Trumpet". Picking one meant
scrolling the whole list and reading it. Typing "guitar" now narrows it to the
slots whose names contain that, anywhere in the name, in any case.

The matching is plain text against the names and nothing else. A synonym
layer that also matched instrument families was tried on 2026-11-18 and
removed the same day -- typing "flute" offered an ocarina. If "fl" is typed,
what comes back contains "fl".

WHAT MUST NOT BREAK:

* The wheel must still never change the instrument. That rule exists because
  this very dropdown was observed silently rewriting songs' VOICE values while
  the user scrolled the page over it (CLAUDE.md -> UI/UX Rules, and
  `ui/custom_widgets/scroll_guard.py`). Making the box editable must not have
  reopened it.
* The box must never show an instrument the track is not playing. It is
  editable so it can be TYPED INTO, not so it can hold arbitrary text.
* Picking an instrument must still report the slot number the song uses.
* What comes back must contain what was typed. Nothing may be offered on
  the grounds that it is a similar instrument.
"""
import os
import sys

import pytest

ROOT_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
for _p in (ROOT_DIR, os.path.join(ROOT_DIR, 'ui'), os.path.join(ROOT_DIR, 'core')):
    if _p not in sys.path:
        sys.path.insert(0, _p)

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

pytest.importorskip('PyQt6.QtWidgets')

from PyQt6.QtCore import QPoint, QPointF, Qt  # noqa: E402
from PyQt6.QtGui import QWheelEvent  # noqa: E402
from PyQt6.QtWidgets import QApplication, QComboBox  # noqa: E402

from ui.custom_widgets.scroll_guard import install_scroll_guard  # noqa: E402

from ui.piano_roll_tracks import TrackRow  # noqa: E402

# A voicegroup shaped like a real one: several guitars, several trumpets,
# a flute, mixed case, and gaps.
NAMES = [''] * 128
NAMES[0] = 'Sc88Pro Piano1 60'
NAMES[3] = 'Mcsf2 Banjo'
# Not a flute. It contains "Fl", so a search for "fl" must offer it.
NAMES[10] = 'Ethnic Flavours Kotsuzumi'
NAMES[17] = 'Zelda3 Z3 Trumpet Octave'
NAMES[24] = 'Sc88Pro Nylon Str Guitar'
NAMES[25] = 'Zelda3 Z3 Trumpet'
NAMES[42] = 'Sc88Pro Trumpet 72'
NAMES[61] = 'Zelda3 Z3 Flute'
NAMES[67] = 'Unused GUITAR Separates Power Chord'
NAMES[73] = 'Ocarina'
NAMES[77] = 'Sd90 Enhanced Delay Shaku'
NAMES[78] = 'Sd90 Classical Whistle'
NAMES[90] = 'Wave 56'


@pytest.fixture(scope='module')
def qapp():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def row(qapp):
    r = TrackRow(0, {'instrument': 3, 'volume': 100, 'note_count': 4}, NAMES)
    yield r
    r.deleteLater()


def _matches(combo, typed):
    """What the dropdown offers after typing *typed*."""
    comp = combo.completer()
    comp.setCompletionPrefix(typed)
    return [comp.completionModel().index(i, 0).data()
            for i in range(comp.completionModel().rowCount())]


# ── Searching ───────────────────────────────────────────────────────────────

def test_typing_guitar_lists_only_the_guitars(row):
    found = _matches(row._inst_combo, 'guitar')
    assert found == ['24: Sc88Pro Nylon Str Guitar',
                     '67: Unused GUITAR Separates Power Chord']


def test_the_search_is_case_insensitive(row):
    assert _matches(row._inst_combo, 'GUITAR') == _matches(row._inst_combo, 'guitar')


def test_it_matches_anywhere_in_the_name_not_just_the_start(row):
    """The names start with a slot number, so start-matching would find nothing."""
    assert _matches(row._inst_combo, 'nylon') == ['24: Sc88Pro Nylon Str Guitar']


def test_typing_trumpet_lists_every_trumpet(row):
    assert _matches(row._inst_combo, 'trumpet') == [
        '17: Zelda3 Z3 Trumpet Octave',
        '25: Zelda3 Z3 Trumpet',
        '42: Sc88Pro Trumpet 72',
    ]


def test_a_name_that_is_not_there_offers_nothing(row):
    assert _matches(row._inst_combo, 'harpsichord') == []


def test_every_slot_is_still_browsable(row):
    """Searching must not have replaced the full list."""
    assert row._inst_combo.count() == 128
    assert row._inst_combo.itemText(3) == '3: Mcsf2 Banjo'
    assert row._inst_combo.itemText(1) == '1: (empty)'


# ── Only what was typed ─────────────────────────────────────────────────────

def test_every_match_contains_what_was_typed(row):
    """The whole contract, in one assertion.

    A synonym layer was added on 2026-11-18 so that "flute" would also
    offer an ocarina, a shakuhachi and a whistle. It was wrong and was
    removed the same day. This is the guard against it coming back in any
    form: whatever is offered, the typed text is in it.
    """
    for typed in ('fl', 'flute', 'gui', 'trump', 'z3', 'sc88', 'e'):
        for name in _matches(row._inst_combo, typed):
            assert typed in name.lower(), (typed, name)


def test_flute_does_not_offer_the_ocarina(row):
    """The specific thing that was wrong, named so it stays wrong forever."""
    assert _matches(row._inst_combo, 'flute') == ['61: Zelda3 Z3 Flute']
    assert '73: Ocarina' in [row._inst_combo.itemText(i)
                             for i in range(row._inst_combo.count())]


def test_fl_offers_every_name_with_fl_in_it(row):
    """Including the ones that are not flutes. "Fl" is "fl"."""
    assert _matches(row._inst_combo, 'fl') == [
        '10: Ethnic Flavours Kotsuzumi',
        '61: Zelda3 Z3 Flute',
    ]


# ── Picking ─────────────────────────────────────────────────────────────────

def test_picking_reports_the_slot_the_song_uses(row):
    got = []
    row.instrument_changed.connect(lambda i, slot: got.append(slot))
    row._inst_combo.setCurrentIndex(24)
    assert got == [24]


def test_the_slot_number_rides_on_the_item(row):
    """So the handler reads the slot, not the row it happens to sit on."""
    assert row._inst_combo.itemData(24) == 24
    assert row._inst_combo.itemData(67) == 67


def test_the_voicegroups_instruments_can_be_replaced(row):
    """Changing voicegroup must re-offer the NEW names, not the old ones."""
    other = [''] * 128
    other[5] = 'Church Organ'
    row.update_instrument_names(other)
    assert _matches(row._inst_combo, 'guitar') == []
    assert _matches(row._inst_combo, 'organ') == ['5: Church Organ']


# ── What must not break ─────────────────────────────────────────────────────

def _wheel():
    return QWheelEvent(
        QPointF(5, 5), QPointF(5, 5), QPoint(0, -120), QPoint(0, -120),
        Qt.MouseButton.NoButton, Qt.KeyboardModifier.NoModifier,
        Qt.ScrollPhase.NoScrollPhase, False)


def test_the_wheel_still_never_changes_the_instrument(row):
    """The rule this dropdown was the reason for.

    The guard is an EVENT FILTER, so the wheel has to be delivered the way Qt
    delivers it. Calling `combo.wheelEvent(...)` goes straight to the handler
    and past every filter on the widget -- it reports a change that a real
    wheel never makes, and it is not a test of the guard at all.
    """
    combo = row._inst_combo
    combo.setCurrentIndex(24)
    for _ in range(5):
        QApplication.sendEvent(combo, _wheel())
    assert combo.currentIndex() == 24


def test_making_the_box_typable_did_not_reopen_the_wheel_hole(row):
    """An editable combo guards exactly as well as the plain one it replaced."""
    plain = QComboBox()
    for i in range(128):
        plain.addItem(f'{i}: x', i)
    install_scroll_guard(plain)
    plain.setCurrentIndex(24)
    row._inst_combo.setCurrentIndex(24)
    for _ in range(5):
        QApplication.sendEvent(plain, _wheel())
        QApplication.sendEvent(row._inst_combo, _wheel())
    assert plain.currentIndex() == 24
    assert row._inst_combo.currentIndex() == 24


def test_typing_something_that_matches_nothing_is_put_back(row):
    combo = row._inst_combo
    combo.setCurrentIndex(24)
    combo.setEditText('banana')
    combo._restore_text()
    assert combo.currentText() == '24: Sc88Pro Nylon Str Guitar'
    assert combo.currentIndex() == 24


def test_typing_cannot_add_a_new_instrument(row):
    combo = row._inst_combo
    before = combo.count()
    combo.setEditText('Totally New Instrument')
    combo._restore_text()
    assert combo.count() == before


def test_the_initial_instrument_is_shown(row):
    assert row._inst_combo.currentIndex() == 3
    assert row._inst_combo.currentText() == '3: Mcsf2 Banjo'


def test_a_phrase_across_two_words_matches(row):
    """The typed text is matched against the name as it is shown."""
    assert _matches(row._inst_combo, 'nylon str') == [
        '24: Sc88Pro Nylon Str Guitar']


def test_picking_a_searched_match_sets_that_slot(row):
    """Reached by typing, but it is still slot 61 that plays."""
    got = []
    row.instrument_changed.connect(lambda i, slot: got.append(slot))
    row._inst_combo.setCurrentIndex(61)
    assert got == [61]
    assert row._inst_combo.itemData(61) == 61
