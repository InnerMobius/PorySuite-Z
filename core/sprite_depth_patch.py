"""Composite-sprite depth-sort engine fix for pokefirered.

The overworld layers sprites by their **feet** — the sprite whose feet
sit lower on screen draws in front.  ``SortSprites`` (``src/sprite.c``)
does this in two steps: a primary sort on ``subpriority`` (quantised to
16-pixel screen bands), then a tie-break for sprites that land in the
same band.

The bug is the tie-break: it compares ``sprite->oam.y`` — the sprite's
**top-left corner** — not its feet.  For a normal two-tile NPC the
corner sits right next to the feet, so the error is sub-pixel and never
shows.  For a tall composite sprite (an 8x8-base sprite driven by a
subsprite table — 48x80, 64x96, …) the corner is several tiles above
the feet, so the sprite is treated as if it were that far up the screen
and loses *every* tie — it draws behind the player across a whole tile
row.  Vanilla never hit this because its only composite object events
(the SS Anne, the truck) are placed where the player cannot walk behind
them.

The fix rewrites the tie-break to compare the sprites' **bottom edges**
(feet): ``oam.y - 2 * centerToCornerVecY``.  ``centerToCornerVecY`` is
``-(height >> 1)``, so doubling and negating it yields the full sprite
height; object-event sprites override ``centerToCornerVecY`` from their
GraphicsInfo ``.height``, so this picks up the true height even though a
composite's base OAM is only 8x8.  Same-size sprites are unaffected (a
constant offset cancels); only mixed-height ties change, and they now
resolve on true feet position.

This is a one-time, idempotent engine patch applied through the
PorySuite-Z patcher — never a hand-edit of pokefirered.  It is detected
by scanning the real source for the ``PORYSUITE-DEPTH`` signature, so a
restored or reimported pokefirered folder is re-detected correctly.

See ``docs/BUGS.md`` (composite depth-sort entry) and
``docs/OVERWORLD_EDITOR_UPGRADE_PLAN.md``.
"""

from __future__ import annotations

import os
from dataclasses import dataclass

# src/sprite.c, relative to the project root.
_SPRITE_C_REL = ("src", "sprite.c")

# Unique signature proving the fix is present in the source.
_SIGNATURE = "PORYSUITE-DEPTH"

# The exact vanilla ``SortSprites`` tie-break — the three lines to
# replace.  Indentation is byte-exact against pokefirered's sprite.c.
_VANILLA = (
    "        while (j > 0\n"
    "            && ((sprite1Priority > sprite2Priority)\n"
    "             || (sprite1Priority == sprite2Priority"
    " && sprite1Y < sprite2Y)))"
)

# The patched tie-break: compare feet (oam.y + height, expressed as
# ``-2 * centerToCornerVecY``) instead of the top corner ``oam.y``.
_PATCHED = (
    "        // PORYSUITE-DEPTH: break subpriority ties on the sprite's feet\n"
    "        // — oam.y minus 2*centerToCornerVecY is the bottom edge (full\n"
    "        // height) — instead of oam.y (the top corner), so a tall\n"
    "        // composite sprite layers against the player correctly instead\n"
    "        // of losing every tie.  Same-size sprites are unaffected.\n"
    "        while (j > 0\n"
    "            && ((sprite1Priority > sprite2Priority)\n"
    "             || (sprite1Priority == sprite2Priority\n"
    "                 && sprite1Y - 2 * sprite1->centerToCornerVecY\n"
    "                  < sprite2Y - 2 * sprite2->centerToCornerVecY)))"
)


@dataclass
class DepthPatchResult:
    """Outcome of an :func:`ensure_sprite_depth_fix` call.

    ``changed`` is True only when the file was actually rewritten.
    ``ok`` is True when the source ends up correct (already patched, or
    just patched).  ``ok`` is False only when the patch could not be
    applied — ``sprite.c`` missing, or its ``SortSprites`` tie-break not
    matching the vanilla shape (a hand-modified engine).  ``detail`` is a
    one-line plain-English summary for the caller's user-facing log.
    """
    changed: bool
    ok: bool
    detail: str


# ── file IO ─────────────────────────────────────────────────────────────

def _read(path: str) -> str:
    with open(path, encoding="utf-8", errors="replace") as f:
        return f.read()


def _atomic_write(path: str, text: str) -> None:
    """Write ``text`` to ``path`` atomically via temp + rename.

    A half-written engine source file would break the build, so the new
    content is staged in a sibling ``.tmp`` and swapped in with
    ``os.replace`` (atomic on Win32 and POSIX).
    """
    tmp = path + ".tmp"
    try:
        with open(tmp, "w", encoding="utf-8", newline="\n") as f:
            f.write(text)
            f.flush()
            try:
                os.fsync(f.fileno())
            except OSError:
                pass
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            try:
                os.remove(tmp)
            except OSError:
                pass


# ── pure text transform (unit-testable without a project) ───────────────

def _is_applied_text(text: str) -> bool:
    """True when ``text`` already carries the depth fix."""
    return _SIGNATURE in text


def _apply_to_text(text: str):
    """Return ``(new_text, changed, ok)`` for one ``sprite.c`` body.

    * already patched → ``(text, False, True)``
    * vanilla tie-break found → ``(patched, True, True)``
    * neither → ``(text, False, False)`` — engine is hand-modified.
    """
    if _SIGNATURE in text:
        return text, False, True
    if _VANILLA not in text:
        return text, False, False
    return text.replace(_VANILLA, _PATCHED, 1), True, True


# ── project-level entry points ──────────────────────────────────────────

def is_sprite_depth_fix_applied(project_root: str) -> bool:
    """True when ``src/sprite.c`` carries the composite-depth fix."""
    path = os.path.join(project_root, *_SPRITE_C_REL)
    try:
        return _is_applied_text(_read(path))
    except OSError:
        return False


def ensure_sprite_depth_fix(project_root: str) -> DepthPatchResult:
    """Apply the composite-sprite depth fix to ``src/sprite.c``.

    Idempotent: a no-op once the fix is present.  Safe to call on every
    overworld-sprite creation — it rewrites the file at most once, the
    first time.
    """
    path = os.path.join(project_root, *_SPRITE_C_REL)
    if not os.path.isfile(path):
        return DepthPatchResult(
            False, False, "src/sprite.c not found — depth fix skipped",
        )

    try:
        text = _read(path)
    except OSError as e:
        return DepthPatchResult(
            False, False, f"could not read src/sprite.c: {e}",
        )

    new_text, changed, ok = _apply_to_text(text)
    if not ok:
        return DepthPatchResult(
            False, False,
            "SortSprites tie-break not found in src/sprite.c — the engine "
            "looks hand-modified; depth fix not applied",
        )
    if not changed:
        return DepthPatchResult(
            False, True, "composite-sprite depth fix already applied",
        )

    try:
        _atomic_write(path, new_text)
    except OSError as e:
        return DepthPatchResult(
            False, False, f"could not write src/sprite.c: {e}",
        )
    return DepthPatchResult(
        True, True,
        "Applied composite-sprite depth fix to src/sprite.c "
        "(SortSprites tie-break now compares feet)",
    )
