"""A song edited outside PorySuite has to survive to the ROM.

pokefirered builds every song with one rule (``audio_rules.mk``)::

    sound/songs/midi/%.s: sound/songs/midi/%.mid sound/songs/midi/midi.cfg

so the ``.s`` is regenerated only when its ``.mid`` looks newer. PorySuite
writes some ``.s`` files directly, and for those the ``.mid`` on disk is a
26-byte placeholder; letting make run mid2agb on a placeholder replaces a real
composition with an empty header. PorySuite defended against that by forcing
the timestamps — ``.s`` to now, ``.mid`` an hour into the past.

**The defence could not tell a placeholder from a real composition.** Editing a
song in PoryDAW writes a genuine ``.mid`` full of notes; PorySuite stamped it
into the past anyway, make saw nothing to rebuild, and the edit never reached
the ROM. Measured on the owner's project after a PoryDAW session: 377 of 378
songs had a ``.s`` at least as new as their ``.mid``, including the two they
had just edited, and the ``.s`` for one of those differed from a fresh mid2agb
run by 1,147 lines.

The distinction this adds: a ``.mid`` holding real music is somebody's work and
its timestamp is not PorySuite's to move. A placeholder is PorySuite's own
scaffolding and may still be locked down.

**Why releasing is deliberately per-song.** 374 of the project's 378 ``.s``
files differ from a fresh conversion of their own ``.mid``, because PorySuite
authors those ``.s`` files directly and its encoding is not mid2agb's. So
"rebuild everything whose ``.mid`` looks newer" would rewrite nearly every song
in the project from MIDI. Nothing here does that. The ledger makes future
edits exact; past ones are released by name.

Projects here are built in temp directories; no real song is read or written.
"""
from __future__ import annotations

import os
import time

import pytest

from core.sound import midi_ownership as mo

# A 26-byte placeholder of the kind PorySuite writes.
PLACEHOLDER = (b'MThd' + (6).to_bytes(4, 'big') + (0).to_bytes(2, 'big')
               + (1).to_bytes(2, 'big') + (96).to_bytes(2, 'big')
               + b'MTrk' + (0).to_bytes(4, 'big'))

assert len(PLACEHOLDER) <= mo.PLACEHOLDER_MAX_BYTES


def _real_mid(path: str, note: int = 60):
    """A .mid with an actual note in it, the way a DAW would leave one."""
    mido = pytest.importorskip('mido')
    mid = mido.MidiFile()
    track = mido.MidiTrack()
    mid.tracks.append(track)
    track.append(mido.Message('note_on', note=note, velocity=100, time=0))
    track.append(mido.Message('note_off', note=note, velocity=0, time=480))
    mid.save(path)


@pytest.fixture
def project(tmp_path):
    d = tmp_path / 'sound' / 'songs' / 'midi'
    d.mkdir(parents=True)
    (d / 'midi.cfg').write_text('mus_a.mid: -G001\nmus_b.mid: -G001\n',
                                encoding='utf-8')
    return str(tmp_path)


def _song(project_root, label, *, real: bool, s_newer: bool):
    d = mo.midi_dir(project_root)
    mid = os.path.join(d, label + '.mid')
    s = os.path.join(d, label + '.s')
    if real:
        _real_mid(mid)
    else:
        with open(mid, 'wb') as fh:
            fh.write(PLACEHOLDER)
    with open(s, 'w', encoding='utf-8') as fh:
        # A real song: it plays a note (a silent .s is just an empty song).
        fh.write(f'\t.include "MPlayDef.s"\n@ {label}\n\t.byte\t\tN06   , Cn3 , v100\n')
    base = time.time() - 10_000
    os.utime(mid, (base, base))
    os.utime(s, (base + (60 if s_newer else -60),) * 2)
    return mid, s


# ─────────────────────────────────────────────────────────────────────────────
# Telling scaffolding from music
# ─────────────────────────────────────────────────────────────────────────────

def test_a_placeholder_is_recognised(project):
    mid, _ = _song(project, 'mus_a', real=False, s_newer=True)
    assert mo.is_placeholder_mid(mid)


def test_a_midi_with_notes_is_not_a_placeholder(project):
    mid, _ = _song(project, 'mus_a', real=True, s_newer=True)
    assert not mo.is_placeholder_mid(mid)


def test_a_missing_file_is_treated_as_a_placeholder(project):
    """Keeps the old lock behaviour for a song that has no .mid at all."""
    assert mo.is_placeholder_mid(os.path.join(mo.midi_dir(project), 'nope.mid'))


