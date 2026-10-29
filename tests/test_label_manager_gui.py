"""The Variables & Flags screen shows categories and real references, and
never reclaims a slot without the user saying so.

Companion to test_flag_manager_classification.py, which covers the backend.
This file covers the screen itself: that the filters match the backend's own
protection levels, that the detail panel names the actual maps / scripts /
engine files, and -- the important one -- that the three reclaim gates behave:

  free          goes ahead with no prompt (nothing refers to it);
  review        a plain yes/no;
  investigate   the user must type the slot's own name;
  protected     refused outright.

A snapshot of every file is taken before any rename.
"""
import json
import os

import pytest

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

pytest.importorskip('PyQt6.QtWidgets')

from PyQt6.QtWidgets import QApplication, QMessageBox  # noqa: E402

import ui.label_manager as lm  # noqa: E402
from eventide.backend import var_flag_manager as vf  # noqa: E402


FLAGS_H = """#ifndef GUARD_CONSTANTS_FLAGS_H
#define GUARD_CONSTANTS_FLAGS_H
#define TEMP_FLAGS_START 0x0
#define FLAG_TEMP_1      (TEMP_FLAGS_START + 0x01)
#define TEMP_FLAGS_END   0x1F
#define FLAG_0x020           0x020
#define FLAG_HIDE_SHOPKEEPER 0x021
#define FLAG_HIDE_GUARD      0x022
#define FLAG_ENGINE_ONLY     0x023
#define FLAG_HIDDEN_ITEMS_START 1000
#define FLAG_HIDDEN_ITEM_CAVE_POTION (FLAG_HIDDEN_ITEMS_START + 0)
#define TRAINER_FLAGS_START  0x500
#define TRAINER_FLAGS_END    0x7FF
#define SYS_FLAGS            0x800
#define FLAG_BADGE01_GET     (SYS_FLAGS + 0x20)
#define FLAGS_COUNT (FLAG_BADGE01_GET + 1)
#endif
"""

VARS_H = """#define VARS_START 0x4000
#define TEMP_VARS_START 0x4000
#define VAR_TEMP_0 (TEMP_VARS_START + 0x0)
#define TEMP_VARS_END VAR_TEMP_0
#define VAR_0x4011 0x4011
#define VARS_END 0x40FF
#define SPECIAL_VARS_START 0x8000
"""


@pytest.fixture(scope='module')
def qapp():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def widget(qapp, tmp_path, monkeypatch):
    vf.invalidate_cache()
    inc = tmp_path / 'include' / 'constants'
    inc.mkdir(parents=True)
    (inc / 'flags.h').write_text(FLAGS_H, encoding='utf-8')
    (inc / 'vars.h').write_text(VARS_H, encoding='utf-8')

    src = tmp_path / 'src'
    src.mkdir()
    (src / 'engine.c').write_text(
        'void f(void) { FlagSet(FLAG_ENGINE_ONLY); }\n', encoding='utf-8')

    maps = tmp_path / 'data' / 'maps'
    (maps / 'CaveOne').mkdir(parents=True)
    (maps / 'CaveOne' / 'scripts.inc').write_text(
        '\tsetflag FLAG_HIDE_SHOPKEEPER\n\tsetflag FLAG_HIDE_GUARD\n',
        encoding='utf-8')
    (maps / 'CaveTwo').mkdir(parents=True)
    (maps / 'CaveTwo' / 'scripts.inc').write_text(
        '\tclearflag FLAG_HIDE_GUARD\n', encoding='utf-8')

    backups = tmp_path / '_backups'
    w = lm.LabelManagerWidget()
    monkeypatch.setattr(w, '_backup_root', lambda: str(backups))
    w.load_project(str(tmp_path))
    w._run_scan(force=True)
    yield w, tmp_path, backups
    vf.invalidate_cache()


def _select(w, name):
    w.select_constant(name)
    assert w._current_const_name() == name


def _rows(w):
    return [w._const_list.item(i).data(lm.Qt.ItemDataRole.UserRole)
            for i in range(w._const_list.count())]


# ── what the list shows ─────────────────────────────────────────────────────

def test_the_scan_classifies_everything_the_screen_shows(widget):
    w, _, _ = widget
    assert w._scanned
    assert all(e.get('protection') for e, _ in w._iter_entries())


