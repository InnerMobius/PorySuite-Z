"""Regression: saving a MIDI-sourced song must not corrupt its volume or
note encoding.

Two bugs this locks down, both of which broke a decaying SFX (SE_CONFIRM) when
its volume was adjusted in the editor:

1. **Double-scale.** The parser stores the EFFECTIVE VOL it read from the .s
   expression ``<mult>*mvl/mxv`` (already master-scaled). ``write_midi_file``
   used to write that effective value straight back as MIDI cc7, so mid2agb
   re-applied the master on rebuild and the track got quieter every save.
   The fix un-scales cc7 back to the raw multiplier (eff*127/mvl).

2. **Chord mis-encoding.** PorySuite's own .s writer dropped the explicit
   note-length on a chord tone that mid2agb keeps (writing a bare ``Fs5`` where
   mid2agb writes ``N01 , Fs5``). It assembled fine but played broken. The fix
   regenerates the .s with mid2agb itself (``recompile_song``), the reference
   encoder the build uses — so editor saves match the build byte-for-byte.
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

import mido  # noqa: E402
from core.sound.song_parser import parse_song_file  # noqa: E402
from core.sound.midi_exporter import write_midi_file  # noqa: E402
from core.sound.song_compiler import find_mid2agb, recompile_song  # noqa: E402

# A decaying SFX: VOL 96 -> 48 (raw multipliers) under master mvl=80, plus a
# two-note chord (Gn4 + Cn5 at the same tick, the second tone bare = running).
_SONG = """\t.include "MPlayDef.s"
\t.equ t_grp, voicegroup001
\t.equ t_mvl, 80
\t.section .rodata
\t.global t
\t.align 2
t_1:
\t.byte VOICE , 0
\t.byte VOL , 96*t_mvl/mxv
\t.byte N03 , Gn4 , v127
\t.byte Cn5
\t.byte W08
\t.byte VOL , 48*t_mvl/mxv
\t.byte N03 , Dn4 , v127
\t.byte W08
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


def _make_song(d):
    p = os.path.join(d, "t.s")
    with open(p, "w", encoding="utf-8", newline="\n") as f:
        f.write(_SONG)
    song = parse_song_file(p)
    song.file_path = p
    return song


def _cc7(mid_path):
    return [m.value for tr in mido.MidiFile(mid_path).tracks
            for m in tr if m.type == "control_change" and m.control == 7]


class VolumeUnscaleTest(unittest.TestCase):
    """write_midi_file must write the RAW multiplier, not the master-scaled
    effective level — otherwise mid2agb double-applies the master."""

    def test_cc7_is_raw_multiplier_not_effective(self):
        with tempfile.TemporaryDirectory() as d:
            song = _make_song(d)
            # parser stores effective: 96*80/127=60, 48*80/127=30
            eff = [c.value for c in song.tracks[0].commands if c.cmd == "VOL"]
            self.assertEqual(eff, [60, 30])
            m = os.path.join(d, "t.mid")
            write_midi_file(song, m)
            cc7 = _cc7(m)
            # Must be the raw multipliers (~96, ~48), NOT the effective (60, 30).
            self.assertEqual(len(cc7), 2)
            self.assertGreaterEqual(cc7[0], 94, f"VOL double-scaled: {cc7}")
            self.assertGreaterEqual(cc7[1], 46, f"VOL double-scaled: {cc7}")

    def test_full_master_is_identity(self):
        # With mvl=127, effective == raw multiplier, so no un-scale happens.
        with tempfile.TemporaryDirectory() as d:
            song = _make_song(d)
            song.master_volume = 127
            for c in song.tracks[0].commands:
                if c.cmd == "VOL":
                    c.value = 100  # an effective level at full master
            m = os.path.join(d, "t.mid")
            write_midi_file(song, m)
            self.assertEqual(_cc7(m)[0], 100)

    def test_effective_roundtrips(self):
        # eff -> raw -> (mid2agb would re-scale) -> eff must be stable.
        with tempfile.TemporaryDirectory() as d:
            song = _make_song(d)
            m = os.path.join(d, "t.mid")
            write_midi_file(song, m)
            cc7 = _cc7(m)
            mvl = song.master_volume
            back = [round(c * mvl / 127) for c in cc7]
            self.assertEqual(back, [60, 30])  # matches the parsed effective


@unittest.skipUnless(find_mid2agb(os.path.join(ROOT_DIR, "pokefirered")),
                     "project mid2agb unavailable")
class Mid2agbRecompileTest(unittest.TestCase):
    """recompile_song must regenerate the .s via mid2agb with the correct
    chord encoding (explicit length on the chord tone), in a throwaway project
    tree so the live project is never touched."""

    def _temp_project(self, d, mvl, voice=None):
        midi = os.path.join(d, "sound", "songs", "midi")
        tools = os.path.join(d, "tools", "mid2agb")
        os.makedirs(midi)
        os.makedirs(tools)
        real = find_mid2agb(os.path.join(ROOT_DIR, "pokefirered"))
        shutil.copy2(real, os.path.join(tools, os.path.basename(real)))
        # build a .mid from the synthetic song
        song = _make_song(d)
        if voice is not None:
            for t in song.tracks:
                for c in t.commands:
                    if c.cmd == "VOICE":
                        c.value = voice
        write_midi_file(song, os.path.join(midi, "t.mid"))
        with open(os.path.join(midi, "midi.cfg"), "w", encoding="utf-8") as f:
            f.write(f"t.mid: -E -G001 -V{mvl:03d}\n")
        return d

    def test_recompile_preserves_decay_and_chord(self):
        with tempfile.TemporaryDirectory() as d:
            self._temp_project(d, mvl=90)
            ok, err = recompile_song(d, "t")
            self.assertTrue(ok, f"recompile failed: {err}")
            with open(os.path.join(d, "sound", "songs", "midi", "t.s"),
                      encoding="utf-8") as _f:
                s = _f.read()
            import re
            mult = [int(x) for x in re.findall(r"VOL\s+,\s+(\d+)\*", s)]
            # decay shape preserved (strictly decreasing), two steps
            self.assertEqual(len(mult), 2)
            self.assertGreater(mult[0], mult[1], f"decay lost: {mult}")
            # mvl equate reflects the requested master
            self.assertIn("90", re.search(r"_mvl,\s*(\d+)", s).group(1))
            # the chord's second tone keeps an explicit length (mid2agb form),
            # never a bare running-status note that plays wrong
            cn5 = [ln for ln in s.splitlines() if "Cn5" in ln]
            self.assertTrue(cn5 and all("N" in ln for ln in cn5),
                            f"chord tone mis-encoded: {cn5}")

    def test_recompile_preserves_chosen_voice(self):
        # SE_SMALL_ITEM bug class: the editor's chosen instrument voice must
        # survive the .s -> .mid -> mid2agb -> .s round-trip and not revert to
        # a stale voice. (The bomb chime went silent because the .mid held an
        # old voice number the regenerated .s kept inheriting.)
        with tempfile.TemporaryDirectory() as d:
            self._temp_project(d, mvl=100, voice=127)
            ok, err = recompile_song(d, "t")
            self.assertTrue(ok, f"recompile failed: {err}")
            with open(os.path.join(d, "sound", "songs", "midi", "t.s"),
                      encoding="utf-8") as _f:
                s = _f.read()
            import re
            voices = re.findall(r"VOICE\s+,\s+(\d+)", s)
            self.assertIn("127", voices,
                          f"chosen voice 127 did not survive the round-trip: {voices}")


if __name__ == "__main__":
    unittest.main()
