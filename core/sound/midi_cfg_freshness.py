"""Keep a harmless change to midi.cfg from rebuilding every song.

The game's build makes every song's ``.s`` depend on the one shared settings
file (``audio_rules.mk``: ``<song>.s: <song>.mid midi.cfg``), and ``make``
judges by modification time only. So ANY rewrite of ``midi.cfg`` — a git
checkout or pull (which rewrites it, often with Windows line endings and the
same content), PorySuite saving one song's flags — makes every song older than
it, and the next ``make`` re-runs mid2agb on all of them (2026-11-15: 371
songs rebuilt, not one of them changed).

PorySuite used to answer that by moving ``midi.cfg``'s clock back without
looking at it — "now minus an hour" in some places (not enough: songs built
two days earlier were still older), "below the oldest song" in others. Neither
looked at what changed, so a REAL flag change made by another program could
be hidden from the build as easily as a harmless one.

This module decides by content. A song's ``.s`` header records every flag the
project uses: ``-G`` voicegroup (``_grp``), ``-V`` master volume (``_mvl``),
``-R`` reverb (``_rev``), ``-P`` priority (``_pri``), ``-E`` exact gate
(``_exg``), and so on. For each song a ``midi.cfg`` change would make stale,
mid2agb itself writes the header for that song's CURRENT flags (on a tiny
stand-in MIDI — the header depends only on the flags), and it is compared with
the header in the song's ``.s``:

* the same — the ``.s`` already reflects the current settings; the change did
  not affect this song;
* different (or mid2agb refuses the flags) — the song genuinely needs
  rebuilding.

Then ``midi.cfg``'s time is set just below every song, and each song that
genuinely needs rebuilding gets its ``.s`` time set below ``midi.cfg``, so
``make`` rebuilds exactly those and nothing else. ``.mid`` changes are not
judged here: a song whose ``.mid`` is newer than its ``.s`` is left for
``make`` (and for midi_ownership's rules about other editors' files).

This only helps when PorySuite runs after the change (project load, its file
watcher, its Make button, its own saves). A plain ``make`` from a terminal right
after a pull, with PorySuite closed, is decided by the Makefile alone. Which of
three cases a project is in is read from its own song rule
(``audio_rule_mode``): upstream pret's rule (FireRed, Emerald,
pokeemerald-expansion) gets the content check above; a project whose rule
makes each song depend on its OWN flags has the problem fixed in the build,
for every ``make``, and PorySuite leaves midi.cfg alone; any rule PorySuite
does not recognise is left alone too (only songs written in PorySuite, whose
``.mid`` is a placeholder, are protected), because guessing could hide a
real change.
"""

from __future__ import annotations

import io
import logging
import os
import re
from dataclasses import dataclass, field

_log = logging.getLogger("SoundEditor.MidiCfgFreshness")

MIDI_SUBDIR = os.path.join("sound", "songs", "midi")
_EQU = re.compile(r"^\s*\.equ\s+\w+?_(grp|pri|rev|mvl|key|tbs|exg|cmp)\s*,\s*(.+?)\s*$")
_LABEL = re.compile(r"^\s*\.equ\s+(\w+)_grp\s*,")
_HEADER_CACHE: dict = {}

#: mid2agb options whose effect shows in the song header (tools/mid2agb/
#: main.cpp, identical option set in pret FireRed, pret Emerald and
#: pokeemerald-expansion): -E _exg, -G _grp, -N _cmp, -P _pri, -R _rev,
#: -V _mvl, -X _tbs. -L renames every label and is checked through the label.
#: Any OTHER option (a fork's own) cannot be verified from the .s, so a song
#: whose line has one is never assumed current.
_HEADER_OPTIONS = frozenset("EGNPRVX")

