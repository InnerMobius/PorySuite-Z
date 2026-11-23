"""Validated, all-or-nothing saves for songs that have a real ``.mid`` source.

Why this module exists
======================

In this project the build source of a song is its ``.mid`` plus its line in
``midi.cfg``; the ``.s`` is what mid2agb makes from them. The Sound Editor's
tempo edit used to throw the real ``.mid`` away, re-draw it from PorySuite's
reading of the ``.s`` (``midi_exporter.song_to_midi``) and rebuild the ``.s``
from that. The re-drawing dropped repeated sections, effects, trailing silence
and a point of volume, so a tempo save — even one that left the tempo where it
was — damaged 355 of the project's 380 tempo-editable songs. See BUGS.md,
"Sound Editor tempo save rewrites the song's .mid through a lossy converter".

The rule here is the opposite: **edit the source in place, change only what the
user changed, prove it, then write.**

* ``save_tempo_change`` patches only the three-byte payloads of the tempo
  events inside the existing ``.mid``. Every other byte of the file is left as
  it was, so nothing else in the song can change.
* Before anything is written, mid2agb (the build's own encoder) is run on the
  candidate ``.mid`` in a temporary folder, and the ``.s`` it produces is
  compared with the ``.s`` on disk. Only the lines the operation is allowed to
  change may differ. Anything else refuses the save.
* ``commit_files`` writes the ``.mid`` and ``.s`` as a pair: both land, or
  neither does. A failure half way puts the first file back.

Nothing in this module writes to the project except ``commit_files``, and it
only does so after validation passed.
"""

from __future__ import annotations

import logging
import os
import re
import shutil
import subprocess
import tempfile
import time
from dataclasses import dataclass, field
from typing import Callable, Optional

_log = logging.getLogger("SoundEditor.SafeSave")

MIDI_SUBDIR = os.path.join("sound", "songs", "midi")


class SongSaveRefused(Exception):
    """A save was stopped before it could change any file.

    ``str(exc)`` is a plain-English reason, suitable for showing the user.
    """


# ─────────────────────────────────────────────────────────────────────────────
# Standard MIDI File walking — just enough to find the tempo events
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class TempoEvent:
    payload_offset: int     # byte offset of the 3-byte tempo value in the file
    track: int              # index of the MTrk chunk (0-based)
    abs_tick: int           # absolute tick within that track
    usec_per_beat: int

    @property
    def bpm(self) -> float:
        return 60_000_000 / self.usec_per_beat


def _read_vlq(data: bytes, i: int) -> tuple[int, int]:
    value = 0
    for _ in range(4):
        if i >= len(data):
            raise ValueError("truncated variable-length quantity")
        b = data[i]
        i += 1
        value = (value << 7) | (b & 0x7F)
        if not b & 0x80:
            return value, i
    raise ValueError("variable-length quantity longer than 4 bytes")


def find_tempo_events(data: bytes) -> list[TempoEvent]:
    """Every Set Tempo meta event in a Standard MIDI File, with its byte offset.

    A real event walk, not a byte search: ``FF 51 03`` can occur inside a text
    or SysEx payload, and patching one of those would corrupt the file.
    Raises ValueError for anything that is not a well-formed SMF.
    """
    if data[:4] != b"MThd":
        raise ValueError("not a Standard MIDI File (no MThd header)")
    hlen = int.from_bytes(data[4:8], "big")
    i = 8 + hlen
    out: list[TempoEvent] = []
    track = -1
    while i + 8 <= len(data):
        cid = data[i:i + 4]
        clen = int.from_bytes(data[i + 4:i + 8], "big")
        start, end = i + 8, i + 8 + clen
        if end > len(data):
            raise ValueError("chunk runs past the end of the file")
        i = end
        if cid != b"MTrk":
            continue
        track += 1
        j, tick, running = start, 0, None
        while j < end:
            delta, j = _read_vlq(data, j)
            tick += delta
            status = data[j]
            if status & 0x80:
                j += 1
            else:
                if running is None:
                    raise ValueError("running status with no previous status")
                status = running
            if status == 0xFF:
                mtype = data[j]
                length, j = _read_vlq(data, j + 1)
                if mtype == 0x51 and length == 3:
                    out.append(TempoEvent(j, track, tick,
                                          int.from_bytes(data[j:j + 3], "big")))
                j += length
                running = None
            elif status in (0xF0, 0xF7):
                length, j = _read_vlq(data, j)
                j += length
                running = None
            else:
                running = status
                kind = status & 0xF0
                # Data bytes start at j whether the status byte was explicit
                # (already stepped over) or implied by running status.
                j += 1 if kind in (0xC0, 0xD0) else 2
        if j != end:
            raise ValueError("track events overrun their chunk")
    return out


def displayed_bpm(usec_per_beat: int) -> int:
    """The BPM mid2agb writes for this tempo (``agb.cpp``: round(60e6 / usec))."""
    return int(round(60_000_000 / usec_per_beat))


def scale_tempos(data: bytes, new_first_bpm: int) -> tuple[bytes, list[tuple[int, int]]]:
    """Return a copy of *data* with every tempo scaled so the FIRST tempo is
    *new_first_bpm* and every later tempo keeps its proportion to it.

    Only tempo payload bytes change; the file length and every other byte stay
    identical. Returns (new_bytes, [(old_usec, new_usec), ...]). If the first
    tempo already shows as *new_first_bpm* the input is returned unchanged.
    """
    events = find_tempo_events(data)
    if not events:
        raise SongSaveRefused("this song's MIDI file has no tempo to change")
    first = min(events, key=lambda e: (e.abs_tick, e.track, e.payload_offset))
    base = displayed_bpm(first.usec_per_beat)
    if new_first_bpm <= 0:
        raise SongSaveRefused(f"{new_first_bpm} BPM is not a valid tempo")
    if new_first_bpm == base:
        return data, []
    ratio = new_first_bpm / base
    buf = bytearray(data)
    changes = []
    for ev in events:
        if ev is first:
            new_us = int(round(60_000_000 / new_first_bpm))
        else:
            new_us = int(round(ev.usec_per_beat / ratio))
        new_us = max(1, min(0xFFFFFF, new_us))
        buf[ev.payload_offset:ev.payload_offset + 3] = new_us.to_bytes(3, "big")
        changes.append((ev.usec_per_beat, new_us))
    return bytes(buf), changes


