"""Where the game reveals each Fame Checker entry — read-only.

A `famechecker` macro call is what turns an entry from hidden into readable.
This module finds every one of them and says what it points at, so the editor
can answer three questions the source cannot be eyeballed for:

* **Does this unlock point at something that exists?** Both engine handlers
  bound-check and then do *nothing* — no error, no assert::

      SetFlavorTextFlagFromSpecialVars:
          if (person < NUM_FAMECHECKER_PERSONS && index < 6) { … }
      UpdatePickStateFromSpecialVar8005:
          if (person < NUM_FAMECHECKER_PERSONS && state < 3) { … }

  So an out-of-range unlock runs, prints its dialogue, and silently never
  fires. A project that renumbers people or adds a seventh entry gets scripts
  that look right and quietly do nothing — invisible in game AND in the source.
  This is the highest-value thing here and it needs no UI to be useful.

* **Is any entry unreachable?** An entry no call unlocks can never be read: its
  text is dead. That is the defect.

* **Are two calls pointing at the same entry?** That is NOT a defect — see
  `duplicates`. Two people mentioning the same fact is ordinary design.

Read-only by construction: nothing here writes. Editing these calls means
editing assembler inside files Porymap and EVENTide also own, which is its own
piece of work with its own writer discipline.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field


# The macro (`asm/macros/event.inc`):
#     famechecker person:req, index:req, function=SetFlavorTextFlagFromSpecialVars
# GAS accepts the third argument positionally OR as `function=NAME`. This
# project uses the positional form exclusively — zero calls use the keyword —
# so a parser built only against `function=` would misfile every three-argument
# call as a two-argument one. Both forms are handled.
_CALL_RE = re.compile(
    r"^[ \t]*famechecker[ \t]+([^\n@]*?)[ \t]*(?:@[^\n]*)?$", re.MULTILINE)

_LABEL_RE = re.compile(r"^([A-Za-z_]\w*)::", re.MULTILINE)

# A label definition may use one colon or two; only `::` is exported, but a
# single-colon local label is still a definition and must not be mistaken for a
# reference to itself.
_LABEL_DEF_RE = re.compile(r"^([A-Za-z_]\w*):{1,2}", re.MULTILINE)
_IDENT_RE = re.compile(r"[A-Za-z_]\w*")

# A script label can be named from anywhere a project keeps source, so the
# whole tree is walked rather than a fixed list of directories. Missing one
# reports a LIVE script as dead, which is the expensive direction, and a
# project is free to organise itself however it likes.
_REF_EXTS = (".inc", ".s", ".c", ".h", ".json")

# Not source: build output, VCS internals, and the editor's own state. Skipped
# for speed, and because a stale object file naming a deleted script would
# otherwise keep it looking alive.
_SKIP_DIRS = {".git", ".github", "build", "__pycache__", ".vscode", ".idea"}


@dataclass
class UnlockCall:
    """One `famechecker` call site."""
    person: str = ""            # as written: a constant, a number, or a VAR_*
    index: str = ""             # flavor-text index, or an FCPICKSTATE_* value
    function: str = ""          # the handler, resolved or defaulted
    kind: str = ""              # "flavor" | "pickstate" | "unknown"
    file_rel: str = ""
    line: int = 0
    label: str = ""             # the script label this call sits inside
    person_index: int = -1      # resolved, or -1 when it could not be resolved
    entry_index: int = -1
    problem: str = ""           # why this call will not do what it looks like
    unreachable: bool = False   # its script is named nowhere, so it never runs


@dataclass
class UnlockReport:
    calls: list = field(default_factory=list)
    # (person_index, entry_index) -> [UnlockCall, ...]
    by_entry: dict = field(default_factory=dict)
    problems: list = field(default_factory=list)

    @property
    def flavor_calls(self) -> list:
        return [c for c in self.calls if c.kind == "flavor"]

    @property
    def pickstate_calls(self) -> list:
        return [c for c in self.calls if c.kind == "pickstate"]

    @property
    def broken(self) -> list:
        """Calls that run but silently do nothing."""
        return [c for c in self.calls if c.problem]

    @property
    def dead(self) -> list:
        """Calls in a script nothing names, so they never run at all.

        Kept apart from `broken`: those run and have no effect, these are never
        reached. The fix is different, so the report must not merge them.
        """
        return [c for c in self.calls if c.unreachable]

    @property
    def duplicates(self) -> list:
        """Entries unlocked from more than one place.

        **Not a defect.** Every one in vanilla is a map call plus a call in the
        shared journal script — two characters telling you the same fact, which
        is ordinary design. Reported as information, never offered for "repair":
        deleting one would remove a real unlock.
        """
        return sorted(k for k, v in self.by_entry.items() if len(v) > 1)

    def orphans(self, person_count: int, entries_per_person: int) -> list:
        """Entries NOTHING unlocks — their text can never be read in game.

        This is the defect that matters, and it is the opposite of a duplicate.
        """
        return [(p, e)
                for p in range(person_count)
                for e in range(entries_per_person)
                if (p, e) not in self.by_entry]


def _iter_source(project_dir: str, exts, needle: str = ""):
    """Walk the project's source, yielding (path, filename, text).

    Whole-tree rather than a fixed set of directories: where a project keeps
    its scripts, macros and C is the project's business. `needle` skips files
    that cannot possibly match, which is what keeps that affordable.
    """
    for dirpath, dirs, files in os.walk(project_dir):
        # Sorted so that report order is stable across machines and runs.
        dirs[:] = sorted(d for d in dirs if d not in _SKIP_DIRS)
        for name in sorted(files):
            if not name.endswith(exts):
                continue
            path = os.path.join(dirpath, name)
            try:
                with open(path, encoding="utf-8",
                          errors="surrogateescape") as fh:
                    text = fh.read()
            except OSError:
                continue
            if needle and needle not in text:
                continue
            yield path, name, text


def find_unreferenced_labels(project_dir: str, labels) -> set:
    """Of `labels`, the ones whose name appears NOWHERE but their own definition.

    Vanilla ships two such scripts — `EventScript_PokemonJournalUnused1` and
    `...Unused2` — holding three `famechecker` calls between them. They can
    never run, so those three unlocks do nothing, and counting them made two
    entries look unlocked from two places when one of the two was dead.

    **Deliberately narrow: this answers "definitely dead", not "reachable".** A
    label reached only from another dead label is dead too, and this will not
    say so. That needs a call graph rooted in the map tables, and getting it
    slightly wrong reports a LIVE script as dead — which, in a tool that will
    offer to change things, is much worse than missing one. Zero references is
    a fact; reachability is an inference. Only the fact is reported.
    """
    labels = set(labels)
    if not labels:
        return set()
    seen = set()
    for path, name, text in _iter_source(project_dir, _REF_EXTS):
        # A commented-out `call Foo` is not a reference to Foo.
        if name.endswith((".inc", ".s")):
            text = _strip_asm_comments(text)
        # Every position at which this file DEFINES a label, so the definition
        # is not counted as a reference to itself.
        defs = {m.start() for m in _LABEL_DEF_RE.finditer(text)}
        for m in _IDENT_RE.finditer(text):
            if m.group(0) in labels and m.start() not in defs:
                seen.add(m.group(0))
        if seen >= labels:
            return set()
    return labels - seen


def _strip_asm_comments(text: str) -> str:
    """Blank GAS `@` comments. Needed wherever a DECLARATION is searched for.

    `_CALL_RE` already ignores `@` when reading call sites; not doing the same
    when reading the macro definition meant a commented-out
    `@ .macro famechecker … function=OLD_NAME` above the real one won on
    textual order, and every two-argument call became "unknown" — safe, but a
    pile of errors whose actual cause is one dead line. The inconsistency was
    inside a single module.
    """
    return re.sub(r"@[^\n]*", "", text or "")


def parse_macro_default_handler(project_dir: str) -> str:
    """The `famechecker` macro's own default handler, read from the macro.

    Hardcoding it worked on vanilla and produced a SILENT WRONG ANSWER
    elsewhere: a project that renamed the default had all 101 two-argument
    calls confidently attributed to a handler it no longer contains, with
    nothing reported. The macro declares the value; read it from there.

    Searched for wherever the project actually defines it, not in a fixed list
    of macro files — the same mistake one level up.
    """
    for _path, _name, text in _iter_source(project_dir, (".inc", ".s"),
                                          needle="famechecker"):
        m = re.search(r"\.macro\s+famechecker\b[^\n]*?\bfunction\s*=\s*(\w+)",
                      _strip_asm_comments(text))
        if m:
            return m.group(1)
    return ""


def parse_unlock_handlers(project_dir: str, candidates=None) -> dict:
    """{handler name: "flavor" | "pickstate"}, resolved by WHAT IT WRITES.

    Not by its name. Substring-matching `"PickState" in name` classified by
    test order for a handler containing both markers, with nothing reported —
    and a name choosing what a call MEANS is the reasoning this parser refuses
    everywhere else. (A name choosing the WORDING of a message is fine; that is
    a different thing.)

    The two engine handlers are told apart by the save field each one assigns:
    the flavor handler ORs a bit into `flavorTextFlags`, the pick-state handler
    assigns `.pickState`. EVERY C file in the project is searched, not one
    named file — a project is free to split or rename its sources, and reading
    only `src/fame_checker.c` made every unlock in such a project classify as
    "unknown". The field names stay, because those are what the feature IS;
    where a project keeps its code is not.

    `candidates` narrows the search to functions that can actually BE unlock
    handlers — the macro's default plus whatever the calls name. That matters:
    without it,
    a handler is excluded for writing BOTH fields, which is how
    `ResetFameChecker` is kept out — but a real handler that inlines the
    pick-state write instead of calling it does the same thing and is excluded
    too. Narrowing first excludes `ResetFameChecker` because nothing calls it,
    which frees the write test to prefer `flavor` when a genuine candidate
    writes both.
    """
    out = {}
    for _path, _name, src in _iter_source(project_dir, (".c",)):
        _scan_handlers(src, candidates, out)
    return out


def _scan_handlers(src: str, candidates, out: dict) -> None:
    """Classify every `void f(void)` in one file by the save field it writes."""
    for m in re.finditer(r"\bvoid\s+(\w+)\s*\(\s*void\s*\)\s*\{", src):
        depth, i = 1, m.end()
        while i < len(src) and depth:
            if src[i] == "{":
                depth += 1
            elif src[i] == "}":
                depth -= 1
            i += 1
        name = m.group(1)
        if candidates is not None and name not in candidates:
            continue
        body = src[m.end():i - 1]
        writes_flavor = bool(re.search(r"\bflavorTextFlags\s*\|?=", body))
        writes_pick = bool(re.search(r"\.pickState\s*=[^=]", body))
        # Once the candidates are narrowed, a function that writes BOTH is a
        # flavor unlock that also advances the display state — which is exactly
        # what the vanilla one does, via a call rather than inline. Excluding
        # "writes both" was only ever a stand-in for "isn't reachable as a
        # handler", and it wrongly excluded a handler that inlines that step.
        if writes_flavor:
            out[name] = "flavor"
        elif writes_pick:
            out[name] = "pickstate"


def _split_args(text: str) -> list:
    out, depth, cur = [], 0, []
    for ch in text:
        if ch in "([":
            depth += 1
        elif ch in ")]":
            depth -= 1
        if ch == "," and depth == 0:
            out.append("".join(cur).strip())
            cur = []
        else:
            cur.append(ch)
    if cur:
        out.append("".join(cur).strip())
    return [a for a in out if a]


def _label_at(labels: list, pos: int) -> str:
    """The script label a call at *pos* sits inside, or "".

    Labels partition an `.inc` file, so the nearest PRECEDING label is the
    containing one — but containment is ASSERTED rather than assumed
    (`start <= pos < next start`), because the reachability and map-attribution
    work depends on this being right, and "nearest preceding, without checking
    it contains the target" produced a real defect in `_enclosing_body`.
    """
    for n, m in enumerate(labels):
        if m.start() > pos:
            break
        nxt = labels[n + 1].start() if n + 1 < len(labels) else None
        if m.start() <= pos and (nxt is None or pos < nxt):
            return m.group(1)
    return ""


def parse_unlock_calls(project_dir: str, person_consts=None,
                       entries_per_person: int = 0,
                       pickstate_consts=None) -> UnlockReport:
    """Find every `famechecker` call under `data/`.

    Both the maps and the shared scripts are scanned — a third of the calls in
    vanilla live in `data/scripts/fame_checker.inc`, so globbing only
    `data/maps/` would miss them.
    """
    rep = UnlockReport()
    consts = list(person_consts or [])
    index_of = {c: i for i, c in enumerate(consts)}
    states = set(pickstate_consts or ())
    # Both READ FROM THE PROJECT, never assumed — see each function.
    default_fn = parse_macro_default_handler(project_dir)
    handlers = {}
    if not default_fn:
        rep.problems.append(
            "Could not find a famechecker macro definition anywhere in this "
            "project, so two-argument unlocks cannot be identified.")

    # Every assembler source in the project, not just `data/`: where a project
    # keeps its scripts is the project's business, and a call the scan never
    # visits is an unlock this report silently omits.
    for path, _name, text in _iter_source(project_dir, (".inc", ".s"),
                                          needle="famechecker"):
        labels = list(_LABEL_RE.finditer(text))
        rel = os.path.relpath(path, project_dir).replace("\\", "/")
        for m in _CALL_RE.finditer(text):
            args = _split_args(m.group(1))
            if not args:
                continue
            call = UnlockCall(
                file_rel=rel,
                line=text[:m.start()].count("\n") + 1,
                label=_label_at(labels, m.start()),
            )
            # The third argument may be positional OR `function=NAME`, and
            # a keyword form can legally appear on any argument.
            positional, keyword = [], {}
            for a in args:
                km = re.match(r"^(\w+)\s*=\s*(.+)$", a)
                if km:
                    keyword[km.group(1)] = km.group(2).strip()
                else:
                    positional.append(a)
            call.person = keyword.get("person") or (
                positional[0] if positional else "")
            call.index = keyword.get("index") or (
                positional[1] if len(positional) > 1 else "")
            call.function = keyword.get("function") or (
                positional[2] if len(positional) > 2 else default_fn)

            rep.calls.append(call)

    # SECOND PASS. The handler set is narrowed to what can actually BE one —
    # the macro's default plus whatever the calls name. Narrowing BEFORE the
    # write test is what lets `ResetFameChecker` be excluded for not being
    # called, rather than for writing both fields; excluding on "writes both"
    # also excluded a genuine handler that inlines the pick-state step.
    #
    # Deliberately NOT also narrowed to `data/specials.inc`. Measured on both
    # projects, that intersection removes nothing, and it can only ever remove
    # a handler on a project that does not link (`special` expands to
    # `.2byte SPECIAL_\function`, and `SPECIAL_x` exists only where
    # `def_special` created it — so a missing entry is a build error, which is
    # loud, and this module exists for failures that are silent). It also
    # breaks two shapes this tool is meant to support: a `famechecker` macro
    # refactored onto `callnative`, which needs no table entry at all, and a
    # specials table split across an `.include`.
    named = {c.function for c in rep.calls if c.function}
    if default_fn:
        named.add(default_fn)
    handlers = parse_unlock_handlers(project_dir, named)
    if not handlers:
        rep.problems.append(
            "Could not work out what this project's unlock handlers do from "
            "src/fame_checker.c, so no unlock can be checked.")

    # A call inside a script nothing names never runs, so it unlocks nothing
    # and must not be counted as coverage — otherwise an entry unlocked once
    # for real and once from dead code reads as "unlocked twice", and an entry
    # unlocked ONLY from dead code reads as unlocked when it can never be read.
    dead_labels = find_unreferenced_labels(
        project_dir, {c.label for c in rep.calls if c.label})

    for call in rep.calls:
        _classify(call, index_of, states, len(consts), entries_per_person,
                  handlers)
        call.unreachable = call.label in dead_labels
        if call.kind == "flavor" and call.person_index >= 0 \
                and call.entry_index >= 0 and not call.unreachable:
            rep.by_entry.setdefault(
                (call.person_index, call.entry_index), []).append(call)
    return rep


def _classify(call: UnlockCall, index_of: dict, states: set,
              person_count: int, entries_per_person: int,
              handlers: dict) -> None:
    """Decide what a call does, and whether it will actually do it."""
    fn = call.function
    # Classify by the HANDLER — what the engine dispatches on — and resolve the
    # handler by WHAT IT WRITES rather than what it is called. Not by argument
    # count either: `FCPICKSTATE_COLORED` is 2, indistinguishable from flavor
    # index 2.
    call.kind = handlers.get(fn, "unknown")
    if call.kind == "unknown":
        call.problem = (
            f"'{fn}' isn't an unlock handler this editor can recognise in "
            f"src/fame_checker.c, so what this line unlocks can't be checked"
            if fn else
            "this line names no unlock handler, so what it unlocks can't be "
            "checked")

    # The person argument need not be a constant — the macro accepts a bare
    # number or a VAR_*. Every call in this project uses a constant, which is
    # exactly the condition under which a constant-only parser looks correct
    # and silently drops the rest. Report what can't be resolved.
    if call.person in index_of:
        call.person_index = index_of[call.person]
    elif re.fullmatch(r"\d+", call.person or ""):
        call.person_index = int(call.person)
    elif call.person.startswith("VAR_"):
        call.problem = call.problem or (
            f"this unlocks whoever '{call.person}' happens to hold when the "
            f"script runs, so the editor can't say which person it affects")
    elif call.person:
        # The likeliest cause by far, and the one the user needs told: the
        # script names a person that has been renamed or removed. Wording only
        # — the classification is identical either way, so this is not the
        # name-prefix reasoning the parser refuses elsewhere.
        call.problem = call.problem or (
            f"'{call.person}' isn't one of this project's people — the script "
            f"will run and the unlock will silently do nothing")

    if call.kind == "flavor":
        if re.fullmatch(r"\d+", call.index or ""):
            call.entry_index = int(call.index)
        elif call.index in states:
            call.problem = call.problem or (
                f"this unlocks a piece of text but names a display state "
                f"('{call.index}') instead of an entry number")
    elif call.kind == "pickstate" and call.index not in states \
            and not re.fullmatch(r"\d+", call.index or ""):
        call.problem = call.problem or (
            f"'{call.index}' isn't a display state this editor can resolve")

    # THE POINT OF THIS MODULE. Both handlers bound-check and then do nothing,
    # with no error of any kind — so an out-of-range unlock runs, prints its
    # dialogue, and never fires. Nothing in game or in the source shows it.
    if 0 <= person_count <= call.person_index:
        call.problem = call.problem or (
            f"there is no person {call.person_index} — this project has "
            f"{person_count}. The script will run and the unlock will "
            f"silently do nothing.")
    if call.kind == "flavor" and entries_per_person \
            and call.entry_index >= entries_per_person:
        call.problem = call.problem or (
            f"there is no entry {call.entry_index} — each person has "
            f"{entries_per_person}. The script will run and the unlock will "
            f"silently do nothing.")
