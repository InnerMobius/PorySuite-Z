/* stub_engine.c — host harness for the headless battle-animation engine.
 *
 * The pokefirered animation C (sprite.c, task.c, battle_anim*.c) computes all
 * motion on plain struct fields; it only touches GBA hardware (OAM/DMA/GPU regs)
 * at the final copy-to-screen stage, which we never call. This file provides
 * host definitions for the ~105 symbols the anim code references but that live
 * in engine files we don't compile:
 *
 *   - Real engine GLOBALS (correct types) the anim code reads/writes.
 *   - REAL implementations of the math that affects motion: ObjAffineSet
 *     (affine scale/rotate for Fly/Bulk Up), ArcTan2, Q_8_8_*, CpuSet, Random,
 *     AllocZeroed/Free.
 *   - NO-OP stubs for everything cosmetic to motion: sound, GPU registers,
 *     palette blends, BG tilemaps, healthbox, scanline, mon-gfx VRAM loaders.
 *
 * Nothing in pokefirered/ is modified — its .c files compile in place with a
 * force-included host_pre.h. This file lives entirely in the tool.
 */

#include "global.h"
#include "gflib.h"
#include "battle.h"
#include "battle_anim.h"
#include "task.h"
#include "scanline_effect.h"
#include "main.h"
#include "sound.h"
#include "m4a.h"
#include "malloc.h"
#include "trig.h"
#include "decompress.h"
#include "pokemon.h"

/* ───────────────────────── engine globals ───────────────────────── */

struct Main gMain;
struct PaletteFadeControl gPaletteFade;
struct ScanlineEffect gScanlineEffect;
u16 gScanlineEffectRegBuffers[2][0x3C0];
u16 gPlttBufferFaded[PLTT_BUFFER_SIZE];
u16 gPlttBufferUnfaded[PLTT_BUFFER_SIZE];

u8 gBattlerSpriteIds[MAX_BATTLERS_COUNT];
u8 gBattlerPositions[MAX_BATTLERS_COUNT];
u8 gBattlerAttacker;
u8 gBattlerTarget;
u8 gBattlersCount;
u32 gBattleTypeFlags;
u8 gBattleTerrain;
u8 gBattleMonForms[MAX_BATTLERS_COUNT];
u16 gBattlerPartyIndexes[MAX_BATTLERS_COUNT];
u32 gTransformedPersonalities[MAX_BATTLERS_COUNT];
u8 gEffectBattler;
u8 gHealthboxSpriteIds[MAX_BATTLERS_COUNT];

/* Per-species coord tables. The .size byte (width:4 hi, height:4 lo, ×8) drives
 * GetBattlerSpriteCoordAttr's LEFT/RIGHT/TOP/BOTTOM = X±width/2, Y±height/2.
 * Zeroed → every edge collapses to the mon's centre, so effects positioned by
 * the mon's bounding box (Foresight's magnifier scanning the target, Metronome's
 * finger beside the head, many others) barely move. Default every species to a
 * full 64×64 box (size 0x88) so those bounds spread correctly. y_offset stays 0
 * so it doesn't shift the base coords. */
const struct MonCoords gMonFrontPicCoords[NUM_SPECIES + 1] = {
    [0 ... NUM_SPECIES] = { .size = 0x88, .y_offset = 0 } };
const struct MonCoords gMonBackPicCoords[NUM_SPECIES + 1] = {
    [0 ... NUM_SPECIES] = { .size = 0x88, .y_offset = 0 } };
const u8 gEnemyMonElevation[NUM_SPECIES] = {0};

/* Tables we never traverse in the motion path (we drive createsprite via API,
 * not the move→script lookup). Dummy single entries keep them valid symbols. */
const struct CompressedSpriteSheet gMonFrontPicTable[1];
const struct CompressedSpriteSheet gMonBackPicTable[1];
const u8 *const gBattleAnims_Moves[1] = {0};
const u16 gMovesWithQuietBGM[1] = {0};

struct Pokemon gPlayerParty[PARTY_SIZE];
struct Pokemon gEnemyParty[PARTY_SIZE];

/* Pointers the anim code may dereference — back them with static structs so a
 * deref can't segfault (fields are zeroed). */
static struct MonSpritesGfx sMonSpritesGfx;
struct MonSpritesGfx *gMonSpritesGfxPtr = &sMonSpritesGfx;

/* Back every pointer field of gBattleSpritesDataPtr with a real array — the
 * coord/transform paths index battlerData[battler] etc., so NULL would crash. */
static struct BattleSpriteInfo sBattlerData[MAX_BATTLERS_COUNT];
static struct BattleHealthboxInfo sHealthBoxesData[MAX_BATTLERS_COUNT];
static struct BattleAnimationInfo sAnimationData[MAX_BATTLERS_COUNT];
static struct BattleBarInfo sBattleBars[MAX_BATTLERS_COUNT];
static struct BattleSpriteData sBattleSpritesData = {
    .battlerData = sBattlerData,
    .healthBoxesData = sHealthBoxesData,
    .animationData = sAnimationData,
    .battleBars = sBattleBars,
};
struct BattleSpriteData *gBattleSpritesDataPtr = &sBattleSpritesData;

