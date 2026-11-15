"""Clone safety — find build inputs that would be missing from a fresh clone.

The problem this solves
-----------------------
A decomp project's ``.gitignore`` deliberately ignores build output: compiled
graphics (``.4bpp``, ``.gbapal``, ``.lz``), compiled audio samples
(``sound/**/*.bin``), the whole ``build/`` tree.  That is correct — those files
are regenerated from tracked sources on every ``make``.

It stops being correct the moment a file the build *reads* is ignored and has
no tracked source to rebuild it from.  Locally everything works, because the
file is sitting on disk.  Push, clone somewhere else, and ``make`` dies on a
missing input that git was told to skip.

That is not a hypothetical: a project in the wild carried a hand-dropped
``sound/direct_sound_samples/cries/dummy.bin`` with no ``.wav`` beside it.  It
built on the machine that had it and nowhere else, and the failure surfaced
months later on a second computer as a bare
``make: *** [audio_rules.mk:21: .../dummy.bin] Error 1``.

How the check works
-------------------
A fresh clone contains exactly the files git tracks.  So for every asset the
build pulls in with ``.incbin`` / ``INCBIN_*``, ask one question:

    Is it tracked, or can the build create it from things that are tracked?

The second half needs the project's build rules.  Those are *read from the
project*, not guessed:  ``Makefile`` plus every ``*.mk`` beside it are parsed
for variable assignments, explicit rules and pattern rules.  This matters —
``graphics_file_rules.mk`` stitches ``hp_numbers.4bpp`` out of three unrelated
``.4bpp`` files, and ``obi_palpak1.gbapal`` out of three ``old_pal*.gbapal``.
Any checker that assumes "``X.4bpp`` comes from ``X.png``" reports those as
broken.  They are fine, and this module knows they are fine because it read
the rule that builds them.

Why not just ask make
---------------------
``make`` is the authority on its own rules, and asking it was the first thing
tried.  It does not work here:

* ``make -n -B <target>`` happily prints a recipe for a target whose source
  does not exist, so it cannot tell a buildable file from an orphan.
* ``make -p`` (dump the rule database) needs the project's ``tools/`` to build
  first.  Those binaries are platform-specific — on a project built through
  WSL they are Linux ELF files, and make run from MSYS2 aborts while reading
  its own makefiles, long before it prints any rules.

So the rules are parsed directly.  The parse is deliberately narrow and
reports what it could not understand (:attr:`Report.unparsed_rules`) instead
of guessing.

Bias
----
A false alarm here is expensive: a warning that fires on files which are
actually fine teaches the user to dismiss the warning, and then it is worth
nothing when it finally has something true to say.  So every uncertainty
resolves toward silence — an unreadable makefile or a missing git mean "say
nothing at all".  The cost of under-reporting is one broken clone; the cost of
over-reporting is a check nobody reads.

Note which direction that pushes an unparseable rule.  *Dropping* a rule is
the loud choice, not the quiet one: the target that rule would have produced
then looks unbuildable and gets reported.  In this project every unparseable
rule writes into ``build/``, which is never ``incbin``'d, so dropping them
would have been harmless here — but a project that keeps its assets behind a
variable this parser cannot resolve would get a false alarm on every one of
them.  So a rule whose target does not fully expand is kept as a *loose*
pattern (:class:`LooseRule`): anything matching its literal parts counts as
buildable without checking prerequisites.  That can hide a real problem, and
it is the right way round to be wrong.

This module is pure — no Qt, no project objects, one ``git ls-files`` call.
"""

from __future__ import annotations

import os
import re
import subprocess
from dataclasses import dataclass, field

# Follow the codebase convention for keeping console windows off the screen on
# Windows (core/local_env.py, core/programsetup.py, core/sound/song_assembler.py).
_NO_WINDOW: int = getattr(subprocess, "CREATE_NO_WINDOW", 0)

