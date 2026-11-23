"""A project's own script commands show up as real commands, with dropdowns.

THE GAP (reported 2026-11-22). A project can add a script command of its own
(a macro in asm/macros/event.inc). EVENTide could assemble it but only showed
it as one box of comma-separated text, so the only visual way to use a
project routine that takes, say, an item, was "Set Variable VAR_0x8004 to a
typed constant, then Call Script" -- not something a non-coder can do.

A macro can now describe itself in comment lines above its `.macro` line
(name, Add Command heading, help, and which constant families each parameter
takes). EVENTide then names it, explains it, offers it in Add Command, and
edits each parameter with its own searchable dropdown. Nothing in the editor
names a single game's command; everything comes from the project's file.
"""
import os

import pytest

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

pytest.importorskip('PyQt6.QtWidgets')

from PyQt6.QtWidgets import QApplication  # noqa: E402

import eventide.ui.event_editor_tab as eet  # noqa: E402
from eventide.backend import macro_params as mp  # noqa: E402
from eventide.backend.constants_manager import ConstantsManager  # noqa: E402
from eventide.ui.widgets import ConstantPicker  # noqa: E402


EVENT_INC = """\
\t.macro giveitem item:req, amount=1
\t.endm

\t@ An ordinary comment that is not for the editor.
\t@ eventide name: Hand Over
\t@ eventide section: Items & Rewards
\t@ eventide help: The player holds a thing up.
\t@ eventide param item: Thing | ITEM_ PRIZE_
\t.macro handover item:req, count=1
\t.endm

\t@ eventide name: Hand Over: Finish
\t@ eventide section: Brand New Heading
\t.macro handoverend
\t.endm

\t@ eventide name: Not Adjacent

\t.macro detached value:req
\t.endm

\t.macro undescribed value:req
\t.endm
"""

ITEMS_H = """#define ITEM_NONE 0
#define ITEM_POTION 1
#define ITEM_NUGGET 2
"""

PRIZE_H = """#define PRIZE_BASE 0x1000
#define PRIZE_COUPON (PRIZE_BASE + 1)
#define PRIZE_TICKET (PRIZE_BASE + 2)
#define PRIZE_COUNT 2
"""


@pytest.fixture(scope='module')
def qapp():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def project(qapp, tmp_path, monkeypatch):
    (tmp_path / 'asm' / 'macros').mkdir(parents=True)
    (tmp_path / 'asm' / 'macros' / 'event.inc').write_text(
        EVENT_INC, encoding='utf-8')
    inc = tmp_path / 'include' / 'constants'
    inc.mkdir(parents=True)
    (inc / 'items.h').write_text(ITEMS_H, encoding='utf-8')
    (inc / 'prize.h').write_text(PRIZE_H, encoding='utf-8')
    mp.clear_cache()
    ConstantsManager.load(str(tmp_path))
    monkeypatch.setattr(eet, '_PROJECT_ROOT', str(tmp_path))
    yield tmp_path
    mp.clear_cache()


def test_only_described_adjacent_macros_become_commands(project):
    cmds = mp.get_project_commands(str(project))
    assert list(cmds) == ['handover', 'handoverend']
    pc = cmds['handover']
    assert pc.label == 'Hand Over'
    assert pc.section == 'Items & Rewards'
    assert pc.help == 'The player holds a thing up.'
    (p_item, l_item, f_item), (p_count, l_count, f_count) = pc.params
    assert (p_item.name, p_item.required, l_item, f_item) == (
        'item', True, 'Thing', ('ITEM_', 'PRIZE_'))
    assert (p_count.name, p_count.required, l_count, f_count) == (
        'count', False, 'Count', ())


def test_an_invented_family_is_read_from_the_headers(project):
    assert ConstantsManager.with_prefix('PRIZE_') == ['PRIZE_COUPON', 'PRIZE_TICKET']
    assert ConstantsManager.with_prefix('ITEM_') == ConstantsManager.ITEMS


def test_the_list_line_reads_as_a_command(project):
    assert eet._stringize(('handover', 'ITEM_POTION')) == '@>Hand Over: Potion'
    assert eet._stringize(('handover', 'PRIZE_COUPON, 3')) == \
        '@>Hand Over: Coupon, 3'
    assert eet._stringize(('handoverend',)) == '@>Hand Over: Finish'
    assert eet._command_help('handover') == 'The player holds a thing up.'


def test_the_dialog_has_a_dropdown_of_both_families(project):
    w = eet._widget_for_tuple(('handover', 'PRIZE_TICKET'))
    assert isinstance(w, eet._ProjectCommandWidget)
    picker = w.findChildren(ConstantPicker)[0]
    raws = [picker._display_to_raw[picker.itemText(i)]
            for i in range(picker.count())]
    assert set(raws) == {'ITEM_NONE', 'ITEM_POTION', 'ITEM_NUGGET',
                         'PRIZE_COUPON', 'PRIZE_TICKET'}
    assert 'Ticket  (PRIZE_TICKET)' in picker._display_to_raw
    assert w.to_tuple() == ('handover', 'PRIZE_TICKET')
    picker.set_constant('ITEM_NUGGET')
    assert w.to_tuple() == ('handover', 'ITEM_NUGGET')
    assert picker.invalid_reason() is None


def test_a_required_item_must_be_picked(project):
    w = eet._widget_for_tuple(('handover',))
    assert w.validation_error()
    w.findChildren(ConstantPicker)[0].set_constant('ITEM_POTION')
    assert w.validation_error() is None


def test_a_no_parameter_command_needs_nothing(project):
    w = eet._widget_for_tuple(('handoverend',))
    assert w._header_only
    assert w.to_tuple() == ('handoverend',)


def test_built_in_and_undescribed_commands_are_unchanged(project):
    assert isinstance(eet._widget_for_tuple(('giveitem', 'ITEM_POTION')),
                      eet._GiveItemWidget)
    assert isinstance(eet._widget_for_tuple(('undescribed', '1')),
                      eet._GenericWidget)


def test_add_command_offers_them_under_their_headings(project):
    pages = eet._pages_with_project_commands()
    sections = {name: cmds for _, secs in pages for name, cmds in secs}
    assert ('Hand Over', 'handover') in sections['Items & Rewards']
    assert sections['Brand New Heading'] == [('Hand Over: Finish',
                                              'handoverend')]
    # The new heading goes above Freeze & Release, which stays last.
    page1 = [name for name, _ in pages[0][1]]
    assert page1[-1].startswith('Freeze')
    assert page1[-2] == 'Brand New Heading'
    # The module's own lists are never touched.
    assert all(raw != 'handover' for _, cmds in eet._PAGE_1_COMMANDS
               for _, raw in cmds)


def test_typo_in_a_multi_family_box_names_both(project):
    picker = ConstantPicker(['ITEM_POTION', 'PRIZE_COUPON'],
                            prefix=('ITEM_', 'PRIZE_'))
    picker.setEditText('POTION')
    assert 'ITEM_ or PRIZE_' in picker.invalid_reason()
