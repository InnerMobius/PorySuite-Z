# EVENTide User Guide

## What does each tool do?

PorySuite-Z has three main tools. Each handles a different part of your ROM hack:

| Tool | What it edits | Files it writes |
|------|--------------|-----------------|
| **Porymap** | Map layouts, tilesets, wild encounters, connections | `map.json`, layout files, encounter JSON |
| **PorySuite** | Pokémon stats, items, moves, trainer teams/AI | `trainers.h`, `trainer_parties.h`, species data |
| **EVENTide** | NPC scripts, dialogue text, event commands | `scripts.inc`, `text.inc` |

They all work on the same project folder but don't directly talk to each other. When you make changes in one tool, the others pick them up next time they load.

---

## How do I add a new trainer NPC?

This is a multi-step process that crosses three tools. Here's the full walkthrough:

### Step 1: Create the trainer in PorySuite

1. Open PorySuite and go to the **Trainers** tab
2. Click **Add Trainer**
3. Set the trainer's name, class, party, AI flags, and items
4. Save in PorySuite — this writes the trainer's data to `trainers.h` and `trainer_parties.h`
5. Note the trainer constant name (e.g. `TRAINER_LASS_IRIS`) — you'll need it

> **Important**: PorySuite only creates the trainer's battle data (who they are, what Pokémon they have). It does NOT create the script that makes the battle happen on the map, and it does NOT create the dialogue text. That's EVENTide's job.

### Step 2: Place the NPC on the map in Porymap

1. Open Porymap and load the map
2. Add a new Object Event where you want the trainer to stand
3. Set the object's **Script** field to a name like `MapName_EventScript_TrainerName`
4. Set their **Graphic** to the right sprite
5. Save in Porymap

### Step 3: Create the battle script in EVENTide

1. Open EVENTide and load the same map (Open Map button)
2. Select the NPC you just placed from the Object dropdown
3. Click **New NPC Script** → **Trainer**
4. A trainer picker appears — search for and select your trainer (e.g. `TRAINER_LASS_IRIS`)
5. EVENTide automatically creates:
   - The `trainerbattle_single` command linked to your trainer
   - Intro text label with placeholder dialogue
   - Defeat text label with placeholder dialogue
   - Post-battle text label
6. Double-click the Trainer Battle command to edit the dialogue text directly
7. Click **Save**

### Step 4: Build and test

The game should now compile and the trainer battle should work. If the trainer's intro says placeholder text, go back to step 3.6 and edit it.

---

## How do I add a regular NPC (non-trainer)?

### Simple NPC that just talks

1. Place the NPC in Porymap with a script name
2. In EVENTide, select the NPC → **New NPC Script** → **Simple Talker**
3. Double-click the message command to edit what they say
4. Save

### NPC that gives an item once

1. Place the NPC in Porymap
2. In EVENTide → **New NPC Script** → **Item Giver**
3. EVENTide auto-picks an unused flag and creates a 2-page script:
   - Page 1: Gives the item and sets the flag
   - Page 2: "Already got it" dialogue
4. Double-click the Give Item command to change what item they give
5. Save

### NPC that changes dialogue after a story event

1. Place the NPC in Porymap
2. In EVENTide → **New NPC Script** → **Flag-gated NPC**
3. EVENTide auto-picks an unused flag and creates 2 pages:
   - Page 1: What they say before the flag is set
   - Page 2: What they say after
4. Set the flag from another script (e.g. a boss battle or story trigger) using the `setflag` command
5. Save

---

## How do I edit an existing trainer's dialogue?

1. Open EVENTide, load the map
2. Select the trainer NPC from the Object dropdown
3. Double-click the **Trainer Battle** command in the command list
4. The edit dialog shows:
   - **Trainer dropdown**: Which trainer constant this battle uses
   - **Intro text**: Label name + editable text box showing what they say before the fight
   - **Defeat text**: Label name + editable text box showing what they say when they lose
5. Edit the text directly in the text boxes
6. Click OK, then Save

Text uses these control codes (same as the .inc files):
- `\n` = new line
- `\l` = scroll line (continues without clearing the textbox)
- `\p` = page break (clears the textbox and continues)
- `$` = end of string (required at the end)

---

## Common questions

### Why can't I edit trainer teams in EVENTide?

Trainer party data (which Pokémon, levels, moves, held items) is managed by PorySuite's Trainers tab, not EVENTide. EVENTide only handles the script side — the battle command that triggers the fight, and the dialogue text.

To change a trainer's party: open PorySuite → Trainers tab → find and edit the trainer.

### Why does the game crash when I add a trainer battle?

The `trainerbattle_single` command references text labels like `MapName_Text_TrainerIntro`. If those labels don't exist in `text.inc`, the game crashes.

EVENTide's templates auto-create these labels with placeholder text. If you're adding commands manually (not using the template), make sure the text labels you reference actually exist.

### I created a trainer in PorySuite but it doesn't show up in EVENTide's dropdown

EVENTide reads trainer constants from `include/constants/opponents.h` when the project loads. If you just added the trainer in PorySuite:
1. Save in PorySuite (this writes `opponents.h`)
2. Close and reopen EVENTide (or reload the project) so it picks up the new constants

### What's the difference between the trainer's name in PorySuite vs the text in EVENTide?

- **PorySuite's trainer name** (e.g. "IRIS") is the in-game trainer name shown during battle (above the health bar)
- **EVENTide's intro/defeat text** is what the trainer says before and after the fight (the dialogue boxes)

They're stored in completely different files and edited in different tools.

### Can I have the same trainer appear on multiple maps?

Yes. The trainer constant (e.g. `TRAINER_LASS_IRIS`) is defined once in PorySuite. You can reference it from `trainerbattle_single` commands on any map. Each map has its own text labels for that trainer's dialogue, so they can say different things on different maps.

### What does each page tab mean?

Each NPC can have multiple "pages" — these are separate script labels that the main script can jump to using `goto` or `goto_if_set`. Common patterns:
- **Before/after flag**: Page 1 checks a flag, jumps to Page 2 if set
- **Item giver**: Page 1 gives the item, Page 2 is the "already got it" text
- **Trainer**: Usually just one page (the battle command handles the state internally)

### How do I make an NPC that only appears after a certain point in the game?

That's handled in Porymap, not EVENTide. In the object event's properties, set a **Flag** field — the NPC only appears when that flag is set (or unset, depending on the flag type). Use `setflag` in another script to control when they show up.

---

## Tool workflow summary

```
Porymap                    PorySuite                  EVENTide
───────                    ─────────                  ────────
Place NPC on map     →     Create trainer data    →   Create battle script
Set position/sprite        Set party/AI/items         Set dialogue text
Set script name            Set trainer class           Link to trainer constant
                           Save → opponents.h          Save → scripts.inc + text.inc
                                  trainers.h
                                  trainer_parties.h
```

All three tools edit the same project folder. Changes are picked up when the other tools reload.