#: Directories that are never scanned for references and never parsed for
#: rules.  ``build`` is output, ``tools`` is the toolchain's own source,
#: ``porymap`` is a vendored third-party app.
_SKIP_DIRS = frozenset({
    ".git", "build", "tools", "porymap", "node_modules", "__pycache__",
})

#: Source extensions that can carry an ``.incbin`` / ``INCBIN_*`` reference.
_SOURCE_EXTS = frozenset({".s", ".inc", ".c", ".h"})

#: Asset path inside an incbin reference.  Requires a directory separator and
#: a short extension so that stray string literals on the same line are not
#: mistaken for file paths.
_ASSET_IN_QUOTES = re.compile(r'"([A-Za-z0-9_./\-]+/[A-Za-z0-9_.\-]+\.[A-Za-z0-9]{1,8})"')

#: A line that pulls a binary blob into the ROM.
_INCBIN_LINE = re.compile(r'(?:^|[^A-Za-z0-9_])(?:\.incbin|INCBIN_[A-Z0-9]*)\s*[("]')

#: ``NAME := value`` / ``NAME = value`` / ``NAME += value`` / ``NAME ?= value``
_ASSIGN = re.compile(r'^\s*([A-Za-z_][A-Za-z0-9_]*)\s*(:=|::=|\?=|\+=|=)\s*(.*)$')

#: ``$(NAME)`` or ``${NAME}`` — only plain names, so ``$(shell ...)``,
#: ``$(wildcard ...)`` and ``$<`` are left alone and mark the rule unexpandable.
_VAR_REF = re.compile(r'\$[({]([A-Za-z_][A-Za-z0-9_]*)[)}]')

#: Any remaining ``$`` construct after expansion — means we did not understand
#: the line and must not draw conclusions from it.
_LEFTOVER_DOLLAR = re.compile(r'\$')

#: Directives that look like rules because they contain a colon.
_NOT_A_RULE = re.compile(r'^\s*(?:\.PHONY|\.SECONDARY|\.PRECIOUS|\.DELETE_ON_ERROR'
                         r'|\.NOTPARALLEL|\.SUFFIXES|\.DEFAULT|\.INTERMEDIATE'
                         r'|include|-include|sinclude|ifeq|ifneq|ifdef|ifndef'
                         r'|else|endif|define|endef|export|unexport|override'
                         r'|vpath|undefine)\b')


@dataclass(frozen=True)
class Rule:
    """One parsed makefile rule."""

    targets: tuple[str, ...]
    prereqs: tuple[str, ...]
    has_recipe: bool
    origin: str
    """``file:line`` the rule was read from, for explaining a finding."""

    @property
    def is_pattern(self) -> bool:
        return any("%" in t for t in self.targets)


@dataclass(frozen=True)
class LooseRule:
    """A rule whose target could not be fully expanded.

    Matching is done on the literal fragments only, so this deliberately
    over-matches.  Everything it matches is treated as buildable — see the
    module docstring on why that is the safe direction.
    """

    matcher: re.Pattern[str]
    origin: str

    #: A loose pattern must carry at least one literal run this long,
    #: otherwise it degenerates towards ``.*`` and would silence the entire
    #: check.  Such rules are discarded instead.
    MIN_LITERAL = 3

    @classmethod
    def build(cls, target: str, origin: str) -> "LooseRule | None":
        """Compile *target* into a loose matcher, or ``None`` if too vague."""
        # Split on every construct we could not resolve, plus `%`.
        parts = re.split(r'\$[({][^)}]*[)}]|\$.|%', target)
        if not any(len(p) >= cls.MIN_LITERAL for p in parts):
            return None
        # Whatever sat between the literal fragments was a construct we could
        # not resolve, so it becomes a wildcard.
        pattern = ".*".join(re.escape(p) for p in parts)
        try:
            return cls(re.compile(f"^{pattern}$"), origin)
        except re.error:
            return None


