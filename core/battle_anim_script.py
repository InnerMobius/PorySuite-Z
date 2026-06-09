"""Pure data layer for the Move Animations timeline (Battle Anims tab).

Parses pokefirered's battle-animation SCRIPTS — the per-move bytecode in
``data/battle_anim_scripts.s`` — into an ordered, classified command
timeline the UI can show: which sounds play, which sprites spawn, the
delays between them, the visual tasks.

No Qt, no project-data-manager imports (stdlib only), so it's unit-
testable in isolation.

Structure of the source file
============================

A move-index → script-label table::

    gBattleAnims_Moves::
        .4byte Move_NONE       @ move 0
        .4byte Move_POUND      @ move 1
        ...

…then one labelled script per move (and shared subroutines reached via
``call``)::

    Move_POUND:
        loadspritegfx ANIM_TAG_IMPACT
        playsewithpan SE_M_DOUBLE_SLAP, SOUND_PAN_TARGET   @ sound
        createsprite gBasicHitSplatSpriteTemplate, ...      @ spawn
        createvisualtask AnimTask_ShakeMon, 2, ...          @ visual task
        waitforvisualfinish
        end

    RoarEffect:
        ...
        return

Each command is one token (the opcode) followed by comma-separated args.
``call`` jumps to a shared subroutine and returns after it; ``end`` ends
a move script, ``return`` ends a subroutine.

Scope
=====

Read-only this phase: parse + classify + flatten.  Editing the timeline
(changing sounds / delays, adding commands) is a later phase.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from typing import Dict, List, Optional


_SCRIPTS_REL = os.path.join("data", "battle_anim_scripts.s")
_MOVE_TABLE_LABEL = "gBattleAnims_Moves"
_MOVE_NAMES_REL = os.path.join("src", "data", "text", "move_names.h")
_SONGS_REL = os.path.join("include", "constants", "songs.h")


# Command "kind" buckets — drive the timeline icon + grouping in the UI.
KIND_SOUND = "sound"
KIND_SPRITE = "sprite"
KIND_TASK = "task"
KIND_DELAY = "delay"
KIND_GFX = "gfx"
KIND_CONTROL = "control"
KIND_OTHER = "other"


# Opcode → kind.  Prefix rules cover the families; exact names cover the
# rest.  Anything unmatched is KIND_OTHER.
_EXACT_KIND = {
    "delay": KIND_DELAY,
    "loadspritegfx": KIND_GFX,
    "unloadspritegfx": KIND_GFX,
    "call": KIND_CONTROL,
    "return": KIND_CONTROL,
    "end": KIND_CONTROL,
    "jump": KIND_CONTROL,
    "goto": KIND_CONTROL,
    "waitforvisualfinish": KIND_CONTROL,
    "monbg": KIND_CONTROL,
    "clearmonbg": KIND_CONTROL,
    "monbg_static": KIND_CONTROL,
    "clearmonbg_static": KIND_CONTROL,
    "setalpha": KIND_CONTROL,
    "blendoff": KIND_CONTROL,
    "setarg": KIND_CONTROL,
    "stopsound": KIND_SOUND,
}


def classify_opcode(name: str) -> str:
    """Return the KIND_* bucket for a battle-anim opcode."""
    if name in _EXACT_KIND:
        return _EXACT_KIND[name]
    # waitplayse / waitplaysewithpan PLAY a sound (then wait) — they were missed
    # by the playse/loopse/panse prefixes, so moves whose only SE is a
    # waitplayse* (Reflect = SE_M_REFLECT, Role Play's DETECT "ting") were silent.
    if name.startswith(("playse", "loopse", "panse", "waitplayse")):
        return KIND_SOUND
    if name.startswith("createsprite"):
        return KIND_SPRITE
    if name.startswith("createvisualtask") or name.startswith("delaybytask"):
        return KIND_TASK
    if name.startswith("jump") or name.startswith("goto"):
        return KIND_CONTROL
    return KIND_OTHER


# ───────────────────────────────────────────────────────── dataclasses ──

@dataclass
class Command:
    """One opcode in a battle-anim script."""

    name: str                       # opcode, e.g. "playsewithpan"
    args: List[str] = field(default_factory=list)
    kind: str = KIND_OTHER
    raw: str = ""                   # original source line (stripped)
    depth: int = 0                  # 0 = top-level; >0 = inlined from a call
    call_target: str = ""           # for `call`, the subroutine label

    @property
    def summary(self) -> str:
        """Human-readable one-liner for the timeline row."""
        if self.kind == KIND_SOUND and self.args:
            pan = f"  (pan {self.args[1]})" if len(self.args) > 1 else ""
            return f"Play sound {self.args[0]}{pan}"
        if self.kind == KIND_DELAY:
            n = self.args[0] if self.args else "?"
            return f"Wait {n} frame(s)"
        if self.kind == KIND_SPRITE and self.args:
            return f"Spawn sprite {self.args[0]}"
        if self.kind == KIND_TASK and self.args:
            return f"Visual task {self.args[0]}"
        if self.kind == KIND_GFX and self.args:
            return f"Load gfx {self.args[0]}"
        if self.name == "call" and self.args:
            return f"Call {self.args[0]}"
        if self.name == "waitforvisualfinish":
            return "Wait for visuals to finish"
        if self.name == "end":
            return "End"
        if self.name == "return":
            return "Return"
        if self.args:
            return f"{self.name} {', '.join(self.args)}"
        return self.name


# ───────────────────────────────────────────────────────────── parsing ──

_LABEL_RE = re.compile(r"^(\w+):\s*$")


def _split_args(s: str) -> List[str]:
    """Split a comma-separated opcode arg list, but NOT on commas INSIDE
    parentheses — so RGB(24, 6, 23) (and any macro with internal commas) stays
    a single arg. A naive split breaks it into "RGB(24", "6", "23)", which both
    mis-counts the args AND loses the colour (it resolves to 0 = black), so e.g.
    Poison Tail's MetallicShine tinted the user BLACK instead of purple."""
    out: List[str] = []
    depth = 0
    cur: List[str] = []
    for ch in s:
        if ch == "(":
            depth += 1
            cur.append(ch)
        elif ch == ")":
            depth = max(0, depth - 1)
            cur.append(ch)
        elif ch == "," and depth == 0:
            a = "".join(cur).strip()
            if a:
                out.append(a)
            cur = []
        else:
            cur.append(ch)
    a = "".join(cur).strip()
    if a:
        out.append(a)
    return out