def test_an_unreadable_midi_is_treated_as_real(project):
    """The safe direction.

    Being wrong here costs one needless rebuild. Being wrong the other way
    overwrites a composition nobody can read back.
    """
    p = os.path.join(mo.midi_dir(project), 'mus_a.mid')
    with open(p, 'wb') as fh:
        fh.write(b'not a midi file at all, but comfortably over thirty bytes')
    assert not mo.is_placeholder_mid(p)


def test_only_a_placeholder_may_be_locked(project):
    ph, _ = _song(project, 'mus_a', real=False, s_newer=True)
    real, _ = _song(project, 'mus_b', real=True, s_newer=True)
    assert mo.may_lock_timestamp(ph)
    assert not mo.may_lock_timestamp(real)


# ─────────────────────────────────────────────────────────────────────────────
# Finding the songs the build will silently skip
# ─────────────────────────────────────────────────────────────────────────────

def test_a_real_midi_behind_its_s_is_stuck(project):
    _song(project, 'mus_a', real=True, s_newer=True)
    assert [s.label for s in mo.find_stuck_songs(project)] == ['mus_a']


def test_a_real_midi_ahead_of_its_s_is_not_stuck(project):
    """make will rebuild it on its own; nothing to report."""
    _song(project, 'mus_a', real=True, s_newer=False)
    assert mo.find_stuck_songs(project) == []


def test_a_placeholder_behind_its_s_is_not_stuck(project):
    """That one is suppressed on purpose and must stay suppressed."""
    _song(project, 'mus_a', real=False, s_newer=True)
    assert mo.find_stuck_songs(project) == []


def test_a_song_with_no_s_yet_is_not_stuck(project):
    _real_mid(os.path.join(mo.midi_dir(project), 'mus_a.mid'))
    assert mo.find_stuck_songs(project) == []


def test_it_reports_how_far_behind(project):
    _song(project, 'mus_a', real=True, s_newer=True)
    assert mo.find_stuck_songs(project)[0].behind_by == pytest.approx(60, abs=2)


# ─────────────────────────────────────────────────────────────────────────────
# Releasing — by name, never wholesale
# ─────────────────────────────────────────────────────────────────────────────

def test_releasing_makes_the_build_rule_fire(project):
    mid, s = _song(project, 'mus_a', real=True, s_newer=True)
    assert os.stat(s).st_mtime >= os.stat(mid).st_mtime      # stuck

    report = mo.release_external_edits(project, ['mus_a'])

    assert report.released == ['mus_a']
    assert os.stat(s).st_mtime < os.stat(mid).st_mtime, (
        'make would still skip this song')


def test_releasing_does_not_delete_the_old_s(project):
    """A conversion that goes wrong should leave something to compare with."""
    _, s = _song(project, 'mus_a', real=True, s_newer=True)
    before = open(s, encoding='utf-8').read()
    mo.release_external_edits(project, ['mus_a'])
    assert os.path.isfile(s)
    assert open(s, encoding='utf-8').read() == before


def test_releasing_one_song_leaves_the_others_alone(project):
    """374 of the owner's 378 .s files differ from a fresh conversion, so a
    wholesale release would rewrite nearly every song in the project."""
    _song(project, 'mus_a', real=True, s_newer=True)
    _, other_s = _song(project, 'mus_b', real=True, s_newer=True)
    before = os.stat(other_s).st_mtime

    mo.release_external_edits(project, ['mus_a'])

    assert os.stat(other_s).st_mtime == before


def test_releasing_never_touches_a_placeholder(project):
    _, s = _song(project, 'mus_a', real=False, s_newer=True)
    before = os.stat(s).st_mtime
    report = mo.release_external_edits(project, ['mus_a'])
    assert report.released == []
    assert os.stat(s).st_mtime == before


# ─────────────────────────────────────────────────────────────────────────────
# The ledger: PorySuite knowing what it wrote
# ─────────────────────────────────────────────────────────────────────────────

def test_an_unrecorded_song_is_whatever_the_caller_asks_for(project):
    """`when_unknown` is required, and the two callers want opposite answers.

    Releasing a song the user NAMED is generous: PorySuite has no claim on a
    file it has no record of writing. Withdrawing build protection is
    conservative: an edit that does not rebuild is visible and reportable,
    while regenerating over a hand-authored .s is silent and permanent.
    A default would have quietly handed one of them the wrong answer.
    """
    _song(project, 'mus_a', real=True, s_newer=True)
    assert mo.is_external_edit(project, 'mus_a', when_unknown=True) is True
    assert mo.is_external_edit(project, 'mus_a', when_unknown=False) is False


