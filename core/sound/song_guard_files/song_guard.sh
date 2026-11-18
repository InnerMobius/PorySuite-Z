#!/bin/sh
# song_guard.sh - keeps `make` from overwriting, deleting or ignoring song music.
#
# Installed into this project by PorySuite-Z's "Song build protection" setup,
# only with the project owner's permission. Remove it with the same setup's
# "Remove protection" button, which puts the three marked build lines back.
#
# WHY. Each song has two files: <song>.mid and <song>.s. make rebuilds the .s
# from the .mid whenever the .mid (or midi.cfg) has a NEWER FILE TIME, and
# file times say nothing about who wrote what: git sets them in checkout order,
# a pull rewrites midi.cfg, `make clean` deletes every .s. So edits made in the
# .s were overwritten, a .mid pulled from elsewhere could be ignored, and a
# song whose only real copy was its .s could become silence.
#
# HOW. Every .s this guard writes starts with one comment line:
#     @ song-guard v1 mid=<sha1 of the .mid> s=<fingerprint> flags: <flags>
# It records which .mid (and which midi.cfg flags) the .s was built from, and a
# fingerprint of what the .s held then (comments, blank lines and line endings
# ignored). The line travels with the .s - through git, to other computers -
# and needs no cache. From it:
#   * the .s still matches its fingerprint  -> it holds nothing its .mid
#     cannot rebuild; rebuilding it is safe.
#   * the .s does NOT match (it was edited)  -> it holds work of its own:
#       - its .mid and flags are unchanged   -> keep the .s (it is the newer work)
#       - its .mid or flags changed too      -> STOP: both sides hold work.
#   * s=-  means "the .s is this song's source" (chosen, or the .mid is an
#     empty stand-in): never rebuilt while its .mid is unchanged.
# A .s with no such line is checked by building the .mid aside and comparing;
# if they differ the guard cannot tell which is newer and STOPS.
#
# Nothing here judges by file times. A refusal changes nothing.
#
# Commands (run from the project folder):
#   sh song_guard.sh build <in.mid> <out.s> [flags...]   (make's song rule; converter in
#                                                         $SONG_GUARD_MID2AGB)
#   sh song_guard.sh pending <midi dir>     songs make must check although their times say "fine"
#   sh song_guard.sh clean <midi dir>       `make clean`: delete only rebuildable .s files
#   sh song_guard.sh status <midi dir>      what each song's record says (changes nothing)
#   sh song_guard.sh keep <midi dir> <song>     settle a song in favour of its .s
#   sh song_guard.sh rebuild <midi dir> <song>  settle a song in favour of its .mid
#                                               (the .s is copied to song_guard_backups/ first)
#   sh song_guard.sh fingerprint <file.s>...    diagnosis: the fingerprint of each .s
#   sh song_guard.sh canon [flags...]           diagnosis: flags in the order records use

LC_ALL=C
export LC_ALL
set -f

TAG='@ song-guard v1'
PLACEHOLDER_MAX=30
ME=${SONG_GUARD_SH:-song_guard.sh}

say() { printf 'song_guard: %s\n' "$*" >&2; }
die() { printf 'song_guard: %s\n' "$*" >&2; exit 1; }

if command -v sha1sum >/dev/null 2>&1; then
    SHA1=sha1sum
elif command -v shasum >/dev/null 2>&1; then
    SHA1='shasum -a 1'
else
    die "neither sha1sum nor shasum is installed, so songs cannot be checked. Nothing was changed."
fi

sha1_file() { $SHA1 < "$1" | cut -c1-40; }
EMPTY=$(printf '' | $SHA1 | cut -c1-40)      # the fingerprint of an .s with nothing in it

# What the assembler sees of an .s: no comments (@ ...), no trailing spaces,
# no blank lines, no carriage returns. PorySuite computes the same digest.
fingerprint() {
    awk '{ gsub(/\r/, ""); i = index($0, "@"); if (i) $0 = substr($0, 1, i - 1);
           sub(/[ \t\v\f]+$/, ""); if ($0 != "") print }' "$1" | $SHA1 | cut -c1-40
}

# Flags in a fixed order (mid2agb's options do not depend on order) - unless
# one option is given twice, when order can matter and is kept as written.
canon_flags() {
    printf '%s\n' "$@" | awk '
        NF { n++; w[n] = $1; k = substr($1, 1, 2); if (k in seen) dup = 1; seen[k] = 1 }
        END {
            if (!dup) for (i = 2; i <= n; i++) { v = w[i]; j = i - 1
                while (j > 0 && w[j] > v) { w[j + 1] = w[j]; j-- } w[j + 1] = v }
            s = ""; for (i = 1; i <= n; i++) s = s (i > 1 ? " " : "") w[i]; print s }'
}

