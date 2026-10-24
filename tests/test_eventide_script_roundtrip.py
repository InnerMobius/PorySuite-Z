"""Regression guard: EVENTide must not change a script it was only asked to re-save.

THE BUGS (found 2026-10-24 by round-tripping all 453 maps through EVENTide's own
parser and writer). Every one of these fired on a plain Save of an unedited map:

  1. `delay_16` / `delay_8` / `delay_4` became `delay _16`.
     The parser matched commands with `stripped.startswith('delay')`, which has no
     word boundary, so the movement macros were swallowed and re-emitted split in
     two. `delay _16` is not a command -- the project would stop assembling. 241 lines
     were affected. The same hazard hid `applymovementat` behind `applymovement`.

  2. `message X` was rewritten as `msgbox X`.
     They are different opcodes: `message` is 0x67 + a text pointer (show the words,
     nothing else), `msgbox` is loadword + callstd (the whole show / wait / button /
     close routine). 112 lines changed behaviour -- e.g. a `message` + `waitmessage`
     + `multichoice` sequence had its window closed before the multichoice ran.

  3. `warp MAP, warpId, x, y` lost its Y.
     The parse regex and the writer were both hard-coded to three arguments; the
     engine macro takes up to four. The player landed on the wrong tile.

  4. `lock`, `release` and `end` were injected into scripts that never had them.
     +289 locks, +197 releases, +748 ends project-wide. A `lock` added to an
     On Transition map script freezes the player on map entry, and an `end` added
     to a `<Map>_MapScripts::` table writes a stray opcode past its `.byte 0`
     terminator. An `end` appended to a block that deliberately falls through into
     the next label (vanilla does this) severs the fall-through.

  5. Saving a map rewrote the SHARED script files it had merely read, with every
     comment stripped out. (The "only write what changed" half of that fix is UI
     state in EventEditorTab; what this file guards is that the writer preserves
     comments and blank lines at all.)

THE RULES:
  * The parser matches on the first whitespace-delimited token, never on a prefix.
  * `message` keeps its own render mode ('raw') so the writer can tell it from msgbox.
  * Warps carry however many arguments they were written with.
  * The writer emits ONLY what the command list holds -- it never guesses at a
    missing lock/release, never terminates a data table, and never terminates a
    block that fell through in the original file.
  * Comments and blank lines inside a block survive a round trip.

If you touch `_parse_script_lines`, `lines_from_commands`, `_render_label_block`
or `write_scripts_inc`, keep this green.

KNOWN GAP, deliberately tolerated below: a trailing inline `@ comment` on a
*command* line is still dropped (2 lines project-wide). Comment lines of their own
and comments on `Label::` lines are preserved. If that is ever fixed, tighten
`_commands()` to stop stripping them.
"""

import os
import sys

import pytest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from eventide.backend.eventide_utils import (  # noqa: E402
    _parse_script_lines,
    lines_from_commands,
    parse_scripts_inc,
    parse_all_texts,
    write_scripts_inc,
)


def _parse(*lines):
    """Parse raw script lines the way the map reader does."""
    return _parse_script_lines(list(lines), {})


def _write(cmds, texts=None):
    """Render command tuples back to script lines, stripped of newlines."""
    return [ln.rstrip('\n') for ln in lines_from_commands(cmds, texts or {})]


def _roundtrip(*lines):
    """Parse then re-render, returning the rendered lines."""
    return _write(_parse(*lines))


# ─────────────────────────────────────────────────────────────────────────────
# Bug 1 — prefix matching without a word boundary
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize('macro', ['delay_1', 'delay_4', 'delay_8',
                                   'delay_16', 'delay_32'])
def test_delay_movement_macros_are_not_split(macro):
    """`delay_16` must survive as `delay_16`, not become `delay _16`."""
    assert _parse(f'\t{macro}') == [(macro,)]
    assert _roundtrip(f'\t{macro}') == [macro]


def test_real_delay_command_still_parses():
    """The fix must not break the actual `delay` command."""
    assert _parse('\tdelay 30') == [('delay', '30')]
    assert _roundtrip('\tdelay 30') == ['delay 30']


def test_applymovementat_is_not_swallowed_by_applymovement():
    """`applymovementat` is its own command, not `applymovement` + junk."""
    line = '\tapplymovementat LOCALID_X, Movement_Y, 3, 4'
    assert _parse(line)[0][0] == 'applymovementat'
    assert _roundtrip(line) == ['applymovementat LOCALID_X, Movement_Y, 3, 4']


def test_unknown_command_sharing_a_prefix_round_trips():
    """A command whose name merely starts with a known one is passed through."""
    assert _roundtrip('\tsetflagsomething FOO') == ['setflagsomething FOO']


# ─────────────────────────────────────────────────────────────────────────────
# Bug 2 — `message` is not `msgbox`
# ─────────────────────────────────────────────────────────────────────────────