/* BG scroll / window regs the BG-based moves write (Surf, Dig). Plain globals;
 * the driver reads them out for BG moves. */
u16 gBattle_BG1_X, gBattle_BG1_Y;
u16 gBattle_BG2_X, gBattle_BG2_Y;
/* 64K-aligned so their wasm addresses have a ZERO low half (bit 15 clear).
 * AnimShakeMonOrBattleTerrain (Rock Throw / Magnitude / Earthquake-likes) stores
 * &gBattle_BG3_X split across two s16 sprite-data fields and rebuilds it as
 * data[6] | (data[7] << 16) — a GBA-era trick. If the address's low half has bit
 * 15 set, the s16 data[6] sign-extends to a bad pointer and the deref FAULTS, so
 * the shake sprite traps and gets banned. Forcing the low half to 0 dodges the
 * sign-extension so the screen/terrain-shake actually runs. */
u16 __attribute__((aligned(0x10000))) gBattle_BG3_X;
u16 __attribute__((aligned(0x10000))) gBattle_BG3_Y;
u16 gBattle_WIN0H, gBattle_WIN0V, gBattle_WIN1H, gBattle_WIN1V;

struct MusicPlayerInfo gMPlayInfo_BGM;
struct MusicPlayerInfo gMPlayInfo_SE1;
struct MusicPlayerInfo gMPlayInfo_SE2;

/* ───────────────────────── real motion math ─────────────────────── */

/* OAM affine matrix from (xScale, yScale, rotation) — the same construction the
 * GBA BIOS ObjAffineSet performs, in 8.8 fixed-point against gSineTable (whose
 * amplitude is 256). Drives Fly's stretch + Bulk Up's grow. Callers (e.g.
 * SetSpriteRotScale) pass a contiguous struct OamMatrix dest; we ignore the OAM
 * interleave `offset` since count is 1 for sprite rot/scale. */
void ObjAffineSet(struct ObjAffineSrcData *src, void *dest, s32 count, s32 offset)
{
    struct OamMatrix *mat = (struct OamMatrix *)dest;
    s32 i;
    for (i = 0; i < count; i++)
    {
        u8 idx = (u8)((src[i].rotation >> 8) & 0xFF);
        s32 sinv = gSineTable[idx];
        s32 cosv = gSineTable[(idx + 64) & 0xFF];
        s32 xs = src[i].xScale;
        s32 ys = src[i].yScale;
        mat[i].a =  (s16)((xs * cosv) >> 8);
        mat[i].b =  (s16)(-((xs * sinv) >> 8));
        mat[i].c =  (s16)((ys * sinv) >> 8);
        mat[i].d =  (s16)((ys * cosv) >> 8);
    }
    (void)offset;
}

void BgAffineSet(struct BgAffineSrcData *src, struct BgAffineDstData *dest, s32 count)
{
    s32 i;
    for (i = 0; i < count; i++)
    {
        dest[i].pa = 256; dest[i].pb = 0; dest[i].pc = 0; dest[i].pd = 256;
        dest[i].dx = 0; dest[i].dy = 0;
    }
}

void CpuSet(const void *src, void *dest, u32 control)
{
    u32 count = control & 0x1FFFFF;
    if (control & (1 << 24)) /* CPU_SET_SRC_FIXED → fill */
    {
        if (control & (1 << 26)) { u32 v = *(const u32 *)src; u32 *d = dest; while (count--) *d++ = v; }
        else                     { u16 v = *(const u16 *)src; u16 *d = dest; while (count--) *d++ = v; }
    }
    else
    {
        if (control & (1 << 26)) { const u32 *s = src; u32 *d = dest; while (count--) *d++ = *s++; }
        else                     { const u16 *s = src; u16 *d = dest; while (count--) *d++ = *s++; }
    }
}

#include <math.h>
u16 ArcTan2(s16 x, s16 y)
{
    double a;
    if (x == 0 && y == 0) return 0;
    a = atan2((double)y, (double)x);          /* -pi..pi */
    return (u16)((s32)(a * 32768.0 / M_PI) & 0xFFFF);
}

s16 Q_8_8_mul(s16 x, s16 y) { return (s16)(((s32)x * (s32)y) >> 8); }
s16 Q_8_8_inv(s16 y)        { return y ? (s16)(((s32)0x100 << 8) / y) : 0; }

/* Deterministic LCG (the GBA's). The driver reseeds per animation so playback
 * is reproducible; the exact sequence need not match a live battle. */
static u32 sRng = 0x12345678;
u16 Random(void) { sRng = sRng * 1103515245 + 24013; return (u16)(sRng >> 16); }
void HostSeedRng(u32 seed) { sRng = seed; }

