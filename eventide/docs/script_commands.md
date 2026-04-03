# FireRed Script Command Reference

This document lists the bytecode commands used in the FireRed scripting engine. Each command is indexed by its byte value (in hex) followed by a short human readable name derived from the corresponding function in `scrcmd.c`.

The names give a general indication of what each command does (for example, `setflag` sets an in-game flag). These commands can be used to implement cutscenes and events and will serve as the building blocks for the planned visual scripting tool.

```
0x00 - nop
0x01 - nop1
0x02 - end
0x03 - return
0x04 - call
0x05 - goto
0x06 - goto_if
0x07 - call_if
0x08 - gotostd
0x09 - callstd
0x0a - gotostd_if
0x0b - callstd_if
0x0c - returnram
0x0d - endram
0x0e - setmysteryeventstatus
0x0f - loadword
0x10 - loadbyte
0x11 - setptr
0x12 - loadbytefromptr
0x13 - setptrbyte
0x14 - copylocal
0x15 - copybyte
0x16 - setvar
0x17 - addvar
0x18 - subvar
0x19 - copyvar
0x1a - setorcopyvar
0x1b - compare_local_to_local
0x1c - compare_local_to_value
0x1d - compare_local_to_ptr
0x1e - compare_ptr_to_local
0x1f - compare_ptr_to_value
0x20 - compare_ptr_to_ptr
0x21 - compare_var_to_value
0x22 - compare_var_to_var
0x23 - callnative
0x24 - gotonative
0x25 - special
0x26 - specialvar
0x27 - waitstate
0x28 - delay
0x29 - setflag
0x2a - clearflag
0x2b - checkflag
0x2c - initclock
0x2d - dotimebasedevents
0x2e - gettime
0x2f - playse
0x30 - waitse
0x31 - playfanfare
0x32 - waitfanfare
0x33 - playbgm
0x34 - savebgm
0x35 - fadedefaultbgm
0x36 - fadenewbgm
0x37 - fadeoutbgm
0x38 - fadeinbgm
0x39 - warp
0x3a - warpsilent
0x3b - warpdoor
0x3c - warphole
0x3d - warpteleport
0x3e - setwarp
0x3f - setdynamicwarp
0x40 - setdivewarp
0x41 - setholewarp
0x42 - getplayerxy
0x43 - getpartysize
0x44 - additem
0x45 - removeitem
0x46 - checkitemspace
0x47 - checkitem
0x48 - checkitemtype
0x49 - addpcitem
0x4a - checkpcitem
0x4b - adddecoration
0x4c - removedecoration
0x4d - checkdecor
0x4e - checkdecorspace
0x4f - applymovement
0x50 - applymovementat
0x51 - waitmovement
0x52 - waitmovementat
0x53 - removeobject
0x54 - removeobjectat
0x55 - addobject
0x56 - addobjectat
0x57 - setobjectxy
0x58 - showobjectat
0x59 - hideobjectat
0x5a - faceplayer
0x5b - turnobject
0x5c - trainerbattle
0x5d - dotrainerbattle
0x5e - gotopostbattlescript
0x5f - gotobeatenscript
0x60 - checktrainerflag
0x61 - settrainerflag
0x62 - cleartrainerflag
0x63 - setobjectxyperm
0x64 - copyobjectxytoperm
0x65 - setobjectmovementtype
0x66 - waitmessage
0x67 - message
0x68 - closemessage
0x69 - lockall
0x6a - lock
0x6b - releaseall
0x6c - release
0x6d - waitbuttonpress
0x6e - yesnobox
0x6f - multichoice
0x70 - multichoicedefault
0x71 - multichoicegrid
0x72 - drawbox
0x73 - erasebox
0x74 - drawboxtext
0x75 - showmonpic
0x76 - hidemonpic
0x77 - showcontestpainting
0x78 - braillemessage
0x79 - givemon
0x7a - giveegg
0x7b - setmonmove
0x7c - checkpartymove
0x7d - bufferspeciesname
0x7e - bufferleadmonspeciesname
0x7f - bufferpartymonnick
0x80 - bufferitemname
0x81 - bufferdecorationname
0x82 - buffermovename
0x83 - buffernumberstring
0x84 - bufferstdstring
0x85 - bufferstring
0x86 - pokemart
0x87 - pokemartdecoration
0x88 - pokemartdecoration2
0x89 - playslotmachine
0x8a - setberrytree
0x8b - choosecontestmon
0x8c - startcontest
0x8d - showcontestresults
0x8e - contestlinktransfer
0x8f - random
0x90 - addmoney
0x91 - removemoney
0x92 - checkmoney
0x93 - showmoneybox
0x94 - hidemoneybox
0x95 - updatemoneybox
0x96 - getpokenewsactive
0x97 - fadescreen
0x98 - fadescreenspeed
0x99 - setflashlevel
0x9a - animateflash
0x9b - messageautoscroll
0x9c - dofieldeffect
0x9d - setfieldeffectargument
0x9e - waitfieldeffect
0x9f - setrespawn
0xa0 - checkplayergender
0xa1 - playmoncry
0xa2 - setmetatile
0xa3 - resetweather
0xa4 - setweather
0xa5 - doweather
0xa6 - setstepcallback
0xa7 - setmaplayoutindex
0xa8 - setobjectsubpriority
0xa9 - resetobjectsubpriority
0xaa - createvobject
0xab - turnvobject
0xac - opendoor
0xad - closedoor
0xae - waitdooranim
0xaf - setdooropen
0xb0 - setdoorclosed
0xb1 - addelevmenuitem
0xb2 - showelevmenu
0xb3 - checkcoins
0xb4 - addcoins
0xb5 - removecoins
0xb6 - setwildbattle
0xb7 - dowildbattle
0xb8 - setvaddress
0xb9 - vgoto
0xba - vcall
0xbb - vgoto_if
0xbc - vcall_if
0xbd - vmessage
0xbe - vbuffermessage
0xbf - vbufferstring
0xc0 - showcoinsbox
0xc1 - hidecoinsbox
0xc2 - updatecoinsbox
0xc3 - incrementgamestat
0xc4 - setescapewarp
0xc5 - waitmoncry
0xc6 - bufferboxname
0xc7 - textcolor
0xc8 - loadhelp
0xc9 - unloadhelp
0xca - signmsg
0xcb - normalmsg
0xcc - comparestat
0xcd - setmonmodernfatefulencounter
0xce - checkmonmodernfatefulencounter
0xcf - trywondercardscript
0xd0 - setworldmapflag
0xd1 - warpspinenter
0xd2 - setmonmetlocation
0xd3 - getbraillestringwidth
0xd4 - bufferitemnameplural
```

