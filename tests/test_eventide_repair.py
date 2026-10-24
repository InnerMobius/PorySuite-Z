"""Regression guard: the EVENTide damage repair must fix real damage and nothing else.

`core/eventide_repair.py` edits the user's GAME SOURCE. A false positive there does
not produce a wrong colour on screen — it silently rewrites a script that was
correct. So the bar is higher than usual, and these tests are mostly about what it
must REFUSE to touch.

The three traps, all of them measured against a clean copy of the game before the
detectors were written:

  * `goto` followed by `end` looks exactly like dead code and is NOT. Vanilla does
    it 432 times. A detector that swept it would rewrite most of the project.
  * A command MISSING versus vanilla is the user's own edit, never damage. This
    project deleted all 59 `goto_if_questlog` lines along with the Quest Log system;
    "restoring" them would destroy real work.
  * An unindented file proves nothing on its own — 27 undamaged maps in this project
    are unindented, several of them upstream. Indentation is only repaired in a file
    that already has a structural finding.

If you add a detector, measure it against a clean copy first and add the matching
"must not touch" test here.
"""

import os
import sys

import pytest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _p in (_ROOT, os.path.join(_ROOT, 'core'), os.path.join(_ROOT, 'ui')):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from core.eventide_repair import (  # noqa: E402
    KIND_END_DATA,
    KIND_END_DEAD,
    KIND_INDENT,
    KIND_MESSAGE,
    REPAIRABLE,
    repair,
    scan,
    summarize,
)


def _project(tmp_path, name, body):
    """Build a throwaway project holding one map, and return its root."""
    d = tmp_path / 'data' / 'maps' / name
    d.mkdir(parents=True)
    (d / 'scripts.inc').write_text(body, encoding='utf-8')
    return tmp_path


def _text(tmp_path, name='Town'):
    return (tmp_path / 'data' / 'maps' / name / 'scripts.inc').read_text(
        encoding='utf-8')


def _kinds(findings):
    return summarize(findings)


# ─────────────────────────────────────────────────────────────────────────────
# What it must FIX
# ─────────────────────────────────────────────────────────────────────────────

CLERK = (
    'Town_MapScripts::\n'
    '\t.byte 0\n'
    'end\n'
    '\n'
    'Town_EventScript_Clerk::\n'
    '\tlock\n'
    '\tfaceplayer\n'
    '\tmsgbox Text_MayIHelpYou\n'
    '\twaitmessage\n'
    '\tpokemart Town_Mart_Items\n'
    '\trelease\n'
    '\tend\n'
)


def test_finds_the_three_damage_kinds(tmp_path):
    root = _project(tmp_path, 'Town', CLERK)
    counts = _kinds(scan(root))
    assert counts.get(KIND_END_DATA) == 1
    assert counts.get(KIND_MESSAGE) == 1


def test_msgbox_before_waitmessage_becomes_message(tmp_path):
    root = _project(tmp_path, 'Town', CLERK)
    repair(root, kinds={KIND_MESSAGE})
    out = _text(tmp_path)
    assert '\tmessage Text_MayIHelpYou\n\twaitmessage\n' in out
    assert 'msgbox Text_MayIHelpYou' not in out
    # The OTHER msgbox in the file must be left alone.
    assert 'pokemart Town_Mart_Items' in out


def test_stray_end_after_a_data_table_is_removed(tmp_path):
    root = _project(tmp_path, 'Town', CLERK)
    repair(root, kinds={KIND_END_DATA})
    body = _text(tmp_path).split('Town_EventScript_Clerk')[0]
    assert '.byte 0' in body
    assert 'end' not in body.split()


@pytest.mark.parametrize('terminator', ['end', 'return'])
def test_unreachable_end_after_a_terminator_is_removed(tmp_path, terminator):
    root = _project(tmp_path, 'Town',
                    f'Town_A::\n\tmsgbox T\n\t{terminator}\n'
                    f'\n@ a note for the next label\n'
                    f'end\n'
                    f'\nTown_B::\n\treturn\n')
    counts = _kinds(scan(root))
    assert counts.get(KIND_END_DEAD) == 1
    repair(root, kinds={KIND_END_DEAD})
    assert _text(tmp_path).split().count('end') == (
        1 if terminator == 'end' else 0)
    # the comment that caused the bug must survive the repair
    assert '@ a note for the next label' in _text(tmp_path)


def test_indentation_restored_only_in_a_damaged_file(tmp_path):
    root = _project(tmp_path, 'Town', CLERK)
    repair(root, kinds={KIND_INDENT})
    assert '\tend\n' in _text(tmp_path)          # the stray end got a tab too
    assert 'Town_MapScripts::' in _text(tmp_path)  # label lines stay flush


def test_comments_are_not_indented(tmp_path):
    """Vanilla keeps `@` comments flush-left 96 times against 9 indented, and
    most of them introduce the NEXT label. Indenting one would invent a
    difference from the clean copy instead of removing one."""
    root = _project(tmp_path, 'Town',
                    'Town_A::\n'
                    'msgbox T\n'
                    'return\n'
                    '\n'
                    '@ a note about the next label\n'
                    'end\n'
                    '\nTown_B::\n\treturn\n')
    repair(root)
    out = _text(tmp_path)
    assert '\n@ a note about the next label\n' in out   # still flush
    assert '\t@' not in out
    assert '\tmsgbox T\n' in out                        # real commands indented