def _parse_command_line(line: str) -> Optional[Command]:
    """Parse one indented opcode line into a Command, or None for a
    non-command line (blank, comment, directive)."""
    s = line.strip()
    if not s or s.startswith(("@", "//", ".", "/*", "*")):
        return None
    # Strip trailing line comment.
    s = re.split(r"\s+@", s, maxsplit=1)[0].strip()
    if not s:
        return None
    parts = s.split(None, 1)
    name = parts[0]
    args: List[str] = []
    if len(parts) > 1:
        args = _split_args(parts[1])
    kind = classify_opcode(name)
    target = args[0] if (name == "call" and args) else ""
    return Command(name=name, args=args, kind=kind, raw=s, call_target=target)


def parse_scripts_text(text: str) -> Dict[str, List[Command]]:
    """Parse battle-anim script source TEXT into ``{label: [Command]}``.

    Each label's own top-level commands (depth 0), in order.  Pure-data
    blocks (the ``.4byte`` move table) end up empty and are dropped.
    Used both for the on-disk file and for re-parsing in-memory edits.
    """
    scripts: Dict[str, List[Command]] = {}
    current: Optional[str] = None
    for raw in text.splitlines():
        m = _LABEL_RE.match(raw)
        if m:
            current = m.group(1)
            scripts.setdefault(current, [])
            continue
        if current is None:
            continue
        cmd = _parse_command_line(raw)
        if cmd is not None:
            scripts[current].append(cmd)
    return {k: v for k, v in scripts.items() if v}


def parse_anim_scripts(root: str) -> Dict[str, List[Command]]:
    """Parse every labelled script/subroutine in ``battle_anim_scripts.s``.

    Returns ``{label: [Command, ...]}`` — each label's own top-level
    commands (depth 0), in order.  The ``.4byte`` move table is skipped
    (it's parsed separately by :func:`parse_move_anim_table`).  Never
    raises; a missing file yields ``{}``.
    """
    path = os.path.join(root, _SCRIPTS_REL)
    if not os.path.isfile(path):
        return {}
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            return parse_scripts_text(f.read())
    except OSError:
        return {}


