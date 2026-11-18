"""Sampled instruments must not be left silent by the Voicegroups editor.

BUGS.md 2026-11-17 "Z3 instruments silent". Sampled (DirectSound) voices read
their envelope on a 0-255 scale; square / noise / wave voices on 0-7 / 0-15.
Changing a filler square slot to a sample kept the square's (0, 0, 15, 0) -
on a sample that is "attack 0: never fades in", so the instrument made no
sound, in the editor's preview and in the game. 13 slots of voicegroup 191
(Cremia's) and one of voicegroup 014 had it.

The real ``SoundEditorTab`` on a throwaway copy of the game's sound folder:
* switching a slot to a sample gives it the sampled envelope (255, 0, 255, 0),
  and switching back gives the square one (0, 0, 15, 0);
* a sampled slot with attack 0 shows the warning, "Make Them Audible" fixes
  every such slot in the voicegroup, marks them unsaved, and the save writes
  exactly those lines and nothing else.
"""
from __future__ import annotations

import hashlib
import os
import shutil
import sys
import types

import pytest

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
pytest.importorskip('PyQt6.QtWidgets')

from PyQt6.QtWidgets import QApplication  # noqa: E402

ROOT_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
GAME = os.path.join(ROOT_DIR, 'pokefirered')

pytestmark = pytest.mark.skipif(
    not os.path.isfile(os.path.join(GAME, 'sound', 'voice_groups.inc'))
    or not os.path.isfile(os.path.join(GAME, 'include', 'constants', 'songs.h')),
    reason='needs the pokefirered sound folder and songs.h to copy')

DS_ENV = (255, 0, 255, 0)
SQ_ENV = (0, 0, 15, 0)


@pytest.fixture(scope='module')
def qapp():
    return QApplication.instance() or QApplication([])


@pytest.fixture(scope='module')
def editor(qapp, tmp_path_factory):
    """A real Sound Editor on a throwaway copy. The test that saves runs first;
    the type-switch test after it only edits in memory."""
    real_sd = sys.modules.get('sounddevice')
    sys.modules['sounddevice'] = types.SimpleNamespace(OutputStream=None)
    root = str(tmp_path_factory.mktemp('silent_samples') / 'game')
    shutil.copytree(os.path.join(GAME, 'sound'), os.path.join(root, 'sound'))
    os.makedirs(os.path.join(root, 'include', 'constants'))
    shutil.copy2(os.path.join(GAME, 'include', 'constants', 'songs.h'),
                 os.path.join(root, 'include', 'constants'))
    from ui.sound_editor_tab import SoundEditorTab
    se = SoundEditorTab()
    se.resize(1400, 900)
    se.show()
    se.load_project(root)
    qapp.processEvents()
    se.test_root = root
    yield se
    se.hide()
    se.deleteLater()
    qapp.processEvents()
    if real_sd is not None:
        sys.modules['sounddevice'] = real_sd
    else:
        sys.modules.pop('sounddevice', None)
    slug = hashlib.sha1(os.path.abspath(root).encode()).hexdigest()[:12]
    shutil.rmtree(os.path.join(ROOT_DIR, 'cache', slug), ignore_errors=True)


def _env(inst):
    return (inst.attack, inst.decay, inst.sustain, inst.release)


def _select(vgtab, vg_name, slot):
    from ui.voicegroups_tab import _ROLE_VG_NAME, _ROLE_SLOT_IDX
    tree = vgtab._vg_tree
    for i in range(tree.topLevelItemCount()):
        if tree.topLevelItem(i).data(0, _ROLE_VG_NAME) == vg_name:
            tree.setCurrentItem(tree.topLevelItem(i))
            break
    else:
        pytest.fail(f'{vg_name} is not listed')
    for i in range(vgtab._slot_tree.topLevelItemCount()):
        row = vgtab._slot_tree.topLevelItem(i)
        if row.data(0, _ROLE_SLOT_IDX) == slot:
            vgtab._slot_tree.setCurrentItem(row)
            return vgtab._current_inst
    pytest.fail(f'slot {slot} of {vg_name} is not listed')


