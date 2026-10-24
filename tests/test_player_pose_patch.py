"""Guards for the player-avatar-pose patcher.

Registering a pose is eight edits across eight files and every one of them has
to agree, so most of these tests are about the agreements rather than about
any single edit.

The three that would cost a day each if they regressed:

* **Append the state, never insert it.** The enum's values are indices into
  ``sPlayerAvatarGfxIds``. Inserting in the middle renumbers every state after
  it, so anything holding a saved state id comes back as a different pose —
  and save data outlives a rebuild, so it would look like a corrupted save
  rather than a bad patch.

* **A one-frame sheet cannot use the normal anim table.** Every facing and
  walk state would index frames that do not exist and the sprite renders as
  garbage. The generated table maps every state to one anim, the way the
  engine's own single-entry pose tables do.

* **`.size` is copied, not computed.** Across this engine's own 16x32 player
  poses it is 512 for some and 256 for others with no rule derivable from the
  geometry, so it is taken from the player's normal sprite — which can only
  over-allocate, never truncate.
"""

import os
import re
import sys
import zlib

import pytest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _p in (_ROOT, os.path.join(_ROOT, 'core'), os.path.join(_ROOT, 'ui')):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from core.player_pose_patch import (  # noqa: E402
    PlayerPosePatchError,
    apply_pose_to_call,
    describe,
    ensure_player_pose,
    is_pose_installed,
    project_supports_poses,
)

_PICS = os.path.join('graphics', 'object_events', 'pics', 'people')


def _png(path, width, height):
    """A minimal valid PNG — only the header is read."""
    def chunk(tag, data):
        return (len(data).to_bytes(4, 'big') + tag + data
                + zlib.crc32(tag + data).to_bytes(4, 'big'))
    ihdr = (width.to_bytes(4, 'big') + height.to_bytes(4, 'big')
            + bytes([8, 3, 0, 0, 0]))
    with open(path, 'wb') as fh:
        fh.write(b'\x89PNG\r\n\x1a\n' + chunk(b'IHDR', ihdr)
                 + chunk(b'IEND', b''))


def _project(tmp_path, red_h=32, green_h=32, with_avatar_table=True):
    root = str(tmp_path)
    for rel in ('include/constants', 'include', 'src/data/object_events',
                'src', _PICS.replace('\\', '/')):
        os.makedirs(os.path.join(root, rel), exist_ok=True)

    _png(os.path.join(root, _PICS, 'red_pose.png'), 16, red_h)
    _png(os.path.join(root, _PICS, 'green_pose.png'), 16, green_h)

    def w(rel, text):
        with open(os.path.join(root, rel), 'w', encoding='utf-8') as fh:
            fh.write(text)

    w('include/constants/event_objects.h',
      '#define OBJ_EVENT_GFX_RED_NORMAL 0\n'
      '#define OBJ_EVENT_GFX_GREEN_NORMAL 7\n\n'
      '#define NUM_OBJ_EVENT_GFX     188\n')
    w('include/global.fieldmap.h',
      'enum {\n'
      '    PLAYER_AVATAR_GFX_NORMAL,\n'
      '    PLAYER_AVATAR_GFX_BIKE,\n'
      '    PLAYER_AVATAR_GFX_BOMB,\n'
      '};\n')
    table = (
        'static const u8 sPlayerAvatarGfxIds[][GENDER_COUNT] = {\n'
        '    [PLAYER_AVATAR_GFX_NORMAL] = {OBJ_EVENT_GFX_RED_NORMAL, OBJ_EVENT_GFX_GREEN_NORMAL},\n'
        '};\n') if with_avatar_table else 'u8 nothing;\n'
    w('src/field_player_avatar.c', table)
    w('src/data/object_events/object_event_graphics.h',
      'const u16 gObjectEventPic_RedNormal[] = INCBIN_U16("x.4bpp");\n')
    w('src/data/object_events/object_event_pic_tables.h',
      'static const struct SpriteFrameImage sPicTable_RedNormal[] = {};\n')
    w('src/data/object_events/object_event_anims.h',
      'static const union AnimCmd *const sAnimTable_Standard[] = {};\n')
    w('src/data/object_events/object_event_graphics_info_pointers.h',
      'const struct ObjectEventGraphicsInfo gObjectEventGraphicsInfo_RedNormal;\n\n'
      'const struct ObjectEventGraphicsInfo *const gObjectEventGraphicsInfoPointers[] = {\n'
      '    [OBJ_EVENT_GFX_RED_NORMAL] = &gObjectEventGraphicsInfo_RedNormal,\n'
      '};\n')
    w('src/data/object_events/object_event_graphics_info.h',
      'const struct ObjectEventGraphicsInfo gObjectEventGraphicsInfo_RedNormal = {\n'
      '    .size = 512,\n    .width = 16,\n    .height = 32,\n};\n')
    w('src/cutscene.c',
      'static void ApplyPose(void)\n{\n'
      '    ObjectEventSetGraphicsId(p, GetPlayerAvatarGraphicsIdByStateId(PLAYER_AVATAR_GFX_BOMB));\n'
      '    ObjectEventTurn(p, DIR_SOUTH);\n}\n\n'
      'static void RestorePose(void)\n{\n'
      '    ObjectEventSetGraphicsId(p, GetPlayerAvatarGraphicsIdByStateId(PLAYER_AVATAR_GFX_NORMAL));\n'
      '}\n')
    return root


