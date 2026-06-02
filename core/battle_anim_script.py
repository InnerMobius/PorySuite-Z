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
    if name.startswith(("playse", "loopse", "panse")):
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
        args = [a.strip() for a in parts[1].split(",") if a.strip()]
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


def resolve_timeline(scripts: Dict[str, List[Command]], label: str,
                     inline_calls: bool = True,
                     max_depth: int = 2) -> List[Command]:
    """Flatten a script into a linear timeline.

    Top-level commands of ``label`` in order.  When ``inline_calls`` is
    set, each ``call X`` is followed by X's commands (minus its trailing
    ``return``) at ``depth+1``, so the UI can show the shared subroutine's
    sounds/sprites in place.  Depth-limited and cycle-guarded so a
    recursive/looping script can't hang.  ``end``/``return`` terminate
    the current level.
    """
    out: List[Command] = []

    def _walk(lbl: str, depth: int, seen: frozenset):
        if depth > max_depth or lbl in seen or lbl not in scripts:
            return
        seen2 = seen | {lbl}
        for cmd in scripts[lbl]:
            c = Command(name=cmd.name, args=list(cmd.args), kind=cmd.kind,
                        raw=cmd.raw, depth=depth, call_target=cmd.call_target)
            if cmd.name == "return":
                return
            if cmd.name == "end":
                out.append(c)
                return
            out.append(c)
            if inline_calls and cmd.name == "call" and cmd.call_target:
                _walk(cmd.call_target, depth + 1, seen2)

    _walk(label, 0, frozenset())
    return out
