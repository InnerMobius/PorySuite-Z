"""Song build protection: a guard in the project's own build, opt-in per project.

Why it exists
-------------
A song has a ``.mid`` and a ``.s``. pret's rule rebuilds the ``.s`` from the
``.mid`` whenever the ``.mid`` or midi.cfg has a newer FILE TIME, and nothing
else is consulted. File times do not say who wrote what (git sets them in
checkout order), so a terminal ``make`` after a pull or a midi.cfg rewrite
overwrote ``.s`` edits, a commit touching both files shipped a stale ``.s``,
and ``make clean`` deleted every ``.s`` including those that were a song's only
real copy (forensic report 2026-11-15: B, G, I, J, K2). PorySuite's Make button
decides by content (``song_sync``) — but only when PorySuite runs the build.

What the guard does
-------------------
``song_guard.sh`` replaces mid2agb in the song rule and ``rm -f *.s`` in
``make clean``. Each ``.s`` it writes starts with a comment::

    @ song-guard v1 mid=<sha1 of the .mid> s=<fingerprint> flags: <flags>

— which ``.mid`` and flags the ``.s`` was built from and a fingerprint of what
the ``.s`` held then. The line travels with the ``.s`` (git, other computers);
no cache. A ``.s`` still matching its fingerprint holds nothing its ``.mid``
cannot rebuild. One that does not was edited: kept while its ``.mid`` and
flags are unchanged, and the build STOPS if they changed too. ``s=-`` marks a
``.s`` that is the song's source. ``song_guard.sh pending`` makes make check
every song whose record no longer matches its ``.mid`` (a pull, a branch
switch, a DAW) whatever the file times say. Full rules: the script's header.

This module
-----------
* the same fingerprint / flag order / record line as the script, in Python
  (``fingerprint``, ``canon_flags``, ``parse_stamp``, ``with_stamp``), so
  PorySuite and the terminal reach the same answer;
* ``record_verdict`` — what a song's record says, for ``song_sync``;
* ``detect`` / ``plan_install`` / ``install`` / ``plan_uninstall`` /
  ``uninstall`` — the setup, which changes exactly three build lines (shown as a
  diff first, backed up first, refused when the project's rules are not the
  shape it knows) and adds two files;
* ``stamp_writes`` — the records written at setup and by the Make button.

Nothing here runs unless the project owner chose to install it.
"""

from __future__ import annotations

import difflib
import hashlib
import json
import logging
import os
import time
from dataclasses import dataclass, field
from typing import Iterable, Optional

_log = logging.getLogger("SoundEditor.SongGuard")

TAG = "@ song-guard v1"
SCRIPT = "song_guard.sh"
MAKEFRAG = "song_guard.mk"
FILES_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "song_guard_files")
MIDI_SUBDIR = os.path.join("sound", "songs", "midi")
NO_MID = "0" * 40               # "built from a .mid that is not the current one"
SOURCE = "-"                    # the .s is the song's source

INCLUDE_LINE = "include song_guard.mk"
RECIPE_OLD = "\t$(MID) $$< $$@ $2"
RECIPE_NEW = "\t$(SONG_GUARD) $$< $$@ $2"
CLEAN_OLD = "\trm -f $(MID_SUBDIR)/*.s"
CLEAN_NEW = "\t$(SONG_GUARD_CLEAN)"
DEFINE_LINE = "define MID_RULE"

NOT_INSTALLED = "not_installed"
INSTALLED = "installed"
PARTIAL = "partial"
UNSUPPORTED = "unsupported"


# ─────────────────────────────────────────────────────────────────────────────
# The record line — must agree byte for byte with song_guard.sh
# ─────────────────────────────────────────────────────────────────────────────

def _sha1(data: bytes) -> str:
    return hashlib.sha1(data).hexdigest()