def _read(root, rel):
    with open(os.path.join(root, rel), encoding='utf-8') as fh:
        return fh.read()


# ─────────────────────────────────────────────────────────────────────────────
# The expensive ones
# ─────────────────────────────────────────────────────────────────────────────

def test_the_new_state_is_appended_not_inserted(tmp_path):
    """Enum values index the gfx table. Inserting renumbers saved states."""
    root = _project(tmp_path)
    before = _read(root, 'include/global.fieldmap.h')
    order_before = re.findall(r'PLAYER_AVATAR_GFX_(\w+),', before)
    ensure_player_pose(root, 'POSE', 'red_pose', 'green_pose')
    order_after = re.findall(r'PLAYER_AVATAR_GFX_(\w+),',
                             _read(root, 'include/global.fieldmap.h'))
    assert order_after[:len(order_before)] == order_before
    assert order_after[-1] == 'POSE'


def test_a_single_frame_sheet_gets_a_hold_pose_anim_table(tmp_path):
    """Not the normal table — it would index frames that do not exist."""
    root = _project(tmp_path, red_h=32, green_h=32)      # one 16x32 frame
    ensure_player_pose(root, 'POSE', 'red_pose', 'green_pose')
    anims = _read(root, 'src/data/object_events/object_event_anims.h')
    table = re.search(r'sAnimTable_PosePose\[\] = \{(.*?)\};', anims, re.S)
    assert table, 'no anim table generated'
    targets = set(re.findall(r'= (\w+),', table.group(1)))
    assert targets == {'sAnim_PosePose'}, 'every state must hold one pose'
    assert 'ANIM_STD_FACE_NORTH' in table.group(1)
    assert 'ANIM_RUN_EAST' in table.group(1)
    pics = _read(root, 'src/data/object_events/object_event_pic_tables.h')
    assert pics.count('overworld_frame(gObjectEventPic_RedPose') == 1


def test_size_is_copied_from_the_normal_player_sprite(tmp_path):
    """Not computed: 16x32 player poses use 512 or 256 with no geometric rule,
    and under-allocating truncates the sprite's tiles."""
    root = _project(tmp_path)
    ensure_player_pose(root, 'POSE', 'red_pose', 'green_pose')
    info = _read(root, 'src/data/object_events/object_event_graphics_info.h')
    block = info[info.index('gObjectEventGraphicsInfo_RedPose'):]
    assert '.size = 512' in block, 'should match RedNormal, not 16*32/2'


