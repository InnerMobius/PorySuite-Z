"""Tests for ``core/battle_anim_vm.py`` — the per-frame sprite simulator.

Proves the fundamentals the old archetype model couldn't: sprites move per
their real callbacks, animate, and self-destruct.
"""

from __future__ import annotations

import importlib.util
import os
import sys

_THIS = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.abspath(os.path.join(_THIS, ".."))


def _load():
    path = os.path.join(_ROOT, "core", "battle_anim_vm.py")
    spec = importlib.util.spec_from_file_location("battle_anim_vm", path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules.setdefault("battle_anim_vm", mod)
    spec.loader.exec_module(mod)
    return mod


vm = _load()


def _ctx(args=None, atk=(72, 80), tgt=(176, 40),
         atk_side=None, tgt_side=None):
    a = vm.Battler(atk[0], atk[1], atk_side or vm.SIDE_PLAYER)
    t = vm.Battler(tgt[0], tgt[1], tgt_side or vm.SIDE_OPPONENT)
    return vm.AnimContext(attacker=a, target=t, args=list(args or []))


def test_gba_sin_range():
    assert vm.gba_sin(0, 12) == 0
    assert vm.gba_sin(64, 12) == 12        # quarter turn → +amplitude
    assert vm.gba_sin(192, 12) == -12      # three-quarter → -amplitude


def test_ghost_rises_and_dies():
    ctx = _ctx()  # player attacks
    s = vm.spawn("AnimGhostStatusSprite", ctx, tag="ANIM_TAG_GHOSTLY_SPIRIT")
    assert s is not None
    # Starts at the target.
    assert s.x == ctx.target.x
    ys = []
    for _ in range(40):
        if not s.alive:
            break
        s.step(ctx)
        ys.append(s.render_y)
    # It rose: later y is well above (smaller than) the start.
    assert ys[-1] < ys[0], f"ghost should rise: {ys[0]} -> {ys[-1]}"
    # And it wobbles horizontally (x2 not constant).
    # Eventually it dies.
    for _ in range(200):
        if not s.alive:
            break
        s.step(ctx)
    assert not s.alive, "ghost must self-destruct"


def test_ember_translates_attacker_to_target():
    # createsprite gEmberSpriteTemplate ... 20,0,-16,24,20,1
    ctx = _ctx(args=[20, 0, -16, 24, 20, 1])
    s = vm.spawn("TranslateAnimSpriteToTargetMonLocation", ctx,
                 tag="ANIM_TAG_SMALL_EMBER")
    start_x = s.render_x
    # Starts near the attacker (player at x=72, +20 toward target).  The
    # engine applies one translation step during StartAnimLinearTranslation,
    # so allow a few px of initial drift.
    assert abs(start_x - (72 + 20)) <= 8, start_x
    xs = [s.render_x]
    for _ in range(20):
        s.step(ctx)
        xs.append(s.render_x)
    # Moved rightward toward the target (x increases).
    assert xs[-1] > xs[0] + 30, f"ember should travel toward target: {xs[0]}->{xs[-1]}"
    # Ends near the target x (176 - 16 = 160).
    assert abs(xs[-1] - 160) <= 6, xs[-1]


def test_ember_destroys_on_arrival():
    ctx = _ctx(args=[20, 0, -16, 24, 20, 1])
    s = vm.spawn("TranslateAnimSpriteToTargetMonLocation", ctx)
    for _ in range(40):
        if not s.alive:
            break
        s.step(ctx)
    assert not s.alive, "ember must destroy after reaching the target"


def test_curse_nail_advances_frames_then_dies():
    ctx = _ctx()  # player attacks
    s = vm.spawn("AnimCurseNail", ctx, tag="ANIM_TAG_NAIL")
    # Nail sits at attacker + 24 (player side).
    assert s.x == 72 + 24, s.x
    for _ in range(400):
        if not s.alive:
            break
        s.step(ctx)
    assert s.frame_advance >= 1, "nail should advance its sheet frames"
    assert not s.alive, "nail must self-destruct after its sequence"


def test_x_offset_points_toward_target_both_directions():
    # Player attacks: target to the right → +arg0 moves right.
    ctx = _ctx(args=[20, 0])
    s = vm.spawn("AnimSpriteOnMonPos", ctx)   # arg2==0 → attacker anchor
    assert s.x == 72 + 20
    # Enemy attacks (attacker on the right): +arg0 moves left.
    ctx2 = _ctx(args=[20, 0], atk=(176, 40), tgt=(72, 80),
                atk_side=vm.SIDE_OPPONENT, tgt_side=vm.SIDE_PLAYER)
    s2 = vm.spawn("AnimSpriteOnMonPos", ctx2)
    assert s2.x == 176 - 20


def test_fallback_sits_then_destroys():
    ctx = _ctx()
    s = vm.spawn("SomeUnportedBespokeCallback", ctx,
                 fallback_lifetime=10)
    assert s is not None and s.alive
    for _ in range(9):
        s.step(ctx)
    assert s.alive          # still within lifetime
    for _ in range(5):
        s.step(ctx)
    assert not s.alive      # destroyed after lifetime


def test_setup_linear_travels_and_destroys():
    ctx = _ctx()
    s = vm.new_sprite(tag="x")
    s.x, s.y = 72, 80
    vm.setup_linear(s, (176, 40), 20)
    xs = [s.render_x]
    for _ in range(20):
        if not s.alive:
            break
        s.step(ctx)
        xs.append(s.render_x)
    assert xs[-1] > xs[0] + 30, xs            # moved toward the dest
    for _ in range(8):
        if not s.alive:
            break
        s.step(ctx)
    assert not s.alive                        # destroyed after arrival


def test_setup_arc_rises_then_lands():
    ctx = _ctx()
    s = vm.new_sprite(tag="x")
    s.x, s.y = 72, 80
    vm.setup_arc(s, (176, 80), 20, height=30)
    ys = []
    for _ in range(20):
        if not s.alive:
            break
        s.step(ctx)
        ys.append(s.render_y)
    # Peaks above the straight line (some y well above the 80 endpoints).
    assert min(ys) < 70, ys
    assert not s.alive or s.render_x > 150     # ends near the dest x


def test_setup_static_holds_then_destroys():
    ctx = _ctx()
    s = vm.new_sprite(tag="x")
    s.x, s.y = 100, 100
    vm.setup_static(s, 5)
    for _ in range(4):
        s.step(ctx)
    assert s.alive and s.render_x == 100      # held in place
    for _ in range(4):
        if not s.alive:
            break
        s.step(ctx)
    assert not s.alive


def test_bite_teeth_close_then_destroy_and_lower_jaw_flips():
    # Upper fang: starts above the target (arg1=-32), closes toward centre.
    up = _ctx(args=[0, -32, 0, 0, 819, 10])
    s = vm.spawn("AnimBite", up, tag="ANIM_TAG_SHARP_TEETH")
    assert s.flip_v is False
    assert s.y == up.target.y - 32          # starts above target
    ys = []
    for _ in range(10):
        s.step(up)
        ys.append(s.render_y)
    assert ys[-1] > ys[0]                    # moved DOWN toward centre (closing)
    # Then it opens back and self-destructs.
    for _ in range(20):
        if not s.alive:
            break
        s.step(up)
    assert not s.alive
    # Lower fang: arg2 != 0 → vertically flipped.
    lo = _ctx(args=[0, 32, 4, 0, -819, 10])
    s2 = vm.spawn("AnimBite", lo, tag="ANIM_TAG_SHARP_TEETH")
    assert s2.flip_v is True
    assert s2.y == lo.target.y + 32          # starts below target


def test_animsim_steps_and_prunes():
    ctx = _ctx()
    sim = vm.AnimSim(ctx)
    sim.add(vm.spawn("SomeUnported", ctx, fallback_lifetime=3))
    sim.add(vm.spawn("AnimGhostStatusSprite", ctx))
    assert sim.active()
    for _ in range(5):
        sim.step()
    # The short-lived fallback is pruned; the ghost may still be alive.
    assert all(s.alive for s in sim.sprites)
