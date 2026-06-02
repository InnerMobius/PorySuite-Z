"""A small per-frame battle-anim sprite simulator ("VM").

The Battle Anims preview used to approximate each sprite as one of a few
static/linear "archetypes" guessed from its init callback.  That can't
capture real motion: pokefirered battle-anim sprites are per-frame state
machines — each has ``data[0..7]``, a position (``x``/``y`` plus the
``x2``/``y2`` offsets the movers actually write), an ``oam.tileNum`` frame
cursor, and a ``callback`` that runs every frame, mutates state, chains to
the next callback, and eventually destroys the sprite.

This module ports that model: the shared motion PRIMITIVES the engine reuses
(linear translation, wait-for-duration, destroy + stored-followup chaining)
plus a registry of per-sprite init callbacks.  A sprite whose callback is in
the registry runs faithfully (real trajectory, frame cursor, self-destruct);
anything not yet ported falls back to a generic "sit then destroy" so it
still shows and clears.

Pure stdlib + math (no Qt), so it's unit-testable: e.g. the Curse ghost's
``y2`` must decrease over time (it rises), Ember's ``x2`` must ramp toward
the target, and the nail must self-destroy after its sequence.

Coordinate space is the 240x160 GBA canvas.  Battler frame-centers and
side come from the caller (so the Player/Enemy direction toggle works).
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional


SIDE_PLAYER = "player"
SIDE_OPPONENT = "opponent"


def gba_sin(index: int, amplitude: int) -> int:
    """pokefirered ``Sin(index, amplitude)`` — a 256-step sine scaled by
    amplitude.  ``index`` wraps mod 256."""
    return int(round(amplitude * math.sin((index & 0xFF) * math.pi / 128.0)))


@dataclass
class Battler:
    x: int
    y: int
    side: str          # SIDE_PLAYER / SIDE_OPPONENT


@dataclass
class AnimContext:
    """Per-animation runtime context: who's attacking/targeted + the current
    command's ``gBattleAnimArgs`` (already int-coerced; non-numeric → 0)."""
    attacker: Battler
    target: Battler
    args: List[int] = field(default_factory=list)

    def arg(self, i: int) -> int:
        return self.args[i] if 0 <= i < len(self.args) else 0


class Sprite:
    """One battle-anim sprite's runtime state (mirrors the engine fields the
    common callbacks touch)."""

    __slots__ = ("x", "y", "x2", "y2", "data", "tile", "invisible", "alive",
                 "callback", "callback6", "tag", "subpriority", "age",
                 "frame_advance", "flip", "_ctx")

    def __init__(self, tag: str = "", subpriority: int = 0):
        self.x = 0
        self.y = 0
        self.x2 = 0
        self.y2 = 0
        self.data = [0] * 8
        self.tile = 0            # oam.tileNum-style frame cursor
        self.invisible = False
        self.alive = True
        self.callback: Optional[Callable[["Sprite", AnimContext], None]] = None
        self.callback6: Optional[Callable[["Sprite", AnimContext], None]] = None
        self.tag = tag
        self.subpriority = subpriority
        self.age = 0             # frames lived (for fallback lifetime + frame cycle)
        self.frame_advance = 0   # how many sheet-frames the sprite has advanced
        self.flip = False        # horizontal flip (set by the renderer's side rule)

    @property
    def render_x(self) -> int:
        return self.x + self.x2

    @property
    def render_y(self) -> int:
        return self.y + self.y2

    def step(self, ctx: AnimContext) -> None:
        """Run one frame: invoke the current callback, then age."""
        if self.callback is not None:
            self.callback(self, ctx)
        self.age += 1


# ─────────────────────────────────────────── shared position helpers ──

def _x_offset_dir(ctx: AnimContext) -> int:
    """``+offset`` points from attacker toward target in x
    (SetAnimSpriteInitialXOffset)."""
    if ctx.target.x > ctx.attacker.x:
        return 1
    if ctx.target.x < ctx.attacker.x:
        return -1
    return 1 if ctx.attacker.side == SIDE_PLAYER else -1


def init_pos_to_attacker(s: Sprite, ctx: AnimContext) -> None:
    s.x = ctx.attacker.x + _x_offset_dir(ctx) * ctx.arg(0)
    s.y = ctx.attacker.y + ctx.arg(1)


