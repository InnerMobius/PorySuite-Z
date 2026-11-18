"""Which of a song's two files holds its newest music — decided by content.

A song has two files: ``<song>.mid`` and ``<song>.s``. The game build turns the
``.mid`` into the ``.s`` (mid2agb) whenever ``make`` thinks the ``.mid`` (or the
song's settings) is newer, and judges "newer" by file times alone. Git sets
those times in the order it writes files, not in the order anybody wrote the
music, so for most of 2026 PorySuite guessed with timestamps, git tracking and a
per-machine record of its own writes — and lost or hid music each time the
guess was wrong (forensic report, 2026-11-15: scenarios D, K, K2).

This module never guesses from times, tracking or missing records. For each
song it asks what the files PLAY:

* **IN_SYNC** — the ``.s`` plays exactly what the build makes from the ``.mid``
  with the song's midi.cfg settings. Nothing can be lost either way.
* **MIDI_AHEAD** — the ``.s`` plays what the build made from an EARLIER
  ``.mid`` (or earlier settings), found in git history or in PorySuite's record
  of a time the two matched. The ``.s`` holds nothing that version did not, so
  the build may — and must — rebuild it from the current ``.mid``.
* **S_AHEAD** — the ``.s`` holds music no known ``.mid`` produces, and the
  ``.mid`` and settings are exactly as they were when the two last matched.
  The ``.s`` is the newer work and is kept.
* **S_AUTHORED** — a song written in PorySuite whose ``.mid`` is an empty
  stand-in (or was rendered from the ``.s`` by the integrity sweep): the ``.s``
  is its only real copy and is kept.
* **CONFLICT** — the ``.s`` holds music no known ``.mid`` produces AND the
  ``.mid`` or settings changed since the two last matched. Both sides hold
  unique work. Nothing is touched; the build is refused until somebody chooses.
* **UNKNOWN** — the ``.s`` holds music no known ``.mid`` produces, and there is
  no record of the two ever matching (no git history, an untracked ``.s`` that
  matches no committed ``.mid``), or the build's converter rejects the ``.mid``.
  Provenance cannot be established, so this is treated like a conflict, never
  as "the ``.mid`` wins" or "the ``.s`` wins".

``apply_plan`` then makes ``make`` do what the content decided: the ``.s`` of a
MIDI_AHEAD song is dated just before its ``.mid`` (rebuilt); a kept ``.s`` is
dated now and copied to a backup first. ``verify_after_build`` checks afterwards
that every kept song is unchanged and every MIDI_AHEAD song really was rebuilt —
a successful ROM build is not taken as proof that music survived.

A song whose ``.s`` carries a record line (song build protection,
``core/sound/song_guard.py``) is classified from that line first: it travels
with the ``.s`` and answers on any computer, with no git history or cache.

What this module alone cannot do: stop a terminal ``make``, ``make clean``, or
a midi.cfg rewrite under pret's rule from acting on timestamps while PorySuite
is not involved. That needs the project's own build — the opt-in song build
protection (Tools → Song Build Protection), which, when installed, makes
``apply_plan`` write record lines instead of moving any file time.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import shutil
import subprocess
import tempfile
import threading
import time
from dataclasses import dataclass, field
from typing import Callable, Iterable, Optional

_log = logging.getLogger("SoundEditor.SongSync")

MIDI_SUBDIR = os.path.join("sound", "songs", "midi")
HISTORY_LIMIT = 30          # commits per song searched for a time the two files matched

IN_SYNC = "in_sync"
MIDI_AHEAD = "midi_ahead"
S_AHEAD = "s_ahead"
S_AUTHORED = "s_authored"
CONFLICT = "conflict"
UNKNOWN = "unknown"
KEPT_STATES = (S_AHEAD, S_AUTHORED)
BLOCKING_STATES = (CONFLICT, UNKNOWN)


@dataclass
class SongState:
    label: str
    state: str
    reason: str
    s_key: str = ""          # the .s as classified (text key), for the after-build check
    mid_digest: str = ""


@dataclass
class SyncPlan:
    project_root: str
    mode: str = ""
    songs: dict = field(default_factory=dict)            # label -> SongState
    unchecked: list = field(default_factory=list)        # (label, why)
    note: str = ""                                       # why checking was limited, if it was
    protected: bool = False                              # song build protection installed

    def of(self, *states) -> list:
        return [s for s in self.songs.values() if s.state in states]

    @property
    def blocking(self) -> list:
        return self.of(*BLOCKING_STATES)

    def summary(self) -> str:
        counts = {}
        for s in self.songs.values():
            counts[s.state] = counts.get(s.state, 0) + 1
        order = (IN_SYNC, MIDI_AHEAD, S_AHEAD, S_AUTHORED, CONFLICT, UNKNOWN)
        bits = [f"{counts[k]} {k.replace('_', ' ')}" for k in order if counts.get(k)]
        if self.unchecked:
            bits.append(f"{len(self.unchecked)} not checked")
        return ", ".join(bits) or "no songs"


@dataclass
class ApplyReport:
    rebuild: list = field(default_factory=list)           # MIDI_AHEAD songs sent to the build
    kept: dict = field(default_factory=dict)              # label -> s_key of each kept .s
    expected: dict = field(default_factory=dict)          # label -> text key the rebuild must produce
    backup_dir: str = ""
    errors: list = field(default_factory=list)
    stamped: int = 0                                      # record lines written (protected projects)


# ─────────────────────────────────────────────────────────────────────────────
# Small helpers
# ─────────────────────────────────────────────────────────────────────────────

def _no_window() -> dict:
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    return {"creationflags": flags} if flags else {}


def _sha1(data: bytes) -> str:
    return hashlib.sha1(data).hexdigest()


def _read(path: str) -> Optional[bytes]:
    try:
        with open(path, "rb") as fh:
            return fh.read()
    except OSError:
        return None


def _is_placeholder_bytes(data: Optional[bytes]) -> bool:
    from core.sound.midi_ownership import PLACEHOLDER_MAX_BYTES
    if data is None or len(data) > PLACEHOLDER_MAX_BYTES:
        return False
    return True


def text_key(s_text: str, label: str) -> str:
    """What the assembler sees of an .s (comments, blank lines, line endings and
    internal-name copies ignored), as a digest."""
    from core.sound.safe_song_save import semantic_lines
    return _sha1("\n".join(semantic_lines(s_text, label)).encode("latin-1", "replace"))


def music_key(s_text: str, label: str) -> Optional[str]:
    """What an .s PLAYS — its header settings and, per track, every note and
    control as the engine hears it — as a digest. Two .s files that play the
    same song (one flattened, one using repeats; a long note as one command or
    as TIE/EOT) get the same key. None when the .s cannot be read as a song."""
    from collections import Counter
    from core.sound.midi_cfg_freshness import header_of
    from core.sound.safe_song_save import audible_signature
    from core.sound.song_parser import parse_song_file
    tmp = tempfile.mkdtemp(prefix="porysuite_sync_")
    try:
        path = os.path.join(tmp, label + ".s")
        with open(path, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(s_text.replace("\r\n", "\n"))
        song = parse_song_file(path)
        sig = audible_signature(song)
    except Exception as exc:
        _log.debug("music_key(%s) failed: %s", label, exc)
        return None
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    header = sorted(header_of(s_text).items())
    tracks = []
    for ti in sorted(sig):
        groups = []
        for g, v in sorted(sig[ti].items()):
            if isinstance(v, Counter):
                groups.append((g, sorted(repr(k) for k in v.elements())))
            else:
                groups.append((g, repr(v)))
        tracks.append((ti, groups))
    return _sha1(repr((header, tracks)).encode("utf-8"))


# ─────────────────────────────────────────────────────────────────────────────
# Renders (mid2agb in a temp folder), cached per project
# ─────────────────────────────────────────────────────────────────────────────

class _Renderer:
    """Runs the project's own mid2agb, never in the project. Remembers each
    result's text key on disk (PorySuite's cache, not the game tree), so only a
    song whose .mid or settings changed is converted again."""

    def __init__(self, project_root: str):
        from core.sound.song_compiler import find_mid2agb
        self.root = project_root
        self.exe = find_mid2agb(project_root)
        fp = ""
        if self.exe:
            try:
                st = os.stat(self.exe)
                fp = f"{st.st_size}:{int(st.st_mtime)}"
            except OSError:
                pass
        self._fp = fp
        self._lock = threading.Lock()
        self._texts: dict = {}
        self._path = ""
        self._cache: dict = {}
        try:
            from core.app_info import get_cache_dir
            self._path = os.path.join(get_cache_dir(project_root), "song_sync_renders.json")
            with open(self._path, encoding="utf-8") as fh:
                data = json.load(fh)
            if isinstance(data, dict):
                self._cache = data
        except Exception:
            self._cache = {}

    def _key(self, label: str, mid_bytes: bytes, flags) -> str:
        return _sha1(f"{label}|{' '.join(flags)}|{_sha1(mid_bytes)}|{self._fp}".encode())

    def text(self, label: str, mid_bytes: bytes, flags) -> str:
        """The .s the build makes. Raises SongSaveRefused with mid2agb's reason."""
        from core.sound.safe_song_save import render_s_with_mid2agb
        k = self._key(label, mid_bytes, flags)
        with self._lock:
            if k in self._texts:
                return self._texts[k]
        out = render_s_with_mid2agb(self.root, label, mid_bytes, list(flags))
        with self._lock:
            self._texts[k] = out
            self._cache.setdefault(k, {})["t"] = text_key(out, label)
        return out

    def keys(self, label: str, mid_bytes: bytes, flags, *, music: bool):
        """(text key, music key or None) of what the build makes."""
        k = self._key(label, mid_bytes, flags)
        with self._lock:
            hit = dict(self._cache.get(k, {}))
        if "t" not in hit or (music and "m" not in hit):
            out = self.text(label, mid_bytes, flags)
            hit["t"] = text_key(out, label)
            if music:
                hit["m"] = music_key(out, label)
            with self._lock:
                self._cache[k] = hit
        return hit["t"], hit.get("m")

    def save(self) -> None:
        if not self._path:
            return
        try:
            with self._lock:
                data = dict(self._cache)
            tmp = self._path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as fh:
                json.dump(data, fh)
            os.replace(tmp, self._path)
        except OSError as exc:
            _log.debug("Could not write the render cache: %s", exc)


