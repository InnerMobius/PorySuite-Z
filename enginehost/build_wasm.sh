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
CFLAGS="--target=wasm32-wasi -O1 -std=gnu11 -w \
  -Wno-implicit-function-declaration -Wno-implicit-int -Wno-incompatible-pointer-types \
  -Wno-int-conversion -Wno-int-to-pointer-cast -Wno-pointer-to-int-cast"

mkdir -p "$OUT"

# 1) name->pointer table + names.json from the project's templates/tasks.
python enginehost/gen_tables.py "$PROJ" "$OUT"

# 2) compile the full animation subsystem + engine + harness.
SRC_PATHS=$(ls "$PROJ"/src/battle_anim*.c "$PROJ"/src/sprite.c "$PROJ"/src/task.c "$PROJ"/src/trig.c "$PROJ"/src/util.c)
for path in $SRC_PATHS; do
  f=$(basename "$path" .c)
  "$CLANG" -c $CFLAGS $PRE $INC "$path" -o "$OUT/$f.o"
done
for s in stub_engine stub_gfx_data driver; do
  "$CLANG" -c $CFLAGS $PRE $INC "enginehost/$s.c" -o "$OUT/$s.o"
done
"$CLANG" -c $CFLAGS $PRE $INC "$OUT/gen_tables.c" -o "$OUT/gen_tables.o"

# 3) link the reactor module (exported functions the Python driver calls).
EXP="-Wl,--export=engine_reset,--export=engine_set_arg,--export=engine_create_sprite,\
--export=engine_create_task,--export=engine_step,--export=engine_busy,\
--export=engine_snapshot,--export=engine_snapshot_addr,--export=engine_snap_stride"
"$CLANG" --target=wasm32-wasi -mexec-model=reactor $EXP "$OUT"/*.o -lm \
  -o "$OUT/anim_engine_reactor.wasm"

echo "OK -> $OUT/anim_engine_reactor.wasm ($(stat -c%s "$OUT/anim_engine_reactor.wasm" 2>/dev/null || echo '?') bytes)"