# ── What kind of song build rule a project has ─────────────────────────────
WHOLE_CFG = "whole_cfg"     # upstream pret: each song depends on all of midi.cfg
PER_SONG = "per_song"       # each song depends on its own flags (parse-time copies)
UNKNOWN = "unknown"         # anything else: PorySuite does not touch midi.cfg


def _strip_make_comments(text: str) -> str:
    return "\n".join(l for l in text.replace("\r", "").split("\n") if not l.lstrip().startswith("#"))


def audio_rule_mode(project_root: str) -> str:
    """Which song build rule this project's Makefile really uses, read from
    the rule itself (never from what happens to be on disk in build/):

    * WHOLE_CFG — the Makefile includes audio_rules.mk, songs are built in
      sound/songs/midi, and the song rule's prerequisites are the song's .mid
      and $(MID_CFG_PATH) = sound/songs/midi/midi.cfg (extra prerequisites,
      such as pokeemerald-expansion's battle config, are fine): pret FireRed,
      pret Emerald, pokeemerald-expansion.
    * PER_SONG — the same, but each song depends on its own flags copy
      $(MID_FLAGS_DIR)/$1.flags, refreshed at parse time by a $(shell) loop.
    * UNKNOWN — anything else (no audio_rules.mk, not included, rules moved,
      another dependency shape, a recipe-based flags rule). PorySuite then
      leaves midi.cfg's time alone: guessing could hide a real change.

    Read fresh on every call, so it is always this project's answer."""
    mk = os.path.join(project_root, "Makefile")
    ar = os.path.join(project_root, "audio_rules.mk")
    try:
        makefile = _strip_make_comments(open(mk, encoding="utf-8", errors="replace").read())
        rules = _strip_make_comments(open(ar, encoding="utf-8", errors="replace").read())
    except OSError:
        return UNKNOWN
    if not re.search(r"^\s*-?include\s+audio_rules\.mk\s*$", makefile, re.M):
        return UNKNOWN
    if not re.search(r"^MID_SUBDIR\s*[:?]?=\s*sound/songs/midi\s*$", makefile, re.M):
        return UNKNOWN
    if not re.search(r"^MID_ASM_DIR\s*:?=\s*\$\(MID_SUBDIR\)\s*$", rules, re.M):
        return UNKNOWN
    m = re.search(r"^define\s+MID_RULE\s*$(.*?)^endef\s*$", rules, re.M | re.S)
    if not m:
        return UNKNOWN
    target = next((l for l in m.group(1).split("\n") if l.startswith("$(MID_ASM_DIR)/$1.s:")), None)
    if target is None:
        return UNKNOWN
    prereqs = target.split(":", 1)[1].split("|")[0].split()
    if "$(MID_SUBDIR)/$1.mid" not in prereqs:
        return UNKNOWN
    if "$(MID_FLAGS_DIR)/$1.flags" in prereqs and "$(MID_CFG_PATH)" not in prereqs:
        parse_time = re.search(r"^\$\(shell [^\n]*\$\(MID_FLAGS_DIR\)[^\n]*while read", rules, re.M)
        recipe = re.search(r"^\$\(MID_FLAGS_DIR\)/\$1\.flags\s*:", m.group(1), re.M)
        return PER_SONG if parse_time and not recipe else UNKNOWN
    if ("$(MID_CFG_PATH)" in prereqs
            and re.search(r"^MID_CFG_PATH\s*:?=\s*\$\(MID_SUBDIR\)/midi\.cfg\s*$", rules, re.M)):
        return WHOLE_CFG
    return UNKNOWN


def cfg_flags(text: str) -> dict:
    """{song label: tuple of flags} from midi.cfg text. Line endings and
    spacing do not matter; blank lines and comments are skipped."""
    out = {}
    for line in text.replace("\r", "").split("\n"):
        line = line.split("#", 1)[0].strip()
        head, sep, rest = line.partition(":")
        if not sep or not head.strip().endswith(".mid"):
            continue
        out[head.strip()[:-4]] = tuple(rest.split())
    return out


