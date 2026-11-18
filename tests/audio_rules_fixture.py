"""Throwaway project build files for music tests.

``UPSTREAM_AUDIO_RULES`` is pret's song rule exactly as pokefirered and
pokeemerald ship it in ``audio_rules.mk`` (every song depends on the whole
midi.cfg). ``write_project`` gives a test project a Makefile shaped like
pret's (MID_SUBDIR = sound/songs/midi, ``include audio_rules.mk``) so
PorySuite recognises its build rule the way it would a real project's.
"""
import os

UPSTREAM_AUDIO_RULES = """\
# This file contains rules for making assemblies for most music in the game.

MID_ASM_DIR := $(MID_SUBDIR)

# For each line in midi.cfg, we do some trickery to convert it into a make rule for the `.mid` file described on the line
# Data following the colon in said file corresponds to arguments passed into mid2agb
MID_CFG_PATH := $(MID_SUBDIR)/midi.cfg

# $1: Source path no extension, $2 Options
define MID_RULE
$(MID_ASM_DIR)/$1.s: $(MID_SUBDIR)/$1.mid $(MID_CFG_PATH)
\t$(MID) $$< $$@ $2
endef
#                            source path,                             remaining text (options)
define MID_EXPANSION
\t$(eval $(call MID_RULE,$(basename $(patsubst %:,%,$(word 1,$1))),$(wordlist 2,999,$1)))
endef

$(foreach line,$(shell cat $(MID_CFG_PATH) | sed "s/ /__SPACE__/g"),$(call MID_EXPANSION,$(subst __SPACE__, ,$(line))))

# Warn users building without a .cfg - build will fail at link time
$(MID_ASM_DIR)/%.s: $(MID_SUBDIR)/%.mid
\t$(warning $< does not have an associated entry in midi.cfg! It cannot be built)
"""


def write_project(root: str, rules: str = UPSTREAM_AUDIO_RULES, mid_tool: str = "echo") -> None:
    """Makefile + audio_rules.mk for a throwaway project at *root*. `make all`
    builds every song .s that has a .mid; MID is *mid_tool*."""
    with open(os.path.join(root, "audio_rules.mk"), "w", newline="\n") as f:
        f.write(rules)
    with open(os.path.join(root, "Makefile"), "w", newline="\n") as f:
        f.write("MID_SUBDIR = sound/songs/midi\n"
                f"MID := {mid_tool}\n"
                "include audio_rules.mk\n"
                "all: $(patsubst %.mid,%.s,$(wildcard sound/songs/midi/*.mid))\n")
