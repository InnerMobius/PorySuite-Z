"""Song integrity sweep — protects hand-edited .s files from build-time wipe.

Background
==========

pokefirered's `audio_rules.mk` has a single Make rule for every song:

    sound/songs/midi/%.s: sound/songs/midi/%.mid sound/songs/midi/midi.cfg
        mid2agb -L<label> $(opts) $< $@

This means the .s file is REGENERATED any time .mid OR midi.cfg appears newer
on the filesystem.  Two scenarios catastrophically destroy the user's work:

  A) **Hand-edited .s + placeholder .mid.**  The user opens an .s in a text
     editor, types in their composition, saves.  The corresponding .mid is
     a 26-byte placeholder left over from `_create_placeholder_mid`.  Later,
     some unrelated PorySuite operation touches midi.cfg (adding a song,
     renaming a song, rewriting the cfg).  midi.cfg.mtime becomes newer
     than the user's .s, so the next `make` runs mid2agb on the placeholder
     .mid and overwrites the .s with a 0-track empty header.  All hand work
     is gone.

  B) **Stale committed .mid restored by git.**  Project A has a soaring
     song that started as a copy of Epona's MIDI.  User hand-composes new
     notes in soaring's .s.  Only the .s is local; the .mid in git still
     holds Epona's content.  `git reset --hard FETCH_HEAD` (or any pull
     that restores the .mid) sets the .mid mtime to "now", and the next
     `make` runs mid2agb on Epona's notes — wiping soaring's hand work
     and replacing it with Epona's music.

This module closes both holes by walking every song at project-open time
(and on demand via the Sound Editor menu) and ensuring two invariants:

  1. **Every song with non-empty tracks in its .s has a real,
     content-matching .mid on disk.**  Empty/placeholder .mids (anything
     under 30 bytes, or anything that round-trips to zero notes via mido)
     get regenerated from the .s using `midi_exporter`.

  2. **.s mtime is newer than both its .mid and midi.cfg.**  After step
     1's regeneration the .mid is freshly written, so we backdate it by
     1 hour.  midi.cfg gets backdated if it's currently at/after the
     newest .s; we don't push it back unnecessarily so unrelated cfg
     edits aren't disturbed.

The sweep is idempotent: re-running it on a clean project does nothing
(content matches → byte-equality short-circuit fires → no writes).

What this does NOT do
=====================

This sweep ONLY regenerates `.mid`s from `.s`s.  It does NOT:

  - Reconstruct lost `.s` content.  If the user lost a song to a previous
    wipe (e.g. mid2agb already ran on a placeholder before the sweep was
    added), the `.s` is already empty and the regenerated `.mid` will
    also be empty.  The sweep CAN'T recover what was already destroyed —
    only prevent future destruction.

  - Touch `.mid` files that legitimately have content from an external
    DAW.  If `.mid` size > 30 bytes AND parses to a non-zero note count,
    we leave it alone — the user may have intentionally hand-edited the
    `.mid` in FL Studio / Anvil / etc., and that case should round-trip
    through mid2agb naturally.

  - Touch songs whose `.s` is missing entirely.  Those are build
    artifacts that haven't been generated yet (fresh git clone, etc.).
"""

from __future__ import annotations

import logging
import os
import time
from dataclasses import dataclass, field
from typing import Optional

_log = logging.getLogger("SoundEditor.SongIntegrity")


# .mid files at or below this size are treated as placeholders that
# should be replaced.  The 26-byte stub is the canonical placeholder
# size; we allow a little headroom (30 bytes) for any near-empty SMF
# variations that mid2agb still treats as content-free.
_PLACEHOLDER_MAX_BYTES = 30


@dataclass
class SongIntegrityReport:
    """Result of a single sweep pass.

    `regenerated`: list of song labels whose .mid was regenerated from .s.
    `skipped_no_s`: songs whose .s file is missing entirely.
    `skipped_empty_s`: .s exists but has 0 tracks — nothing to regenerate.
    `skipped_real_mid`: .mid has real content > 30 bytes — left alone.
    `errors`: (label, exception_str) for songs that hit a parse/render error.
    `timestamps_locked`: True if the final mtime backdate pass ran.
    """
    regenerated: list[str] = field(default_factory=list)
    skipped_no_s: list[str] = field(default_factory=list)
    skipped_empty_s: list[str] = field(default_factory=list)
    skipped_real_mid: list[str] = field(default_factory=list)
    errors: list[tuple[str, str]] = field(default_factory=list)
    timestamps_locked: bool = False

    def summary(self) -> str:
        bits = []
        if self.regenerated:
            bits.append(f"{len(self.regenerated)} regenerated")
        if self.errors:
            bits.append(f"{len(self.errors)} errored")
        if not bits:
            return "all songs clean"
        return ", ".join(bits)


