"""Regression: an editor's edit changes ONLY the edited events in the song's .mid.

BUGS.md, 2026-11-15 — "A Piano Roll edit replaced the song's .mid with a fresh
export". mid2agb reproduced the music, but a re-export dropped what only the
.mid held: track names (340 of 383 songs), time-signature events (99),
controllers mid2agb ignores (CC127 in 63, bank select, RPN bend range, ...),
exact MIDI velocities, notes on tracks the build skips. core/sound/midi_patch
now applies the edit to the existing file.

Test data here is built independently: a hand-made "DAW-style" .mid with every
kind of event a DAW may leave (sysex, text, names, bank select, RPN, CC127,
note-offs written as velocity-0 note-ons, a note-less chunk, 96 ticks/beat),
plus copies of the project's real songs. Everything runs in temp folders with
the project's own mid2agb; nothing in the project is written.
"""
import io
import os
import shutil
import sys
import tempfile
import unittest
from collections import Counter

ROOT_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
for _p in (ROOT_DIR, os.path.join(ROOT_DIR, "core")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import mido  # noqa: E402

from core.sound.song_parser import parse_song_file  # noqa: E402
from core.sound.song_compiler import find_mid2agb  # noqa: E402
from core.sound.midi_exporter import SongEdit, TrackEdit  # noqa: E402
from core.sound import safe_song_save as SSS  # noqa: E402
from core.sound.midi_patch import agb_velocity  # noqa: E402

GAME = os.path.join(ROOT_DIR, "pokefirered")
REAL_MIDI = os.path.join(GAME, "sound", "songs", "midi")
_HAVE = bool(find_mid2agb(GAME)) and os.path.isfile(os.path.join(REAL_MIDI, "midi.cfg"))


def _census(path):
    """Every event as (chunk, absolute tick, content) — order-independent."""
    m = mido.MidiFile(path)
    c = Counter()
    for ti, t in enumerate(m.tracks):
        ab = 0
        for e in t:
            ab += e.time
            if e.type == "end_of_track":
                continue
            d = e.dict()
            d.pop("time", None)
            c[(ti, ab, tuple(sorted((k, repr(v)) for k, v in d.items())))] += 1
    return c, m


def _project(d, cfg_lines, songs=()):
    midi = os.path.join(d, "sound", "songs", "midi")
    os.makedirs(midi)
    os.makedirs(os.path.join(d, "tools", "mid2agb"))
    shutil.copy2(find_mid2agb(GAME), os.path.join(d, "tools", "mid2agb"))
    with open(os.path.join(midi, "midi.cfg"), "w", encoding="utf-8", newline="\n") as f:
        f.write("".join(cfg_lines))
    for label in songs:
        for ext in (".s", ".mid"):
            shutil.copy2(os.path.join(REAL_MIDI, label + ext), midi)
    return midi


def _daw_style_mid(path):
    """A .mid written the way a DAW might: names, sysex, text, RPN, CC127, ..."""
    mid = mido.MidiFile(type=1, ticks_per_beat=96)
    cond = mido.MidiTrack()
    cond += [mido.MetaMessage("track_name", name="Conductor", time=0),
             mido.MetaMessage("time_signature", numerator=4, denominator=4, time=0),
             mido.MetaMessage("set_tempo", tempo=500000, time=0),
             mido.MetaMessage("text", text="made in some DAW", time=0),
             mido.MetaMessage("marker", text="[", time=0),
             mido.MetaMessage("marker", text="]", time=96 * 8),
             mido.MetaMessage("end_of_track", time=0)]
    lead = mido.MidiTrack()
    lead += [mido.MetaMessage("track_name", name="Lead Ocarina", time=0),
             mido.Message("sysex", data=[0x7E, 0x7F, 0x09, 0x01], time=0),
             mido.Message("control_change", channel=0, control=0, value=1, time=0),
             mido.Message("control_change", channel=0, control=101, value=0, time=0),
             mido.Message("control_change", channel=0, control=100, value=0, time=0),
             mido.Message("control_change", channel=0, control=6, value=12, time=0),
             mido.Message("control_change", channel=0, control=127, value=0, time=0),
             mido.Message("program_change", channel=0, program=5, time=0),
             mido.Message("control_change", channel=0, control=7, value=100, time=0)]
    # Eight notes, ends written as note_on velocity 0, velocities not multiples of 4.
    for i, (pitch, vel) in enumerate([(60, 93), (62, 81), (64, 77), (65, 102),
                                      (67, 93), (69, 81), (71, 77), (72, 102)]):
        lead.append(mido.Message("note_on", channel=0, note=pitch, velocity=vel, time=0 if i == 0 else 48))
        lead.append(mido.Message("note_on", channel=0, note=pitch, velocity=0, time=48))
    lead.append(mido.MetaMessage("end_of_track", time=0))
    bass = mido.MidiTrack()
    bass += [mido.MetaMessage("track_name", name="Bass", time=0),
             mido.Message("program_change", channel=1, program=33, time=0)]
    for i in range(4):
        bass.append(mido.Message("note_on", channel=1, note=36, velocity=110, time=0 if i == 0 else 96))
        bass.append(mido.Message("note_off", channel=1, note=36, velocity=40, time=96))
    bass.append(mido.MetaMessage("end_of_track", time=0))
    notes_only_text = mido.MidiTrack()
    notes_only_text += [mido.MetaMessage("track_name", name="Notes to self", time=0),
                        mido.MetaMessage("text", text="no channel events here", time=0),
                        mido.MetaMessage("end_of_track", time=0)]
    mid.tracks += [cond, lead, bass, notes_only_text]
    mid.save(path)


@unittest.skipUnless(_HAVE, "game project / mid2agb unavailable")
class DawStyleFileTest(unittest.TestCase):

    def _setup(self, d):
        midi = _project(d, ["daw_song.mid:                 -E -G013 -V100\n"])
        mid_path = os.path.join(midi, "daw_song.mid")
        _daw_style_mid(mid_path)
        s_text = SSS.render_s_with_mid2agb(d, "daw_song", open(mid_path, "rb").read())
        s_path = os.path.join(midi, "daw_song.s")
        with open(s_path, "w", encoding="latin-1", newline="\n") as f:
            f.write(s_text)                      # what the build would have made
        return midi, mid_path, s_path

    def _canvas_notes(self, song, track):
        from core.sound.midi_exporter import _expand_track
        ex = _expand_track(song.tracks[track])
        return [{"tick": t, "pitch": c.pitch, "duration": c.duration,
                 "velocity": c.velocity, "track": track}
                for t, _s, c in ex.events[:ex.played_count] if c.cmd == "NOTE"]

    def test_note_edit_changes_only_that_note(self):
        with tempfile.TemporaryDirectory() as d:
            midi, mid_path, s_path = self._setup(d)
            before, m0 = _census(mid_path)
            song = parse_song_file(s_path)
            notes = self._canvas_notes(song, 0)
            notes[2] = dict(notes[2], pitch=notes[2]["pitch"] + 2)
            SSS.save_editor_song(d, song, SongEdit(tracks={0: TrackEdit(notes=notes)}))
            after, m1 = _census(mid_path)
            self.assertEqual(m1.ticks_per_beat, 96)
            self.assertEqual(len(m1.tracks), 4, "the note-less chunk must stay")
            gone, new = before - after, after - before
            self.assertEqual(sum(gone.values()), 2, gone)        # the old note's on + off
            self.assertEqual(sum(new.values()), 2, new)          # the new note's on + off
            for key in gone:
                self.assertIn(("note", "64"), key[2])
            names = [e.name for t in m1.tracks for e in t if e.type == "track_name"]
            self.assertEqual(names, ["Conductor", "Lead Ocarina", "Bass", "Notes to self"])
            # the .s now has the edit
            self.assertIn("Fs3", open(s_path, encoding="latin-1").read())

    def test_noop_save_writes_nothing(self):
        with tempfile.TemporaryDirectory() as d:
            midi, mid_path, s_path = self._setup(d)
            raw = (open(mid_path, "rb").read(), open(s_path, "rb").read())
            SSS.save_editor_song(d, parse_song_file(s_path), SongEdit())
            self.assertEqual(raw, (open(mid_path, "rb").read(), open(s_path, "rb").read()))

    def test_mid_changed_after_the_s_was_built_is_refused(self):
        # Another editor (PoryDAW writes only the .mid) changed a note; no build yet.
        with tempfile.TemporaryDirectory() as d:
            midi, mid_path, s_path = self._setup(d)
            m = mido.MidiFile(mid_path)
            for e in m.tracks[2]:
                if e.type == "note_on" and e.velocity == 110:
                    e.velocity = 60
                    break
            m.save(mid_path)
            raw = (open(mid_path, "rb").read(), open(s_path, "rb").read())
            song = parse_song_file(s_path)
            notes = self._canvas_notes(song, 0)
            notes[0] = dict(notes[0], pitch=notes[0]["pitch"] + 1)
            with self.assertRaises(SSS.SongSaveRefused) as cm:
                SSS.save_editor_song(d, song, SongEdit(tracks={0: TrackEdit(notes=notes)}))
            self.assertIn("was changed after", str(cm.exception))
            self.assertEqual(raw, (open(mid_path, "rb").read(), open(s_path, "rb").read()))

    def test_output_keeps_the_layout_other_editors_rely_on(self):
        # Format 1; same resolution; chunk 0 holds no channel events; every
        # chunk that has channel events uses one channel (mid2agb: one game
        # track per channel with notes; a chunk-per-track editor sees the same).
        with tempfile.TemporaryDirectory() as d:
            midi, mid_path, s_path = self._setup(d)
            song = parse_song_file(s_path)
            notes = self._canvas_notes(song, 1)
            notes.append(dict(notes[0], tick=notes[0]["tick"] + 24))
            SSS.save_editor_song(d, song, SongEdit(tracks={1: TrackEdit(notes=notes)}))
            m = mido.MidiFile(mid_path)
            self.assertEqual((m.type, m.ticks_per_beat), (1, 96))
            self.assertFalse(any(hasattr(e, "channel") for e in m.tracks[0]))
            for t in m.tracks[1:]:
                chans = {e.channel for e in t if hasattr(e, "channel")}
                self.assertLessEqual(len(chans), 1, chans)


@unittest.skipUnless(_HAVE, "game project / mid2agb unavailable")
class RealSongPatchTest(unittest.TestCase):

    def _edit_one_note(self, label, track=0):
        from core.sound.midi_exporter import _expand_track
        with tempfile.TemporaryDirectory() as d:
            midi = _project(d, [open(os.path.join(REAL_MIDI, "midi.cfg"), encoding="utf-8").read()], [label])
            mid_path, s_path = (os.path.join(midi, label + e) for e in (".mid", ".s"))
            before, m0 = _census(mid_path)
            song = parse_song_file(s_path)
            ex = _expand_track(song.tracks[track])
            notes = [{"tick": t, "pitch": c.pitch, "duration": c.duration,
                      "velocity": c.velocity if c.velocity else 100, "track": track}
                     for t, _s, c in ex.events[:ex.played_count] if c.cmd == "NOTE"]
            from core.sound.song_parser import extract_tie_notes, Track
            from core.sound.track_renderer import flatten_track_commands
            ft = Track(index=track, label=song.tracks[track].label)
            ft.commands = flatten_track_commands(song.tracks[track].commands, loop_count=0)
            notes += [dict(n, track=track) for n in extract_tie_notes(ft)]
            notes[0] = dict(notes[0], pitch=notes[0]["pitch"] + 1)
            SSS.save_editor_song(d, song, SongEdit(tracks={track: TrackEdit(notes=notes)}))
            after, m1 = _census(mid_path)
            return before, after, m0, m1

    def test_only_the_note_changes_in_real_songs(self):
        # Kakariko: repeats + track names; obtain_item: velocities that mid2agb
        # rounds (93 -> v096); lostwoods: notes after the loop's GOTO; cycling,
        # vs_gigalakitu: effects, echo, many tempos; castle_town: RPN events.
        for label in ("mus_kakariko", "mus_obtain_item", "mus_lostwoods", "mus_cycling",
                      "mus_vs_gigalakitu", "mus_castle_town"):
            with self.subTest(song=label):
                before, after, m0, m1 = self._edit_one_note(label)
                self.assertEqual(m0.ticks_per_beat, m1.ticks_per_beat)
                self.assertEqual(sum((before - after).values()), 2, (before - after))
                self.assertEqual(sum((after - before).values()), 2, (after - before))

    def test_mid2agb_velocity_rounding(self):
        self.assertEqual([agb_velocity(v) for v in (0, 1, 4, 5, 93, 96, 125, 127)],
                         [0, 4, 4, 8, 96, 96, 127, 127])


@unittest.skipUnless(_HAVE, "game project / mid2agb unavailable")
class FallbackPolicyTest(unittest.TestCase):
    """When an edit cannot be placed in the .mid, a full export is allowed only
    for a .mid PorySuite itself wrote last; anything else is refused."""

    def _attempt(self, own):
        from core.sound import midi_patch
        from core.sound.midi_ownership import record_own_write
        label = "mus_kakariko"
        with tempfile.TemporaryDirectory() as d:
            midi = _project(d, [open(os.path.join(REAL_MIDI, "midi.cfg"), encoding="utf-8").read()], [label])
            if own:
                record_own_write(d, label)
            real = midi_patch.patch_midi

            def unavailable(*a, **k):
                raise midi_patch.MidiPatchUnavailable("forced for the test")
            midi_patch.patch_midi = unavailable
            try:
                s_path = os.path.join(midi, label + ".s")
                song = parse_song_file(s_path)
                v = next(c for c in song.tracks[0].commands if c.cmd == "VOICE")
                v.value = (v.value + 1) % 128
                before = open(os.path.join(midi, label + ".mid"), "rb").read()
                try:
                    SSS.save_editor_song(d, song, SongEdit())
                    return "saved", before != open(os.path.join(midi, label + ".mid"), "rb").read()
                except SSS.SongSaveRefused as e:
                    return "refused", str(e)
            finally:
                midi_patch.patch_midi = real
                import hashlib
                slug = hashlib.sha1(os.path.abspath(d).encode()).hexdigest()[:12]
                shutil.rmtree(os.path.join(ROOT_DIR, "cache", slug), ignore_errors=True)

    def test_external_mid_is_refused(self):
        outcome, detail = self._attempt(own=False)
        self.assertEqual(outcome, "refused")
        self.assertIn("not written by PorySuite", detail)

    def test_porysuites_own_mid_may_be_re_exported(self):
        outcome, changed = self._attempt(own=True)
        self.assertEqual(outcome, "saved")
        self.assertTrue(changed)


if __name__ == "__main__":
    unittest.main()