/* Arena allocator. wasi-libc's malloc/calloc spins forever under this reactor
 * module's heap setup (a calloc(0x2000) burned 400M wasm instructions and never
 * returned — it hung the whole app on any move that allocates: Heal Bell's
 * music-note palettes, Transform/Sketch's extra mon sprite, Ghost, ...). The
 * anim code only needs small, transient scratch buffers (multiUseBuffer is
 * alloc'd, used within one task, and freed), so a bump arena is sufficient and
 * can't loop. Each play is a fresh wasm instance, so sArena/sTop reset to 0
 * (BSS) per move; within a play we bump and wrap (live buffers are few and
 * short-lived). Free is a no-op — buffers are reclaimed in bulk per instance. */
static u8 sArena[512 * 1024];
static u32 sArenaTop;
void *AllocZeroed(u32 size)
{
    u32 n = (size + 7u) & ~7u;
    if (n == 0)
        n = 8;
    if (n > sizeof(sArena))
        n = sizeof(sArena);
    if (sArenaTop + n > sizeof(sArena))
        sArenaTop = 0;                 /* transient scratch: wrap the arena */
    {
        u8 *p = &sArena[sArenaTop];
        sArenaTop += n;
        memset(p, 0, n);
        return p;
    }
}
void Free(void *pointer) { (void)pointer; }

/* Report a live, valid mon so IsBattlerSpritePresent() returns TRUE — otherwise
 * mon-acting tasks (ShakeMon, the lunge) abort with SPRITE_NONE and the target
 * never reacts. Real party data isn't needed for motion; sane fields suffice. */
u32 GetMonData2(struct Pokemon *mon, s32 field)
{
    (void)mon;
    switch (field)
    {
    case 57: /* MON_DATA_HP      */
    case 58: /* MON_DATA_MAX_HP  */
        return 100;
    case 56: /* MON_DATA_LEVEL   */
        return 50;
    case 11: /* MON_DATA_SPECIES */
    case 12: /* MON_DATA_SPECIES2 */
    case 65: /* MON_DATA_SPECIES_OR_EGG */
        return 1;
    default:
        return 0;
    }
}

/* ───────────────────────── no-op stubs (cosmetic to motion) ─────── */

/* BLDALPHA top-layer (sprite/mon) blend coefficient, 0..16. A sprite whose OAM
 * objMode is ST_OAM_OBJ_BLEND is alpha-blended at this/16 — the engine's
 * `setalpha` opcode + every fade-to/from-invisible writes it. Default 16 =
 * opaque. Recorded here because SetGpuReg is otherwise a no-op. */
u8 gHostBldEva = 16;

/* ── BG-mon-copy state (Memento / Role Play "soul shadow") ───────────────────
 * MoveBattlerSpriteToBG copies a battler's mon onto a BG layer; the shadow tasks
 * then blacken it (FillPalette RGB_BLACK), stretch it via a per-scanline VOFS
 * buffer, and clip it with WIN0. The real VRAM copy faults under wasm, so we
 * record only what the renderer needs to rebuild the ghost: which BG layer each
 * battler was copied to (0 none / 1 BG1 / 2 BG2) and the base vertical scroll
 * captured at copy time (= the AnimTask data[10] the scanline stretch deviates
 * from). The scanline buffer + WIN0 + BLDALPHA are all computed by the tasks
 * running in-engine and read out via driver exports. */
u8  gHostMonBg[4];        /* per-battler: 0 none, 1 BG1, 2 BG2 */
s16 gHostMonBgBaseY[4];   /* base BGnVOFS at copy time (data[10]) */
u8  gHostShadowLayer;     /* BG layer (1/2) the ACTIVE scanline stretch drives —
                           * recorded from ScanlineEffect_SetParams' dmaDest so
                           * the renderer ties the current stretch to the one mon
                           * on that layer. Memento runs two shadows in sequence
                           * (attacker then target, on opposite layers); without
                           * this the idle mon would render the other's buffer. */
u8  gHostScanAxis;        /* axis of the active scanline effect: 0 none, 1 = HOFS
                           * (horizontal per-row warp — Extrasensory's psychic-BG
                           * distortion), 2 = VOFS (vertical — Memento's soul-
                           * shadow stretch). Lets the renderer pick H vs V. */
u8  gHostScanWide;        /* 1 = the scanline DMA is 32-bit (HOFS+VOFS interleaved
                           * per row — Acid Armor); 0 = 16-bit (one value per row
                           * — Dragon Dance, Extrasensory). The renderer reads
                           * buf[2*row] for HOFS when wide, buf[row] when not. */

/* REG_OFFSET_MOSAIC BG level (low byte: bits 0-3 H, 4-7 V). Transform's morph
 * (AnimTask_TransformMon) ramps this 0->15 to pixelate the mon, swaps its pic to
 * the target, then ramps back 15->0. The renderer pixelates the monbg'd mon by
 * this amount. */
u8 gHostMosaic = 0;
/* Set when HandleSpeciesGfxDataChange runs — Transform's mid-morph gfx swap: the
 * attacker's pic becomes the target's. The renderer swaps the displayed mon. */
u8 gHostMonSwapped = 0;