def fingerprint(s_bytes: bytes) -> str:
    """What the assembler sees of an .s — comments (``@ ...``), trailing
    spaces, blank lines and carriage returns removed — as a sha1. The script's
    ``fingerprint`` gives the same value."""
    out = []
    for line in s_bytes.replace(b"\r", b"").split(b"\n"):
        line = line.split(b"@", 1)[0].rstrip(b" \t\v\f")
        if line:
            out.append(line + b"\n")
    return _sha1(b"".join(out))


def canon_flags(flags: Iterable[str]) -> str:
    """mid2agb's options in a fixed order (their order does not matter to
    it), unless one option appears twice — then as written."""
    words = [w for w in flags if w]
    keys = [w[:2] for w in words]
    if len(set(keys)) == len(keys):
        words = sorted(words, key=lambda w: w.encode("latin-1", "replace"))
    return " ".join(words)


@dataclass
class Stamp:
    mid: str
    s: str
    flags: str

    @property
    def is_source(self) -> bool:
        return self.s == SOURCE


def parse_stamp(s_bytes: Optional[bytes]) -> Optional[Stamp]:
    if not s_bytes:
        return None
    first = s_bytes.split(b"\n", 1)[0].replace(b"\r", b"").decode("latin-1")
    if not first.startswith(TAG + " mid="):
        return None
    parts = first.split()
    if len(parts) < 6 or not parts[3].startswith("mid=") or not parts[4].startswith("s=") \
            or parts[5] != "flags:":
        return None
    return Stamp(parts[3][4:], parts[4][2:], " ".join(parts[6:]))


def stamp_line(mid_sha: str, s_field: str, cflags: str) -> str:
    return f"{TAG} mid={mid_sha} s={s_field} flags: {cflags}"


def strip_stamp(s_bytes: bytes) -> bytes:
    """*s_bytes* without its record line (only ever the first line)."""
    if parse_stamp(s_bytes) is None:
        return s_bytes
    nl = s_bytes.find(b"\n")
    return b"" if nl < 0 else s_bytes[nl + 1:]


def with_stamp(s_bytes: bytes, mid_sha: str, s_field: str, cflags: str, *, eol: str = "") -> bytes:
    """*s_bytes* with its record line replaced (or added) as the first line.
    Nothing else changes in the content. Line endings follow the same rule as
    the script: *eol* "lf" (git stores the file with LF) writes LF throughout,
    which is what git checks out, so git does not list the song as changed;
    otherwise the file keeps its own (CRLF when it has a carriage return)."""
    if eol == "lf":
        body = strip_stamp(s_bytes).replace(b"\r", b"")
        return stamp_line(mid_sha, s_field, cflags).encode("latin-1") + b"\n" + body
    nl = b"\r\n" if b"\r" in s_bytes[:65536] else b"\n"
    return stamp_line(mid_sha, s_field, cflags).encode("latin-1") + nl + strip_stamp(s_bytes)


