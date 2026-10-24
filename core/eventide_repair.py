"""Find and repair the damage older EVENTide builds left in a project's map scripts.

Five writer/parser defects were fixed on 2026-10-24 (see `docs/BUGS.md`). Three of
them never reached disk, because a save only rewrites a map's event pages, its
`*_MapScripts::` table, generated scene scripts and move routes edited through the
Move Route dialog — everything else in the file is copied through verbatim. Two did
reach disk, and this module finds and repairs what they left behind.

Design rules this module follows, and must keep following:

* **Every detector was calibrated against a clean copy of the game.** A pattern is
  only treated as damage when the clean copy contains ZERO of it. That is why
  `goto` followed by `end` is deliberately NOT a detector — vanilla does it 432
  times, so an "unreachable end" sweep that included it would rewrite the whole
  project. Re-measure before adding a detector.
* **Nothing is inferred from absence.** A command missing versus vanilla is the
  user's own edit, not damage. This project has removed all 59 `goto_if_questlog`
  lines along with the whole Quest Log system, and a repair that "restored" them
  would destroy real work.
* **Indentation is only repaired in files already proven damaged** by a structural
  finding. 27 undamaged maps in this project are also unindented — several were
  unindented upstream — so indentation on its own is not evidence of anything.
* **Ambiguous damage is reported, never rewritten.** The injected `lock` / `release`
  lines cannot be told apart from the user's own edits by pattern alone, so they are
  listed for review and left exactly where they are.

The repair is idempotent: running it twice changes nothing the second time.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

# A `Label::` at the start of a line (possibly indented).
_LABEL_RE = re.compile(r'^([A-Za-z0-9_]+)::')

# Assembler directives that terminate a DATA table rather than a script. A
# `<Map>_MapScripts::` table closes with `.byte 0`; a `map_script_2` table
# (OnFrame / OnWarp) closes with `.2byte 0`. Nothing may follow them.
_DATA_DIRECTIVES = ('.byte', '.2byte', '.4byte')

# Commands after which an `end` is unreachable. `goto` / `gotostd` are NOT here
# on purpose — see the module docstring.
_HARD_TERMINATORS = frozenset({'end', 'return'})

KIND_MESSAGE = 'message_to_msgbox'
KIND_END_DATA = 'stray_end_after_data'
KIND_END_DEAD = 'stray_end_unreachable'
KIND_INDENT = 'lost_indentation'
KIND_REVIEW_LOCK = 'review_injected_lock'

#: Kinds this module will rewrite. Anything else is report-only.
REPAIRABLE = (KIND_MESSAGE, KIND_END_DATA, KIND_END_DEAD, KIND_INDENT)

_TITLES = {
    KIND_MESSAGE: 'Dialogue closing too early (message turned into msgbox)',
    KIND_END_DATA: 'Stray "end" after a map-script table',
    KIND_END_DEAD: 'Stray "end" that can never run',
    KIND_INDENT: 'Lost tab indentation',
    KIND_REVIEW_LOCK: 'Possible injected lock/release — needs your eye',
}


def title_for(kind: str) -> str:
    """Plain-English heading for a finding kind."""
    return _TITLES.get(kind, kind)


@dataclass
class Finding:
    """One thing wrong in one file."""
    kind: str
    map_name: str
    path: Path
    line: int           # 1-based, into the file as it is on disk
    detail: str

    @property
    def repairable(self) -> bool:
        return self.kind in REPAIRABLE


@dataclass
class RepairResult:
    repaired: list[Finding] = field(default_factory=list)
    skipped: list[Finding] = field(default_factory=list)
    files_written: list[Path] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)


def _is_code(stripped: str) -> bool:
    """True for a line that is an actual command, not a blank or a comment."""
    return bool(stripped) and not stripped.startswith('@')


def _head(stripped: str) -> str:
    return stripped.split(None, 1)[0] if stripped else ''


def _scan_lines(lines: list[str], map_name: str, path: Path) -> list[Finding]:
    """Find every structural problem in one already-read file."""
    found: list[Finding] = []

    # Walk once, tracking the previous CODE line and the label we are inside.
    in_label = False
    prev_code = None          # (index, stripped)
    for i, raw in enumerate(lines):
        s = raw.strip()
        if _LABEL_RE.match(s):
            in_label = True
            prev_code = None
            continue
        if not _is_code(s):
            continue
        if in_label and prev_code is not None:
            prev = prev_code[1]
            if s == 'end':
                if _head(prev) in _DATA_DIRECTIVES:
                    found.append(Finding(
                        KIND_END_DATA, map_name, path, i + 1,
                        f'"end" written after the table terminator "{prev}" — '
                        f'a stray byte past the end of a data table'))
                elif prev in _HARD_TERMINATORS:
                    found.append(Finding(
                        KIND_END_DEAD, map_name, path, i + 1,
                        f'"end" directly after "{prev}" — it can never run'))
            # `msgbox <label>` + `waitmessage` is the fingerprint of a `message`
            # command that was rewritten. msgbox already waits internally, so a
            # bare waitmessage under it is a leftover. Only the no-type form is
            # matched: a typed msgbox followed by waitmessage is not something
            # the old writer could produce.
            if s == 'waitmessage' and _head(prev) == 'msgbox':
                arg = prev.split(None, 1)[1].strip() if ' ' in prev else ''
                if arg and ',' not in arg:
                    found.append(Finding(
                        KIND_MESSAGE, map_name, path, prev_code[0] + 1,
                        f'"msgbox {arg}" should be "message {arg}" — the text '
                        f'box closes before the next command runs'))
        prev_code = (i, s)

    return found


def _indent_findings(lines: list[str], map_name: str,
                     path: Path) -> list[Finding]:
    """Body lines inside a label block that lost their leading tab.

    ``@`` comments are skipped. Vanilla keeps them flush-left 96 times against
    9 indented, so indenting one would invent a difference from the clean copy
    rather than remove one — and most of these comments introduce the NEXT
    label, where flush-left is what reads correctly.
    """
    out: list[Finding] = []
    in_label = False
    for i, raw in enumerate(lines):
        s = raw.strip()
        if _LABEL_RE.match(s):
            in_label = True
            continue
        if not in_label or not s or s.startswith('@'):
            continue
        if raw[:1] not in ('\t', ' '):
            out.append(Finding(KIND_INDENT, map_name, path, i + 1,
                               f'"{s[:48]}" lost its leading tab'))
    return out


def _read(path: Path) -> list[str]:
    return path.read_text(encoding='utf-8').splitlines()


def scan(project_root: str | Path) -> list[Finding]:
    """Return every finding across the project's map scripts. Read-only.

    Indentation findings are only reported for files that ALSO have a
    structural finding — an unindented file on its own proves nothing.
    """
    root = Path(project_root)
    maps = root / 'data' / 'maps'
    results: list[Finding] = []
    if not maps.is_dir():
        return results

    for map_dir in sorted(maps.iterdir()):
        path = map_dir / 'scripts.inc'
        if not path.is_file():
            continue
        try:
            lines = _read(path)
        except Exception:
            continue
        structural = _scan_lines(lines, map_dir.name, path)
        results.extend(structural)
        if structural:
            results.extend(_indent_findings(lines, map_dir.name, path))
    return results


def damaged_maps(findings: list[Finding]) -> list[str]:
    """Names of the maps with at least one structural finding."""
    return sorted({f.map_name for f in findings if f.kind != KIND_INDENT})


def summarize(findings: list[Finding]) -> dict[str, int]:
    """kind -> count."""
    out: dict[str, int] = {}
    for f in findings:
        out[f.kind] = out.get(f.kind, 0) + 1
    return out


def _apply(lines: list[str], findings: list[Finding],
           kinds: set[str]) -> tuple[list[str], list[Finding]]:
    """Return (new_lines, applied) for one file.

    Line numbers in *findings* refer to the ORIGINAL file, so deletions are
    collected first and applied in one pass rather than one at a time.
    """
    drop: set[int] = set()
    rewrite: dict[int, str] = {}
    indent: set[int] = set()
    applied: list[Finding] = []

    for f in findings:
        if f.kind not in kinds:
            continue
        idx = f.line - 1
        if not (0 <= idx < len(lines)):
            continue
        raw, s = lines[idx], lines[idx].strip()
        if f.kind in (KIND_END_DATA, KIND_END_DEAD):
            if s != 'end':
                continue                      # file moved under us — skip
            drop.add(idx)
        elif f.kind == KIND_MESSAGE:
            if _head(s) != 'msgbox':
                continue
            lead = raw[:len(raw) - len(raw.lstrip())]
            rewrite[idx] = lead + 'message' + s[len('msgbox'):]
        elif f.kind == KIND_INDENT:
            if not s or raw[:1] in ('\t', ' '):
                continue
            indent.add(idx)
        else:
            continue
        applied.append(f)

    out: list[str] = []
    for i, raw in enumerate(lines):
        if i in drop:
            continue
        if i in rewrite:
            raw = rewrite[i]
        if i in indent:
            raw = '\t' + raw
        out.append(raw)
    return out, applied


def repair(project_root: str | Path, kinds=None,
           findings: list[Finding] | None = None) -> RepairResult:
    """Apply the repairable findings and write the files back.

    *kinds* limits what is touched (default: everything in ``REPAIRABLE``).
    Report-only kinds are never applied even if asked for.
    Files are written with LF endings, matching the rest of the project.
    """
    wanted = set(kinds) if kinds is not None else set(REPAIRABLE)
    wanted &= set(REPAIRABLE)

    result = RepairResult()
    all_findings = scan(project_root) if findings is None else list(findings)

    by_file: dict[Path, list[Finding]] = {}
    for f in all_findings:
        if f.kind in wanted:
            by_file.setdefault(f.path, []).append(f)
        else:
            result.skipped.append(f)

    for path, items in by_file.items():
        try:
            lines = _read(path)
        except Exception as exc:
            result.errors.append(f'{path.name}: could not read ({exc})')
            continue
        new_lines, applied = _apply(lines, items, wanted)
        if not applied or new_lines == lines:
            continue
        try:
            path.write_text('\n'.join(new_lines) + '\n',
                            encoding='utf-8', newline='\n')
        except Exception as exc:
            result.errors.append(f'{path.name}: could not write ({exc})')
            continue
        result.files_written.append(path)
        result.repaired.extend(applied)

    return result


# ── Optional: compare against a clean copy of the game ───────────────────────

def review_against_reference(project_root: str | Path,
                             reference_root: str | Path,
                             only_maps: list[str] | None = None
                             ) -> list[Finding]:
    """List `lock` / `lockall` / `release` lines a map has that the clean copy
    does not. REPORT ONLY — these are never rewritten.

    A hit is NOT proof of damage. The same commits that saved through EVENTide
    also carry a lot of hand-written work, so some of these are the user's own
    edits. This exists so a human can look at a short list instead of the whole
    project. Maps with no counterpart in the reference (new maps) are skipped,
    because there is nothing to compare them to.
    """
    root, ref = Path(project_root), Path(reference_root)
    watch = ('lock', 'lockall', 'release', 'releaseall')
    out: list[Finding] = []

    def blocks(path: Path) -> dict[str, list[str]]:
        found: dict[str, list[str]] = {}
        cur = None
        if not path.is_file():
            return found
        for ln in path.read_text(encoding='utf-8').splitlines():
            s = ln.strip()
            m = _LABEL_RE.match(s)
            if m:
                cur = m.group(1)
                found[cur] = []
            elif cur and _is_code(s):
                found[cur].append(s)
        return found

    maps_dir = root / 'data' / 'maps'
    if not maps_dir.is_dir():
        return out
    for map_dir in sorted(maps_dir.iterdir()):
        if only_maps is not None and map_dir.name not in only_maps:
            continue
        live_path = map_dir / 'scripts.inc'
        ref_path = ref / 'data' / 'maps' / map_dir.name / 'scripts.inc'
        if not live_path.is_file() or not ref_path.is_file():
            continue
        live, clean = blocks(live_path), blocks(ref_path)
        for label, body in live.items():
            if label not in clean:
                continue          # a block the user added — nothing to compare
            before = clean[label]
            for cmd in watch:
                extra = body.count(cmd) - before.count(cmd)
                if extra > 0:
                    out.append(Finding(
                        KIND_REVIEW_LOCK, map_dir.name, live_path, 0,
                        f'{label}: {extra} more "{cmd}" than the clean copy'))
    return out
