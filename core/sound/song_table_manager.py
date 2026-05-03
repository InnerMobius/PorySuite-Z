"""
Manages the song table, songs.h constants, and midi.cfg configuration.

Reads and writes:
  - sound/song_table.inc  (gSongTable — label + player mapping)
  - include/constants/songs.h  (MUS_*/SE_* #defines)
  - sound/songs/midi/midi.cfg  (mid2agb options per song)
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from typing import Optional

from core.sound.sound_constants import PLAYER_NAMES


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------

@dataclass
class SongEntry:
    """One entry in the song table."""
    index: int               # position in gSongTable (= the #define value)
    label: str               # assembly label e.g. 'mus_cycling'
    constant: str            # C constant e.g. 'MUS_CYCLING'
    music_player: int        # 0=BGM, 1=SE1, 2=SE2, 3=SE3
    unknown: int             # second field (often same as music_player)

    # From midi.cfg (if available)
    voicegroup_index: Optional[int] = None   # -G flag value
    reverb: Optional[int] = None             # -R flag value
    volume: Optional[int] = None             # -V flag value
    priority: Optional[int] = None           # -P flag value
    extra_flags: str = ''                    # any other flags (e.g. -E)
    midi_filename: Optional[str] = None      # e.g. 'mus_cycling.mid'

    @property
    def player_name(self) -> str:
        return PLAYER_NAMES.get(self.music_player, f'PLAYER_{self.music_player}')

    @property
    def is_bgm(self) -> bool:
        return self.music_player == 0

    @property
    def is_se(self) -> bool:
        return self.music_player != 0

    @property
    def friendly_name(self) -> str:
        """Convert MUS_CYCLING -> Cycling, SE_USE_ITEM -> Use Item."""
        name = self.constant
        for prefix in ('MUS_', 'SE_'):
            if name.startswith(prefix):
                name = name[len(prefix):]
                break
        return name.replace('_', ' ').title()


@dataclass
class SongTableData:
    """Complete parsed song table + constants + config."""
    entries: list[SongEntry] = field(default_factory=list)

    # Lookup helpers
    _by_index: dict[int, SongEntry] = field(default_factory=dict, repr=False)
    _by_label: dict[str, SongEntry] = field(default_factory=dict, repr=False)
    _by_constant: dict[str, SongEntry] = field(default_factory=dict, repr=False)

    def rebuild_indices(self):
        self._by_index = {e.index: e for e in self.entries}
        self._by_label = {e.label: e for e in self.entries}
        self._by_constant = {e.constant: e for e in self.entries}

    def by_index(self, idx: int) -> Optional[SongEntry]:
        return self._by_index.get(idx)

    def by_label(self, label: str) -> Optional[SongEntry]:
        return self._by_label.get(label)

    def by_constant(self, const: str) -> Optional[SongEntry]:
        return self._by_constant.get(const)

    @property
    def bgm_entries(self) -> list[SongEntry]:
        return [e for e in self.entries if e.is_bgm]

    @property
    def se_entries(self) -> list[SongEntry]:
        return [e for e in self.entries if e.is_se]

    @property
    def next_index(self) -> int:
        return max((e.index for e in self.entries), default=-1) + 1


# ---------------------------------------------------------------------------
# Parsers
# ---------------------------------------------------------------------------

_RE_SONG_MACRO = re.compile(r'song\s+(\w+)\s*,\s*(\d+)\s*,\s*(\d+)')
_RE_DEFINE = re.compile(
    r'#define\s+((?:MUS|SE)_\w+)\s+(\d+)\s'
)
_RE_CFG_LINE = re.compile(
    r'^(\S+\.mid):\s*(.*)'
)


def _parse_song_table(filepath: str) -> list[tuple[str, int, int]]:
    """Parse song_table.inc -> [(label, music_player, unknown), ...]"""
    entries = []
    with open(filepath, 'r', encoding='utf-8') as f:
        for line in f:
            m = _RE_SONG_MACRO.search(line)
            if m:
                entries.append((m.group(1), int(m.group(2)), int(m.group(3))))
    return entries


def _parse_songs_h(filepath: str) -> dict[int, str]:
    """Parse songs.h -> {index: constant_name}"""
    mapping = {}
    with open(filepath, 'r', encoding='utf-8') as f:
        for line in f:
            m = _RE_DEFINE.search(line)
            if m:
                mapping[int(m.group(2))] = m.group(1)
    return mapping


def _parse_midi_cfg(filepath: str) -> dict[str, dict]:
    """Parse midi.cfg -> {midi_filename: {voicegroup_index, reverb, volume, priority, extra_flags}}"""
    config = {}
    if not os.path.isfile(filepath):
        return config
    with open(filepath, 'r', encoding='utf-8') as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith('#'):
                continue
            m = _RE_CFG_LINE.match(line)
            if not m:
                continue
            midi_file = m.group(1)
            flags_str = m.group(2).strip()

            entry: dict = {'midi_filename': midi_file}
            extra = []

            for flag in flags_str.split():
                if flag.startswith('-G'):
                    try:
                        entry['voicegroup_index'] = int(flag[2:])
                    except ValueError:
                        extra.append(flag)
                elif flag.startswith('-R'):
                    try:
                        entry['reverb'] = int(flag[2:])
                    except ValueError:
                        extra.append(flag)
                elif flag.startswith('-V'):
                    try:
                        entry['volume'] = int(flag[2:])
                    except ValueError:
                        extra.append(flag)
                elif flag.startswith('-P'):
                    try:
                        entry['priority'] = int(flag[2:])
                    except ValueError:
                        extra.append(flag)
                else:
                    extra.append(flag)

            entry['extra_flags'] = ' '.join(extra)
            config[midi_file] = entry

    return config


# ---------------------------------------------------------------------------
# Main loader
# ---------------------------------------------------------------------------

def load_song_table(project_root: str) -> SongTableData:
    """Load the complete song table from a pokefirered project."""
    table_path = os.path.join(project_root, 'sound', 'song_table.inc')
    songs_h_path = os.path.join(project_root, 'include', 'constants', 'songs.h')
    cfg_path = os.path.join(project_root, 'sound', 'songs', 'midi', 'midi.cfg')

    # Parse all three sources
    raw_entries = _parse_song_table(table_path)
    constants_map = _parse_songs_h(songs_h_path)
    midi_config = _parse_midi_cfg(cfg_path)

    # Build unified entries
    data = SongTableData()
    for idx, (label, player, unk) in enumerate(raw_entries):
        constant = constants_map.get(idx, label.upper())

        entry = SongEntry(
            index=idx,
            label=label,
            constant=constant,
            music_player=player,
            unknown=unk,
        )

        # Match midi.cfg by filename convention: label + '.mid'
        midi_file = label + '.mid'
        cfg = midi_config.get(midi_file, {})
        if cfg:
            entry.midi_filename = cfg.get('midi_filename', midi_file)
            entry.voicegroup_index = cfg.get('voicegroup_index')
            entry.reverb = cfg.get('reverb')
            entry.volume = cfg.get('volume')
            entry.priority = cfg.get('priority')
            entry.extra_flags = cfg.get('extra_flags', '')

        data.entries.append(entry)

    data.rebuild_indices()
    return data


# ---------------------------------------------------------------------------
# Writers (for add/remove/edit song operations)
# ---------------------------------------------------------------------------

def write_song_table(project_root: str, data: SongTableData):
    """Write the song_table.inc file."""
    table_path = os.path.join(project_root, 'sound', 'song_table.inc')
    lines = ['gSongTable::\n']
    for entry in data.entries:
        lines.append(f'\tsong {entry.label}, {entry.music_player}, {entry.unknown}\n')
    # Footer: dummy_song_header is required by the engine
    lines.append('\n')
    lines.append('dummy_song_header:\n')
    lines.append('\t.byte 0, 0, 0, 0\n')
    with open(table_path, 'w', encoding='utf-8') as f:
        f.writelines(lines)


def write_songs_h(project_root: str, data: SongTableData):
    """Write the songs.h constants file."""
    songs_h_path = os.path.join(project_root, 'include', 'constants', 'songs.h')
    lines = [
        '#ifndef GUARD_CONSTANTS_SONGS_H\n',
        '#define GUARD_CONSTANTS_SONGS_H\n',
        '\n',
    ]

    # Find max constant name length for alignment
    max_len = max((len(e.constant) for e in data.entries), default=20)

    for entry in data.entries:
        padded = entry.constant.ljust(max_len)
        lines.append(f'#define {padded} {entry.index}\n')

    lines.append('\n')
    lines.append('#define MUS_NONE                    0xFFFF\n')
    lines.append('\n')
    lines.append('#endif  // GUARD_CONSTANTS_SONGS_H\n')

    with open(songs_h_path, 'w', encoding='utf-8') as f:
        f.writelines(lines)


def write_midi_cfg(project_root: str, data: SongTableData,
                   removed_midi_files: set[str] | None = None):
    """Write the midi.cfg file, preserving entries not in our data model.

    midi.cfg contains build rules for ALL songs — both MUS_ (background music)
    and SE_ (sound effects).  Our SongTableData only tracks entries from
    song_table.inc, which may not include every SE_.  A full rewrite would
    drop those untracked entries, breaking the build.

    Strategy: read the existing file, update/add/remove only entries whose
    label matches one in our data, and leave everything else untouched.

    Args:
        removed_midi_files: optional set of midi filenames (e.g. 'mus_old.mid')
            that should be REMOVED from the file. Used by delete_song and
            rename_song to clean up old entries.
    """
    cfg_path = os.path.join(project_root, 'sound', 'songs', 'midi', 'midi.cfg')
    removed = removed_midi_files or set()

    # Build a set of midi filenames we manage
    managed: dict[str, SongEntry] = {}
    for entry in data.entries:
        midi_file = entry.midi_filename or (entry.label + '.mid')
        managed[midi_file] = entry

    # Read existing lines, tracking which managed entries we've seen
    existing_lines: list[str] = []
    seen_managed: set[str] = set()
    if os.path.isfile(cfg_path):
        with open(cfg_path, 'r', encoding='utf-8') as f:
            for line in f:
                stripped = line.strip()
                if not stripped or stripped.startswith('#'):
                    existing_lines.append(line)
                    continue

                m = _RE_CFG_LINE.match(stripped)
                if m:
                    midi_file = m.group(1)
                    if midi_file in removed:
                        # Explicitly removed — skip this line
                        continue
                    elif midi_file in managed:
                        # Replace with our version
                        seen_managed.add(midi_file)
                        entry = managed[midi_file]
                        existing_lines.append(_format_cfg_line(entry) + '\n')
                    else:
                        # Not managed by us — preserve as-is
                        existing_lines.append(line)
                else:
                    existing_lines.append(line)

    # Append any managed entries that weren't already in the file
    for midi_file, entry in managed.items():
        if midi_file not in seen_managed:
            existing_lines.append(_format_cfg_line(entry) + '\n')

    with open(cfg_path, 'w', encoding='utf-8') as f:
        f.writelines(existing_lines)

    # CRITICAL: midi.cfg is a Makefile dependency for EVERY .s file.
    # Rewriting midi.cfg makes its mtime newer than all .s files, so the
    # next `make` will re-run mid2agb on EVERY song.  For songs with
    # placeholder .mid files (e.g. .s-imported or piano-roll-edited songs),
    # mid2agb produces empty/garbage output that wipes the real music.
    #
    # Fix: touch every .s file so they're all newer than midi.cfg.
    midi_dir = os.path.dirname(cfg_path)
    for fn in os.listdir(midi_dir):
        if fn.endswith('.s'):
            s_path = os.path.join(midi_dir, fn)
            try:
                os.utime(s_path)
            except OSError:
                pass


def _format_cfg_line(entry: SongEntry) -> str:
    """Format a single midi.cfg line from a SongEntry."""
    midi_file = entry.midi_filename or (entry.label + '.mid')
    padded_fn = (midi_file + ':').ljust(30)

    flags = []
    if entry.extra_flags:
        flags.append(entry.extra_flags)
    if entry.reverb is not None:
        flags.append(f'-R{entry.reverb:02d}')
    if entry.voicegroup_index is not None:
        flags.append(f'-G{entry.voicegroup_index:03d}')
    if entry.volume is not None:
        flags.append(f'-V{entry.volume:03d}')
    if entry.priority is not None:
        flags.append(f'-P{entry.priority}')

    return f'{padded_fn} {" ".join(flags)}'


# ---------------------------------------------------------------------------
# Rename / Delete operations
# ---------------------------------------------------------------------------

def rename_song(
    project_root: str,
    old_constant: str,
    new_constant: str,
) -> str:
    """Rename a song's constant and all associated files/references.

    Updates songs.h, song_table.inc, midi.cfg, and renames the .s file.
    Returns the new label on success, raises ValueError on failure.
    """
    data = load_song_table(project_root)
    entry = data.by_constant(old_constant)
    if entry is None:
        raise ValueError(f"Song '{old_constant}' not found in song table")

    # Derive new label from new constant (lowercase)
    new_label = new_constant.lower()
    old_label = entry.label

    # Update the entry
    entry.constant = new_constant
    entry.label = new_label

    # Update midi filename
    old_midi = entry.midi_filename or (old_label + '.mid')
    entry.midi_filename = new_label + '.mid'

    # Write all three config files
    write_songs_h(project_root, data)
    write_song_table(project_root, data)
    write_midi_cfg(project_root, data, removed_midi_files={old_midi})
    data.rebuild_indices()

    # Rename the .s file
    midi_dir = os.path.join(project_root, 'sound', 'songs', 'midi')
    old_s = os.path.join(midi_dir, f'{old_label}.s')
    new_s = os.path.join(midi_dir, f'{new_label}.s')

    if os.path.isfile(old_s):
        # Rewrite internal labels in the .s file
        with open(old_s, 'r', encoding='utf-8') as f:
            content = f.read()

        # Replace the old label with the new one (assembly labels)
        content = content.replace(old_label, new_label)

        # Write to new file (or same if name didn't change)
        with open(new_s, 'w', encoding='utf-8') as f:
            f.write(content)

        # Remove old file if name actually changed
        if old_s != new_s and os.path.isfile(old_s):
            os.remove(old_s)

    # Rename the companion .mid file if it exists.
    # CRITICAL: Make's audio rule infers the .s target from the .mid filename.
    # If the .mid keeps its old name, every build will try to assemble a .s
    # that no longer exists at that path, producing:
    #   "can't open sound/songs/midi/<old_label>.s for reading"
    # Also clean up any stale compiled .o under build/*/sound/songs/midi/
    # baked from the old .mid name so the linker doesn't pull a dead object.
    old_mid = os.path.join(midi_dir, old_midi)
    new_mid = os.path.join(midi_dir, entry.midi_filename)
    if os.path.isfile(old_mid) and old_mid != new_mid:
        try:
            if os.path.isfile(new_mid):
                os.remove(new_mid)
            os.rename(old_mid, new_mid)
        except OSError:
            # Non-fatal: the rename will surface as a build error and the
            # user can retry. Better than crashing the rename action.
            pass

    # Sweep stale .o objects compiled from the old name out of build/.
    build_root = os.path.join(project_root, 'build')
    if os.path.isdir(build_root):
        old_o_name = f'{old_label}.o'
        for sub in os.listdir(build_root):
            stale_o = os.path.join(build_root, sub, 'sound', 'songs', 'midi', old_o_name)
            if os.path.isfile(stale_o):
                try:
                    os.remove(stale_o)
                except OSError:
                    pass

    return new_label


def delete_song(
    project_root: str,
    constant: str,
) -> None:
    """Delete a song from the project.

    Removes from songs.h, song_table.inc, midi.cfg, and deletes the .s file.
    Re-indexes all remaining songs so there are no gaps.
    """
    data = load_song_table(project_root)
    entry = data.by_constant(constant)
    if entry is None:
        raise ValueError(f"Song '{constant}' not found in song table")

    label = entry.label
    deleted_midi = entry.midi_filename or (label + '.mid')

    # Remove the entry
    data.entries.remove(entry)

    # Re-index all remaining entries (no gaps allowed)
    for i, e in enumerate(data.entries):
        e.index = i

    # Write all three config files
    write_songs_h(project_root, data)
    write_song_table(project_root, data)
    write_midi_cfg(project_root, data, removed_midi_files={deleted_midi})
    data.rebuild_indices()

    # Delete the .s file and .mid file
    midi_dir = os.path.join(project_root, 'sound', 'songs', 'midi')
    for ext in ('.s', '.mid'):
        path = os.path.join(midi_dir, f'{label}{ext}')
        if os.path.isfile(path):
            os.remove(path)

    # Clean up build artifacts (.o files) if they exist
    for build_dir_name in ('build/firered_modern', 'build/firered'):
        obj_path = os.path.join(
            project_root, build_dir_name, 'sound', 'songs', 'midi',
            f'{label}.o')
        if os.path.isfile(obj_path):
            os.remove(obj_path)


def cleanup_orphaned_songs(project_root: str) -> list[str]:
    """Find and remove orphaned song registrations AND stray MIDI files.

    Two kinds of cleanup:

    1. **Orphaned registrations** — a MUS_* entry in song_table.inc / songs.h /
       midi.cfg whose .s assembly file is missing on disk.  Removes from all
       three config files and re-indexes.

    2. **Stray MIDI files** — .mid files in the midi directory whose stem
       doesn't match any registered song label.  These are leftovers from
       imports that used the original filename instead of the label name.
       The Makefile's wildcard picks them up and tries to build from them,
       causing "can't open .s for reading" errors.  Deleted silently.

    Returns a list of names describing what was cleaned up.
    """
    data = load_song_table(project_root)
    midi_dir = os.path.join(project_root, 'sound', 'songs', 'midi')
    cleaned: list[str] = []

    # ── 1. Orphaned registrations (MUS_* with no .s file) ─────────────
    orphans: list[SongEntry] = []
    for entry in data.entries:
        if not entry.constant.startswith('MUS_'):
            continue
        if entry.constant == 'MUS_DUMMY':
            continue
        s_path = os.path.join(midi_dir, f'{entry.label}.s')
        if not os.path.isfile(s_path):
            orphans.append(entry)

    if orphans:
        removed_midis = set()
        for orphan in orphans:
            cleaned.append(orphan.constant)
            removed_midis.add(orphan.midi_filename or (orphan.label + '.mid'))
            data.entries.remove(orphan)

        # Re-index remaining entries (no gaps)
        for i, e in enumerate(data.entries):
            e.index = i

        # Rewrite all three config files
        write_songs_h(project_root, data)
        write_song_table(project_root, data)
        write_midi_cfg(project_root, data, removed_midi_files=removed_midis)
        data.rebuild_indices()

        # Clean up orphaned .mid files and build artifacts
        for orphan in orphans:
            mid_path = os.path.join(midi_dir, f'{orphan.label}.mid')
            if os.path.isfile(mid_path):
                os.remove(mid_path)
            for build_dir_name in ('build/firered_modern', 'build/firered'):
                obj_path = os.path.join(
                    project_root, build_dir_name, 'sound', 'songs', 'midi',
                    f'{orphan.label}.o')
                if os.path.isfile(obj_path):
                    os.remove(obj_path)

    # ── 2. Stray .mid files (not matching any registered song label) ──
    # NOTE: known_labels comes from song_table.inc, which does NOT include
    # all SE_ entries that exist only in midi.cfg.  Only delete a .mid file
    # if it also has NO entry in midi.cfg (check both sources).
    known_labels = {e.label for e in data.entries}
    cfg_labels: set[str] = set()
    existing_cfg = _parse_midi_cfg(os.path.join(midi_dir, 'midi.cfg'))
    for midi_file in existing_cfg:
        cfg_labels.add(midi_file[:-4] if midi_file.endswith('.mid') else midi_file)

    if os.path.isdir(midi_dir):
        for fn in os.listdir(midi_dir):
            if not fn.endswith('.mid'):
                continue
            stem = fn[:-4]  # strip '.mid'
            if stem not in known_labels and stem not in cfg_labels:
                stray_path = os.path.join(midi_dir, fn)
                try:
                    os.remove(stray_path)
                    cleaned.append(f'stray:{fn}')
                except OSError:
                    pass

    return cleaned


def find_song_references(project_root: str, constant: str) -> list[str]:
    """Search for references to a song constant in the project source.

    Returns a list of file paths that reference the constant.
    Helps the user understand what will break if they delete a song.

    Skips auto-generated .inc files (they contain a "DO NOT MODIFY" header
    and are rebuilt from .json source files during make).  Searches .json
    files so that Porymap-style map headers are checked too.
    """
    refs = []
    search_dirs = ['src', 'data', 'include']

    for search_dir in search_dirs:
        dir_path = os.path.join(project_root, search_dir)
        if not os.path.isdir(dir_path):
            continue
        for root, dirs, files in os.walk(dir_path):
            for fn in files:
                if not fn.endswith(('.c', '.h', '.inc', '.s', '.json')):
                    continue
                fpath = os.path.join(root, fn)
                # Skip songs.h itself
                if fpath.endswith(os.path.join('constants', 'songs.h')):
                    continue
                try:
                    with open(fpath, 'r', encoding='utf-8', errors='ignore') as f:
                        content = f.read()
                    # Skip auto-generated .inc files — the real source of
                    # truth is the .json file they're generated from.
                    if fn.endswith('.inc') and 'DO NOT MODIFY' in content[:200]:
                        continue
                    if constant in content:
                        rel = os.path.relpath(fpath, project_root)
                        refs.append(rel)
                except OSError:
                    pass

    return refs