def init_pos_to_target(s: Sprite, ctx: AnimContext) -> None:
    s.x = ctx.target.x + _x_offset_dir(ctx) * ctx.arg(0)
    s.y = ctx.target.y + ctx.arg(1)


# ──────────────────────────────────────── shared motion primitives ──
# data[] convention (read from battle_anim_mons.c):
#   data[0] = frame count remaining (sTransl_Speed on setup)
#   data[1] = InitX → then xDelta (fixed-point, low bit = sign)
#   data[2] = DestX → then yDelta
#   data[3] = InitY → then x accumulator
#   data[4] = DestY → then y accumulator

def _init_linear_translation(s: Sprite) -> None:
    dx = s.data[2] - s.data[1]      # DestX - InitX
    dy = s.data[4] - s.data[3]      # DestY - InitY
    speed = s.data[0] or 1
    x_delta = (abs(dx) << 8) // speed
    y_delta = (abs(dy) << 8) // speed
    x_delta = (x_delta | 1) if dx < 0 else (x_delta & ~1)
    y_delta = (y_delta | 1) if dy < 0 else (y_delta & ~1)
    s.data[1] = x_delta
    s.data[2] = y_delta
    s.data[3] = 0
    s.data[4] = 0


def _anim_translate_linear(s: Sprite) -> bool:
    """Per-frame linear move (writes x2/y2).  Returns True when finished."""
    if not s.data[0]:
        return True
    v1 = s.data[1]
    v2 = s.data[2]
    x = (s.data[3] + v1) & 0xFFFF
    y = (s.data[4] + v2) & 0xFFFF
    s.x2 = -(x >> 8) if (v1 & 1) else (x >> 8)
    s.y2 = -(y >> 8) if (v2 & 1) else (y >> 8)
    s.data[3] = x
    s.data[4] = y
    s.data[0] -= 1
    return False


def _translate_linear_with_followup(s: Sprite, ctx: AnimContext) -> None:
    if _anim_translate_linear(s):
        # arrived → run the stored followup (usually destroy)
        cb6, s.callback6 = s.callback6, None
        s.callback = cb6
        if cb6 is not None:
            cb6(s, ctx)


def _start_linear_translation(s: Sprite, ctx: AnimContext) -> None:
    s.data[1] = s.x          # InitX
    s.data[3] = s.y          # InitY
    _init_linear_translation(s)
    s.callback = _translate_linear_with_followup
    s.callback(s, ctx)


def _wait_for_duration(s: Sprite, ctx: AnimContext) -> None:
    if s.data[0] > 0:
        s.data[0] -= 1
    else:
        cb6, s.callback6 = s.callback6, None
        s.callback = cb6
        if cb6 is not None:
            cb6(s, ctx)


def _destroy(s: Sprite, ctx: AnimContext) -> None:
    s.alive = False


# ───────────────────────────────────────────── ported init callbacks ──
# Each takes (sprite, ctx) and sets the sprite's start position + data[] +
# the callback that drives it.  Faithful ports of the real C callbacks.

def _cb_translate_to_target(s: Sprite, ctx: AnimContext) -> None:
    """TranslateAnimSpriteToTargetMonLocation (Ember & many): start at the
    attacker, fly to the target over arg4 frames, then destroy."""
    init_pos_to_attacker(s, ctx)
    xdir = _x_offset_dir(ctx)
    s.data[0] = max(1, ctx.arg(4))
    s.data[2] = ctx.target.x + xdir * ctx.arg(2)   # DestX
    s.data[4] = ctx.target.y + ctx.arg(3)          # DestY
    s.callback6 = _destroy
    _start_linear_translation(s, ctx)


def _cb_on_mon_pos(s: Sprite, ctx: AnimContext) -> None:
    """AnimSpriteOnMonPos / AnimHitSplatBasic: sit on attacker (arg2==0) or
    target, play frames, destroy after a short duration."""
    if ctx.arg(2) == 0:
        init_pos_to_attacker(s, ctx)
    else:
        init_pos_to_target(s, ctx)
    s.data[0] = 24
    s.callback6 = _destroy
    s.callback = _wait_for_duration