# read_stamp FILE -> stamped=1 and st_mid / st_s / st_flags, or stamped=0
read_stamp() {
    stamped=0; st_mid=; st_s=; st_flags=
    first=
    IFS= read -r first < "$1" || [ -n "$first" ] || return 0
    first=$(printf '%s' "$first" | tr -d '\r')
    case "$first" in
        "$TAG mid="*" s="*" flags:"*) ;;
        *) return 0 ;;
    esac
    set -- $first
    [ "$4" != "${4#mid=}" ] && [ "$5" != "${5#s=}" ] && [ "$6" = "flags:" ] || return 0
    st_mid=${4#mid=}; st_s=${5#s=}
    shift 6
    st_flags=$*
    stamped=1
}

is_placeholder() {   # a .mid this small holds no music (PorySuite's empty stand-in)
    [ -f "$1" ] && [ "$(wc -c < "$1" | tr -d ' ')" -le "$PLACEHOLDER_MAX" ]
}

has_notes() { grep -Eq '(^|[^A-Za-z0-9_])(N[0-9][0-9]|TIE)([^A-Za-z0-9_]|$)' "$1"; }

# write_stamped RENDERED_S MID_SHA S_FIELD FLAGS DEST  (atomic: the old file
# stays whole until the new one is complete)
write_stamped() {
    # Byte-exact tools only (head/tail/cat/tr): Windows builds of grep, sed and
    # awk can drop the \r of CRLF lines, which would change the user's file.
    #
    # Line endings: when git's own rules say this file is stored with LF
    # (pret's .gitattributes: "*.s text eol=lf"), the file is written with LF,
    # which is exactly what git stores and checks out. mid2agb on Windows
    # writes CRLF; without this every rebuilt song showed up in git as a
    # change of line endings only. Otherwise the file keeps its own endings.
    eol=
    if [ "$(target_eol "$5")" = lf ]; then
        strip_cr=1
    else
        strip_cr=0
        if [ "$(head -c 65536 "$1" | tr -d -c '\r' | wc -c | tr -d ' ')" -gt 0 ]; then eol=$(printf '\r'); fi
    fi
    read_stamp "$1"
    { printf '%s mid=%s s=%s flags: %s%s\n' "$TAG" "$2" "$3" "$4" "$eol"
      if [ "$stamped" = 1 ]; then tail -n +2 "$1"; else cat "$1"; fi; } > "$5.song-guard-raw" \
        || { rm -f "$5.song-guard-raw"; return 1; }
    if [ "$strip_cr" = 1 ]; then
        tr -d '\r' < "$5.song-guard-raw" > "$5.song-guard-new" || { rm -f "$5.song-guard-raw" "$5.song-guard-new"; return 1; }
        rm -f "$5.song-guard-raw"
    else
        mv -f "$5.song-guard-raw" "$5.song-guard-new"
    fi
    mv -f "$5.song-guard-new" "$5"
}

# The line ending git stores this file with ("lf", "crlf", or something else
# when git has no rule or the project is not a git repository).
target_eol() {
    command -v git >/dev/null 2>&1 || { echo none; return; }
    git check-attr eol -- "$1" 2>/dev/null | sed -n 's/.*: eol: //p'
}

has_cr() { [ "$(head -c 65536 "$1" | tr -d -c '\r' | wc -c | tr -d ' ')" -gt 0 ]; }

TMPD=
cleanup() { [ -n "$TMPD" ] && rm -rf "$TMPD"; }
trap cleanup EXIT
trap 'cleanup; exit 130' INT TERM

# render IN LABEL FLAGS... -> $RENDER (the .s mid2agb makes, in a temp folder)
render() {
    r_in=$1; r_label=$2; shift 2
    [ -n "$SONG_GUARD_MID2AGB" ] || die "internal error: no song converter was given. Nothing was changed."
    TMPD=$(mktemp -d "${TMPDIR:-/tmp}/song_guard.XXXXXX") || die "cannot make a temporary folder. Nothing was changed."
    RENDER=$TMPD/$r_label.s
    # shellcheck disable=SC2086
    if ! $SONG_GUARD_MID2AGB "$r_in" "$RENDER" "$@" >"$TMPD/log" 2>&1 || [ ! -s "$RENDER" ]; then
        cat "$TMPD/log" >&2
        die "STOPPED - $r_label: the song converter could not convert $r_in (the .mid may be damaged, or a flag in midi.cfg is not one it knows). Its .s was not changed."
    fi
}

