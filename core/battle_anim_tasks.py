"""Mon-acting battle-animation TASK simulator.

Many battle animations have NO ``createsprite`` at all — they're driven
entirely by ``createvisualtask`` calls that move / shake / scale the *mon
sprites themselves*.  Examples:

* **Bind** sways the attacker (``AnimTask_SwayMon``) and squeezes the target
  (``AnimTask_ScaleMonAndRestore``) — zero anim sprites.
* Most damaging moves shake the target on impact (``AnimTask_ShakeMon``).
* Many status / general effects act only on a mon.

The sprite VM (:mod:`core.battle_anim_vm`) can't show any of these because
there's no anim sprite to spawn — so in the editor they looked like "nothing
happens".  This module fills that gap: it ports the common *mon-manipulation*
tasks with the SAME per-frame math as pokefirered's ``AnimTask_*`` step
functions and exposes, each frame, a transform (pixel offset + scale) for the
attacker and/or target mon.  The battle preview applies those transforms when
it draws the mon pixmaps, so the mons actually react.

Only mon-acting tasks are modelled.  Palette blends, BG scrolls, gfx loaders,
and sprite-spawning tasks are intentionally ignored here (they either don't
move a mon, or their sprites come through the script's ``createsprite`` rows).

Pure stdlib + math (no Qt) so it's unit-testable in isolation, exactly like
``battle_anim_vm``.  The sine math is identical to the VM's ``gba_sin`` so
sway/scale timing matches the game.
"""

from __future__ import annotations

import math
from typing import Callable, Dict, List, Optional


# Side tags mirror battle_anim_vm so the two modules speak the same language.
SIDE_PLAYER = "player"
SIDE_OPPONENT = "opponent"

# Battler selector indices (gBattleAnimArgs battler values).
ATTACKER = 0
TARGET = 1


def gba_sin(index: int, amplitude: int) -> int:
    """pokefirered ``Sin(index, amplitude)`` — 256-step sine scaled by
    amplitude; ``index`` wraps mod 256.  Identical to the VM's table."""
    return int(round(amplitude * math.sin((index & 0xFF) * math.pi / 128.0)))


def _to_battler(v: int) -> int:
    """Map a gBattleAnimArgs battler selector to ATTACKER/TARGET for the
    single-battle preview.  0/2 (self + ally) → attacker side; 1/3 (foe + its
    ally) → target side."""
    return TARGET if (int(v) & 1) else ATTACKER


class TaskCtx:
    """Which side each battler's mon is on (needed for the engine's
    side-dependent sign rules in SwayMon)."""

    __slots__ = ("attacker_side", "target_side")

    def __init__(self, attacker_side: str = SIDE_PLAYER,
                 target_side: str = SIDE_OPPONENT):
        self.attacker_side = attacker_side
        self.target_side = target_side

    def side_of(self, battler: int) -> str:
        return self.attacker_side if battler == ATTACKER else self.target_side


class MonFx:
    """One frame's transform for a mon: pixel offset (dx, dy) + scale
    (sx, sy), where scale is a DISPLAY multiplier (1.0 = unchanged)."""

    __slots__ = ("dx", "dy", "sx", "sy")

    def __init__(self, dx: int = 0, dy: int = 0, sx: float = 1.0, sy: float = 1.0):
        self.dx = dx
        self.dy = dy
        self.sx = sx
        self.sy = sy


class MonTask:
    """Base mon-acting task.  Subclasses port a pokefirered ``AnimTask_*``
    state machine: ``__init__`` mirrors the C init (including the immediate
    first ``func()`` call the engine makes), and :meth:`step` runs one frame.

    ``battler`` is ATTACKER or TARGET — which mon this task transforms.
    Offset is held in ``x2``/``y2`` (pixels); scale in ``xscale``/``yscale``
    (8.8 fixed-point as the engine stores it; display scale = 256 / value)."""

    __slots__ = ("battler", "alive", "data", "x2", "y2", "xscale", "yscale")

    def __init__(self, battler: int):
        self.battler = battler
        self.alive = True
        self.data = [0] * 16
        self.x2 = 0
        self.y2 = 0
        self.xscale = 0x100      # 1.0 in 8.8 fixed-point
        self.yscale = 0x100

    def step(self, ctx: TaskCtx) -> None:  # pragma: no cover - overridden
        raise NotImplementedError

    def fx(self) -> MonFx:
        sx = 256.0 / self.xscale if self.xscale else 1.0
        sy = 256.0 / self.yscale if self.yscale else 1.0
        return MonFx(int(self.x2), int(self.y2), sx, sy)


