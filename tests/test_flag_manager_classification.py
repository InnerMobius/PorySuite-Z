"""Flags are classified by what the project actually does with them.

WHY THIS EXISTS (2026-10-25). Two defects sat on top of each other.

1. The Event Editor's "find an unused flag" picker matched on the NAME
   ``FLAG_UNUSED_*``. Vanilla FireRed labels only nine slots that way, and all
   nine are the tail of the hidden-item block. Hundreds of ordinary free slots
   named ``FLAG_0x###`` were invisible to it, so the editor reported the
   project was nearly out of flags when it had hundreds spare.

2. The Flag Manager split slots into "your scripts" and "vanilla engine" purely
   on whether ``src/`` mentioned them. Every vanilla hide-the-NPC flag is
   referenced from map data only, so hundreds of vanilla flags were presented
   as the user's own work.

The fix is to resolve each slot's real NUMBER through the header's own
arithmetic, place it in the engine region that owns it, and look at every file
that references it. What is guarded here:

  * values resolve across headers (``MAX_TRAINERS_COUNT`` lives elsewhere);
  * the hidden-item block ends at the last named hidden item, not at the start
    of the trainer block -- FireRed puts ordinary story flags in the gap;
  * ``free`` means referenced NOWHERE, so claiming one cannot change anything;
  * a flag used by engine C code, a badge, a temporary slot or a trainer slot
    is never offered as a reclaim candidate;
  * a flag used on one map is a candidate; the same flag used on two is not;
  * renaming takes a restorable snapshot first and leaves other files alone.
"""
import json
import os

import pytest

from eventide.backend import var_flag_manager as vf


FLAGS_H = """#ifndef GUARD_CONSTANTS_FLAGS_H
#define GUARD_CONSTANTS_FLAGS_H

#include "constants/opponents.h"

#define TEMP_FLAGS_START 0x0
#define FLAG_TEMP_1      (TEMP_FLAGS_START + 0x01)
#define FLAG_TEMP_2      (TEMP_FLAGS_START + 0x02)
#define TEMP_FLAGS_END   0x1F

#define FLAG_0x020               0x020
#define FLAG_0x021               0x021
#define FLAG_HIDE_SHOPKEEPER     0x022
#define FLAG_HIDE_POTION_BALL    0x023
#define FLAG_GOT_TOWN_MAP        0x024
#define FLAG_HIDE_GUARD          0x025
#define FLAG_WORLD_MAP_PEWTER    0x026
#define FLAG_DEFEATED_BROCK      0x027

#define FLAG_HIDDEN_ITEMS_START 1000
#define FLAG_HIDDEN_ITEM_CAVE_POTION  (FLAG_HIDDEN_ITEMS_START + 0)
#define FLAG_HIDDEN_ITEM_CAVE_ETHER   (FLAG_HIDDEN_ITEMS_START + 1)

// Unused hidden item flags
#define FLAG_UNUSED_0x3EA        0x3EA
#define FLAG_UNUSED_0x3EB        0x3EB

// Boss clear flags -- in the gap, but ordinary story flags
#define FLAG_CLEARED_FIRST_CAVE  0x3F0

#define TRAINER_FLAGS_START      0x500
#define TRAINER_FLAGS_END        (TRAINER_FLAGS_START + MAX_TRAINERS_COUNT - 1)
#define FLAG_TRAINER_FLAG_START  TRAINER_FLAGS_START

#define SYS_FLAGS (TRAINER_FLAGS_END + 1)
#define FLAG_SYS_POKEDEX_GET     (SYS_FLAGS + 0x0)
#define FLAG_BADGE01_GET         (SYS_FLAGS + 0x20)
#define FLAG_0x821               (SYS_FLAGS + 0x21)

#define FLAGS_COUNT (FLAG_0x821 + 1)

#endif
"""

OPPONENTS_H = "#define MAX_TRAINERS_COUNT 768\n"