fixes() {   # how to settle song $1 in midi dir $2
    cat >&2 <<EOF
  Nothing was changed. Choose which file holds this song's music:
    keep the .s:           sh $ME keep $2 $1
    rebuild from the .mid: sh $ME rebuild $2 $1     (copies the .s to song_guard_backups/ first)
  Or open the project in PorySuite-Z and press Make, which asks the same question.
EOF
}

cmd_build() {
    [ $# -ge 2 ] || die "usage: build <in.mid> <out.s> [flags...]"
    in=$1; out=$2; shift 2
    label=$(basename "$out" .s); dir=$(dirname "$out")
    [ -f "$in" ] || die "STOPPED - $label: $in is missing. Its .s was not changed."
    cur=$(sha1_file "$in")
    cflags=$(canon_flags "$@")

    # An .s with nothing in it (emptied by a crash or a failed tool) holds no
    # music to lose: build it like a missing one.
    if [ -f "$out" ] && [ "$(fingerprint "$out")" = "$EMPTY" ]; then
        rm -f "$out"
    fi
    if [ ! -f "$out" ]; then
        render "$in" "$label" "$@"
        if is_placeholder "$in" && ! has_notes "$RENDER"; then
            say "WARNING - $label: its .s was missing and its .mid ($in) holds no notes, so it was built silent. If this song should have music, its .s was never committed - get it from the computer it was written on."
        fi
        write_stamped "$RENDER" "$cur" "$(fingerprint "$RENDER")" "$cflags" "$out" \
            || die "STOPPED - $label: could not write $out."
        return 0
    fi

    read_stamp "$out"
    if [ "$stamped" = 1 ]; then
        body=$(fingerprint "$out")
        pristine=0; [ "$st_s" != "-" ] && [ "$body" = "$st_s" ] && pristine=1
        same=0; [ "$st_mid" = "$cur" ] && [ "$st_flags" = "$cflags" ] && same=1
        if [ "$pristine" = 1 ] && [ "$same" = 1 ]; then
            # Already exactly what this .mid and these flags build (a re-dated
            # .mid, a rewritten midi.cfg). Nothing to convert; mark the target
            # remade so make stops asking. A file git stores with LF but that
            # has CRLF on disk is rewritten with LF instead (same content), so
            # the touch does not make git list it as changed.
            if [ "$(target_eol "$out")" = lf ] && has_cr "$out"; then
                write_stamped "$out" "$st_mid" "$st_s" "$st_flags" "$out" \
                    || die "STOPPED - $label: could not write $out."
            else
                touch "$out"
            fi
            return 0
        fi
        if [ "$pristine" = 0 ] && [ "$same" = 1 ]; then
            say "kept $out - it holds work its .mid does not, and $in and its flags are unchanged since the .s was built from them."
            return 0
        fi
        render "$in" "$label" "$@"
        new=$(fingerprint "$RENDER")
        if [ "$pristine" = 1 ] || [ "$new" = "$body" ]; then
            write_stamped "$RENDER" "$cur" "$new" "$cflags" "$out" \
                || die "STOPPED - $label: could not write $out."
            return 0
        fi
        if is_placeholder "$in" && has_notes "$out"; then
            # An empty stand-in .mid holds no music to lose (e.g. a song copied
            # from another one, whose record names that song's .mid).
            write_stamped "$out" "$cur" "-" "$cflags" "$out" \
                || die "STOPPED - $label: could not write $out."
            say "kept $out - its .mid is an empty stand-in, so the .s is this song's only copy (now recorded as its source)."
            return 0
        fi
        what="its .mid"
        [ "$st_mid" = "$cur" ] && what="its midi.cfg flags (now: $cflags; then: $st_flags)"
        [ "$st_mid" != "$cur" ] && [ "$st_flags" != "$cflags" ] && what="its .mid and its midi.cfg flags"
        say "STOPPED - $label: BOTH of its files hold music the other does not."
        say "  $out was edited after it was built, and $what changed since then too."
        say "  Rebuilding would lose the .s edits; skipping it would ignore the .mid change."
        fixes "$label" "$dir"
        exit 1
    fi

    # No record: build the .mid aside and compare.
    render "$in" "$label" "$@"
    new=$(fingerprint "$RENDER")
    if [ "$new" = "$(fingerprint "$out")" ]; then
        write_stamped "$RENDER" "$cur" "$new" "$cflags" "$out" \
            || die "STOPPED - $label: could not write $out."
        return 0
    fi
    if is_placeholder "$in" && has_notes "$out"; then
        write_stamped "$out" "$cur" "-" "$cflags" "$out" \
            || die "STOPPED - $label: could not write $out."
        say "kept $out - its .mid is an empty stand-in, so the .s is this song's only copy (now recorded as its source)."
        return 0
    fi
    say "STOPPED - $label: its .s holds music its .mid does not make, and there is no record of"
    say "  which one is newer (the .s has no song-guard line - it was written by something else)."
    fixes "$label" "$dir"
    exit 1
}

# One line per midi.cfg song: label, canonical flags. Same parse as PorySuite.
cfg_songs() {
    tr -d '\r' < "$1/midi.cfg" | awk '
        { sub(/#.*/, ""); c = index($0, ":"); if (!c) next
          head = substr($0, 1, c - 1); gsub(/^[ \t]+|[ \t]+$/, "", head)
          if (head !~ /\.mid$/) next
          label = substr(head, 1, length(head) - 4)
          n = split(substr($0, c + 1), w, " ")
          dup = 0; delete seen
          for (i = 1; i <= n; i++) { k = substr(w[i], 1, 2); if (k in seen) dup = 1; seen[k] = 1 }
          if (!dup) for (i = 2; i <= n; i++) { v = w[i]; j = i - 1
              while (j > 0 && w[j] > v) { w[j + 1] = w[j]; j-- } w[j + 1] = v }
          s = ""; for (i = 1; i <= n; i++) s = s (i > 1 ? " " : "") w[i]
          print label "\t" s }'
}

cmd_pending() {
    dir=$1
    [ -f "$dir/midi.cfg" ] || { echo song-guard-ok; return 0; }
    {
        echo "#CFG"; cfg_songs "$dir"
        echo "#MID"
        ( cd "$dir" && set +f && set -- *.mid && [ -e "$1" ] && $SHA1 -- "$@" )
        echo "#SN"
        ( cd "$dir" && set +f && for f in *.s; do [ -e "$f" ] && printf '%s\n' "$f"; done )
        echo "#S"
        ( cd "$dir" && set +f && set -- *.s && [ -e "$1" ] &&
          awk 'FNR == 1 { gsub(/\r/, ""); print FILENAME "\t" $0; nextfile }' "$@" )
    } | awk -v tag="$TAG" '
        /^#CFG$/ { sec = 1; next } /^#MID$/ { sec = 2; next } /^#S$/ { sec = 3; next }
        /^#SN$/ { sec = 4; next }
        sec == 4 { f = $0; sub(/\.s$/, "", f); have[f] = 1; next }
        sec == 1 { split($0, a, "\t"); flags[a[1]] = a[2]; order[++n] = a[1]; next }
        sec == 2 { h = $1; f = $0; sub(/^[^ ]+ +\*?/, "", f); sub(/\.mid$/, "", f); mid[f] = h; next }
        sec == 3 { t = index($0, "\t"); f = substr($0, 1, t - 1); sub(/\.s$/, "", f)
                   have[f] = 1; first[f] = substr($0, t + 1); next }
        END {
            for (i = 1; i <= n; i++) {
                l = order[i]
                if (!(l in mid) || !(l in have)) continue
                line = first[l]
                if (index(line, tag " mid=") != 1) { print l; continue }
                m = line; sub(/^.* mid=/, "", m); sub(/ .*$/, "", m)
                f = line; sub(/^.* flags: ?/, "", f)
                if (m != mid[l] || f != flags[l]) print l
            }
        }'
    # A song whose .mid is an empty stand-in has its music only in the .s. If
    # git ignores that .s, the song exists on this computer alone: say so on
    # every build, before it is committed without its music.
    ( cd "$dir" && set +f && wc -c *.mid 2>/dev/null ) |
        awk -v max="$PLACEHOLDER_MAX" '$2 != "total" && $1 <= max { sub(/\.mid$/, "", $2); print $2 }' |
        while read -r l; do
            [ -f "$dir/$l.s" ] && has_notes "$dir/$l.s" || continue
            if git check-ignore -q "$dir/$l.s" 2>/dev/null; then
                say "WARNING - $l: its .mid is an empty stand-in, so its music is only in $dir/$l.s - and git ignores that file. Committing now would leave other computers with silence. Open the project in PorySuite-Z once (it writes a real .mid), or commit the .s."
            fi
        done
    echo song-guard-ok
}

cmd_clean() {
    dir=$1
    removed=0; kept=
    set +f
    for s in "$dir"/*.s; do
        [ -e "$s" ] || continue
        label=$(basename "$s" .s); mid=$dir/$label.mid
        read_stamp "$s"
        why=
        if [ "$stamped" != 1 ]; then
            why="no song-guard line, so it cannot be shown to be rebuildable"
        elif [ "$st_s" = "-" ]; then
            why="recorded as this song's source"
        elif [ ! -f "$mid" ]; then
            why="it has no .mid to rebuild it from"
        elif [ "$(fingerprint "$s")" != "$st_s" ]; then
            why="it holds edits its .mid does not"
        elif is_placeholder "$mid" && has_notes "$s"; then
            why="its .mid is an empty stand-in"
        elif is_placeholder "$mid"; then
            why=quiet      # a silent song from an empty .mid: nothing to clean, and
                           # keeping it spares the next build a "built silent" warning
        fi
        if [ "$why" = quiet ]; then
            :
        elif [ -z "$why" ]; then
            rm -f "$s" && removed=$((removed + 1))
        else
            kept="$kept
  kept $s - $why"
        fi
    done
    rm -f "$dir"/*.song-guard-new "$dir"/*.song-guard-raw   # half-written copies from an interrupted build
    set -f
    say "make clean: removed $removed song .s file(s) that their .mid rebuilds exactly."
    if [ -n "$kept" ]; then
        say "make clean: KEPT these, because deleting them would lose music:$kept"
    fi
    return 0
}

cmd_status() {
    dir=$1
    set +f
    cfg_songs "$dir" | while IFS="$(printf '\t')" read -r label flags; do
        s=$dir/$label.s; mid=$dir/$label.mid
        [ -f "$mid" ] || continue
        if [ ! -f "$s" ]; then echo "$label: no .s yet - make builds it from its .mid"; continue; fi
        read_stamp "$s"
        if [ "$stamped" != 1 ]; then echo "$label: no record - the next make checks it against its .mid"; continue; fi
        cur=$(sha1_file "$mid")
        same=0; [ "$st_mid" = "$cur" ] && [ "$st_flags" = "$flags" ] && same=1
        pristine=0; [ "$st_s" != "-" ] && [ "$(fingerprint "$s")" = "$st_s" ] && pristine=1
        if [ "$pristine" = 1 ] && [ "$same" = 1 ]; then echo "$label: in sync"
        elif [ "$pristine" = 1 ]; then echo "$label: its .mid changed - the next make rebuilds the .s"
        elif [ "$same" = 1 ]; then echo "$label: its .s holds work of its own - kept"
        else echo "$label: CONFLICT - both files changed; the next make stops until it is settled"
        fi
    done
}

cmd_keep() {
    dir=$1; label=$2
    [ -n "$label" ] || die "usage: keep <midi dir> <song>"
    s=$dir/$label.s; mid=$dir/$label.mid
    [ -f "$s" ] || die "$s does not exist."
    [ -f "$mid" ] || die "$mid does not exist."
    line=$(cfg_songs "$dir" | awk -F '\t' -v l="$label" '$1 == l { print; found = 1 } END { if (!found) exit 1 }') \
        || die "$label has no line in $dir/midi.cfg."
    flags=${line#*"$(printf '\t')"}
    write_stamped "$s" "$(sha1_file "$mid")" "-" "$flags" "$s" || die "could not write $s."
    say "$label: its .s is now recorded as the song's source. make will keep it until its .mid or flags change again (then it asks again)."
}

cmd_rebuild() {
    dir=$1; label=$2
    [ -n "$label" ] || die "usage: rebuild <midi dir> <song>"
    s=$dir/$label.s
    [ -f "$dir/$label.mid" ] || die "$dir/$label.mid does not exist, so the song cannot be rebuilt from it."
    if [ -f "$s" ]; then
        b=song_guard_backups/$(date +%Y%m%d-%H%M%S)
        mkdir -p "$b" && cp -p "$s" "$b/$label.s" && cmp -s "$s" "$b/$label.s" \
            || die "could not back up $s, so it was left alone."
        rm -f "$s"
        say "$label: its .s was copied to $b/$label.s and removed; the next make rebuilds it from its .mid."
    else
        say "$label: it has no .s; the next make builds it from its .mid."
    fi
}

cmd=$1
[ $# -gt 0 ] && shift
case "$cmd" in
    build) cmd_build "$@" ;;
    pending) cmd_pending "$@" ;;
    clean) cmd_clean "$@" ;;
    status) cmd_status "$@" ;;
    keep) cmd_keep "$@" ;;
    rebuild) cmd_rebuild "$@" ;;
    fingerprint) for f in "$@"; do printf '%s %s\n' "$(fingerprint "$f")" "$f"; done ;;
    canon) canon_flags "$@" ;;
    *) die "unknown command '$cmd' (build, pending, clean, status, keep, rebuild)" ;;
esac
