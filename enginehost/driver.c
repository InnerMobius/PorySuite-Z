/* driver.c — exported-function interface for the headless animation engine
 * (WASM reactor module). Python (via wasmtime) drives a whole move:
 *
 *   engine_reset(attackerIsPlayer)
 *   engine_set_arg(i, value)              // gBattleAnimArgs before a create
 *   engine_create_sprite(tplIndex, battler, subpriority)   -> spriteId
 *   engine_create_task(taskIndex)         -> taskId
 *   engine_step()                         // one GBA frame
 *   engine_snapshot()                     -> count of active sprites
 *   engine_snapshot_addr()                -> address of the snapshot buffer
 *
 * Python reads the snapshot array straight out of wasm linear memory. The
 * engine computes MOTION only; pixels are drawn in Qt from the project's PNGs.
 *
 * Template/task indices come from names.json (gen_tables.py), so any template
 * the project defines is addressable.
 */

#include "global.h"
#include "gflib.h"
#include "sprite.h"
#include "task.h"
#include "battle_anim.h"
#include "constants/battle_anim.h"

extern const struct SpriteTemplate *const gHostTemplates[];
extern const int gHostTemplateCount;
typedef void (*HostTaskFn)(u8);
extern HostTaskFn const gHostTasks[];
extern const int gHostTaskCount;

extern u8 gBattleAnimAttacker, gBattleAnimTarget;
extern s16 gBattleAnimArgs[];

/* Position-holder template for the two mon sprites (non-TAG_NONE so CreateSprite
 * doesn't deref a null image table). Python draws the real mon. */
static const struct SpriteTemplate sMonTemplate = {
    .tileTag = 0, .paletteTag = 0, .oam = &gDummyOamData,
    .anims = gDummySpriteAnimTable, .images = NULL,
    .affineAnims = gDummySpriteAffineAnimTable, .callback = SpriteCallbackDummy,
};

/* Which host-template index each sprite came from (-1 = mon / internal). */
static int sSpriteTpl[MAX_SPRITES];

struct Snap {
    int id;
    int x, y, x2, y2;
    int tileNum, shape, size;
    int matrixNum, mA, mB, mC, mD;
    int hFlip, vFlip, affineMode;
    int priority, subpriority, paletteNum;
    int invisible;
    int templateIndex;   /* host index, or -1 */
    int isMon;           /* battler index if a mon sprite, else -1 */
};
static struct Snap sSnap[MAX_SPRITES];

__attribute__((export_name("engine_reset")))
void engine_reset(int attackerIsPlayer)
{
    int i;
    ResetSpriteData();
    ResetTasks();
    for (i = 0; i < MAX_SPRITES; i++)
        sSpriteTpl[i] = -1;
    for (i = 0; i < ANIM_ARGS_COUNT; i++)
        gBattleAnimArgs[i] = 0;

    gBattleTypeFlags = 0;
    gBattlersCount = 2;
    gBattlerPositions[0] = 0; gBattlerPositions[1] = 1;
    gBattlerPositions[2] = 2; gBattlerPositions[3] = 3;
    gBattlerPartyIndexes[0] = 0; gBattlerPartyIndexes[1] = 0;

    /* Player mon at (72,80), enemy at (176,40). Attacker = player or enemy. */
    gBattlerSpriteIds[0] = CreateSprite(&sMonTemplate, 72, 80, 10);
    gBattlerSpriteIds[1] = CreateSprite(&sMonTemplate, 176, 40, 10);
    if (attackerIsPlayer) { gBattleAnimAttacker = 0; gBattleAnimTarget = 1; }
    else                  { gBattleAnimAttacker = 1; gBattleAnimTarget = 0; }
}

__attribute__((export_name("engine_set_arg")))
void engine_set_arg(int i, int value)
{
    if (i >= 0 && i < ANIM_ARGS_COUNT)
        gBattleAnimArgs[i] = (s16)value;
}

/* Mirrors Cmd_createsprite: position at the target's coords, run the template's
 * callback every frame. battler selects attacker/target subpriority anchor. */
__attribute__((export_name("engine_create_sprite")))
int engine_create_sprite(int tplIndex, int battler, int subpriority)
{
    u8 id;
    int coordBattler = (battler == 0) ? gBattleAnimAttacker : gBattleAnimTarget;
    if (tplIndex < 0 || tplIndex >= gHostTemplateCount)
        return -1;
    (void)coordBattler;
    id = CreateSpriteAndAnimate(
        gHostTemplates[tplIndex],
        GetBattlerSpriteCoord(gBattleAnimTarget, BATTLER_COORD_X_2),
        GetBattlerSpriteCoord(gBattleAnimTarget, BATTLER_COORD_Y_PIC_OFFSET),
        (u8)subpriority);
    if (id < MAX_SPRITES)
        sSpriteTpl[id] = tplIndex;
    return id;
}

__attribute__((export_name("engine_create_task")))
int engine_create_task(int taskIndex)
{
    u8 tid;
    if (taskIndex < 0 || taskIndex >= gHostTaskCount)
        return -1;
    tid = CreateTask(gHostTasks[taskIndex], 5);
    gHostTasks[taskIndex](tid);   /* engine calls the task once on creation */
    return tid;
}

__attribute__((export_name("engine_step")))
void engine_step(void)
{
    AnimateSprites();
    RunTasks();
}

static int sIsMonSprite(int id)
{
    if (id == gBattlerSpriteIds[0]) return 0;
    if (id == gBattlerSpriteIds[1]) return 1;
    return -1;
}

