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

#endif /* PORYSUITE_HOST_PRE_H */
