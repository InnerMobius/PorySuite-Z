/* Spike driver: set up a single-battle scene, create one REAL effect sprite via
 * the engine's own CreateSpriteAndAnimate, step frames, and print its position.
 * Proves the native engine produces faithful per-frame motion. */
#include "global.h"
#include "gflib.h"
#include "sprite.h"
#include "task.h"
#include "battle_anim.h"
#include "constants/battle_anim.h"
#include <stdio.h>

extern const struct SpriteTemplate gEmberSpriteTemplate;
extern u8 gBattleAnimAttacker, gBattleAnimTarget;
extern s16 gBattleAnimArgs[];

/* tileTag MUST NOT be TAG_NONE (0xFFFF): that branch dereferences images->size,
 * and we use no image table. A non-TAG_NONE tag takes the sheet branch, which
 * resolves to "no sheet" (sheetTileStart = -1) with no deref. Pure position
 * holder for gBattlerSpriteIds; Python renders the real mon. */
static const struct SpriteTemplate sMonTemplate = {
  .tileTag = 0, .paletteTag = 0, .oam = &gDummyOamData,
  .anims = gDummySpriteAnimTable, .images = NULL,
  .affineAnims = gDummySpriteAffineAnimTable, .callback = SpriteCallbackDummy,
};

#define CK(msg) do { fprintf(stderr, "CK: %s\n", msg); fflush(stderr); } while (0)
int main(void) {
  CK("ResetSpriteData"); ResetSpriteData();
  CK("ResetTasks"); ResetTasks();
  gBattleTypeFlags = 0; gBattlersCount = 2;
  gBattlerPositions[0]=0; gBattlerPositions[1]=1; gBattlerPositions[2]=2; gBattlerPositions[3]=3;
  gBattlerPartyIndexes[0]=0; gBattlerPartyIndexes[1]=0;
  gBattleAnimAttacker=0; gBattleAnimTarget=1;
  CK("create mon0"); gBattlerSpriteIds[0] = CreateSprite(&sMonTemplate, 72, 80, 10);
  CK("create mon1"); gBattlerSpriteIds[1] = CreateSprite(&sMonTemplate, 176, 40, 10);
  /* Ember: createsprite gEmberSpriteTemplate ... 20,0,-16,24,20,1 */
  gBattleAnimArgs[0]=20; gBattleAnimArgs[1]=0; gBattleAnimArgs[2]=-16;
  gBattleAnimArgs[3]=24; gBattleAnimArgs[4]=20; gBattleAnimArgs[5]=1;
  CK("coord X_2"); volatile int cx = GetBattlerSpriteCoord(gBattleAnimTarget, BATTLER_COORD_X_2);
  CK("coord Y_PIC_OFFSET"); volatile int cy = GetBattlerSpriteCoord(gBattleAnimTarget, BATTLER_COORD_Y_PIC_OFFSET);
  CK("CreateSpriteAndAnimate ember");
  u8 e = CreateSpriteAndAnimate(&gEmberSpriteTemplate, cx, cy, 3);
  CK("created ember");
  printf("attacker(0) coord X=%d  target(1) coord X=%d\n",
         GetBattlerSpriteCoord(0,BATTLER_COORD_X_2), GetBattlerSpriteCoord(1,BATTLER_COORD_X_2));
  printf("ember id=%d start render=(%d,%d)\n", e, gSprites[e].x+gSprites[e].x2, gSprites[e].y+gSprites[e].y2);
  for (int f=0; f<40; f++) {
    fprintf(stderr, "CK: frame %d AnimateSprites\n", f); fflush(stderr);
    AnimateSprites(); RunTasks();
    if (!gSprites[e].inUse) { printf("frame %2d: ember DESTROYED (reached target)\n", f); break; }
    printf("frame %2d: render=(%3d,%3d)\n", f, gSprites[e].x+gSprites[e].x2, gSprites[e].y+gSprites[e].y2);
  }
  return 0;
}