/* "Still animating?" — active EFFECT sprites (not the mon holders) + active
 * tasks. The timeline player uses this for waitforvisualfinish / end-drain. */
__attribute__((export_name("engine_busy")))
int engine_busy(void)
{
    int i, n = 0;
    for (i = 0; i < MAX_SPRITES; i++)
        if (gSprites[i].inUse && sIsMonSprite(i) < 0)
            n++;
    for (i = 0; i < NUM_TASKS; i++)
        if (gTasks[i].isActive)
            n++;
    return n;
}

__attribute__((export_name("engine_snapshot")))
int engine_snapshot(void)
{
    int i, n = 0;
    for (i = 0; i < MAX_SPRITES; i++)
    {
        struct Sprite *s = &gSprites[i];
        struct Snap *o;
        if (!s->inUse)
            continue;
        o = &sSnap[n++];
        o->id = i;
        o->x = s->x; o->y = s->y; o->x2 = s->x2; o->y2 = s->y2;
        /* Frame-relative tile offset → Python turns this into a PNG cell index.
         * OAM tileNum is a 10-bit field, so the offset is 10-bit too. We never
         * load real VRAM, so sheetTileStart is often TAG_NONE (0xFFFF) for an
         * un-loaded gfx tag; the anim system then bases oam.tileNum at
         * (0xFFFF + frame) masked to 10 bits. Masking the difference to 0x3FF
         * recovers the true frame offset in every case (loaded or not) — without
         * it, a multi-part sprite like Dig's dirt mound mis-reads its left half
         * (e.g. 1024 instead of 0) and both halves draw the same tile. */
        o->tileNum = (int)((u16)(s->oam.tileNum - s->sheetTileStart) & 0x3FF);
        o->shape = s->oam.shape; o->size = s->oam.size;
        o->matrixNum = s->oam.matrixNum;
        o->affineMode = s->oam.affineMode;
        if (s->oam.affineMode != ST_OAM_AFFINE_OFF)
        {
            struct OamMatrix *m = &gOamMatrices[s->oam.matrixNum];
            o->mA = m->a; o->mB = m->b; o->mC = m->c; o->mD = m->d;
        }
        else { o->mA = 256; o->mB = 0; o->mC = 0; o->mD = 256; }
        /* Flip: many callbacks flip by writing the bit straight into
         * oam.matrixNum (ST_OAM_HFLIP/VFLIP) for NON-affine sprites, bypassing
         * sprite->hFlip/vFlip. Read BOTH or facing is wrong for those (Curse
         * nail, Foresight magnifier, ...). */
        o->hFlip = s->hFlip;
        o->vFlip = s->vFlip;
        if (s->oam.affineMode == ST_OAM_AFFINE_OFF)
        {
            if (s->oam.matrixNum & ST_OAM_HFLIP) o->hFlip = 1;
            if (s->oam.matrixNum & ST_OAM_VFLIP) o->vFlip = 1;
        }
        o->priority = s->oam.priority; o->subpriority = s->subpriority;
        o->paletteNum = s->oam.paletteNum;
        o->invisible = s->invisible;
        o->templateIndex = sSpriteTpl[i];
        o->isMon = sIsMonSprite(i);
    }
    return n;
}

__attribute__((export_name("engine_snapshot_addr")))
int engine_snapshot_addr(void)
{
    return (int)(intptr_t)&sSnap[0];
}

/* BG1 scroll (the layer fadetobg / sliding-bg / surf-wave tasks drive), packed
 * as (x<<16)|y for the preview to scroll the animation background by. */
__attribute__((export_name("engine_bg_scroll")))
int engine_bg_scroll(void)
{
    return ((gBattle_BG1_X & 0xFFFF) << 16) | (gBattle_BG1_Y & 0xFFFF);
}

/* BG2 scroll — the attacker's mon-on-BG layer for a player-side Dig-style sink
 * uses BG2 (priority rank 2); the enemy side uses BG1 above. Packed (x<<16)|y. */
__attribute__((export_name("engine_bg2_scroll")))
int engine_bg2_scroll(void)
{
    return ((gBattle_BG2_X & 0xFFFF) << 16) | (gBattle_BG2_Y & 0xFFFF);
}

/* Diagnostic: how this build lays out the affine-anim cmd struct, vs the <<3
 * (8-byte) stride RunAffineAnimFromTaskData assumes. */
extern int HostStubAffSizeof(void);   /* sizeof in stub_engine.c's TU (the wrap) */
__attribute__((export_name("engine_dbg")))
int engine_dbg(int what)
{
    static const union AffineAnimCmd probe[] = {
        AFFINEANIMCMD_FRAME(-4, -5, 0, 12),
        AFFINEANIMCMD_FRAME(0, 0, 0, 24),
    };
    if (what == 0) return (int)sizeof(union AffineAnimCmd);
    if (what == 1) return (int)sizeof(struct AffineAnimFrameCmd);
    if (what == 2) return (int)_Alignof(union AffineAnimCmd);
    if (what == 3) return (int)((const char *)&probe[1] - (const char *)&probe[0]);
    if (what == 4) return HostStubAffSizeof();
    if (what == 5) {   /* read probe[1] via typed ptr: expect xScale=0,yScale=0,dur=24 */
        const union AffineAnimCmd *c = &probe[0] + 1;
        return ((c->frame.xScale & 0xFF))
             | ((c->frame.yScale & 0xFF) << 8)
             | ((c->frame.duration & 0xFF) << 16);
    }
    return 0;
}

__attribute__((export_name("engine_snap_stride")))
int engine_snap_stride(void)
{
    return (int)sizeof(struct Snap);
}