def test_recording_a_write_claims_it(project):
    _song(project, 'mus_a', real=True, s_newer=True)
    mo.record_own_write(project, 'mus_a')
    assert not mo.is_external_edit(project, 'mus_a', when_unknown=True)


def test_a_later_change_shows_up_as_external(project):
    """The whole point: content, not timestamps, decides."""
    _song(project, 'mus_a', real=True, s_newer=True)
    mo.record_own_write(project, 'mus_a')
    assert not mo.is_external_edit(project, 'mus_a', when_unknown=True)

    _real_mid(os.path.join(mo.midi_dir(project), 'mus_a.mid'), note=72)
    assert mo.is_external_edit(project, 'mus_a', when_unknown=True)


def test_a_placeholder_is_never_an_external_edit(project):
    _song(project, 'mus_a', real=False, s_newer=True)
    assert not mo.is_external_edit(project, 'mus_a', when_unknown=True)


def test_the_ledger_is_not_written_into_the_game_tree(project):
    """It belongs in PorySuite's cache — the project is somebody's git repo."""
    _song(project, 'mus_a', real=True, s_newer=True)
    mo.record_own_write(project, 'mus_a')
    strays = [os.path.join(dp, fn)
              for dp, _dn, fns in os.walk(project) for fn in fns
              if 'ledger' in fn.lower()]
    assert strays == []


# ─────────────────────────────────────────────────────────────────────────────
# The writer's timestamp lock — where the damage was actually done
# ─────────────────────────────────────────────────────────────────────────────

from core.sound.song_writer import lock_song_timestamps  # noqa: E402


def test_the_lock_still_backdates_a_placeholder(project):
    """The protection that made the lock worth having stays."""
    mid, s = _song(project, 'mus_a', real=False, s_newer=False)
    cfg = os.path.join(mo.midi_dir(project), 'midi.cfg')

    lock_song_timestamps(s, mid, cfg)

    assert os.stat(mid).st_mtime < os.stat(s).st_mtime, (
        'a placeholder could now regenerate over the authored .s')


def test_the_lock_leaves_a_real_midi_alone(project):
    """THE regression. This is the whole PoryDAW bug in one assertion."""
    mid, s = _song(project, 'mus_a', real=True, s_newer=False)
    cfg = os.path.join(mo.midi_dir(project), 'midi.cfg')
    before = os.stat(mid).st_mtime

    lock_song_timestamps(s, mid, cfg)

    assert os.stat(mid).st_mtime == before, (
        "PorySuite moved a DAW's .mid into the past; the edit can no longer "
        "reach the ROM")


def test_a_real_midi_newer_than_the_s_still_rebuilds_after_a_save(project):
    """What the owner actually needs: edit in a DAW, save in PorySuite, build.

    The .s is stamped to now by the lock, so the .mid has to be newer than
    that for make to fire — this asserts the end state, not the mechanism.
    """
    mid, s = _song(project, 'mus_a', real=True, s_newer=False)
    cfg = os.path.join(mo.midi_dir(project), 'midi.cfg')
    future = time.time() + 120          # the DAW wrote it after the .s
    os.utime(mid, (future, future))

    lock_song_timestamps(s, mid, cfg)

    assert os.stat(mid).st_mtime > os.stat(s).st_mtime, (
        'make would skip this song and the DAW edit would be lost')


def test_the_lock_still_backdates_midi_cfg(project):
    mid, s = _song(project, 'mus_a', real=False, s_newer=False)
    cfg = os.path.join(mo.midi_dir(project), 'midi.cfg')
    now = time.time() + 60
    os.utime(cfg, (now, now))

    lock_song_timestamps(s, mid, cfg)

    assert os.stat(cfg).st_mtime < os.stat(s).st_mtime


def test_locking_a_placeholder_records_it_as_ours(project):
    mid, s = _song(project, 'mus_a', real=False, s_newer=False)
    cfg = os.path.join(mo.midi_dir(project), 'midi.cfg')
    lock_song_timestamps(s, mid, cfg)
    assert not mo.is_external_edit(project, 'mus_a', when_unknown=True)


