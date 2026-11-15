"""A song table row written by another tool must not disappear.

`sound/song_table.inc` is a list, and an entry's index is its POSITION in that
list. So a row the parser cannot read is not one missing song — it renumbers
every song after it.

That is what happened. PoryDAW registers a song with pokeemerald's symbolic
constants (`MUSIC_PLAYER_BGM` and `MUSIC_PLAYER_SE1` are both strings inside
porydaw.exe), FireRed writes bare numbers and defines no such names, and the
parser's pattern was digits-only::

    _RE_SONG_MACRO = re.compile(r'song\\s+(\\w+)\\s*,\\s*(\\d+)\\s*,\\s*(\\d+)')

The row did not match, and was skipped without a word. Measured on the real
project: 385 rows in the file, 384 entries back, and PorySuite labelled
`mus_sevii_route` as **MUS_CASTLE_TOWN** — so clicking "Castle Town" in the
song list played sevii route, which is exactly how it was reported. 52 songs
after it carried the wrong constant too.

Two things matter here and they are different:

* **Reading.** A name is resolved against the project's own
  `music_player_table.inc`, where row order IS the index. Not a table written
  down in the tool: a project may add or reorder players, and resolving
  against a guess points a song at the wrong channel instead of failing.
* **Assembling.** Resolving a name for display does NOT make it assemble.
  `.2byte MUSIC_PLAYER_BGM` still needs a `#define` somewhere, and FireRed has
  none. A row can be perfectly understood by PorySuite and still break the
  build, so the two are tested apart.

Projects here are built in temp directories.
"""
from __future__ import annotations

import os

import pytest

from core.sound.song_table_manager import (
    load_song_table, parse_music_players, _parse_song_table,
)

PLAYER_TABLE = """\
\t.align 2
gMPlayTable::
\tmusic_player gMPlayInfo_BGM, gMPlayTrack_BGM, NUM_TRACKS_BGM, 0
\tmusic_player gMPlayInfo_SE1, gMPlayTrack_SE1, NUM_TRACKS_SE1, 1
\tmusic_player gMPlayInfo_SE2, gMPlayTrack_SE2, NUM_TRACKS_SE2, 1
\tmusic_player gMPlayInfo_SE3, gMPlayTrack_SE3, NUM_TRACKS_SE3, 0
"""


def _project(tmp_path, table_rows: str, players: str = PLAYER_TABLE):
    sound = tmp_path / 'sound'
    (sound / 'songs' / 'midi').mkdir(parents=True)
    (sound / 'song_table.inc').write_text(table_rows, encoding='utf-8')
    (sound / 'music_player_table.inc').write_text(players, encoding='utf-8')
    inc = tmp_path / 'include' / 'constants'
    inc.mkdir(parents=True)
    (inc / 'songs.h').write_text(
        '#define MUS_FIRST   0\n'
        '#define MUS_SECOND  1\n'
        '#define MUS_THIRD   2\n'
        '#define MUS_FOURTH  3\n', encoding='utf-8')
    (sound / 'songs' / 'midi' / 'midi.cfg').write_text('', encoding='utf-8')
    return str(tmp_path)


NUMERIC = """\
\tsong mus_first, 0, 0
\tsong mus_second, 0, 0
\tsong mus_third, 0, 0
\tsong mus_fourth, 0, 0
"""

#: The same table with one row written the way PoryDAW writes it.
SYMBOLIC = """\
\tsong mus_first, 0, 0
\tsong mus_second, MUSIC_PLAYER_BGM, 0
\tsong mus_third, 0, 0
\tsong mus_fourth, 0, 0
"""


# ─────────────────────────────────────────────────────────────────────────────
# Reading the project's own player list
# ─────────────────────────────────────────────────────────────────────────────

def test_the_players_come_from_the_projects_own_table(tmp_path):
    root = _project(tmp_path, NUMERIC)
    assert parse_music_players(root) == {
        'MUSIC_PLAYER_BGM': 0,
        'MUSIC_PLAYER_SE1': 1,
        'MUSIC_PLAYER_SE2': 2,
        'MUSIC_PLAYER_SE3': 3,
    }


def test_row_order_is_the_index_not_the_name(tmp_path):
    """A project may order its players however it likes.

    Resolving against a table written into the tool would point a song at the
    wrong channel here, and it would play — just wrong.
    """
    reordered = PLAYER_TABLE.replace(
        '\tmusic_player gMPlayInfo_BGM, gMPlayTrack_BGM, NUM_TRACKS_BGM, 0\n',
        '')
    reordered += ('\tmusic_player gMPlayInfo_BGM, gMPlayTrack_BGM, '
                  'NUM_TRACKS_BGM, 0\n')
    root = _project(tmp_path, NUMERIC, players=reordered)
    assert parse_music_players(root)['MUSIC_PLAYER_BGM'] == 3


def test_no_player_table_is_survivable(tmp_path):
    root = _project(tmp_path, NUMERIC)
    os.remove(os.path.join(root, 'sound', 'music_player_table.inc'))
    assert parse_music_players(root) == {}


# ─────────────────────────────────────────────────────────────────────────────
# The row is kept, and the indices hold
# ─────────────────────────────────────────────────────────────────────────────

def test_a_symbolic_row_is_not_dropped(tmp_path):
    root = _project(tmp_path, SYMBOLIC)
    labels = [e.label for e in load_song_table(root).entries]
    assert labels == ['mus_first', 'mus_second', 'mus_third', 'mus_fourth']