def label_of(s_text: str):
    """The song's own label in its .s (the prefix of its header equates)."""
    for line in s_text.splitlines()[:40]:
        m = _LABEL.match(line)
        if m:
            return m.group(1)
    return None


def header_of(s_text: str) -> dict:
    """The song-header equates of an .s, by kind (grp, mvl, ...)."""
    out = {}
    for line in s_text.splitlines()[:40]:
        m = _EQU.match(line)
        if m:
            out[m.group(1)] = re.sub(r"\s+", "", m.group(2))
    return out


def _probe_midi() -> bytes:
    import mido
    mf = mido.MidiFile(type=1, ticks_per_beat=96)
    tr = mido.MidiTrack()
    tr.append(mido.Message("note_on", note=60, velocity=100, time=0))
    tr.append(mido.Message("note_off", note=60, velocity=0, time=96))
    mf.tracks.append(tr)
    buf = io.BytesIO()
    mf.save(file=buf)
    return buf.getvalue()


def header_for_flags(project_root: str, flags: tuple):
    """The header mid2agb writes for *flags*, or None if mid2agb refuses
    them. Run once per distinct set of flags."""
    key = (os.path.abspath(project_root), tuple(flags))
    if key not in _HEADER_CACHE:
        from core.sound.safe_song_save import render_s_with_mid2agb
        try:
            s = render_s_with_mid2agb(project_root, "probe", _probe_midi(), list(flags))
            _HEADER_CACHE[key] = header_of(s)
        except Exception as exc:
            _log.info("mid2agb refused flags %s: %s", " ".join(flags), exc)
            _HEADER_CACHE[key] = None
    return _HEADER_CACHE[key]


def song_reflects_cfg(project_root: str, label: str, flags: tuple) -> bool:
    """True when <label>.s already carries what its current flags give: the
    header mid2agb writes for them, and the label (-L, else the song's name).
    An option whose effect cannot be seen in the .s makes this False — the
    song is then left for the build, never assumed current."""
    want_label = label
    header_flags = []
    for f in flags:
        if len(f) < 2 or f[0] != "-":
            return False
        k = f[1].upper()
        if k == "L":
            want_label = f[2:]
            continue
        if k not in _HEADER_OPTIONS:
            return False
        header_flags.append(f)
    want = header_for_flags(project_root, tuple(header_flags))
    if not want:
        return False
    s_path = os.path.join(project_root, MIDI_SUBDIR, label + ".s")
    try:
        text = open(s_path, "rb").read().decode("latin-1")
    except OSError:
        return False
    return header_of(text) == want and label_of(text) == want_label


@dataclass
class SettleReport:
    checked: int = 0
    current: list = field(default_factory=list)       # cfg change did not affect them
    rebuild: list = field(default_factory=list)       # genuinely need rebuilding
    cfg_moved: bool = False
    mode: str = WHOLE_CFG
    protected: list = field(default_factory=list)     # UNKNOWN mode: placeholder songs kept

    def summary(self) -> str:
        if self.mode == PER_SONG:
            return "the build checks each song's own flags; midi.cfg left as it is"
        if self.mode == UNKNOWN:
            return ("this project's song build rule is not one PorySuite recognises; midi.cfg "
                    f"left as it is ({len(self.protected)} PorySuite-written song(s) protected)")
        if not self.checked:
            return "midi.cfg is older than every song; nothing to settle"
        return (f"{len(self.current)} song(s) unaffected by the midi.cfg change, "
                f"{len(self.rebuild)} left to rebuild")