def midi_differences_except_tempo(old: bytes, new: bytes) -> list[str]:
    """Every difference between two MIDI files other than tempo values.

    Empty list = the files carry exactly the same notes, controllers, program
    changes, markers and timing; only Set Tempo values may differ.
    """
    import io
    import mido
    a = mido.MidiFile(file=io.BytesIO(old))
    b = mido.MidiFile(file=io.BytesIO(new))
    problems = []
    if (a.type, a.ticks_per_beat, len(a.tracks)) != (b.type, b.ticks_per_beat, len(b.tracks)):
        problems.append("file layout changed (type, resolution or track count)")
        return problems
    for ti, (ta, tb) in enumerate(zip(a.tracks, b.tracks)):
        if len(ta) != len(tb):
            problems.append(f"track {ti}: event count {len(ta)} -> {len(tb)}")
            continue
        for k, (ma, mb) in enumerate(zip(ta, tb)):
            if ma.type == "set_tempo" and mb.type == "set_tempo" and ma.time == mb.time:
                continue
            if ma != mb:
                problems.append(f"track {ti} event {k}: {ma!r} -> {mb!r}")
                if len(problems) > 20:
                    return problems
    return problems


# ─────────────────────────────────────────────────────────────────────────────
# mid2agb into a temporary folder
# ─────────────────────────────────────────────────────────────────────────────

def _no_window() -> dict:
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    return {"creationflags": flags} if flags else {}


def mid2agb_flags(project_root: str, label: str) -> Optional[list[str]]:
    from core.sound.song_compiler import _flags_for
    return _flags_for(project_root, label)


def render_s_with_mid2agb(project_root: str, label: str, mid_bytes: bytes,
                          flags: Optional[list[str]] = None) -> str:
    """Run the project's own mid2agb on *mid_bytes* in a temporary folder and
    return the ``.s`` text it produced (line endings normalised to LF).

    Never touches the project: the input and output live in a fresh temp
    directory that is removed afterwards, whatever happens. Raises
    SongSaveRefused with the reason if mid2agb is missing, the song has no
    midi.cfg line, or mid2agb fails.
    """
    from core.sound.song_compiler import find_mid2agb
    exe = find_mid2agb(project_root)
    if not exe:
        raise SongSaveRefused(
            "mid2agb could not be found (tools/mid2agb), so the change cannot "
            "be checked before saving")
    if flags is None:
        flags = mid2agb_flags(project_root, label)
    if flags is None:
        raise SongSaveRefused(f"{label}.mid has no line in midi.cfg")
    tmp = tempfile.mkdtemp(prefix="porysuite_song_")
    try:
        # The output file name decides every label mid2agb writes, so it must
        # be the song's own label.
        mid = os.path.join(tmp, label + ".mid")
        out = os.path.join(tmp, label + ".s")
        with open(mid, "wb") as fh:
            fh.write(mid_bytes)
        try:
            proc = subprocess.run([exe, mid, out] + list(flags),
                                  capture_output=True, text=True, timeout=60,
                                  **_no_window())
        except (OSError, subprocess.SubprocessError) as exc:
            raise SongSaveRefused(f"mid2agb could not be run: {exc}") from exc
        if proc.returncode != 0 or not os.path.isfile(out):
            msg = (proc.stderr or proc.stdout or "no output").strip()
            raise SongSaveRefused(f"mid2agb rejected the song: {msg}")
        with open(out, "rb") as fh:
            return fh.read().replace(b"\r\n", b"\n").decode("latin-1")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


# ─────────────────────────────────────────────────────────────────────────────
# What a song actually plays — for checking edits made in an editor
# ─────────────────────────────────────────────────────────────────────────────

# Plain-English names for each kind of musical information, used in messages.
SIGNATURE_GROUPS = {
    'NOTE': 'notes', 'TIE': 'notes', 'EOT': 'notes',
    'VOICE': 'instrument changes', 'VOL': 'volume', 'PAN': 'pan',
    'MOD': 'vibrato', 'LFOS': 'vibrato', 'LFODL': 'vibrato', 'MODT': 'vibrato',
    'BEND': 'pitch bend', 'BENDR': 'pitch bend', 'TUNE': 'tuning',
    'XCMD': 'echo', 'PRIO': 'priority', 'MEMACC': 'memory commands',
    'TEMPO': 'tempo', 'KEYSH': 'key shift',
}
# Values the engine starts a track with; an explicit one at tick 0 sounds the
# same as none (an editor may drop "PAN , c_v+0" without changing anything).
_DEFAULT_AT_START = {('PAN', 64), ('BEND', 64), ('MOD', 0), ('KEYSH', 0), ('TUNE', 64)}
# A track's opening value of these is its own group ("starting volume"), so an
# edit to how a track starts is told apart from a change in the middle.
_STARTING = frozenset(('VOICE', 'VOL', 'PAN', 'MOD', 'TEMPO'))


class _HeldEvent:
    """One NOTE / TIE / EOT at its PLAYED tick, for the pairing pass.

    The walk below works on an expanded stream, where a command inside a
    pattern is heard at a different tick each time the pattern plays, so the
    pairing cannot read the raw commands -- it needs the tick the note was
    actually heard at.
    """
    __slots__ = ('cmd', 'tick', 'pitch', 'velocity')

    def __init__(self, cmd, tick, pitch, velocity):
        self.cmd, self.tick, self.pitch, self.velocity = cmd, tick, pitch, velocity