def test_every_later_song_keeps_its_own_constant(tmp_path):
    """THE regression. Dropping one row renumbered everything after it.

    On the real project that made PorySuite call `mus_sevii_route`
    MUS_CASTLE_TOWN, so the song list played the wrong song.
    """
    root = _project(tmp_path, SYMBOLIC)
    got = {e.label: e.constant for e in load_song_table(root).entries}
    assert got == {
        'mus_first': 'MUS_FIRST',
        'mus_second': 'MUS_SECOND',
        'mus_third': 'MUS_THIRD',
        'mus_fourth': 'MUS_FOURTH',
    }


def test_the_symbolic_row_resolves_to_its_number(tmp_path):
    root = _project(tmp_path, SYMBOLIC)
    entry = load_song_table(root).by_label('mus_second')
    assert entry.music_player == 0


def test_a_non_zero_player_resolves_too(tmp_path):
    root = _project(tmp_path, SYMBOLIC.replace('MUSIC_PLAYER_BGM',
                                               'MUSIC_PLAYER_SE2'))
    assert load_song_table(root).by_label('mus_second').music_player == 2


def test_numeric_rows_are_unaffected(tmp_path):
    """The format every other row in a FireRed project uses."""
    root = _project(tmp_path, NUMERIC)
    data = load_song_table(root)
    assert len(data.entries) == 4
    assert all(e.music_player == 0 for e in data.entries)
    assert data.unresolved_fields == []


# ─────────────────────────────────────────────────────────────────────────────
# A name the project does not define is reported, never swallowed
# ─────────────────────────────────────────────────────────────────────────────

def test_an_unknown_name_still_keeps_the_row(tmp_path):
    """Keeping it is what protects every later song's index."""
    root = _project(tmp_path, SYMBOLIC.replace('MUSIC_PLAYER_BGM',
                                               'MUSIC_PLAYER_NONSENSE'))
    data = load_song_table(root)
    assert [e.label for e in data.entries] == [
        'mus_first', 'mus_second', 'mus_third', 'mus_fourth']
    assert data.by_label('mus_fourth').constant == 'MUS_FOURTH'


def test_an_unknown_name_is_reported(tmp_path):
    root = _project(tmp_path, SYMBOLIC.replace('MUSIC_PLAYER_BGM',
                                               'MUSIC_PLAYER_NONSENSE'))
    assert load_song_table(root).unresolved_fields == [
        ('mus_second', 'MUSIC_PLAYER_NONSENSE')]


def test_a_resolved_name_is_not_reported(tmp_path):
    """Reporting a name the project DOES define would be noise."""
    root = _project(tmp_path, SYMBOLIC)
    assert load_song_table(root).unresolved_fields == []


def test_the_third_field_is_read_the_same_way(tmp_path):
    rows = SYMBOLIC.replace('\tsong mus_third, 0, 0\n',
                            '\tsong mus_third, 0, MUSIC_PLAYER_SE1\n')
    root = _project(tmp_path, rows)
    assert load_song_table(root).by_label('mus_third').unknown == 1


# ─────────────────────────────────────────────────────────────────────────────
# Reading is not assembling
# ─────────────────────────────────────────────────────────────────────────────

def test_resolving_a_name_does_not_mean_it_will_build(tmp_path):
    """The distinction that matters most here.

    PorySuite can read `MUSIC_PLAYER_BGM` by consulting the project's player
    table. The ASSEMBLER cannot: `.2byte MUSIC_PLAYER_BGM` needs a `#define`,
    and FireRed has none. A row can be perfectly understood and still break
    the build, so understanding it must never be mistaken for fixing it.
    """
    root = _project(tmp_path, SYMBOLIC)
    data = load_song_table(root)
    assert data.by_label('mus_second').music_player == 0   # read fine
    raw = open(os.path.join(root, 'sound', 'song_table.inc'),
               encoding='utf-8').read()
    assert 'MUSIC_PLAYER_BGM' in raw, (
        'the file still carries the undefined name; reading it changed '
        'nothing on disk, and nothing here should pretend otherwise')


def test_parsing_never_writes_to_the_project(tmp_path):
    root = _project(tmp_path, SYMBOLIC)
    table = os.path.join(root, 'sound', 'song_table.inc')
    before = open(table, 'rb').read()
    load_song_table(root)
    assert open(table, 'rb').read() == before


# ─────────────────────────────────────────────────────────────────────────────
# The low-level parser
# ─────────────────────────────────────────────────────────────────────────────

def test_the_parser_reports_through_its_out_list(tmp_path):
    root = _project(tmp_path, SYMBOLIC)
    out: list = []
    rows = _parse_song_table(os.path.join(root, 'sound', 'song_table.inc'),
                            {'MUSIC_PLAYER_BGM': 0}, out)
    assert len(rows) == 4
    assert out == []


def test_the_parser_without_a_player_map_keeps_rows(tmp_path):
    """Called with nothing to resolve against, it must still not lose a row."""
    root = _project(tmp_path, SYMBOLIC)
    out: list = []
    rows = _parse_song_table(os.path.join(root, 'sound', 'song_table.inc'),
                            {}, out)
    assert [r[0] for r in rows] == [
        'mus_first', 'mus_second', 'mus_third', 'mus_fourth']
    assert out == [('mus_second', 'MUSIC_PLAYER_BGM')]