# ─────────────────────────────────────────────────────────────────────────────
# Positive evidence of a time the two files matched
# ─────────────────────────────────────────────────────────────────────────────

def _record_path(project_root: str) -> str:
    try:
        from core.app_info import get_cache_dir
        return os.path.join(get_cache_dir(project_root), "song_sync_record.json")
    except Exception:
        return ""


def _load_record(project_root: str) -> dict:
    p = _record_path(project_root)
    try:
        with open(p, encoding="utf-8") as fh:
            data = json.load(fh)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _save_record(project_root: str, record: dict) -> None:
    p = _record_path(project_root)
    if not p:
        return
    try:
        tmp = p + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(record, fh, indent=1, sort_keys=True)
        os.replace(tmp, p)
    except OSError as exc:
        _log.debug("Could not write the song sync record: %s", exc)


class _Git:
    """Read-only git access for one project: what each song's files held in
    earlier commits. Every call is optional — a project that is not a git
    repository simply has no history."""

    def __init__(self, project_root: str):
        self.root = project_root
        self.prefix: Optional[str] = None
        self.tracked: set = set()
        r = self._run("rev-parse", "--show-prefix")
        if r is None:
            return
        self.prefix = r.decode("utf-8", "replace").strip()
        ls = self._run("ls-files", "-z", "--", "sound/songs/midi")
        if ls is not None:
            self.tracked = {p.decode("utf-8", "replace") for p in ls.split(b"\0") if p}

    def _run(self, *args, data: Optional[bytes] = None) -> Optional[bytes]:
        try:
            r = subprocess.run(["git", "-C", self.root] + list(args), input=data,
                               capture_output=True, timeout=60, **_no_window())
        except (OSError, subprocess.SubprocessError):
            return None
        return r.stdout if r.returncode == 0 else None

    @property
    def ok(self) -> bool:
        return self.prefix is not None

    def commits(self, rels: Iterable[str]) -> list:
        if not self.ok:
            return []
        out = self._run("log", f"-n{HISTORY_LIMIT}", "--format=%H", "--", *rels)
        return out.decode().split() if out else []

    def blobs(self, specs: list) -> dict:
        """{"<commit>:<path relative to the project>": bytes or None}, one git
        process for all of them."""
        if not self.ok or not specs:
            return {}
        full = [s.split(":", 1)[0] + ":" + self.prefix + s.split(":", 1)[1] for s in specs]
        raw = self._run("cat-file", "--batch", data=("\n".join(full) + "\n").encode())
        out: dict = {}
        if raw is None:
            return out
        i = 0
        for spec in specs:
            nl = raw.find(b"\n", i)
            if nl < 0:
                break
            head = raw[i:nl].split()
            i = nl + 1
            if len(head) == 3 and head[1] == b"blob":
                size = int(head[2])
                out[spec] = raw[i:i + size]
                i += size + 1
            else:
                out[spec] = None
        return out


