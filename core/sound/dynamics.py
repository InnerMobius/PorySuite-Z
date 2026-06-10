"""Inspect / flatten a song's mid-song "dynamics" — the control events the
piano roll does NOT show.

Imported MIDIs carry continuous-controller envelopes (CC7 volume, CC1
modulation, pitch-bend, tempo maps) that become mid-song VOL / MOD / PAN / BEND
/ TEMPO commands in the `.s`.  The piano roll edits NOTES only, so these
envelopes are invisible and uneditable — a song plays with volume dips, tempo
shifts, and vibrato the user never authored and can't double-click.

This module lets the Sound Editor:
  • SHOW how many such mid-song events each track carries (`count_*` /
    `describe_dynamics`), so the hidden dynamics are no longer invisible.
  • FLATTEN them (`flatten_*`) — remove the mid-song events, keeping each
    parameter's tick-0 base value — for a constant, predictable song.

VOICE (instrument) changes are deliberately NOT treated as dynamics: a mid-song
VOICE switch is a structural choice (a different instrument), not a volume/tempo
wobble, so flattening leaves it intact.
"""
from __future__ import annotations

from typing import Dict

# The continuous "expression" controllers that produce mid-song dynamics.
DYNAMICS_CMDS = ("VOL", "PAN", "MOD", "BEND", "BENDR", "TEMPO")

# Friendly labels for the UI readout.
DYNAMICS_LABELS = {
    "VOL": "volume",
    "PAN": "pan",
    "MOD": "vibrato",
    "BEND": "bend",
    "BENDR": "bend-range",
    "TEMPO": "tempo",
}


def count_track_dynamics(track) -> Dict[str, int]:
    """Per-type count of MID-SONG (tick > 0) dynamic control events on a track."""
    out = {c: 0 for c in DYNAMICS_CMDS}
    for cmd in getattr(track, "commands", []) or []:
        if cmd.tick > 0 and cmd.cmd in out:
            out[cmd.cmd] += 1
    return out


def count_song_dynamics(song) -> Dict[str, int]:
    """Per-type count summed across every track in the song."""
    total = {c: 0 for c in DYNAMICS_CMDS}
    for tr in getattr(song, "tracks", []) or []:
        for key, val in count_track_dynamics(tr).items():
            total[key] += val
    return total


def total_mid_song_events(counts: Dict[str, int]) -> int:
    return sum(counts.values())


def describe_dynamics(counts: Dict[str, int]) -> str:
    """Human readout, e.g. ``'38 volume  ·  15 vibrato  ·  1 tempo'``.
    Returns '' when the track/song has no mid-song dynamics."""
    parts = [f"{n} {DYNAMICS_LABELS[cmd]}"
             for cmd, n in counts.items() if n]
    return "  ·  ".join(parts)


def flatten_track_dynamics(track) -> int:
    """Remove every MID-SONG (tick > 0) dynamic control event from a track,
    keeping the tick-0 base values, all notes/waits, and structure.  Returns the
    number of events removed."""
    kept = []
    removed = 0
    for cmd in getattr(track, "commands", []) or []:
        if cmd.tick > 0 and cmd.cmd in DYNAMICS_CMDS:
            removed += 1
            continue
        kept.append(cmd)
    track.commands = kept
    return removed


def flatten_song_dynamics(song) -> int:
    """Flatten every track in the song.  Returns the total events removed."""
    return sum(flatten_track_dynamics(tr)
               for tr in (getattr(song, "tracks", []) or []))