def git_eol(project_root: str, paths) -> dict:
    """{path: "lf" / "crlf" / other} — the line ending git's attributes give each
    path (one git call). Empty when the project is not a git repository."""
    import subprocess
    paths = list(paths)
    if not paths:
        return {}
    rel = [os.path.relpath(p, project_root).replace(os.sep, "/") for p in paths]
    try:
        r = subprocess.run(["git", "-C", project_root, "check-attr", "--stdin", "-z", "eol"],
                           input="\0".join(rel).encode() + b"\0", capture_output=True, timeout=60,
                           creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    except (OSError, subprocess.SubprocessError):
        return {}
    if r.returncode != 0:
        return {}
    parts = r.stdout.decode("utf-8", "replace").split("\0")
    by_rel = {parts[i]: parts[i + 2] for i in range(0, len(parts) - 2, 3)}
    return {p: by_rel.get(q, "") for p, q in zip(paths, rel)}


# Verdicts of a song's record
REC_IN_SYNC = "in_sync"           # the .s is what its current .mid + flags built
REC_MIDI_AHEAD = "midi_ahead"     # the .s is what an EARLIER .mid / flags built
REC_S_AHEAD = "s_ahead"           # the .s was edited (or is the source); .mid + flags unchanged
REC_BOTH_MOVED = "both_moved"     # edited AND the .mid / flags changed: a conflict unless the
                                  # current .mid happens to build exactly this .s


def record_verdict(s_bytes: bytes, mid_bytes: bytes, flags) -> Optional[tuple]:
    """(verdict, Stamp) from a song's record line, or None when it has none."""
    st = parse_stamp(s_bytes)
    if st is None:
        return None
    pristine = not st.is_source and fingerprint(s_bytes) == st.s
    same = st.mid == _sha1(mid_bytes) and st.flags == canon_flags(flags)
    if pristine:
        return (REC_IN_SYNC if same else REC_MIDI_AHEAD), st
    return (REC_S_AHEAD if same else REC_BOTH_MOVED), st


# ─────────────────────────────────────────────────────────────────────────────
# Is it installed? Can it be?
# ─────────────────────────────────────────────────────────────────────────────

def shipped(name: str) -> bytes:
    with open(os.path.join(FILES_DIR, name), "rb") as fh:
        return fh.read().replace(b"\r\n", b"\n")


def _read(path: str) -> Optional[bytes]:
    try:
        with open(path, "rb") as fh:
            return fh.read()
    except OSError:
        return None


def _lines(data: bytes) -> list:
    return data.decode("latin-1").split("\n")


def _count(lines: list, text: str) -> int:
    return sum(1 for ln in lines if ln.rstrip("\r") == text)


def _rule_files(project_root: str) -> dict:
    """{name: bytes} of the build files the song rule and clean rule live in."""
    out = {}
    for name in ("Makefile", "audio_rules.mk"):
        data = _read(os.path.join(project_root, name))
        if data is not None:
            out[name] = data
    return out


@dataclass
class GuardStatus:
    state: str
    problems: list = field(default_factory=list)     # plain-English reasons (unsupported / partial)
    recipe_file: str = ""                            # where the song rule lives
    clean_file: str = ""                             # where `rm -f *.s` lives
    script_current: bool = True                      # installed script = the one PorySuite ships

    @property
    def installed(self) -> bool:
        return self.state == INSTALLED


def _locate(files: dict, old: str, new: str) -> tuple:
    """(file holding *old*, file holding *new*, problems)."""
    olds = [(n, _count(_lines(d), old)) for n, d in files.items()]
    news = [(n, _count(_lines(d), new)) for n, d in files.items()]
    o = [(n, c) for n, c in olds if c]
    w = [(n, c) for n, c in news if c]
    return o, w


def detect(project_root: str) -> GuardStatus:
    """What state the protection is in for this project. Read-only."""
    files = _rule_files(project_root)
    if "Makefile" not in files:
        return GuardStatus(UNSUPPORTED, ["the project has no Makefile"])
    have_sh = os.path.isfile(os.path.join(project_root, SCRIPT))
    have_mk = os.path.isfile(os.path.join(project_root, MAKEFRAG))
    ro, rn = _locate(files, RECIPE_OLD, RECIPE_NEW)
    co, cn = _locate(files, CLEAN_OLD, CLEAN_NEW)
    inc = [(n, _count(_lines(d), INCLUDE_LINE)) for n, d in files.items()]
    inc = [(n, c) for n, c in inc if c]
    st = GuardStatus(NOT_INSTALLED)
    guarded_build = bool(rn) and bool(inc) and have_mk and have_sh
    guarded_clean = bool(cn)
    if not (rn or cn or inc or have_mk or have_sh):
        problems = []
        if sum(c for _, c in ro) != 1:
            problems.append(
                "the song rule's converter line (a tab, then  $(MID) $$< $$@ $2 ) was "
                + ("not found" if not ro else "found more than once")
                + " in Makefile / audio_rules.mk, so PorySuite cannot tell where to put the guard")
        if sum(c for _, c in co) != 1:
            problems.append(
                "the clean rule's song line (a tab, then  rm -f $(MID_SUBDIR)/*.s ) was "
                + ("not found" if not co else "found more than once")
                + " in Makefile / audio_rules.mk")
        if ro and len(ro) == 1:
            st.recipe_file = ro[0][0]
            lines = _lines(files[ro[0][0]])
            if _count(lines, DEFINE_LINE) != 1:
                problems.append(f"{ro[0][0]} does not define the song rule as  define MID_RULE  "
                                f"exactly once")
            mad = any(ln.replace(" ", "").rstrip("\r") == "MID_ASM_DIR:=$(MID_SUBDIR)" for ln in lines)
            if not mad:
                problems.append(f"{ro[0][0]} does not build song .s files in the .mid folder "
                                f"(MID_ASM_DIR := $(MID_SUBDIR)); the guard supports only that layout")
        if co and len(co) == 1:
            st.clean_file = co[0][0]
        if any("SONG_GUARD" in d.decode("latin-1") for d in files.values()):
            problems.append("the build files already mention SONG_GUARD")
        if problems:
            return GuardStatus(UNSUPPORTED, problems, st.recipe_file, st.clean_file)
        return st
    st.recipe_file = rn[0][0] if rn else (ro[0][0] if ro else "")
    st.clean_file = cn[0][0] if cn else (co[0][0] if co else "")
    if guarded_build and guarded_clean and not ro and not co:
        st.state = INSTALLED
        st.script_current = (_read(os.path.join(project_root, SCRIPT)) or b"").replace(b"\r\n", b"\n") \
            == shipped(SCRIPT) and (_read(os.path.join(project_root, MAKEFRAG)) or b"") \
            .replace(b"\r\n", b"\n") == shipped(MAKEFRAG)
        return st
    st.state = PARTIAL
    if not have_sh:
        st.problems.append(f"{SCRIPT} is missing")
    if not have_mk:
        st.problems.append(f"{MAKEFRAG} is missing")
    if not inc:
        st.problems.append(f"the song rule file does not include {MAKEFRAG}")
    if not rn:
        st.problems.append("the song rule still runs the converter directly (unguarded)")
    if not cn:
        st.problems.append("make clean still deletes every song .s (unguarded)")
    if ro and rn:
        st.problems.append("the song rule is guarded in one place and not in another")
    return st


def is_installed(project_root: str) -> bool:
    try:
        return detect(project_root).installed
    except Exception:
        return False


# ─────────────────────────────────────────────────────────────────────────────
# Install / uninstall plans (shown to the user as a diff before anything is written)
# ─────────────────────────────────────────────────────────────────────────────

class GuardRefused(Exception):
    """The project's build files are not a shape the setup knows; nothing was
    changed. ``reasons`` is a list of plain-English sentences."""

    def __init__(self, reasons):
        super().__init__("; ".join(reasons))
        self.reasons = list(reasons)


@dataclass
class FileChange:
    name: str                    # path relative to the project
    old: Optional[bytes]         # None: the file is new
    new: Optional[bytes]         # None: the file is removed

    def diff(self) -> str:
        a = (self.old or b"").decode("latin-1").replace("\r", "").splitlines(keepends=True)
        b = (self.new or b"").decode("latin-1").replace("\r", "").splitlines(keepends=True)
        return "".join(difflib.unified_diff(a, b, "a/" + self.name if self.old is not None else "/dev/null",
                                            "b/" + self.name if self.new is not None else "/dev/null"))


@dataclass
class GuardPlan:
    action: str                                     # "install" or "uninstall"
    changes: list = field(default_factory=list)     # FileChange, in the order they are written

    def diff(self, *, full_new_files: bool = True) -> str:
        parts = []
        for c in self.changes:
            if c.old is None and not full_new_files:
                parts.append(f"--- /dev/null\n+++ b/{c.name}\n(new file, {len(c.new or b'')} bytes)\n")
            else:
                parts.append(c.diff())
        return "\n".join(parts)

    def names(self) -> list:
        return [c.name for c in self.changes]


def _replace_line(data: bytes, old: str, new: str) -> bytes:
    lines = data.split(b"\n")
    out = []
    for raw in lines:
        txt = raw.decode("latin-1")
        cr = txt.endswith("\r")
        if txt.rstrip("\r") == old:
            out.append((new + ("\r" if cr else "")).encode("latin-1"))
        else:
            out.append(raw)
    return b"\n".join(out)


def _insert_before(data: bytes, anchor: str, new: str) -> bytes:
    lines = data.split(b"\n")
    out = []
    for raw in lines:
        txt = raw.decode("latin-1")
        if txt.rstrip("\r") == anchor:
            cr = "\r" if txt.endswith("\r") else ""
            out.append((f"# Song build protection (PorySuite-Z, opt-in; see {MAKEFRAG})" + cr).encode("latin-1"))
            out.append((new + cr).encode("latin-1"))
        out.append(raw)
    return b"\n".join(out)


def _remove_line(data: bytes, text: str, *, with_comment: bool = True) -> bytes:
    lines = data.split(b"\n")
    out = []
    for raw in lines:
        txt = raw.decode("latin-1").rstrip("\r")
        if txt == text:
            if with_comment and out and out[-1].decode("latin-1").rstrip("\r").startswith(
                    "# Song build protection (PorySuite-Z"):
                out.pop()
            continue
        out.append(raw)
    return b"\n".join(out)


def plan_install(project_root: str) -> GuardPlan:
    """The exact changes ``install`` would make, in write order. Raises
    GuardRefused (nothing changed) when the project is not a supported shape or
    the guard is already (partly) there."""
    st = detect(project_root)
    if st.state == UNSUPPORTED:
        raise GuardRefused(st.problems)
    if st.state == INSTALLED:
        raise GuardRefused(["song build protection is already installed in this project"])
    if st.state == PARTIAL:
        raise GuardRefused(["song build protection is partly installed ("
                            + "; ".join(st.problems) + "). Remove it first, then install again"])
    files = _rule_files(project_root)
    edits: dict = {}
    # 1. make clean: delete only rebuildable .s files. Written first: until the
    #    include exists the new line expands to nothing, so make clean deletes no
    #    song at all — every half-installed moment is safe.
    edits[st.clean_file] = _replace_line(files[st.clean_file], CLEAN_OLD, CLEAN_NEW)
    # 2. the song rule: the guard instead of the converter, and the include
    #    that defines it. Same file, one write: never one without the other.
    base = edits.get(st.recipe_file, files[st.recipe_file])
    base = _insert_before(base, DEFINE_LINE, INCLUDE_LINE)
    edits[st.recipe_file] = _replace_line(base, RECIPE_OLD, RECIPE_NEW)
    plan = GuardPlan("install")
    plan.changes.append(FileChange(SCRIPT, None, shipped(SCRIPT)))
    plan.changes.append(FileChange(MAKEFRAG, None, shipped(MAKEFRAG)))
    order = [st.clean_file] + ([st.recipe_file] if st.recipe_file != st.clean_file else [])
    for name in order:
        plan.changes.append(FileChange(name, files[name], edits[name]))
    return plan


def plan_uninstall(project_root: str) -> GuardPlan:
    """The changes ``uninstall`` would make: the three lines put back, the two
    files removed. Works on a partly-installed project too. Song .s files are
    not touched (their record lines are comments; they stay valid if the guard
    is installed again)."""
    files = _rule_files(project_root)
    st = detect(project_root)
    if st.state in (NOT_INSTALLED, UNSUPPORTED) and not any(
            os.path.isfile(os.path.join(project_root, n)) for n in (SCRIPT, MAKEFRAG)):
        raise GuardRefused(["song build protection is not installed in this project"])
    plan = GuardPlan("uninstall")
    new = {}
    for name, data in files.items():
        d = _remove_line(data, INCLUDE_LINE)
        d = _replace_line(d, RECIPE_NEW, RECIPE_OLD)
        d = _replace_line(d, CLEAN_NEW, CLEAN_OLD)
        if d != data:
            new[name] = d
    # The song rule's file first (the build stops using the guard), then the
    # clean rule's: while make clean still names $(SONG_GUARD_CLEAN) without
    # the include it expands to nothing and deletes no song.
    def has_rule(name):
        lines = files[name].decode("latin-1").replace("\r", "").split("\n")
        return INCLUDE_LINE in lines or RECIPE_NEW in lines
    for name in sorted(new, key=lambda n: 0 if has_rule(n) else 1):
        plan.changes.append(FileChange(name, files[name], new[name]))
    for name in (SCRIPT, MAKEFRAG):
        data = _read(os.path.join(project_root, name))
        if data is not None:
            plan.changes.append(FileChange(name, data, None))
    return plan


# ─────────────────────────────────────────────────────────────────────────────
# Writing a plan — backed up first, verified after, rolled back on failure
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class GuardResult:
    ok: bool
    backup_dir: str = ""
    messages: list = field(default_factory=list)


def backup_dir_for(project_root: str) -> str:
    from core.app_info import get_cache_dir
    d = os.path.join(get_cache_dir(project_root), "song_guard_setup", time.strftime("%Y%m%d-%H%M%S"))
    os.makedirs(d, exist_ok=True)
    return d


def _backup(project_root: str, plan: GuardPlan) -> str:
    """Copy every file the plan changes or removes; verify each copy."""
    d = backup_dir_for(project_root)
    manifest = {"project": os.path.abspath(project_root), "action": plan.action,
                "when": time.strftime("%Y-%m-%d %H:%M:%S"), "files": {}}
    for c in plan.changes:
        if c.old is None:
            continue
        dst = os.path.join(d, c.name.replace("/", os.sep))
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        with open(dst, "wb") as fh:
            fh.write(c.old)
        if _read(dst) != c.old:
            raise OSError(f"the backup of {c.name} does not match the original")
        manifest["files"][c.name] = _sha1(c.old)
    with open(os.path.join(d, "manifest.json"), "w", encoding="utf-8") as fh:
        json.dump(manifest, fh, indent=1)
    return d


def apply(project_root: str, plan: GuardPlan) -> GuardResult:
    """Write *plan* (from ``plan_install`` / ``plan_uninstall``). Every file is
    first checked to still be exactly what the plan was made from, then backed
    up; the writes are all-or-nothing (``safe_song_save.commit_files``); the
    result is checked with ``detect``. Nothing in sound/ is touched."""
    from core.sound.safe_song_save import commit_files
    res = GuardResult(False)
    for c in plan.changes:
        cur = _read(os.path.join(project_root, c.name))
        if cur != c.old:
            res.messages.append(f"{c.name} changed since the plan was shown; nothing was written. "
                                f"Open the setup again to see the current changes.")
            return res
    try:
        res.backup_dir = _backup(project_root, plan)
    except OSError as exc:
        res.messages.append(f"could not back up the build files ({exc}); nothing was written")
        return res
    writes = [(os.path.join(project_root, c.name), c.new) for c in plan.changes if c.new is not None]
    try:
        commit_files(writes)
    except Exception as exc:
        res.messages.append(f"writing failed and was rolled back ({exc}); the backup is in "
                            f"{res.backup_dir}")
        return res
    for c in plan.changes:
        if c.new is None:
            try:
                os.remove(os.path.join(project_root, c.name))
            except OSError as exc:
                res.messages.append(f"could not remove {c.name}: {exc}")
    st = detect(project_root)
    want = INSTALLED if plan.action == "install" else NOT_INSTALLED
    res.ok = st.state == want
    if not res.ok:
        res.messages.append(f"after writing, the project reads as '{st.state}' instead of "
                            f"'{want}': " + "; ".join(st.problems))
    _log.info("Song guard %s in %s: %s (backup %s)", plan.action, project_root,
              "ok" if res.ok else "FAILED", res.backup_dir)
    return res


# ─────────────────────────────────────────────────────────────────────────────
# Record lines written by PorySuite (setup, Make button, integrity sweep)
# ─────────────────────────────────────────────────────────────────────────────

def _cfg(project_root: str) -> dict:
    from core.sound.midi_cfg_freshness import cfg_flags
    data = _read(os.path.join(project_root, MIDI_SUBDIR, "midi.cfg")) or b""
    return cfg_flags(data.decode("utf-8", "replace"))


def stamp_writes(project_root: str, plan, *, keep: Iterable[str] = ()) -> tuple:
    """([(path, new bytes)], {label: why not recorded}) for a song_sync plan.

    * IN_SYNC → built from the current .mid (``s`` = the .s's fingerprint)
    * MIDI_AHEAD → built from an earlier .mid (``mid=000…``): the next make
      rebuilds it whatever the file times say
    * S_AHEAD / S_AUTHORED, and blocking songs named in *keep* (an explicit
      choice) → the .s is the song's source (``s=-``)
    * CONFLICT / UNKNOWN not in *keep* → not recorded: the guard stops on them
      until somebody chooses.
    A song whose line already says the same is skipped."""
    from core.sound import song_sync as S
    flags = _cfg(project_root)
    md = os.path.join(project_root, MIDI_SUBDIR)
    keep = set(keep)
    writes, skipped = [], {}
    eols = git_eol(project_root, [os.path.join(md, st.label + ".s") for st in plan.songs.values()])
    for st in plan.songs.values():
        label = st.label
        s_path, mid_path = os.path.join(md, label + ".s"), os.path.join(md, label + ".mid")
        s, mid = _read(s_path), _read(mid_path)
        if s is None or mid is None or label not in flags:
            continue
        cf = canon_flags(flags[label])
        if st.state == S.IN_SYNC:
            new = with_stamp(s, _sha1(mid), fingerprint(s), cf, eol=eols.get(s_path, ""))
        elif st.state == S.MIDI_AHEAD:
            new = with_stamp(s, NO_MID, fingerprint(s), cf, eol=eols.get(s_path, ""))
        elif st.state in S.KEPT_STATES or label in keep:
            new = with_stamp(s, _sha1(mid), SOURCE, cf, eol=eols.get(s_path, ""))
        else:
            skipped[label] = st.reason
            continue
        if new != s:
            writes.append((s_path, new))
    return writes, skipped


def write_stamps(writes: list, chunk: int = 64) -> list:
    """Write record lines in all-or-nothing chunks. Each written .s is valid on
    its own, so an interruption between chunks only leaves some songs without
    a record (the guard checks those against their .mid). Returns errors."""
    from core.sound.safe_song_save import commit_files
    errors = []
    for i in range(0, len(writes), chunk):
        try:
            commit_files(writes[i:i + chunk])
        except Exception as exc:
            errors.append(str(exc))
    return errors


def mark_source(project_root: str, label: str) -> bool:
    """Record *label*'s .s as its source against its current .mid (after the
    integrity sweep renders a .mid from the .s). Only in protected projects.
    True when a line was written."""
    if not is_installed(project_root):
        return False
    md = os.path.join(project_root, MIDI_SUBDIR)
    s, mid = _read(os.path.join(md, label + ".s")), _read(os.path.join(md, label + ".mid"))
    flags = _cfg(project_root)
    if s is None or mid is None or label not in flags:
        return False
    s_path = os.path.join(md, label + ".s")
    new = with_stamp(s, _sha1(mid), SOURCE, canon_flags(flags[label]),
                     eol=git_eol(project_root, [s_path]).get(s_path, ""))
    if new == s:
        return False
    from core.sound.safe_song_save import commit_files
    commit_files([(os.path.join(md, label + ".s"), new)])
    return True


# ─────────────────────────────────────────────────────────────────────────────
# Optional: track song .s files in git (only when the owner ticks it in setup)
# ─────────────────────────────────────────────────────────────────────────────

GITIGNORE_RULE = "sound/songs/midi/*.s"


def plan_track_s(project_root: str) -> Optional[FileChange]:
    """The .gitignore change that stops ignoring song .s files, or None when
    there is nothing to change. Never applied without the owner's choice."""
    data = _read(os.path.join(project_root, ".gitignore"))
    if data is None:
        return None
    lines = data.split(b"\n")
    out, changed = [], False
    for raw in lines:
        if raw.decode("latin-1").strip() == GITIGNORE_RULE:
            cr = b"\r" if raw.endswith(b"\r") else b""
            out.append(b"# song .s files are tracked (chosen in PorySuite-Z's song build protection setup):" + cr)
            out.append(b"# " + raw)
            changed = True
        else:
            out.append(raw)
    return FileChange(".gitignore", data, b"\n".join(out)) if changed else None


# ─────────────────────────────────────────────────────────────────────────────
# The whole setup, as the setup window runs it
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class SetupReport:
    result: GuardResult
    stamped: int = 0
    unsettled: dict = field(default_factory=dict)    # label -> why the guard will stop on it
    errors: list = field(default_factory=list)


def install_with_records(project_root: str, plan: GuardPlan, *, progress=None) -> SetupReport:
    """Write an install *plan* (plus any optional change the owner ticked and
    appended, e.g. ``plan_track_s``), then give every song a record line from
    PorySuite's content check (``song_sync``, with git history): songs whose
    files agree, whose .mid is newer, or whose .s holds the newer work are
    recorded; songs where both sides changed or the order cannot be told get no
    line, so the build stops on them until somebody chooses."""
    from core.sound import song_sync
    res = apply(project_root, plan)
    rep = SetupReport(res)
    if not res.ok:
        return rep
    try:
        sync = song_sync.classify_project(project_root, progress=progress)
    except Exception as exc:
        rep.errors.append(f"the song check failed ({exc}); songs got no record line, so the "
                          f"first build checks each one against its .mid")
        return rep
    writes, rep.unsettled = stamp_writes(project_root, sync)
    rep.errors += write_stamps(writes)
    rep.stamped = len(writes)
    for label, why in sync.unchecked:
        rep.unsettled.setdefault(label, why)
    return rep


# ─────────────────────────────────────────────────────────────────────────────
# Is this project at risk? (the Sound Editor's status line; read-only)
# ─────────────────────────────────────────────────────────────────────────────

RISK_PROTECTED = "protected"       # the guard is installed: terminal builds check songs too
RISK_SAFE = "safe"                 # not installed, and every song can be rebuilt from its .mid
RISK_AT_RISK = "at_risk"           # not installed, and some songs have music only in their .s
RISK_UNCHECKED = "unchecked"       # not installed, and the songs could not be checked yet


@dataclass
class RiskReport:
    state: str
    at_risk: dict = field(default_factory=dict)      # label -> why its music is only in the .s
    note: str = ""


def assess_risk(project_root: str) -> RiskReport:
    """Read-only: is this project protected, and if not, would a terminal
    build or ``make clean`` lose music that only a ``.s`` holds? Writes no game
    file (``song_sync.classify_project`` only reads the project; its caches
    live in PorySuite's own folder). A song that its ``.mid`` rebuilds exactly
    (in step, or only an older build) is not at risk and is not listed."""
    if is_installed(project_root):
        return RiskReport(RISK_PROTECTED)
    from core.sound import song_sync
    plan = song_sync.classify_project(project_root)
    risky = (song_sync.S_AHEAD, song_sync.S_AUTHORED, song_sync.CONFLICT, song_sync.UNKNOWN)
    at = {s.label: s.reason for s in plan.songs.values() if s.state in risky}
    if at:
        return RiskReport(RISK_AT_RISK, at, plan.note)
    if plan.unchecked:
        return RiskReport(RISK_UNCHECKED, {}, plan.note)
    return RiskReport(RISK_SAFE)