def scripts_path(root: str) -> str:
    """Absolute path of the battle-anim scripts source file."""
    return os.path.join(root, _SCRIPTS_REL)


def parse_move_anim_table(root: str) -> List[str]:
    """Parse ``gBattleAnims_Moves::`` → ordered list of script labels.

    Index == battle move ID (entry 0 = move 0).  Returns ``[]`` when the
    table or file is absent.
    """
    path = os.path.join(root, _SCRIPTS_REL)
    if not os.path.isfile(path):
        return []
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            text = f.read()
    except OSError:
        return []
    # Grab from the label to the first line that isn't a .4byte/blank.
    m = re.search(_MOVE_TABLE_LABEL + r"::\s*\n((?:\s*\.4byte\s+\w+\s*\n)+)",
                  text)
    if not m:
        return []
    body = m.group(1)
    return re.findall(r"\.4byte\s+(\w+)", body)


def _pretty_anim_name(token: str) -> str:
    """``B_ANIM_STATUS_CONFUSION`` → ``Confusion``; ``General_StatsChange``
    → ``Stats Change`` — a readable label for a non-move animation."""
    s = token.strip()
    for pre in ("B_ANIM_STATUS_", "B_ANIM_", "Status_", "General_", "Special_"):
        if s.startswith(pre):
            s = s[len(pre):]
            break
    # split snake_case and CamelCase into words
    s = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", "_", s)
    words = [w for w in s.replace("_", " ").split() if w]
    return " ".join(w.capitalize() for w in words) if words else token


def parse_named_anim_table(root: str, table_label: str) -> List[tuple]:
    """Parse a non-move animation table (``gBattleAnims_StatusConditions``,
    ``gBattleAnims_General``, ``gBattleAnims_Special``) into ordered
    ``[(display_name, script_label)]``.

    Each entry is ``.4byte ScriptLabel  @ B_ANIM_*`` — the comment gives the
    readable name (falls back to the label).  Returns ``[]`` when the table
    or file is absent.
    """
    path = os.path.join(root, _SCRIPTS_REL)
    if not os.path.isfile(path):
        return []
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            text = f.read()
    except OSError:
        return []
    m = re.search(re.escape(table_label) +
                  r"::\s*\n((?:[ \t]*\.4byte\s+\w+[^\n]*\n|[ \t]*\n)+)", text)
    if not m:
        return []
    out: List[tuple] = []
    for label, comment in re.findall(
            r"\.4byte\s+(\w+)[ \t]*(?:@[ \t]*(\S+))?", m.group(1)):
        name = _pretty_anim_name(comment if comment else label)
        out.append((name, label))
    return out


def move_label_to_name(label: str) -> str:
    """``Move_THUNDER_PUNCH`` -> ``Thunder Punch`` (fallback display name
    when the project's own move names aren't available)."""
    name = label
    if name.startswith("Move_"):
        name = name[len("Move_"):]
    parts = [p for p in name.split("_") if p]
    return " ".join(p.capitalize() for p in parts) if parts else label


def anim_label_to_move_const(label: str) -> str:
    """``Move_THUNDER_PUNCH`` -> ``MOVE_THUNDER_PUNCH``.

    The anim-table label encodes the move constant directly, so we don't
    need positional correlation to look up the move's name.
    """
    if label.startswith("Move_"):
        return "MOVE_" + label[len("Move_"):]
    return ""


def parse_move_names(root: str) -> Dict[str, str]:
    """Parse ``gMoveNames[]`` -> ``{MOVE_CONST: name}`` from the project.

    Reads the project's OWN move names (respecting any renames / custom
    moves) — never assume vanilla names.  Returns ``{}`` if the file is
    absent.
    """
    path = os.path.join(root, _MOVE_NAMES_REL)
    if not os.path.isfile(path):
        return {}
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            text = f.read()
    except OSError:
        return {}
    out: Dict[str, str] = {}
    for const, name in re.findall(
            r"\[(MOVE_\w+)\]\s*=\s*_\(\"([^\"]*)\"\)", text):
        out[const] = name
    return out


