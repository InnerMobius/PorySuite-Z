"""Polyphony + voice-budget analysis for the MIDI import wizard.

The GBA mixes only a handful of PCM (DirectSound) voices at once, plus 4 fixed
PSG hardware channels (square1, square2, wave, noise); each M4A track is
monophonic. A MIDI can ask for far more — FROZENHY.mid peaks at 18 simultaneous
notes — and the importer silently flattens chords / the engine steals voices, so
it plays broken. This module measures what a MIDI actually DEMANDS (PEAK
SIMULTANEOUS NOTES, not just track count) and reads the project's REAL voice
budget, so the wizard can warn honestly and let the user trim before importing.
"""
from __future__ import annotations

import collections
import os
import re
from dataclasses import dataclass, field
from typing import Dict, List, Tuple

# PSG hardware channels — fixed, one voice each.
PSG_CHANNELS = ("square1", "square2", "wave", "noise")


# ── polyphony ────────────────────────────────────────────────────────────────

@dataclass
class ChannelPoly:
    channel: int                              # 1-based (matches read_midi_info)
    chord_peak: int                           # max notes sounding at once WITHIN this channel
    events: List[Tuple[int, int]] = field(default_factory=list)  # (abs_tick, +1 on / -1 off)

    @property
    def is_chordal(self) -> bool:
        return self.chord_peak > 1


@dataclass
class PolyReport:
    overall_peak: int                         # peak simultaneous notes across the whole song
    overall_peak_tick: int                    # where (abs tick) the peak occurs
    per_channel: Dict[int, ChannelPoly]       # 1-based channel -> ChannelPoly
    total_notes: int
    duration_sec: float
    ticks_per_beat: int

    def peak_beat(self) -> int:
        return self.overall_peak_tick // max(1, self.ticks_per_beat)


def _peak(events: List[Tuple[int, int]]) -> Tuple[int, int]:
    """Max concurrent count + the tick where it first occurs.

    At the same tick, note-OFFs are processed before note-ONs — a note that ends
    exactly as another starts isn't an overlap — giving the true simultaneous
    count (and so the realistic voice demand)."""
    evs = sorted(events, key=lambda e: (e[0], e[1] > 0))
    cur = peak = peak_tick = 0
    for tick, delta in evs:
        cur = max(0, cur + delta)   # stray unmatched note-offs can't drive it negative
        if cur > peak:
            peak, peak_tick = cur, tick
    return peak, peak_tick


def analyze_polyphony(midi_path: str) -> PolyReport:
    """Analyze a MIDI's true polyphony — per channel and overall — from the RAW
    note events (before any flatten/dedupe the importer applies)."""
    import mido

    mid = mido.MidiFile(midi_path)
    ch_events: Dict[int, List[Tuple[int, int]]] = collections.defaultdict(list)
    all_events: List[Tuple[int, int]] = []
    total = 0

    for track in mid.tracks:
        t = 0
        for msg in track:
            t += msg.time
            if msg.type == "note_on" and msg.velocity > 0:
                ch_events[msg.channel].append((t, +1))
                all_events.append((t, +1))
                total += 1
            elif msg.type == "note_off" or (msg.type == "note_on" and msg.velocity == 0):
                ch = getattr(msg, "channel", None)
                if ch is not None:
                    ch_events[ch].append((t, -1))
                    all_events.append((t, -1))

    per_channel: Dict[int, ChannelPoly] = {}
    for ch, evs in ch_events.items():
        cp, _ = _peak(evs)
        per_channel[ch + 1] = ChannelPoly(channel=ch + 1, chord_peak=cp, events=evs)

    overall_peak, overall_tick = _peak(all_events)
    return PolyReport(
        overall_peak=overall_peak,
        overall_peak_tick=overall_tick,
        per_channel=per_channel,
        total_notes=total,
        duration_sec=float(getattr(mid, "length", 0.0) or 0.0),
        ticks_per_beat=mid.ticks_per_beat or 480,
    )


def _mono_events(events: List[Tuple[int, int]]) -> List[Tuple[int, int]]:
    """Collapse a channel's note events to a SINGLE mono voice: +1 when the
    channel starts sounding, -1 when it goes fully silent. mid2agb flattens a
    chordal track to one note at a time, so a flattened channel costs exactly one
    voice no matter how many notes its chords contain."""
    evs = sorted(events, key=lambda e: (e[0], e[1] > 0))
    out: List[Tuple[int, int]] = []
    cur = 0
    for tick, delta in evs:
        prev = cur
        cur += delta
        if prev == 0 and cur > 0:
            out.append((tick, +1))
        elif prev > 0 and cur == 0:
            out.append((tick, -1))
    return out