# ─────────────────────────────────────────────────────────────────────────────
# Classification
# ─────────────────────────────────────────────────────────────────────────────

def _rel(label: str, ext: str) -> str:
    return f"sound/songs/midi/{label}{ext}"


def _per_song_flags_pending(project_root: str, label: str, flags) -> bool:
    """Under the per-song build rule, True when make will rebuild this song
    because its settings TEXT differs from the text it was last built with."""
    p = os.path.join(project_root, "build", "midi_flags", label + ".flags")
    data = _read(p)
    if data is None:
        return False
    return data.decode("utf-8", "replace").strip() != " ".join(flags)


class _Classifier:
    def __init__(self, project_root: str, rendered_from_s: Iterable[str]):
        from core.sound.midi_cfg_freshness import cfg_flags, audio_rule_mode
        self.root = project_root
        self.md = os.path.join(project_root, MIDI_SUBDIR)
        cfg = _read(os.path.join(self.md, "midi.cfg")) or b""
        self.flags = cfg_flags(cfg.decode("utf-8", "replace"))
        self.mode = audio_rule_mode(project_root)
        self.rendered_from_s = set(rendered_from_s)
        self.r = _Renderer(project_root)
        self.record = _load_record(project_root)
        self.git: Optional[_Git] = None
        self._mk: dict = {}                 # (label, text key) -> music key of a .s
        self._mk_lock = threading.Lock()

    def same(self, s_text: str, s_key: str, label: str, mid_bytes: bytes, flags) -> bool:
        """Does *s_text* play what the build makes from *mid_bytes*?"""
        t, _ = self.r.keys(label, mid_bytes, flags, music=False)
        if t == s_key:
            return True
        with self._mk_lock:
            known = (label, s_key) in self._mk
            mine = self._mk.get((label, s_key))
        if not known:
            mine = music_key(s_text, label)          # a duplicate computation is harmless
            with self._mk_lock:
                self._mk[(label, s_key)] = mine
        if mine is None:
            return False
        _, m = self.r.keys(label, mid_bytes, flags, music=True)
        return m is not None and m == mine

    def working(self, label: str):
        """First pass (runs in a worker thread): IN_SYNC or S_AUTHORED, or None
        when history is needed."""
        from core.sound.midi_ownership import is_porysuite_written
        from core.sound.safe_song_save import SongSaveRefused
        s_path = os.path.join(self.md, label + ".s")
        mid_path = os.path.join(self.md, label + ".mid")
        s_bytes, mid_bytes = _read(s_path), _read(mid_path)
        if s_bytes is None or mid_bytes is None:
            return None, None
        s_text = s_bytes.decode("latin-1")
        sk, md = text_key(s_text, label), _sha1(mid_bytes)
        flags = self.flags[label]
        if is_porysuite_written(s_path, mid_path) or label in self.rendered_from_s:
            why = ("written in PorySuite: its .mid is an empty stand-in, so the .s is the only copy"
                   if is_porysuite_written(s_path, mid_path) else
                   "written in PorySuite: its .mid was rendered from the .s just now")
            if label in self.rendered_from_s:
                try:
                    if self.same(s_text, sk, label, mid_bytes, flags):
                        return SongState(label, IN_SYNC, "plays what the build makes from its .mid",
                                         sk, md), (s_text, mid_bytes)
                except SongSaveRefused:
                    pass
            return SongState(label, S_AUTHORED, why, sk, md), (s_text, mid_bytes)
        # The song's own record line (song build protection, core/sound/song_guard.py):
        # which .mid and flags its .s was built from and what the .s held then. It
        # travels with the .s, so it answers on any computer without git history.
        from core.sound import song_guard as G
        rec = G.record_verdict(s_bytes, mid_bytes, flags)
        if rec is not None:
            verdict, stamp = rec
            if verdict == G.REC_IN_SYNC:
                return SongState(label, IN_SYNC, "its record line shows it was built from this .mid "
                                 "with these settings, unchanged since", sk, md), (s_text, mid_bytes)
            if verdict == G.REC_MIDI_AHEAD:
                return SongState(label, MIDI_AHEAD, "its record line shows it is unchanged since it was "
                                 "built from an earlier .mid or other settings, so the build must rebuild "
                                 "it", sk, md), (s_text, mid_bytes)
            if verdict == G.REC_S_AHEAD:
                why = ("its record line marks the .s as the song's source, and its .mid and settings "
                       "are unchanged since" if stamp.is_source else
                       "its .s was edited after it was built, and its .mid and settings are unchanged "
                       "since: the .s is the newer work")
                return SongState(label, S_AHEAD, why, sk, md), (s_text, mid_bytes)
            if self.r.exe:
                try:
                    if self.same(s_text, sk, label, mid_bytes, flags):
                        return SongState(label, IN_SYNC, "plays what the build makes from its .mid",
                                         sk, md), (s_text, mid_bytes)
                except SongSaveRefused:
                    pass
            return SongState(label, CONFLICT,
                             "both sides changed since its record line was written: its .s "
                             + ("is marked as the song's source" if stamp.is_source else
                                "was edited after it was built")
                             + ", and its .mid or its midi.cfg settings changed too", sk, md), \
                (s_text, mid_bytes)
        if not self.r.exe:
            return None, (s_text, mid_bytes)
        try:
            if self.same(s_text, sk, label, mid_bytes, flags):
                return SongState(label, IN_SYNC, "plays what the build makes from its .mid", sk, md), \
                    (s_text, mid_bytes)
        except SongSaveRefused as exc:
            return SongState(label, UNKNOWN,
                             f"the game's song converter rejects its .mid ({exc}), so what the build "
                             f"would make cannot be checked", sk, md), (s_text, mid_bytes)
        return None, (s_text, mid_bytes)

    def resolve(self, label: str, s_text: str, mid_bytes: bytes) -> SongState:
        """Second pass, for a song whose .s does not play what its .mid makes."""
        from core.sound.safe_song_save import SongSaveRefused
        from core.sound.midi_cfg_freshness import cfg_flags
        sk, md = text_key(s_text, label), _sha1(mid_bytes)
        flags = tuple(self.flags[label])
        if self.git is None:
            self.git = _Git(self.root)
        g = self.git
        s_tracked = _rel(label, ".s") in g.tracked

        # Every earlier version of the song this project knows: (where, mid, s or None, flags).
        versions = []
        commits = g.commits([_rel(label, ".mid"), _rel(label, ".s")])
        specs = []
        for c in commits:
            specs += [f"{c}:{_rel(label, '.mid')}", f"{c}:{_rel(label, '.s')}",
                      f"{c}:sound/songs/midi/midi.cfg"]
        blobs = g.blobs(specs)
        for c in commits:
            m = blobs.get(f"{c}:{_rel(label, '.mid')}")
            s = blobs.get(f"{c}:{_rel(label, '.s')}")
            cfg = blobs.get(f"{c}:sound/songs/midi/midi.cfg")
            f = cfg_flags(cfg.decode("utf-8", "replace")).get(label) if cfg else None
            versions.append((c[:7], m, s.decode("latin-1") if s is not None else None,
                             tuple(f) if f is not None else flags))

        def renders(m, f):
            try:
                return m is not None and not _is_placeholder_bytes(m) and \
                    self.same(s_text, sk, label, m, f)
            except SongSaveRefused:
                return False

        rec = self.record.get(label)
        # (a) Is the .s just what the build made from an earlier .mid? Then it
        # holds nothing unique and the current .mid must be built.
        if rec and rec.get("s") == sk:
            return SongState(label, MIDI_AHEAD,
                             "its .s is unchanged since PorySuite last saw it match its .mid; the "
                             ".mid or its settings changed since, so the build must rebuild it", sk, md)
        for where, m, _s, f in versions:
            if renders(m, f):
                return SongState(label, MIDI_AHEAD,
                                 f"its .s is what the build makes from the .mid of commit {where}; the "
                                 f".mid or its settings changed since, and the .s holds nothing that "
                                 f"version did not, so the build must rebuild it", sk, md)

        # (b) The .s holds music no known .mid produces. When did the two last agree?
        baseline = None
        for where, m, s, f in versions:
            if s is None or m is None or _is_placeholder_bytes(m):
                continue
            try:
                if self.same(s, text_key(s, label), label, m, f):
                    baseline = (f"commit {where}", _sha1(m), f)
                    break
            except SongSaveRefused:
                continue
        if baseline is None and rec and rec.get("mid"):
            baseline = ("PorySuite's last check on this computer", rec["mid"], tuple(rec.get("flags") or ()))
        if baseline is not None:
            where, base_mid, base_flags = baseline
            mid_moved = base_mid != md
            flags_moved = tuple(base_flags) != flags
            if not mid_moved and not flags_moved:
                return SongState(label, S_AHEAD,
                                 f"its .s holds edits its .mid does not, and the .mid and its settings "
                                 f"are unchanged since they last matched ({where}): the .s is the newer "
                                 f"work", sk, md)
            moved = " and ".join(x for x, y in (("its .mid", mid_moved), ("its midi.cfg settings",
                                                                          flags_moved)) if y)
            return SongState(label, CONFLICT,
                             f"both sides changed since they last matched ({where}): its .s holds "
                             f"edits its .mid does not, and {moved} changed too", sk, md)
        if any(_is_placeholder_bytes(m) for _w, m, _s, _f in versions if m is not None):
            return SongState(label, S_AUTHORED,
                             "written in PorySuite: its committed .mid was an empty stand-in, so the "
                             ".s is the original", sk, md)
        why = ("there is no record of its .s and .mid ever matching"
               + ("" if g.ok else " (the project is not a git repository)")
               + ("" if s_tracked or not g.ok else " (its .s is not tracked by git)"))
        return SongState(label, UNKNOWN,
                         f"its .s holds music its .mid does not produce, and {why}: which is newer "
                         f"cannot be established", sk, md)