def move_display_name(label: str, move_names: Dict[str, str]) -> str:
    """Best display name for a move-anim script label.

    Prefers the project's own ``gMoveNames`` entry (so renamed / custom
    moves show their real name).  Vanilla names are stored ALL-CAPS in
    FRLG (e.g. ``POUND``); those are prettified to Title Case for
    readability, while a mixed-case (user-authored) name is shown as-is.
    Falls back to the label-derived name when no move name is found.
    """
    const = anim_label_to_move_const(label)
    raw = (move_names.get(const, "") or "").replace("$", "").strip()
    # FRLG padding / empty-name placeholders (e.g. MOVE_NONE = "-$$$$$$").
    if raw and raw not in ("-",):
        if raw.isupper():
            return " ".join(w.capitalize() for w in raw.split())
        return raw
    return move_label_to_name(label)


def rewrite_script_command(text: str, label: str, cmd_index: int,
                           new_command: str) -> Optional[str]:
    """Return ``text`` with the ``cmd_index``-th command of script
    ``label`` replaced by ``new_command``.

    ``cmd_index`` counts commands the SAME way :func:`parse_anim_scripts`
    does (skipping blank / comment / directive lines), so a 0-based index
    from a parsed (depth-0) command list lines up exactly.  Leading
    indentation of the original line is preserved.

    Editing only ever targets a script's OWN commands — never inlined
    ``call`` subroutines — so a caller editing a move's timeline can't
    accidentally rewrite a shared sub-script used by other moves.

    Returns the modified text, or ``None`` when the label or index can't
    be found (so the caller can fail loudly instead of writing garbage).
    """
    lines = text.splitlines(keepends=True)
    # Find the label line.
    label_re = re.compile(r"^" + re.escape(label) + r":\s*$")
    start = None
    for i, ln in enumerate(lines):
        if label_re.match(ln):
            start = i + 1
            break
    if start is None:
        return None
    # Walk the block, counting command lines until cmd_index.
    count = 0
    for i in range(start, len(lines)):
        ln = lines[i]
        if _LABEL_RE.match(ln):
            break  # hit the next label — index out of range for this block
        if _parse_command_line(ln) is None:
            continue
        if count == cmd_index:
            indent = ln[: len(ln) - len(ln.lstrip())]
            newline = "\n" if ln.endswith("\n") else ""
            lines[i] = f"{indent}{new_command}{newline}"
            return "".join(lines)
        count += 1
    return None


def _block_command_indices(lines: List[str], label: str):
    """Locate script ``label``'s body in ``lines`` (keepends-split).

    Returns ``(start, cmd_line_indices)`` where ``start`` is the index of
    the first line after the label and ``cmd_line_indices`` is the list of
    indices (into ``lines``) of each depth-0 COMMAND line in that block,
    counted the SAME way the parser counts (skipping blanks / comments /
    directives), in order.  The block ends at the next label or EOF.

    Returns ``(None, [])`` when the label isn't found.  This is the shared
    spine of every structural edit (insert / delete / move / rewrite) so
    they all agree on what "command N of this script" means.
    """
    label_re = re.compile(r"^" + re.escape(label) + r":\s*$")
    start = None
    for i, ln in enumerate(lines):
        if label_re.match(ln):
            start = i + 1
            break
    if start is None:
        return None, []
    cmd_idx: List[int] = []
    for i in range(start, len(lines)):
        if _LABEL_RE.match(lines[i]):
            break
        if _parse_command_line(lines[i]) is not None:
            cmd_idx.append(i)
    return start, cmd_idx


def _line_parts(ln: str):
    """Split a source line into ``(indent, content, newline)`` so an edit
    can swap the content while preserving the slot's indentation + EOL."""
    stripped = ln.rstrip("\r\n")
    newline = ln[len(stripped):]
    indent = stripped[: len(stripped) - len(stripped.lstrip())]
    return indent, stripped.strip(), newline


