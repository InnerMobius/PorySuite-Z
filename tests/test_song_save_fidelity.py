"""Regression: saving a song must never change its music beyond the edit.

BUGS.md, 2026-11-15 — "Sound Editor tempo save rewrites the song's .mid through
a lossy converter and destroys music". A tempo save — even one that left the
tempo where it was — re-drew the song's .mid from PorySuite's reading of the .s
and rebuilt the .s from that. On a no-op save it changed 355 of the project's
380 tempo-editable songs: every repeated section went silent and everything
after it slid earlier (Kakariko 455 -> 283 notes, 98.8 s -> 72.0 s), echo /
vibrato / bend-range / tuning commands vanished, closing silence was cut and
volumes dropped by one.

These tests run on COPIES of the project's real songs in temp folders, with
the project's own mid2agb. Nothing in the project is ever written.

Locked down:
  * The exporter: mid2agb run on song_to_midi(parse(.s)) gives back the .s,
    for the songs that exposed each defect (repeats, effects, echo, MEMACC,
    time signatures, wait splits, trailing silence, repeated tempos).
  * The tempo save edits only the .mid's tempo bytes; a no-op writes nothing;
    later tempo changes keep their proportion; midi.cfg is untouched; an .s
    holding work its .mid lacks is refused, untouched.
  * The header save changes only header lines and that song's midi.cfg line.
  * A failure half way through a two-file write puts the first file back.
  * The parser fixes (XCMD, MEMACC, running status for VOICE / EOT / TIE, gate
    time on repeat lines) and the flattener's output cap.
"""
import os
import shutil
import sys
import tempfile
import unittest