def classify_project(project_root: str, *, rendered_from_s: Iterable[str] = (),
                     progress: Optional[Callable[[int, int], None]] = None,
                     workers: int = 8) -> SyncPlan:
    """Read-only: classify every song that has a ``.mid``, a ``.s`` and a
    midi.cfg line. *rendered_from_s* names songs whose ``.mid`` the integrity
    sweep just rendered from their ``.s``. *progress(done, total)* is called
    from the calling thread."""
    from concurrent.futures import ThreadPoolExecutor, as_completed
    c = _Classifier(project_root, rendered_from_s)
    plan = SyncPlan(project_root, mode=c.mode)
    if not os.path.isdir(c.md):
        return plan
    labels = sorted(l for l in c.flags
                    if os.path.isfile(os.path.join(c.md, l + ".mid"))
                    and os.path.isfile(os.path.join(c.md, l + ".s")))
    if not c.r.exe:
        plan.note = ("the game's song converter (tools/mid2agb) is not built yet, so only songs "
                     "written in PorySuite could be checked")
    pending = {}
    done = 0
    with ThreadPoolExecutor(max_workers=max(1, workers)) as ex:
        futs = {ex.submit(c.working, l): l for l in labels}
        for fut in as_completed(futs):
            label = futs[fut]
            try:
                st, files = fut.result()
            except Exception as exc:
                st, files = SongState(label, UNKNOWN, f"could not be checked ({exc})"), None
            if st is not None:
                plan.songs[label] = st
            elif files is not None:
                if c.r.exe:
                    pending[label] = files
                else:
                    plan.unchecked.append((label, plan.note))
            done += 1
            if progress:
                progress(done, len(labels))
    for label, (s_text, mid_bytes) in sorted(pending.items()):
        try:
            plan.songs[label] = c.resolve(label, s_text, mid_bytes)
        except Exception as exc:
            plan.songs[label] = SongState(label, UNKNOWN, f"could not be checked ({exc})")
    from core.sound.midi_cfg_freshness import PER_SONG
    from core.sound.song_guard import is_installed
    plan.protected = is_installed(project_root)
    # Under the per-song rule make rebuilds a song whose flags TEXT changed. With
    # the guard installed that rebuild keeps an edited .s (it compares flags by
    # meaning), so this applies only to unprotected projects.
    if c.mode == PER_SONG and not plan.protected:
        for st in plan.of(*KEPT_STATES):
            if _per_song_flags_pending(project_root, st.label, tuple(c.flags[st.label])):
                st.state = CONFLICT
                st.reason += (" — but this project's build will rebuild it from its .mid anyway, "
                              "because its midi.cfg line's text changed since it was last built")
    # Remember every song seen in step, as positive evidence for later checks.
    for st in plan.of(IN_SYNC):
        c.record[st.label] = {"mid": st.mid_digest, "s": st.s_key,
                              "flags": list(c.flags[st.label]), "when": int(time.time())}
    _save_record(project_root, c.record)
    c.r.save()
    _log.info("Song sync check: %s", plan.summary())
    for st in plan.songs.values():
        if st.state != IN_SYNC:
            _log.info("  %s: %s — %s", st.label, st.state, st.reason)
    return plan


