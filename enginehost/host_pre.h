/* host_pre.h — force-included before every pokefirered source file when
 * building the headless animation engine on the host (MinGW/gcc).
 *
 * The decomp normally runs tools/preproc over each .c to turn _("...") text
 * macros and INCBIN(...) binary includes into real data BEFORE gcc sees them.
 * We bypass preproc (we only need motion logic, not text/graphics bytes), so
 * we provide the "modern compiler" fallbacks here. global.h only defines these
 * inside an #if defined(__APPLE__)||__CYGWIN__||__INTELLISENSE__ branch that is
 * inactive on MinGW, so our definitions persist with no redefinition conflict.
 *
 * NOTE: _(x) expands to x (NOT (x)) so that `u8 arr[] = _("string")` stays a
 * valid string-literal array initializer. INCBIN(...) -> {0} yields a valid
 * (dummy) array; the actual graphics bytes are never needed for motion.
 *
 * This file lives in the TOOL, never inside pokefirered/. No game source is
 * modified — the .c files are compiled in place with this force-include.
 */
#ifndef PORYSUITE_HOST_PRE_H
#define PORYSUITE_HOST_PRE_H

/* The decomp relies on freestanding builtins (abs, memcpy, etc.) without always
 * including the standard headers. Modern gcc (15+) makes an implicit abs() a
 * hard error, so declare the standard library up front. */
#include <stdlib.h>
#include <string.h>

#define _(x) x
#define __(x) x
#define INCBIN(...) {0}
#define INCBIN_U8 INCBIN
#define INCBIN_U16 INCBIN
#define INCBIN_U32 INCBIN
#define INCBIN_S8 INCBIN
#define INCBIN_S16 INCBIN
#define INCBIN_S32 INCBIN

/* 64K-align the sprite coord-offset globals (the GBA screen/terrain SHAKE that
 * Metal Claw / Dragon Claw / etc. drive). AnimShakeMonOrBattleTerrain stores
 * &gSpriteCoordOffset split across two s16 sprite-data fields and rebuilds it as
 * data[6] | (data[7] << 16); if the address's low half has bit 15 set it
 * sign-extends to a bogus 0xFFFFxxxx pointer (the same trap class fixed for
 * gBattle_BG3_X/Y). Forcing 64K alignment makes the low half 0 so the rebuild is
 * exact and the shake reads/writes the REAL global. This force-include is seen
 * before sprite.c's definition, so the definition inherits the alignment; every
 * other file just references the now-aligned symbol. (s16 == signed short.) */
extern short gSpriteCoordOffsetX __attribute__((aligned(0x10000)));
extern short gSpriteCoordOffsetY __attribute__((aligned(0x10000)));

#endif /* PORYSUITE_HOST_PRE_H */
