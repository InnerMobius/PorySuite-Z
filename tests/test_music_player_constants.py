"""A PoryDAW-registered song has to assemble, on anybody's project.

PoryDAW is the music editor most pokefirered projects use. When it registers a
song it writes pokeemerald's symbolic constants::

    song mus_castle_town, MUSIC_PLAYER_BGM, 0

Both `MUSIC_PLAYER_BGM` and `MUSIC_PLAYER_SE1` are strings inside
porydaw.exe, and its UI shows no such field, so a user cannot know it is
happening. pokeemerald defines those names; **pokefirered does not** —
checked against a clean vanilla tree, the name appears nowhere in it.

The song macro emits `.2byte \\music_player`, so the name has no value and the
project stops assembling. That is not one person's bug. It is every
pokefirered project whose owner adds a song in PoryDAW.

Hence the constraints these tests exist to hold, in order of importance:

1. **A project that does not need this is never touched.** PorySuite serves
   plenty of people who have never opened PoryDAW. On their tree every
   function here must do nothing at all.
2. **Values are derived from the project, never assumed.** Row order in
   `music_player_table.inc` IS the index. A name resolved from a guess points
   a song at the wrong channel — which plays, just wrong, which is worse than
   failing.
3. **A name the project cannot explain is reported, never invented.**
4. **Running it twice changes nothing.**

Projects here are built in temp directories.
"""
from __future__ import annotations

import os

import pytest

from core.sound import music_player_constants as mpc

PLAYERS = """\
\t.equiv TRACK_SIZE, 0x50
\t.equiv NUM_TRACKS_BGM, 10

\t.align 2
gMPlayTable::
\tmusic_player gMPlayInfo_BGM, gMPlayTrack_BGM, NUM_TRACKS_BGM, 0
\tmusic_player gMPlayInfo_SE1, gMPlayTrack_SE1, NUM_TRACKS_SE1, 1
\tmusic_player gMPlayInfo_SE2, gMPlayTrack_SE2, NUM_TRACKS_SE2, 1
"""

VANILLA_SONGS = """\
gSongTable::
\tsong mus_dummy, 0, 0
\tsong se_use_item, 1, 1
"""

PORYDAW_SONGS = """\
gSongTable::
\tsong mus_dummy, 0, 0
\tsong se_use_item, 1, 1
\tsong mus_castle_town, MUSIC_PLAYER_BGM, 0
"""


def _project(tmp_path, songs: str, players: str = PLAYERS):
    # newline='' so a fixture that deliberately uses CRLF gets CRLF on disk.
    # Path.write_text translates, which would turn '\r\n' into '\r\r\n' here
    # and make the ending test assert something that was never true.
    (tmp_path / 'sound').mkdir(parents=True, exist_ok=True)
    for name, text in (('song_table.inc', songs),
                       ('music_player_table.inc', players)):
        with open(tmp_path / 'sound' / name, 'w',
                  encoding='utf-8', newline='') as fh:
            fh.write(text)
    return str(tmp_path)


def _players_text(root: str) -> str:
    return open(os.path.join(root, mpc.PLAYER_TABLE), encoding='utf-8').read()


# ─────────────────────────────────────────────────────────────────────────────
# A project that does not need this is never touched
# ─────────────────────────────────────────────────────────────────────────────

def test_a_vanilla_project_needs_nothing(tmp_path):
    """The most important test here.

    PorySuite serves plenty of people who have never opened PoryDAW.
    """
    root = _project(tmp_path, VANILLA_SONGS)
    found = mpc.inspect(root)
    assert found.referenced == set()
    assert found.missing == {}
    assert not found.breaks_build


def test_a_vanilla_project_is_not_even_searched(tmp_path):
    """They should not pay for a walk of the tree to be told nothing is wrong.

    Without this the short-circuit is invisible: removing it changes no
    answer, only how much work every project that never used PoryDAW does on
    every call.
    """
    root = _project(tmp_path, VANILLA_SONGS)
    assert mpc.inspect(root).deep_scanned is False


