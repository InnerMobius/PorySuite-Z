#!/usr/bin/env bash
# Build anim_engine_reactor.wasm from a pokefirered project.
#   usage: enginehost/build_wasm.sh [PROJECT_DIR] [OUT_DIR]
# Compiles pokefirered's animation subsystem + the host harness to wasm32 and
# links a reactor module the Python driver (core/battle_anim_engine.py) loads.
# Run from the porysuite repo root.
set -e

PROJ="${1:-pokefirered}"
OUT="${2:-enginehost/buildwasm}"
CLANG="$(ls /c/GBA/tools/wasi-sdk-*/bin/clang.exe 2>/dev/null | sort -V | tail -1)"
[ -z "$CLANG" ] && { echo "ERROR: wasi-sdk clang not found under /c/GBA/tools"; exit 1; }

INC="-I $PROJ/include -I $PROJ/gflib -I $PROJ/include/gba"
PRE="-include enginehost/host_pre.h"
# -DUBFIX: enable the decomp's own undefined-behaviour guards (notably the
# guarded SAFE_DIV). On real GBA hardware divide-by-zero returns garbage without
# trapping; wasm TRAPS on it (e.g. AnimTask_GrowAndShrink -> SAFE_DIV by a 0
# y-scale matrix). UBFIX makes those divides return 0 instead of trapping.
CFLAGS="--target=wasm32-wasi -O1 -std=gnu11 -w -DUBFIX \
  -Wno-implicit-function-declaration -Wno-implicit-int -Wno-incompatible-pointer-types \
  -Wno-int-conversion -Wno-int-to-pointer-cast -Wno-pointer-to-int-cast"

mkdir -p "$OUT"

# 1) name->pointer table + names.json from the project's templates/tasks.
python enginehost/gen_tables.py "$PROJ" "$OUT"

# 2) compile the full animation subsystem + engine + harness.
SRC_PATHS=$(ls "$PROJ"/src/battle_anim*.c "$PROJ"/src/sprite.c "$PROJ"/src/task.c "$PROJ"/src/trig.c "$PROJ"/src/util.c)
for path in $SRC_PATHS; do
  f=$(basename "$path" .c)
  EXTRA=""
  # The project's RunAffineAnimFromTaskData hardcodes an 8-byte affine-cmd
  # stride (`data[7] << 3`) — an agbcc-ABI assumption that reads garbage on
  # clang's 6-byte union (Bulk Up etc.). Compile its definition under a renamed
  # symbol so it's unused; stub_engine.c supplies an ABI-correct one that all
  # callers link against. (--wrap fails to redirect at -O1.)
  if [ "$f" = "battle_anim_mons" ]; then
    # Also rename SetGreyscaleOrOriginalPalette: the real one greyscales the
    # palette buffer (empty in the host); stub_engine.c supplies a version that
    # RECORDS a per-slot grey flag so the renderer can desaturate (Perish Song).
    # CreateAdditionalMonSpriteForMoveAnim loads a mon's pic into VRAM (faults
    # under wasm) which traps Role Play's silhouette task → banned + never runs;
    # the host version makes a placeholder sprite so the task runs (the renderer
    # draws the target mon's pic onto it).
    EXTRA="-DRunAffineAnimFromTaskData=RunAffineAnimFromTaskData_ORIG \
      -DSetGreyscaleOrOriginalPalette=SetGreyscaleOrOriginalPalette_ORIG \
      -DCreateAdditionalMonSpriteForMoveAnim=CreateAdditionalMonSpriteForMoveAnim_ORIG"
  fi
  # MoveBattlerSpriteToBG copies a mon onto a BG layer via hardware-address VRAM
  # fills (BG_SCREEN_ADDR/BG_PLTT) that fault under wasm — trapping the wall moves
  # (Barrier/Light Screen/Reflect/Mirror Coat/Magic Coat) + dark moves. Rename its
  # definition and let stub_engine.c supply a host version that skips the VRAM
  # copy but records which BG layer + base scroll the mon was copied to, so the
  # renderer can draw the Memento/Role Play soul-shadow ghost.
  if [ "$f" = "battle_anim" ]; then
    EXTRA="-DMoveBattlerSpriteToBG=MoveBattlerSpriteToBG_ORIG"
  fi
  # The headless engine never loads sprite SHEETS (loadspritegfx is a no-op), so
  # GetSpriteTileStartByTag returns 0xFFFF (not-found) for every tag. Anims that
  # HIDE a sprite when its sheet is "missing" — e.g. Status_Freeze's ice cube does
  # `if (GetSpriteTileStartByTag(ANIM_TAG_ICE_CUBE) == 0xFFFF) invisible = TRUE` —
  # then render NOTHING. Rename sprite.c's real one; stub_engine.c supplies a host
  # version that reports a valid tile start so those visibility guards pass (the
  # renderer draws the sprite from the project PNG by tag, so zeroed tiles are
  # harmless). sprite.c's own callers still use the real _ORIG.
  if [ "$f" = "sprite" ]; then
    EXTRA="-DGetSpriteTileStartByTag=GetSpriteTileStartByTag_ORIG"
  fi
  "$CLANG" -c $CFLAGS $EXTRA $PRE $INC "$path" -o "$OUT/$f.o"