def insert_script_command(text: str, label: str, cmd_index: int,
                          new_command: str) -> Optional[str]:
    """Return ``text`` with ``new_command`` inserted as the
    ``cmd_index``-th command of script ``label``.

    ``cmd_index`` is 0-based and ranges over ``[0, count]`` (where
    ``count`` is the script's own depth-0 command count): inserting at
    ``i`` makes the new command the i-th, pushing the old i-th and the
    rest down; inserting at ``count`` appends after the last command.
    Indentation is copied from the command at the insertion point (or the
    last command when appending, or one tab in an empty block).

    Only ever touches the script's OWN commands — inlined ``call``
    subroutines are never editable — so a move's timeline edit can't
    rewrite a shared sub-script.  Returns ``None`` (no write) when the
    label is missing or the index is out of range.
    """
    lines = text.splitlines(keepends=True)
    start, cmd_idx = _block_command_indices(lines, label)
    if start is None:
        return None
    count = len(cmd_idx)
    if not (0 <= cmd_index <= count):
        return None
    if count == 0:
        indent, newline, insert_at = "\t", "\n", start
    elif cmd_index < count:
        indent, _c, newline = _line_parts(lines[cmd_idx[cmd_index]])
        newline = newline or "\n"
        insert_at = cmd_idx[cmd_index]
    else:  # append after the last command
        indent, _c, newline = _line_parts(lines[cmd_idx[-1]])
        newline = newline or "\n"
        insert_at = cmd_idx[-1] + 1
    lines.insert(insert_at, f"{indent}{new_command}{newline}")
    return "".join(lines)


def delete_script_command(text: str, label: str,
                          cmd_index: int) -> Optional[str]:
    """Return ``text`` with the ``cmd_index``-th command of script
    ``label`` removed entirely (the whole source line).

    Same indexing as :func:`insert_script_command` / the parser.  Only the
    script's own depth-0 commands are addressable.  Returns ``None`` when
    the label or index can't be found.
    """
    lines = text.splitlines(keepends=True)
    start, cmd_idx = _block_command_indices(lines, label)
    if start is None or not (0 <= cmd_index < len(cmd_idx)):
        return None
    del lines[cmd_idx[cmd_index]]
    return "".join(lines)


def move_script_command(text: str, label: str, cmd_index: int,
                        delta: int) -> Optional[str]:
    """Return ``text`` with the ``cmd_index``-th command of script
    ``label`` swapped with its neighbour ``delta`` positions away
    (``-1`` = move up / earlier, ``+1`` = move down / later).

    Only the command CONTENT is swapped; each slot keeps its own
    indentation + EOL, and any comment / blank lines physically between
    the two commands stay put (the command hops over them).  Same indexing
    and own-commands-only rule as the other editors.  Returns ``None`` when
    ``delta`` isn't ±1 or either index is out of range.
    """
    if delta not in (-1, 1):
        return None
    lines = text.splitlines(keepends=True)
    start, cmd_idx = _block_command_indices(lines, label)
    if start is None:
        return None
    j = cmd_index + delta
    if not (0 <= cmd_index < len(cmd_idx)) or not (0 <= j < len(cmd_idx)):
        return None
    a, b = cmd_idx[cmd_index], cmd_idx[j]
    ind_a, con_a, nl_a = _line_parts(lines[a])
    ind_b, con_b, nl_b = _line_parts(lines[b])
    lines[a] = f"{ind_a}{con_b}{nl_a}"
    lines[b] = f"{ind_b}{con_a}{nl_b}"
    return "".join(lines)


# ──────────────────────────── structured createsprite / task editing ──
# A createsprite/createvisualtask command's tokens have fixed leading
# fields then a variable arg list; parsing them into named fields lets the
# UI offer real per-field editors (which sprite, anchored to whom, layer
# order, x/y offset) instead of raw text.

@dataclass
class CreateSpriteCmd:
    """Parsed ``createsprite template, battler, subpriority, argv...``."""

    template: str           # SpriteTemplate symbol, e.g. gEmberSpriteTemplate
    battler: str            # ANIM_ATTACKER / ANIM_TARGET / ...
    subpriority: str        # subpriority offset (layer order); may be expr
    args: List[str] = field(default_factory=list)  # gBattleAnimArgs values


def parse_createsprite(cmd: "Command") -> Optional[CreateSpriteCmd]:
    """Structure a ``createsprite`` Command, or ``None`` if it isn't one /
    is malformed (fewer than the 3 required leading fields)."""
    if cmd.name != "createsprite" or len(cmd.args) < 3:
        return None
    return CreateSpriteCmd(
        template=cmd.args[0], battler=cmd.args[1], subpriority=cmd.args[2],
        args=list(cmd.args[3:]))


