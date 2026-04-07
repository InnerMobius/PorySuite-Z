"""MIDI Import pipeline for PorySuite-Z Sound Editor.

Reads MIDI files, extracts metadata (tracks, instruments, tempo, duration),
runs mid2agb to convert to GBA assembly, and registers the new song in
song_table.inc, songs.h, and midi.cfg.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
from dataclasses import dataclass, field
from typing import Optional, List, Tuple

import mido


# ── General MIDI instrument names (0-127) ──────────────────────────────────

GM_INSTRUMENTS = [
    "Acoustic Grand Piano", "Bright Acoustic Piano", "Electric Grand Piano",
    "Honky-tonk Piano", "Electric Piano 1", "Electric Piano 2", "Harpsichord",
    "Clavinet", "Celesta", "Glockenspiel", "Music Box", "Vibraphone",
    "Marimba", "Xylophone", "Tubular Bells", "Dulcimer", "Drawbar Organ",
    "Percussive Organ", "Rock Organ", "Church Organ", "Reed Organ",
    "Accordion", "Harmonica", "Tango Accordion", "Nylon Guitar",
    "Steel Guitar", "Jazz Guitar", "Clean Guitar", "Muted Guitar",
    "Overdriven Guitar", "Distortion Guitar", "Guitar Harmonics",
    "Acoustic Bass", "Electric Bass (finger)", "Electric Bass (pick)",
    "Fretless Bass", "Slap Bass 1", "Slap Bass 2", "Synth Bass 1",
    "Synth Bass 2", "Violin", "Viola", "Cello", "Contrabass",
    "Tremolo Strings", "Pizzicato Strings", "Orchestral Harp", "Timpani",
    "String Ensemble 1", "String Ensemble 2", "Synth Strings 1",
    "Synth Strings 2", "Choir Aahs", "Voice Oohs", "Synth Choir",
    "Orchestra Hit", "Trumpet", "Trombone", "Tuba", "Muted Trumpet",
    "French Horn", "Brass Section", "Synth Brass 1", "Synth Brass 2",
    "Soprano Sax", "Alto Sax", "Tenor Sax", "Baritone Sax", "Oboe",
    "English Horn", "Bassoon", "Clarinet", "Piccolo", "Flute", "Recorder",
    "Pan Flute", "Blown Bottle", "Shakuhachi", "Whistle", "Ocarina",
    "Lead 1 (square)", "Lead 2 (sawtooth)", "Lead 3 (calliope)",
    "Lead 4 (chiff)", "Lead 5 (charang)", "Lead 6 (voice)",
    "Lead 7 (fifths)", "Lead 8 (bass + lead)", "Pad 1 (new age)",
    "Pad 2 (warm)", "Pad 3 (polysynth)", "Pad 4 (choir)",
    "Pad 5 (bowed)", "Pad 6 (metallic)", "Pad 7 (halo)", "Pad 8 (sweep)",
    "FX 1 (rain)", "FX 2 (soundtrack)", "FX 3 (crystal)", "FX 4 (atmosphere)",
    "FX 5 (brightness)", "FX 6 (goblins)", "FX 7 (echoes)", "FX 8 (sci-fi)",
    "Sitar", "Banjo", "Shamisen", "Koto", "Kalimba", "Bagpipe", "Fiddle",
    "Shanai", "Tinkle Bell", "Agogo", "Steel Drums", "Woodblock",
    "Taiko Drum", "Melodic Tom", "Synth Drum", "Reverse Cymbal",
    "Guitar Fret Noise", "Breath Noise", "Seashore", "Bird Tweet",
    "Telephone Ring", "Helicopter", "Applause", "Gunshot",
]


@dataclass
class MidiTrackInfo:
    """Info about one track in a MIDI file."""
    channel: int
    name: str              # Track name from MIDI, or "Track N"
    instrument_num: int    # GM instrument number (0-127), -1 for drums
    instrument_name: str   # Human-readable GM instrument name
    note_count: int
    note_min: int          # Lowest MIDI note
    note_max: int          # Highest MIDI note
    is_drums: bool         # True if channel 10 (drums)


@dataclass
class MidiFileInfo:
    """Parsed metadata from a MIDI file."""
    path: str
    filename: str
    tracks: List[MidiTrackInfo] = field(default_factory=list)
    tempo_bpm: float = 120.0
    duration_sec: float = 0.0
    ticks_per_beat: int = 480
    midi_type: int = 0     # 0 or 1
    total_notes: int = 0
    total_measures: int = 0
    time_sig_num: int = 4  # e.g. 4 in 4/4
    time_sig_den: int = 4  # e.g. 4 in 4/4


def read_midi_info(midi_path: str) -> MidiFileInfo:
    """Read a MIDI file and extract track/instrument/tempo metadata."""
    mid = mido.MidiFile(midi_path)

    info = MidiFileInfo(
        path=midi_path,
        filename=os.path.basename(midi_path),
        ticks_per_beat=mid.ticks_per_beat,
        midi_type=mid.type,
        duration_sec=mid.length,
    )

    # Extract tempo from the first tempo message
    for track in mid.tracks:
        for msg in track:
            if msg.type == 'set_tempo':
                info.tempo_bpm = round(mido.tempo2bpm(msg.tempo), 1)
                break
        if info.tempo_bpm != 120.0:
            break

    # Build per-channel info
    channel_notes: dict[int, list[int]] = {}       # channel -> [note pitches]
    channel_programs: dict[int, int] = {}           # channel -> last program number
    channel_names: dict[int, str] = {}              # channel -> track name

    for i, track in enumerate(mid.tracks):
        track_name = None
        for msg in track:
            if msg.type == 'track_name':
                track_name = msg.name
            elif msg.type == 'program_change':
                channel_programs[msg.channel] = msg.program
            elif msg.type == 'note_on' and msg.velocity > 0:
                channel_notes.setdefault(msg.channel, []).append(msg.note)

        # Associate name with channels in this track
        if track_name:
            for msg in track:
                if hasattr(msg, 'channel') and msg.channel not in channel_names:
                    channel_names[msg.channel] = track_name

    # Build track info for each active channel
    for ch in sorted(channel_notes.keys()):
        notes = channel_notes[ch]
        is_drums = (ch == 9)  # MIDI channel 10 = index 9 = drums
        prog = channel_programs.get(ch, 0)
        gm_name = "Drums" if is_drums else (
            GM_INSTRUMENTS[prog] if 0 <= prog < 128 else f"Program {prog}")

        name = channel_names.get(ch, f"Channel {ch + 1}")

        info.tracks.append(MidiTrackInfo(
            channel=ch + 1,  # 1-based for display
            name=name,
            instrument_num=-1 if is_drums else prog,
            instrument_name=gm_name,
            note_count=len(notes),
            note_min=min(notes) if notes else 0,
            note_max=max(notes) if notes else 0,
            is_drums=is_drums,
        ))

    info.total_notes = sum(t.note_count for t in info.tracks)

    # Compute total measures from time signature and total ticks
    ts_num, ts_den = 4, 4
    total_ticks = 0
    for track in mid.tracks:
        track_ticks = 0
        for msg in track:
            track_ticks += msg.time
            if msg.type == 'time_signature':
                ts_num = msg.numerator
                ts_den = msg.denominator
        total_ticks = max(total_ticks, track_ticks)

    info.time_sig_num = ts_num
    info.time_sig_den = ts_den
    ticks_per_measure = mid.ticks_per_beat * ts_num * (4 // ts_den) if ts_den else mid.ticks_per_beat * 4
    info.total_measures = max(1, -(-total_ticks // ticks_per_measure))  # ceiling division

    return info


# ── mid2agb conversion ─────────────────────────────────────────────────────

@dataclass
class Mid2AgbSettings:
    """Settings for mid2agb conversion."""
    voicegroup_num: int = 0
    reverb: int = 0           # 0 = off, 1-127
    master_volume: int = 127  # 0-127
    priority: int = 0         # 0-127
    exact_gate: bool = True   # -E flag (almost always True)
    high_resolution: bool = False  # -X flag (48 clocks/beat)
    no_compression: bool = False   # -N flag


def _find_mid2agb(project_root: str) -> Optional[str]:
    """Locate the mid2agb executable in the project."""
    candidates = [
        os.path.join(project_root, "tools", "mid2agb", "mid2agb.exe"),
        os.path.join(project_root, "tools", "mid2agb", "mid2agb"),
    ]
    for c in candidates:
        if os.path.isfile(c):
            return c
    return None


def run_mid2agb(
    project_root: str,
    midi_path: str,
    output_label: str,
    settings: Mid2AgbSettings,
) -> Tuple[bool, str, str]:
    """Run mid2agb to convert a MIDI file to a GBA .s assembly file.

    Args:
        project_root: Path to the pokefirered project root.
        midi_path: Path to the input .mid file.
        output_label: The song label (e.g. "mus_my_song") — used for
            the output filename and assembly label.
        settings: Conversion settings.

    Returns:
        (success, output_s_path, error_message)
    """
    mid2agb = _find_mid2agb(project_root)
    if not mid2agb:
        return False, "", "mid2agb not found in tools/mid2agb/"

    # The .mid must be in sound/songs/midi/ for the build system
    midi_dir = os.path.join(project_root, "sound", "songs", "midi")
    os.makedirs(midi_dir, exist_ok=True)

    # Copy MIDI to the project's midi directory if not already there
    midi_dest = os.path.join(midi_dir, os.path.basename(midi_path))
    if os.path.abspath(midi_path) != os.path.abspath(midi_dest):
        shutil.copy2(midi_path, midi_dest)

    # Output .s file goes alongside the .mid
    s_filename = output_label + ".s"
    s_path = os.path.join(midi_dir, s_filename)

    # Build command
    cmd = [mid2agb, midi_dest, s_path, f"-L{output_label}"]
    cmd.append(f"-V{settings.master_volume}")
    cmd.append(f"-G{settings.voicegroup_num}")
    if settings.priority > 0:
        cmd.append(f"-P{settings.priority}")
    if settings.reverb > 0:
        cmd.append(f"-R{settings.reverb}")
    if settings.exact_gate:
        cmd.append("-E")
    if settings.high_resolution:
        cmd.append("-X")
    if settings.no_compression:
        cmd.append("-N")

    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=30,
            cwd=project_root,
        )
        if result.returncode != 0:
            err = result.stderr.strip() or result.stdout.strip() or "Unknown error"
            return False, "", f"mid2agb failed: {err}"

        if not os.path.isfile(s_path):
            return False, "", "mid2agb ran but no .s file was produced"

        return True, s_path, ""

    except FileNotFoundError:
        return False, "", f"Could not run mid2agb at {mid2agb}"
    except subprocess.TimeoutExpired:
        return False, "", "mid2agb timed out (30s limit)"
    except Exception as e:
        return False, "", f"mid2agb error: {e}"


# ── Song registration ──────────────────────────────────────────────────────

def _next_constant_index(songs_h_path: str) -> int:
    """Find the next available index for a new song constant in songs.h."""
    max_idx = -1
    if os.path.isfile(songs_h_path):
        with open(songs_h_path, encoding="utf-8") as f:
            for line in f:
                m = re.match(r'#define\s+(?:MUS|SE)_\w+\s+(\d+)', line)
                if m:
                    max_idx = max(max_idx, int(m.group(1)))
    return max_idx + 1


def register_song(
    project_root: str,
    label: str,
    constant: str,
    music_player: int = 0,
    settings: Optional[Mid2AgbSettings] = None,
) -> Tuple[bool, str]:
    """Register a new song in song_table.inc, songs.h, and midi.cfg.

    Args:
        project_root: Path to pokefirered root.
        label: Assembly label (e.g. "mus_my_song").
        constant: Constant name (e.g. "MUS_MY_SONG").
        music_player: 0 = BGM, 1 = SE1, 2 = SE2, 3 = SE3.
        settings: Mid2AgbSettings used during conversion (for midi.cfg).

    Returns:
        (success, error_message)
    """
    if settings is None:
        settings = Mid2AgbSettings()

    # ── 1. Add to songs.h ──────────────────────────────────────────────
    songs_h = os.path.join(project_root, "include", "constants", "songs.h")
    if not os.path.isfile(songs_h):
        return False, "include/constants/songs.h not found"

    next_idx = _next_constant_index(songs_h)

    with open(songs_h, encoding="utf-8") as f:
        content = f.read()

    # Find the last #define line and add after it
    lines = content.split('\n')
    last_define_idx = -1
    for i, line in enumerate(lines):
        if re.match(r'#define\s+(?:MUS|SE)_\w+', line):
            last_define_idx = i

    if last_define_idx == -1:
        return False, "Could not find existing constants in songs.h"

    # Check for duplicate
    if f"#define {constant} " in content or f"#define {constant}\t" in content:
        return False, f"Constant {constant} already exists in songs.h"

    new_line = f"#define {constant} {next_idx}"
    lines.insert(last_define_idx + 1, new_line)

    with open(songs_h, "w", encoding="utf-8", newline="\n") as f:
        f.write('\n'.join(lines))

    # ── 2. Add to song_table.inc ───────────────────────────────────────
    table_path = os.path.join(project_root, "sound", "song_table.inc")
    if not os.path.isfile(table_path):
        return False, "sound/song_table.inc not found"

    with open(table_path, encoding="utf-8") as f:
        table_content = f.read()

    # Add entry before the end marker or at the end of the song entries
    new_entry = f"\tsong {label}, {music_player}, 0\n"

    # Find the last 'song' line
    table_lines = table_content.split('\n')
    last_song_idx = -1
    for i, line in enumerate(table_lines):
        if line.strip().startswith('song '):
            last_song_idx = i

    if last_song_idx == -1:
        return False, "Could not find song entries in song_table.inc"

    table_lines.insert(last_song_idx + 1, new_entry.rstrip())

    with open(table_path, "w", encoding="utf-8", newline="\n") as f:
        f.write('\n'.join(table_lines))

    # ── 3. Add to midi.cfg ─────────────────────────────────────────────
    cfg_path = os.path.join(project_root, "sound", "songs", "midi", "midi.cfg")
    if os.path.isfile(cfg_path):
        # Build the config line to match existing format
        mid_filename = label + ".mid"
        flags = []
        if settings.exact_gate:
            flags.append("-E")
        if settings.reverb > 0:
            flags.append(f"-R{settings.reverb}")
        flags.append(f"-G{settings.voicegroup_num}")
        flags.append(f"-V{settings.master_volume:03d}")
        if settings.priority > 0:
            flags.append(f"-P{settings.priority}")

        cfg_line = f"{mid_filename}:{' ' * max(1, 30 - len(mid_filename))}{' '.join(flags)}\n"

        with open(cfg_path, "a", encoding="utf-8", newline="\n") as f:
            f.write(cfg_line)

    return True, ""


# ── Full import pipeline ───────────────────────────────────────────────────

@dataclass
class ImportResult:
    """Result of a full MIDI import operation."""
    success: bool
    s_file_path: str = ""
    constant: str = ""
    label: str = ""
    error: str = ""
    warnings: List[str] = field(default_factory=list)


def import_midi(
    project_root: str,
    midi_path: str,
    constant: str,
    music_player: int = 0,
    settings: Optional[Mid2AgbSettings] = None,
) -> ImportResult:
    """Full MIDI import: convert with mid2agb, then register in all files.

    Args:
        project_root: Path to pokefirered root.
        midi_path: Path to the .mid file to import.
        constant: Song constant name (e.g. "MUS_MY_SONG").
        music_player: 0 = BGM, 1 = SE1, 2 = SE2, 3 = SE3.
        settings: Conversion settings.

    Returns:
        ImportResult with success status and details.
    """
    if settings is None:
        settings = Mid2AgbSettings()

    # Derive label from constant: MUS_MY_SONG -> mus_my_song
    label = constant.lower()

    result = ImportResult(success=False, constant=constant, label=label)

    # Step 1: Run mid2agb
    ok, s_path, err = run_mid2agb(project_root, midi_path, label, settings)
    if not ok:
        result.error = err
        return result

    result.s_file_path = s_path

    # Step 2: Register in song table, songs.h, midi.cfg
    ok, err = register_song(project_root, label, constant, music_player, settings)
    if not ok:
        result.error = err
        return result

    result.success = True
    return result


def validate_constant_name(name: str, project_root: str) -> Tuple[bool, str]:
    """Check if a song constant name is valid and available.

    Returns (valid, error_message).
    """
    if not name:
        return False, "Name cannot be empty"

    if not name.startswith("MUS_") and not name.startswith("SE_"):
        return False, "Must start with MUS_ or SE_"

    if not re.match(r'^[A-Z][A-Z0-9_]+$', name):
        return False, "Use only uppercase letters, numbers, and underscores"

    if len(name) < 5:
        return False, "Name too short"

    # Check for duplicates
    songs_h = os.path.join(project_root, "include", "constants", "songs.h")
    if os.path.isfile(songs_h):
        with open(songs_h, encoding="utf-8") as f:
            content = f.read()
        if f"#define {name} " in content or f"#define {name}\t" in content:
            return False, f"{name} already exists"

    return True, ""