# ─────────────────────────────────────────────────────────────────────────────
# All eight edits agree
# ─────────────────────────────────────────────────────────────────────────────

def test_a_multi_frame_sheet_gets_one_frame_per_frame(tmp_path):
    root = _project(tmp_path, red_h=32 * 4, green_h=32 * 4)
    ensure_player_pose(root, 'POSE', 'red_pose', 'green_pose')
    pics = _read(root, 'src/data/object_events/object_event_pic_tables.h')
    assert pics.count('overworld_frame(gObjectEventPic_RedPose') == 4


def test_every_file_gets_its_edit(tmp_path):
    root = _project(tmp_path)
    ensure_player_pose(root, 'POSE', 'red_pose', 'green_pose')
    assert 'OBJ_EVENT_GFX_POSE_RED' in _read(root, 'include/constants/event_objects.h')
    assert 'NUM_OBJ_EVENT_GFX     190' in _read(root, 'include/constants/event_objects.h')
    assert 'gObjectEventPic_RedPose' in _read(root, 'src/data/object_events/object_event_graphics.h')
    assert 'sPicTable_GreenPose' in _read(root, 'src/data/object_events/object_event_pic_tables.h')
    assert 'sAnimTable_PosePose' in _read(root, 'src/data/object_events/object_event_anims.h')
    ptrs = _read(root, 'src/data/object_events/object_event_graphics_info_pointers.h')
    assert 'gObjectEventGraphicsInfo_RedPose;' in ptrs
    assert '[OBJ_EVENT_GFX_POSE_RED]' in ptrs
    assert 'gObjectEventGraphicsInfo_GreenPose = {' in _read(
        root, 'src/data/object_events/object_event_graphics_info.h')
    avatar = _read(root, 'src/field_player_avatar.c')
    assert '[PLAYER_AVATAR_GFX_POSE]' in avatar
    assert 'OBJ_EVENT_GFX_POSE_RED, OBJ_EVENT_GFX_POSE_GREEN' in avatar


def test_num_obj_event_gfx_is_defined_exactly_once(tmp_path):
    root = _project(tmp_path)
    ensure_player_pose(root, 'POSE', 'red_pose', 'green_pose')
    text = _read(root, 'include/constants/event_objects.h')
    assert len(re.findall(r'^#define NUM_OBJ_EVENT_GFX', text, re.M)) == 1


def test_incbin_paths_use_forward_slashes(tmp_path):
    """Backslashes from a Windows path join would not build."""
    root = _project(tmp_path)
    ensure_player_pose(root, 'POSE', 'red_pose', 'green_pose')
    gfx = _read(root, 'src/data/object_events/object_event_graphics.h')
    line = [l for l in gfx.splitlines() if 'RedPose' in l][0]
    assert '\\' not in line and 'pics/people/red_pose.4bpp' in line


def test_is_idempotent(tmp_path):
    root = _project(tmp_path)
    ensure_player_pose(root, 'POSE', 'red_pose', 'green_pose')
    snap = {f: _read(root, f) for f in (
        'include/constants/event_objects.h', 'include/global.fieldmap.h',
        'src/field_player_avatar.c')}
    assert 'already registered' in ensure_player_pose(
        root, 'POSE', 'red_pose', 'green_pose')
    for f, before in snap.items():
        assert _read(root, f) == before


# ─────────────────────────────────────────────────────────────────────────────
# Refusing rather than guessing
# ─────────────────────────────────────────────────────────────────────────────

def test_refuses_mismatched_frame_counts(tmp_path):
    """Two genders animating differently is not something to silently allow."""
    root = _project(tmp_path, red_h=32, green_h=32 * 3)
    with pytest.raises(PlayerPosePatchError, match='frame'):
        ensure_player_pose(root, 'POSE', 'red_pose', 'green_pose')