def settle_midi_cfg(project_root: str) -> SettleReport:
    """Make the next build rebuild only the songs a midi.cfg change really
    affects. See the module docstring. Writes only timestamps — and nothing at
    all when the build already tracks each song's flags itself."""
    report = SettleReport(mode=audio_rule_mode(project_root))
    if report.mode == PER_SONG:
        return report
    if report.mode == UNKNOWN:
        return _protect_placeholders_only(project_root, report)
    md = os.path.join(project_root, MIDI_SUBDIR)
    cfg = os.path.join(md, "midi.cfg")
    if not os.path.isfile(cfg):
        return report
    try:
        cfg_mt = os.path.getmtime(cfg)
        flags = cfg_flags(open(cfg, "rb").read().decode("utf-8", "replace"))
    except OSError:
        return report
    s_times = {}
    for label in flags:
        p = os.path.join(md, label + ".s")
        if os.path.isfile(p):
            s_times[label] = os.path.getmtime(p)
    stale = [lb for lb, t in s_times.items() if t <= cfg_mt]
    if not stale:
        return report
    # (Without mid2agb no header can be made: every song that is not a
    # placeholder then counts as needing a rebuild — nothing is hidden.)
    from core.sound.midi_ownership import is_porysuite_written
    report.checked = len(stale)
    for label in stale:
        mid = os.path.join(md, label + ".mid")
        if not os.path.isfile(mid) or is_porysuite_written(os.path.join(md, label + ".s"), mid):
            # The .s is this song's only source (written in PorySuite; the
            # .mid is scaffolding). make must NEVER rebuild it: mid2agb on a
            # placeholder replaces the composition with an empty header.
            report.current.append(label)
        elif song_reflects_cfg(project_root, label, flags[label]):
            report.current.append(label)
        else:
            report.rebuild.append(label)
    below_all = min(s_times.values()) - 2
    try:
        for label in report.rebuild:
            mid_mt = os.path.getmtime(os.path.join(md, label + ".mid"))
            if s_times[label] < mid_mt:
                continue        # its newer .mid rebuilds it anyway; leave its time alone
            t = below_all - 2
            os.utime(os.path.join(md, label + ".s"), (t, t))
        os.utime(cfg, (below_all, below_all))
        report.cfg_moved = True
    except OSError as exc:
        _log.warning("Could not settle midi.cfg timestamps: %s", exc)
    _log.info("midi.cfg settled: %s%s", report.summary(),
              f" ({', '.join(report.rebuild[:8])})" if report.rebuild else "")
    return report


def _protect_placeholders_only(project_root: str, report: SettleReport) -> SettleReport:
    """UNKNOWN build rule: midi.cfg's time is not touched (nothing is known
    about what depends on it). The one thing done is what can never hide a
    change: a song whose .mid is a placeholder (written in PorySuite; its .s
    is the only copy) gets its .s dated now when it is not already newer than
    its .mid and midi.cfg, so no build can regenerate it from the placeholder."""
    from core.sound.midi_ownership import is_porysuite_written
    md = os.path.join(project_root, MIDI_SUBDIR)
    cfg = os.path.join(md, "midi.cfg")
    try:
        cfg_mt = os.path.getmtime(cfg) if os.path.isfile(cfg) else 0.0
        names = sorted(fn[:-2] for fn in os.listdir(md) if fn.endswith(".s"))
    except OSError:
        return report
    for label in names:
        s_path = os.path.join(md, label + ".s")
        mid = os.path.join(md, label + ".mid")
        if not is_porysuite_written(s_path, mid):
            continue
        try:
            newest_dep = max(cfg_mt, os.path.getmtime(mid))
            if os.path.getmtime(s_path) <= newest_dep:
                import time
                t = max(time.time(), newest_dep + 1)
                os.utime(s_path, (t, t))
                report.protected.append(label)
        except OSError:
            pass
    _log.info("midi.cfg not settled: %s", report.summary())
    return report


def settle_for_cfg_path(cfg_path: str) -> SettleReport:
    """settle_midi_cfg for the project that holds *cfg_path*
    (<root>/sound/songs/midi/midi.cfg)."""
    root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(
        os.path.abspath(cfg_path)))))
    return settle_midi_cfg(root)