void SetGpuReg(u8 r, u16 v)
{
    if (r == 0x52)               /* REG_OFFSET_BLDALPHA: low 5 bits = EVA */
        gHostBldEva = (v & 0x1F);
    else if (r == 0x4c)          /* REG_OFFSET_MOSAIC: low byte = BG mosaic (H|V<<4) */
        gHostMosaic = (v & 0xFF);
}
void SetGpuRegBits(u8 r, u16 m) { (void)r; (void)m; }
void ClearGpuRegBits(u8 r, u16 m) { (void)r; (void)m; }
u16  GetGpuReg(u8 r) { (void)r; return 0; }

/* ── Per-palette-slot blend recording ─────────────────────────────────────
 * The host has no loaded palette DATA (no VRAM/gfx), so the real palette buffer
 * is meaningless. But every tint/flash/fade the game does funnels through these
 * blend calls as "blend palette slot N toward COLOR by COEFF/16". We RECORD that
 * per slot (32 slots: 0-15 BG, 16-31 OBJ). engine_snapshot stamps each sprite/mon
 * with its slot's (coeff,color); the Python renderer applies the SAME per-channel
 * blend math (out = base + ((target-base)*coeff>>4)) to the project's real palette.
 * So Double Team's dark blend, status tints, MetallicShine, BlendColorCycle, etc.
 * all tint correctly without the engine ever holding a real palette. */
u8  gHostPalBlendCoeff[32];   /* 0 = no tint, else 1..16 */
u16 gHostPalBlendColor[32];   /* BGR555 target */
u8  gHostPalGray[32];         /* 1 = slot greyscaled (Perish Song et al.) */

void HostResetPalBlend(void)
{
    int i;
    for (i = 0; i < 32; i++) {
        gHostPalBlendCoeff[i] = 0; gHostPalBlendColor[i] = 0; gHostPalGray[i] = 0;
    }
    gHostBldEva = 16;   /* opaque until a setalpha/fade changes it */
    gPaletteFade.active = 0;   /* no software fade in progress */
    for (i = 0; i < 4; i++) { gHostMonBg[i] = 0; gHostMonBgBaseY[i] = 0; }
    gScanlineEffect.state = 0;   /* no shadow scanline stretch in progress */
    gScanlineEffect.srcBuffer = 0;
    gHostShadowLayer = 0;
    gHostScanAxis = 0;
    gHostScanWide = 0;
    gHostMosaic = 0;        /* no mosaic pixelation in progress (Transform) */
    gHostMonSwapped = 0;    /* attacker's pic not yet morphed to the target's */
}

/* Record a per-slot greyscale flag. The real one (battle_anim_mons.c) averages
 * the palette in gPlttBufferUnfaded/Faded — which the host never populates, so
 * it does nothing. The build renames the project copy (-D…=_ORIG) so callers
 * (Perish Song's AnimTask_SetGrayscaleOrOriginalPal, etc.) link this; the
 * renderer desaturates any sprite/mon whose slot is flagged. paletteNum is the
 * full slot (caller passes oam.paletteNum + 16). */
void SetGreyscaleOrOriginalPalette(u16 paletteNum, bool8 restoreOriginalColor)
{
    if (paletteNum < 32)
        gHostPalGray[paletteNum] = restoreOriginalColor ? 0 : 1;
}

void BlendPalette(u16 palOffset, u16 numEntries, u8 coeff, u16 blendColor)
{
    /* Record for every 16-colour slot this range touches. */
    u16 first = palOffset >> 4;
    u16 last  = numEntries ? ((palOffset + numEntries - 1) >> 4) : first;
    u16 s;
    for (s = first; s <= last && s < 32; s++) {
        gHostPalBlendCoeff[s] = (coeff > 16) ? 16 : coeff;
        gHostPalBlendColor[s] = blendColor;
    }
}

void BlendPalettes(u32 selectedPalettes, u8 coeff, u16 color)
{
    u16 s;
    for (s = 0; s < 32; s++)
        if (selectedPalettes & (1u << s)) {
            gHostPalBlendCoeff[s] = (coeff > 16) ? 16 : coeff;
            gHostPalBlendColor[s] = color;
        }
}

void BlendPalettesUnfaded(u32 selectedPalettes, u8 coeff, u16 color)
{
    BlendPalettes(selectedPalettes, coeff, color);
}

void LoadPalette(const void *s, u16 o, u16 n) { (void)s;(void)o;(void)n; }
void LoadCompressedPalette(const u32 *s, u16 o, u16 n) { (void)s;(void)o;(void)n; }
void FillPalette(u16 v, u16 o, u16 n) { (void)v;(void)o;(void)n; }
void TintPlttBuffer(u32 a, s8 r, s8 g, s8 b) { (void)a;(void)r;(void)g;(void)b; }
void InvertPlttBuffer(u32 a) { (void)a; }
void UnfadePlttBuffer(u32 a) { (void)a; }
/* Software palette fade — BeginNormalPaletteFade fades the selected palette
 * slots from startY to targetY toward blendColor over time (FadeScreenToWhite,
 * Moonlight's end fade, fade-from-black intros). Faithful to palette.c: it sets
 * up gPaletteFade; UpdatePaletteFade (run each engine_step) ramps the coefficient
 * and records the per-slot blend via BlendPalette. Non-fade moves never set
 * gPaletteFade.active, so UpdatePaletteFade is a no-op for them. */