The list above captures all script commands found in `data/script_cmd_table.inc` at the time of writing. Refer to `scrcmd.c` for implementation details of each command.

## Map Script Workflow

A FireRed map is stored in a directory under `data/maps/` containing three files:
`map.json`, `scripts.inc`, and `text.inc`. The JSON file describes the map layout
and declares every object, warp, or background event. Each event references a
script label defined in `scripts.inc`. Any dialog those scripts display is kept in
`text.inc`.

`data/event_scripts.s` includes every `scripts.inc` and `text.inc` file so that
all map scripts become part of the final build. When the project is built the
assembler reads the commands listed above, using macros from
`asm/macros/event.inc` and constants from the `include/constants/` directory.

Event objects in `map.json` have flags that control when they appear. Scripts can
set or clear these flags with commands like `setflag` and `clearflag` to hide or
show NPCs. Objects also specify a facing direction in the JSON which determines
how the sprite is oriented when the map loads. Movement commands in the script
can temporarily change that direction while the event runs.

A typical workflow when editing a map is:

1. Update `map.json` to add or modify events and assign script labels.
2. Implement those labels in `scripts.inc` using the command list above.
3. Add any new text to `text.inc` so the scripts can reference it with `msgbox` or
   related commands.
4. Rebuild the project. The build system assembles the scripts and regenerates
   headers so the game can reference the new events correctly.

These notes should help future contributors understand how FireRed's map scripts
are structured and how the flags and facing values tie into the event data.