def test_free_only_filter_shows_just_the_free_slots(widget):
    w, _, _ = widget
    w._filter_combo.setCurrentIndex(3)          # "Free only"
    rows = _rows(w)
    assert 'FLAG_0x020' in rows
    assert 'FLAG_HIDE_SHOPKEEPER' not in rows
    assert 'FLAG_ENGINE_ONLY' not in rows


def test_reclaim_candidates_filter_shows_only_single_use_slots(widget):
    w, _, _ = widget
    w._filter_combo.setCurrentIndex(4)          # "Reclaim candidates"
    rows = _rows(w)
    assert 'FLAG_HIDE_SHOPKEEPER' in rows       # one map
    assert 'FLAG_HIDE_GUARD' not in rows        # two maps
    assert 'FLAG_0x020' not in rows             # free, not a "candidate"
    assert 'FLAG_BADGE01_GET' not in rows


def test_protected_filter_collects_engine_badges_and_temporaries(widget):
    w, _, _ = widget
    w._filter_combo.setCurrentIndex(6)          # "Protected"
    rows = _rows(w)
    for name in ('FLAG_ENGINE_ONLY', 'FLAG_BADGE01_GET', 'FLAG_TEMP_1'):
        assert name in rows, name


# ── what the detail panel says ──────────────────────────────────────────────

def test_detail_panel_names_the_maps_that_use_a_flag(widget):
    w, _, _ = widget
    _select(w, 'FLAG_HIDE_GUARD')
    text = w._detail_refs.toPlainText()
    assert 'CaveOne' in text and 'CaveTwo' in text
    assert 'Maps (2)' in text


def test_detail_panel_names_the_engine_file(widget):
    w, _, _ = widget
    _select(w, 'FLAG_ENGINE_ONLY')
    text = w._detail_refs.toPlainText()
    assert 'src/engine.c' in text
    assert 'Engine source' in text


def test_detail_panel_says_plainly_when_nothing_uses_it(widget):
    w, _, _ = widget
    _select(w, 'FLAG_0x020')
    assert 'Nothing in this project refers' in w._detail_refs.toPlainText()


def test_detail_header_shows_category_protection_and_slot(widget):
    w, _, _ = widget
    _select(w, 'FLAG_HIDE_SHOPKEEPER')
    head = w._detail_status.text()
    assert vf.CATEGORIES['event'] in head
    assert vf.PROTECTION['review'] in head
    assert 'slot 33' in head
    assert 'general' in head


# ── the reclaim gates ───────────────────────────────────────────────────────

def test_a_free_slot_needs_no_confirmation(widget, monkeypatch):
    w, _, _ = widget
    monkeypatch.setattr(lm.QMessageBox, 'question',
                        staticmethod(_boom), raising=False)
    monkeypatch.setattr(lm.QInputDialog, 'getText',
                        staticmethod(_boom), raising=False)
    e = w._status_by_name['FLAG_0x020']
    assert w._confirm_reclaim('FLAG_0x020', e) is True


def _boom(*a, **k):
    raise AssertionError('the user should not have been asked')


def test_a_single_use_slot_asks_yes_or_no(widget, monkeypatch):
    w, _, _ = widget
    asked = {}

    def question(parent, title, text, *a, **k):
        asked['text'] = text
        return QMessageBox.StandardButton.No

    monkeypatch.setattr(lm.QMessageBox, 'question', staticmethod(question),
                        raising=False)
    e = w._status_by_name['FLAG_HIDE_SHOPKEEPER']
    assert w._confirm_reclaim('FLAG_HIDE_SHOPKEEPER', e) is False
    assert '1 map' in asked['text']


def test_a_shared_slot_makes_you_type_its_name(widget, monkeypatch):
    w, _, _ = widget
    monkeypatch.setattr(lm.QMessageBox, 'question',
                        staticmethod(_boom), raising=False)
    monkeypatch.setattr(lm.QMessageBox, 'information',
                        staticmethod(lambda *a, **k: None), raising=False)
    monkeypatch.setattr(lm.QInputDialog, 'getText',
                        staticmethod(lambda *a, **k: ('not the name', True)),
                        raising=False)
    e = w._status_by_name['FLAG_HIDE_GUARD']
    assert w._confirm_reclaim('FLAG_HIDE_GUARD', e) is False

    monkeypatch.setattr(lm.QInputDialog, 'getText',
                        staticmethod(lambda *a, **k: ('FLAG_HIDE_GUARD', True)),
                        raising=False)
    assert w._confirm_reclaim('FLAG_HIDE_GUARD', e) is True