# ─────────────────────────────────────────────────────────────────────────────
# A song .s naming a voicegroup the project no longer has
# ─────────────────────────────────────────────────────────────────────────────

_GRP = None


def _grp_re():
    global _GRP
    if _GRP is None:
        import re
        _GRP = re.compile(r"^(\s*\.equ\s+\w+_grp\s*,\s*)(voicegroup\w+)", re.M)
    return _GRP


def stale_voicegroup_verdict(project_root: str, label: str, s_text: str, valid_vgs) -> tuple:
    """(regenerable, explanation) for a ``.s`` whose voicegroup is missing.

    Regenerable only when the build of the song's current ``.mid`` (with its
    midi.cfg line) names a voicegroup that DOES exist and plays exactly what
    this ``.s`` plays apart from that voicegroup — i.e. the ``.s`` is just an
    old build. Anything else (the ``.s`` holds music the ``.mid`` does not, the
    midi.cfg line names a missing voicegroup too, no ``.mid``, a placeholder,
    the converter missing or refusing) is not regenerable: the caller must
    leave the song alone and report it."""
    from core.sound.midi_cfg_freshness import cfg_flags
    from core.sound.safe_song_save import SongSaveRefused
    md = os.path.join(project_root, MIDI_SUBDIR)
    mid = _read(os.path.join(md, label + ".mid"))
    if mid is None:
        return False, "it has no .mid to rebuild it from"
    if _is_placeholder_bytes(mid):
        return False, "its .mid is an empty stand-in, so the .s is the song's only copy"
    flags = cfg_flags((_read(os.path.join(md, "midi.cfg")) or b"").decode("utf-8", "replace"))
    if label not in flags:
        return False, "it has no line in midi.cfg, so the build cannot rebuild it"
    r = _Renderer(project_root)
    if not r.exe:
        return False, "the game's song converter (tools/mid2agb) is not built, so it cannot be checked"
    try:
        built = r.text(label, mid, flags[label])
    except SongSaveRefused as exc:
        return False, f"the game's song converter rejects its .mid ({exc})"
    m = _grp_re().search(built)
    if not m or m.group(2) not in valid_vgs:
        vg = m.group(2) if m else "no voicegroup"
        return False, (f"its midi.cfg line names {vg}, which is missing too, so rebuilding it "
                       f"would not help")
    repointed = _grp_re().sub(lambda x: x.group(1) + m.group(2), s_text, count=1)
    if text_key(repointed, label) == text_key(built, label):
        return True, f"it is an old build of its .mid; the rebuild uses {m.group(2)}"
    mine, theirs = music_key(repointed, label), music_key(built, label)
    if mine is not None and mine == theirs:
        return True, f"it is an old build of its .mid; the rebuild uses {m.group(2)}"
    return False, "it holds music its .mid does not, so rebuilding it would lose that music"