bool8 BeginNormalPaletteFade(u32 selectedPalettes, s8 delay, u8 startY,
                             u8 targetY, u16 blendColor)
{
    if (gPaletteFade.active)
        return FALSE;
    gPaletteFade.deltaY = 2;
    if (delay < 0) { gPaletteFade.deltaY += (delay * -1); delay = 0; }
    gPaletteFade_selectedPalettes = selectedPalettes;
    gPaletteFade.delayCounter = delay;
    gPaletteFade_delay = delay;
    gPaletteFade.y = startY;
    gPaletteFade.targetY = targetY;
    gPaletteFade.blendColor = blendColor;
    gPaletteFade.active = TRUE;
    gPaletteFade.mode = 0;   /* NORMAL_FADE (local enum in palette.c) */
    gPaletteFade.yDec = (startY < targetY) ? FALSE : TRUE;
    UpdatePaletteFade();
    return TRUE;
}

u8 UpdatePaletteFade(void)
{
    u32 sel;
    u16 off;
    if (!gPaletteFade.active || gPaletteFade.mode != 0)   /* 0 = NORMAL_FADE */
        return 0;
    if (gPaletteFade.delayCounter < gPaletteFade_delay) {
        gPaletteFade.delayCounter++;
        return 2;
    }
    gPaletteFade.delayCounter = 0;
    sel = gPaletteFade_selectedPalettes;
    off = 0;
    while (sel) {
        if (sel & 1)
            BlendPalette(off, 16, gPaletteFade.y, gPaletteFade.blendColor);
        sel >>= 1;
        off += 16;
    }
    if (gPaletteFade.y == gPaletteFade.targetY) {
        gPaletteFade_selectedPalettes = 0;
        gPaletteFade.active = FALSE;
    } else {
        s8 v = gPaletteFade.y;
        if (!gPaletteFade.yDec) {
            v += gPaletteFade.deltaY;
            if (v > (s8)gPaletteFade.targetY) v = gPaletteFade.targetY;
        } else {
            v -= gPaletteFade.deltaY;
            if (v < (s8)gPaletteFade.targetY) v = gPaletteFade.targetY;
        }
        gPaletteFade.y = v;
    }
    return 1;
}
void BeginHardwarePaletteFade(u8 a, u8 b, u8 c, u8 d, u8 e) { (void)a;(void)b;(void)c;(void)d;(void)e; }
void PaletteStruct_ResetById(u16 id) { (void)id; }

void PlaySE(u16 s) { (void)s; }
void PlaySE12WithPanning(u16 s, s8 p) { (void)s;(void)p; }
void PlaySE1WithPanning(u16 s, s8 p) { (void)s;(void)p; }
void SE12PanpotControl(s8 p) { (void)p; }
bool8 IsSEPlaying(void) { return FALSE; }
void m4aMPlayStop(struct MusicPlayerInfo *p) { (void)p; }
void m4aMPlayVolumeControl(struct MusicPlayerInfo *p, u16 a, u16 b) { (void)p;(void)a;(void)b; }

void LZDecompressVram(const void *s, void *d) { (void)s;(void)d; }
void LZDecompressWram(const void *s, void *d) { (void)s;(void)d; }
s16 RequestDma3Copy(const void *s, void *d, u16 n, u8 m) { (void)s;(void)d;(void)n;(void)m; return 0; }
s16 RequestDma3Fill(s32 v, void *d, u16 n, u8 m) { (void)v;(void)d;(void)n;(void)m; return 0; }

u16 LoadBgTiles(u8 a, const void *s, u16 n, u16 o) { (void)a;(void)s;(void)n;(void)o; return 0; }
void CopyToBgTilemapBuffer(u8 a, const void *s, u16 m, u16 o) { (void)a;(void)s;(void)m;(void)o; }
void CopyBgTilemapBufferToVram(u8 a) { (void)a; }
void FillBgTilemapBufferRect(u8 a, u16 t, u8 x, u8 y, u8 w, u8 h, u8 p) { (void)a;(void)t;(void)x;(void)y;(void)w;(void)h;(void)p; }
void CopyToBgTilemapBufferRect_ChangePalette(u8 a, const void *s, u8 x, u8 y, u8 w, u8 h, u8 p) { (void)a;(void)s;(void)x;(void)y;(void)w;(void)h;(void)p; }
void CopyBattlerSpriteToBg(s32 a, u8 x, u8 y, u8 pos, u8 pal, u8 *td, u16 *mp, u16 to) { (void)a;(void)x;(void)y;(void)pos;(void)pal;(void)td;(void)mp;(void)to; }
void DrawMainBattleBackground(void) {}
s32 GetAnimBgAttribute(u8 a, u8 b) { (void)a;(void)b; return 0; }
/* Capture the anim BG's SCREEN_SIZE (attributeId 0 = BG_ANIM_SCREEN_SIZE) the
 * move sets. The renderer needs it to lay out the tilemap's screenblocks: a
 * 2-screenblock map is 512x256 (side-by-side) for size 1 vs 256x512 (stacked)
 * for size 2. Read out via engine_bg_screen_size — no hardcoded per-move width. */