def test_a_protected_slot_is_refused(widget, monkeypatch):
    w, _, _ = widget
    told = {}

    def information(parent, title, text, *a, **k):
        told['text'] = text

    monkeypatch.setattr(lm.QMessageBox, 'information',
                        staticmethod(information), raising=False)
    monkeypatch.setattr(lm.QInputDialog, 'getText',
                        staticmethod(_boom), raising=False)
    for name in ('FLAG_ENGINE_ONLY', 'FLAG_BADGE01_GET', 'FLAG_TEMP_1'):
        e = w._status_by_name[name]
        assert w._confirm_reclaim(name, e) is False, name
    assert 'will not rename' in told['text']


def test_cancelling_the_typed_confirmation_changes_nothing(widget, monkeypatch):
    w, root, _ = widget
    before = (root / 'data/maps/CaveOne/scripts.inc').read_bytes()
    monkeypatch.setattr(lm.QInputDialog, 'getText',
                        staticmethod(lambda *a, **k: ('', False)),
                        raising=False)
    e = w._status_by_name['FLAG_HIDE_GUARD']
    assert w._confirm_reclaim('FLAG_HIDE_GUARD', e) is False
    assert (root / 'data/maps/CaveOne/scripts.inc').read_bytes() == before


# ── the rename itself ───────────────────────────────────────────────────────

def test_reclaiming_renames_everywhere_and_leaves_a_backup(widget, monkeypatch):
    w, root, backups = widget
    monkeypatch.setattr(lm.QMessageBox, 'information',
                        staticmethod(lambda *a, **k: None), raising=False)
    original = (root / 'data/maps/CaveOne/scripts.inc').read_text(
        encoding='utf-8')

    w._do_code_rename('FLAG_HIDE_SHOPKEEPER', 'FLAG_HYRULE_SHOP',
                      'flag', friendly='', is_new=False)

    script = (root / 'data/maps/CaveOne/scripts.inc').read_text(
        encoding='utf-8')
    assert 'FLAG_HYRULE_SHOP' in script
    assert 'FLAG_HIDE_SHOPKEEPER' not in script
    hdr = (root / 'include/constants/flags.h').read_text(encoding='utf-8')
    assert 'FLAG_HYRULE_SHOP' in hdr

    stamps = list(backups.iterdir())
    assert len(stamps) == 1
    saved = stamps[0] / 'data' / 'maps' / 'CaveOne' / 'scripts.inc'
    assert saved.read_text(encoding='utf-8') == original


def test_reclaiming_carries_the_friendly_label_over(widget, monkeypatch):
    w, root, _ = widget
    monkeypatch.setattr(lm.QMessageBox, 'information',
                        staticmethod(lambda *a, **k: None), raising=False)
    w._labels['FLAG_HIDE_SHOPKEEPER'] = {'label': 'Shop open', 'notes': 'x'}

    w._do_code_rename('FLAG_HIDE_SHOPKEEPER', 'FLAG_SHOP_OPEN',
                      'flag', friendly='', is_new=False)

    assert 'FLAG_HIDE_SHOPKEEPER' not in w._labels
    assert w._labels['FLAG_SHOP_OPEN']['label'] == 'Shop open'


def test_the_rescan_after_a_rename_sees_the_new_name(widget, monkeypatch):
    """The scan is memoised, so a stale cache here would show a ghost slot."""
    w, _, _ = widget
    monkeypatch.setattr(lm.QMessageBox, 'information',
                        staticmethod(lambda *a, **k: None), raising=False)
    w._do_code_rename('FLAG_HIDE_SHOPKEEPER', 'FLAG_SHOP_OPEN',
                      'flag', friendly='', is_new=False)
    assert 'FLAG_SHOP_OPEN' in w._status_by_name
    assert 'FLAG_HIDE_SHOPKEEPER' not in w._status_by_name


def test_labels_are_saved_without_touching_game_files(widget):
    w, root, _ = widget
    before = (root / 'include/constants/flags.h').read_bytes()
    w._labels['FLAG_0x020'] = {'label': 'Spare one', 'notes': ''}
    w._save_labels()
    data = json.loads(
        (root / 'porysuite_labels.json').read_text(encoding='utf-8'))
    assert data['labels']['FLAG_0x020']['label'] == 'Spare one'
    assert (root / 'include/constants/flags.h').read_bytes() == before