def backup_song_file(project_root: str, path: str, why: str = "song_backups") -> str:
    """Copy *path* into PorySuite's cache (never the game tree) and return the
    copy's path. Raises OSError when it cannot."""
    from core.app_info import get_cache_dir
    d = os.path.join(get_cache_dir(project_root), why, time.strftime("%Y%m%d-%H%M%S"))
    os.makedirs(d, exist_ok=True)
    dst = os.path.join(d, os.path.basename(path))
    shutil.copy2(path, dst)
    if _read(dst) != _read(path):
        raise OSError(f"the backup of {os.path.basename(path)} does not match the original")
    return dst


# ─────────────────────────────────────────────────────────────────────────────
# Making the build follow the decision, and checking it did
# ─────────────────────────────────────────────────────────────────────────────

def apply_plan(plan: SyncPlan, *, keep: Iterable[str] = ()) -> ApplyReport:
    """Make the build do what the content decided. MIDI_AHEAD songs are sent
    to the build; S_AHEAD / S_AUTHORED songs, and the blocking songs named in
    *keep* (an explicit choice), keep their ``.s``, which is copied to a backup
    in PorySuite's cache first. Blocking songs not in *keep* are not touched.

    Unprotected project: writes only file times (make reads nothing else).
    Protected project (song build protection installed): writes no times at
    all - each song's record line instead (``song_guard.stamp_writes``), which
    the build itself reads, so the decision also holds for a later terminal
    make and on other computers. A kept blocking song is then settled for good
    (recorded as the song's source), not just for this build."""
    root = plan.project_root
    md = os.path.join(root, MIDI_SUBDIR)
    rep = ApplyReport()
    keep = set(keep)
    for st in plan.of(MIDI_AHEAD):
        s, mid = os.path.join(md, st.label + ".s"), os.path.join(md, st.label + ".mid")
        if plan.protected:
            rep.rebuild.append(st.label)
            continue
        try:
            mid_mt = os.path.getmtime(mid)
            if os.path.getmtime(s) >= mid_mt:
                os.utime(s, (mid_mt - 2, mid_mt - 2))
            rep.rebuild.append(st.label)
        except OSError as exc:
            rep.errors.append((st.label, str(exc)))
    to_keep = [st for st in plan.songs.values()
               if st.state in KEPT_STATES or (st.state in BLOCKING_STATES and st.label in keep)]
    if to_keep:
        try:
            from core.app_info import get_cache_dir
            rep.backup_dir = os.path.join(get_cache_dir(root), "song_backups",
                                          time.strftime("%Y%m%d-%H%M%S"))
            os.makedirs(rep.backup_dir, exist_ok=True)
        except Exception as exc:
            rep.errors.append(("(backup folder)", str(exc)))
            rep.backup_dir = ""
    now = time.time()
    for st in to_keep:
        s = os.path.join(md, st.label + ".s")
        try:
            if rep.backup_dir:
                shutil.copy2(s, os.path.join(rep.backup_dir, st.label + ".s"))
            if not plan.protected:
                os.utime(s, (now, now))
            rep.kept[st.label] = st.s_key
        except OSError as exc:
            rep.errors.append((st.label, str(exc)))
    if plan.protected:
        from core.sound.song_guard import stamp_writes, write_stamps
        writes, _skipped = stamp_writes(root, plan, keep=keep)
        for err in write_stamps(writes):
            rep.errors.append(("(song records)", err))
        rep.stamped = len(writes)
    _log.info("Song sync applied: %d sent to the build, %d kept (backup %s)",
              len(rep.rebuild), len(rep.kept), rep.backup_dir or "none")
    return rep