def audible_signature(song, expanded=None) -> dict:
    """Everything the engine plays, per track, grouped by kind.

    Built on the same playback walk the MIDI exporter uses (or on *expanded*,
    an edited walk from ``midi_exporter.expand_song``): pattern calls are
    followed, so a repeat counts every time it is heard, at the time it is
    heard. Events after the loop's GOTO are left out (the engine never reaches
    them). Every note is compared as (start, pitch, velocity, audible length),
    so a long note written as TIE/EOT equals the same note written as one
    command. A volume is its raw level as the .s writes it, a tempo its BPM,
    and default values at the very start count as absent.

    Returns {track_index: {group: Counter(event_key)}} plus, per track, the
    'length' (loop end, or end of track) and the 'loop' (start, end).
    """
    from collections import Counter
    from core.sound.midi_exporter import expand_song, _raw_vol, _tempo_bpm, _clamp_velocity
    from core.sound.tie_pairing import tie_spans
    if expanded is None:
        expanded = expand_song(song)
    master = getattr(song, 'master_volume', None)
    tbs = max(1, int(getattr(song, 'tempo_base', 1) or 1))
    sig: dict = {}
    for ti, ex in enumerate(expanded):
        groups: dict = {}

        def put(group, key):
            groups.setdefault(group, Counter())[key] += 1

        held: list = []
        played = ex.events[:ex.played_count]
        for t, _seq, c in played:
            kind = c.cmd
            if kind == 'NOTE':
                # Not held, but it sets the running key a keyless EOT reads,
                # so the pairing pass has to see it too.
                held.append(_HeldEvent(kind, t, c.pitch, c.velocity))
                put('notes', (t, c.pitch, _clamp_velocity(c.velocity),
                              int(c.duration or 0) + int(c.gate_time or 0)))
            elif kind in ('TIE', 'EOT'):
                # Held notes are paired after the walk, by the one shared
                # rule in core.sound.tie_pairing. Pairing them here, in a
                # rule of this function's own, is what let the save check
                # and the editor read the same note as two different
                # lengths -- BUGS.md 2026-11-18.
                held.append(_HeldEvent(kind, t, c.pitch, c.velocity))
            elif kind in SIGNATURE_GROUPS:
                if kind == 'VOL':
                    value = _raw_vol(c, master)
                elif kind == 'TEMPO':
                    value = round(_tempo_bpm(c, tbs))
                else:
                    value = c.value
                if t == 0 and (kind, value) in _DEFAULT_AT_START:
                    continue
                group = SIGNATURE_GROUPS[kind]
                if t == 0 and kind in _STARTING:
                    group = 'starting ' + group
                put(group, (kind, t, value, getattr(c, 'sub', None), getattr(c, 'arg1', None)))
        end = ex.goto_time if ex.goto_time is not None else ex.end_time
        for t0, pitch, vel, t1 in tie_spans(held, end_tick=end):
            put('notes', (t0, pitch, _clamp_velocity(vel), t1 - t0))
        # mid2agb gives a track with no volume before its first note a full one
        # at the start (agb.cpp PrintAgbTrack: "VOL , 127*mvl/mxv"); that is
        # what the engine then plays, so it is what is compared.
        first_note = min((k[0] for k in groups.get('notes', ())), default=None)
        if first_note is not None and not any(
                c.cmd == 'VOL' and t <= first_note for t, _seq, c in played):
            put('starting volume', ('VOL', 0, 127, None, None))
        loop_start = ex.label_times.get(ex.goto_target) if ex.goto_target else None
        groups['length'] = end
        groups['loop'] = (loop_start, ex.goto_time)
        sig[ti] = groups
    return sig


def _signature_before(track_sig: dict, cutoff: int) -> dict:
    """One track's signature with only the events that start before *cutoff*;
    a note still sounding at *cutoff* is measured up to it (the loop's end
    cuts it there)."""
    from collections import Counter
    out = {}
    for group, value in track_sig.items():
        if not isinstance(value, Counter):
            out[group] = value
            continue
        if group == 'notes':                 # (t, pitch, velocity, length)
            kept = Counter()
            for k, n in value.items():
                if k[0] < cutoff:
                    kept[(k[0], k[1], k[2], min(k[3], cutoff - k[0]))] += n
            out[group] = kept
        else:                                # (kind, t, ...)
            out[group] = Counter({k: n for k, n in value.items() if k[1] < cutoff})
    return out


def signature_differences(before: dict, after: dict, ignore=(),
                          ignore_by_track: Optional[dict] = None) -> list[str]:
    """Plain-English list of what differs between two audible signatures.

    *ignore* names groups (e.g. 'notes') that the operation is allowed to
    change on every track; *ignore_by_track* adds groups per track index.
    """
    from collections import Counter
    out = []
    if len(before) != len(after):
        out.append(f"the number of tracks changes from {len(before)} to {len(after)}")
    for ti in sorted(set(before) | set(after)):
        a, b = before.get(ti, {}), after.get(ti, {})
        skip = set(ignore) | set((ignore_by_track or {}).get(ti, ()))
        for group in sorted(set(a) | set(b)):
            if group in skip:
                continue
            va, vb = a.get(group), b.get(group)
            if group in ('length', 'loop'):
                if va != vb:
                    out.append(f"track {ti + 1}: {group} changes ({va} -> {vb} ticks)")
                continue
            ca, cb = va or Counter(), vb or Counter()
            if ca != cb:
                lost = sum((ca - cb).values())
                added = sum((cb - ca).values())
                bits = []
                if lost:
                    bits.append(f"{lost} lost or moved")
                if added:
                    bits.append(f"{added} new or moved")
                line = f"track {ti + 1}: {group} — {', '.join(bits)}"
                detail = _examples(group, ca - cb, cb - ca)
                if detail:
                    line += f" ({detail})"
                out.append(line)
    return out


def _examples(group: str, lost, added, limit: int = 2) -> str:
    """A couple of concrete items behind a count.

    "2 lost or moved, 2 new or moved" says nothing a person can act on. This
    names the notes and, where the same note is on both sides, what actually
    differs about it. Diagnosing the 2026-11-18 mus_lab refusal needed the
    comparison monkeypatched in a scratch script to get at exactly this.
    """
    if group != 'notes':
        gone = sorted(lost)[:limit]
        came = sorted(added)[:limit]
        parts = [f"gone: {g}" for g in gone] + [f"new: {c}" for c in came]
        return "; ".join(parts)
    # (tick, pitch, velocity, length) — pair the two sides up by the note's
    # identity so a changed length reads as a change rather than as two
    # unrelated notes.
    by_head = {}
    for k in lost:
        by_head.setdefault((k[0], k[1]), [None, None])[0] = k
    for k in added:
        by_head.setdefault((k[0], k[1]), [None, None])[1] = k
    parts = []
    for (tick, pitch), (a, b) in sorted(by_head.items()):
        if a and b and a[3] != b[3]:
            parts.append(f"tick {tick} key {pitch}: length {a[3]} on screen, "
                         f"{b[3]} in the build")
        elif a and b:
            parts.append(f"tick {tick} key {pitch}: velocity {a[2]} on screen, "
                         f"{b[2]} in the build")
        elif a:
            parts.append(f"tick {tick} key {pitch}: on screen, not in the build")
        else:
            parts.append(f"tick {tick} key {pitch}: in the build, not on screen")
        if len(parts) >= limit:
            break
    return "; ".join(parts)


# ─────────────────────────────────────────────────────────────────────────────
# Comparing two .s files by what they assemble to
# ─────────────────────────────────────────────────────────────────────────────

_EQU_PREFIX = re.compile(r"^\s*\.equ\s+(\w+?)_grp\s*,", re.M)
TEMPO_LINE = re.compile(r"^\s*\.byte\s+TEMPO\s*,")