def format_createsprite(cs: CreateSpriteCmd) -> str:
    """Render a :class:`CreateSpriteCmd` back to a source command string."""
    parts = [cs.template, cs.battler, cs.subpriority, *cs.args]
    return "createsprite " + ", ".join(p.strip() for p in parts if p.strip())


@dataclass
class CreateVisualTaskCmd:
    """Parsed ``createvisualtask addr, priority, argv...``."""

    addr: str               # task function symbol, e.g. AnimTask_ShakeMon
    priority: str
    args: List[str] = field(default_factory=list)


def parse_createvisualtask(cmd: "Command") -> Optional[CreateVisualTaskCmd]:
    if cmd.name != "createvisualtask" or len(cmd.args) < 2:
        return None
    return CreateVisualTaskCmd(
        addr=cmd.args[0], priority=cmd.args[1], args=list(cmd.args[2:]))


def format_createvisualtask(t: CreateVisualTaskCmd) -> str:
    parts = [t.addr, t.priority, *t.args]
    return "createvisualtask " + ", ".join(p.strip() for p in parts if p.strip())


def parse_sound_effects(root: str) -> List[str]:
    """Parse ``#define SE_*`` from ``include/constants/songs.h`` into an
    ordered list of sound-effect constant names (for the sound picker).

    Returns ``[]`` if the file is absent.  Order is file order (which is
    by value), so the picker reads naturally.
    """
    path = os.path.join(root, _SONGS_REL)
    if not os.path.isfile(path):
        return []
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            text = f.read()
    except OSError:
        return []
    return re.findall(r"#define\s+(SE_\w+)\s+", text)


def format_command(name: str, args: List[str]) -> str:
    """Render an opcode + args back to source form: ``name a, b, c``."""
    if args:
        return f"{name} {', '.join(args)}"
    return name


def find_anim_branches(scripts: Dict[str, List[Command]],
                       label: str) -> List[str]:
    """Return the branch target labels of the FIRST ``choosetwoturnanim`` in
    a move's resolved path, or ``[]`` if it doesn't branch.

    ``choosetwoturnanim A, B`` is how a move shows a different animation
    depending on the user (e.g. Curse → ``CurseGhost`` for Ghost-types vs
    ``CurseStats`` for everyone else, which is why Slowking's Curse looks
    different).  The UI uses this to offer a variant picker.
    """
    timeline = resolve_timeline(scripts, label)
    for cmd in timeline:
        if cmd.name == "choosetwoturnanim" and len(cmd.args) >= 2:
            return list(cmd.args)
    return []