def combined_peak(channel_polys: List[ChannelPoly], flatten: bool = False) -> Tuple[int, int]:
    """Peak simultaneous VOICES across a SUBSET of channels — the live voice
    meter as the user toggles which channels import onto PCM.

    flatten=True  -> each channel collapses to one mono voice (mid2agb's default;
                     chords flattened to the top note). This is the realistic
                     post-import voice demand.
    flatten=False -> internal chord notes are counted (the cost of SPLITTING
                     chords into separate voices).

    Returns (peak, tick)."""
    merged: List[Tuple[int, int]] = []
    for cp in channel_polys:
        merged.extend(_mono_events(cp.events) if flatten else cp.events)
    return _peak(merged)


def merge_polys(polys: List[ChannelPoly], channel: int) -> ChannelPoly:
    """Combine several channels into one effective channel (a merged track).
    chord_peak is recomputed from the UNION of their note events, so the merged
    track's true polyphony — and the cost of keeping it un-flattened — is right."""
    events: List[Tuple[int, int]] = []
    for p in polys:
        events.extend(p.events)
    pk, _ = _peak(events)
    return ChannelPoly(channel=channel, chord_peak=pk, events=events)


def find_duplicate_instruments(tracks) -> Dict[int, List[int]]:
    """{GM program -> [1-based channels]} for non-drum programs used on more than
    one channel (merge candidates). ``tracks`` is any list of objects exposing
    ``.instrument_num``, ``.channel`` and ``.is_drums`` (e.g. MidiTrackInfo)."""
    by_prog: Dict[int, List[int]] = collections.defaultdict(list)
    for t in tracks:
        if getattr(t, "is_drums", False):
            continue
        prog = getattr(t, "instrument_num", -1)
        if prog is not None and prog >= 0:
            by_prog[prog].append(t.channel)
    return {p: chs for p, chs in by_prog.items() if len(chs) > 1}


# ── project voice budget ──────────────────────────────────────────────────────

@dataclass
class VoiceLimits:
    pcm: int            # simultaneous PCM/DirectSound voices the engine mixes
    psg: int            # PSG hardware channels (always 4: square1, square2, wave, noise)
    track_cap: int      # max tracks per song (MAX_MUSICPLAYER_TRACKS)
    pcm_source: str     # human note on where the PCM number came from


_DEFAULT = VoiceLimits(pcm=8, psg=4, track_cap=16, pcm_source="default (project value unreadable)")


def _read(path: str) -> str:
    try:
        with open(path, encoding="utf-8", errors="ignore") as fh:
            return fh.read()
    except OSError:
        return ""


def read_voice_limits(project_root: str) -> VoiceLimits:
    """Read the project's REAL voice budget — never hardcoded.

    PCM = the runtime ``SoundInfo.maxChans`` (``src/m4a.c``; pokefirered's default
    is 8 — the actual mixing limit, smaller than the 12-slot hardware array).
    Falls back to ``MAX_DIRECTSOUND_CHANNELS`` then 8. track_cap =
    ``MAX_MUSICPLAYER_TRACKS`` (fallback 16). PSG = 4 (fixed hardware)."""
    pcm = None
    pcm_src = ""
    m4a_c = _read(os.path.join(project_root, "src", "m4a.c"))
    if m4a_c:
        m = re.search(r"maxChans\s*=\s*(\d+)\s*;", m4a_c)
        if m:
            pcm = int(m.group(1))
            pcm_src = "SoundInfo.maxChans (src/m4a.c)"
    if pcm is None:
        for rel in (("include", "gba", "m4a_internal.h"),
                    ("constants", "m4a_constants.inc")):
            m = re.search(r"MAX_DIRECTSOUND_CHANNELS[ ,]+(\d+)",
                          _read(os.path.join(project_root, *rel)))
            if m:
                pcm = int(m.group(1))
                pcm_src = "MAX_DIRECTSOUND_CHANNELS (hardware array size)"
                break
    if pcm is None:
        return VoiceLimits(**{**_DEFAULT.__dict__})

    cap = None
    m = re.search(r"MAX_MUSICPLAYER_TRACKS\s+(\d+)",
                  _read(os.path.join(project_root, "include", "gba", "m4a_internal.h")))
    if m:
        cap = int(m.group(1))

    return VoiceLimits(pcm=pcm, psg=4,
                       track_cap=cap if cap is not None else _DEFAULT.track_cap,
                       pcm_source=pcm_src)