s8 gHostAnimBgScreenSize = -1;   /* -1 = unset (assume linear 256-wide) */
void SetAnimBgAttribute(u8 bgId, u8 attributeId, u8 value)
{
    (void)bgId;
    if (attributeId == 0)        /* BG_ANIM_SCREEN_SIZE */
        gHostAnimBgScreenSize = (s8)value;
}

void LoadSpecialPokePic(const struct CompressedSpriteSheet *s, void *d, s32 sp, u32 p, bool8 f) { (void)s;(void)d;(void)sp;(void)p;(void)f; }
void LoadSpecialPokePic_DontHandleDeoxys(const struct CompressedSpriteSheet *s, void *d, s32 sp, u32 p, bool8 f) { (void)s;(void)d;(void)sp;(void)p;(void)f; }

/* Host CreateAdditionalMonSpriteForMoveAnim (the project copy is renamed _ORIG by
 * the build -D). The real one loads a mon's pic into VRAM via hardware DMA, which
 * faults under wasm — that trapped Role Play's silhouette task so it got banned +
 * never ran. We just create a PLACEHOLDER sprite at the requested position so the
 * task RUNS (sets objMode BLEND, ramps BLDALPHA, scales it). The renderer draws
 * the target mon's actual pic onto the silhouette, keyed to the engine's eva fade.
 * The placeholder carries no gfx (tileTag 0) so the renderer skips it directly. */
/* The placeholder MUST match the engine's real mon-pic OAM: 64x64 + AFFINE_NORMAL.
 * The animation's scale step (Role Play's Step2) does `oam.affineMode |= DOUBLE_MASK`
 * then TrySetSpriteRotScale, which only writes the matrix when `affineMode & 1`.
 * A dummy OFF(0) OAM becomes ERASE(2) (bit 0 clear) → the scale is silently
 * dropped (matrix stuck at identity, sprite never resizes). NORMAL(1) becomes
 * DOUBLE(3) → the real squash matrix is written + captured in the snapshot. */
static const struct OamData sHostMonPicOam = {
    .affineMode = ST_OAM_AFFINE_NORMAL,
    .objMode = ST_OAM_OBJ_NORMAL,
    .shape = ST_OAM_SQUARE,
    .size = 3,                 /* 64x64 */
};
static const struct SpriteTemplate sHostMonPicTemplate = {
    .tileTag = 0, .paletteTag = 0, .oam = &sHostMonPicOam,
    .anims = gDummySpriteAnimTable, .images = NULL,
    .affineAnims = gDummySpriteAffineAnimTable, .callback = SpriteCallbackDummy,
};
/* Which battler each additional-mon placeholder represents, keyed by sprite id —
 * so the renderer can substitute that battler's reference pic with NO task-name
 * match (a renamed/duplicated Role Play works identically). */
static u8 sAddlMonBattler[MAX_SPRITES];
static u8 sAddlMonBackpic[MAX_SPRITES];
u8 CreateAdditionalMonSpriteForMoveAnim(u16 species, bool8 isBackpic, u8 templateId,
        s16 x, s16 y, u8 subpriority, u32 personality, u32 trainerId,
        u32 battlerId, bool32 ignoreDeoxys)
{
    (void)species; (void)templateId; (void)personality;
    (void)trainerId; (void)ignoreDeoxys;
    u8 id = CreateSprite(&sHostMonPicTemplate, x, y, subpriority);
    if (id < MAX_SPRITES)
    {
        sAddlMonBattler[id] = (u8)battlerId;
        sAddlMonBackpic[id] = (u8)isBackpic;
    }
    return id;
}
/* 1 if sprite i is an additional-mon placeholder (created via the dedicated
 * template above), filling the battler it stands in for + the requested pic
 * orientation. The template identity is the marker — no name/tag heuristic. */
int HostAddlMonInfo(int i, int *battler, int *backpic)
{
    if (i < 0 || i >= MAX_SPRITES || gSprites[i].template != &sHostMonPicTemplate)
        return 0;
    if (battler) *battler = (int)sAddlMonBattler[i];
    if (backpic) *backpic = (int)sAddlMonBackpic[i];
    return 1;
}
static const u32 sDummyPal[8] = {0};
const u32 *GetMonSpritePalFromSpeciesAndPersonality(u16 s, u32 o, u32 p) { (void)s;(void)o;(void)p; return sDummyPal; }
bool8 ShouldIgnoreDeoxysForm(u8 a, u8 b) { (void)a;(void)b; return FALSE; }
void HandleSpeciesGfxDataChange(u8 a, u8 b, u8 c) { (void)a;(void)b;(void)c; gHostMonSwapped = 1; }
void LoadBattleMonGfxAndAnimate(u8 a, bool8 b, u8 c) { (void)a;(void)b;(void)c; }
u8 UpdateMonIconFrame(struct Sprite *s) { (void)s; return 0; }
void SetBattlerShadowSpriteCallback(u8 a, u16 b) { (void)a;(void)b; }
void SetHealthboxSpriteInvisible(u8 a) { (void)a; }
void SetHealthboxSpriteVisible(u8 a) { (void)a; }
void UpdateOamPriorityInAllHealthboxes(u8 a) { (void)a; }
bool8 LoadCompressedSpriteSheetUsingHeap(const struct CompressedSpriteSheet *s) { (void)s; return FALSE; }
bool8 LoadCompressedSpritePaletteUsingHeap(const struct CompressedSpritePalette *s) { (void)s; return FALSE; }