VARS_H = """#ifndef GUARD_CONSTANTS_VARS_H
#define GUARD_CONSTANTS_VARS_H
#define VARS_START 0x4000
#define TEMP_VARS_START 0x4000
#define VAR_TEMP_0 (TEMP_VARS_START + 0x0)
#define TEMP_VARS_END VAR_TEMP_0
#define VAR_QUEST_STEP 0x4011
#define VAR_0x4012 0x4012
#define VARS_END 0x40FF
#define VARS_COUNT (VARS_END - VARS_START + 1)
#define SPECIAL_VARS_START 0x8000
#define VAR_0x8004 0x8000
#endif
"""


@pytest.fixture
def project(tmp_path):
    """A miniature FireRed decomp with one flag of each interesting shape."""
    (tmp_path / 'include' / 'constants').mkdir(parents=True)
    (tmp_path / 'include' / 'constants' / 'flags.h').write_text(
        FLAGS_H, encoding='utf-8')
    (tmp_path / 'include' / 'constants' / 'opponents.h').write_text(
        OPPONENTS_H, encoding='utf-8')
    (tmp_path / 'include' / 'constants' / 'vars.h').write_text(
        VARS_H, encoding='utf-8')

    src = tmp_path / 'src'
    src.mkdir()
    # Engine code reads these two.
    (src / 'region_map.c').write_text(
        'void f(void) { FlagSet(FLAG_WORLD_MAP_PEWTER); }\n', encoding='utf-8')
    (src / 'badges.c').write_text(
        'void g(void) { FlagGet(FLAG_BADGE01_GET); '
        'FlagGet(FLAG_SYS_POKEDEX_GET); FlagClear(FLAG_TEMP_1); }\n',
        encoding='utf-8')

    maps = tmp_path / 'data' / 'maps'
    # One map only -> reclaim candidates.
    (maps / 'CaveOne').mkdir(parents=True)
    (maps / 'CaveOne' / 'scripts.inc').write_text(
        '\tsetflag FLAG_HIDE_SHOPKEEPER\n'
        '\tsetflag FLAG_HIDE_POTION_BALL\n'
        '\tsetflag FLAG_GOT_TOWN_MAP\n'
        '\tsetflag FLAG_CLEARED_FIRST_CAVE\n'
        '\tsetflag FLAG_DEFEATED_BROCK\n'
        '\tsetflag FLAG_HIDDEN_ITEM_CAVE_POTION\n'
        '\tsetvar VAR_QUEST_STEP, 1\n', encoding='utf-8')
    (maps / 'CaveOne' / 'map.json').write_text(json.dumps({
        'object_events': [], 'warp_events': [], 'coord_events': [],
        'bg_events': []}), encoding='utf-8')
    # Two maps -> shared.
    (maps / 'CaveTwo').mkdir(parents=True)
    (maps / 'CaveTwo' / 'scripts.inc').write_text(
        '\tgoto_if_set FLAG_HIDE_GUARD, Somewhere\n', encoding='utf-8')
    (maps / 'CaveThree').mkdir(parents=True)
    (maps / 'CaveThree' / 'scripts.inc').write_text(
        '\tclearflag FLAG_HIDE_GUARD\n', encoding='utf-8')
    return tmp_path


def by_name(entries):
    return {e['name']: e for e in entries}


# ── values and regions ──────────────────────────────────────────────────────

def test_values_resolve_across_headers(project):
    vals = vf.resolve_values(project, vf.FLAGS_HEADER)
    assert vals['FLAG_0x020'] == 0x20
    assert vals['FLAG_HIDDEN_ITEM_CAVE_ETHER'] == 1001
    # TRAINER_FLAGS_END needs MAX_TRAINERS_COUNT out of opponents.h.
    assert vals['TRAINER_FLAGS_END'] == 0x500 + 768 - 1
    assert vals['SYS_FLAGS'] == 0x500 + 768
    assert vals['FLAG_BADGE01_GET'] == vals['SYS_FLAGS'] + 0x20