ROOT_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
for _p in (ROOT_DIR, os.path.join(ROOT_DIR, "core")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import io  # noqa: E402

import mido  # noqa: E402

from core.sound.song_parser import parse_song_file  # noqa: E402
from core.sound.midi_exporter import song_to_midi  # noqa: E402
from core.sound.song_compiler import find_mid2agb  # noqa: E402
from core.sound import safe_song_save as SSS  # noqa: E402

GAME = os.path.join(ROOT_DIR, "pokefirered")
REAL_MIDI = os.path.join(GAME, "sound", "songs", "midi")
_HAVE_PROJECT = bool(find_mid2agb(GAME)) and os.path.isfile(os.path.join(REAL_MIDI, "midi.cfg"))

# Each one exposed a defect: repeats (kakariko, title, castle_town, faron_woods,
# lostwoods, berry_pick), effects + echo + repeated tempos (vs_gigalakitu,
# cycling, level_up), MEMACC (rs_vs_trainer), time signatures (se_note_a,
# se_card_flip, se_m_sing, silph), trailing silence (se_door).
SONGS = ["mus_kakariko", "mus_castle_town", "mus_title", "mus_faron_woods",
         "mus_lostwoods", "mus_vs_gigalakitu", "mus_cycling", "mus_berry_pick",
         "mus_level_up", "mus_rs_vs_trainer", "se_note_a", "se_card_flip",
         "se_m_sing", "mus_silph", "se_door"]


def _project_copy(d, labels):
    """A throwaway project: copies of the given songs, midi.cfg, mid2agb."""
    midi = os.path.join(d, "sound", "songs", "midi")
    tools = os.path.join(d, "tools", "mid2agb")
    os.makedirs(midi)
    os.makedirs(tools)
    shutil.copy2(find_mid2agb(GAME), tools)
    shutil.copy2(os.path.join(REAL_MIDI, "midi.cfg"), midi)
    for label in labels:
        for ext in (".s", ".mid"):
            shutil.copy2(os.path.join(REAL_MIDI, label + ext), midi)
    return midi


def _read(path):
    with open(path, "rb") as f:
        return f.read()


@unittest.skipUnless(_HAVE_PROJECT, "game project / mid2agb unavailable")
class ExporterRoundTripTest(unittest.TestCase):
    """mid2agb(song_to_midi(parse(.s))) == .s — the exporter loses nothing."""

    def test_named_songs_rebuild_exactly(self):
        with tempfile.TemporaryDirectory() as d:
            _project_copy(d, [])
            for label in SONGS:
                with self.subTest(song=label):
                    text = _read(os.path.join(REAL_MIDI, label + ".s")).decode("latin-1")
                    buf = io.BytesIO()
                    song_to_midi(parse_song_file(os.path.join(REAL_MIDI, label + ".s"))).save(file=buf)
                    out = SSS.render_s_with_mid2agb(d, label, buf.getvalue())
                    diff = SSS.compare_s(text, out, label)
                    self.assertTrue(diff.same_length and not diff.changed,
                                    f"{label}: {diff.describe()}")

    @unittest.skipUnless(os.environ.get("PORYSUITE_FULL_SONG_SWEEP"),
                         "set PORYSUITE_FULL_SONG_SWEEP=1 to check every song (~2 min)")
    def test_every_song_rebuilds_exactly(self):
        with tempfile.TemporaryDirectory() as d:
            _project_copy(d, [])
            for f in sorted(os.listdir(REAL_MIDI)):
                if not f.endswith(".s"):
                    continue
                label = f[:-2]
                with self.subTest(song=label):
                    text = _read(os.path.join(REAL_MIDI, f)).decode("latin-1")
                    buf = io.BytesIO()
                    song_to_midi(parse_song_file(os.path.join(REAL_MIDI, f))).save(file=buf)
                    diff = SSS.compare_s(text, SSS.render_s_with_mid2agb(d, label, buf.getvalue()), label)
                    self.assertTrue(diff.same_length and not diff.changed, diff.describe())

    def test_repeats_are_heard_at_their_time(self):
        # Kakariko: 455 notes over 98.8 s. The old exporter wrote 283 over 72 s.
        song = parse_song_file(os.path.join(REAL_MIDI, "mus_kakariko.s"))
        mid = song_to_midi(song)
        notes = sum(1 for t in mid.tracks for m in t if m.type == "note_on" and m.velocity > 0)
        self.assertEqual(notes, 455)
        real = mido.MidiFile(os.path.join(REAL_MIDI, "mus_kakariko.mid"))
        self.assertAlmostEqual(mid.length, real.length, delta=0.1)

    def test_effects_are_written(self):
        # mus_cycling: LFOS (CC21) and BENDR (CC20) per track; vs_gigalakitu: echo (CC30/29).
        cyc = song_to_midi(parse_song_file(os.path.join(REAL_MIDI, "mus_cycling.s")))
        ccs = {m.control for t in cyc.tracks for m in t if m.type == "control_change"}
        self.assertTrue({20, 21} <= ccs, ccs)
        gig = song_to_midi(parse_song_file(os.path.join(REAL_MIDI, "mus_vs_gigalakitu.s")))
        ccs = {m.control for t in gig.tracks for m in t if m.type == "control_change"}
        self.assertTrue({29, 30} <= ccs, ccs)


@unittest.skipUnless(_HAVE_PROJECT, "game project / mid2agb unavailable")
class TempoSaveTest(unittest.TestCase):

    def _first_bpm(self, mid_bytes):
        ev = SSS.find_tempo_events(mid_bytes)
        first = min(ev, key=lambda e: (e.abs_tick, e.track))
        return SSS.displayed_bpm(first.usec_per_beat)

    def test_noop_writes_nothing(self):
        with tempfile.TemporaryDirectory() as d:
            midi = _project_copy(d, ["mus_kakariko"])
            files = [os.path.join(midi, n) for n in ("mus_kakariko.s", "mus_kakariko.mid", "midi.cfg")]
            before = [(_read(f), os.stat(f).st_mtime_ns) for f in files]
            bpm = self._first_bpm(_read(files[1]))
            r = SSS.save_song_tempo(d, "mus_kakariko", bpm)
            self.assertFalse(r.changed)
            self.assertEqual(before, [(_read(f), os.stat(f).st_mtime_ns) for f in files])

    def test_tempo_change_keeps_every_note_and_effect(self):
        for label in ("mus_kakariko", "mus_vs_gigalakitu", "mus_cycling", "mus_castle_town"):
            with self.subTest(song=label), tempfile.TemporaryDirectory() as d:
                midi = _project_copy(d, [label])
                s_path, mid_path = (os.path.join(midi, label + e) for e in (".s", ".mid"))
                cfg = _read(os.path.join(midi, "midi.cfg"))
                old_mid, old_s = _read(mid_path), _read(s_path).decode("latin-1")
                bpm = self._first_bpm(old_mid)
                r = SSS.save_song_tempo(d, label, bpm + 10)
                self.assertTrue(r.changed)
                new_mid, new_s = _read(mid_path), _read(s_path).decode("latin-1")
                self.assertEqual(len(new_mid), len(old_mid))
                self.assertEqual(SSS.midi_differences_except_tempo(old_mid, new_mid), [])
                diff = SSS.compare_s(old_s, new_s, label)
                self.assertTrue(diff.only(lambda a, b: bool(SSS.TEMPO_LINE.match(a))),
                                diff.describe())
                self.assertIn(f"TEMPO , {bpm + 10}*", new_s)
                self.assertEqual(cfg, _read(os.path.join(midi, "midi.cfg")), "midi.cfg changed")
                self.assertGreater(os.stat(s_path).st_mtime, os.stat(mid_path).st_mtime)

    def test_later_tempos_keep_their_proportion(self):
        label = "mus_zeldas_lullaby"   # 22 tempo changes
        with tempfile.TemporaryDirectory() as d:
            midi = _project_copy(d, [label])
            mid_path = os.path.join(midi, label + ".mid")
            old = [e.bpm for e in SSS.find_tempo_events(_read(mid_path))]
            base = self._first_bpm(_read(mid_path))
            SSS.save_song_tempo(d, label, base * 2)
            new = [e.bpm for e in SSS.find_tempo_events(_read(mid_path))]
            self.assertEqual(len(old), len(new))
            for a, b in zip(old, new):
                self.assertAlmostEqual(b / a, 2.0, delta=0.01)

    def test_s_only_work_is_refused_and_untouched(self):
        label = "mus_kakariko"
        with tempfile.TemporaryDirectory() as d:
            midi = _project_copy(d, [label])
            s_path, mid_path = (os.path.join(midi, label + e) for e in (".s", ".mid"))
            text = _read(s_path).decode("latin-1").replace("VOICE , 74", "VOICE , 75", 1)
            self.assertIn("VOICE , 75", text)
            with open(s_path, "wb") as f:
                f.write(text.encode("latin-1"))
            before = (_read(s_path), _read(mid_path))
            with self.assertRaises(SSS.SongSaveRefused):
                SSS.save_song_tempo(d, label, self._first_bpm(before[1]) + 5)
            self.assertEqual(before, (_read(s_path), _read(mid_path)))

    def test_missing_mid2agb_is_refused_and_untouched(self):
        label = "mus_kakariko"
        with tempfile.TemporaryDirectory() as d:
            midi = _project_copy(d, [label])
            shutil.rmtree(os.path.join(d, "tools"))
            orig_which = shutil.which
            shutil.which = lambda *_a, **_k: None
            try:
                before = (_read(os.path.join(midi, label + ".s")), _read(os.path.join(midi, label + ".mid")))
                with self.assertRaises(SSS.SongSaveRefused):
                    SSS.save_song_tempo(d, label, self._first_bpm(before[1]) + 5)
                self.assertEqual(before, (_read(os.path.join(midi, label + ".s")),
                                          _read(os.path.join(midi, label + ".mid"))))
            finally:
                shutil.which = orig_which


@unittest.skipUnless(_HAVE_PROJECT, "game project / mid2agb unavailable")
class HeaderSaveTest(unittest.TestCase):

    def test_noop_writes_nothing(self):
        for label in ("mus_kakariko", "se_bag_pocket", "se_dex_search"):  # -V absent / "-v100"
            with self.subTest(song=label), tempfile.TemporaryDirectory() as d:
                midi = _project_copy(d, [label])
                files = [os.path.join(midi, n) for n in (label + ".s", label + ".mid", "midi.cfg")]
                before = [_read(f) for f in files]
                s = parse_song_file(files[0])
                from core.sound.song_table_manager import voicegroup_index_from_name
                self.assertFalse(SSS.save_song_header(
                    d, label, priority=s.priority, reverb=s.reverb, volume=s.master_volume,
                    voicegroup=voicegroup_index_from_name(s.voicegroup)))
                self.assertEqual(before, [_read(f) for f in files])

    def test_volume_change_touches_only_header_and_its_cfg_line(self):
        label = "mus_kakariko"
        with tempfile.TemporaryDirectory() as d:
            midi = _project_copy(d, [label])
            s_path, cfg_path = os.path.join(midi, label + ".s"), os.path.join(midi, "midi.cfg")
            old_s, old_cfg = _read(s_path).decode("latin-1"), _read(cfg_path).decode("utf-8")
            s = parse_song_file(s_path)
            self.assertTrue(SSS.save_song_header(d, label, priority=s.priority, reverb=s.reverb,
                                                 volume=s.master_volume - 10))
            diff = SSS.compare_s(old_s, _read(s_path).decode("latin-1"), label)
            self.assertTrue(diff.only(lambda a, b: bool(SSS.HEADER_EQU.match(a))), diff.describe())
            new_cfg = _read(cfg_path).decode("utf-8")
            others = lambda t: [l for l in t.splitlines() if not l.startswith(label + ".mid")]
            self.assertEqual(others(old_cfg), others(new_cfg))
            self.assertEqual(parse_song_file(s_path).master_volume, s.master_volume - 10)

    def test_lowercase_v_is_replaced_not_duplicated(self):
        from core.sound.song_table_manager import apply_header_flags
        self.assertEqual(sorted(apply_header_flags(["-E", "-v100", "-G127"], volume=90)),
                         sorted(["-E", "-V090", "-G127"]))


class CommitFilesTest(unittest.TestCase):

    def test_second_write_failing_puts_the_first_back(self):
        with tempfile.TemporaryDirectory() as d:
            a, b = os.path.join(d, "a.mid"), os.path.join(d, "a.s")
            for p, t in ((a, b"old mid"), (b, b"old s")):
                with open(p, "wb") as f:
                    f.write(t)
            real = os.replace
            calls = []

            def flaky(src, dst):
                calls.append(dst)
                if len(calls) == 2:
                    raise OSError("disk full")
                return real(src, dst)
            os.replace = flaky
            try:
                with self.assertRaises(OSError):
                    SSS.commit_files([(a, b"new mid"), (b, b"new s")])
            finally:
                os.replace = real
            self.assertEqual(_read(a), b"old mid")
            self.assertEqual(_read(b), b"old s")
            self.assertEqual(sorted(os.listdir(d)), ["a.mid", "a.s"], "temp files left behind")


class ParserFixesTest(unittest.TestCase):
    SONG = """\t.include "MPlayDef.s"
\t.equ t_grp, voicegroup001
\t.equ t_mvl, 127
\t.equ t_key, 0
\t.equ t_tbs, 1
\t.section .rodata
\t.global t
@**************** Track 1 (Midi-Chn.4) ****************@
t_1:
\t.byte KEYSH , t_key+0
@ 000   ----------------------------------------
\t.byte TEMPO , 129*t_tbs/2
\t.byte VOICE , 3
\t.byte 5
\t.byte XCMD , xIECV , 8
\t.byte xIECL , 9
\t.byte MEMACC, mem_set, 0x00, 117
\t.byte PAN , c_v+0
\t.byte N06 , Cn3 , v100
\t.byte Dn3 , v090 , gtp2
\t.byte W12
\t.byte TIE , En3 , v100
\t.byte Fn3
\t.byte W24
\t.byte EOT , En3
\t.byte Fn3
\t.byte W06
\t.byte FINE
t:
\t.byte 1
\t.byte 0
\t.byte 0
\t.byte 0
\t.word t_grp
\t.word t_1
\t.end
"""

    def _song(self, d):
        p = os.path.join(d, "t.s")
        with open(p, "w", encoding="utf-8", newline="\n") as f:
            f.write(self.SONG)
        return parse_song_file(p)

    def test_commands(self):
        with tempfile.TemporaryDirectory() as d:
            cmds = self._song(d).tracks[0].commands
            kinds = [(c.cmd, c.value, getattr(c, "sub", None)) for c in cmds
                     if c.cmd not in ("WAIT", "LABEL")]
            self.assertIn(("VOICE", 5, None), kinds, "a bare value after VOICE is another VOICE")
            self.assertIn(("XCMD", 8, "xIECV"), kinds)
            self.assertIn(("XCMD", 9, "xIECL"), kinds, "the XCMD continuation line was dropped")
            mem = [c for c in cmds if c.cmd == "MEMACC"]
            self.assertEqual([(m.sub, m.arg1, m.value) for m in mem], [("mem_set", 0, 117)])
            self.assertEqual([c for c in cmds if c.cmd == "PAN"].__len__(), 1,
                             "MEMACC operands were read as phantom PAN events")
            notes = [c for c in cmds if c.cmd == "NOTE"]
            self.assertEqual([(n.pitch, n.gate_time) for n in notes], [(60, None), (62, 2)])
            self.assertEqual([c.cmd for c in cmds if c.cmd in ("TIE", "EOT")],
                             ["TIE", "TIE", "EOT", "EOT"],
                             "a bare pitch after TIE / EOT repeats that command, not a note")
            self.assertEqual(self._song(d).tracks[0].bar_marks[0][0], 0)

    def test_odd_tempo_and_channel_survive_export(self):
        with tempfile.TemporaryDirectory() as d:
            mid = song_to_midi(self._song(d))
            tempos = [round(60_000_000 / m.tempo) for t in mid.tracks for m in t if m.type == "set_tempo"]
            self.assertEqual(tempos, [129])
            chans = {m.channel for m in mid.tracks[1] if hasattr(m, "channel")}
            self.assertEqual(chans, {3}, "Midi-Chn.4 is MIDI channel 3 (1-based comment)")


class FlattenCapTest(unittest.TestCase):
    """track_renderer.flatten_track_commands capped its OUTPUT at 4x the written
    commands, cutting the end off pattern-heavy tracks (9 tracks in 8 of this
    project's songs) — in the Piano Roll and in its saves."""

    def test_pattern_heavy_track_is_not_cut_off(self):
        from core.sound.track_renderer import flatten_track_commands
        lines = ["\t.include \"MPlayDef.s\"", "\t.equ p_grp, voicegroup001", "\t.equ p_mvl, 127",
                 "\t.section .rodata", "\t.global p", "p_1:", "p_1_000:"]
        lines += ["\t.byte N01 , Cn3 , v100", "\t.byte W01"] * 8 + ["\t.byte PEND"]
        lines += ["\t.byte PATT", "\t .word p_1_000"] * 40 + ["\t.byte FINE", "p:", "\t.byte 1",
                  "\t.byte 0", "\t.byte 0", "\t.byte 0", "\t.word p_grp", "\t.word p_1", "\t.end"]
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "p.s")
            with open(path, "w", encoding="utf-8", newline="\n") as f:
                f.write("\n".join(lines) + "\n")
            track = parse_song_file(path).tracks[0]
            flat = flatten_track_commands(track.commands, loop_count=0)
            self.assertEqual(sum(1 for c in flat if c.cmd == "NOTE"), 8 * 41)

    def test_loop_end_is_not_cut_short(self):
        # get_flattened_loop_info had the same kind of cap (8x the commands)
        # and reported a loop end in the middle of a pattern-heavy track.
        from core.sound.track_renderer import get_flattened_loop_info
        lines = ["\t.include \"MPlayDef.s\"", "\t.equ p_grp, voicegroup001", "\t.equ p_mvl, 127",
                 "\t.section .rodata", "\t.global p", "p_1:", "p_1_B1:", "p_1_000:"]
        lines += ["\t.byte N01 , Cn3 , v100", "\t.byte W01"] * 8 + ["\t.byte PEND"]
        lines += ["\t.byte PATT", "\t .word p_1_000"] * 40
        lines += ["\t.byte GOTO", "\t .word p_1_B1", "\t.byte FINE", "p:", "\t.byte 1", "\t.byte 0",
                  "\t.byte 0", "\t.byte 0", "\t.word p_grp", "\t.word p_1", "\t.end"]
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "p.s")
            with open(path, "w", encoding="utf-8", newline="\n") as f:
                f.write("\n".join(lines) + "\n")
            start, end, _ = get_flattened_loop_info(parse_song_file(path).tracks[0].commands)
            self.assertEqual((start, end), (0, 8 * 41))


