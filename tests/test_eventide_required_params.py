"""A command the editor saves must be one the project can build.

A script command is an assembler macro, and a macro declares which of its
parameters are required. Leaving one out does not fail in the editor and does
not fail in game — it fails the BUILD, later, as a message naming a macro and a
line number with nothing to connect it back to the dialog it came from:

    data/maps/Faron/scripts.inc:69: Error: Missing value for required
        parameter `song' of macro `savebgm'

An audit found 84 of the 127 commands the editor offers could be saved with a
required parameter empty. The check reads the parameter lists out of the
PROJECT'S OWN macro file, so a project that adds commands or changes which
parameters are required is checked against its own rules.

These tests use invented macro files in a temp directory — the rules under test
are about reading whatever a project declares, not about any particular game.
"""
from __future__ import annotations

import os

import pytest

from eventide.backend.macro_params import (
    clear_cache, describe_missing, flatten_args, get_macros, missing_required,
)


@pytest.fixture
def project(tmp_path):
    """A project whose macro file declares a mix of required and optional."""
    macros = tmp_path / 'asm' / 'macros'
    macros.mkdir(parents=True)
    (macros / 'event.inc').write_text(
        "\t.macro savebgm song:req\n"
        "\t.byte 0x34\n"
        "\t.endm\n"
        "\n"
        "\t.macro warp map:req, x=0, y=0\n"
        "\t.endm\n"
        "\n"
        "\t.macro playbgm song:req, save_song:req\n"
        "\t.endm\n"
        "\n"
        "\t.macro fadedefaultbgm\n"
        "\t.endm\n"
        "\n"
        "\t.macro lightsabre colour:req @ a command this project invented\n"
        "\t.endm\n"
        "\n"
        "\t.macro gatherthings first:req, rest:vararg\n"
        "\t.endm\n",
        encoding='utf-8')
    clear_cache()
    yield tmp_path
    clear_cache()


# ── reading the project's own macros ──────────────────────────────────────

def test_the_projects_own_commands_are_known(project):
    """A command this project invented is checked like any other."""
    assert 'lightsabre' in get_macros(project)


def test_required_and_optional_are_told_apart(project):
    params = {p.name: p.required for p in get_macros(project)['warp']}
    assert params == {'map': True, 'x': False, 'y': False}


def test_a_trailing_comment_is_not_read_as_a_parameter(project):
    names = [p.name for p in get_macros(project)['lightsabre']]
    assert names == ['colour']


# ── catching what would break the build ───────────────────────────────────

def test_a_required_parameter_left_empty_is_caught(project):
    assert missing_required(project, 'savebgm', ('',)) == ['song']


def test_a_required_parameter_missing_entirely_is_caught(project):
    """`savebgm` with no argument at all — the line that started this."""
    assert missing_required(project, 'savebgm', ()) == ['song']


def test_a_filled_command_passes(project):
    assert missing_required(project, 'savebgm', ('MUS_OAK',)) == []


def test_only_the_required_slot_matters(project):
    """`warp` needs a map; x and y have defaults and may be left alone."""
    assert missing_required(project, 'warp', ('MAP_FARON', '', '')) == []
    assert missing_required(project, 'warp', ('', '5', '5')) == ['map']


def test_every_missing_name_is_reported(project):
    assert missing_required(project, 'playbgm', ('', '')) == ['song', 'save_song']


def test_the_right_slot_is_blamed(project):
    """Position matters: a filled first slot must not excuse an empty second."""
    assert missing_required(project, 'playbgm', ('MUS_OAK', '')) == ['save_song']


def test_arguments_joined_into_one_string_are_still_positional(project):
    """Widgets hand back either separate values or one comma-joined string."""
    assert flatten_args(('MUS_OAK, 1',)) == ['MUS_OAK', '1']
    assert missing_required(project, 'playbgm', ('MUS_OAK, 1',)) == []
    assert missing_required(project, 'playbgm', (', 1',)) == ['song']


# ── never block somebody's work on a guess ────────────────────────────────

def test_a_command_with_no_parameters_is_fine(project):
    assert missing_required(project, 'fadedefaultbgm', ()) == []


def test_an_unknown_command_is_never_blocked(project):
    """A command this project does not declare cannot be judged, so it passes."""
    assert missing_required(project, 'notacommandhere', ('',)) == []


def test_a_vararg_macro_is_never_blocked(project):
    """A vararg tail swallows the rest, so positions stop meaning anything."""
    assert missing_required(project, 'gatherthings', ('',)) == []


def test_a_project_with_no_macro_file_is_never_blocked(tmp_path):
    clear_cache()
    assert missing_required(tmp_path, 'savebgm', ('',)) == []
    clear_cache()


def test_no_root_at_all_is_never_blocked():
    clear_cache()
    assert missing_required('', 'savebgm', ('',)) == []
    clear_cache()


# ── what the user is told ─────────────────────────────────────────────────

def test_the_message_names_the_parameter(project):
    msg = describe_missing('savebgm', ['song'])
    assert '"song"' in msg and 'savebgm' in msg


def test_the_message_reads_as_a_sentence_for_several(project):
    msg = describe_missing('playbgm', ['song', 'save_song'])
    assert '"song" and "save song"' in msg
    assert 'need a value' in msg


def test_the_message_says_why_it_matters(project):
    assert 'building' in describe_missing('savebgm', ['song'])


# ── against the real project, if it is on disk ────────────────────────────

_REAL = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                     'pokefirered')


@pytest.mark.skipif(not os.path.isdir(os.path.join(_REAL, 'asm', 'macros')),
                    reason='game source not present')
def test_real_macros_parse_and_find_required_parameters():
    clear_cache()
    macros = get_macros(_REAL)
    assert len(macros) > 100
    assert any(p.required for p in macros.get('savebgm', []))
    # A command with genuinely no parameters must not be reported as needing any.
    assert missing_required(_REAL, 'fadedefaultbgm', ()) == []
    clear_cache()