# ─────────────────────────────────────────────────────────────────────────────
# The half-fix, and why the tests above did not catch it
#
# The first version of this repair gated only the `.mid` half of the lock: it
# stopped pushing a real `.mid` into the past, and went on stamping the `.s`
# to NOW regardless. Either half alone makes the `.s` the newer file, which is
# the whole of what make tests — so the bug came straight back.
#
# `test_a_real_midi_newer_than_the_s_still_rebuilds_after_a_save` should have
# caught it and did not, because it put the `.mid` TWO MINUTES in the future
# before calling the lock. With that head start the `.s` could be stamped to
# `now` and the `.mid` was still ahead. Real editing has no such margin: the
# DAW writes the `.mid`, and PorySuite stamps the `.s` afterwards, which is
# later by definition.
#
# Measured on the owner's project: `.mid` 12:13:40, `.s` 12:14:32 — content
# byte-identical to what was already committed, so nothing had been written,
# only stamped.
# ─────────────────────────────────────────────────────────────────────────────

def test_a_save_after_a_daw_edit_does_not_overtake_it(project):
    """THE second regression, in its realistic form.

    No future-dating. The `.mid` is edited, and the lock runs AFTER — exactly
    the order that occurs when PorySuite saves a song in the seconds following
    a DAW save.
    """
    mid, s = _song(project, 'mus_a', real=True, s_newer=False)
    cfg = os.path.join(mo.midi_dir(project), 'midi.cfg')

    now = time.time()
    os.utime(mid, (now, now))           # the DAW saves
    os.utime(s, (now - 60, now - 60))   # the .s is older, as it should be

    lock_song_timestamps(s, mid, cfg)   # PorySuite saves, moments later

    assert os.stat(s).st_mtime <= os.stat(mid).st_mtime, (
        'the .s overtook the .mid, so make will skip the DAW edit')


def test_the_s_is_not_even_touched_for_an_externally_owned_song(project):
    """Stronger and simpler: do not move the timestamp at all.

    PorySuite has no business ordering these two files for a song whose
    source of truth is somebody else's `.mid`.
    """
    mid, s = _song(project, 'mus_a', real=True, s_newer=False)
    cfg = os.path.join(mo.midi_dir(project), 'midi.cfg')
    before = os.stat(s).st_mtime

    lock_song_timestamps(s, mid, cfg)

    assert os.stat(s).st_mtime == before


def test_a_placeholder_song_still_gets_its_s_stamped(project):
    """The control. Without it, "never stamp anything" passes the two tests
    above and quietly removes the protection the lock exists for."""
    mid, s = _song(project, 'mus_a', real=False, s_newer=False)
    cfg = os.path.join(mo.midi_dir(project), 'midi.cfg')
    before = os.stat(s).st_mtime

    lock_song_timestamps(s, mid, cfg)

    assert os.stat(s).st_mtime > before
    assert os.stat(mid).st_mtime < os.stat(s).st_mtime


# ─────────────────────────────────────────────────────────────────────────────
# Recording what the .mid files look like, so the NEXT edit is recognisable
#
# The pre-build guard touches a `.s` only when it is older than its `.mid` —
# which is exactly and only the songs with a pending edit. It was aimed at
# precisely the files it must not touch, which is why the problem survived
# three separate repairs downstream of it.
#
# Telling an outside edit from our own by comparing the MUSIC was tried and
# measured, and does not work: the exporter re-times a few notes in
# pattern-compressed tracks, so on a real project it called 4 of 5 songs
# foreign, vanilla ones the owner had never opened included. A digest does not
# care whether the exporter is perfect.
# ─────────────────────────────────────────────────────────────────────────────

def test_seeding_records_every_midi(project):
    _song(project, 'mus_a', real=True, s_newer=True)
    _song(project, 'mus_b', real=True, s_newer=True)
    assert mo.seed_ledger(project) == 2


def test_a_song_is_not_foreign_right_after_seeding(project):
    _song(project, 'mus_a', real=True, s_newer=True)
    mo.seed_ledger(project)
    assert mo.is_external_edit(project, 'mus_a', when_unknown=False) is False


def test_an_outside_edit_after_seeding_is_recognised(project):
    """THE signal the pre-build guard needs."""
    mid, _s = _song(project, 'mus_a', real=True, s_newer=True)
    mo.seed_ledger(project)
    _real_mid(mid, note=72)          # something else saves the file
    assert mo.is_external_edit(project, 'mus_a', when_unknown=False) is True