@dataclass(frozen=True)
class Finding:
    """A build input that a fresh clone would not have."""

    path: str
    """Repo-relative, forward slashes."""

    referenced_by: str
    """Repo-relative source file whose ``.incbin`` pulls it in."""

    ignored: bool
    """True when ``.gitignore`` is why it is untracked."""

    on_disk: bool
    """True when the file exists here — the usual case, and why it went
    unnoticed locally."""

    def describe(self) -> str:
        """One plain-English sentence for the user."""
        why = ("it is covered by a .gitignore rule"
               if self.ignored else "it was never added to git")
        where = "" if self.on_disk else " It is not on this machine either."
        return (f"{self.path} is read by the build (from "
                f"{self.referenced_by}) but {why}, and nothing in the "
                f"project's build rules can recreate it from a tracked "
                f"file.{where}")


@dataclass
class Report:
    """Result of a clone-safety check."""

    findings: list[Finding] = field(default_factory=list)

    checked: int = 0
    """How many referenced assets were examined."""

    rules_parsed: int = 0
    loose_rules: int = 0
    """Rules whose target could not be fully expanded.  These match loosely
    and mark anything they match as buildable, so they make the check
    quieter, never louder."""

    skipped_reason: str = ""
    """Non-empty when the check could not run at all — then
    :attr:`findings` is empty because nothing was learned, not because
    nothing is wrong."""

    @property
    def ran(self) -> bool:
        return not self.skipped_reason


# ───────────────────────────── git ──────────────────────────────────────────

def _git(root: str, *args: str) -> tuple[int, str]:
    """Run git in *root*.  Returns ``(returncode, stdout)``; never raises."""
    try:
        proc = subprocess.run(
            ["git", *args],
            cwd=root, capture_output=True, text=True,
            encoding="utf-8", errors="replace",
            creationflags=_NO_WINDOW,
        )
    except (OSError, ValueError):
        return 127, ""
    return proc.returncode, proc.stdout


def _tracked_files(root: str) -> set[str] | None:
    """Every path git tracks, repo-relative with forward slashes.

    ``None`` means git could not answer, which disables the whole check.
    """
    code, out = _git(root, "ls-files", "-z")
    if code != 0:
        return None
    return {p.replace("\\", "/") for p in out.split("\0") if p}


def _ignored(root: str, paths: list[str]) -> set[str]:
    """Subset of *paths* that .gitignore covers.

    Used only to word the message, so a failure here is harmless.
    """
    if not paths:
        return set()
    try:
        proc = subprocess.run(
            ["git", "check-ignore", "--stdin"],
            cwd=root, input="\n".join(paths), capture_output=True, text=True,
            encoding="utf-8", errors="replace", creationflags=_NO_WINDOW,
        )
    except (OSError, ValueError):
        return set()
    # Exit 0 = some ignored, 1 = none ignored, 128 = error.
    if proc.returncode not in (0, 1):
        return set()
    return {ln.strip().replace("\\", "/") for ln in proc.stdout.splitlines()
            if ln.strip()}


# ────────────────────────── makefile parsing ────────────────────────────────

def _logical_lines(text: str) -> list[tuple[int, str]]:
    """Join backslash continuations.  Returns ``(1-based line no, text)``."""
    out: list[tuple[int, str]] = []
    buf = ""
    start = 0
    for idx, raw in enumerate(text.splitlines(), start=1):
        if not buf:
            start = idx
        if raw.endswith("\\"):
            buf += raw[:-1] + " "
            continue
        out.append((start, buf + raw))
        buf = ""
    if buf:
        out.append((start, buf))
    return out


def _expand(value: str, variables: dict[str, str], depth: int = 0,
            active: frozenset[str] = frozenset()) -> str:
    """Substitute ``$(NAME)`` references.  Leaves anything else untouched.

    *active* holds the names currently being expanded.  A name already in
    there is left as a literal reference instead of being substituted again —
    without that, pokefirered's ``BUILD_NAME := $(BUILD_NAME)_modern`` (one of
    several such lines in ``config.mk``, each inside a conditional this parser
    does not evaluate) expands into itself until the depth cap, producing a
    multi-kilobyte string of ``_modern_modern_modern…``.
    """
    if depth > 12 or "$" not in value:
        return value

    def sub(match: re.Match[str]) -> str:
        name = match.group(1)
        if name not in variables or name in active:
            # Unknown or self-referential — keep the reference so the caller
            # can see the line was not fully understood.
            return match.group(0)
        return _expand(variables[name], variables, depth + 1,
                       active | {name})

    new = _VAR_REF.sub(sub, value)
    if new == value:
        return new
    return _expand(new, variables, depth + 1, active)


