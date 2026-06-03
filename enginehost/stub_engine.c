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

/* Per-species coord tables (GetBattlerSpriteCoord reads .y_offset). Zeroed for
 * now → coords default to the base sBattlerCoords; the driver can fill in real
 * per-species values via the API. NUM_SPECIES sizing keeps indexing in-bounds. */
const struct MonCoords gMonFrontPicCoords[NUM_SPECIES + 1];
const struct MonCoords gMonBackPicCoords[NUM_SPECIES + 1];
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
u16 gBattle_BG3_X, gBattle_BG3_Y;
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

void *AllocZeroed(u32 size) { void *p = calloc(1, size ? size : 1); return p; }
void Free(void *pointer)    { free(pointer); }

u32 GetMonData2(struct Pokemon *mon, s32 field) { (void)mon; (void)field; return 0; }

/* ───────────────────────── no-op stubs (cosmetic to motion) ─────── */

void SetGpuReg(u8 r, u16 v) { (void)r; (void)v; }
void SetGpuRegBits(u8 r, u16 m) { (void)r; (void)m; }
void ClearGpuRegBits(u8 r, u16 m) { (void)r; (void)m; }
u16  GetGpuReg(u8 r) { (void)r; return 0; }

void BlendPalette(u16 a, u16 b, u8 c, u16 d) { (void)a;(void)b;(void)c;(void)d; }
void BlendPalettes(u32 a, u8 b, u16 c) { (void)a;(void)b;(void)c; }
void LoadPalette(const void *s, u16 o, u16 n) { (void)s;(void)o;(void)n; }
void LoadCompressedPalette(const u32 *s, u16 o, u16 n) { (void)s;(void)o;(void)n; }
void FillPalette(u16 v, u16 o, u16 n) { (void)v;(void)o;(void)n; }
void TintPlttBuffer(u32 a, s8 r, s8 g, s8 b) { (void)a;(void)r;(void)g;(void)b; }
void InvertPlttBuffer(u32 a) { (void)a; }
void UnfadePlttBuffer(u32 a) { (void)a; }
bool8 BeginNormalPaletteFade(u32 a, s8 b, u8 c, u8 d, u16 e) { (void)a;(void)b;(void)c;(void)d;(void)e; return FALSE; }
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
void SetAnimBgAttribute(u8 a, u8 b, u8 c) { (void)a;(void)b;(void)c; }

void LoadSpecialPokePic(const struct CompressedSpriteSheet *s, void *d, s32 sp, u32 p, bool8 f) { (void)s;(void)d;(void)sp;(void)p;(void)f; }
void LoadSpecialPokePic_DontHandleDeoxys(const struct CompressedSpriteSheet *s, void *d, s32 sp, u32 p, bool8 f) { (void)s;(void)d;(void)sp;(void)p;(void)f; }
static const u32 sDummyPal[8] = {0};
const u32 *GetMonSpritePalFromSpeciesAndPersonality(u16 s, u32 o, u32 p) { (void)s;(void)o;(void)p; return sDummyPal; }
bool8 ShouldIgnoreDeoxysForm(u8 a, u8 b) { (void)a;(void)b; return FALSE; }
void HandleSpeciesGfxDataChange(u8 a, u8 b, u8 c) { (void)a;(void)b;(void)c; }
void LoadBattleMonGfxAndAnimate(u8 a, bool8 b, u8 c) { (void)a;(void)b;(void)c; }
u8 UpdateMonIconFrame(struct Sprite *s) { (void)s; return 0; }
void SetBattlerShadowSpriteCallback(u8 a, u16 b) { (void)a;(void)b; }
void SetHealthboxSpriteInvisible(u8 a) { (void)a; }
void SetHealthboxSpriteVisible(u8 a) { (void)a; }
void UpdateOamPriorityInAllHealthboxes(u8 a) { (void)a; }
bool8 LoadCompressedSpriteSheetUsingHeap(const struct CompressedSpriteSheet *s) { (void)s; return FALSE; }
bool8 LoadCompressedSpritePaletteUsingHeap(const struct CompressedSpritePalette *s) { (void)s; return FALSE; }

u8 ScanlineEffect_InitWave(u8 a, u8 b, u8 c, u8 d, u8 e, u8 f, bool8 g) { (void)a;(void)b;(void)c;(void)d;(void)e;(void)f;(void)g; return 0; }
void ScanlineEffect_SetParams(struct ScanlineEffectParams p) { (void)p; }
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