def semantic_lines(s_text: str, label: str) -> list[str]:
    """The lines of a song ``.s`` that matter to the assembler.

    Comments (``@ ...``) and blank lines are dropped; they carry no music.
    Internal names are normalised to *label*: two songs in this project are
    copies whose internal names still say ``deoxys`` while mid2agb names
    everything after the file — same bytes once assembled, different text.
    """
    text = s_text.replace("\r\n", "\n")
    for prefix in set(_EQU_PREFIX.findall(text)):
        if prefix != label:
            text = re.sub(r"\b" + re.escape(prefix) + r"(?=_)", label, text)
    out = []
    for line in text.split("\n"):
        line = line.split("@", 1)[0].rstrip()
        if line.strip():
            out.append(line)
    return out


@dataclass
class SDiff:
    same_length: bool
    changed: list[tuple[int, str, str]] = field(default_factory=list)

    def only(self, allowed: Callable[[str, str], bool]) -> bool:
        return self.same_length and all(allowed(a, b) for _, a, b in self.changed)

    def describe(self, limit: int = 6) -> str:
        if not self.same_length:
            return "the song's structure changed (different number of commands)"
        bits = [f"line {n}: '{a.strip()}' -> '{b.strip()}'" for n, a, b in self.changed[:limit]]
        more = len(self.changed) - limit
        return "; ".join(bits) + (f"; and {more} more" if more > 0 else "")


def compare_s(old_text: str, new_text: str, label: str) -> SDiff:
    a, b = semantic_lines(old_text, label), semantic_lines(new_text, label)
    d = SDiff(same_length=len(a) == len(b))
    for n, (x, y) in enumerate(zip(a, b), 1):
        if x != y:
            d.changed.append((n, x, y))
    return d


# ─────────────────────────────────────────────────────────────────────────────
# All-or-nothing commit of several files
# ─────────────────────────────────────────────────────────────────────────────

def _replace(src: str, dst: str, tries: int = 10, wait: float = 0.05) -> None:
    """os.replace, retried briefly on "access denied / in use". On Windows a
    file another program has open for a moment (a virus scanner reading the
    file just written, an indexer, a file watch) refuses replacement for that
    moment. A lasting refusal still raises, and the caller rolls back."""
    for attempt in range(tries):
        try:
            os.replace(src, dst)
            return
        except PermissionError:
            if attempt == tries - 1:
                raise
            time.sleep(wait)


def commit_files(writes: list[tuple[str, bytes]]) -> None:
    """Write every (path, bytes) pair, or none of them.

    Each new file is written in full to a temporary file beside its target
    first, so a failure while writing leaves every target untouched. The
    replacements then happen in order; if one fails, the files already
    replaced are put back from the copies read beforehand, and the error is
    raised. Temporary files are always removed.
    """
    originals: dict[str, Optional[bytes]] = {}
    temps: dict[str, str] = {}
    try:
        for path, data in writes:
            originals[path] = open(path, "rb").read() if os.path.isfile(path) else None
            tmp = f"{path}.porysuite-tmp"
            with open(tmp, "wb") as fh:
                fh.write(data)
                fh.flush()
                os.fsync(fh.fileno())
            temps[path] = tmp
        done: list[str] = []
        try:
            for path, _ in writes:
                _replace(temps[path], path)
                del temps[path]      # only once it has landed; else cleaned below
                done.append(path)
        except Exception:
            for path in reversed(done):
                try:
                    orig = originals[path]
                    if orig is None:
                        os.remove(path)
                    else:
                        with open(path, "wb") as fh:
                            fh.write(orig)
                except OSError as exc:
                    _log.error("Could not put %s back after a failed save: %s", path, exc)
            raise
    finally:
        for tmp in temps.values():
            try:
                os.remove(tmp)
            except OSError:
                pass


# ─────────────────────────────────────────────────────────────────────────────
# The tempo edit
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class TempoSaveResult:
    changed: bool
    old_bpm: int
    new_bpm: int
    tempo_events: int = 0
    message: str = ""


def save_tempo_change(project_root: str, label: str, new_bpm: int) -> TempoSaveResult:
    """Change a song's tempo by editing its ``.mid`` in place.

    Every tempo event is scaled by the same factor, so the first tempo becomes
    *new_bpm* and any later tempo changes keep their proportion (a slow-down
    stays a slow-down). Nothing else in the ``.mid`` changes, ``midi.cfg`` is
    not touched, and the ``.s`` is rebuilt by mid2agb exactly as the build
    would.

    Refuses — changing nothing — when:
      * the ``.s`` on disk is not what mid2agb makes from the ``.mid`` (it
        holds edits the ``.mid`` does not, which this save would discard);
      * the rebuilt ``.s`` differs from the old one anywhere except TEMPO lines;
      * mid2agb is missing or fails.
    A no-op (the tempo already shows as *new_bpm*) writes nothing at all.
    """
    md = os.path.join(project_root, MIDI_SUBDIR)
    mid_path = os.path.join(md, label + ".mid")
    s_path = os.path.join(md, label + ".s")
    if not os.path.isfile(mid_path):
        raise SongSaveRefused(f"{label} has no .mid file, so its tempo cannot be edited safely")
    if not os.path.isfile(s_path):
        raise SongSaveRefused(f"{label}.s is missing; build the project first")

    old_mid = open(mid_path, "rb").read()
    old_s = open(s_path, "rb").read().decode("latin-1")
    try:
        events = find_tempo_events(old_mid)
    except ValueError as exc:
        raise SongSaveRefused(f"{label}.mid could not be read: {exc}") from exc
    if not events:
        raise SongSaveRefused(f"{label}.mid has no tempo to change")
    first = min(events, key=lambda e: (e.abs_tick, e.track, e.payload_offset))
    old_bpm = displayed_bpm(first.usec_per_beat)

    new_mid, changes = scale_tempos(old_mid, int(new_bpm))
    if not changes:
        return TempoSaveResult(False, old_bpm, old_bpm, len(events),
                               "tempo unchanged — nothing written")

    problems = midi_differences_except_tempo(old_mid, new_mid)
    if problems or len(new_mid) != len(old_mid):
        raise SongSaveRefused("the tempo edit would have changed more than the tempo: "
                              + "; ".join(problems[:5]))

    flags = mid2agb_flags(project_root, label)
    baseline = render_s_with_mid2agb(project_root, label, old_mid, flags)
    base_diff = compare_s(old_s, baseline, label)
    if base_diff.changed or not base_diff.same_length:
        raise SongSaveRefused(
            f"{label}.s does not match what its .mid builds to, so it holds changes the "
            f".mid does not. Saving a tempo would rebuild the .s from the .mid and lose "
            f"them. Differences: {base_diff.describe()}")

    new_s = render_s_with_mid2agb(project_root, label, new_mid, flags)
    diff = compare_s(old_s, new_s, label)
    tempo_only = diff.only(lambda a, b: bool(TEMPO_LINE.match(a) and TEMPO_LINE.match(b)))
    first_tempo = next((l for l in semantic_lines(new_s, label) if TEMPO_LINE.match(l)), "")
    if not tempo_only:
        raise SongSaveRefused("after the tempo edit, mid2agb's output changed outside the "
                              "tempo lines: " + diff.describe())
    if not re.search(rf"TEMPO\s*,\s*{int(new_bpm)}\*", first_tempo):
        raise SongSaveRefused(f"the rebuilt song does not start at {new_bpm} BPM "
                              f"('{first_tempo.strip()}')")

    commit_files([(mid_path, new_mid), (s_path, new_s.encode("latin-1"))])
    # .s must stay newer than its .mid, or make would regenerate it needlessly.
    now = time.time()
    try:
        os.utime(mid_path, (now - 2, now - 2))
        os.utime(s_path, (now, now))
    except OSError:
        pass
    try:
        from core.sound.midi_ownership import record_own_write
        record_own_write(project_root, label)
    except Exception:
        pass
    _log.info("Tempo save %s: %d -> %d BPM (%d tempo event(s), %d .s line(s) changed)",
              label, old_bpm, new_bpm, len(changes), len(diff.changed))
    return TempoSaveResult(True, old_bpm, int(new_bpm), len(changes),
                           f"tempo {old_bpm} -> {new_bpm} BPM")