def _split_fields(text: str) -> list[str]:
    """Split on whitespace, but not inside a ``$(...)`` / ``${...}`` group.

    A plain ``.split()`` tears ``$(shell echo graphics)/mystery.4bpp`` into
    three nonsense tokens — ``$(shell``, ``echo``, ``graphics)/mystery.4bpp``
    — and the last of those looks like a real filename with a stray paren.
    Any unresolved function call with arguments hits this.
    """
    fields: list[str] = []
    buf: list[str] = []
    depth = 0
    idx = 0
    while idx < len(text):
        ch = text[idx]
        if ch == "$" and idx + 1 < len(text) and text[idx + 1] in "({":
            depth += 1
            buf.append(text[idx:idx + 2])
            idx += 2
            continue
        if depth and ch in ")}":
            depth -= 1
        elif not depth and ch.isspace():
            if buf:
                fields.append("".join(buf))
                buf = []
            idx += 1
            continue
        buf.append(ch)
        idx += 1
    if buf:
        fields.append("".join(buf))
    return fields


def _strip_comment(line: str) -> str:
    """Remove a trailing ``#`` comment from a non-recipe line."""
    out = []
    for ch in line:
        if ch == "#":
            break
        out.append(ch)
    return "".join(out)


def parse_rules(root: str) -> tuple[list[Rule], list[LooseRule]]:
    """Parse ``Makefile`` and every ``*.mk`` beside it.

    Returns ``(rules, loose_rules)``.  A rule whose target still contains an
    unexpanded ``$`` after expansion becomes a :class:`LooseRule` rather than
    being dropped, so it silences rather than invents findings.
    """
    try:
        present = os.listdir(root)
    except OSError:
        return [], []
    # Windows paths are case-insensitive, so testing for both "Makefile" and
    # "makefile" parses the same file twice and duplicates every rule in it.
    seen: set[str] = set()
    names: list[str] = []
    for candidate in ["Makefile", "makefile", "GNUmakefile",
                      *sorted(n for n in present if n.endswith(".mk"))]:
        key = candidate.lower()
        if key not in seen:
            seen.add(key)
            names.append(candidate)

    variables: dict[str, str] = {}
    raw_rules: list[dict] = []
    loose: list[LooseRule] = []

    for name in names:
        path = os.path.join(root, name)
        if not os.path.isfile(path):
            continue
        try:
            with open(path, "r", encoding="utf-8", errors="replace") as fh:
                text = fh.read()
        except OSError:
            continue

        in_define = False
        pending: list[dict] = []
        for lineno, logical in _logical_lines(text):
            # Recipe lines start with a hard tab and belong to the rule above.
            # Most of pokefirered's rules put the recipe on its own tab line
            # rather than after a `;`, and the distinction matters: a rule with
            # no prerequisites AND no recipe produces nothing, while one with
            # a tab recipe produces its target out of nowhere quite happily.
            if logical.startswith("\t"):
                if pending and logical.strip():
                    pending[-1]["has_recipe"] = True
                continue

            bare = _strip_comment(logical).strip()
            if not bare:
                continue

            # `define NAME ... endef` blocks build rules through $(eval), which
            # this parser does not evaluate.  Skip the body outright.
            if re.match(r'^define\b', bare):
                in_define = True
                continue
            if in_define:
                if re.match(r'^endef\b', bare):
                    in_define = False
                continue

            assign = _ASSIGN.match(bare)
            if assign:
                var, op, val = assign.group(1), assign.group(2), assign.group(3)
                if op in (":=", "::="):
                    # Simple assignment: make expands the right-hand side NOW,
                    # against the values held at this point.  So in
                    # `X := firered` / `X := $(X)_modern` the second line sees
                    # `firered` and stores `firered_modern`.  Expanding here
                    # rather than at use is both what make does and what stops
                    # the reference recursing into itself later.
                    val = _expand(val, variables)
                if op == "+=" and var in variables:
                    variables[var] = f"{variables[var]} {val}".strip()
                elif op == "?=" and var in variables:
                    pass
                else:
                    variables[var] = val
                continue

            if _NOT_A_RULE.match(bare):
                continue

            # A rule needs a colon that is not part of an assignment.
            if ":" not in bare:
                continue

            head, _, rest = bare.partition(":")
            if rest.startswith("="):          # `VAR :=` already handled above
                continue
            if rest.startswith(":"):          # double-colon rule
                rest = rest[1:]

            # `target: prereq ; recipe`
            prereq_text, semi, inline_recipe = rest.partition(";")

            targets_raw = _split_fields(_expand(head, variables))
            prereqs_raw = _split_fields(_expand(prereq_text, variables))

            if not targets_raw:
                continue

            origin = f"{name}:{lineno}"

            # A target we could not resolve becomes a loose matcher.  Its
            # prerequisites are not consulted at all — we have no way to know
            # what they expand to, and guessing would be the loud mistake.
            if any(_LEFTOVER_DOLLAR.search(t) for t in targets_raw):
                for target in targets_raw:
                    rule = LooseRule.build(target.replace("\\", "/"), origin)
                    if rule is not None:
                        loose.append(rule)
                pending = []
                continue

            # A prerequisite we could not resolve is different: the target is
            # known, so dropping the whole rule would report that target.
            # Treat the unresolved prerequisite as satisfied instead, and
            # remember that we did — a rule left with no prerequisites and no
            # recipe is otherwise classed as producing nothing, which would
            # put us right back to reporting the target.
            prereqs = tuple(p.replace("\\", "/") for p in prereqs_raw
                            if not _LEFTOVER_DOLLAR.search(p))
            dropped_prereq = len(prereqs) != len(prereqs_raw)

            entry = {
                "targets": tuple(t.replace("\\", "/") for t in targets_raw),
                "prereqs": prereqs,
                "has_recipe": bool(semi and inline_recipe.strip()) or dropped_prereq,
                "origin": origin,
            }
            pending = [entry]
            raw_rules.append(entry)

    return [Rule(**e) for e in raw_rules], loose          # type: ignore[arg-type]