def _set_type(vgtab, macro):
    idx = vgtab._type_combo.findData(macro)
    assert idx >= 0, macro
    vgtab._type_combo.setCurrentIndex(idx)


def _a_filler_slot(vgtab):
    """(voicegroup name, slot) of a filler square slot in a voicegroup that has
    no silent sampled slot already (so the warning starts hidden)."""
    from ui.voicegroups_tab import _is_filler_instrument
    from core.sound.sound_constants import directsound_envelope_is_silent
    for name in sorted(vgtab._voicegroup_data.voicegroups):
        vg = vgtab._voicegroup_data.get_voicegroup(name)
        if any(i.is_directsound and directsound_envelope_is_silent(i.attack)
               for i in vg.instruments):
            continue
        for inst in vg.instruments:
            if _is_filler_instrument(inst) and inst.is_square and _env(inst) == SQ_ENV:
                return name, inst.slot_index
    pytest.skip('no voicegroup with a filler square slot')


def test_make_them_audible_fixes_and_saves_only_those_slots(editor, qapp):
    vgtab = editor._voicegroups_tab
    path = os.path.join(editor.test_root, 'sound', 'voice_groups.inc')
    before = open(path, encoding='utf-8').read().splitlines()

    # Make two silent sampled slots the way the bug did.
    name, slot = _a_filler_slot(vgtab)
    vg = vgtab._voicegroup_data.get_voicegroup(name)
    fillers = [i.slot_index for i in vg.instruments
               if i.is_square and _env(i) == SQ_ENV and i.slot_index != slot][:1]
    silent_slots = [slot] + fillers
    for s in silent_slots:
        inst = _select(vgtab, name, s)
        _set_type(vgtab, 'voice_directsound')
        vgtab._adsr_sliders['attack'].setValue(0)
        assert inst.attack == 0
    assert (not vgtab._silent_row.isHidden()), 'no warning for a sampled slot with attack 0'
    assert vgtab._silent_label.text().startswith(f'{len(silent_slots)} sampled')

    current = vgtab._current_inst.slot_index
    vgtab._btn_fix_silent.click()
    qapp.processEvents()
    for s in silent_slots:
        assert _env(vg.get_instrument(s)) == DS_ENV
        assert s in vgtab._dirty_slots.get(name, set())
    assert not (not vgtab._silent_row.isHidden())
    assert vgtab._current_inst.slot_index == current, 'the repair moved the selection'
    assert vgtab.has_unsaved_changes()

    vgtab.save_to_disk()
    after = open(path, encoding='utf-8').read().splitlines()
    assert len(after) == len(before)
    changed = [(b, a) for b, a in zip(before, after) if b != a]
    assert len(changed) == len(silent_slots), changed[:5]
    for b, a in changed:
        assert b.strip().startswith('voice_square_1')
        assert a.strip().startswith('voice_directsound ')
        assert a.rstrip().endswith('255, 0, 255, 0')


def test_switching_type_gives_the_new_family_its_envelope(editor):
    vgtab = editor._voicegroups_tab
    name, slot = _a_filler_slot(vgtab)
    inst = _select(vgtab, name, slot)
    assert _env(inst) == SQ_ENV

    _set_type(vgtab, 'voice_directsound')
    assert inst.is_directsound and inst.sample_label
    assert _env(inst) == DS_ENV, 'a square slot turned sample kept the square envelope'
    assert not (not vgtab._silent_row.isHidden())
    assert vgtab._adsr_sliders['attack'].value() == 255

    # Within the sampled family the envelope is the user's: keep it.
    vgtab._adsr_sliders['release'].setValue(165)
    _set_type(vgtab, 'voice_directsound_no_resample')
    assert _env(inst) == (255, 0, 255, 165)

    _set_type(vgtab, 'voice_square_1')
    assert _env(inst) == SQ_ENV, 'a sample turned square kept the 0-255 envelope'