# ── shake family ──────────────────────────────────────────────────────────

class _ShakeBase(MonTask):
    """Common storage for the shake tasks (they share the data layout)."""

    __slots__ = ()


class ShakeMonTask(_ShakeBase):
    """``AnimTask_ShakeMon`` — toggles the mon between (xOff, yOff) and (0, 0)
    ``numShakes`` times, ``timer`` frames apart, then snaps back to 0.

    args = [battler, xOff, yOff, numShakes, timer]"""

    def __init__(self, args: List[int], ctx: TaskCtx):
        super().__init__(_to_battler(args[0] if len(args) > 0 else 0))
        x_off = args[1] if len(args) > 1 else 0
        y_off = args[2] if len(args) > 2 else 0
        self.x2 = x_off
        self.y2 = y_off
        self.data[1] = args[3] if len(args) > 3 else 1      # numShakes
        self.data[2] = args[4] if len(args) > 4 else 1      # timer reload
        self.data[3] = self.data[2]                          # timer
        self.data[4] = x_off
        self.data[5] = y_off
        self._core()                                         # engine runs step once

    def _core(self) -> None:
        d = self.data
        if d[3] == 0:
            self.x2 = d[4] if self.x2 == 0 else 0
            self.y2 = d[5] if self.y2 == 0 else 0
            d[3] = d[2]
            d[1] -= 1
            if d[1] == 0:
                self.x2 = 0
                self.y2 = 0
                self.alive = False
        else:
            d[3] -= 1

    def step(self, ctx: TaskCtx) -> None:
        self._core()


class ShakeMon2Task(ShakeMonTask):
    """``AnimTask_ShakeMon2`` — like ShakeMon but toggles between +off and
    -off (not 0), so the mon shudders symmetrically."""

    def _core(self) -> None:
        d = self.data
        if d[3] == 0:
            self.x2 = -d[4] if self.x2 == d[4] else d[4]
            self.y2 = -d[5] if self.y2 == d[5] else d[5]
            d[3] = d[2]
            d[1] -= 1
            if d[1] == 0:
                self.x2 = 0
                self.y2 = 0
                self.alive = False
        else:
            d[3] -= 1


class ShakeMonInPlaceTask(_ShakeBase):
    """``AnimTask_ShakeMonInPlace`` — accumulating shudder: alternately adds
    and subtracts (2*xOff, 2*yOff) ``count`` times, then a half-step to settle.

    args = [battler, xOff, yOff, count, timer]"""

    def __init__(self, args: List[int], ctx: TaskCtx):
        super().__init__(_to_battler(args[0] if len(args) > 0 else 0))
        x_off = args[1] if len(args) > 1 else 0
        y_off = args[2] if len(args) > 2 else 0
        self.x2 = x_off
        self.y2 = y_off
        self.data[1] = 0                                     # iteration
        self.data[2] = args[3] if len(args) > 3 else 1       # count
        self.data[3] = 0                                     # timer
        self.data[4] = args[4] if len(args) > 4 else 1       # timer reload
        self.data[5] = x_off * 2
        self.data[6] = y_off * 2
        self._core()

    def _core(self) -> None:
        d = self.data
        if d[3] == 0:
            if d[1] & 1:
                self.x2 += d[5]
                self.y2 += d[6]
            else:
                self.x2 -= d[5]
                self.y2 -= d[6]
            d[3] = d[4]
            d[1] += 1
            if d[1] >= d[2]:
                if d[1] & 1:
                    self.x2 += d[5] // 2
                    self.y2 += d[6] // 2
                else:
                    self.x2 -= d[5] // 2
                    self.y2 -= d[6] // 2
                self.alive = False
        else:
            d[3] -= 1

    def step(self, ctx: TaskCtx) -> None:
        self._core()


# ── sway ─────────────────────────────────────────────────────────────────

