# EVENTide command whitelist with human-readable labels

Commands not listed here fall back to a generic label generated from the command
name so every entry in the editor has a descriptive title.

whitelisted_commands = {
    # Warps & Teleportation
    "warp": "Warp Player",
    "warpsilent": "Warp Player (Silent)",
    "warpdoor": "Warp Through Door",
    "warphole": "Fall Through Hole",
    "warpteleport": "Teleport Player",

    # Items
    "additem": "Give Item",
    "removeitem": "Remove Item",
    "checkitem": "Check for Item",
    "checkitemspace": "Check for Item Space",

    # Pokémon & Battles
    "givemon": "Give Pokémon",
    "giveegg": "Give Egg",
    "setmonmove": "Set Pokémon Move",
    "checkpartymove": "Check Party for Move",
    "trainerbattle": "Start Trainer Battle",
    "dotrainerbattle": "Force Trainer Battle",
    "setwildbattle": "Prepare Wild Battle",
    "dowildbattle": "Start Wild Battle",

    # NPC & Object Control
    "applymovement": "Apply Movement to NPC",
    "applymovementat": "Apply Movement at Coordinates",
    "waitmovement": "Wait for Movement",
    "removeobject": "Remove NPC/Object",
    "addobject": "Add NPC/Object",
    "showobjectat": "Show NPC/Object",
    "hideobjectat": "Hide NPC/Object",
    "faceplayer": "Make NPC Face Player",
    "turnobject": "Turn NPC",
    "setobjectxy": "Move NPC",
    "setobjectxyperm": "Move NPC Permanently",

    # Dialogue & Text
    "message": "Show Message",
    "messageautoscroll": "Auto-Scroll Message",
    "waitmessage": "Wait for Message",
    "closemessage": "Close Message",
    "yesnobox": "Yes/No Choice",
    "multichoice": "Multi-Choice Box",
    "multichoicedefault": "Multi-Choice Box (Default Selection)",
    "multichoicegrid": "Multi-Choice Grid",

    # Sound & Music
    "playse": "Play Sound Effect",
    "waitse": "Wait for Sound Effect",
    "playfanfare": "Play Fanfare",
    "waitfanfare": "Wait for Fanfare",
    "playbgm": "Play Music",
    "fadeoutbgm": "Fade Out Music",
    "fadeinbgm": "Fade In Music",
    "fadescreen": "Fade Screen",
    "fadescreenspeed": "Fade Screen (Set Speed)",

    # Weather & Effects
    "setweather": "Set Weather",
    "doweather": "Trigger Weather Effect",
    "resetweather": "Reset Weather",
    "setflashlevel": "Set Flash Level",
    "animateflash": "Flash Animation",
    "dofieldeffect": "Trigger Field Effect",

    # Movement Locking
    "lockall": "Lock All Movement",
    "lock": "Lock Player Movement",
    "releaseall": "Release All Movement",
    "release": "Release Player Movement",

    # Waiting
    "waitbuttonpress": "Wait for Button Press",
    "delay": "Wait (Timed Delay)",

    # Flags & Variables
    "setflag": "Set Flag",
    "clearflag": "Clear Flag",
    "checkflag": "Check Flag",

    # Money & Coins
    "addmoney": "Give Money",
    "removemoney": "Take Money",
    "checkmoney": "Check Money",
    "addcoins": "Give Coins",
    "removecoins": "Take Coins",
    "checkcoins": "Check Coins",

    # Misc
    "setrespawn": "Set Respawn Point",
    "playmoncry": "Play Pokémon Cry",
    "end": "End Script",
    "return": "Return from Script",
"call": "Call Script",
}

## Common Movement Labels

EVENTide replaces certain movement script labels with shorter descriptions in
the command list:

```
Common_Movement_FaceOriginalDirection -> Face Original Direction
Common_Movement_FacePlayer            -> Face Player
Common_Movement_FaceAwayPlayer        -> Face Away From Player
Common_Movement_Delay32               -> Delay 32 Frames
Common_Movement_Delay48               -> Delay 48 Frames
Common_Movement_ExclamationMark       -> Exclamation Mark
Common_Movement_QuestionMark          -> Question Mark
Common_Movement_WalkInPlaceFasterDown -> Walk In Place Faster Down
Common_Movement_WalkInPlaceFasterUp   -> Walk In Place Faster Up
Common_Movement_WalkInPlaceFasterLeft -> Walk In Place Faster Left
Common_Movement_WalkInPlaceFasterRight-> Walk In Place Faster Right
```