# ─────────────────────────────────────────────────────────────────────────────
# Songs whose .s is the only source (no .mid, or a placeholder one)
# ─────────────────────────────────────────────────────────────────────────────

def is_midi_sourced(project_root: str, label: str) -> bool:
    """True when the song's ``.mid`` holds real music, i.e. it is the build
    source and the ``.s`` is what mid2agb makes from it."""
    mid = os.path.join(project_root, MIDI_SUBDIR, label + ".mid")
    if not os.path.isfile(mid):
        return False
    from core.sound.midi_ownership import is_placeholder_mid
    return not is_placeholder_mid(mid)


_S_TEMPO = re.compile(r"(\.byte\s+TEMPO\s*,\s*)(\d+)(\s*\*\s*\w+_tbs\s*/\s*2)")


def retempo_s_text(s_text: str, new_first_bpm: int) -> tuple[str, int]:
    """Scale every ``TEMPO , N*x_tbs/2`` line of an ``.s`` so the first becomes
    *new_first_bpm* and the rest keep their proportion. Only the numbers on
    those lines change. Returns (new_text, number_of_lines_changed)."""
    found = list(_S_TEMPO.finditer(s_text))
    if not found:
        raise SongSaveRefused("this song has no tempo command to change")
    base = int(found[0].group(2))
    if base == new_first_bpm:
        return s_text, 0
    ratio = new_first_bpm / base
    pieces, last, n = [], 0, 0
    for k, m in enumerate(found):
        new = new_first_bpm if k == 0 else max(1, int(round(int(m.group(2)) * ratio)))
        pieces.append(s_text[last:m.start(2)])
        pieces.append(str(new))
        last = m.end(2)
        n += int(new) != int(m.group(2))
    pieces.append(s_text[last:])
    return "".join(pieces), n


def _commit_s_only(project_root: str, label: str, old_s: str, new_s: str,
                   allowed: Callable[[str, str], bool], what: str) -> None:
    diff = compare_s(old_s, new_s, label)
    if not diff.only(allowed):
        raise SongSaveRefused(f"the {what} edit would have changed more than the {what}: "
                              + diff.describe())
    md = os.path.join(project_root, MIDI_SUBDIR)
    s_path = os.path.join(md, label + ".s")
    commit_files([(s_path, new_s.encode("latin-1"))])
    # The .s is this song's source: keep make from regenerating it from the
    # placeholder beside it (song_writer.lock_song_timestamps only moves the
    # .mid if it IS a placeholder).
    try:
        from core.sound.song_writer import lock_song_timestamps
        lock_song_timestamps(s_path, os.path.join(md, label + ".mid"),
                             os.path.join(md, "midi.cfg"))
    except Exception as exc:
        _log.warning("Could not lock timestamps for %s: %s", label, exc)


def save_song_tempo(project_root: str, label: str, new_bpm: int) -> TempoSaveResult:
    """The Sound Editor's tempo save, for either kind of song."""
    if is_midi_sourced(project_root, label):
        return save_tempo_change(project_root, label, new_bpm)
    s_path = os.path.join(project_root, MIDI_SUBDIR, label + ".s")
    if not os.path.isfile(s_path):
        raise SongSaveRefused(f"{label}.s is missing")
    old_s = open(s_path, "rb").read().decode("latin-1")
    new_s, n = retempo_s_text(old_s, int(new_bpm))
    old_bpm = int(_S_TEMPO.search(old_s).group(2))
    if not n:
        return TempoSaveResult(False, old_bpm, old_bpm, 0, "tempo unchanged — nothing written")
    _commit_s_only(project_root, label, old_s, new_s,
                   lambda a, b: bool(TEMPO_LINE.match(a) and TEMPO_LINE.match(b)), "tempo")
    _log.info("Tempo save (.s-sourced) %s: %d -> %d BPM", label, old_bpm, new_bpm)
    return TempoSaveResult(True, old_bpm, int(new_bpm), n, f"tempo {old_bpm} -> {new_bpm} BPM")


# ─────────────────────────────────────────────────────────────────────────────
# Header flags: volume / priority / reverb / voicegroup
# ─────────────────────────────────────────────────────────────────────────────

HEADER_EQU = re.compile(r"^\s*\.equ\s+\w+_(grp|pri|rev|mvl)\s*,")


def _cfg_path(project_root: str) -> str:
    return os.path.join(project_root, MIDI_SUBDIR, "midi.cfg")


def _equ_value(kind: str, value: int) -> str:
    if kind == "grp":
        return f"voicegroup{value:03d}"
    if kind == "rev":
        return f"reverb_set+{value}" if value and value > 0 else "0"
    return str(value)