# ──────────────────────────── buildability ──────────────────────────────────

class RuleSet:
    """Answers "could the build create this file from tracked sources?"."""

    #: Guards against a pathological rule chain.  pokefirered's deepest real
    #: chain is 3 (``x.lz`` ← ``x.4bpp`` ← ``x.png``).
    MAX_DEPTH = 12

    def __init__(self, rules: list[Rule], tracked: set[str],
                 loose: list[LooseRule] | None = None) -> None:
        self._tracked = tracked
        self._loose = loose or []
        self._explicit: dict[str, list[Rule]] = {}
        self._patterns: list[Rule] = []
        self._memo: dict[str, bool] = {}

        for rule in rules:
            # A rule with neither prerequisites nor a recipe cannot produce
            # anything.  pokefirered has several (`%.png: ;`, `%.s: ;`) whose
            # only job is to stop make searching implicit rules.  Treating
            # them as producers would declare every .png buildable from thin
            # air and hide a genuinely missing source.
            if not rule.prereqs and not rule.has_recipe:
                continue
            if rule.is_pattern:
                self._patterns.append(rule)
            else:
                for target in rule.targets:
                    self._explicit.setdefault(target, []).append(rule)

    @staticmethod
    def _stem(pattern: str, path: str) -> str | None:
        """Return the ``%`` stem if *pattern* matches *path*, else ``None``."""
        if "%" not in pattern:
            return path if pattern == path else None
        prefix, _, suffix = pattern.partition("%")
        if not path.startswith(prefix) or not path.endswith(suffix):
            return None
        if len(path) < len(prefix) + len(suffix):
            return None
        stem = path[len(prefix):len(path) - len(suffix) or None]
        return stem or None

    def can_build(self, path: str, depth: int = 0) -> bool:
        """True when *path* is tracked, or derivable from tracked files."""
        path = path.replace("\\", "/")
        if path in self._tracked:
            return True
        if depth >= self.MAX_DEPTH:
            # Too deep to be a real decomp rule chain.  Answer "yes" so the
            # file is not reported — silence beats a false alarm.
            return True
        if path in self._memo:
            return self._memo[path]

        # Mark in progress as buildable so a cyclic rule set cannot recurse
        # forever or produce a finding out of its own cycle.
        self._memo[path] = True

        result = False
        for rule in self._explicit.get(path, ()):
            if all(self.can_build(p, depth + 1) for p in rule.prereqs):
                result = True
                break

        if not result:
            for rule in self._patterns:
                for target in rule.targets:
                    stem = self._stem(target, path)
                    if stem is None:
                        continue
                    prereqs = [p.replace("%", stem) for p in rule.prereqs]
                    if all(self.can_build(p, depth + 1) for p in prereqs):
                        result = True
                        break
                if result:
                    break

        if not result:
            result = any(lr.matcher.match(path) for lr in self._loose)

        self._memo[path] = result
        return result


