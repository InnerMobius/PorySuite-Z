"""Guards for the sprite-swap engine patcher.

This writes to the user's GAME SOURCE, so the bar is the same as the repair
tool's: it must be idempotent, it must refuse rather than guess, and it must
never produce something that assembles but behaves wrong.

THE BUG THIS ALMOST SHIPPED WITH
================================
The script command table is INDEX-addressed: opcode N dispatches to the Nth
`.4byte` entry. The first version counted every `.4byte ScrCmd_` line in the
file — including the `ScrCmd_nop` that sits AFTER `gScriptCmdTableEnd::` as a
terminator. That made the count one too high, so the macro emitted 0xD6 while
the entry landed at index 0xD5.

That assembles cleanly and the command silently does nothing, because 0xD6
dispatches to the terminator. It would have looked like "the engine ignores my
command" with nothing in the build to point at. Hence
`test_opcode_matches_the_entry_index`, which is the test that matters most
here, and the self-check inside the installer that refuses on a mismatch.
"""

import os
import re
import sys

import pytest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _p in (_ROOT, os.path.join(_ROOT, 'core'), os.path.join(_ROOT, 'ui')):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from core.sprite_swap_patch import (  # noqa: E402
    COMMAND_NAME,
    SpriteSwapPatchError,
    describe,
    ensure_sprite_swap_support,
    is_installed,
    next_free_opcode,
)

_TABLE_END = 'gScriptCmdTableEnd::'


def _project(tmp_path, entries=3, last_opcode=None):
    """A throwaway project with just the three files the patcher touches."""
    (tmp_path / 'asm' / 'macros').mkdir(parents=True)
    (tmp_path / 'src').mkdir()
    (tmp_path / 'data').mkdir()

    (tmp_path / 'asm' / 'macros' / 'event.inc').write_text(
        '\t.macro nop\n\t.byte 0x00\n\t.endm\n', encoding='utf-8')
    (tmp_path / 'src' / 'scrcmd.c').write_text(
        '#include "global.h"\n\nbool8 ScrCmd_nop(struct ScriptContext * ctx)\n'
        '{\n    return FALSE;\n}\n', encoding='utf-8')

    last = entries - 1 if last_opcode is None else last_opcode
    rows = ''.join(f'\t.4byte ScrCmd_cmd{i}    @ 0x{i:02x}\n'
                   for i in range(entries))
    # The terminator after the end marker is NOT an opcode — the thing the
    # first version miscounted.
    (tmp_path / 'data' / 'script_cmd_table.inc').write_text(
        f'gScriptCmdTable::\n{rows}\n{_TABLE_END}\n\t.4byte ScrCmd_nop\n',
        encoding='utf-8')
    return str(tmp_path), last


def _table_index_of_new_entry(root):
    body = open(os.path.join(root, 'data', 'script_cmd_table.inc'),
                encoding='utf-8').read().split(_TABLE_END, 1)[0]
    return len(re.findall(r'^\s*\.4byte\s+ScrCmd_', body, re.M)) - 1


def _macro_opcode(root):
    text = open(os.path.join(root, 'asm', 'macros', 'event.inc'),
                encoding='utf-8').read()
    m = re.search(rf'\.macro {COMMAND_NAME}.*?\.byte (0x[0-9a-fA-F]+)',
                  text, re.S)
    return int(m.group(1), 16) if m else None


# ─────────────────────────────────────────────────────────────────────────────
# The one that matters
# ─────────────────────────────────────────────────────────────────────────────

def test_opcode_matches_the_entry_index(tmp_path):
    """The table is index-addressed. A mismatch assembles and does nothing."""
    root, _ = _project(tmp_path, entries=213)
    ensure_sprite_swap_support(root)
    assert _macro_opcode(root) == _table_index_of_new_entry(root)


def test_the_terminator_is_not_counted_as_an_opcode(tmp_path):
    """`ScrCmd_nop` after the end marker is a terminator, not a command."""
    root, _ = _project(tmp_path, entries=5)
    assert next_free_opcode(root) == 5     # not 6


def test_next_free_opcode_follows_the_last_real_entry(tmp_path):
    root, _ = _project(tmp_path, entries=213)
    assert next_free_opcode(root) == 0xD5


# ─────────────────────────────────────────────────────────────────────────────
# Refusing rather than guessing
# ─────────────────────────────────────────────────────────────────────────────