u8 ScanlineEffect_InitWave(u8 a, u8 b, u8 c, u8 d, u8 e, u8 f, bool8 g) { (void)a;(void)b;(void)c;(void)d;(void)e;(void)f;(void)g; return 0; }
/* The host can't run the per-HBlank DMA, but DoMementoShadowEffect still fills
 * gScanlineEffectRegBuffers every frame; the renderer reads that buffer to
 * reconstruct the vertical stretch. We mark the effect ACTIVE here (and pin
 * srcBuffer 0, the buffer DoMementoShadowEffect indexes) so the renderer can
 * tell a Memento-style shadow (scanline stretch running) from a plain monbg
 * freeze (Mimic, no scanline). */
void ScanlineEffect_SetParams(struct ScanlineEffectParams p)
{
    gScanlineEffect.state = 1;
    gScanlineEffect.srcBuffer = 0;
    gHostScanWide = (p.dmaControl == (u32)SCANLINE_EFFECT_DMACNT_32BIT) ? 1 : 0;
    /* dmaDest is &REG_BGnHOFS/VOFS — the BG layer + axis this effect scrolls per
     * scanline. Never dereferenced; compared as a constant. VOFS = vertical
     * (Memento soul-shadow stretch); HOFS = horizontal (Extrasensory psychic-BG
     * warp). The renderer uses (layer, axis) to apply the right distortion. */
    if (p.dmaDest == (volatile void *)REG_ADDR_BG1VOFS)      { gHostShadowLayer = 1; gHostScanAxis = 2; }
    else if (p.dmaDest == (volatile void *)REG_ADDR_BG2VOFS) { gHostShadowLayer = 2; gHostScanAxis = 2; }
    else if (p.dmaDest == (volatile void *)REG_ADDR_BG1HOFS) { gHostShadowLayer = 1; gHostScanAxis = 1; }
    else if (p.dmaDest == (volatile void *)REG_ADDR_BG2HOFS) { gHostShadowLayer = 2; gHostScanAxis = 1; }
    /* Per-scanline BLDALPHA (Surf's wave: a rising band of per-row alpha over the
     * scene). The buffer holds BLDALPHA_BLEND(eva,evb) per row; the renderer draws
     * the anim BG at each row's eva/16 opacity. */
    else if (p.dmaDest == (volatile void *)REG_ADDR_BLDALPHA) { gHostShadowLayer = 0; gHostScanAxis = 3; }
    else                                                     { gHostShadowLayer = 0; gHostScanAxis = 0; }
}

/* Read-outs for the driver's shadow exports (kept here where the globals + the
 * ScanlineEffect struct live, so driver.c needs no struct layout). */
int HostScanlineSrcBufAddr(void) { return (int)(intptr_t)&gScanlineEffectRegBuffers[gScanlineEffect.srcBuffer & 1][0]; }
int HostScanlineState(void)      { return gScanlineEffect.state; }
int HostShadowLayer(void)        { return gHostShadowLayer; }
int HostScanAxis(void)           { return gHostScanAxis; }
int HostScanWide(void)           { return gHostScanWide; }
int HostWin0H(void)              { return gBattle_WIN0H; }
void ScanlineEffect_Stop(void) {}

/* SmokescreenImpact is real (battle_anim_smokescreen.c is compiled). */
void UpdatePlayerPosInThrowAnim(struct Sprite *s) { (void)s; }

/* ── extra symbols pulled in by smokescreen / special (ball throw, level-up) ── */
u8 gBattleCommunication[BATTLE_COMMUNICATION_ENTRIES_COUNT];
bool8 gDoingBattleAnim;
u16 gLastUsedItem;
static struct SaveBlock2 sSaveBlock2;
struct SaveBlock2 *gSaveBlock2Ptr = &sSaveBlock2;
const u32 gSmokescreenImpactPalette[8] = {0};
const u32 gSmokescreenImpactTiles[8] = {0};
const u32 gUnusedLevelupAnimationGfx[8] = {0};
const u32 gUnusedLevelupAnimationTilemap[8] = {0};
/* Ball templates (catch animation only) — dummy but with a valid OAM + dummy
 * callback so a stray ball sprite can't deref NULL. */
const struct SpriteTemplate gBallSpriteTemplates[16];

const u32 gBattleAnimSpriteGfx_Particles[] = {0};
void m4aMPlayAllStop(void) {}
void ClearBehindSubstituteBit(u8 b) { (void)b; }
void FreeBallGfx(u8 b) { (void)b; }
void LoadBallGfx(u8 b) { (void)b; }
void SpriteCB_PlayerThrowInit(struct Sprite *s) { (void)s; }
void SpriteCB_SetInvisible(struct Sprite *s) { if (s) s->invisible = TRUE; }