def test_a_project_that_needs_it_is_searched(tmp_path):
    """The control, so the test above cannot pass by never scanning at all."""
    root = _project(tmp_path, PORYDAW_SONGS)
    assert mpc.inspect(root).deep_scanned is True


def test_a_vanilla_project_is_not_written_to(tmp_path):
    root = _project(tmp_path, VANILLA_SONGS)
    before = open(os.path.join(root, mpc.PLAYER_TABLE), 'rb').read()
    changed, msg = mpc.apply(root)
    assert not changed
    assert open(os.path.join(root, mpc.PLAYER_TABLE), 'rb').read() == before
    assert 'nothing to define' in msg


def test_inspecting_never_writes(tmp_path):
    root = _project(tmp_path, PORYDAW_SONGS)
    before = open(os.path.join(root, mpc.PLAYER_TABLE), 'rb').read()
    mpc.inspect(root)
    assert open(os.path.join(root, mpc.PLAYER_TABLE), 'rb').read() == before


def test_a_project_that_already_defines_them_is_left_alone(tmp_path):
    """Defining a name twice is an assembler error, so this matters."""
    root = _project(tmp_path, PORYDAW_SONGS,
                    players='\t.equiv MUSIC_PLAYER_BGM, 0\n' + PLAYERS)
    before = _players_text(root)
    changed, msg = mpc.apply(root)
    assert not changed
    assert 'already defined' in msg
    assert _players_text(root) == before


def test_a_c_style_define_elsewhere_also_counts(tmp_path):
    root = _project(tmp_path, PORYDAW_SONGS)
    inc = os.path.join(root, 'include', 'constants')
    os.makedirs(inc)
    with open(os.path.join(inc, 'm4a.h'), 'w', encoding='utf-8') as fh:
        fh.write('#define MUSIC_PLAYER_BGM 0\n')
    assert mpc.inspect(root).missing == {}


# ─────────────────────────────────────────────────────────────────────────────
# Values come from the project
# ─────────────────────────────────────────────────────────────────────────────

def test_the_index_is_the_row_order(tmp_path):
    root = _project(tmp_path, PORYDAW_SONGS)
    assert mpc.player_indices(root) == {
        'MUSIC_PLAYER_BGM': 0,
        'MUSIC_PLAYER_SE1': 1,
        'MUSIC_PLAYER_SE2': 2,
    }


def test_a_reordered_table_gives_a_different_value(tmp_path):
    """Not assumed from the name.

    A tool that hardcoded BGM=0 would silently put this project's song on the
    wrong music player, and it would play.
    """
    reordered = PLAYERS.replace(
        '\tmusic_player gMPlayInfo_BGM, gMPlayTrack_BGM, NUM_TRACKS_BGM, 0\n',
        '')
    reordered += ('\tmusic_player gMPlayInfo_BGM, gMPlayTrack_BGM, '
                  'NUM_TRACKS_BGM, 0\n')
    root = _project(tmp_path, PORYDAW_SONGS, players=reordered)
    assert mpc.inspect(root).missing == {'MUSIC_PLAYER_BGM': 2}


def test_the_written_value_matches_the_project(tmp_path):
    reordered = PLAYERS.replace(
        '\tmusic_player gMPlayInfo_BGM, gMPlayTrack_BGM, NUM_TRACKS_BGM, 0\n',
        '')
    reordered += ('\tmusic_player gMPlayInfo_BGM, gMPlayTrack_BGM, '
                  'NUM_TRACKS_BGM, 0\n')
    root = _project(tmp_path, PORYDAW_SONGS, players=reordered)
    mpc.apply(root)
    assert '.equiv MUSIC_PLAYER_BGM, 2' in _players_text(root)


# ─────────────────────────────────────────────────────────────────────────────
# A name the project cannot explain is reported, never invented
# ─────────────────────────────────────────────────────────────────────────────