def test_message_is_not_rewritten_as_msgbox():
    assert _roundtrip('\tmessage Text_Hello') == ['message Text_Hello']


def test_message_carries_the_raw_render_mode():
    (cmd,) = _parse('\tmessage Text_Hello')
    assert cmd[0] == 'message' and cmd[4] == 'raw'


def test_msgbox_still_round_trips_with_its_type():
    assert _roundtrip('\tmsgbox Text_Sign, MSGBOX_SIGN') == [
        'msgbox Text_Sign, MSGBOX_SIGN']


def test_message_then_multichoice_keeps_the_window_open():
    """The exact vanilla sequence that bug 2 broke (BirthIsland_Harbor)."""
    out = _roundtrip('\tmessage Text_WhereTo',
                     '\twaitmessage',
                     '\tmultichoice 20, 8, MULTICHOICE_X, FALSE')
    assert out[0] == 'message Text_WhereTo'
    assert out[1] == 'waitmessage'


# ─────────────────────────────────────────────────────────────────────────────
# Bug 3 — warp arity
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize('line', [
    'warp MAP_PALLET_TOWN',
    'warp MAP_PALLET_TOWN, 3',
    'warp MAP_PALLET_TOWN, 26, 30',
    'warp MAP_SAFARI_ZONE_CENTER, 255, 26, 30',
    'warpsilent MAP_X, 1, 2, 3',
    'warpdoor MAP_X, 5, 6',
])
def test_every_warp_arity_round_trips(line):
    assert _roundtrip('\t' + line) == [line]


def test_four_argument_warp_keeps_its_y():
    (cmd,) = _parse('\twarp MAP_SAFARI_ZONE_CENTER, 255, 26, 30')
    assert cmd == ('warp', 'MAP_SAFARI_ZONE_CENTER', 255, 26, 30)


# ─────────────────────────────────────────────────────────────────────────────
# Bug 4 — no injected lock / release / end
# ─────────────────────────────────────────────────────────────────────────────

def test_applymovement_does_not_get_a_lock_injected():
    """An On Transition script that moves an NPC must not gain a `lock`."""
    out = _roundtrip('\tapplymovement LOCALID_X, Movement_Y',
                     '\twaitmovement 0')
    assert out == ['applymovement LOCALID_X, Movement_Y', 'waitmovement 0']


def test_call_does_not_get_a_lock_injected():
    assert _roundtrip('\tcall Some_Script') == ['call Some_Script']


def test_writer_adds_nothing_at_all_to_a_plain_script():
    lines = ['\tlock', '\tfaceplayer', '\tmsgbox Text_Hi', '\trelease', '\tend']
    assert _roundtrip(*lines) == [ln.strip() for ln in lines]


def _write_block(tmp_path, body):
    """Run one label block through write_scripts_inc and return the result."""
    path = tmp_path / 'scripts.inc'
    path.write_text(body, encoding='utf-8')
    scripts, hidden = parse_scripts_inc(tmp_path, {})
    write_scripts_inc(dict(scripts), hidden, {}, path)
    return path.read_text(encoding='utf-8')


def test_map_scripts_data_table_does_not_get_an_end(tmp_path):
    """A `<Map>_MapScripts::` table is data; `end` past its `.byte 0` is junk."""
    out = _write_block(tmp_path, (
        'Town_MapScripts::\n'
        '\tmap_script MAP_SCRIPT_ON_TRANSITION, Town_OnTransition\n'
        '\t.byte 0\n'
    ))
    assert 'end' not in out.split()


def test_fallthrough_block_does_not_get_an_end(tmp_path):
    """Vanilla lets a block run on into the next label; `end` would cut it."""
    out = _write_block(tmp_path, (
        'Town_EventScript_TrySet::\n'
        '\tgoto_if_unset FLAG_X, Town_EventScript_Done\n'
        '\tsetobjectxyperm LOCALID_X, 16, 14\n'
        'Town_EventScript_Done::\n'
        '\treturn\n'
    ))
    before_done = out.split('Town_EventScript_Done::')[0]
    assert 'end' not in before_done.split()


def test_terminated_block_keeps_exactly_one_end(tmp_path):
    out = _write_block(tmp_path, 'S::\n\tmsgbox Text_A\n\tend\n')
    assert out.split().count('end') == 1


def test_unterminated_new_script_still_gets_an_end(tmp_path):
    """Several New Script templates rely on the writer supplying the `end`."""
    path = tmp_path / 'scripts.inc'
    write_scripts_inc({'S': [('msgbox', 'Text_A')]}, {}, {}, path)
    assert path.read_text(encoding='utf-8').split()[-1] == 'end'


# ─────────────────────────────────────────────────────────────────────────────
# Bug 5 — comments and blank lines survive
# ─────────────────────────────────────────────────────────────────────────────

def test_comments_and_blank_lines_survive_a_round_trip(tmp_path):
    body = (
        'S::\n'
        '\tlock\n'
        '\n'
        '\t@ explain the next bit\n'
        '\tmsgbox Text_A\n'
        '\trelease\n'
        '\tend\n'
    )
    out = _write_block(tmp_path, body)
    assert '@ explain the next bit' in out