/* No-op MoveBattlerSpriteToBG (the project's copy is renamed _ORIG via the build
 * -D). The real one copies a mon onto a BG layer through hardware-address VRAM
 * fills that fault under wasm, trapping the wall moves (Barrier/Light Screen/
 * Reflect/Mirror Coat/Magic Coat) + dark moves. The "moved to BG" mon is handled
 * render-side instead: the tab flags monbg battlers bgFrozen and draws them
 * full-size + untransformed (so Mimic's frozen target doesn't shrink). */
void MoveBattlerSpriteToBG(u8 battlerId, u8 toBG_2)
{
    /* The real copy does hardware VRAM fills that fault under wasm. We DON'T copy
     * tiles — the renderer draws the mon's pic as the ghost. But we DO replicate
     * the scroll-register math, because the shadow tasks read gBattle_BGn_Y right
     * after this (as data[10], the neutral scanline value), and we record which
     * BG layer + that base scroll so the renderer can place the ghost. We do NOT
     * touch sprite->invisible: the real code hides the sprite then
     * AnimTask_InitMementoShadow re-shows it; leaving it visible avoids hiding
     * mons in untested monbg callers — the shadow draws as an ADDITIONAL ghost. */
    u8 sid;
    s16 baseX, baseY;
    if (battlerId >= 4)
        return;
    sid = gBattlerSpriteIds[battlerId];
    baseX = (s16)(-(gSprites[sid].x + gSprites[sid].x2) + 0x20);
    baseY = (s16)(-(gSprites[sid].y + gSprites[sid].y2) + 0x20);
    if (!toBG_2) { gBattle_BG1_X = baseX; gBattle_BG1_Y = baseY; }
    else         { gBattle_BG2_X = baseX; gBattle_BG2_Y = baseY; }
    gHostMonBg[battlerId] = toBG_2 ? 2 : 1;
    gHostMonBgBaseY[battlerId] = baseY;
}

/* --- ABI-correct RunAffineAnimFromTaskData ---------------------------------
 * The decomp indexes the affine-anim command table with a HARDCODED 8-byte
 * stride: `LoadPointerFromVars(...) + (task->data[7] << 3)`. That matches the
 * GBA toolchain, which rounds `union AffineAnimCmd` up to 8 bytes; clang/wasm
 * packs the same union to 6 bytes, so the index lands mid-struct and every
 * GrowAndShrink-family move (Bulk Up, Swords Dance, Harden, ...) reads garbage
 * scale data. The fault is ABI-specific, not in the game data — so we DON'T
 * touch the project's source. Instead we reimplement this one function VERBATIM
 * except the index uses TYPED pointer arithmetic, which strides by the real
 * sizeof on whatever ABI we compile for. The build compiles the project's copy
 * under the renamed symbol RunAffineAnimFromTaskData_ORIG (unused), so every
 * caller (effects_2/3, psychic) links against THIS definition instead. (We use
 * a compile-time rename, not -Wl,--wrap: at -O1 wasm-ld's --wrap fails to
 * redirect these calls.) */
bool8 RunAffineAnimFromTaskData(struct Task *task)
{
    const union AffineAnimCmd *cmd =
        (const union AffineAnimCmd *)LoadPointerFromVars(task->data[13], task->data[14])
        + task->data[7];
    switch (cmd->type)
    {
    default:
        if (!cmd->frame.duration)
        {
            task->data[10] = cmd->frame.xScale;
            task->data[11] = cmd->frame.yScale;
            task->data[12] = cmd->frame.rotation;
            ++task->data[7];
            ++cmd;
        }
        task->data[10] += cmd->frame.xScale;
        task->data[11] += cmd->frame.yScale;
        task->data[12] += cmd->frame.rotation;
        SetSpriteRotScale(task->data[15], task->data[10], task->data[11], task->data[12]);
        SetBattlerSpriteYOffsetFromYScale(task->data[15]);
        if (++task->data[8] >= cmd->frame.duration)
        {
            task->data[8] = 0;
            ++task->data[7];
        }
        break;
    case AFFINEANIMCMDTYPE_JUMP:
        task->data[7] = cmd->jump.target;
        break;
    case AFFINEANIMCMDTYPE_LOOP:
        if (cmd->loop.count)
        {
            if (task->data[9])
            {
                if (!--task->data[9])
                {
                    ++task->data[7];
                    break;
                }
            }
            else
            {
                task->data[9] = cmd->loop.count;
            }
            if (!task->data[7])
                break;
            while (TRUE)
            {
                --task->data[7];
                --cmd;
                if (cmd->type == AFFINEANIMCMDTYPE_LOOP)
                {
                    ++task->data[7];
                    return TRUE;
                }
                if (!task->data[7])
                    return TRUE;
            }
        }
        ++task->data[7];
        break;
    case AFFINEANIMCMDTYPE_END:
        gSprites[task->data[15]].y2 = 0;
        ResetSpriteRotScale(task->data[15]);
        return FALSE;
    }
    return TRUE;
}

int HostStubAffSizeof(void) { return (int)sizeof(union AffineAnimCmd); }