# ───────────────────────── reference scanning ───────────────────────────────

def _iter_source_files(root: str):
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in _SKIP_DIRS]
        for name in filenames:
            if os.path.splitext(name)[1].lower() in _SOURCE_EXTS:
                yield os.path.join(dirpath, name)


def find_references(root: str) -> dict[str, str]:
    """Map every incbin'd asset path to the first source file that reads it."""
    refs: dict[str, str] = {}
    for abs_path in _iter_source_files(root):
        rel_src = os.path.relpath(abs_path, root).replace("\\", "/")
        try:
            with open(abs_path, "r", encoding="utf-8", errors="replace") as fh:
                for line in fh:
                    if "incbin" not in line and "INCBIN" not in line:
                        continue
                    if not _INCBIN_LINE.search(line):
                        continue
                    for asset in _ASSET_IN_QUOTES.findall(line):
                        refs.setdefault(asset.replace("\\", "/"), rel_src)
        except OSError:
            continue
    return refs


# ──────────────────────────────── entry point ───────────────────────────────

def check(root: str) -> Report:
    """Look for build inputs a fresh clone of *root* would not have."""
    report = Report()

    if not os.path.isdir(os.path.join(root, ".git")):
        report.skipped_reason = "not a git repository"
        return report

    tracked = _tracked_files(root)
    if tracked is None:
        report.skipped_reason = "git could not list tracked files"
        return report

    rules, loose = parse_rules(root)
    report.rules_parsed = len(rules)
    report.loose_rules = len(loose)
    if not rules:
        report.skipped_reason = "no build rules could be read"
        return report

    refs = find_references(root)
    report.checked = len(refs)

    ruleset = RuleSet(rules, tracked, loose)
    broken = {asset: src for asset, src in refs.items()
              if not ruleset.can_build(asset)}

    ignored = _ignored(root, sorted(broken))
    for asset in sorted(broken):
        report.findings.append(Finding(
            path=asset,
            referenced_by=broken[asset],
            ignored=asset in ignored,
            on_disk=os.path.isfile(os.path.join(root, asset)),
        ))

    return report


# ───────────────────────────── repair ───────────────────────────────────────

_EXCEPTION_HEADER = (
    "# Build inputs that have no tracked source to rebuild them from.\n"
    "# A rule above ignores these as build output, which is right for every\n"
    "# other file it covers — but nothing in the project can recreate these,\n"
    "# so a fresh clone has no copy and `make` stops on the first one.\n"
    "# Un-ignored by PorySuite's clone-safety check:\n"
)


