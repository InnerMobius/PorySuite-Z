"""Real-time sequencer for the Piano Roll.

Plays notes on-the-fly as a cursor advances through the timeline.
No pre-rendering of the full song — each note is synthesized when the
cursor reaches it and mixed into the live audio output.

This is how a real DAW piano roll works:
  - Cursor advances based on BPM
  - When cursor crosses a note's start tick, that note is queued for
    rendering on a background thread (not in the audio callback)
  - A worker thread renders notes and adds them to the active voice pool
  - Active voices are mixed together sample-by-sample in the audio callback
  - Pause/resume/seek are instant because there's no buffer to re-render
"""

from __future__ import annotations

import threading
from collections import deque
from dataclasses import dataclass, field
from typing import Optional

import numpy as np

from core.sound.audio_engine import (
    OUTPUT_SAMPLE_RATE,
    render_instrument,
    apply_pan,
)
from core.sound.voicegroup_parser import Voicegroup, VoicegroupData
from core.sound.sample_loader import SampleData


# ---------------------------------------------------------------------------
# Active voice — a note that is currently producing sound
# ---------------------------------------------------------------------------

@dataclass
class _ActiveVoice:
    """A note currently playing in the output mix."""
    audio: np.ndarray     # pre-rendered mono float32 samples for this note
    position: int = 0     # current read position within audio
    pan: int = 64         # stereo pan (0=L, 64=C, 127=R)
    track: int = 0        # which track this belongs to
    finished: bool = False


# ---------------------------------------------------------------------------
# Render request — sent from audio callback to worker thread
# ---------------------------------------------------------------------------

@dataclass
class _RenderRequest:
    """A note waiting to be rendered by the worker thread."""
    voice_slot: int
    pitch: int
    velocity: int
    duration_samples: int
    release_samples: int
    track_vol: float
    pan: int
    track: int


# ---------------------------------------------------------------------------
# Per-track state (instrument, volume, pan from the song data)
# ---------------------------------------------------------------------------

@dataclass
class TrackPlayState:
    """Playback state for one track."""
    voice: int = 0        # current instrument slot (0-127)
    volume: int = 100     # track volume (0-127)
    pan: int = 64         # 0=left, 64=center, 127=right
    key_shift: int = 0
    muted: bool = False
    bend: float = 0.0     # pitch bend in semitones (from BEND command)
    bend_range: int = 2   # BENDR: max semitones (default 2)


# ---------------------------------------------------------------------------
# Real-time Sequencer
# ---------------------------------------------------------------------------