def set_s_header(s_text: str, *, priority=None, reverb=None, volume=None,
                 voicegroup=None) -> str:
    """Rewrite only the ``.equ <label>_pri/_rev/_mvl/_grp`` lines of an ``.s``,
    in mid2agb's own format. ``None`` leaves that line alone."""
    wanted = {"pri": priority, "rev": reverb, "mvl": volume, "grp": voicegroup}

    def sub(m):
        kind = m.group(2)
        val = wanted.get(kind)
        if val is None:
            return m.group(0)
        return f"{m.group(1)}{_equ_value(kind, int(val))}"
    return re.sub(r"(^\s*\.equ\s+\w+_(grp|pri|rev|mvl)\s*,\s*)([^\n@]*?)(?=\s*(?:@|$))",
                  sub, s_text, flags=re.M)


def save_song_header(project_root: str, label: str, *, priority=None, reverb=None,
                     volume=None, voicegroup=None) -> bool:
    """Save a header edit. Returns False when nothing needed to change.

    For a MIDI-sourced song these values are mid2agb flags: midi.cfg's line is
    updated and the ``.s`` is rebuilt by mid2agb from the UNCHANGED ``.mid``.
    Before writing, the old ``.s`` must match what the old flags build to (so no
    ``.s``-only work is discarded) and the new ``.s`` may differ only on its
    header ``.equ`` lines. Both files are written together or not at all.
    """
    cfg_path = _cfg_path(project_root)
    md = os.path.join(project_root, MIDI_SUBDIR)
    s_path = os.path.join(md, label + ".s")
    if not os.path.isfile(s_path):
        raise SongSaveRefused(f"{label}.s is missing; build the project first")
    old_s = open(s_path, "rb").read().decode("latin-1")
    cfg_text = open(cfg_path, "rb").read().decode("utf-8") if os.path.isfile(cfg_path) else ""

    from core.sound.song_table_manager import midi_cfg_with_flags
    new_cfg, old_flags, new_flags = midi_cfg_with_flags(
        cfg_text, label, priority=priority, reverb=reverb, volume=volume, voicegroup=voicegroup)

    if not is_midi_sourced(project_root, label):
        new_s = set_s_header(old_s, priority=priority, reverb=reverb, volume=volume,
                             voicegroup=voicegroup)
        cfg_changed = old_flags is not None and new_cfg != cfg_text
        if new_s == old_s and not cfg_changed:
            return False
        if new_s != old_s:
            _commit_s_only(project_root, label, old_s, new_s,
                           lambda a, b: bool(HEADER_EQU.match(a) and HEADER_EQU.match(b)), "header")
        if cfg_changed:
            commit_files([(cfg_path, new_cfg.encode("utf-8"))])
            from core.sound.midi_cfg_freshness import settle_for_cfg_path
            settle_for_cfg_path(cfg_path)
        return True

    if old_flags is None:
        raise SongSaveRefused(f"{label}.mid has no line in midi.cfg")
    if new_cfg == cfg_text:
        return False
    mid_bytes = open(os.path.join(md, label + ".mid"), "rb").read()
    baseline = render_s_with_mid2agb(project_root, label, mid_bytes, old_flags)
    base_diff = compare_s(old_s, baseline, label)
    if base_diff.changed or not base_diff.same_length:
        raise SongSaveRefused(
            f"{label}.s does not match what its .mid builds to, so it holds changes the "
            f".mid does not. Saving would rebuild the .s and lose them. Differences: "
            f"{base_diff.describe()}")
    new_s = render_s_with_mid2agb(project_root, label, mid_bytes, new_flags)
    diff = compare_s(old_s, new_s, label)
    if not diff.only(lambda a, b: bool(HEADER_EQU.match(a) and HEADER_EQU.match(b))):
        raise SongSaveRefused("the header edit would have changed more than the header: "
                              + diff.describe())
    commit_files([(s_path, new_s.encode("latin-1")), (cfg_path, new_cfg.encode("utf-8"))])
    from core.sound.midi_cfg_freshness import settle_for_cfg_path
    settle_for_cfg_path(cfg_path)
    try:
        os.utime(s_path, None)
    except OSError:
        pass
    _log.info("Header save %s: %s -> %s", label, " ".join(old_flags), " ".join(new_flags))
    return True


# ─────────────────────────────────────────────────────────────────────────────
# Editor saves (the Piano Roll)
# ─────────────────────────────────────────────────────────────────────────────

# Which commands an editor edits the value of lives with the parser, beside
# the identity that is numbered from it -- the two have to agree, or a
# command gets an identity the matching below never looks for.
from core.sound.song_parser import is_value_command


def apply_command_edits(base_track, live_track) -> set:
    """Replay the value edits an editor made to *live_track*'s commands onto
    *base_track* (the same track freshly read from disk), in place.

    Notes, waits and structure are not touched here (the editor's notes come
    from its canvas; the timing and structure come from the file). Every other
    command is matched to its counterpart by ``origin`` — where it came from in
    the file, the same on every read of the same file. A changed value is
    copied, a command the editor removed is removed, and one it created (no
    origin) is placed just before the next command that follows it in the
    editor. Returns the command kinds that changed.

    Refuses when the editor's song does not come from the file as it is now
    (it was changed on disk after the editor opened it): matching it up would
    be guessing.

    "The file as it is now" means its MUSIC, not its layout. Commands are
    matched by (track, ordinal among that track's value commands), so a .s
    rebuilt from the same .mid -- or one that Song Build Protection has added
    its header line to -- still lines up and still saves. A command added to
    or removed from the file shifts the ordinals after it, the values stop
    matching, and the save is refused as it should be.
    """
    import copy

    def relevant(cmds):
        return [c for c in cmds if is_value_command(c.cmd)]

    base_ns = relevant(base_track.commands)
    live_ns = relevant(live_track.commands)
    by_origin = {c.origin: c for c in base_ns if getattr(c, 'origin', None) is not None}
    stale = SongSaveRefused(
        f"{live_track.label} no longer matches its file — the song was changed on disk "
        f"after it was opened here. Reopen it and make the edit again.")
    changed: set = set()
    kept: set = set()
    inserts: list = []   # (base command it goes before, or None for the end; new command)
    pending: list = []
    for lc in live_ns:
        origin = getattr(lc, 'origin', None)
        if origin is None:
            pending.append(copy.copy(lc))
            changed.add(lc.cmd)
            continue
        bc = by_origin.get(origin)
        if bc is None or bc.cmd != lc.cmd or bc.parsed_value != lc.parsed_value:
            raise stale
        kept.add(id(bc))
        inserts.extend((bc, p) for p in pending)
        pending = []
        if lc.value != bc.value:
            bc.value = lc.value
            changed.add(bc.cmd)
    inserts.extend((None, p) for p in pending)
    removed = {id(c) for c in base_ns if id(c) not in kept}
    changed.update(c.cmd for c in base_ns if id(c) in removed)
    if not removed and not inserts:
        return changed
    rebuilt = []
    for c in base_track.commands:
        rebuilt.extend(n for a, n in inserts if a is c)
        if id(c) not in removed:
            rebuilt.append(c)
    tail = [n for a, n in inserts if a is None]
    if tail:
        # Before the track's closing GOTO / FINE, so the engine still plays them.
        end = len(rebuilt)
        while end > 0 and rebuilt[end - 1].cmd in ('GOTO', 'FINE'):
            end -= 1
        rebuilt[end:end] = tail
    base_track.commands = rebuilt
    return changed


