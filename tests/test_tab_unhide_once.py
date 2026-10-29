"""The Variables & Flags tab gets its toolbar icon back, exactly once.

WHAT HAPPENED (found 2026-10-25). v0.1.47b added Settings -> Tabs, and the
two brand-new editors it shipped with were hidden on first run. "labels" --
the Variables & Flags / Flag Manager page -- was swept into that same default
list. Nothing was deleted, renamed or disconnected: only the toolbar icon
stopped being drawn, and the page stayed reachable through Tools and View. For
anyone who had already opened Settings -> Tabs once, "labels" was then written
into their saved settings, so dropping it from the default list alone would
not have brought the icon back.

THE RULE: the correction is applied once, recorded under its own settings key,
and never repeated -- so a user who deliberately hides the tab afterwards
keeps it hidden.
"""
import os
import sys

import pytest

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

# unified_mainwindow does `from app_info import ...`, which only resolves once
# core/ is on the path — app.py puts it there at startup.
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _sub in ('core', 'ui'):
    _p = os.path.join(_ROOT, _sub)
    if _p not in sys.path:
        sys.path.insert(0, _p)

pytest.importorskip('PyQt6.QtCore')

from PyQt6.QtCore import QSettings  # noqa: E402

import ui.unified_mainwindow as umw  # noqa: E402


class Stub:
    """Just the two attributes the correction touches."""

    _UNHIDE_ONCE = umw.UnifiedMainWindow._UNHIDE_ONCE
    _apply_unhide_once = umw.UnifiedMainWindow._apply_unhide_once

    def __init__(self, hidden):
        self._hidden_tabs = set(hidden)


@pytest.fixture
def settings(tmp_path):
    return QSettings(str(tmp_path / 'settings.ini'), QSettings.Format.IniFormat)


def test_labels_is_no_longer_hidden_by_default():
    assert 'labels' not in umw.UnifiedMainWindow._default_hidden_tabs
    # the two gated editors stay hidden until a project supports them
    assert 'encounters' in umw.UnifiedMainWindow._default_hidden_tabs
    assert 'fame_checker' in umw.UnifiedMainWindow._default_hidden_tabs


def test_an_already_saved_hidden_labels_is_put_back(settings):
    s = Stub({'fame_checker', 'labels'})
    s._apply_unhide_once(settings)
    assert s._hidden_tabs == {'fame_checker'}
    assert settings.value('tabs/hidden') == ['fame_checker']
    assert settings.value('tabs/unhidden_labels_once', False, type=bool)


def test_hiding_it_again_afterwards_sticks(settings):
    Stub({'labels'})._apply_unhide_once(settings)        # the one correction
    again = Stub({'labels'})                             # user hid it on purpose
    again._apply_unhide_once(settings)
    assert again._hidden_tabs == {'labels'}


def test_nothing_is_written_when_the_tab_was_not_hidden(settings):
    s = Stub({'encounters'})
    s._apply_unhide_once(settings)
    assert s._hidden_tabs == {'encounters'}
    assert settings.value('tabs/hidden') is None         # untouched
    assert settings.value('tabs/unhidden_labels_once', False, type=bool)


def test_other_hidden_tabs_are_left_alone(settings):
    s = Stub({'labels', 'encounters', 'fame_checker'})
    s._apply_unhide_once(settings)
    assert s._hidden_tabs == {'encounters', 'fame_checker'}
