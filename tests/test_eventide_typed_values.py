"""Typing is allowed; typing nonsense should look like nonsense.

The pickers stay typeable on purpose — typing is faster than hunting through a
dropdown, and a project may legitimately name a constant this editor has not
parsed. What was missing is feedback: an unrecognised entry went through in
silence and surfaced much later, either as a build error or as a command that
simply did nothing (a graphics name typed into a field that wanted an object
id, for instance).

So an unrecognised entry outlines the box red and says why. It does NOT block
saving. "I do not recognise this" is not the same as "this is wrong", and an
empty constant list means parsing failed rather than that everything is wrong.
"""
from __future__ import annotations

import os

import pytest

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

pytest.importorskip('PyQt6.QtWidgets')

from PyQt6.QtWidgets import QApplication  # noqa: E402

from eventide.ui.widgets import ConstantPicker  # noqa: E402


@pytest.fixture(scope='module')
def qapp():
    return QApplication.instance() or QApplication([])


SONGS = ['MUS_OAK', 'MUS_PALLET', 'MUS_DUMMY']


@pytest.fixture
def picker(qapp):
    return ConstantPicker(SONGS, prefix='MUS_')


def _is_red(p):
    return 'e74c3c' in p.styleSheet()


# ── what counts as fine ───────────────────────────────────────────────────

def test_a_real_constant_is_accepted(picker):
    picker.set_constant('MUS_OAK')
    assert picker.invalid_reason() is None
    assert not _is_red(picker)


def test_an_empty_box_is_not_flagged_here(picker):
    """Emptiness is the required-parameter check's job, not this one's."""
    picker.setEditText('')
    assert picker.invalid_reason() is None
    assert not _is_red(picker)


def test_typing_the_raw_constant_is_fine(picker):
    picker.setEditText('MUS_PALLET')
    assert picker.invalid_reason() is None


def test_correcting_a_typo_clears_the_outline(picker):
    picker.setEditText('MUS_OAKK')
    assert _is_red(picker)
    picker.setEditText('MUS_OAK')
    assert not _is_red(picker)


# ── what gets flagged ─────────────────────────────────────────────────────

def test_a_typo_is_flagged(picker):
    picker.setEditText('MUS_OAKK')
    assert _is_red(picker)
    assert 'MUS_OAKK' in picker.invalid_reason()


def test_a_name_from_the_wrong_family_says_so(picker):
    """A graphics name in a music field: the prefix is the giveaway."""
    picker.setEditText('OBJ_EVENT_GFX_MAN')
    reason = picker.invalid_reason()
    assert 'is not a MUS_' in reason
    assert 'start with MUS_' in reason


def test_the_reason_names_what_was_typed(picker):
    picker.setEditText('MUS_NOPE')
    assert '"MUS_NOPE"' in picker.invalid_reason()


# ── never cry wolf ────────────────────────────────────────────────────────

def test_nothing_is_flagged_when_no_constants_were_parsed(qapp):
    """An empty list means parsing failed — not that every entry is wrong."""
    p = ConstantPicker([], prefix='MUS_')
    p.setEditText('ANYTHING AT ALL')
    assert p.invalid_reason() is None
    assert not _is_red(p)


def test_a_picker_with_no_prefix_still_checks_membership(qapp):
    p = ConstantPicker(['Yes', 'No'], prefix='', show_pretty=False)
    p.setEditText('Maybe')
    assert p.invalid_reason() is not None
    p.setEditText('Yes')
    assert p.invalid_reason() is None


def test_the_value_is_still_handed_back_as_typed(picker):
    """Flagging must not change what gets written — it is a warning only."""
    picker.setEditText('MUS_SOMETHING_CUSTOM')
    assert picker.invalid_reason() is not None
    assert picker.selected_constant() == 'MUS_SOMETHING_CUSTOM'
