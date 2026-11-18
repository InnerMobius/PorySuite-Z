# song_guard.mk - song build protection (see song_guard.sh for what it does).
#
# Installed by PorySuite-Z's "Song build protection" setup, only with the
# project owner's permission. It is used by three marked lines elsewhere:
#   audio_rules.mk  include song_guard.mk          (this file)
#   audio_rules.mk  $(SONG_GUARD) $$< $$@ $2        (was: $(MID) $$< $$@ $2)
#   Makefile        $(SONG_GUARD_CLEAN)            (was: rm -f $(MID_SUBDIR)/*.s)
# PorySuite's "Remove protection" button puts them back; doing it by hand is
# the same three edits plus deleting song_guard.mk and song_guard.sh.

SONG_GUARD_SH := song_guard.sh
SONG_GUARD = SONG_GUARD_MID2AGB='$(MID)' SONG_GUARD_SH='$(SONG_GUARD_SH)' $(SHELL) $(SONG_GUARD_SH) build
SONG_GUARD_CLEAN = SONG_GUARD_SH='$(SONG_GUARD_SH)' $(SHELL) $(SONG_GUARD_SH) clean $(MID_SUBDIR)

# Songs whose record says their .s may be out of step with their .mid - a
# pull, a branch switch or another program changed one of them - are checked
# on every build, whatever their file times say. Skipped when every goal is a
# clean/tidy-type rule.
_SONG_GUARD_GOALS := $(if $(MAKECMDGOALS),$(filter-out $(RULES_NO_SCAN) song-keep song-rebuild song-status,$(MAKECMDGOALS)),all)
ifneq (,$(_SONG_GUARD_GOALS))
SONG_GUARD_PENDING := $(shell SONG_GUARD_SH='$(SONG_GUARD_SH)' $(SHELL) $(SONG_GUARD_SH) pending $(MID_SUBDIR))
ifeq (,$(filter song-guard-ok,$(SONG_GUARD_PENDING)))
$(error song_guard: the song check could not run (see above), so this build could overwrite or ignore song music. Nothing was built)
endif
SONG_GUARD_PENDING := $(filter-out song-guard-ok,$(SONG_GUARD_PENDING))
ifneq (,$(SONG_GUARD_PENDING))
$(patsubst %,$(MID_SUBDIR)/%.s,$(SONG_GUARD_PENDING)): song-guard-check
endif
endif

.PHONY: song-guard-check song-keep song-rebuild song-status
song-guard-check: ;

# A song .s is never deleted by make itself (an interrupted or failed build).
.PRECIOUS: $(patsubst %.mid,%.s,$(wildcard $(MID_SUBDIR)/*.mid))

# Settling a song the guard stopped on:  make song-keep SONG=mus_x  /  make song-rebuild SONG=mus_x
song-keep: ; @SONG_GUARD_SH='$(SONG_GUARD_SH)' $(SHELL) $(SONG_GUARD_SH) keep $(MID_SUBDIR) $(SONG)
song-rebuild: ; @SONG_GUARD_SH='$(SONG_GUARD_SH)' $(SHELL) $(SONG_GUARD_SH) rebuild $(MID_SUBDIR) $(SONG)
song-status: ; @SONG_GUARD_SH='$(SONG_GUARD_SH)' $(SHELL) $(SONG_GUARD_SH) status $(MID_SUBDIR)