def test_refuses_a_table_that_is_not_a_dense_run(tmp_path):
    """A gap means the index assumption is wrong, so guessing is unsafe."""
    root, _ = _project(tmp_path, entries=4)
    p = os.path.join(root, 'data', 'script_cmd_table.inc')
    text = open(p, encoding='utf-8').read().replace('@ 0x03', '@ 0x09')
    open(p, 'w', encoding='utf-8').write(text)
    with pytest.raises(SpriteSwapPatchError, match='dense'):
        next_free_opcode(root)


def test_refuses_when_the_project_already_defines_the_macro(tmp_path):
    root, _ = _project(tmp_path)
    p = os.path.join(root, 'asm', 'macros', 'event.inc')
    with open(p, 'a', encoding='utf-8') as fh:
        fh.write(f'\n\t.macro {COMMAND_NAME} a, b\n\t.byte 0x99\n\t.endm\n')
    with pytest.raises(SpriteSwapPatchError, match='already defines'):
        ensure_sprite_swap_support(root)


def test_refuses_a_project_missing_the_files(tmp_path):
    (tmp_path / 'asm' / 'macros').mkdir(parents=True)
    with pytest.raises(SpriteSwapPatchError, match='not found'):
        ensure_sprite_swap_support(str(tmp_path))


# ─────────────────────────────────────────────────────────────────────────────
# Install behaviour
# ─────────────────────────────────────────────────────────────────────────────

def test_installs_all_three_edits(tmp_path):
    root, _ = _project(tmp_path)
    assert not is_installed(root)
    ensure_sprite_swap_support(root)
    assert is_installed(root)
    macros = open(os.path.join(root, 'asm/macros/event.inc'), encoding='utf-8').read()
    scrcmd = open(os.path.join(root, 'src/scrcmd.c'), encoding='utf-8').read()
    table = open(os.path.join(root, 'data/script_cmd_table.inc'), encoding='utf-8').read()
    assert f'.macro {COMMAND_NAME}' in macros
    assert f'ScrCmd_{COMMAND_NAME}(struct ScriptContext' in scrcmd
    assert f'.4byte ScrCmd_{COMMAND_NAME}' in table


def test_is_idempotent(tmp_path):
    root, _ = _project(tmp_path)
    ensure_sprite_swap_support(root)
    snapshot = {f: open(os.path.join(root, f), encoding='utf-8').read()
                for f in ('asm/macros/event.inc', 'src/scrcmd.c',
                          'data/script_cmd_table.inc')}
    msg = ensure_sprite_swap_support(root)
    assert 'already installed' in msg
    for f, before in snapshot.items():
        assert open(os.path.join(root, f), encoding='utf-8').read() == before


def test_the_entry_goes_before_the_end_marker(tmp_path):
    """After the terminator it would never be reached."""
    root, _ = _project(tmp_path)
    ensure_sprite_swap_support(root)
    text = open(os.path.join(root, 'data/script_cmd_table.inc'),
                encoding='utf-8').read()
    assert text.index(f'ScrCmd_{COMMAND_NAME}') < text.index(_TABLE_END)


def test_handler_keeps_the_turn_that_makes_the_swap_render(tmp_path):
    """ObjectEventSetGraphicsId allocates VRAM but does not fill it — without
    the follow-up turn the sprite renders as garbage. Documented in bomb.c."""
    root, _ = _project(tmp_path)
    ensure_sprite_swap_support(root)
    scrcmd = open(os.path.join(root, 'src/scrcmd.c'), encoding='utf-8').read()
    # Match the CALLS, not the comment above them that names both.
    set_at = scrcmd.index('ObjectEventSetGraphicsId(objectEvent')
    turn_at = scrcmd.index('ObjectEventTurn(objectEvent')
    assert set_at < turn_at, 'the turn must follow the swap, not precede it'


def test_handler_routes_the_player_separately(tmp_path):
    """The player is not in the map's object event list."""
    root, _ = _project(tmp_path)
    ensure_sprite_swap_support(root)
    scrcmd = open(os.path.join(root, 'src/scrcmd.c'), encoding='utf-8').read()
    assert 'OBJ_EVENT_ID_PLAYER' in scrcmd
    assert 'gPlayerAvatar.objectEventId' in scrcmd


def test_describe_says_what_would_happen_before_anything_is_written(tmp_path):
    root, _ = _project(tmp_path, entries=213)
    before = describe(root)
    assert 'not installed' in before and '0xD5' in before
    assert not is_installed(root)
    ensure_sprite_swap_support(root)
    assert 'installed' in describe(root)