def resolve_timeline(scripts: Dict[str, List[Command]], label: str,
                     inline_calls: bool = True,
                     max_depth: int = 6,
                     branch_choice: int = 0) -> List[Command]:
    """Flatten a script into a linear timeline.

    Top-level commands of ``label`` in order.  When ``inline_calls`` is set
    the resolver also follows control flow so a move that lives in a
    branched-to label still shows its real commands:

      * ``call X``               → inline X's commands at ``depth+1``,
                                    then continue after the call.
      * ``goto X``               → tail-jump: inline X then stop this block.
      * ``choosetwoturnanim A,B`` → follow the first branch (A) as a
                                    representative animation, then stop.

    Conditional jumps (``jumpifmoveturn`` / ``jumpargeq`` / ``jumpret*``)
    are left as fall-through — their guarded label is an alternate path we
    can't evaluate without the engine, so we show the main line.

    Depth-limited and cycle-guarded so a recursive/looping script can't
    hang.  ``end``/``return`` terminate the current level.  Branch-inlined
    commands carry ``depth>0`` so the editor treats them read-only (they
    live in a different label).
    """
    out: List[Command] = []
    # Labels in FILE order, so a label that ends WITHOUT a terminator can fall
    # through to the next one (assembly semantics) — e.g. Move_HORN_DRILL ends on
    # a createvisualtask and continues into HornDrillContinue: where the actual
    # sprites are. Without this, only the first label's commands resolve.
    _keys = list(scripts.keys())
    _next = {_keys[i]: _keys[i + 1] for i in range(len(_keys) - 1)}

    def _rest_has_content(cmds, start: int) -> bool:
        """Do the commands AFTER index ``start`` (the fall-through of a
        conditional jump) lead to real animation — a ``goto`` (to a main line) or
        a ``create*`` (inline sprites/tasks) BEFORE hitting end/return? Used to
        tell a SKIP jump (Castform: jump-to-skip, then `goto` the morph → fall
        through) from a VARIANT-DISPATCH jump (Safari Reaction: a row of jumps
        ending in bare `end`, no main line → follow the target). Other jumps and
        waits/sounds in between don't count as content."""
        for c in cmds[start:]:
            if c.name in ("end", "return"):
                return False
            if c.name == "goto" or c.name.startswith("create"):
                return True
        return False

    def _walk(lbl: str, depth: int, seen: frozenset):
        if depth > max_depth or lbl in seen or lbl not in scripts:
            return
        seen2 = seen | {lbl}
        _cmds = scripts[lbl]
        for ci, cmd in enumerate(_cmds):
            c = Command(name=cmd.name, args=list(cmd.args), kind=cmd.kind,
                        raw=cmd.raw, depth=depth, call_target=cmd.call_target)
            if cmd.name == "return":
                return
            if cmd.name == "end":
                out.append(c)
                return
            out.append(c)
            if not inline_calls:
                continue
            if cmd.name == "call" and cmd.call_target:
                _walk(cmd.call_target, depth + 1, seen2)
            elif cmd.name == "goto" and cmd.args:
                _walk(cmd.args[0], depth + 1, seen2)
                return  # tail-jump: nothing after a goto runs
            elif cmd.name == "choosetwoturnanim" and cmd.args:
                # Two-turn move: arg[0] is the SETUP (turn 1 — Sky Attack's
                # charge), arg[1] the UNLEASH (turn 2 — the dive over the sliding
                # sky bg). The variant picker drives branch_choice, so each shows
                # its OWN animation; do NOT override the user's pick (that made
                # setup render as unleash). branch_choice=0 → setup, 1 → unleash.
                pick = cmd.args[min(branch_choice, len(cmd.args) - 1)]
                _walk(pick, depth + 1, seen2)
                return  # follow the chosen branch
            elif (cmd.name.startswith("jumparg")
                  and cmd.args and cmd.args[-1] in scripts):
                # ARG-based conditional jump (jumpargeq …): the branch TARGET (its
                # last arg, a label) holds a VARIANT's real animation — Magnitude's
                # power tiers, Return's friendship tiers. We can't evaluate the
                # runtime arg, so follow it as a representative variant; without
                # this the move resolves to just its selector task + end.
                _walk(cmd.args[-1], depth + 1, seen2)
                return
            elif (cmd.name.startswith("jump") and cmd.name != "jumpifcontest"
                  and cmd.args and cmd.args[-1] in scripts):
                # Return-value / turn conditional jump (jumpret* / jumpreteq /
                # jumpifmoveturn). Two shapes, told apart by whether the
                # fall-through has real content:
                #   • SKIP-then-main (Castform: `jumpreteq TRUE, <skip>` then
                #     `goto <morph>`) → fall-through HAS content → FALL THROUGH so
                #     the main line runs (don't follow the skip).
                #   • VARIANT-DISPATCH (Safari Reaction: a row of `jumpreteq
                #     <type>, <variant>` ending in bare `end`) → fall-through has
                #     NO content → FOLLOW the first target as a representative.
                # jumpifcontest is excluded above: the preview is never a contest,
                # so it always falls through (the non-contest path).
                if _rest_has_content(_cmds, ci + 1):
                    continue
                _walk(cmd.args[-1], depth + 1, seen2)
                return
        # Reached the end of this label with no end/return/goto/branch → the
        # script falls through into the next label in file order (continuation
        # labels like HornDrillContinue). depth+1 marks it read-only in the
        # editor (it lives in a different label) while still driving playback.
        if inline_calls:
            nxt = _next.get(lbl)
            if nxt:
                _walk(nxt, depth + 1, seen2)

    _walk(label, 0, frozenset())
    return out