def test_hidden_item_block_ends_at_the_last_named_hidden_item(project):
    """FireRed leaves spare slots and then ordinary story flags in the gap.

    Treating everything up to the trainer block as hidden-item territory
    locked away dozens of perfectly reclaimable flags.
    """
    regions = vf.flag_regions(vf.resolve_values(project, vf.FLAGS_HEADER))
    assert regions['hidden_start'] == 1000
    assert regions['hidden_end'] == 1001          # not 0x4FF
    assert regions['trainer_start'] == 0x500


def test_every_flag_gets_a_slot_number(project):
    """An unplaceable slot is protected rather than guessed at, so a header
    that stops resolving would show up here instead of silently downgrading."""
    entries = vf.scan(project, 'flag')
    assert entries
    assert all(e['slot'] is not None for e in entries)


# ── classification ──────────────────────────────────────────────────────────

def test_unreferenced_slots_are_free(project):
    e = by_name(vf.scan(project, 'flag'))
    assert e['FLAG_0x020']['protection'] == 'free'
    assert e['FLAG_0x021']['category'] == 'free'
    assert e['FLAG_0x020']['refs_total'] == 0


def test_a_flag_used_on_one_map_is_a_reclaim_candidate(project):
    e = by_name(vf.scan(project, 'flag'))
    shop = e['FLAG_HIDE_SHOPKEEPER']
    assert shop['protection'] == 'review'
    assert shop['category'] == 'event'
    assert shop['maps'] == ['CaveOne']


def test_item_pickups_are_told_apart_from_plain_events(project):
    e = by_name(vf.scan(project, 'flag'))
    assert e['FLAG_HIDE_POTION_BALL']['category'] == 'item'
    assert e['FLAG_GOT_TOWN_MAP']['category'] == 'item'
    assert e['FLAG_HIDE_POTION_BALL']['protection'] == 'review'


def test_a_flag_used_on_two_maps_is_not_a_candidate(project):
    e = by_name(vf.scan(project, 'flag'))
    guard = e['FLAG_HIDE_GUARD']
    assert guard['category'] == 'shared'
    assert guard['protection'] == 'investigate'
    assert guard['maps'] == ['CaveThree', 'CaveTwo']


def test_engine_referenced_flags_are_protected(project):
    e = by_name(vf.scan(project, 'flag'))
    world = e['FLAG_WORLD_MAP_PEWTER']
    assert world['category'] == 'engine'
    assert world['protection'] == 'protected'
    assert world['engine'] == ['src/region_map.c']


def test_badges_progression_and_system_are_protected(project):
    e = by_name(vf.scan(project, 'flag'))
    for name in ('FLAG_BADGE01_GET', 'FLAG_SYS_POKEDEX_GET', 'FLAG_0x821'):
        assert e[name]['protection'] == 'protected', name
        assert e[name]['category'] == 'system', name


def test_temporary_flags_are_protected_and_named_as_such(project):
    e = by_name(vf.scan(project, 'flag'))
    assert e['FLAG_TEMP_1']['category'] == 'temporary'
    assert e['FLAG_TEMP_1']['protection'] == 'protected'
    assert e['FLAG_TEMP_2']['category'] == 'temporary'


def test_hidden_item_block_is_held_back_for_investigation(project):
    """Engine code addresses that block by slot number, not by name."""
    e = by_name(vf.scan(project, 'flag'))
    assert e['FLAG_HIDDEN_ITEM_CAVE_POTION']['protection'] == 'investigate'
    assert e['FLAG_HIDDEN_ITEM_CAVE_ETHER']['protection'] == 'investigate'


def test_spare_slots_after_the_hidden_item_block_are_usable(project):
    """0x3EA/0x3EB sit past the last named hidden item, so they are free."""
    e = by_name(vf.scan(project, 'flag'))
    assert e['FLAG_UNUSED_0x3EA']['protection'] == 'free'
    assert e['FLAG_CLEARED_FIRST_CAVE']['protection'] == 'review'