def test_comment_on_a_label_line_survives(tmp_path):
    out = _write_block(tmp_path, 'S:: @ 8169002\n\tmsgbox Text_A\n\tend\n')
    assert 'S:: @ 8169002' in out


# ─────────────────────────────────────────────────────────────────────────────
# Whole-project sweep — the check that found all five bugs
# ─────────────────────────────────────────────────────────────────────────────

_PROJECT = os.path.join(_ROOT, 'pokefirered')
_MAPS = os.path.join(_PROJECT, 'data', 'maps')

requires_project = pytest.mark.skipif(
    not os.path.isdir(_MAPS),
    reason='needs the pokefirered test project at porysuite/pokefirered')


def _split_comment(line):
    """Return (code, comment) for a script line, honouring quoted strings.

    An `@` inside a "..." string is text, not the start of a comment, so only
    the segments outside quotes are scanned.
    """
    out, in_string = [], False
    for i, seg in enumerate(line.split('"')):
        if i % 2 == 1:                      # inside a quoted string
            out.append(seg)
            in_string = True
            continue
        at = seg.find('@')
        if at >= 0:
            out.append(seg[:at])
            code = '"'.join(out)
            if in_string:
                code = code if code.count('"') % 2 == 0 else code + '"'
            return ' '.join(code.split()), seg[at:].strip()
        out.append(seg)
    return ' '.join('"'.join(out).split()), ''


def _commands(text):
    """Multiset of executable lines, whitespace-normalised, comments removed."""
    from collections import Counter
    found = Counter()
    for line in text.splitlines():
        code, _ = _split_comment(line)
        if code:
            found[code] += 1
    return found


def _comment_lines(text):
    """Multiset of whole-line `@` comments (the ones bug 5 deleted)."""
    from collections import Counter
    return Counter(
        ' '.join(ln.split()) for ln in text.splitlines()
        if ln.strip().startswith('@'))


def _map_dirs():
    return sorted(
        os.path.join(_MAPS, n) for n in os.listdir(_MAPS)
        if os.path.isfile(os.path.join(_MAPS, n, 'scripts.inc')))


@requires_project
def test_every_map_script_survives_a_save(tmp_path):
    """Re-saving an unedited map must not change one executable line.

    This is the check that caught all five bugs. It runs the real parser over
    every map in the test project, re-renders it with the real writer, and
    compares the command streams. Whitespace and the one known trailing-inline-
    comment gap are normalised away; anything else is a regression.
    """
    import shutil

    broken = []
    for map_dir in _map_dirs():
        src = os.path.join(map_dir, 'scripts.inc')
        texts = parse_all_texts(__import__('pathlib').Path(map_dir),
                                __import__('pathlib').Path(_PROJECT))
        scripts, hidden = parse_scripts_inc(
            __import__('pathlib').Path(map_dir), texts)
        if not scripts:
            continue
        dest = tmp_path / 'scripts.inc'
        shutil.copyfile(src, dest)
        write_scripts_inc(dict(scripts), hidden, dict(texts), dest)

        with open(src, encoding='utf-8') as fh:
            before = fh.read()
        after = dest.read_text(encoding='utf-8')

        added = _commands(after) - _commands(before)
        lost = _commands(before) - _commands(after)
        if added or lost:
            name = os.path.basename(map_dir)
            for line, n in list(added.items())[:3]:
                broken.append(f'{name}: ADDED {n}x  {line}')
            for line, n in list(lost.items())[:3]:
                broken.append(f'{name}: LOST  {n}x  {line}')

    assert not broken, (
        f'{len(broken)} script line(s) changed on a no-op save:\n  '
        + '\n  '.join(broken[:40]))


@requires_project
def test_no_map_loses_a_comment_line(tmp_path):
    """Whole-line `@` comments must survive a re-save of every map."""
    import shutil
    import pathlib

    broken = []
    for map_dir in _map_dirs():
        src = os.path.join(map_dir, 'scripts.inc')
        texts = parse_all_texts(pathlib.Path(map_dir), pathlib.Path(_PROJECT))
        scripts, hidden = parse_scripts_inc(pathlib.Path(map_dir), texts)
        if not scripts:
            continue
        dest = tmp_path / 'scripts.inc'
        shutil.copyfile(src, dest)
        write_scripts_inc(dict(scripts), hidden, dict(texts), dest)

        with open(src, encoding='utf-8') as fh:
            lost = _comment_lines(fh.read()) - _comment_lines(
                dest.read_text(encoding='utf-8'))
        for line, n in list(lost.items())[:3]:
            broken.append(f'{os.path.basename(map_dir)}: LOST {n}x  {line}')

    assert not broken, (
        f'{len(broken)} comment line(s) deleted on a no-op save:\n  '
        + '\n  '.join(broken[:40]))