def write_gitignore_exceptions(root: str, paths: list[str]) -> list[str]:
    """Append ``!path`` exceptions so *paths* can be committed.

    Append-only and idempotent, following ``_ensure_gitignore_entry`` in
    ``ui/dialogs/newproject.py``: nothing already in the file is reordered,
    rewritten or removed, because a ``.gitignore`` is the project owner's
    document and the request here is only to add.

    Returns the paths an exception was actually written for.
    """
    if not paths:
        return []
    gi_path = os.path.join(root, ".gitignore")
    try:
        existing = ""
        if os.path.isfile(gi_path):
            with open(gi_path, "r", encoding="utf-8",
                      errors="surrogateescape") as fh:
                existing = fh.read()
    except OSError:
        return []

    already = {ln.strip().lstrip("!") for ln in existing.splitlines()
               if ln.strip().startswith("!")}
    todo = [p for p in paths if p not in already]
    if not todo:
        return []

    block = []
    if existing and not existing.endswith("\n"):
        block.append("")
    block.append("")
    block.append(_EXCEPTION_HEADER.rstrip("\n"))
    block.extend(f"!{p}" for p in todo)
    block.append("")

    try:
        with open(gi_path, "a", encoding="utf-8", errors="surrogateescape",
                  newline="\n") as fh:
            fh.write("\n".join(block))
    except OSError:
        return []
    return todo


def stage(root: str, paths: list[str]) -> tuple[list[str], str]:
    """``git add -f`` each of *paths*.  Returns ``(staged, error)``.

    Staging only.  Committing is the project owner's call, and the files
    involved may be large binaries they want to look at first.
    """
    staged: list[str] = []
    for path in paths:
        code, _ = _git(root, "add", "-f", "--", path)
        if code == 0:
            staged.append(path)
        else:
            return staged, f"git add failed for {path}"
    return staged, ""


@dataclass
class RepairResult:
    """Outcome of :func:`repair`."""

    unignored: list[str] = field(default_factory=list)
    staged: list[str] = field(default_factory=list)
    error: str = ""


def repair(root: str, findings: list[Finding]) -> RepairResult:
    """Un-ignore and stage every file in *findings*.

    Staging, never committing: these are often large binaries, and whether
    they belong in the history is the project owner's call, not a side effect
    of pressing Push.
    """
    paths = [f.path for f in findings]
    ignored = [f.path for f in findings if f.ignored]
    unignored = write_gitignore_exceptions(root, ignored)
    staged, error = stage(root, paths)
    return RepairResult(unignored=unignored, staged=staged, error=error)


def warning_html(findings: list[Finding], limit: int = 6) -> str:
    """The warning shown before a push, as rich text.

    Lives here rather than in the dialog so it can be tested without a Qt
    main window — importing ``ui/mainwindow.py`` under the test suite kills
    the interpreter outright.
    """
    shown = findings[:limit]
    bullets = "".join(f"<li><code>{f.path}</code></li>" for f in shown)
    if len(findings) > len(shown):
        bullets += f"<li>… and {len(findings) - len(shown)} more</li>"
    return (
        f"<b>{len(findings)} file(s) the build needs are not in git.</b>"
        f"<ul>{bullets}</ul>"
        f"<p>They are on this computer, so building here works. Anyone who "
        f"clones this project — including you on another machine — gets no "
        f"copy, and <code>make</code> stops on the first one.</p>"
        f"<p style='color:#aaa; font-size:11px;'>{findings[0].describe()}</p>")


def repair_summary(result: RepairResult) -> str:
    """Plain-English account of what :func:`repair` just did."""
    lines = []
    if result.unignored:
        lines.append(
            f"Added {len(result.unignored)} exception(s) to .gitignore so "
            f"these files can be committed. Nothing already in that file was "
            f"changed or reordered.")
    if result.staged:
        lines.append(f"Staged {len(result.staged)} file(s).")
    if result.error:
        lines.append(f"Stopped early: {result.error}")
    lines.append("Commit them, then push — the push dialog did not open.")
    return "\n\n".join(lines)
