"""Tests for ``core/battle_anim_script.py`` — move→script table + timeline.

Pure stdlib module; loaded directly. Unit tests on synthetic script text
plus integration against the real project tree.
"""

from __future__ import annotations

import importlib.util
import os
import sys

import pytest

_THIS = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.abspath(os.path.join(_THIS, ".."))
_PROJECT = os.path.join(_ROOT, "pokefirered")


def _load():
    path = os.path.join(_ROOT, "core", "battle_anim_script.py")
    spec = importlib.util.spec_from_file_location("battle_anim_script", path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules.setdefault("battle_anim_script", mod)
    spec.loader.exec_module(mod)
    return mod


mod = _load()


# ───────────────────────────────────────────── classification units ──

@pytest.mark.parametrize("op,kind", [
    ("playsewithpan", "sound"),
    ("playse", "sound"),
    ("loopsewithpan", "sound"),
    ("panse", "sound"),
    ("stopsound", "sound"),
    ("createsprite", "sprite"),
    ("createvisualtask", "task"),
    ("delay", "delay"),
    ("loadspritegfx", "gfx"),
    ("unloadspritegfx", "gfx"),
    ("call", "control"),
    ("end", "control"),
    ("return", "control"),
    ("waitforvisualfinish", "control"),
    ("blendoff", "control"),
    ("some_unknown_op", "other"),
])
def test_classify_opcode(op, kind):
    assert mod.classify_opcode(op) == kind


def test_parse_command_line_splits_name_and_args():
    c = mod._parse_command_line(
        "\tplaysewithpan SE_M_DOUBLE_SLAP, SOUND_PAN_TARGET")
    assert c.name == "playsewithpan"
    assert c.args == ["SE_M_DOUBLE_SLAP", "SOUND_PAN_TARGET"]
    assert c.kind == "sound"
    assert "Play sound SE_M_DOUBLE_SLAP" in c.summary


def test_parse_command_line_strips_trailing_comment():
    c = mod._parse_command_line("\tdelay 10 @ wait a beat")
    assert c.name == "delay"
    assert c.args == ["10"]
    assert c.summary == "Wait 10 frame(s)"


def test_parse_command_line_ignores_blank_and_directive():
    assert mod._parse_command_line("") is None
    assert mod._parse_command_line("   ") is None
    assert mod._parse_command_line("\t.4byte Move_POUND") is None
    assert mod._parse_command_line("\t@ a comment") is None


def test_call_records_target():
    c = mod._parse_command_line("\tcall RoarEffect")
    assert c.name == "call"
    assert c.call_target == "RoarEffect"
    assert c.summary == "Call RoarEffect"


def test_move_label_to_name():
    assert mod.move_label_to_name("Move_THUNDER_PUNCH") == "Thunder Punch"
    assert mod.move_label_to_name("Move_POUND") == "Pound"


def test_anim_label_to_move_const():
    assert mod.anim_label_to_move_const("Move_POUND") == "MOVE_POUND"
    assert mod.anim_label_to_move_const("Move_THUNDER_PUNCH") == "MOVE_THUNDER_PUNCH"
    assert mod.anim_label_to_move_const("RoarEffect") == ""


def test_move_display_name_prefers_project_name():
    names = {
        "MOVE_POUND": "POUND",                 # vanilla all-caps -> prettified
        "MOVE_THUNDER_PUNCH": "THUNDERPUNCH",  # all-caps -> prettified
        "MOVE_CUT": "Slash",                   # user-renamed (mixed case) -> as-is
        "MOVE_NONE": "-$$$$$$",                # padding -> fall back to label
    }
    assert mod.move_display_name("Move_POUND", names) == "Pound"
    assert mod.move_display_name("Move_THUNDER_PUNCH", names) == "Thunderpunch"
    assert mod.move_display_name("Move_CUT", names) == "Slash"   # respects rename
    assert mod.move_display_name("Move_NONE", names) == "None"   # fallback
    # Unknown const -> label-derived fallback.
    assert mod.move_display_name("Move_CUSTOM_THING", names) == "Custom Thing"


# ───────────────────────────────────────────── synthetic parse/flatten ──

_SYNTH = """\
gBattleAnims_Moves::
\t.4byte Move_NONE
\t.4byte Move_POUND
\t.4byte Move_GROWL

Move_NONE:
\tend

Move_POUND:
\tloadspritegfx ANIM_TAG_IMPACT
\tplaysewithpan SE_M_DOUBLE_SLAP, SOUND_PAN_TARGET
\tcreatesprite gBasicHitSplatSpriteTemplate, ANIM_ATTACKER, 2
\tcreatevisualtask AnimTask_ShakeMon, 2, ANIM_TARGET
\twaitforvisualfinish
\tend

Move_GROWL:
\tcall RoarEffect
\tdelay 10
\tend

RoarEffect:
\tplaysewithpan SE_M_GROWL, SOUND_PAN_ATTACKER
\treturn
"""


def _write(tmp_path, text):
    d = tmp_path / "data"
    d.mkdir()
    (d / "battle_anim_scripts.s").write_text(text, encoding="utf-8")
    return str(tmp_path)


def test_parse_move_table_ordered(tmp_path):
    root = _write(tmp_path, _SYNTH)
    table = mod.parse_move_anim_table(root)
    assert table == ["Move_NONE", "Move_POUND", "Move_GROWL"]


def test_parse_scripts_extracts_commands(tmp_path):
    root = _write(tmp_path, _SYNTH)
    scripts = mod.parse_anim_scripts(root)
    assert "Move_POUND" in scripts
    pound = scripts["Move_POUND"]
    names = [c.name for c in pound]
    assert names == ["loadspritegfx", "playsewithpan", "createsprite",
                     "createvisualtask", "waitforvisualfinish", "end"]
    # The move table block must NOT appear as a script.
    assert "gBattleAnims_Moves" not in scripts


def test_resolve_timeline_inlines_calls(tmp_path):
    root = _write(tmp_path, _SYNTH)
    scripts = mod.parse_anim_scripts(root)
    tl = mod.resolve_timeline(scripts, "Move_GROWL", inline_calls=True)
    names = [(c.name, c.depth) for c in tl]
    # call (depth 0) → RoarEffect's playsewithpan (depth 1, return dropped)
    #                → back to delay (depth 0) → end
    assert ("call", 0) in names
    assert ("playsewithpan", 1) in names
    assert ("delay", 0) in names
    assert ("end", 0) in names
    # `return` from the subroutine is NOT emitted.
    assert all(c.name != "return" for c in tl)


def test_resolve_timeline_no_inline(tmp_path):
    root = _write(tmp_path, _SYNTH)
    scripts = mod.parse_anim_scripts(root)
    tl = mod.resolve_timeline(scripts, "Move_GROWL", inline_calls=False)
    assert [c.name for c in tl] == ["call", "delay", "end"]


def test_format_command():
    assert mod.format_command("delay", ["20"]) == "delay 20"
    assert mod.format_command("playsewithpan",
                              ["SE_M_GROWL", "SOUND_PAN_TARGET"]) \
        == "playsewithpan SE_M_GROWL, SOUND_PAN_TARGET"
    assert mod.format_command("end", []) == "end"


def test_rewrite_script_command_replaces_right_line():
    text = (
        "Move_POUND:\n"
        "\tloadspritegfx ANIM_TAG_IMPACT\n"
        "\tplaysewithpan SE_M_DOUBLE_SLAP, SOUND_PAN_TARGET\n"
        "\tdelay 5\n"
        "\tend\n"
    )
    # Index 2 = the `delay 5` command (0=loadspritegfx,1=playse,2=delay).
    out = mod.rewrite_script_command(text, "Move_POUND", 2, "delay 20")
    assert out is not None
    assert "\tdelay 20\n" in out
    assert "\tdelay 5\n" not in out
    # Other lines untouched; indentation preserved.
    assert "\tloadspritegfx ANIM_TAG_IMPACT\n" in out
    assert "\tplaysewithpan SE_M_DOUBLE_SLAP, SOUND_PAN_TARGET\n" in out
    # Round-trip: re-parse and confirm the edit landed at the right index.
    import tempfile, os as _os
    # (parse via the in-memory helper by writing to a temp tree)
    # Simpler: re-run rewrite on the new text to edit the sound at idx 1.
    out2 = mod.rewrite_script_command(
        out, "Move_POUND", 1, "playsewithpan SE_M_GROWL, SOUND_PAN_ATTACKER")
    assert "SE_M_GROWL" in out2 and "SE_M_DOUBLE_SLAP" not in out2


def test_rewrite_script_command_only_targets_named_block():
    # A `delay` exists in two scripts; editing Move_B's must not touch Move_A's.
    text = (
        "Move_A:\n\tdelay 5\n\tend\n"
        "\n"
        "Move_B:\n\tdelay 5\n\tend\n"
    )
    out = mod.rewrite_script_command(text, "Move_B", 0, "delay 99")
    assert out.count("delay 5") == 1     # Move_A's delay survives
    assert "delay 99" in out
    # And it's in the Move_B block.
    b_block = out.split("Move_B:")[1]
    assert "delay 99" in b_block


def test_rewrite_script_command_missing_label_or_index():
    text = "Move_X:\n\tdelay 5\n\tend\n"
    assert mod.rewrite_script_command(text, "Move_NOPE", 0, "delay 1") is None
    assert mod.rewrite_script_command(text, "Move_X", 99, "delay 1") is None


# ───────────────────────────────────── structural edits (insert/del/move) ──

_EDIT_SAMPLE = (
    "Move_POUND:\n"
    "\tloadspritegfx ANIM_TAG_IMPACT\n"          # idx 0
    "\tplaysewithpan SE_M_POUND, SOUND_PAN_TARGET\n"  # idx 1
    "\tdelay 5\n"                                 # idx 2
    "\tend\n"                                     # idx 3
)


def _cmds(mod_, text, label):
    """Re-parse a block's depth-0 command names for assertions."""
    return [c.name for c in mod_.parse_scripts_text(text).get(label, [])]


def test_insert_command_in_middle_shifts_rest_down():
    out = mod.insert_script_command(
        _EDIT_SAMPLE, "Move_POUND", 2, "createsprite gFoo, ANIM_TARGET, 2")
    assert out is not None
    names = _cmds(mod, out, "Move_POUND")
    # New command became index 2; delay/end pushed down.
    assert names == ["loadspritegfx", "playsewithpan",
                     "createsprite", "delay", "end"]
    # Indentation copied from the insertion point (one tab).
    assert "\tcreatesprite gFoo, ANIM_TARGET, 2\n" in out


def test_insert_command_at_end_appends():
    out = mod.insert_script_command(
        _EDIT_SAMPLE, "Move_POUND", 4, "nop")
    names = _cmds(mod, out, "Move_POUND")
    assert names[-1] == "nop"


def test_insert_command_into_empty_block_uses_tab_indent():
    text = "Move_EMPTY:\nMove_NEXT:\n\tend\n"
    out = mod.insert_script_command(text, "Move_EMPTY", 0, "end")
    assert "Move_EMPTY:\n\tend\n" in out
    # Did not bleed into Move_NEXT.
    assert _cmds(mod, out, "Move_NEXT") == ["end"]


def test_insert_command_out_of_range_or_missing():
    assert mod.insert_script_command(
        _EDIT_SAMPLE, "Move_POUND", 99, "nop") is None
    assert mod.insert_script_command(
        _EDIT_SAMPLE, "Move_NOPE", 0, "nop") is None


def test_delete_command_removes_only_that_line():
    out = mod.delete_script_command(_EDIT_SAMPLE, "Move_POUND", 1)
    names = _cmds(mod, out, "Move_POUND")
    assert names == ["loadspritegfx", "delay", "end"]
    assert "playsewithpan" not in out


def test_delete_command_out_of_range_or_missing():
    assert mod.delete_script_command(_EDIT_SAMPLE, "Move_POUND", 99) is None
    assert mod.delete_script_command(_EDIT_SAMPLE, "Move_NOPE", 0) is None


def test_move_command_down_swaps_with_next():
    out = mod.move_script_command(_EDIT_SAMPLE, "Move_POUND", 1, +1)
    names = _cmds(mod, out, "Move_POUND")
    # playse (1) and delay (2) swap.
    assert names == ["loadspritegfx", "delay", "playsewithpan", "end"]


def test_move_command_up_swaps_with_prev():
    out = mod.move_script_command(_EDIT_SAMPLE, "Move_POUND", 2, -1)
    names = _cmds(mod, out, "Move_POUND")
    assert names == ["loadspritegfx", "delay", "playsewithpan", "end"]


def test_move_command_preserves_indentation_and_hops_comments():
    text = (
        "Move_X:\n"
        "\tplayse SE_A\n"          # idx 0
        "\t@ a comment between\n"
        "\tdelay 7\n"              # idx 1
        "\tend\n"                  # idx 2
    )
    out = mod.move_script_command(text, "Move_X", 0, +1)
    names = _cmds(mod, out, "Move_X")
    assert names == ["delay", "playse", "end"]
    # Comment stayed where it was (command hopped over it).
    assert "\t@ a comment between\n" in out
    assert "\tdelay 7\n" in out and "\tplayse SE_A\n" in out


def test_move_command_edges_and_bad_delta_return_none():
    assert mod.move_script_command(_EDIT_SAMPLE, "Move_POUND", 0, -1) is None
    assert mod.move_script_command(_EDIT_SAMPLE, "Move_POUND", 3, +1) is None
    assert mod.move_script_command(_EDIT_SAMPLE, "Move_POUND", 1, 2) is None
    assert mod.move_script_command(_EDIT_SAMPLE, "Move_NOPE", 0, +1) is None


# ───────────────────────── structured createsprite / createvisualtask ──

def test_parse_createsprite_splits_fields():
    c = mod._parse_command_line(
        "\tcreatesprite gEmberSpriteTemplate, ANIM_TARGET, 2, 20, 0, -16, 24, 20, 1")
    cs = mod.parse_createsprite(c)
    assert cs is not None
    assert cs.template == "gEmberSpriteTemplate"
    assert cs.battler == "ANIM_TARGET"
    assert cs.subpriority == "2"
    assert cs.args == ["20", "0", "-16", "24", "20", "1"]


def test_format_createsprite_round_trips():
    c = mod._parse_command_line(
        "\tcreatesprite gBasicHitSplatSpriteTemplate, ANIM_ATTACKER, 2, -8, 0, ANIM_TARGET, 2")
    cs = mod.parse_createsprite(c)
    out = mod.format_createsprite(cs)
    assert out == ("createsprite gBasicHitSplatSpriteTemplate, "
                   "ANIM_ATTACKER, 2, -8, 0, ANIM_TARGET, 2")
    # And re-parsing the formatted form yields the same structure.
    cs2 = mod.parse_createsprite(mod._parse_command_line("\t" + out))
    assert (cs2.template, cs2.battler, cs2.subpriority, cs2.args) == \
           (cs.template, cs.battler, cs.subpriority, cs.args)


def test_parse_createsprite_rejects_non_createsprite_or_short():
    assert mod.parse_createsprite(
        mod._parse_command_line("\tdelay 5")) is None
    assert mod.parse_createsprite(
        mod._parse_command_line("\tcreatesprite gFoo, ANIM_TARGET")) is None


def test_resolve_timeline_follows_goto_tail_jump():
    text = (
        "Move_X:\n"
        "\tloadspritegfx ANIM_TAG_A\n"
        "\tgoto SharedEnd\n"
        "\tplayse SE_NEVER\n"      # after a goto — must NOT appear
        "SharedEnd:\n"
        "\tplayse SE_DONE\n"
        "\tend\n"
    )
    scripts = mod.parse_scripts_text(text)
    tl = mod.resolve_timeline(scripts, "Move_X")
    names = [c.name for c in tl]
    assert "goto" in names
    assert "loadspritegfx" in names
    # The goto target's commands are inlined…
    assert any(c.name == "playse" and c.args == ["SE_DONE"] for c in tl)
    # …and nothing after the goto in the original block runs.
    assert not any(c.args == ["SE_NEVER"] for c in tl)


def test_resolve_timeline_follows_choosetwoturnanim_first_branch():
    text = (
        "Move_CURSEISH:\n"
        "\tchoosetwoturnanim BranchA, BranchB\n"
        "BranchA:\n"
        "\tcreatesprite gFoo, ANIM_TARGET, 2\n"
        "\tend\n"
        "BranchB:\n"
        "\tplayse SE_OTHER\n"
        "\tend\n"
    )
    scripts = mod.parse_scripts_text(text)
    tl = mod.resolve_timeline(scripts, "Move_CURSEISH")
    # The first branch (BranchA) is followed; BranchB is not.
    assert any(c.name == "createsprite" for c in tl)
    assert not any(c.args == ["SE_OTHER"] for c in tl)


def test_resolve_timeline_branch_commands_are_read_only_depth():
    text = (
        "Move_Y:\n\tgoto Tail\n"
        "Tail:\n\tplayse SE_X\n\tend\n"
    )
    scripts = mod.parse_scripts_text(text)
    tl = mod.resolve_timeline(scripts, "Move_Y")
    # The goto itself is depth 0; the inlined tail commands are depth>0
    # (a different label) so the editor treats them read-only.
    goto = next(c for c in tl if c.name == "goto")
    tail = next(c for c in tl if c.name == "playse")
    assert goto.depth == 0
    assert tail.depth > 0


def test_parse_and_format_createvisualtask():
    c = mod._parse_command_line(
        "\tcreatevisualtask AnimTask_ShakeMon, 2, ANIM_TARGET, 3, 0, 6, 1")
    t = mod.parse_createvisualtask(c)
    assert t.addr == "AnimTask_ShakeMon"
    assert t.priority == "2"
    assert t.args == ["ANIM_TARGET", "3", "0", "6", "1"]
    assert mod.format_createvisualtask(t) == (
        "createvisualtask AnimTask_ShakeMon, 2, ANIM_TARGET, 3, 0, 6, 1")


def test_resolve_timeline_cycle_guard(tmp_path):
    # A subroutine that calls itself must not hang.
    text = (
        "Loopy:\n\tcall Loopy\n\tend\n"
    )
    root = _write(tmp_path, text)
    scripts = mod.parse_anim_scripts(root)
    tl = mod.resolve_timeline(scripts, "Loopy", inline_calls=True, max_depth=3)
    assert any(c.name == "call" for c in tl)  # didn't hang, returned


# ─────────────────────────────────────────── integration (real tree) ──

@pytest.mark.skipif(not os.path.isdir(_PROJECT),
                    reason="pokefirered test project not present")
class TestReal:

    def test_move_table_full(self):
        table = mod.parse_move_anim_table(_PROJECT)
        assert len(table) >= 300, f"only {len(table)} move entries"
        assert table[0] == "Move_NONE"
        assert table[1] == "Move_POUND"

    def test_pound_script_has_sound_and_sprite(self):
        scripts = mod.parse_anim_scripts(_PROJECT)
        assert "Move_POUND" in scripts
        tl = mod.resolve_timeline(scripts, "Move_POUND")
        kinds = {c.kind for c in tl}
        assert "sound" in kinds
        assert "sprite" in kinds

    def test_every_table_entry_resolves_or_is_known_missing(self):
        table = mod.parse_move_anim_table(_PROJECT)
        scripts = mod.parse_anim_scripts(_PROJECT)
        # The vast majority of table labels should have a parsed script.
        present = sum(1 for lbl in table if lbl in scripts)
        assert present >= len(table) * 0.9, (
            f"only {present}/{len(table)} move scripts resolved")

    def test_sound_effects_parse(self):
        se = mod.parse_sound_effects(_PROJECT)
        assert len(se) >= 200, f"only {len(se)} SE_ constants"
        assert "SE_SELECT" in se
        assert all(s.startswith("SE_") for s in se)

    def test_move_names_parse_and_resolve(self):
        names = mod.parse_move_names(_PROJECT)
        assert len(names) >= 300, f"only {len(names)} move names parsed"
        assert "MOVE_POUND" in names
        # Display name for Move_POUND resolves via the project's gMoveNames.
        disp = mod.move_display_name("Move_POUND", names)
        assert disp.lower() == "pound"
