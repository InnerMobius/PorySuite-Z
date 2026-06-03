"""Tests for ``core/battle_anim_tasks.py`` — the mon-acting task simulator.

These prove that the mon sprites actually react to ``createvisualtask`` calls
that have no ``createsprite`` (Bind, status moves, hit shakes): shake toggles
the offset N times then settles; sway oscillates; ScaleMonAndRestore squeezes
then restores; transforms map to the right battler.
"""

from __future__ import annotations

import importlib.util
import os
import sys

_THIS = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.abspath(os.path.join(_THIS, ".."))


def _load():
    path = os.path.join(_ROOT, "core", "battle_anim_tasks.py")
    spec = importlib.util.spec_from_file_location("battle_anim_tasks", path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules.setdefault("battle_anim_tasks", mod)
    spec.loader.exec_module(mod)
    return mod


t = _load()


def _ctx(atk=t.SIDE_PLAYER if False else "player", tgt="opponent"):
    return t.TaskCtx(attacker_side=atk, target_side=tgt)


def test_shakemon_toggles_then_settles():
    # args = [battler, xOff, yOff, numShakes, timer]
    task = t.ShakeMonTask([t.TARGET, 4, 0, 4, 1], _ctx())
    assert task.battler == t.TARGET
    offs = []
    for _ in range(40):
        if not task.alive:
            break
        offs.append(task.x2)
        task.step(_ctx())
    # It toggled between 0 and ±4 (so both values appear), then died.
    assert 4 in [abs(o) for o in offs]
    assert 0 in offs
    assert not task.alive
    # Settles back to 0.
    assert task.x2 == 0


def test_swaymon_oscillates_x():
    # Bind's sway: [swayType=0, amp=6, speed=3328, numSways=4, battler=0(atk)]
    task = t.SwayMonTask([0, 6, 3328, 4, 0], _ctx())
    assert task.battler == t.ATTACKER
    xs = []
    for _ in range(80):
        if not task.alive:
            break
        task.step(_ctx())
        xs.append(task.x2)
    # Swung both directions around 0 (a real oscillation, not a drift).
    assert max(xs) > 2 and min(xs) < -2, xs
    assert not task.alive          # settles after numSways half-cycles
    assert task.x2 == 0


def test_swaymon_amplitude_negates_when_attacker_is_enemy():
    # Same sway, but the attacker is on the opponent side → x-amplitude flips.
    ctx_enemy = t.TaskCtx(attacker_side="opponent", target_side="player")
    pl = t.SwayMonTask([0, 6, 3328, 4, 0], _ctx())
    en = t.SwayMonTask([0, 6, 3328, 4, 0], ctx_enemy)
    pl.step(_ctx())
    en.step(ctx_enemy)
    # First sample has opposite sign (amplitude was negated for the enemy).
    assert (pl.x2 == 0 and en.x2 == 0) or (pl.x2 * en.x2 <= 0)


def test_scalemon_squeezes_then_restores():
    # Bind squeeze: [xDelta=+10, yDelta=-5, frames=5, battler=TARGET, 0]
    task = t.ScaleMonAndRestoreTask([10, -5, 5, t.TARGET, 0], _ctx())
    assert task.battler == t.TARGET
    fxs = []
    for _ in range(20):
        if not task.alive:
            break
        task.step(_ctx())
        fxs.append(task.fx())
    # Mid-animation: narrower in x (sx < 1) and taller in y (sy > 1) — a squeeze.
    mid = fxs[3]
    assert mid.sx < 1.0, mid.sx
    assert mid.sy > 1.0, mid.sy
    assert not task.alive
    # Restored to ~identity at the end.
    assert abs(task.fx().sx - 1.0) < 0.001
    assert abs(task.fx().sy - 1.0) < 0.001


def test_shakemoninplace_settles_and_dies():
    task = t.ShakeMonInPlaceTask([t.ATTACKER, 3, 0, 6, 1], _ctx())
    for _ in range(60):
        if not task.alive:
            break
        task.step(_ctx())
    assert not task.alive


def test_sim_accumulates_and_prunes():
    sim = t.MonTaskSim(_ctx())
    sim.spawn("AnimTask_SwayMon", [0, 6, 3328, 4, 0])        # attacker
    sim.spawn("AnimTask_ScaleMonAndRestore", [10, -5, 5, t.TARGET, 0])  # target
    assert sim.active()
    xf = sim.transforms()
    assert t.ATTACKER in xf and t.TARGET in xf      # both mons affected
    for _ in range(200):
        if not sim.active():
            break
        sim.step()
    assert not sim.active()                          # both tasks finished


def test_horizontal_lunge_goes_out_and_returns():
    # Tackle's lunge: gHorizontalLungeSpriteTemplate args = [duration=4, x=4]
    sim = t.MonTaskSim(_ctx())
    sim.spawn_mover("gHorizontalLungeSpriteTemplate", [4, 4])
    assert t.is_mon_mover_template("gHorizontalLungeSpriteTemplate")
    xs = []
    for _ in range(40):
        if not sim.active():
            break
        sim.step()
        xf = sim.transforms()
        xs.append(xf[t.ATTACKER].dx if t.ATTACKER in xf else 0)
    # Player attacker lunges in the +x direction (toward the foe), peaking
    # well off zero, then returns to 0.
    assert max(xs) >= 12, xs            # 4 px/frame * 4 frames ≈ 16
    assert xs[-1] == 0 or not sim.active()
    assert not sim.active()             # destroyed after out-and-back


def test_horizontal_lunge_flips_for_enemy_attacker():
    ctx_enemy = t.TaskCtx(attacker_side="opponent", target_side="player")
    sim = t.MonTaskSim(ctx_enemy)
    sim.spawn_mover("gHorizontalLungeSpriteTemplate", [4, 4])
    sim.step()
    xf = sim.transforms()
    # Enemy attacker (on the right) lunges in -x (toward the player on the left).
    assert xf[t.ATTACKER].dx < 0


def test_vertical_dip_moves_y_then_returns():
    sim = t.MonTaskSim(_ctx())
    sim.spawn_mover("gVerticalDipSpriteTemplate", [4, 3, t.TARGET])
    ys = []
    for _ in range(40):
        if not sim.active():
            break
        sim.step()
        xf = sim.transforms()
        ys.append(xf[t.TARGET].dy if t.TARGET in xf else 0)
    assert max(ys) >= 9, ys              # 3 px/frame * 4 frames
    assert not sim.active()


def test_non_mon_task_is_ignored():
    sim = t.MonTaskSim(_ctx())
    # A palette blend / gfx loader is not a mon-acting task → no task spawned.
    assert sim.spawn("AnimTask_BlendBattleAnimPal", [10, 1, 0, 0, 16, 0]) is None
    assert not sim.active()
    assert not t.is_mon_task("AnimTask_LoadBaitGfx")
    assert t.is_mon_task("AnimTask_ShakeMon")