def expected_rebuilds(plan: SyncPlan, rep: ApplyReport) -> None:
    """Fill rep.expected with the text key each MIDI_AHEAD song must have after
    the build (what mid2agb makes from its current .mid)."""
    r = _Renderer(plan.project_root)
    from core.sound.midi_cfg_freshness import cfg_flags
    md = os.path.join(plan.project_root, MIDI_SUBDIR)
    flags = cfg_flags((_read(os.path.join(md, "midi.cfg")) or b"").decode("utf-8", "replace"))
    for label in rep.rebuild:
        mid = _read(os.path.join(md, label + ".mid"))
        if mid is None or label not in flags or not r.exe:
            continue
        try:
            rep.expected[label] = r.keys(label, mid, flags[label], music=False)[0]
        except Exception:
            continue
    r.save()


def verify_after_build(project_root: str, rep: ApplyReport) -> list:
    """Plain-English problems found after a build: a kept .s that changed, or a
    song that should have been rebuilt from its .mid and was not."""
    md = os.path.join(project_root, MIDI_SUBDIR)
    out = []
    for label, key in sorted(rep.kept.items()):
        data = _read(os.path.join(md, label + ".s"))
        if data is None:
            out.append(f"{label}.s was DELETED by the build; the copy kept before the build is in "
                       f"{rep.backup_dir or '(no backup folder)'}")
        elif text_key(data.decode("latin-1"), label) != key:
            out.append(f"{label}.s was CHANGED by the build although it holds work its .mid does not; "
                       f"the copy kept before the build is in {rep.backup_dir or '(no backup folder)'}")
    for label, key in sorted(rep.expected.items()):
        data = _read(os.path.join(md, label + ".s"))
        if data is None or text_key(data.decode("latin-1"), label) != key:
            out.append(f"{label}.s was NOT rebuilt from its newer .mid — the ROM still has its old music")
    return out