def test_trainer_flags_are_preserved_not_offered(project):
    e = by_name(vf.scan(project, 'flag'))
    brock = e['FLAG_DEFEATED_BROCK']
    assert brock['category'] == 'trainer'
    assert brock['protection'] == 'preserve'


def test_reclaimable_set_never_includes_a_protected_level(project):
    for e in vf.scan(project, 'flag'):
        if e['protection'] in vf.RECLAIMABLE:
            assert e['category'] not in ('engine', 'system', 'temporary',
                                         'trainer'), e['name']


def test_free_means_referenced_nowhere(project):
    entries = vf.scan(project, 'flag')
    for e in entries:
        if e['protection'] == 'free':
            assert e['refs_total'] == 0, e['name']
            assert not e['maps'] and not e['engine'] and not e['scripts']


def test_vars_are_classified_too(project):
    e = by_name(vf.scan(project, 'var'))
    assert e['VAR_0x4012']['protection'] == 'free'
    assert e['VAR_QUEST_STEP']['protection'] == 'review'
    assert e['VAR_TEMP_0']['category'] == 'temporary'
    assert e['VAR_0x8004']['protection'] == 'protected'


# ── picking a free slot ─────────────────────────────────────────────────────

def test_free_names_are_in_slot_order_and_exclude_used_slots(project):
    entries = vf.scan(project, 'flag')
    free = vf.free_names(entries)
    assert free[0] == 'FLAG_0x020'
    assert 'FLAG_HIDE_SHOPKEEPER' not in free
    assert 'FLAG_WORLD_MAP_PEWTER' not in free
    assert 'FLAG_TEMP_1' not in free
    # the widening: plain FLAG_0x### slots are offered, not just FLAG_UNUSED_*
    assert 'FLAG_0x021' in free


def test_first_free_prefers_a_slot_that_was_never_given_a_purpose(project):
    entries = vf.scan(project, 'flag')
    assert vf.first_free(entries)['name'] == 'FLAG_0x020'


def test_the_spare_tail_is_free_but_kept_out_of_the_automatic_pool(project):
    """0x3EA/0x3EB are unreferenced and usable, but they sit in the window
    FLAG_HIDDEN_ITEMS_START + index could grow into. A chest template must not
    quietly take one; the user may still reclaim one by hand."""
    entries = vf.scan(project, 'flag')
    spare = by_name(entries)['FLAG_UNUSED_0x3EA']
    assert spare['protection'] == 'free'
    assert spare['region'] == 'hidden items (spare)'
    assert 'FLAG_UNUSED_0x3EA' in vf.free_names(entries)
    assert 'FLAG_UNUSED_0x3EA' not in vf.auto_claim_names(entries)


def test_the_automatic_pool_never_includes_an_engine_indexed_region(project):
    entries = vf.scan(project, 'flag')
    regions = {e['name']: e['region'] for e in entries}
    for name in vf.auto_claim_names(entries):
        assert regions[name] not in vf.INDEXED_REGIONS, name


def test_first_free_skips_the_indexed_regions_too(project):
    """"New Flag…" claims from the same safe ground the templates do."""
    entries = [e for e in vf.scan(project, 'flag')
               if e['region'] != 'general' or e['protection'] != 'free'
               or e['name'] == 'FLAG_0x021']
    # Only one general-region free slot left, plus the spare-tail ones.
    assert vf.first_free(entries)['name'] == 'FLAG_0x021'


def test_capacity_reports_each_region(project):
    cap = vf.capacity(vf.scan(project, 'flag'))
    assert cap['temporary']['free'] == 0
    assert cap['general']['free'] >= 2
    assert cap['hidden items']['free'] == 0


# ── renaming / reclaiming ───────────────────────────────────────────────────

