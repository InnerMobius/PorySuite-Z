"""Refresh from MIDI — bring a song's ``.s`` up to date with a ``.mid`` that
was changed outside PorySuite, without building the whole game.

The game builds a song with one rule: ``<song>.s`` is mid2agb's output for
``<song>.mid`` with the song's ``midi.cfg`` flags. Another editor (PoryDAW
writes only the ``.mid``) leaves the ``.s`` describing the old music until the
next build, and PorySuite — which reads the ``.s`` — shows the old music and
refuses edits against it. Refreshing does for one song exactly what the build
would do, through the same checks as every other song save: mid2agb runs in a
temp folder with the song's own flags, and the ``.s`` is replaced all at once
(``commit_files``). The ``.mid`` and ``midi.cfg`` are never written, and the
``.mid``'s timestamp is never moved (it is the other editor's file).

Who changed what is decided from content, not timestamps (PorySuite itself
moves timestamps to steer the build):

* ``in_sync``      — the ``.s`` is exactly what the ``.mid`` builds to.
* ``midi_changed`` — they differ and the ``.mid`` is not the one PorySuite
  last wrote (the MIDI ledger in ``midi_ownership``): another editor changed
  it. Refreshing is what the next build would do anyway.
* ``conflict``     — they differ although the ``.mid`` IS PorySuite's own: the
  ``.s`` was changed by something else since. Refreshing would replace those
  changes, so it needs the user's explicit choice (``allow_conflict``).
* ``no_s``         — a real ``.mid`` and no ``.s`` yet (a song registered by
  another editor): refreshing creates it.
* ``no_midi`` / ``no_cfg`` — nothing to refresh from / no flags to build with.

The ``.s`` being replaced is first copied to PorySuite's cache folder
(``replaced_s/<song>-<time>.s``), so even an accepted conflict loses nothing.
"""

from __future__ import annotations

import logging
import os
import shutil
import time
from dataclasses import dataclass

from core.sound.safe_song_save import (
    SongSaveRefused, commit_files, compare_s, mid2agb_flags, render_s_with_mid2agb,
)
from core.sound.midi_ownership import (
    MIDI_SUBDIR, is_external_edit, is_placeholder_mid,
)

_log = logging.getLogger("SoundEditor.MidiRefresh")

REFRESH_STATES = ('midi_changed', 'conflict', 'no_s')


@dataclass
class MidiSyncStatus:
    label: str
    state: str
    detail: str = ""          # what differs, in words (first few lines)
    changed_lines: int = 0

    @property
    def can_refresh(self) -> bool:
        return self.state in REFRESH_STATES

    def message(self) -> str:
        if self.state == 'in_sync':
            return "The song is up to date with its MIDI file."
        if self.state == 'midi_changed':
            return ("This song's MIDI file was changed outside PorySuite (for example in "
                    "PoryDAW), so what PorySuite shows is out of date. Refresh from MIDI "
                    "to load the new version.")
        if self.state == 'conflict':
            return ("This song has changes in both its MIDI file and its assembled music "
                    "file (.s), so PorySuite cannot safely tell which changes you want to keep. "
                    "Refresh from MIDI would replace the .s changes with what the MIDI builds "
                    "to (a backup of the .s is kept). To keep the .s changes instead, leave "
                    "the song as it is.")
        if self.state == 'no_s':
            return ("This song has a MIDI file but has never been built. Refresh from MIDI "
                    "to build just this song.")
        if self.state == 'no_cfg':
            return "This song has no line in midi.cfg, so it cannot be built from its MIDI."
        return "This song has no MIDI file to refresh from."


def _paths(project_root: str, label: str):
    md = os.path.join(project_root, MIDI_SUBDIR)
    return os.path.join(md, label + ".mid"), os.path.join(md, label + ".s")


def midi_sync_status(project_root: str, label: str) -> MidiSyncStatus:
    """How *label*'s ``.s`` stands against its ``.mid``. Read-only."""
    mid_path, s_path = _paths(project_root, label)
    if not os.path.isfile(mid_path) or is_placeholder_mid(mid_path):
        return MidiSyncStatus(label, 'no_midi')
    flags = mid2agb_flags(project_root, label)
    if flags is None:
        return MidiSyncStatus(label, 'no_cfg')
    if not os.path.isfile(s_path):
        return MidiSyncStatus(label, 'no_s')
    built = render_s_with_mid2agb(project_root, label, open(mid_path, "rb").read(), flags)
    disk = open(s_path, "rb").read().decode("latin-1")
    d = compare_s(disk, built, label)
    if d.same_length and not d.changed:
        return MidiSyncStatus(label, 'in_sync')
    state = ('midi_changed' if is_external_edit(project_root, label, when_unknown=True)
             else 'conflict')
    return MidiSyncStatus(label, state, d.describe(3), len(d.changed))


def _backup_dir(project_root: str) -> str:
    try:
        from core.app_info import get_cache_dir
    except ImportError:  # pragma: no cover
        from app_info import get_cache_dir
    return os.path.join(get_cache_dir(project_root), "replaced_s")


def refresh_from_midi(project_root: str, label: str, *, allow_conflict: bool = False) -> MidiSyncStatus:
    """Rebuild *label*'s ``.s`` from its ``.mid``, as the game build would.
    Returns the status it acted on. Raises ``SongSaveRefused`` (nothing
    written) when there is nothing to refresh from, or on a conflict the
    caller did not accept."""
    status = midi_sync_status(project_root, label)
    if status.state == 'in_sync':
        return status
    if not status.can_refresh:
        raise SongSaveRefused(status.message())
    if status.state == 'conflict' and not allow_conflict:
        raise SongSaveRefused(status.message() + " Nothing was written.")
    mid_path, s_path = _paths(project_root, label)
    new_s = render_s_with_mid2agb(project_root, label, open(mid_path, "rb").read(),
                                  mid2agb_flags(project_root, label))
    if os.path.isfile(s_path):
        bdir = _backup_dir(project_root)
        os.makedirs(bdir, exist_ok=True)
        backup = os.path.join(bdir, f"{label}-{time.strftime('%Y%m%d-%H%M%S')}.s")
        shutil.copy2(s_path, backup)
        if open(backup, "rb").read() != open(s_path, "rb").read():
            raise SongSaveRefused("the current .s could not be backed up; nothing was written")
    commit_files([(s_path, new_s.encode("latin-1"))])
    # Newer than its .mid and midi.cfg, so the build sees nothing left to do
    # (it would produce this same file). The .mid's own time is left alone.
    cfg = os.path.join(project_root, MIDI_SUBDIR, "midi.cfg")
    newest = max([time.time()] + [os.path.getmtime(p) + 1 for p in (mid_path, cfg)
                                  if os.path.isfile(p)])
    try:
        os.utime(s_path, (newest, newest))
    except OSError:
        pass
    _log.info("Refreshed %s from its MIDI (%s, %d line(s) differed)", label, status.state,
              status.changed_lines)
    return status