class SourceGuardTest(unittest.TestCase):
    """The editors must never again re-draw a song's .mid from the parsed .s
    and rebuild the .s over the project without a check. Read as source, not
    imported: importing ui.sound_editor_tab opens the app's log for writing."""

    def _src(self, rel):
        with open(os.path.join(ROOT_DIR, rel), encoding="utf-8") as f:
            return f.read()

    def test_sound_editor_saves_through_safe_song_save(self):
        src = self._src("ui/sound_editor_tab.py")
        for gone in ("write_midi_file", "save_song_file", "recompile_song", "update_midi_cfg_flags"):
            self.assertNotIn(gone, src, f"sound_editor_tab must not use {gone}")
        body = src[src.index("def _save_song_inline"):src.index("def _resync_song_from_disk")]
        self.assertIn("save_song_tempo", body)
        self.assertIn("save_song_header", body)

    def test_piano_roll_saves_through_safe_song_save(self):
        src = self._src("ui/piano_roll_window.py")
        for gone in ("notes_to_track_commands", "save_song_file", "recompile_song",
                     "_original_track_commands", "_sync_notes_to_song"):
            self.assertNotIn(gone, src, f"piano_roll_window must not use {gone}")
        self.assertIn("save_editor_song", src[src.index("def save_to_disk"):])


if __name__ == "__main__":
    unittest.main()