class RealtimeSequencer:
    """Plays piano roll notes in real-time with no pre-rendering.

    Note rendering happens on a background worker thread, NOT in the
    audio callback. The callback only does lightweight mixing of already-
    rendered voices. This prevents buffer underruns and tempo glitches.

    Usage:
        seq = RealtimeSequencer(voicegroup, sample_data, voicegroup_data, bpm)
        seq.set_notes(notes, track_states)
        seq.play(start_tick=0)
        ...
        seq.pause()
        # user edits notes
        seq.set_notes(new_notes, track_states)
        seq.resume()
        ...
        seq.stop()
    """

    def __init__(
        self,
        voicegroup: Voicegroup,
        sample_data: SampleData,
        voicegroup_data: VoicegroupData,
        bpm: int = 120,
        tbs: int = 1,
    ):
        self._vg = voicegroup
        self._sample_data = sample_data
        self._vg_data = voicegroup_data
        self._bpm = bpm
        self._tbs = tbs

        # Timing: how many output samples per tick
        ticks_per_frame = bpm * tbs / 150.0
        ticks_per_second = ticks_per_frame * 59.7275
        self._samples_per_tick = OUTPUT_SAMPLE_RATE / ticks_per_second

        # Note data (protected by lock for thread safety)
        self._lock = threading.Lock()
        self._notes: list[dict] = []          # sorted by tick
        self._track_states: dict[int, TrackPlayState] = {}
        self._note_index = 0                  # next note to check in sorted list

        # Playback state
        self._tick_accumulator: float = 0.0   # fractional tick position
        self._current_tick: int = 0
        self._active_voices: list[_ActiveVoice] = []
        self._playing = False
        self._volume = 0.8
        self._stream = None

        # Visible tracks (for mute/solo)
        self._visible_tracks: Optional[set[int]] = None

        # Loop region
        self._loop_start: Optional[int] = None
        self._loop_end: Optional[int] = None

        # Background render thread
        self._render_queue: deque[_RenderRequest] = deque()
        self._render_thread: Optional[threading.Thread] = None
        self._render_stop = threading.Event()

    # ── Public API ─────────────────────────────────────────────────────

    def set_notes(self, notes: list[dict], track_states: dict[int, TrackPlayState]):
        """Update the note list and track states.

        Can be called while playing — takes effect immediately for
        notes the cursor hasn't reached yet.

        notes: list of dicts with keys: tick, pitch, duration, velocity, track
               Control events have an extra 'type' key ('BEND', 'BENDR', 'VOL',
               'PAN') and are processed to update per-track playback state
               before notes at the same tick.
        track_states: dict of track_index -> TrackPlayState
        """
        # Sort by tick, then priority (control events=0 before notes=1)
        sorted_notes = sorted(
            notes,
            key=lambda n: (n['tick'], 0 if 'type' in n else 1),
        )
        with self._lock:
            self._notes = sorted_notes
            self._track_states = dict(track_states)
            # Reset note index to find notes at or after current tick
            self._note_index = self._find_note_index(self._current_tick)

    def set_visible_tracks(self, visible: Optional[set[int]]):
        """Set which tracks are audible (for mute/solo)."""
        with self._lock:
            self._visible_tracks = visible

    def set_bpm(self, bpm: int):
        """Update the tempo. Recalculates timing so playback speed changes
        immediately without restarting."""
        with self._lock:
            self._bpm = bpm
            ticks_per_frame = bpm * self._tbs / 150.0
            ticks_per_second = ticks_per_frame * 59.7275
            self._samples_per_tick = OUTPUT_SAMPLE_RATE / ticks_per_second

    def set_loop(self, start: Optional[int], end: Optional[int]):
        """Set loop region. None = no loop."""
        with self._lock:
            self._loop_start = start
            self._loop_end = end

    def update_voicegroup(self, voicegroup: Voicegroup):
        """Switch to a different voicegroup. Takes effect on next note trigger.

        Active voices keep playing with their already-rendered audio.
        New notes triggered after this call will use the new voicegroup.
        """
        with self._lock:
            self._vg = voicegroup

    def set_track_volume(self, track_index: int, volume: int):
        """Update a track's volume in real time (0-127).

        Active voices keep their current volume. New notes triggered
        after this call use the updated volume.
        """
        with self._lock:
            ts = self._track_states.get(track_index)
            if ts:
                ts.volume = max(0, min(127, volume))

    def set_track_pan(self, track_index: int, pan: int):
        """Update a track's pan in real time (0=left, 64=center, 127=right).

        Active voices keep their current pan. New notes triggered
        after this call use the updated pan.
        """
        with self._lock:
            ts = self._track_states.get(track_index)
            if ts:
                ts.pan = max(0, min(127, pan))

    def set_track_instrument(self, track_index: int, voice_slot: int):
        """Change which instrument slot a track uses (0-127).

        Takes effect on the next note trigger for this track.
        """
        with self._lock:
            ts = self._track_states.get(track_index)
            if ts:
                ts.voice = max(0, min(127, voice_slot))

    @property
    def current_tick(self) -> int:
        return self._current_tick

    @property
    def is_playing(self) -> bool:
        return self._playing

    @property
    def volume(self) -> float:
        return self._volume

    @volume.setter
    def volume(self, val: float):
        self._volume = max(0.0, min(1.0, val))

    def play(self, start_tick: int = 0):
        """Start playback from a specific tick position."""
        self.stop()

        import sounddevice as sd

        with self._lock:
            self._current_tick = start_tick
            self._tick_accumulator = float(start_tick)
            self._note_index = self._find_note_index(start_tick)
            self._active_voices.clear()
            self._render_queue.clear()
            self._playing = True

        # Start the background render thread
        self._render_stop.clear()
        self._render_thread = threading.Thread(
            target=self._render_worker, daemon=True)
        self._render_thread.start()

        self._stream = sd.OutputStream(
            samplerate=OUTPUT_SAMPLE_RATE,
            channels=2,
            dtype='float32',
            callback=self._audio_callback,
            blocksize=1024,
        )
        self._stream.start()

    def pause(self):
        """Pause playback. Cursor stays where it is."""
        with self._lock:
            self._playing = False
            self._active_voices.clear()
            self._render_queue.clear()

    def resume(self):
        """Resume from the current tick position."""
        if self._stream is None:
            self.play(self._current_tick)
            return
        with self._lock:
            self._note_index = self._find_note_index(self._current_tick)
            self._playing = True
        # Restart render thread if it's not running
        if self._render_thread is None or not self._render_thread.is_alive():
            self._render_stop.clear()
            self._render_thread = threading.Thread(
                target=self._render_worker, daemon=True)
            self._render_thread.start()

    def stop(self):
        """Stop playback completely."""
        with self._lock:
            self._playing = False
            self._active_voices.clear()
            self._render_queue.clear()
        # Stop render thread
        self._render_stop.set()
        if self._render_thread is not None:
            self._render_thread.join(timeout=1.0)
            self._render_thread = None
        if self._stream is not None:
            try:
                self._stream.stop()
                self._stream.close()
            except Exception:
                pass
            self._stream = None

    def seek(self, tick: int):
        """Jump to a tick position. Clears all sounding notes."""
        with self._lock:
            self._current_tick = tick
            self._tick_accumulator = float(tick)
            self._note_index = self._find_note_index(tick)
            self._active_voices.clear()
            self._render_queue.clear()

    # ── Internal ───────────────────────────────────────────────────────

    def _find_note_index(self, tick: int) -> int:
        """Binary search for the first note at or after the given tick."""
        notes = self._notes
        lo, hi = 0, len(notes)
        while lo < hi:
            mid = (lo + hi) // 2
            if notes[mid]['tick'] < tick:
                lo = mid + 1
            else:
                hi = mid
        return lo

    def _render_worker(self):
        """Background thread: renders notes from the queue into active voices.

        This keeps the audio callback fast — it only does mixing.
        """
        while not self._render_stop.is_set():
            # Grab a request from the queue
            try:
                req = self._render_queue.popleft()
            except IndexError:
                # Queue empty — wait a bit
                self._render_stop.wait(timeout=0.002)
                continue

            # Snapshot the voicegroup under lock (quick)
            with self._lock:
                vg = self._vg

            instrument = vg.get_instrument(req.voice_slot)
            if instrument is None:
                continue

            try:
                audio = render_instrument(
                    instrument, req.pitch, req.velocity,
                    req.duration_samples, self._sample_data, self._vg_data,
                    release_samples=req.release_samples,
                )
            except Exception:
                continue

            if req.track_vol < 1.0:
                audio *= req.track_vol

            voice = _ActiveVoice(
                audio=audio,
                position=0,
                pan=req.pan,
                track=req.track,
            )

            # Add to active voices under lock
            with self._lock:
                if not self._playing:
                    continue
                # Limit active voices to prevent overload
                if len(self._active_voices) >= 32:
                    self._active_voices.pop(0)
                self._active_voices.append(voice)

    def _audio_callback(self, outdata, frames, time_info, status):
        """Sounddevice callback — fills output buffer in real-time.

        ONLY does mixing of already-rendered voices. Note rendering is
        queued to the background worker thread, keeping this callback fast.
        """
        outdata[:] = 0

        with self._lock:
            if not self._playing:
                return

            spt = self._samples_per_tick
            tick_inc = 1.0 / spt  # tick increment per output sample
            notes = self._notes
            visible = self._visible_tracks
            track_states = self._track_states
            write_pos = 0

            while write_pos < frames:
                # How many samples until the next note triggers?
                if self._note_index < len(notes):
                    next_note_tick = notes[self._note_index]['tick']
                    samples_to_next = int(
                        (next_note_tick - self._tick_accumulator) * spt)
                    samples_to_next = max(0, samples_to_next)
                else:
                    samples_to_next = frames  # no more notes

                # Also check loop boundary
                if (self._loop_end is not None
                        and self._loop_start is not None):
                    samples_to_loop = int(
                        (self._loop_end - self._tick_accumulator) * spt)
                    samples_to_loop = max(0, samples_to_loop)
                    samples_to_next = min(samples_to_next, samples_to_loop)

                # Render this chunk (up to next event or end of buffer)
                chunk_len = min(samples_to_next, frames - write_pos)
                if chunk_len <= 0:
                    chunk_len = 1  # always advance at least 1 sample

                # Mix active voices into the output chunk (vectorized)
                end_pos = write_pos + chunk_len
                vol = self._volume
                for voice in self._active_voices:
                    remaining = len(voice.audio) - voice.position
                    if remaining <= 0:
                        voice.finished = True
                        continue
                    usable = min(chunk_len, remaining)
                    chunk = voice.audio[voice.position:voice.position + usable]
                    pan_f = voice.pan / 127.0
                    outdata[write_pos:write_pos + usable, 0] += chunk * (1.0 - pan_f) * vol
                    outdata[write_pos:write_pos + usable, 1] += chunk * pan_f * vol
                    voice.position += usable

                # Advance tick clock
                self._tick_accumulator += chunk_len * tick_inc
                write_pos = end_pos

                # Check for loop wrap
                if (self._loop_end is not None
                        and self._loop_start is not None
                        and self._tick_accumulator >= self._loop_end):
                    self._tick_accumulator = float(self._loop_start)
                    self._note_index = self._find_note_index(self._loop_start)
                    self._active_voices.clear()
                    # NOTE: Do NOT reset BEND state on loop wrap.
                    # The real GBA M4A engine carries BEND state through
                    # loops (GOTO), and the Songs tab renderer does too.
                    continue

                # Queue notes at the current tick for background rendering
                current_tick = int(self._tick_accumulator)
                while (self._note_index < len(notes)
                       and notes[self._note_index]['tick'] <= current_tick):
                    note = notes[self._note_index]
                    self._note_index += 1

                    trk = note.get('track', 0)
                    ts = track_states.get(trk)

                    # Control events: update track state and skip rendering
                    evt_type = note.get('type')
                    if evt_type == 'BEND':
                        if ts:
                            raw = note.get('value', 64)
                            ts.bend = (raw - 64) / 64.0 * ts.bend_range
                        continue
                    if evt_type == 'BENDR':
                        if ts:
                            ts.bend_range = note.get('value', 2)
                        continue
                    if evt_type == 'VOL':
                        if ts:
                            ts.volume = max(0, min(127, note.get('value', 100)))
                        continue
                    if evt_type == 'PAN':
                        if ts:
                            ts.pan = max(0, min(127, note.get('value', 64)))
                        continue
                    if evt_type:
                        continue  # unknown control event

                    if visible is not None and trk not in visible:
                        continue
                    if ts and ts.muted:
                        continue
                    self._queue_note(note, ts)

            # Clean up finished voices
            self._active_voices = [v for v in self._active_voices
                                   if not v.finished]
            self._current_tick = int(self._tick_accumulator)

    def _queue_note(self, note: dict, ts: Optional[TrackPlayState]):
        """Build a render request and add it to the queue for the worker."""
        voice_slot = ts.voice if ts else 0
        velocity = note.get('velocity', 100)
        pitch = note.get('pitch', 60)
        duration_ticks = note.get('duration', 24)

        if ts:
            pitch += ts.key_shift
            # Apply current pitch bend (BEND command offset in semitones)
            pitch += round(ts.bend)
        pitch = max(0, min(127, pitch))

        # Convert duration ticks to samples.
        # Cap at 60 seconds — TIE notes in slow songs can sustain for
        # very long periods (e.g. a sustained bass at 50 BPM).
        duration_samples = max(1, int(duration_ticks * self._samples_per_tick))
        duration_samples = min(duration_samples, OUTPUT_SAMPLE_RATE * 60)
        release_samples = min(2048, duration_samples)

        track_vol = (ts.volume / 127.0) if ts else 1.0
        pan = ts.pan if ts else 64

        self._render_queue.append(_RenderRequest(
            voice_slot=voice_slot,
            pitch=pitch,
            velocity=velocity,
            duration_samples=duration_samples,
            release_samples=release_samples,
            track_vol=track_vol,
            pan=pan,
            track=note.get('track', 0),
        ))


# ---------------------------------------------------------------------------
# Helper: extract track play states from a SongData
# ---------------------------------------------------------------------------

def extract_track_play_states(song_data) -> dict[int, TrackPlayState]:
    """Read the first VOICE, VOL, PAN commands from each track."""
    states = {}
    for i, track in enumerate(song_data.tracks):
        ts = TrackPlayState()
        for cmd in track.commands:
            if cmd.cmd == 'VOICE' and cmd.value is not None:
                ts.voice = cmd.value
                break
        for cmd in track.commands:
            if cmd.cmd == 'VOL' and cmd.value is not None:
                ts.volume = cmd.value
                break
        for cmd in track.commands:
            if cmd.cmd == 'PAN' and cmd.value is not None:
                ts.pan = cmd.value
                break
        states[i] = ts
    if song_data.key_shift:
        for ts in states.values():
            ts.key_shift = song_data.key_shift
    return states