def midi_export_would_lose(original: bytes, exported: bytes) -> list:
    """What replacing *original* with *exported* would drop, in plain words:
    events of a kind the export has fewer of (by type / controller / meta
    kind), and changed track names. Empty = nothing is lost."""
    import io
    from collections import Counter
    import mido

    def census(data):
        m = mido.MidiFile(file=io.BytesIO(data))
        c = Counter()
        names = []
        for t in m.tracks:
            for e in t:
                if e.type == 'control_change':
                    c[f"controller {e.control}"] += 1
                elif e.type == 'track_name':
                    names.append(e.name)
                elif e.type != 'end_of_track':
                    c[e.type] += 1
        return c, names, len(m.tracks)
    a, names_a, tracks_a = census(original)
    b, names_b, tracks_b = census(exported)
    out = [f"{a[k] - b.get(k, 0)} × {k}" for k in sorted(a) if a[k] > b.get(k, 0)]
    if names_a != names_b:
        out.append("the track names")
    if tracks_a > tracks_b:
        out.append(f"{tracks_a - tracks_b} MIDI track(s)")
    return out


def song_makes_patterns(project_root: str, label: str):
    """Whether the build makes patterns of the song's repeats: True, False
    (its midi.cfg line has -N), or None when the song has no midi.cfg line."""
    from core.sound.song_table_manager import midi_cfg_with_flags
    cfg_path = _cfg_path(project_root)
    if not os.path.isfile(cfg_path):
        return None
    cfg_text = open(cfg_path, "rb").read().decode("utf-8")
    _new, flags, _ = midi_cfg_with_flags(cfg_text, label)
    if flags is None:
        return None
    return not any(f.upper() == '-N' for f in flags)