def _cb_curse_nail(s: Sprite, ctx: AnimContext) -> None:
    """AnimCurseNail: at the attacker (+24 out), H-flip handled by the UI;
    drifts x2 while advancing 3 frames, then fades out + destroys."""
    init_pos_to_attacker(s, ctx)
    if ctx.attacker.side == SIDE_PLAYER:
        s.x += 24
        s.data[1] = -2
    else:
        s.x += -24
        s.data[1] = 2
    s.data[0] = 60
    s.callback = _cb_curse_nail_step1


def _cb_curse_nail_step1(s: Sprite, ctx: AnimContext) -> None:
    if s.data[0] > 0:
        s.data[0] -= 1
        return
    s.x2 += s.data[1]
    if s.x2 + 7 > 14 or s.x2 + 7 < 0:
        s.x += s.x2
        s.x2 = 0
        s.tile += 8
        s.frame_advance += 1
        s.data[2] += 1
        if s.data[2] == 3:
            s.data[0] = 30
            s.callback = _wait_for_duration
            s.callback6 = _cb_curse_nail_fade
        else:
            s.data[0] = 40


def _cb_curse_nail_fade(s: Sprite, ctx: AnimContext) -> None:
    # The real sprite blends out over ~16*3 frames; approximate as a brief
    # hold then destroy (the blend isn't reproduced).
    s.data[0] = 24
    s.callback6 = _destroy
    s.callback = _wait_for_duration


def _cb_ghost_status(s: Sprite, ctx: AnimContext) -> None:
    """AnimGhostStatusSprite: created at the target; wobbles via Sin while
    RISING (y2 grows negative), fades, then destroys.  This is the upward
    motion the static model was missing."""
    init_pos_to_target(s, ctx)
    s.callback = _cb_ghost_status_step


def _cb_ghost_status_step(s: Sprite, ctx: AnimContext) -> None:
    s.x2 = gba_sin(s.data[0], 12)
    if ctx.attacker.side != SIDE_PLAYER:
        s.x2 = -s.x2
    s.data[0] = (s.data[0] + 6) & 0xFF
    s.data[1] += 0x100
    s.y2 = -(s.data[1] >> 8)          # rises
    s.data[7] += 1
    if s.data[7] > 60:                # fade window elapsed → vanish
        s.invisible = True
        s.alive = False


# Registry: callback symbol → ported init function.
CALLBACKS: Dict[str, Callable[[Sprite, AnimContext], None]] = {
    "TranslateAnimSpriteToTargetMonLocation": _cb_translate_to_target,
    "AnimSpriteOnMonPos": _cb_on_mon_pos,
    "AnimHitSplatBasic": _cb_on_mon_pos,
    "AnimCurseNail": _cb_curse_nail,
    "AnimGhostStatusSprite": _cb_ghost_status,
}


def is_ported(callback: str) -> bool:
    return callback in CALLBACKS


def spawn(callback: str, ctx: AnimContext, *, tag: str = "",
          subpriority: int = 0,
          fallback_battler_is_attacker: bool = False,
          fallback_lifetime: int = 48) -> Optional[Sprite]:
    """Create + initialise a sprite for the given callback.

    Ported callbacks run faithfully.  Unported ones get a generic sprite
    that sits at the declared battler for ``fallback_lifetime`` frames then
    destroys, so the long tail still shows + clears rather than vanishing or
    piling up.  Returns the live Sprite (already initialised one frame)."""
    s = Sprite(tag=tag, subpriority=subpriority)
    init = CALLBACKS.get(callback)
    if init is not None:
        init(s, ctx)
    else:
        if fallback_battler_is_attacker:
            init_pos_to_attacker(s, ctx)
        else:
            init_pos_to_target(s, ctx)
        s.data[0] = fallback_lifetime
        s.callback6 = _destroy
        s.callback = _wait_for_duration
    return s


class AnimSim:
    """Holds the live sprites for a playing animation + advances them."""

    def __init__(self, ctx: AnimContext):
        self.ctx = ctx
        self.sprites: List[Sprite] = []

    def add(self, sprite: Optional[Sprite]) -> None:
        if sprite is not None and sprite.alive:
            self.sprites.append(sprite)

    def step(self) -> None:
        """Advance every live sprite one frame; drop the dead."""
        for s in self.sprites:
            if s.alive:
                s.step(self.ctx)
        self.sprites = [s for s in self.sprites if s.alive and not s.invisible]

    def active(self) -> bool:
        return any(s.alive for s in self.sprites)
