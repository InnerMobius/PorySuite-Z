"""Who owns a song's ``.mid`` — PorySuite, or whoever edited it last.

pokefirered builds a song with one rule (``audio_rules.mk``)::

    sound/songs/midi/%.s: sound/songs/midi/%.mid sound/songs/midi/midi.cfg
            mid2agb -L<label> $(opts) $< $@

so the ``.s`` is regenerated whenever the ``.mid`` looks newer. PorySuite
writes some ``.s`` files directly — the piano roll, hand-authored songs — and
for those the ``.mid`` on disk is a 26-byte placeholder. Letting make run
mid2agb on a placeholder replaces a real composition with an empty header, so
PorySuite defends against it by forcing the timestamps: the ``.s`` to now, the
``.mid`` an hour into the past.

**That defence cannot tell a placeholder from a real composition, and it was
applied to both.** Editing a song in an external DAW writes a genuine ``.mid``
full of notes; PorySuite then stamped it into the past, make saw nothing to
rebuild, and the edit never reached the ROM. Measured on the owner's project
after a PoryDAW session: 377 of 378 songs had a ``.s`` newer than their
``.mid``, and the two songs they had just edited were among them — the ``.s``
on disk differed from a fresh mid2agb run by 1,147 lines.

This module is the missing distinction. A ``.mid`` that holds real music is
somebody's work and its timestamp is not PorySuite's to move. A placeholder is
PorySuite's own scaffolding and may be locked down as before.

Nothing here writes to the game tree except :func:`release_external_edits`,
which only ever moves an mtime and is the caller's explicit decision.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field

_log = logging.getLogger("SoundEditor.MidiOwnership")

#: A ``.mid`` at or below this size is scaffolding, not music. The canonical
#: placeholder PorySuite writes is 26 bytes; the headroom covers near-empty
#: SMF variations that mid2agb also treats as content-free. Kept in step with
#: ``song_integrity._PLACEHOLDER_MAX_BYTES`` — they describe the same thing.
PLACEHOLDER_MAX_BYTES = 30

MIDI_SUBDIR = os.path.join("sound", "songs", "midi")


def midi_dir(project_root: str) -> str:
    return os.path.join(project_root, MIDI_SUBDIR)


def is_placeholder_mid(mid_path: str) -> bool:
    """True when this ``.mid`` is PorySuite's own scaffolding, not music.

    Size first, because it is free and catches the canonical 26-byte stub.
    Then an actual parse, because a file can be larger than the threshold and
    still carry no notes — and a file that cannot be parsed at all is NOT
    called a placeholder, since destroying something unreadable is worse than
    leaving it alone.
    """
    try:
        if os.path.getsize(mid_path) <= PLACEHOLDER_MAX_BYTES:
            return True
    except OSError:
        # No file at all is not music, and callers below guard on existence
        # anyway; saying "placeholder" here keeps the old lock behaviour for
        # a song that has no .mid.
        return True

    try:
        import mido
        mid = mido.MidiFile(mid_path)
        for track in mid.tracks:
            for msg in track:
                if msg.type == 'note_on' and getattr(msg, 'velocity', 0) > 0:
                    return False
        return True
    except Exception as exc:
        # Unreadable, or mido missing. Treat it as real: the cost of being
        # wrong here is one song rebuilt that need not have been, against
        # overwriting a composition nobody can read back.
        _log.debug("Could not parse %s (%s) — treating it as real music",
                   mid_path, exc)
        return False


def may_lock_timestamp(mid_path: str) -> bool:
    """True when PorySuite may push this ``.mid``'s mtime into the past.

    The one question every stamping site should ask before it moves a
    timestamp. Only scaffolding may be locked; a real composition's mtime
    belongs to whoever wrote it.
    """
    return is_placeholder_mid(mid_path)


# ─────────────────────────────────────────────────────────────────────────────
# The ledger: what PorySuite itself last wrote
# ─────────────────────────────────────────────────────────────────────────────
#
# A timestamp cannot tell "PorySuite locked this .mid" from "the user edited
# this .mid in a DAW" — both just look old or new. Guessing from mtimes is how
# 377 songs ended up indistinguishable from the two the owner had actually
# edited.
#
# So PorySuite records the digest of every .mid at the moment it writes or
# locks one. A .mid whose digest no longer matches was changed by something
# else, which is the exact question, answered exactly. The ledger lives in
# PorySuite's own cache — never in the game tree, which belongs to the project
# and is somebody's git repository.

def _ledger_path(project_root: str) -> str:
    try:
        try:
            from app_info import get_cache_dir
        except ImportError:
            from core.app_info import get_cache_dir
        return os.path.join(get_cache_dir(project_root), "midi_ledger.json")
    except Exception:
        return ""


def _digest(path: str) -> str:
    """Git's own blob id for this file's bytes.

    Deliberately git's formula — ``sha1("blob <len>\\0" + content)`` — and not
    a plain hash, so a working-tree file can be compared against what
    ``git ls-tree`` reports WITHOUT fetching a single blob. That is what lets
    the committed state be read in one subprocess call instead of one per
    song, which is what made the app hang.
    """
    import hashlib
    try:
        with open(path, "rb") as fh:
            data = fh.read()
    except OSError:
        return ""
    header = b"blob " + str(len(data)).encode("ascii") + b"\0"
    return hashlib.sha1(header + data).hexdigest()


def _load_ledger(project_root: str) -> dict:
    import json
    p = _ledger_path(project_root)
    if not p or not os.path.isfile(p):
        return {}
    try:
        with open(p, encoding="utf-8") as fh:
            data = json.load(fh)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _save_ledger(project_root: str, ledger: dict) -> None:
    import json
    p = _ledger_path(project_root)
    if not p:
        return
    try:
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, "w", encoding="utf-8") as fh:
            json.dump(ledger, fh, indent=1, sort_keys=True)
    except OSError as exc:
        _log.debug("Could not write the MIDI ledger: %s", exc)


def record_own_write(project_root: str, label: str) -> None:
    """Note that PorySuite itself wrote or locked ``<label>.mid``.

    Called by every path that writes a ``.mid`` or moves its timestamp. What
    is recorded is the file's content, not its time, because content is what
    distinguishes PorySuite's own scaffolding from somebody's composition.
    """
    mid = os.path.join(midi_dir(project_root), label + ".mid")
    if not os.path.isfile(mid):
        return
    ledger = _load_ledger(project_root)
    ledger[label] = _digest(mid)
    _save_ledger(project_root, ledger)


def is_external_edit(project_root: str, label: str, *,
                     when_unknown: bool) -> bool:
    """True when ``<label>.mid`` differs from what PorySuite last recorded.

    *when_unknown* is required, and has no default on purpose. The two
    callers want opposite answers for a song with no record yet, and a
    default would have silently handed one of them the wrong one:

    * **Releasing a stuck song** passes ``True``. The user has named the song
      and asked for it to rebuild; PorySuite has no claim on a file it has no
      record of writing.
    * **Withdrawing build protection** passes ``False``. An edit that does not
      rebuild is visible and can be reported; regenerating over a
      hand-authored ``.s`` is silent and permanent.

    **Why content and not the music.** Comparing the notes — render the
    ``.s``, compare to the ``.mid`` — was tried and measured, and it does not
    work: the exporter re-times a few notes in pattern-compressed tracks, so
    it reported 4 of 5 songs on a real project as foreign, vanilla ones
    included. A test polluted by our own export error would withdraw
    protection from nearly everything. A digest does not care whether the
    exporter is perfect.
    """
    mid = os.path.join(midi_dir(project_root), label + ".mid")
    if not os.path.isfile(mid):
        return False
    if is_placeholder_mid(mid):
        return False            # scaffolding of ours, by definition
    known = _load_ledger(project_root).get(label)
    if not known:
        return when_unknown
    return known != _digest(mid)


def _no_window() -> dict:
    """Keyword args that stop a subprocess flashing a console on Windows.

    Without this every git call opens and closes a cmd window. One call is a
    flicker; a call per song made the machine unusable until the owner killed
    Python from Task Manager. Any subprocess this app runs needs these.
    """
    import subprocess
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    return {"creationflags": flags} if flags else {}


def _committed_digests(project_root: str) -> dict[str, str]:
    """``{label: digest}`` for each ``.mid`` as git last committed it.

    **One** git invocation, by design. The first version of this ran
    ``git show HEAD:<file>`` per song — 378 subprocesses on the owner's
    project, each popping a console window, which froze the machine until
    they killed Python from Task Manager. ``ls-tree`` already reports the
    blob id of every file in one go, so the contents never need fetching.

    That is why :func:`_digest` computes git's own blob id rather than a
    plain hash of the bytes: it makes the committed id and the working-tree
    id directly comparable without reading a single blob.

    Read-only, and entirely optional — a project that is not a git checkout
    gets an empty mapping and the caller falls back to the working tree.
    """
    import subprocess
    rel = MIDI_SUBDIR.replace("\\", "/")
    try:
        listing = subprocess.run(
            ["git", "-C", project_root, "ls-tree", "-r", "HEAD", rel],
            capture_output=True, text=True, timeout=30, **_no_window())
    except Exception:
        return {}
    if listing.returncode != 0:
        return {}

    out: dict[str, str] = {}
    for line in (listing.stdout or "").splitlines():
        # "<mode> blob <sha>	<path>"
        try:
            meta, path = line.split("	", 1)
            parts = meta.split()
            if len(parts) < 3 or parts[1] != "blob":
                continue
            if not path.strip().endswith(".mid"):
                continue
            out[os.path.basename(path.strip())[:-4]] = parts[2]
        except ValueError:
            continue
    return out


def seed_ledger(project_root: str) -> int:
    """Record every ``.mid``'s content as it stands right now.

    Called when a project is opened. Anything that differs from this
    afterwards was changed by something other than PorySuite, which is the
    whole question the pre-build guard needs answered.

    Only files with no entry are recorded, so an existing record — which may
    be the thing a change is measured against — is never overwritten.
    Returns how many were newly recorded.
    """
    d = midi_dir(project_root)
    if not os.path.isdir(d):
        return 0
    ledger = _load_ledger(project_root)
    committed = _committed_digests(project_root)
    added = 0
    for fn in sorted(os.listdir(d)):
        if not fn.endswith(".mid"):
            continue
        label = fn[:-4]
        if label in ledger:
            continue
        # Prefer the COMMITTED digest over the one on disk.
        #
        # "What PorySuite last saw" is best approximated by what the project
        # last agreed on, and a working-tree change is exactly the outside
        # edit this is here to notice. Seeding from disk instead would record
        # a pending edit as the baseline and declare it ours — so the very
        # first build after this ships would stamp over the edit that
        # prompted it. Falls back to disk for a file git does not know.
        ledger[label] = (committed.get(label)
                         or _digest(os.path.join(d, fn)))
        added += 1
    if added:
        _save_ledger(project_root, ledger)
        _log.info("Recorded %d .mid file(s) as they stand now", added)
    return added


@dataclass
class StuckSong:
    """A song whose real ``.mid`` make will not rebuild from."""

    label: str
    mid_path: str
    s_path: str
    mid_mtime: float
    s_mtime: float

    @property
    def behind_by(self) -> float:
        """Seconds the ``.mid`` appears to be behind its ``.s``."""
        return self.s_mtime - self.mid_mtime


@dataclass
class ReleaseReport:
    released: list[str] = field(default_factory=list)
    skipped_placeholder: list[str] = field(default_factory=list)
    errors: list[tuple[str, str]] = field(default_factory=list)

    def summary(self) -> str:
        if not self.released:
            return "no songs needed releasing"
        return (f"{len(self.released)} song(s) will rebuild from their MIDI")


def find_stuck_songs(project_root: str) -> list[StuckSong]:
    """Songs with a REAL ``.mid`` that make will nonetheless skip.

    A song is stuck when its ``.mid`` holds music and its ``.s`` is at least
    as new, because the build rule then has nothing to do and the edit in the
    ``.mid`` can never reach the ROM. Read-only.
    """
    d = midi_dir(project_root)
    if not os.path.isdir(d):
        return []

    out: list[StuckSong] = []
    for fn in sorted(os.listdir(d)):
        if not fn.endswith(".mid"):
            continue
        label = fn[:-4]
        mid_path = os.path.join(d, fn)
        s_path = os.path.join(d, label + ".s")
        if not os.path.isfile(s_path):
            continue          # nothing generated yet; make will build it
        if is_placeholder_mid(mid_path):
            continue          # scaffolding, correctly suppressed
        try:
            mid_mt = os.stat(mid_path).st_mtime
            s_mt = os.stat(s_path).st_mtime
        except OSError:
            continue
        if s_mt >= mid_mt:
            out.append(StuckSong(label, mid_path, s_path, mid_mt, s_mt))
    return out


def release_external_edits(project_root: str,
                           labels: list[str] | None = None) -> ReleaseReport:
    """Let make rebuild the songs whose ``.mid`` holds an outside edit.

    Moves the ``.s`` mtime to two seconds BEFORE its ``.mid``, which is the
    smallest change that makes the build rule fire. Deliberately not a delete:
    the ``.s`` stays on disk and readable, so a conversion that goes wrong
    leaves the previous output to compare against rather than a hole.

    *labels* restricts it to specific songs; the default releases every stuck
    one. Returns what it did — callers report it rather than assuming.
    """
    report = ReleaseReport()
    wanted = set(labels) if labels is not None else None

    for song in find_stuck_songs(project_root):
        if wanted is not None and song.label not in wanted:
            continue
        target = song.mid_mtime - 2
        try:
            os.utime(song.s_path, (target, target))
            report.released.append(song.label)
            _log.info("Released %s: its .s now predates its .mid, so the "
                      "build will regenerate it", song.label)
        except OSError as exc:
            report.errors.append((song.label, str(exc)))
            _log.warning("Could not release %s: %s", song.label, exc)

    if wanted:
        for label in sorted(wanted - set(report.released)
                            - {lbl for lbl, _ in report.errors}):
            report.skipped_placeholder.append(label)

    _log.info("Release pass: %s", report.summary())
    return report