class SwayMonTask(MonTask):
    """``AnimTask_SwayMon`` — a sine sway on x (swayType 0) or y (swayType 1)
    of the chosen mon, running ``numSways`` half-periods then settling.

    args = [swayType, amplitude, speed, numSways, battler]"""

    def __init__(self, args: List[int], ctx: TaskCtx):
        battler = ATTACKER if (len(args) > 4 and args[4] == 0) else TARGET
        super().__init__(battler)
        amp = args[1] if len(args) > 1 else 0
        # Engine negates the x-amplitude when the ATTACKER is not the player.
        if ctx.attacker_side != SIDE_PLAYER:
            amp = -amp
        self.data[0] = args[0] if len(args) > 0 else 0       # swayType
        self.data[1] = amp                                   # amplitude
        self.data[2] = args[2] if len(args) > 2 else 0       # speed
        self.data[3] = args[3] if len(args) > 3 else 1       # numSways
        self.data[10] = 0                                    # sine accumulator
        self.data[11] = 0
        self.data[12] = 1

    def step(self, ctx: TaskCtx) -> None:
        d = self.data
        acc = (d[10] + d[2]) & 0xFFFF                         # u16 sine index
        d[10] = acc
        wave = acc >> 8                                       # 0..255
        sine = gba_sin(wave, d[1])
        if d[0] == 0:
            self.x2 = sine
        else:
            side = ctx.side_of(self.battler)
            self.y2 = abs(sine) if side == SIDE_PLAYER else -abs(sine)
        if ((wave > 0x7F and d[11] == 0 and d[12] == 1)
                or (wave < 0x7F and d[11] == 1 and d[12] == 0)):
            d[11] ^= 1
            d[12] ^= 1
            d[3] -= 1
            if d[3] == 0:
                self.x2 = 0
                self.y2 = 0
                self.alive = False


# ── scale ────────────────────────────────────────────────────────────────

class ScaleMonAndRestoreTask(MonTask):
    """``AnimTask_ScaleMonAndRestore`` — ramps the mon's scale by
    (xDelta, yDelta) per frame for ``frames`` frames, then reverses to restore.
    Bind uses (+10, -5): the target gets narrower + taller (a squeeze).

    args = [xDelta, yDelta, frames, battler, mode]"""

    def __init__(self, args: List[int], ctx: TaskCtx):
        super().__init__(_to_battler(args[3] if len(args) > 3 else 1))
        self.data[0] = args[0] if len(args) > 0 else 0       # xScale delta
        self.data[1] = args[1] if len(args) > 1 else 0       # yScale delta
        self.data[2] = args[2] if len(args) > 2 else 1       # frames
        self.data[3] = args[2] if len(args) > 2 else 1       # restore frames

    def step(self, ctx: TaskCtx) -> None:
        d = self.data
        self.xscale += d[0]
        self.yscale += d[1]
        if self.xscale < 1:
            self.xscale = 1
        if self.yscale < 1:
            self.yscale = 1
        d[2] -= 1
        if d[2] == 0:
            if d[3] > 0:
                d[0] = -d[0]
                d[1] = -d[1]
                d[2] = d[3]
                d[3] = 0
            else:
                self.xscale = 0x100
                self.yscale = 0x100
                self.alive = False


# ── mon-mover dummy sprites ────────────────────────────────────────────────
# Some animations move a mon via a DUMMY (invisible, no-gfx) ``createsprite``
# whose callback writes the mon's x2/y2 (e.g. Tackle's forward lunge).  These
# aren't visible sprites — they're mon-movers, so they belong here, not in the
# sprite VM.  They all share TranslateSpriteLinearById: add (dx, dy) to the mon
# each frame for ``frames`` frames, then run a stored follow-up.

class _TwoPhaseTranslate(MonTask):
    """Mirrors DoHorizontalLunge / DoVerticalDip: translate the mon by
    (dx, dy) per frame for ``frames`` frames, then reverse for ``frames``
    frames (back to the start), then destroy.  One idle frame at the turn —
    matching the engine's callback-swap frame — keeps the timing faithful."""

    __slots__ = ("_dx", "_dy", "_frames", "_phase")

    def __init__(self, battler: int, frames: int, dx: int, dy: int):
        super().__init__(battler)
        self._dx = dx
        self._dy = dy
        self._frames = max(1, frames)
        self._phase = 0
        self.data[0] = self._frames

    def step(self, ctx: TaskCtx) -> None:
        if self.data[0] > 0:
            self.data[0] -= 1
            self.x2 += self._dx
            self.y2 += self._dy
        elif self._phase == 0:
            # Engine swaps to the reverse callback this frame (no movement).
            self._phase = 1
            self.data[0] = self._frames
            self._dx = -self._dx
            self._dy = -self._dy
        else:
            self.x2 = 0
            self.y2 = 0
            self.alive = False


