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
#include "palette.h"                  /* BG_PLTT_ID for the BG-palette read-out */

extern const struct SpriteTemplate *const gHostTemplates[];
extern const int gHostTemplateCount;
typedef void (*HostTaskFn)(u8);
extern HostTaskFn const gHostTasks[];
extern const int gHostTaskCount;

extern u8 gBattleAnimAttacker, gBattleAnimTarget;
extern u8 gBattlerAttacker, gBattlerTarget;   /* battle-engine attacker/target —
    * some anim sprites read THESE directly (Superpower's orb/fireball, a dragon
    * move, the SetAnim*ForEffect utility tasks) instead of gBattleAnim*. Must be
    * kept = gBattleAnim* or those effects anchor to the default battler (player). */
extern s16 gBattleAnimArgs[];
extern u8 gHostPalBlendCoeff[32];     /* per-slot tint strength (stub_engine.c) */
extern u16 gHostPalBlendColor[32];    /* per-slot tint colour (BGR555) */
extern u8 gHostPalGray[32];           /* per-slot greyscale flag (stub_engine.c) */
extern u8 gHostBldEva;                /* BLDALPHA top-layer coefficient 0..16 */
extern u8 gHostMonBg[4];              /* per-battler BG-copy layer 0/1/2 (Memento) */
extern s16 gHostMonBgBaseY[4];        /* base BGnVOFS at copy time (data[10]) */
extern u16 gPlttBufferFaded[];        /* the displayed palette buffer (stub_engine.c) —
    * battle-anim tasks animate the BG palette here directly (rotation, fades, …).
    * The host never populates it, so the driver lets Python load the real BG
    * palette in + read whatever the tasks did back out — engine-driven, dynamic. */
extern u8 GetBattleBgPaletteNum(void);  /* compiled from battle_anim_mons.c (= 2) */
extern signed char gHostAnimBgScreenSize;  /* SCREEN_SIZE the move set, or -1 */
int HostScanlineSrcBufAddr(void);     /* &gScanlineEffectRegBuffers[srcBuffer][0] */
int HostScanlineState(void);          /* gScanlineEffect.state (0 = no stretch) */
int HostShadowLayer(void);            /* BG layer (1/2) the active stretch drives */
int HostScanAxis(void);               /* 0 none, 1 HOFS (horizontal), 2 VOFS (vertical) */
int HostScanWide(void);               /* 1 = 32-bit DMA (HOFS+VOFS interleaved) */
int HostWin0H(void);                  /* gBattle_WIN0H: (left<<8)|right */
int HostAddlMonInfo(int i, int *battler, int *backpic);  /* CreateAdditionalMon-
                                       * SpriteForMoveAnim marker: 1 if sprite i is
                                       * one, with the battler it represents. */
void HostResetPalBlend(void);
u8 UpdatePaletteFade(void);           /* software fade step (stub_engine.c) */

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
    int tileTag;         /* sprite->template->tileTag (ANIM_TAG_*), or -1 —
                          * lets the renderer map TASK-spawned sprites (Hail,
                          * Sandstorm, …) to their gfx even with no host index. */
    int isClone;         /* 1 if this is a CloneBattlerSpriteWithBlend copy of a
                          * mon (Double Team after-images, …): same dummy mon
                          * template but NOT a battler holder, so the renderer
                          * draws the attacker's mon pic here, faded. */
    int blendCoeff;      /* palette-blend strength 0..16 for this sprite's slot
                          * (BlendPalette/BlendPalettes/fade). 0 = no tint. */
    int blendColor;      /* BGR555 colour the slot is blended toward. */
    int alpha;           /* 0..16 opacity: BLDALPHA EVA when this sprite's OAM
                          * objMode is BLEND (setalpha, fade-to/from-invisible),
                          * else 16 (opaque). */
    int objMode;         /* OAM obj mode: 0 normal, 1 blend, 2 WINDOW. A WINDOW
                          * sprite is a mask (MetallicShine's invisible mon copy),
                          * NOT drawn — the renderer skips it. */
    int gray;            /* 1 if this sprite's palette slot was greyscaled
                          * (SetGreyscaleOrOriginalPalette — Perish Song). */
    int bgCopy;          /* mon copied to a BG layer: 0 none, 1 BG1, 2 BG2.
                          * MoveBattlerSpriteToBG (Memento/Role Play soul shadow).
                          * Only meaningful for mon sprites (isMon >= 0). */
    int bgCopyBaseY;     /* base BGnVOFS captured at copy time — the neutral value
                          * the per-scanline stretch buffer deviates from. */
    int addlMon;         /* 1 if this is a CreateAdditionalMonSpriteForMoveAnim
                          * placeholder (Role Play's silhouette, or ANY move that
                          * summons a copy of a battler's pic). The renderer draws
                          * addlMonBattler's reference mon here, white, at this
                          * sprite's own transform/alpha — NO task-name match. */
    int addlMonBattler;  /* the battler whose species the addl sprite represents
                          * (what the engine passed), or -1. */
    int addlMonBackpic;  /* isBackpic the engine requested (informational). */
};
static struct Snap sSnap[MAX_SPRITES];