def test_rename_updates_every_reference_and_the_define(project):
    n, files, _backup = vf.rename_symbol(
        project, 'FLAG_HIDE_SHOPKEEPER', 'FLAG_HYRULE_SHOP_OPEN')
    assert n == 2
    hdr = (project / vf.FLAGS_HEADER).read_text(encoding='utf-8')
    assert '#define FLAG_HYRULE_SHOP_OPEN     0x022' in hdr
    assert 'FLAG_HIDE_SHOPKEEPER' not in hdr
    script = (project / 'data/maps/CaveOne/scripts.inc').read_text(
        encoding='utf-8')
    assert 'setflag FLAG_HYRULE_SHOP_OPEN' in script


def test_rename_leaves_unrelated_files_byte_identical(project):
    other = project / 'data/maps/CaveTwo/scripts.inc'
    before = other.read_bytes()
    vf.rename_symbol(project, 'FLAG_HIDE_SHOPKEEPER', 'FLAG_SHOP_OPEN')
    assert other.read_bytes() == before


def test_rename_does_not_touch_a_longer_name_that_starts_the_same(project):
    """FLAG_HIDE_GUARD must survive a rename of FLAG_HIDE_G... nothing."""
    vf.rename_symbol(project, 'FLAG_HIDE_SHOPKEEPER', 'FLAG_HIDE_SHOP')
    hdr = (project / vf.FLAGS_HEADER).read_text(encoding='utf-8')
    assert '#define FLAG_HIDE_SHOP ' in hdr.replace('    ', ' ') or \
           'FLAG_HIDE_SHOP ' in hdr
    assert 'FLAG_HIDE_SHOPKEEPER' not in hdr
    assert 'FLAG_HIDE_GUARD' in hdr


def test_rename_backs_up_every_file_it_is_about_to_change(project, tmp_path):
    backup_root = tmp_path / 'backups'
    original = (project / 'data/maps/CaveOne/scripts.inc').read_text(
        encoding='utf-8')

    n, files, backup = vf.rename_symbol(
        project, 'FLAG_HIDE_SHOPKEEPER', 'FLAG_SHOP_OPEN',
        backup_root=str(backup_root))

    assert backup and os.path.isdir(backup)
    saved = os.path.join(backup, 'data', 'maps', 'CaveOne', 'scripts.inc')
    assert os.path.isfile(saved)
    with open(saved, encoding='utf-8') as f:
        assert f.read() == original          # the pre-rename content
    assert os.path.isfile(
        os.path.join(backup, 'include', 'constants', 'flags.h'))


def test_rename_without_a_backup_root_writes_no_backup(project):
    n, files, backup = vf.rename_symbol(
        project, 'FLAG_HIDE_SHOPKEEPER', 'FLAG_SHOP_OPEN')
    assert backup == ''


def test_a_reclaimed_slot_keeps_its_number(project):
    """Renaming is a label change. The bit a save file already holds is the
    same bit afterwards, which is exactly why reclaiming needs a warning."""
    before = vf.resolve_values(project, vf.FLAGS_HEADER)['FLAG_HIDE_SHOPKEEPER']
    vf.rename_symbol(project, 'FLAG_HIDE_SHOPKEEPER', 'FLAG_SHOP_OPEN')
    after = vf.resolve_values(project, vf.FLAGS_HEADER)['FLAG_SHOP_OPEN']
    assert before == after


# ── caching ─────────────────────────────────────────────────────────────────

def test_cached_scan_is_reused_until_invalidated(project):
    vf.invalidate_cache()
    a = vf.cached_scan(project, 'flag')
    assert vf.cached_scan(project, 'flag') is a
    vf.invalidate_cache(project)
    assert vf.cached_scan(project, 'flag') is not a


def test_invalidate_cache_with_no_argument_clears_every_project(project):
    vf.cached_scan(project, 'flag')
    vf.invalidate_cache()
    assert not vf._cache