done
for s in stub_engine stub_gfx_data driver; do
  "$CLANG" -c $CFLAGS $PRE $INC "enginehost/$s.c" -o "$OUT/$s.o"
done
"$CLANG" -c $CFLAGS $PRE $INC "$OUT/gen_tables.c" -o "$OUT/gen_tables.o"

# 3) link the reactor module (exported functions the Python driver calls).
EXP="-Wl,--export=engine_reset,--export=engine_set_arg,--export=engine_create_sprite,\
--export=engine_create_task,--export=engine_step,--export=engine_busy,\
--export=engine_snapshot,--export=engine_snapshot_addr,--export=engine_snap_stride,\
--export=engine_bg_scroll,--export=engine_dbg,--export=engine_bg2_scroll,\
--export=engine_scanline_addr,--export=engine_scanline_state,--export=engine_win0h,\
--export=engine_pltt_addr,--export=engine_bg_pltt_index,--export=engine_bg_screen_size,\
--export=engine_monbg,--export=engine_mon_fx,--export=engine_bg3_scroll,\
--export=engine_coord_offset,--export=engine_subsprites,--export=engine_subsprites_addr"
"$CLANG" --target=wasm32-wasi -mexec-model=reactor $EXP "$OUT"/*.o -lm \
  -o "$OUT/anim_engine_reactor.wasm"

# 4) Clamp trapping ops to GBA semantics. The real game code has integer
# divides/moduli the GBA tolerates (ARM __aeabi_idiv0 returns 0) but wasm TRAPS
# on divide-by-zero — e.g. AnimSparkElectricityFlashing_Step does `data[7] % 0`
# (Spark passes 0 for the flicker divisor). --trap-mode-clamp rewrites div/mod/
# float->int so a zero divisor yields 0 instead of trapping, so those animations
# run instead of being banned by the host's trap-fallback. Optional: without
# binaryen the wasm still works; the affected divide-by-zero moves just fall back
# to the trap-skip path (their sprite is dropped). NOTE: dist is this CLAMPED
# output — `md5sum` of dist must match buildwasm AFTER this step, not before.
WASMOPT="$(ls /c/GBA/tools/binaryen-*/bin/wasm-opt.exe 2>/dev/null | sort -V | tail -1)"
if [ -n "$WASMOPT" ]; then
  "$WASMOPT" --all-features --trap-mode-clamp "$OUT/anim_engine_reactor.wasm" \
    -o "$OUT/anim_engine_reactor.wasm.tmp" \
    && mv "$OUT/anim_engine_reactor.wasm.tmp" "$OUT/anim_engine_reactor.wasm"
  echo "clamped trapping ops (binaryen wasm-opt $("$WASMOPT" --version 2>/dev/null | head -1))"
else
  echo "WARN: wasm-opt (binaryen) not found under /c/GBA/tools — skipping trap-mode clamp;"
  echo "      divide-by-zero moves (e.g. Spark) will use the trap-skip fallback."
fi

echo "OK -> $OUT/anim_engine_reactor.wasm ($(stat -c%s "$OUT/anim_engine_reactor.wasm" 2>/dev/null || echo '?') bytes)"