def test_an_untouched_song_stays_protected(project):
    """The control. Without it, "everything is foreign" passes the test above
    and withdraws protection from the whole project."""
    mid_a, _ = _song(project, 'mus_a', real=True, s_newer=True)
    _song(project, 'mus_b', real=True, s_newer=True)
    mo.seed_ledger(project)
    _real_mid(mid_a, note=72)
    assert mo.is_external_edit(project, 'mus_a', when_unknown=False) is True
    assert mo.is_external_edit(project, 'mus_b', when_unknown=False) is False


def test_seeding_never_overwrites_an_existing_record(project):
    """The record is the thing a change is measured against.

    Re-recording it on every build would erase the evidence of the very edit
    the guard is supposed to notice.
    """
    mid, _s = _song(project, 'mus_a', real=True, s_newer=True)
    mo.seed_ledger(project)
    _real_mid(mid, note=72)
    mo.seed_ledger(project)          # a later build seeds again
    assert mo.is_external_edit(project, 'mus_a', when_unknown=False) is True


def test_seeding_picks_up_a_song_added_later(project):
    _song(project, 'mus_a', real=True, s_newer=True)
    assert mo.seed_ledger(project) == 1
    _song(project, 'mus_b', real=True, s_newer=True)
    assert mo.seed_ledger(project) == 1


def test_a_placeholder_is_never_foreign_however_it_changes(project):
    """Scaffolding is ours by definition, so it keeps its protection."""
    mid, _s = _song(project, 'mus_a', real=False, s_newer=True)
    mo.seed_ledger(project)
    with open(mid, 'ab') as fh:
        fh.write(b'\x00')
    assert mo.is_external_edit(project, 'mus_a', when_unknown=False) is False


def test_the_keyword_is_required(project):
    """No default, because the two callers want opposite answers."""
    _song(project, 'mus_a', real=True, s_newer=True)
    with pytest.raises(TypeError):
        mo.is_external_edit(project, 'mus_a')


# ─────────────────────────────────────────────────────────────────────────────
# Reading the committed state must stay cheap and silent
#
# The first version of `_committed_digests` ran `git show HEAD:<file>` once
# per song. On a real project that is 378 subprocesses, each opening and
# closing a console window on Windows — the app stopped responding and the
# machine was unusable until Python was killed from Task Manager.
#
# `git ls-tree` already reports every file's blob id in one call, so the
# contents never need fetching. That is also why `_digest` computes git's own
# blob id rather than a plain hash: it makes the committed id and the
# working-tree id directly comparable without reading a single blob.
# ─────────────────────────────────────────────────────────────────────────────

def test_reading_the_committed_state_uses_one_subprocess(project, monkeypatch):
    """THE regression. 378 of these froze the owner's machine."""
    import subprocess
    calls = []
    real = subprocess.run

    def counting(cmd, *a, **k):
        calls.append(cmd)
        return real(cmd, *a, **k)

    monkeypatch.setattr(subprocess, 'run', counting)
    mo._committed_digests(project)
    assert len(calls) <= 1, f'{len(calls)} subprocesses for one lookup'


def test_every_git_call_hides_its_console(project, monkeypatch):
    """A flashing cmd window per song is what made this unusable."""
    import subprocess
    seen = []
    real = subprocess.run

    def recording(cmd, *a, **k):
        seen.append(k)
        return real(cmd, *a, **k)

    monkeypatch.setattr(subprocess, 'run', recording)
    mo._committed_digests(project)
    for kwargs in seen:
        assert 'creationflags' in kwargs, (
            'a git call without creationflags opens a console window')


def test_the_digest_is_gits_own_blob_id(tmp_path):
    """What makes the one-call lookup possible.

    A plain hash of the bytes could not be compared against what `ls-tree`
    reports, so every blob would have to be fetched — which is the thing that
    hung the app.
    """
    import hashlib
    p = tmp_path / 'x.bin'
    p.write_bytes(b'hello')
    expected = hashlib.sha1(b'blob 5\x00hello').hexdigest()
    assert mo._digest(str(p)) == expected


def test_a_missing_file_digests_to_nothing(tmp_path):
    assert mo._digest(str(tmp_path / 'nope.bin')) == ''


def test_a_project_outside_git_is_survivable(project):
    """No git, no committed state — fall back, do not raise."""
    assert mo._committed_digests(project) == {}
    assert mo.seed_ledger(project) >= 0