# ─────────────────────────────────────────────────────────────────────────────
# What it must NOT touch — the expensive mistakes
# ─────────────────────────────────────────────────────────────────────────────

def test_goto_followed_by_end_is_left_alone(tmp_path):
    """Vanilla does this 432 times. Sweeping it would rewrite the project."""
    body = ('Town_A::\n'
            '\tmsgbox T\n'
            '\tgoto Town_B\n'
            '\tend\n'
            '\nTown_B::\n'
            '\tend\n')
    root = _project(tmp_path, 'Town', body)
    assert scan(root) == []
    repair(root)
    assert _text(tmp_path) == body


def test_a_clean_file_is_not_rewritten_at_all(tmp_path):
    body = ('Town_MapScripts::\n'
            '\tmap_script MAP_SCRIPT_ON_TRANSITION, Town_OnTransition\n'
            '\t.byte 0\n'
            '\nTown_OnTransition::\n'
            '\tsetflag FLAG_X\n'
            '\tend\n')
    root = _project(tmp_path, 'Town', body)
    assert scan(root) == []
    res = repair(root)
    assert res.files_written == []
    assert _text(tmp_path) == body


def test_unindented_but_undamaged_file_is_left_alone(tmp_path):
    """27 undamaged maps in the live project are unindented, some upstream.

    Indentation alone is not evidence, so a file with no structural finding
    must not be reindented.
    """
    body = 'Town_A::\nmsgbox T\nend\n'
    root = _project(tmp_path, 'Town', body)
    assert scan(root) == []
    repair(root)
    assert _text(tmp_path) == body


def test_missing_command_is_never_restored(tmp_path):
    """A command absent versus vanilla is the user's edit, not damage.

    This project removed all 59 `goto_if_questlog` lines with the Quest Log
    system. Nothing in the repair may infer damage from absence.
    """
    body = 'Town_Clerk::\n\tlock\n\tmsgbox T\n\trelease\n\tend\n'
    root = _project(tmp_path, 'Town', body)
    repair(root)
    assert 'questlog' not in _text(tmp_path)
    assert _text(tmp_path) == body


def test_typed_msgbox_before_waitmessage_is_left_alone(tmp_path):
    """Only the no-type msgbox form is the fingerprint of a rewritten message."""
    body = 'Town_A::\n\tmsgbox T, MSGBOX_SIGN\n\twaitmessage\n\tend\n'
    root = _project(tmp_path, 'Town', body)
    assert KIND_MESSAGE not in _kinds(scan(root))
    repair(root)
    assert _text(tmp_path) == body


def test_lock_review_is_never_auto_applied(tmp_path):
    """The lock/release findings are report-only — they cannot be told apart
    from the user's own edits, so `repair` must refuse them even if asked."""
    from core.eventide_repair import KIND_REVIEW_LOCK
    assert KIND_REVIEW_LOCK not in REPAIRABLE
    root = _project(tmp_path, 'Town', CLERK)
    before = _text(tmp_path)
    res = repair(root, kinds={KIND_REVIEW_LOCK})
    assert res.files_written == []
    assert _text(tmp_path) == before


# ─────────────────────────────────────────────────────────────────────────────
# Safety properties
# ─────────────────────────────────────────────────────────────────────────────

def test_repair_is_idempotent(tmp_path):
    root = _project(tmp_path, 'Town', CLERK)
    repair(root)
    once = _text(tmp_path)
    assert scan(root) == []
    repair(root)
    assert _text(tmp_path) == once


def test_repairing_one_kind_leaves_the_others_in_place(tmp_path):
    root = _project(tmp_path, 'Town', CLERK)
    repair(root, kinds={KIND_MESSAGE})
    counts = _kinds(scan(root))
    assert KIND_MESSAGE not in counts
    assert counts.get(KIND_END_DATA) == 1


def test_all_fixes_together_produce_the_expected_file(tmp_path):
    root = _project(tmp_path, 'Town', CLERK)
    repair(root)
    assert _text(tmp_path) == (
        'Town_MapScripts::\n'
        '\t.byte 0\n'
        '\n'
        'Town_EventScript_Clerk::\n'
        '\tlock\n'
        '\tfaceplayer\n'
        '\tmessage Text_MayIHelpYou\n'
        '\twaitmessage\n'
        '\tpokemart Town_Mart_Items\n'
        '\trelease\n'
        '\tend\n'
    )


def test_scan_never_writes(tmp_path):
    root = _project(tmp_path, 'Town', CLERK)
    before = _text(tmp_path)
    scan(root)
    scan(root)
    assert _text(tmp_path) == before


def test_missing_maps_directory_is_not_an_error(tmp_path):
    assert scan(tmp_path) == []
    assert repair(tmp_path).files_written == []
