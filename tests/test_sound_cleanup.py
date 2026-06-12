"""Regression tests for the Sound Editor 'Clean Up Files' voicegroup logic.

Covers the two defects behind the voice_groups.inc data-loss incident:
  A. _remove_voicegroup_block excised the target to EOF (no end-marker exists);
     it must surgically remove ONE block and no-op on an unknown name.
  B. usage detection read only built .s files; it must also read the committed
     midi.cfg (-G<NNN>), never flag a referenced voicegroup, and refuse to flag
     anything when no references resolve. delete_entries must refuse a still-
     referenced voicegroup.
"""

import os
import re
import sys
import tempfile
from pathlib import Path

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)
sys.path.insert(0, os.path.join(_ROOT, "core"))

from core.sound.sound_cleanup import (  # noqa: E402
    _remove_voicegroup_block, scan_orphaned_voicegroups,
    voicegroup_song_references, delete_entries, OrphanEntry,
)

_VG = (
    "\t.align 2\n"
    "voicegroup000::\n"
    "\tvoice_directsound 60, 0, DummyData, 0, 0, 0, 0\n"
    "\t.align 2\n"
    "voicegroup001::\n"
    "\tvoice_square_1 60, 0, 0, 0, 0, 0, 0\n"
    "\t.align 2\n"
    "voicegroup002::\n"
    "\tvoice_noise 60, 0, 0, 0, 0, 0\n"
)


def _mkproj(d, with_refs=True):
    sd = Path(d) / "sound"
    (sd / "songs" / "midi").mkdir(parents=True)
    (sd / "voice_groups.inc").write_text(_VG, encoding="utf-8")
    if with_refs:
        (sd / "songs" / "midi" / "midi.cfg").write_text(
            "mus_a.mid: -E -R50 -G001 -V090\n", encoding="utf-8")
        (sd / "songs" / "midi" / "mus_b.s").write_text(
            ".equ mus_b_grp , voicegroup002\n", encoding="utf-8")
    return sd / "voice_groups.inc"


def _labels(p):
    return re.findall(r'^(\w*voicegroup\w*)::', Path(p).read_text(encoding="utf-8"), re.M)


# ── defect A: surgical excision ──

def test_excise_middle_is_surgical():
    with tempfile.TemporaryDirectory() as d:
        vg = _mkproj(d)
        _remove_voicegroup_block(str(vg), "voicegroup001")
        assert _labels(vg) == ["voicegroup000", "voicegroup002"]
        assert "voicegroup001" not in Path(vg).read_text(encoding="utf-8")


def test_excise_last_block():
    with tempfile.TemporaryDirectory() as d:
        vg = _mkproj(d)
        _remove_voicegroup_block(str(vg), "voicegroup002")
        assert _labels(vg) == ["voicegroup000", "voicegroup001"]


def test_excise_unknown_name_is_noop():
    with tempfile.TemporaryDirectory() as d:
        vg = _mkproj(d)
        before = Path(vg).read_text(encoding="utf-8")
        _remove_voicegroup_block(str(vg), "voicegroup999")
        # Must NOT truncate — file byte-identical.
        assert Path(vg).read_text(encoding="utf-8") == before


# ── defect B: usage detection + safety ──

def test_scanner_reads_s_and_midicfg():
    with tempfile.TemporaryDirectory() as d:
        _mkproj(d)
        # 001 referenced via midi.cfg, 002 via .s -> only 000 is orphaned.
        assert [o.label for o in scan_orphaned_voicegroups(d)] == ["voicegroup000"]


def test_song_references_map():
    with tempfile.TemporaryDirectory() as d:
        _mkproj(d)
        refs = voicegroup_song_references(d)
        assert refs.get("voicegroup001") == {"mus_a"}     # midi.cfg -G001
        assert refs.get("voicegroup002") == {"mus_b"}     # .s .equ
        assert "voicegroup000" not in refs


def test_no_references_resolved_flags_nothing():
    with tempfile.TemporaryDirectory() as d:
        _mkproj(d, with_refs=False)
        (Path(d) / "sound" / "songs" / "midi" / "mus_x.mid").write_bytes(b"MThd")
        # Project has songs but nothing resolves -> refuse to flag anything.
        assert scan_orphaned_voicegroups(d) == []


def test_delete_refuses_referenced_voicegroup():
    with tempfile.TemporaryDirectory() as d:
        vg = _mkproj(d)
        errs = delete_entries(
            [OrphanEntry(category="voicegroup", label="voicegroup002",
                         file_path=vg, size_bytes=1)], d)
        assert errs and "voicegroup002" in errs[0]
        assert "voicegroup002" in _labels(vg)             # NOT removed
        # A truly-orphaned one IS removable.
        errs2 = delete_entries(
            [OrphanEntry(category="voicegroup", label="voicegroup000",
                         file_path=vg, size_bytes=1)], d)
        assert not errs2 and "voicegroup000" not in _labels(vg)


# ── scanners 1 & 2 (deep-audit fixes) ──

def test_broken_inc_skips_regenerable_bin():
    """A .bin missing on disk but with a .wav source is regenerable by make —
    not 'broken'. (Fresh clone: all .bin gitignored → must not flag/wipe them.)"""
    from core.sound.sound_cleanup import scan_broken_inc_entries
    with tempfile.TemporaryDirectory() as d:
        sd = Path(d) / "sound"
        (sd / "direct_sound_samples").mkdir(parents=True)
        (sd / "direct_sound_data.inc").write_text(
            '\t.align 2\nFoo::\n\t.incbin "sound/direct_sound_samples/foo.bin"\n\n'
            '\t.align 2\nBar::\n\t.incbin "sound/direct_sound_samples/bar.bin"\n\n',
            encoding="utf-8")
        (sd / "direct_sound_samples" / "foo.wav").write_bytes(b"RIFF")
        # foo.bin missing but rebuildable from foo.wav; bar.bin truly gone.
        assert [e.label for e in scan_broken_inc_entries(d)] == ["Bar"]


def test_orphan_bin_checks_all_inc_files():
    """A .bin referenced by another .inc (e.g. a cry table) is not orphaned."""
    from core.sound.sound_cleanup import scan_orphaned_bins
    with tempfile.TemporaryDirectory() as d:
        sd = Path(d) / "sound"
        (sd / "direct_sound_samples").mkdir(parents=True)
        (sd / "direct_sound_samples" / "used.bin").write_bytes(b"x")
        (sd / "direct_sound_samples" / "dead.bin").write_bytes(b"x")
        (sd / "direct_sound_data.inc").write_text("@ none\n", encoding="utf-8")
        (sd / "cry_tables.inc").write_text(
            '\t.incbin "sound/direct_sound_samples/used.bin"\n', encoding="utf-8")
        assert [e.label for e in scan_orphaned_bins(d, None)] == ["dead"]