def _horizontal_lunge(args: List[int], ctx: TaskCtx) -> MonTask:
    """gHorizontalLungeSpriteTemplate — attacker lunges toward the foe and
    back.  args = [duration, xOffsetPerFrame]; x-sign points toward the foe
    (negated when the attacker is on the opponent side)."""
    frames = args[0] if len(args) > 0 else 1
    dx = args[1] if len(args) > 1 else 0
    if ctx.attacker_side != SIDE_PLAYER:
        dx = -dx
    return _TwoPhaseTranslate(ATTACKER, frames, dx, 0)


def _vertical_dip(args: List[int], ctx: TaskCtx) -> MonTask:
    """gVerticalDipSpriteTemplate — a mon dips down and back.
    args = [duration, yDeltaPerFrame, battler]."""
    frames = args[0] if len(args) > 0 else 1
    dy = args[1] if len(args) > 1 else 0
    battler = _to_battler(args[2] if len(args) > 2 else 0)
    return _TwoPhaseTranslate(battler, frames, 0, dy)


MON_MOVER_TEMPLATES: Dict[str, Callable[[List[int], TaskCtx], MonTask]] = {
    "gHorizontalLungeSpriteTemplate": _horizontal_lunge,
    "gVerticalDipSpriteTemplate": _vertical_dip,
}


def is_mon_mover_template(template: str) -> bool:
    return template in MON_MOVER_TEMPLATES


# Registry: task symbol → factory(args, ctx) → MonTask.
TASK_CALLBACKS: Dict[str, Callable[[List[int], TaskCtx], MonTask]] = {
    "AnimTask_ShakeMon": ShakeMonTask,
    "AnimTask_ShakeMon2": ShakeMon2Task,
    "AnimTask_ShakeMonInPlace": ShakeMonInPlaceTask,
    "AnimTask_SwayMon": SwayMonTask,
    "AnimTask_ScaleMonAndRestore": ScaleMonAndRestoreTask,
}


def is_mon_task(symbol: str) -> bool:
    return symbol in TASK_CALLBACKS


def spawn_task(symbol: str, args: List[int], ctx: TaskCtx) -> Optional[MonTask]:
    """Build a mon-task from its symbol + int-coerced args, or ``None`` if the
    symbol isn't a modelled mon-acting task."""
    factory = TASK_CALLBACKS.get(symbol)
    if factory is None:
        return None
    try:
        t = factory(list(args), ctx)
    except Exception:
        return None
    return t if t.alive else t  # keep even one-frame tasks; sim prunes


class MonTaskSim:
    """Holds the running mon-tasks and accumulates their per-frame transforms.
    Mirrors :class:`core.battle_anim_vm.AnimSim` for the task side."""

    def __init__(self, ctx: TaskCtx):
        self.ctx = ctx
        self.tasks: List[MonTask] = []

    def spawn(self, symbol: str, args: List[int]) -> Optional[MonTask]:
        t = spawn_task(symbol, args, self.ctx)
        if t is not None:
            self.tasks.append(t)
        return t

    def spawn_mover(self, template: str, args: List[int]) -> Optional[MonTask]:
        """Spawn a mon-mover dummy sprite (lunge / dip) as a mon-task."""
        factory = MON_MOVER_TEMPLATES.get(template)
        if factory is None:
            return None
        try:
            t = factory(list(args), self.ctx)
        except Exception:
            return None
        self.tasks.append(t)
        return t

    def step(self) -> None:
        for t in self.tasks:
            if t.alive:
                t.step(self.ctx)
        self.tasks = [t for t in self.tasks if t.alive]

    def active(self) -> bool:
        return any(t.alive for t in self.tasks)

    def transforms(self) -> Dict[int, MonFx]:
        """Accumulated transform per battler (offsets add, scales multiply)."""
        out: Dict[int, MonFx] = {}
        for t in self.tasks:
            if not t.alive:
                continue
            fx = t.fx()
            cur = out.get(t.battler)
            if cur is None:
                out[t.battler] = MonFx(fx.dx, fx.dy, fx.sx, fx.sy)
            else:
                cur.dx += fx.dx
                cur.dy += fx.dy
                cur.sx *= fx.sx
                cur.sy *= fx.sy
        return out
