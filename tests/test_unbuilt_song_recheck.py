"""A song stays "Build required" long after it has been built.

PoryDAW writes a song's table row and its `.mid`; it does not write the `.s`,
which is a build artifact. So a freshly registered song has no `.s` until the
project is built, and the Sound Editor correctly says so — it stores the
sentinel ``"MISSING_S_FILE"`` and greys out Play and Piano Roll.

It then cached that sentinel exactly like a parsed song. Selecting the song
again never re-checked, so it went on reporting "Build required" and stayed
unplayable for the whole session, even after a successful build had written
its `.s` minutes earlier. Restarting the app was the only way out, and nothing
on screen hinted at that.

Observed: `mus_castle_town.s` existed, 45,256 bytes, parsing cleanly to 7
tracks on voicegroup013 — and the editor still refused to open it.

The second half is what the panel showed meanwhile. The missing-file branch
set its labels and disabled its buttons, but never cleared the TRACK LIST, so
an unbuilt song was displayed with whatever the last song left behind: a full
table of instruments and note counts, directly beneath the words "Build
required". A populated table reads as fact.

These test the two decisions without building a whole Sound Editor: the cache
rule, and the branch's duty to clear.
"""
from __future__ import annotations

import os

import pytest

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

pytest.importorskip('PyQt6.QtWidgets')

import ui.sound_editor_tab as sed  # noqa: E402


class _CacheOnly:
    """Just enough object to exercise the cache rule."""

    def __init__(self, cache):
        self._all_songs = cache

    needs_parse = sed.SoundEditorTab._needs_parse


# ─────────────────────────────────────────────────────────────────────────────
# The cache rule
# ─────────────────────────────────────────────────────────────────────────────

def test_an_unseen_song_is_parsed():
    assert _CacheOnly({}).needs_parse('mus_castle_town') is True


def test_a_song_with_no_s_is_checked_again():
    """THE regression. The sentinel used to stick for the whole session."""
    cache = {'mus_castle_town': 'MISSING_S_FILE'}
    assert _CacheOnly(cache).needs_parse('mus_castle_town') is True


def test_a_parsed_song_is_not_re_read():
    """The control. Without it, "always re-parse" would pass the test above
    while re-reading every song on every click."""
    cache = {'mus_castle_town': object()}
    assert _CacheOnly(cache).needs_parse('mus_castle_town') is False


def test_one_songs_state_does_not_decide_anothers():
    cache = {'mus_a': 'MISSING_S_FILE', 'mus_b': object()}
    c = _CacheOnly(cache)
    assert c.needs_parse('mus_a') is True
    assert c.needs_parse('mus_b') is False
    assert c.needs_parse('mus_c') is True


# ─────────────────────────────────────────────────────────────────────────────
# Parsing after a build
# ─────────────────────────────────────────────────────────────────────────────

def test_the_sentinel_is_replaced_once_the_file_appears(tmp_path):
    """What a build actually changes, end to end through `_parse_song`."""
    midi = tmp_path / 'sound' / 'songs' / 'midi'
    midi.mkdir(parents=True)

    class _Entry:
        label = 'mus_castle_town'

    class _Tab:
        _project_root = str(tmp_path)
        _all_songs: dict = {}
        _parse_song = sed.SoundEditorTab._parse_song
        _needs_parse = sed.SoundEditorTab._needs_parse

    tab = _Tab()

    # Before the build: no .s, so the sentinel is stored.
    tab._parse_song(_Entry())
    assert tab._all_songs['mus_castle_town'] == 'MISSING_S_FILE'
    assert tab._needs_parse('mus_castle_town') is True

    # The build writes one.
    (midi / 'mus_castle_town.s').write_text(
        '\t.include "MPlayDef.s"\n'
        '\t.equ mus_castle_town_grp, voicegroup013\n'
        '\t.section .rodata\n'
        '\t.global mus_castle_town\n'
        'mus_castle_town:\n'
        '\t.byte 1\n'
        '\t.byte 0\n'
        '\t.byte 0\n'
        '\t.byte 0\n'
        '\t.word mus_castle_town_grp\n',
        encoding='utf-8')

    tab._parse_song(_Entry())
    assert tab._all_songs['mus_castle_town'] != 'MISSING_S_FILE', (
        'the editor still thinks this song has not been built'
    )


def test_a_song_that_is_still_unbuilt_keeps_the_sentinel(tmp_path):
    midi = tmp_path / 'sound' / 'songs' / 'midi'
    midi.mkdir(parents=True)

    class _Entry:
        label = 'mus_nothing'

    class _Tab:
        _project_root = str(tmp_path)
        _all_songs: dict = {}
        _parse_song = sed.SoundEditorTab._parse_song

    tab = _Tab()
    tab._parse_song(_Entry())
    tab._parse_song(_Entry())
    assert tab._all_songs['mus_nothing'] == 'MISSING_S_FILE'


# ─────────────────────────────────────────────────────────────────────────────
# The branch must clear what it cannot vouch for
# ─────────────────────────────────────────────────────────────────────────────

def test_the_unbuilt_branch_clears_the_track_list():
    """Structural, and deliberately so.

    Reaching this branch for real means constructing the whole Sound Editor
    and a project to go with it. What actually went wrong is one missing
    statement, and its absence is exactly what this reads for: the
    missing-file branch must clear `_track_tree`, because otherwise it shows
    the previous song's instruments and note counts under the words "Build
    required".
    """
    import ast
    import inspect

    import textwrap

    src = textwrap.dedent(
        inspect.getsource(sed.SoundEditorTab._on_song_selected))
    tree = ast.parse(src)

    branch = None
    for node in ast.walk(tree):
        if not isinstance(node, ast.If):
            continue
        test = node.test
        if (isinstance(test, ast.Compare)
                and isinstance(test.comparators[0], ast.Constant)
                and test.comparators[0].value == 'MISSING_S_FILE'):
            branch = node
            break

    assert branch is not None, 'the missing-.s branch has moved or gone'

    clears = [
        n for n in ast.walk(branch)
        if isinstance(n, ast.Call)
        and isinstance(n.func, ast.Attribute)
        and n.func.attr == 'clear'
        and isinstance(n.func.value, ast.Attribute)
        and n.func.value.attr == '_track_tree'
    ]
    assert clears, (
        'the unbuilt-song branch does not clear the track list, so it will '
        'show the previous song\'s tracks'
    )