def test_refuses_a_wrong_width_sheet(tmp_path):
    root = _project(tmp_path)
    _png(os.path.join(root, _PICS, 'red_pose.png'), 24, 32)
    with pytest.raises(PlayerPosePatchError, match='wide'):
        ensure_player_pose(root, 'POSE', 'red_pose', 'green_pose')


def test_refuses_a_part_frame_height(tmp_path):
    root = _project(tmp_path, red_h=40)
    with pytest.raises(PlayerPosePatchError, match='whole number'):
        ensure_player_pose(root, 'POSE', 'red_pose', 'green_pose')


def test_refuses_a_missing_sheet(tmp_path):
    root = _project(tmp_path)
    os.remove(os.path.join(root, _PICS, 'green_pose.png'))
    with pytest.raises(PlayerPosePatchError, match='not in graphics'):
        ensure_player_pose(root, 'POSE', 'red_pose', 'green_pose')


def test_refuses_a_project_without_the_avatar_table(tmp_path):
    root = _project(tmp_path, with_avatar_table=False)
    assert not project_supports_poses(root)
    with pytest.raises(PlayerPosePatchError, match='sPlayerAvatarGfxIds'):
        ensure_player_pose(root, 'POSE', 'red_pose', 'green_pose')


def test_refuses_a_bad_state_name(tmp_path):
    root = _project(tmp_path)
    for bad in ('1POSE', 'my pose', 'pose-x', ''):
        with pytest.raises(PlayerPosePatchError):
            ensure_player_pose(root, bad, 'red_pose', 'green_pose')


def test_describe_changes_nothing(tmp_path):
    root = _project(tmp_path)
    before = _read(root, 'src/field_player_avatar.c')
    msg = describe(root, 'POSE', 'red_pose', 'green_pose')
    assert 'one frame' in msg
    assert not is_pose_installed(root, 'POSE')
    assert _read(root, 'src/field_player_avatar.c') == before


# ─────────────────────────────────────────────────────────────────────────────
# Pointing a cutscene at the new pose
# ─────────────────────────────────────────────────────────────────────────────

def test_repoints_only_the_named_function(tmp_path):
    """A file with several pose swaps must keep the others — in particular the
    RESTORE call, or the pose would never revert."""
    root = _project(tmp_path)
    ensure_player_pose(root, 'POSE', 'red_pose', 'green_pose')
    apply_pose_to_call(root, 'src/cutscene.c', 'ApplyPose', 'POSE')
    text = _read(root, 'src/cutscene.c')
    apply_block = text[text.index('ApplyPose'):text.index('RestorePose')]
    restore_block = text[text.index('static void RestorePose'):]
    assert 'PLAYER_AVATAR_GFX_POSE' in apply_block
    assert 'PLAYER_AVATAR_GFX_NORMAL' in restore_block
    assert 'PLAYER_AVATAR_GFX_POSE' not in restore_block


def test_repointing_is_idempotent(tmp_path):
    root = _project(tmp_path)
    ensure_player_pose(root, 'POSE', 'red_pose', 'green_pose')
    apply_pose_to_call(root, 'src/cutscene.c', 'ApplyPose', 'POSE')
    assert 'already uses' in apply_pose_to_call(
        root, 'src/cutscene.c', 'ApplyPose', 'POSE')


def test_repointing_refuses_an_unknown_function(tmp_path):
    root = _project(tmp_path)
    with pytest.raises(PlayerPosePatchError, match='not found'):
        apply_pose_to_call(root, 'src/cutscene.c', 'NoSuchThing', 'POSE')


def test_repointing_refuses_a_function_with_no_pose_call(tmp_path):
    root = _project(tmp_path)
    with open(os.path.join(root, 'src/cutscene.c'), 'a', encoding='utf-8') as fh:
        fh.write('\nstatic void Unrelated(void)\n{\n    DoNothing();\n}\n')
    with pytest.raises(PlayerPosePatchError, match='does not call'):
        apply_pose_to_call(root, 'src/cutscene.c', 'Unrelated', 'POSE')