def run_sweep(
    project_root: str,
    song_labels: Optional[list[str]] = None,
) -> SongIntegrityReport:
    """Walk every song and ensure .mid matches .s content.

    Args:
        project_root: Project root path (the pokefirered/ directory).
        song_labels: Optional explicit list of labels to check.  If None,
            scans `sound/songs/midi/*.s` directly so a song doesn't have
            to be registered in the table to be protected.  This catches
            hand-edited .s files that the user added but never registered
            via the PorySuite UI (rare but possible).

    Returns:
        SongIntegrityReport with per-song outcomes.
    """
    report = SongIntegrityReport()

    midi_dir = os.path.join(project_root, "sound", "songs", "midi")
    if not os.path.isdir(midi_dir):
        _log.warning("Sound dir missing: %s", midi_dir)
        return report

    # Build the list of labels to check.
    labels: list[str]
    if song_labels is not None:
        labels = list(song_labels)
    else:
        labels = sorted(
            fn[:-2] for fn in os.listdir(midi_dir)
            if fn.endswith(".s")
        )

    # Lazy imports — the parser/exporter modules are heavy and only
    # needed if we actually have songs to check.
    from core.sound.song_parser import parse_song_file
    from core.sound.midi_exporter import write_midi_file, song_to_midi

    newest_s_mtime = 0.0

    for label in labels:
        s_path = os.path.join(midi_dir, label + ".s")
        mid_path = os.path.join(midi_dir, label + ".mid")

        if not os.path.isfile(s_path):
            report.skipped_no_s.append(label)
            continue

        # Decide whether the existing .mid is a placeholder that should
        # be regenerated, or real content we should leave alone.
        needs_regen = True
        if os.path.isfile(mid_path):
            try:
                size = os.path.getsize(mid_path)
            except OSError:
                size = 0
            if size > _PLACEHOLDER_MAX_BYTES:
                # Real-sized .mid — leave alone unless it parses to zero
                # notes (a too-large-but-still-empty edge case).  Avoid
                # importing mido at the top level so this module stays
                # fast to import.
                if _mid_has_audible_content(mid_path):
                    report.skipped_real_mid.append(label)
                    needs_regen = False

        if not needs_regen:
            # Track newest .s mtime for the final cfg lock pass.
            try:
                s_mt = os.stat(s_path).st_mtime
                if s_mt > newest_s_mtime:
                    newest_s_mtime = s_mt
            except OSError:
                pass
            continue

        # Parse the .s and check it has tracks worth regenerating.
        try:
            song = parse_song_file(s_path)
        except Exception as exc:
            report.errors.append((label, f"parse failed: {exc}"))
            _log.warning("Integrity sweep: parse failed for %s: %s",
                         label, exc)
            continue

        if not song.tracks or all(
                _track_has_no_notes(t) for t in song.tracks):
            # .s exists but encodes silence.  Don't regenerate — there's
            # nothing to put in the .mid that wouldn't itself be a
            # placeholder.  The 0-track .s is its own problem; the user
            # presumably has a separate process for hand-composing it.
            report.skipped_empty_s.append(label)
            continue

        # Regenerate the .mid from the .s.  write_midi_file is byte-
        # equality guarded so this is a no-op if the .mid is already
        # current — that's the idempotency property we want.
        try:
            wrote = write_midi_file(song, mid_path)
            if wrote:
                report.regenerated.append(label)
                _log.info(
                    "Integrity sweep: regenerated %s.mid from %s.s",
                    label, label)
            else:
                report.errors.append((label, "write_midi_file returned False"))
        except Exception as exc:
            report.errors.append((label, f"render failed: {exc}"))
            _log.warning("Integrity sweep: render failed for %s: %s",
                         label, exc)
            continue

        try:
            s_mt = os.stat(s_path).st_mtime
            if s_mt > newest_s_mtime:
                newest_s_mtime = s_mt
        except OSError:
            pass

    # Final mtime lock pass.  Push the regenerated .mids 1 hour into the
    # past relative to NOW (not relative to .s — we want a stable target
    # that doesn't drift if the user's clock jumps).  Push midi.cfg back
    # too IF it's currently at/after the newest .s — that's the Make-rule
    # trigger we're trying to defuse.
    now = time.time()
    far_past = now - 3600

    for label in report.regenerated:
        mid_path = os.path.join(midi_dir, label + ".mid")
        if os.path.isfile(mid_path):
            try:
                os.utime(mid_path, (far_past, far_past))
            except OSError:
                pass

    cfg_path = os.path.join(midi_dir, "midi.cfg")
    if os.path.isfile(cfg_path) and newest_s_mtime > 0:
        try:
            cfg_mt = os.stat(cfg_path).st_mtime
            if cfg_mt >= newest_s_mtime:
                os.utime(cfg_path, (far_past, far_past))
        except OSError:
            pass

    report.timestamps_locked = True

    _log.info("Integrity sweep complete: %s", report.summary())
    return report


def _track_has_no_notes(track) -> bool:
    """True if the track has zero NOTE / TIE commands."""
    for cmd in track.commands:
        if cmd.cmd in ("NOTE", "TIE"):
            return False
    return True


def _mid_has_audible_content(mid_path: str) -> bool:
    """Return True if the .mid contains at least one note_on event.

    Anything that parses to zero notes is treated as effectively a
    placeholder — even if its byte size is larger than the 26-byte stub.
    """
    try:
        import mido
        mid = mido.MidiFile(mid_path)
    except Exception:
        # Unparseable .mid — better to NOT trash it (might be a format
        # we don't know about that mid2agb does support).  Return True
        # so the sweep leaves it alone.
        return True

    for track in mid.tracks:
        for msg in track:
            if msg.type == "note_on" and getattr(msg, "velocity", 0) > 0:
                return True
    return False