def save_editor_song(project_root: str, live_song, edit, *, voicegroup=None,
                     patterns=None) -> str:
    """Save an editor's version of a song — validated, all files or none.

    *live_song* is the editor's in-memory song (its header and its non-note
    command values carry the editor's edits); *edit* is a
    ``midi_exporter.SongEdit`` with what the editor changed on its canvas
    (notes, drawn controls, the loop). The song is re-read from disk and only
    those edits are applied to it, on the playback clock — so instrument
    changes, effects, tempo changes and everything else the editor did not
    touch stay exactly as they were.

    Refuses, writing nothing, when the build (mid2agb, run in a temp folder)
    would not play exactly what the editor holds, or would change anything the
    edit did not. Returns the .s path on success.

    *patterns* (True / False / None = leave it) is whether the build makes
    patterns of the song's repeats (midi.cfg's -N). It changes how the song
    is stored, never what plays, so the checks below hold it to that.
    """
    import copy
    import tempfile
    from core.sound.song_parser import parse_song_file
    from core.sound.midi_exporter import expand_song, song_to_midi
    from core.sound.song_table_manager import midi_cfg_with_flags
    from core.sound.midi_cfg_freshness import settle_for_cfg_path

    label = live_song.label
    md = os.path.join(project_root, MIDI_SUBDIR)
    s_path = os.path.join(md, label + ".s")
    mid_path = os.path.join(md, label + ".mid")
    cfg_path = _cfg_path(project_root)
    if not os.path.isfile(s_path):
        raise SongSaveRefused(f"{label}.s is missing; build the project first")

    pristine = parse_song_file(s_path)
    # Replaying the editor's edits needs every command's `origin`. A PorySuite
    # that was running when it was updated still holds the old song reader,
    # whose commands have no such field: matching would be guessing, so stop.
    for song in (live_song, pristine):
        probe = next((c for t in song.tracks for c in t.commands), None)
        if probe is not None and not hasattr(probe, 'origin'):
            raise SongSaveRefused(
                "PorySuite was updated while it was running, and this song was read by "
                "the older version. Restart PorySuite, reopen the song and make the edit "
                "again.")
    edited = copy.deepcopy(pristine)

    # Line the editor's tracks up with the file's (add / remove / duplicate
    # keep labels; a track the file does not have is new).
    by_label: dict = {}
    for bi, bt in enumerate(edited.tracks):
        by_label.setdefault(bt.label, []).append(bi)
    # A track the editor added but never put a note on is left out: mid2agb
    # writes no track for a MIDI channel without notes, so keeping it would
    # shift every later track by one and the checks below would (rightly)
    # refuse the save. The editor's edit is renumbered to match.
    edit_tracks = getattr(edit, 'tracks', None) or {}
    renumbered = {}
    tracks, mapped, changed = [], {}, {}
    used: set = set()
    for li, lt in enumerate(live_song.tracks):
        hits = [bi for bi in by_label.get(lt.label, []) if bi not in used]
        te = edit_tracks.get(li)
        ni = len(tracks)
        if hits:
            bi = hits[0]
            used.add(bi)
            changed[ni] = apply_command_edits(edited.tracks[bi], lt)
            mapped[ni] = bi
            tracks.append(edited.tracks[bi])
        else:
            if te is not None and te.notes is not None:
                has_notes = bool(te.notes)
            else:
                has_notes = any(c.cmd in ('NOTE', 'TIE') for c in lt.commands)
            if not has_notes:
                _log.info("Editor save %s: new track %s has no notes — not written",
                          label, lt.label)
                continue
            tracks.append(copy.deepcopy(lt))
        if te is not None:
            renumbered[ni] = te
    if edit is not None:
        # Every other part of the edit (loop, time signatures, ...) is kept.
        import dataclasses
        edit = dataclasses.replace(edit, tracks=renumbered)
    for i, t in enumerate(tracks):
        t.index = i
    edited.tracks = tracks
    edited.num_tracks = len(tracks)
    for attr in ('master_volume', 'priority', 'reverb', 'voicegroup'):
        if hasattr(live_song, attr):
            setattr(edited, attr, getattr(live_song, attr))

    expected = expand_song(edited, edit)
    cfg_text = open(cfg_path, "rb").read().decode("utf-8") if os.path.isfile(cfg_path) else ""
    new_cfg, old_flags, new_flags = midi_cfg_with_flags(
        cfg_text, label, priority=getattr(edited, 'priority', None),
        reverb=getattr(edited, 'reverb', None), volume=getattr(edited, 'master_volume', None),
        voicegroup=voicegroup, patterns=patterns)
    if old_flags is None:
        raise SongSaveRefused(f"{label}.mid has no line in midi.cfg")
    import io
    buf = io.BytesIO()
    song_to_midi(edited, edit).save(file=buf)
    exported = buf.getvalue()
    # Prefer editing the existing .mid in place: it keeps everything the .s
    # does not hold (track names, time signatures, controllers mid2agb
    # ignores, a DAW's own events). A full export is only used when the song
    # has no real .mid, or when it would lose nothing from the one there.
    mid_bytes = exported
    if is_midi_sourced(project_root, label):
        from core.sound.midi_patch import patch_midi, MidiPatchUnavailable
        original_mid = open(mid_path, "rb").read()
        # The editor shows the .s. If the .mid was changed since that .s was
        # built (PoryDAW writes only the .mid), the .s no longer describes it
        # and an edit measured against the .s cannot be placed in the .mid.
        disk_s = open(s_path, "rb").read().decode("latin-1")
        built = render_s_with_mid2agb(project_root, label, original_mid, old_flags)
        stale = compare_s(disk_s, built, label)
        if stale.changed or not stale.same_length:
            raise SongSaveRefused(
                f"{label}.mid was changed after {label}.s was built (another editor such "
                f"as PoryDAW writes only the .mid), so what this editor shows is out of "
                f"date. Use Refresh from MIDI in the Sound Editor (or build the project), "
                f"reopen the song and make the edit again. "
                f"Differences: {stale.describe(3)}")
        try:
            mid_bytes = patch_midi(original_mid, pristine, edited, edit, mapped)
        except MidiPatchUnavailable as exc:
            # A full export is only safe for a .mid PorySuite itself wrote last
            # (the MIDI ledger knows its digest): anything else may be a DAW's
            # file, and a re-export loses what only the DAW reads — exact
            # velocities, names, unknown events.
            from core.sound.midi_ownership import is_external_edit
            if is_external_edit(project_root, label, when_unknown=True):
                lost = midi_export_would_lose(original_mid, exported)
                raise SongSaveRefused(
                    f"the edit cannot be placed in the existing {label}.mid ({exc}). That "
                    f"file was not written by PorySuite, so replacing it could lose another "
                    f"editor's data" + (f" — at least: {'; '.join(lost[:6])}" if lost else "")
                    + ". Nothing was written.") from exc
            _log.info("Editor save %s: no in-place edit (%s); the .mid is PorySuite's own, "
                      "so it is re-exported", label, exc)

    new_s = render_s_with_mid2agb(project_root, label, mid_bytes, new_flags)

    tmp = tempfile.mkdtemp(prefix="porysuite_check_")
    try:
        p = os.path.join(tmp, label + ".s")
        with open(p, "w", encoding="latin-1", newline="\n") as fh:
            fh.write(new_s)
        result = parse_song_file(p)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    # 1. The build must play exactly what the editor holds.
    mismatch = signature_differences(audible_signature(edited, expected),
                                     audible_signature(result))
    if mismatch:
        raise SongSaveRefused(
            "the built song would not play what the editor shows: " + "; ".join(mismatch[:6]))

    # 2. Nothing the edit did not touch may change.
    before = audible_signature(pristine)
    after = audible_signature(result)
    b_m, a_m, allowed = {}, {}, {}
    edits = getattr(edit, 'tracks', None) or {}
    new_loop = getattr(edit, 'loop', None)
    for li, bi in mapped.items():
        b_m[li], a_m[li] = before[bi], after[li]
        if new_loop is not None:
            # Moving the loop changes what plays after its end — that is the
            # edit. Everything before the earlier of the two ends must match.
            ends = [e for e in (before[bi]['length'], new_loop[1]) if e is not None]
            cutoff = min(ends) if ends else None
            if cutoff is not None:
                b_m[li] = _signature_before(before[bi], cutoff)
                a_m[li] = _signature_before(after[li], cutoff)
        skip: set = set()
        te = edits.get(li)
        if te is not None and te.notes is not None:
            skip |= {'notes', 'length'}
        if te is not None and te.controls is not None:
            # A controls edit may change any editable control's mid-song events.
            from core.sound.midi_exporter import EDITABLE_CONTROLS
            skip |= {SIGNATURE_GROUPS[k] for k in EDITABLE_CONTROLS}
        for kind in changed.get(li, ()):
            group = SIGNATURE_GROUPS.get(kind)
            if group:
                skip |= {group, 'starting ' + group}
        if getattr(edit, 'loop', None) is not None:
            skip |= {'loop', 'length'}
        allowed[li] = skip
    unintended = signature_differences(b_m, a_m, ignore_by_track=allowed)
    if unintended:
        raise SongSaveRefused(
            "the save would also change things that were not edited: "
            + "; ".join(unintended[:6]))

    # Nothing to change: the build would give back the .s already on disk. Then
    # nothing is written — not even the .mid, which may be a DAW's file and
    # carry things only the DAW reads.
    # A time-signature edit only moves bar comments in the .s, which the
    # comparison ignores — it is an edit all the same, so it is written.
    old_s = open(s_path, "rb").read().decode("latin-1")
    same = compare_s(old_s, new_s, label)
    bars_edited = getattr(edit, 'time_signatures', None) is not None and old_s != new_s
    if (new_cfg == cfg_text and same.same_length and not same.changed and not bars_edited
            and os.path.isfile(mid_path)):
        _log.info("Editor save %s: nothing changed — no file written", label)
        return s_path

    writes = [(mid_path, mid_bytes), (s_path, new_s.encode("latin-1"))]
    if new_cfg != cfg_text:
        writes.append((cfg_path, new_cfg.encode("utf-8")))
    commit_files(writes)
    now = time.time()
    try:
        os.utime(mid_path, (now - 2, now - 2))
        os.utime(s_path, (now, now))
    except OSError:
        pass
    if new_cfg != cfg_text:
        settle_for_cfg_path(cfg_path)
    try:
        from core.sound.midi_ownership import record_own_write
        record_own_write(project_root, label)
    except Exception:
        pass
    _log.info("Editor save %s: %d track(s), %d edited", label, len(tracks), len(edits))
    return s_path