def test_an_unexplainable_name_is_reported(tmp_path):
    root = _project(tmp_path, PORYDAW_SONGS.replace('MUSIC_PLAYER_BGM',
                                                    'MUSIC_PLAYER_WHATEVER'))
    found = mpc.inspect(root)
    assert found.unknown == {'MUSIC_PLAYER_WHATEVER'}
    assert found.breaks_build


def test_an_unexplainable_name_is_not_invented(tmp_path):
    root = _project(tmp_path, PORYDAW_SONGS.replace('MUSIC_PLAYER_BGM',
                                                    'MUSIC_PLAYER_WHATEVER'))
    before = _players_text(root)
    changed, msg = mpc.apply(root)
    assert not changed
    assert 'will not invent' in msg
    assert _players_text(root) == before


def test_the_explainable_ones_are_still_defined(tmp_path):
    """One name nobody can explain must not block the rest."""
    songs = PORYDAW_SONGS + '\tsong mus_x, MUSIC_PLAYER_WHATEVER, 0\n'
    root = _project(tmp_path, songs)
    changed, msg = mpc.apply(root)
    assert changed
    assert '.equiv MUSIC_PLAYER_BGM, 0' in _players_text(root)
    assert 'MUSIC_PLAYER_WHATEVER' in msg
    assert '.equiv MUSIC_PLAYER_WHATEVER' not in _players_text(root)


# ─────────────────────────────────────────────────────────────────────────────
# The repair itself
# ─────────────────────────────────────────────────────────────────────────────

def test_the_missing_name_is_defined(tmp_path):
    root = _project(tmp_path, PORYDAW_SONGS)
    changed, _ = mpc.apply(root)
    assert changed
    assert '.equiv MUSIC_PLAYER_BGM, 0' in _players_text(root)


def test_it_is_defined_before_the_table_that_uses_it(tmp_path):
    root = _project(tmp_path, PORYDAW_SONGS)
    mpc.apply(root)
    text = _players_text(root)
    assert text.index('MUSIC_PLAYER_BGM') < text.index('gMPlayTable::')


def test_the_existing_content_survives(tmp_path):
    root = _project(tmp_path, PORYDAW_SONGS)
    before = _players_text(root)
    mpc.apply(root)
    after = _players_text(root)
    assert before in after, 'the player table lost something'


def test_running_it_twice_changes_nothing(tmp_path):
    root = _project(tmp_path, PORYDAW_SONGS)
    mpc.apply(root)
    once = _players_text(root)
    changed, msg = mpc.apply(root)
    assert not changed
    assert _players_text(root) == once


def test_the_second_field_is_read_too(tmp_path):
    songs = PORYDAW_SONGS.replace('MUSIC_PLAYER_BGM, 0',
                                  '0, MUSIC_PLAYER_SE1')
    root = _project(tmp_path, songs)
    assert mpc.inspect(root).missing == {'MUSIC_PLAYER_SE1': 1}


def test_several_names_are_all_defined(tmp_path):
    songs = PORYDAW_SONGS + '\tsong mus_y, MUSIC_PLAYER_SE2, 0\n'
    root = _project(tmp_path, songs)
    mpc.apply(root)
    text = _players_text(root)
    assert '.equiv MUSIC_PLAYER_BGM, 0' in text
    assert '.equiv MUSIC_PLAYER_SE2, 2' in text


def test_crlf_is_preserved(tmp_path):
    root = _project(tmp_path, PORYDAW_SONGS,
                    players=PLAYERS.replace('\n', '\r\n'))
    mpc.apply(root)
    raw = open(os.path.join(root, mpc.PLAYER_TABLE), 'rb').read()
    assert raw.count(b'\n') == raw.count(b'\r\n'), 'a bare LF was introduced'


def test_a_missing_player_table_is_survivable(tmp_path):
    root = _project(tmp_path, PORYDAW_SONGS)
    os.remove(os.path.join(root, mpc.PLAYER_TABLE))
    changed, msg = mpc.apply(root)
    assert not changed
    assert msg