__attribute__((export_name("engine_reset")))
void engine_reset(int attackerIsPlayer)
{
    int i;
    ResetSpriteData();
    ResetTasks();
    /* Initialize the sprite-palette allocator. AllocSpritePalette finds a free
     * slot by scanning sSpritePaletteTags for TAG_NONE (0xFFFF); on fresh BSS
     * that table is all 0x0000, so NO slot reads as free and it returns 0xFF —
     * then OBJ_PLTT_ID(0xFF) indexes far past gPlttBuffer and the write TRAPS.
     * Any move that dynamically allocates a sprite palette (Double Team's
     * after-images, etc.) hit this. FreeAllSpritePalettes sets every slot to
     * TAG_NONE so allocation works. Project-agnostic engine fix. */
    FreeAllSpritePalettes();
    HostResetPalBlend();   /* clear per-slot tint state from the previous move */
    gHostAnimBgScreenSize = -1;   /* the move's BG task re-sets this if it has a BG */
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

    /* Reserve a DEDICATED OAM matrix slot per battler. A mon's affine (bow tilt,
     * grow, squeeze) uses healthBoxesData[battler].matrixNum; without reserving
     * it the slot stays 0 and an effect sprite's AllocOamMatrix grabs slot 0
     * too, so the mon reads the EFFECT's matrix — e.g. Horn Drill's mon shrank
     * to the hit-splat's 0.5x scale. data[0] = battlerId, which
     * PrepareBattlerSpriteForRotScale reads to pick the slot. */
    for (i = 0; i < 2; i++)
    {
        u8 slot = AllocOamMatrix();
        /* Give each battler mon a DISTINCT, reserved OBJ palette slot. The dummy
         * mon template leaves both at the same paletteNum, so a per-mon palette
         * blend (AnimTask_BlendMonInAndOut → Foresight's white flash on just the
         * TARGET) recorded against one slot wrongly tinted BOTH mons. Reserving a
         * unique slot per battler (and marking it used so effect sprites don't
         * grab it) isolates per-mon tints. */
        u8 pal = AllocSpritePalette(0xFFF0 + i);
        gSprites[gBattlerSpriteIds[i]].data[0] = i;
        gSprites[gBattlerSpriteIds[i]].oam.paletteNum = (pal != 0xFF) ? pal : i;
        gBattleSpritesDataPtr->healthBoxesData[i].matrixNum =
            (slot != 0xFF) ? slot : i;
    }

    if (attackerIsPlayer) { gBattleAnimAttacker = 0; gBattleAnimTarget = 1; }
    else                  { gBattleAnimAttacker = 1; gBattleAnimTarget = 0; }
    /* The real engine sets gBattleAnimAttacker = gBattlerAttacker when a move anim
     * launches (battle_anim.c). Mirror it: anim sprites that read the BATTLE-engine
     * globals directly (Superpower orb/fireball via gBattlerAttacker, the
     * SetAnim*ForEffectAnims utility tasks that REASSIGN gBattleAnim* from gBattler*)
     * would otherwise anchor to the default battler 0 (player) regardless of the
     * Player/Enemy direction toggle. Keep them in lockstep. */
    gBattlerAttacker = gBattleAnimAttacker;
    gBattlerTarget   = gBattleAnimTarget;
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
    u8 id, b;
    int off, sub;
    if (tplIndex < 0 || tplIndex >= gHostTemplateCount)
        return -1;
    /* Decode the createsprite subpriority_offset EXACTLY like Cmd_createsprite:
     * the script's 0..127 value is BIASED — >=64 → +(v-64), else → -v — then
     * added to the battler's base subpriority and clamped to >=3. Passing the
     * raw offset inverted layering: Metronome's offsets 11/12 decode to -11/-12,
     * so the finger (more negative = front) is correctly on top of the cloud. */
    off = subpriority & 0x7F;
    off = (off >= 64) ? (off - 64) : -off;
    b = (battler == 1) ? gBattleAnimTarget : gBattleAnimAttacker;
    sub = (int)GetBattlerSpriteSubpriority(b) + off;
    if (sub < 3)
        sub = 3;
    /* Register the template's palette tag BEFORE creating the sprite. The GBA's
     * loadspritegfx + LoadSpritePalette do this; the host stubs gfx loading, so
     * IndexOfSpritePaletteTag(tag) would return 0xFF and a callback that indexes
     * the palette buffer by it (AnimProtect: gPlttBufferFaded[OBJ_PLTT_ID(0xFF)
     * +i]) reads far out of bounds and TRAPS. It must be done BEFORE create:
     * CreateSpriteAndAnimate runs the sprite's INIT callback immediately (that's
     * where AnimProtect reads the tag), so a post-create registration is too
     * late. A registered slot keeps the index valid; the renderer still draws
     * the project palette. */
    {
        const struct SpriteTemplate *tpl = gHostTemplates[tplIndex];
        if (tpl->paletteTag != TAG_NONE
                && IndexOfSpritePaletteTag(tpl->paletteTag) == 0xFF)
            AllocSpritePalette(tpl->paletteTag);
    }
    id = CreateSpriteAndAnimate(
        gHostTemplates[tplIndex],
        GetBattlerSpriteCoord(gBattleAnimTarget, BATTLER_COORD_X_2),
        GetBattlerSpriteCoord(gBattleAnimTarget, BATTLER_COORD_Y_PIC_OFFSET),
        (u8)sub);
    if (id < MAX_SPRITES)
    {
        u8 pal = IndexOfSpritePaletteTag(gHostTemplates[tplIndex]->paletteTag);
        sSpriteTpl[id] = tplIndex;
        if (pal != 0xFF)
            gSprites[id].oam.paletteNum = pal;
    }
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
    UpdatePaletteFade();   /* ramp any software fade-to/from-colour this frame */
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
        o->tileTag = s->template ? (int)s->template->tileTag : -1;
        /* A clone is a copy of the mon's dummy template that is NOT one of the
         * two battler holders (CloneBattlerSpriteWithBlend → Double Team etc.). */
        o->isClone = (s->template == &sMonTemplate && o->isMon < 0) ? 1 : 0;
        /* Tint recorded for this sprite's OBJ palette slot (16 + paletteNum). */
        {
            int slot = 16 + (s->oam.paletteNum & 0xF);
            o->blendCoeff = gHostPalBlendCoeff[slot];
            o->blendColor = gHostPalBlendColor[slot];
            o->gray = gHostPalGray[slot];
        }
        /* Alpha: blend-mode sprites (objMode 1) are drawn at BLDALPHA EVA/16. */
        o->alpha = (s->oam.objMode == 1) ? gHostBldEva : 16;
        o->objMode = s->oam.objMode;
        /* BG-copy shadow (Memento): per-battler, only for mon sprites. */
        if (o->isMon >= 0 && o->isMon < 4) {
            o->bgCopy = gHostMonBg[o->isMon];
            o->bgCopyBaseY = gHostMonBgBaseY[o->isMon];
        } else {
            o->bgCopy = 0; o->bgCopyBaseY = 0;
        }
        /* Additional-mon sprite (Role Play silhouette & friends): the renderer
         * substitutes the reference mon — driven by the engine MARKER, not a
         * task name, so a renamed/duplicated move works identically. */
        {
            int amb = -1, ambp = 0;
            o->addlMon = HostAddlMonInfo(i, &amb, &ambp);
            o->addlMonBattler = amb;
            o->addlMonBackpic = ambp;
        }
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

/* BG3 scroll — the battle-terrain layer the screen/terrain-SHAKE sprite
 * (AnimShakeMonOrBattleTerrain: Rock Throw, Magnitude, Earthquake-likes)
 * oscillates around its base. Packed (x<<16)|y; the renderer offsets the battle
 * scene by it for the rumble. */
extern u16 gBattle_BG3_X, gBattle_BG3_Y;
__attribute__((export_name("engine_bg3_scroll")))
int engine_bg3_scroll(void)
{
    return ((gBattle_BG3_X & 0xFFFF) << 16) | (gBattle_BG3_Y & 0xFFFF);
}

/* The GBA SPRITE-layer screen shake (gSpriteCoordOffsetX/Y), driven by
 * AnimShakeMonOrBattleTerrain — Metal Claw, Dragon Claw, … It jitters every
 * coordOffset-enabled OAM sprite (the battler mons); the renderer offsets the
 * mons by it. Packed signed (x<<16)|y. host_pre.h 64K-aligns these globals so
 * the shake task's split-pointer rebuild stays exact (no sign-extension trap). */
extern short gSpriteCoordOffsetX, gSpriteCoordOffsetY;
__attribute__((export_name("engine_coord_offset")))
int engine_coord_offset(void)
{
    return ((gSpriteCoordOffsetX & 0xFFFF) << 16) | (gSpriteCoordOffsetY & 0xFFFF);
}

/* ── Memento soul-shadow read-outs ──────────────────────────────────────────
 * The shadow tasks (running in-engine) fill a per-scanline BG-VOFS buffer that
 * stretches the blackened mon copy, narrow it with WIN0, and fade it via
 * BLDALPHA. None of that is hardware-rendered here, so we expose the computed
 * state and let the Python renderer reconstruct the ghost. */

/* Address of the active per-scanline vertical-offset buffer (u16[160+]). For
 * each screen row y, buf[y] is the BGnVOFS that row samples at; the deviation
 * from the mon's base scroll (Snap.bgCopyBaseY) is that row's vertical shift. */
__attribute__((export_name("engine_scanline_addr")))
int engine_scanline_addr(void) { return HostScanlineSrcBufAddr(); }

/* bits  0-7  = gScanlineEffect.state (0 = no stretch running → a bgCopy mon is a
 *              plain monbg freeze, not a Memento shadow)
 * bits  8-15 = BLDALPHA EVA (shadow opacity, 0..16)
 * bits 16-17 = BG layer the active stretch drives (1/2) — the renderer draws the
 *              shadow only for the mon whose bgCopy matches this layer.
 * bits 18-19 = axis: 1 = horizontal (HOFS, psychic-BG warp), 2 = vertical (VOFS,
 *              Memento soul-shadow). Lets the renderer pick H vs V distortion. */
__attribute__((export_name("engine_scanline_state")))
int engine_scanline_state(void) {
    return (HostScanlineState() & 0xFF) | ((int)gHostBldEva << 8)
         | ((HostShadowLayer() & 3) << 16) | ((HostScanAxis() & 3) << 18)
         | ((HostScanWide() & 1) << 20);
}

/* WIN0 horizontal bounds (left<<8)|right — the shadow narrows this to a sliver
 * as it finishes; the renderer clips the ghost to [left, right). */
__attribute__((export_name("engine_win0h")))
int engine_win0h(void) { return HostWin0H(); }

/* Per-frame mon FX for the Transform morph: low byte = REG_OFFSET_MOSAIC BG level
 * (0..15, pixelation), bit 8 = the species gfx swap happened (attacker pic is now
 * the target's). The renderer pixelates + swaps the monbg'd mon accordingly. */
extern u8 gHostMosaic;
extern u8 gHostMonSwapped;
__attribute__((export_name("engine_mon_fx")))
int engine_mon_fx(void) { return (int)gHostMosaic | ((int)gHostMonSwapped << 8); }

/* ── BG palette read-out (engine-driven background animation) ────────────────
 * Address of gPlttBufferFaded[0] (the displayed palette buffer). Python writes
 * the move's real BG palette into the BG slot before stepping, then reads it back
 * each frame — so WHATEVER the move's tasks do to it (the psychic rotation, the
 * white-flash, a fade, or a project's CUSTOM palette task) is reflected, with the
 * engine driving the timing. No per-move logic in the renderer. */
__attribute__((export_name("engine_pltt_addr")))
int engine_pltt_addr(void) { return (int)(intptr_t)&gPlttBufferFaded[0]; }

/* The u16 index in gPlttBufferFaded where the anim BG palette lives — slot
 * GetBattleBgPaletteNum() (the engine's own choice, not hardcoded here). */
__attribute__((export_name("engine_bg_pltt_index")))
int engine_bg_pltt_index(void) { return BG_PLTT_ID(GetBattleBgPaletteNum()); }

/* The anim BG's GBA SCREEN_SIZE the move set (0=256x256, 1=512x256, 2=256x512,
 * 3=512x512), or -1 if unset. The renderer lays out the tilemap's screenblocks
 * by this (a 2-screenblock map is side-by-side at size 1, stacked at size 2). */
__attribute__((export_name("engine_bg_screen_size")))
int engine_bg_screen_size(void) { return gHostAnimBgScreenSize; }

/* Process the `monbg` script opcode (the op-runner handles only a subset, so this
 * never ran — Acid Armor / Dragon Dance copy the mon to a BG layer via monbg, and
 * without it MoveBattlerSpriteToBG was never called, leaving gBattle_BGn_X garbage
 * → the per-scanline mon-warp had a wrong base). Replicates Cmd_monbg: map the
 * anim-arg to a battler + pick the BG layer from its position + copy it. */
extern void MoveBattlerSpriteToBG(u8 battlerId, u8 toBG_2);
__attribute__((export_name("engine_monbg")))
void engine_monbg(int animArg) {
    u8 battlerId, position, toBG2;
    /* ANIM_ATTACKER(0)/ANIM_ATK_PARTNER(2) → attacker; ANIM_TARGET(1)/DEF_PARTNER(3) → target */
    battlerId = (animArg == 0 || animArg == 2) ? gBattleAnimAttacker : gBattleAnimTarget;
    if (battlerId >= 4) return;
    position = gBattlerPositions[battlerId];
    /* B_POSITION_OPPONENT_LEFT(1) / B_POSITION_PLAYER_RIGHT(3) → BG1, else BG2 */
    toBG2 = (position == 1 || position == 3) ? 0 : 1;
    MoveBattlerSpriteToBG(battlerId, toBG2);
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
