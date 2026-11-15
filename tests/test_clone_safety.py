"""Clone safety: build inputs that a fresh clone would not have.

The bug this guards against
---------------------------
A decomp `.gitignore` ignores build output on purpose — compiled graphics,
compiled audio samples, the `build/` tree. That is right, because `make`
regenerates them from tracked sources. It stops being right the moment a file
the build *reads* is ignored and has no tracked source behind it. The project
then builds on the one machine holding that file and nowhere else.

Measured on the owner's project: of 8,567 assets the build pulls in with
`.incbin`, exactly one was in that state —
`sound/direct_sound_samples/cries/dummy.bin`, hand-dropped months earlier,
covered by the stock `sound/**/*.bin` rule, with no `.wav` anywhere to rebuild
it from. It surfaced on a second computer as a bare
`make: *** [audio_rules.mk:21: .../dummy.bin] Error 1`.

Why these tests look the way they do
------------------------------------
Three earlier attempts at this check reported 5,468, then 405, then 7 files.
Every one of those was wrong, and wrong the same way: the checker assumed
`X.4bpp` is built from `X.png`, so it flagged the files pokefirered stitches
out of unrelated inputs — `hp_numbers.4bpp` from `hp_bar_anim.4bpp` plus two
number strips, `obi_palpak1.gbapal` from three `old_pal*.gbapal`. The fix was
to stop guessing and read the project's own rule files.

So a test that merely asserts "no findings" proves nothing here — silence was
exactly what three broken versions produced once their thresholds were tuned.
Every silent case below is therefore paired with a mutation that must break
it. If a test asserting silence cannot be made to speak by removing the source
it claims is present, it is not testing anything.

DO NOT REGRESS:
  * `_EMPTY_PATTERN_RULES` must stay out of the producer set. pokefirered has
    `%.png: ;`, `%.s: ;`, `%.pal: ;`, `%.wav: ;` whose only job is to stop
    make searching implicit rules. Counting them as producers declares every
    `.png` buildable out of thin air and silences the entire check — see
    `test_empty_pattern_rule_is_not_a_producer`.
  * A rule whose target does not expand must stay LOOSE, not be dropped.
    Dropping is the loud direction: the target then looks unbuildable and gets
    reported. See `test_unresolvable_target_silences_rather_than_inventing`.
  * `:=` must expand its right-hand side at assignment time. pokefirered's
    `config.mk` has `BUILD_NAME := $(BUILD_NAME)_modern` inside a conditional
    this parser does not evaluate; expanding lazily recursed into a
    multi-kilobyte `_modern_modern_modern…` string and cost 3.6s of the 4.4s
    runtime. See `test_self_referential_variable_does_not_explode`.
"""

from __future__ import annotations

import os
import subprocess
import textwrap

import pytest

from core import clone_safety


# ───────────────────────────── helpers ──────────────────────────────────────

_GIT_OK = None


def _git_available() -> bool:
    global _GIT_OK
    if _GIT_OK is None:
        try:
            subprocess.run(["git", "--version"], capture_output=True,
                           check=True)
            _GIT_OK = True
        except (OSError, subprocess.CalledProcessError):
            _GIT_OK = False
    return _GIT_OK


requires_git = pytest.mark.skipif(
    not _git_available(), reason="git is not on PATH")


def _make_repo(root, *, files: dict[str, str], track: list[str],
               gitignore: str = "") -> str:
    """Build a throwaway git repo.

    *files* are written to disk; only the paths in *track* are committed, so
    "tracked" and "on disk" can differ exactly the way they do in the bug.
    """
    root = str(root)
    for rel, content in files.items():
        path = os.path.join(root, rel.replace("/", os.sep))
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(textwrap.dedent(content))

    if gitignore:
        with open(os.path.join(root, ".gitignore"), "w",
                  encoding="utf-8", newline="\n") as fh:
            fh.write(textwrap.dedent(gitignore))

    run = lambda *a: subprocess.run(  # noqa: E731
        ["git", *a], cwd=root, capture_output=True, text=True, check=True)
    run("init", "-q")
    run("config", "user.email", "t@example.invalid")
    run("config", "user.name", "test")
    run("config", "commit.gpgsign", "false")
    for rel in track:
        run("add", "-f", "--", rel)
    run("commit", "-q", "-m", "fixture")
    return root


#: The tab-indented recipe that most pokefirered rules use. Written as an
#: explicit "\t" so no editor or formatter can turn it into spaces — that
#: would silently change what the parser sees.
_RECIPE = "\t@cat $^ >$@\n"


def _makefile(body: str) -> str:
    """A minimal Makefile carrying the generic rules pokefirered relies on."""
    return (
        "%.png: ;\n"
        "%.pal: ;\n"
        "%.wav: ;\n"
        "%.4bpp:   %.png  ; gfx $< $@\n"
        "%.gbapal: %.pal  ; gfx $< $@\n"
        "%.lz:     %      ; gfx $< $@\n"
        + body
    )


def _paths(report) -> list[str]:
    return [f.path for f in report.findings]


# ──────────────────────── the bug, reproduced ───────────────────────────────

@requires_git
def test_orphan_binary_is_reported(tmp_path):
    """The dummy.bin shape: on disk, ignored, no source, nothing can make it."""
    root = _make_repo(
        tmp_path,
        files={
            "Makefile": _makefile(
                "sound/%.bin: sound/%.wav\n\t@wav2agb $< $@\n"),
            "sound/data.inc": '.incbin "sound/cries/dummy.bin"\n',
            "sound/cries/dummy.bin": "binary",
        },
        track=["Makefile", "sound/data.inc"],
        gitignore="sound/**/*.bin\n",
    )
    report = clone_safety.check(root)

    assert report.ran, report.skipped_reason
    assert _paths(report) == ["sound/cries/dummy.bin"]

    finding = report.findings[0]
    assert finding.ignored is True
    assert finding.on_disk is True
    assert finding.referenced_by == "sound/data.inc"
    assert "gitignore" in finding.describe()


@requires_git
def test_tracked_source_makes_it_silent(tmp_path):
    """Same project, with the .wav committed — the real cries' situation."""
    root = _make_repo(
        tmp_path,
        files={
            "Makefile": _makefile(
                "sound/%.bin: sound/%.wav\n\t@wav2agb $< $@\n"),
            "sound/data.inc": '.incbin "sound/cries/pidgey.bin"\n',
            "sound/cries/pidgey.bin": "binary",
            "sound/cries/pidgey.wav": "RIFF",
        },
        track=["Makefile", "sound/data.inc", "sound/cries/pidgey.wav"],
        gitignore="sound/**/*.bin\n",
    )
    assert _paths(clone_safety.check(root)) == []


@requires_git
def test_detects_its_own_repair(tmp_path):
    """Committing the orphan must clear the finding.

    A guard that cannot notice the fix cannot be trusted to have noticed the
    fault: it may be reporting for some unrelated reason.
    """
    files = {
        "Makefile": _makefile("sound/%.bin: sound/%.wav\n\t@wav2agb $< $@\n"),
        "sound/data.inc": '.incbin "sound/cries/dummy.bin"\n',
        "sound/cries/dummy.bin": "binary",
    }
    broken = _make_repo(tmp_path / "broken", files=files,
                        track=["Makefile", "sound/data.inc"],
                        gitignore="sound/**/*.bin\n")
    fixed = _make_repo(
        tmp_path / "fixed", files=files,
        track=["Makefile", "sound/data.inc", "sound/cries/dummy.bin"],
        gitignore="sound/**/*.bin\n")

    assert _paths(clone_safety.check(broken)) == ["sound/cries/dummy.bin"]
    assert _paths(clone_safety.check(fixed)) == []


# ────────────── the shapes that produced the false positives ────────────────

_MULTI_INPUT = {
    "Makefile": _makefile(
        "GFXDIR := graphics/interface\n"
        "$(GFXDIR)/hp_numbers.4bpp: $(GFXDIR)/hp_bar_anim.4bpp \\\n"
        "                           $(GFXDIR)/numbers1.4bpp \\\n"
        "                           $(GFXDIR)/numbers2.4bpp\n"
        + _RECIPE),
    "src/graphics.c": 'INCBIN_U32("graphics/interface/hp_numbers.4bpp")\n',
    "graphics/interface/hp_bar_anim.png": "png",
    "graphics/interface/numbers1.png": "png",
    "graphics/interface/numbers2.png": "png",
}

_MULTI_SOURCES = [
    "graphics/interface/hp_bar_anim.png",
    "graphics/interface/numbers1.png",
    "graphics/interface/numbers2.png",
]


@requires_git
def test_multi_input_rule_is_silent(tmp_path):
    """`hp_numbers.4bpp` has no `hp_numbers.png`, and is still fine.

    This exact file was one of the seven false positives. It is built by
    concatenating three unrelated `.4bpp` files, each of which comes from its
    own `.png`. Only reading the rule reveals that.
    """
    root = _make_repo(tmp_path, files=_MULTI_INPUT,
                      track=["Makefile", "src/graphics.c"] + _MULTI_SOURCES,
                      gitignore="*.4bpp\n")
    report = clone_safety.check(root)
    assert _paths(report) == []
    # Prove the asset was actually examined. Silence because a reference was
    # never seen is not the same as silence because the rule was understood,
    # and the two are indistinguishable from the finding list alone.
    assert "graphics/interface/hp_numbers.4bpp" in clone_safety.find_references(root)


@requires_git
@pytest.mark.parametrize("missing", _MULTI_SOURCES)
def test_multi_input_rule_speaks_when_a_source_is_missing(tmp_path, missing):
    """The paired mutation: drop any one of the three and it must report.

    Without this, `test_multi_input_rule_is_silent` would pass just as happily
    against a checker that had quietly stopped looking at `.4bpp` files.
    """
    root = _make_repo(
        tmp_path, files=_MULTI_INPUT,
        track=["Makefile", "src/graphics.c"] +
              [p for p in _MULTI_SOURCES if p != missing],
        gitignore="*.4bpp\n")
    assert _paths(clone_safety.check(root)) == [
        "graphics/interface/hp_numbers.4bpp"]


@requires_git
def test_chain_through_lz_and_4bpp(tmp_path):
    """`x.4bpp.lz` ← `x.4bpp` ← `x.png`, three rules deep.

    pokefirered reaches most graphics this way, and the deepest real chain in
    the project goes through an explicit multi-input rule in the middle
    (`obi1.4bpp.lz`). Both levels have to resolve.
    """
    files = {
        "Makefile": _makefile(""),
        "src/graphics.c": 'INCBIN_U32("graphics/terrain.4bpp.lz")\n',
        "graphics/terrain.png": "png",
    }
    present = _make_repo(tmp_path / "present", files=files,
                         track=["Makefile", "src/graphics.c",
                                "graphics/terrain.png"],
                         gitignore="*.4bpp\n*.lz\n")
    absent = _make_repo(tmp_path / "absent", files=files,
                        track=["Makefile", "src/graphics.c"],
                        gitignore="*.4bpp\n*.lz\n")

    assert _paths(clone_safety.check(present)) == []
    assert _paths(clone_safety.check(absent)) == ["graphics/terrain.4bpp.lz"]


# ─────────────────────────── parser invariants ──────────────────────────────

@requires_git
def test_empty_pattern_rule_is_not_a_producer(tmp_path):
    """`%.png: ;` must not make every `.png` buildable from nothing.

    These rules exist to stop make's implicit-rule search, not to create
    files. Treating them as producers silences the whole check — every
    missing source becomes "buildable" and no finding can ever be reported.
    """
    root = _make_repo(
        tmp_path,
        files={
            "Makefile": _makefile(""),       # carries `%.png: ;`
            "src/graphics.c": 'INCBIN_U32("graphics/ghost.4bpp")\n',
        },
        track=["Makefile", "src/graphics.c"],
        gitignore="*.4bpp\n")
    assert _paths(clone_safety.check(root)) == ["graphics/ghost.4bpp"]


@requires_git
def test_tab_indented_recipe_counts_as_a_producer(tmp_path):
    """A rule with no prerequisites but a real recipe does produce its target.

    Detecting recipes only after an inline `;` would misread most of
    pokefirered's rules, which put the recipe on the following tab line.
    """
    root = _make_repo(
        tmp_path,
        files={
            "Makefile": _makefile("graphics/made.4bpp:\n\t@touch $@\n"),
            "src/graphics.c": 'INCBIN_U32("graphics/made.4bpp")\n',
        },
        track=["Makefile", "src/graphics.c"],
        gitignore="*.4bpp\n")
    assert _paths(clone_safety.check(root)) == []


@requires_git
def test_self_referential_variable_does_not_explode(tmp_path):
    """`X := $(X)_modern` must resolve, not recurse.

    pokefirered's `config.mk` has five such lines inside conditionals that
    this parser does not evaluate. Expanding lazily turned one into a
    multi-kilobyte `_modern_modern_modern…` target.
    """
    root = _make_repo(
        tmp_path,
        files={
            "Makefile": _makefile(
                "NAME := firered\n"
                "NAME := $(NAME)_modern\n"
                "build/$(NAME)/out.4bpp: graphics/in.png\n" + _RECIPE),
            "src/graphics.c": 'INCBIN_U32("graphics/in.4bpp")\n',
            "graphics/in.png": "png",
        },
        track=["Makefile", "src/graphics.c", "graphics/in.png"],
        gitignore="*.4bpp\n")
    report = clone_safety.check(root)

    rules, _loose = clone_safety.parse_rules(root)
    targets = [t for rule in rules for t in rule.targets]
    assert "build/firered_modern/out.4bpp" in targets
    assert not any(t.count("_modern") > 1 for t in targets)
    assert report.ran


@requires_git
def test_unresolvable_target_silences_rather_than_inventing(tmp_path):
    """A target behind a variable we cannot expand must not become a finding.

    Dropping such a rule is the LOUD mistake: its target then looks
    unbuildable. A project that keeps assets behind a variable this parser
    cannot resolve would get a false alarm on every one of them, and a
    warning that cries wolf is worth less than no warning.
    """
    root = _make_repo(
        tmp_path,
        files={
            "Makefile": _makefile(
                # $(shell ...) is deliberately beyond the parser.
                "ASSETDIR := $(shell echo graphics)\n"
                "$(ASSETDIR)/mystery.4bpp: whatever.png\n" + _RECIPE),
            "src/graphics.c": 'INCBIN_U32("graphics/mystery.4bpp")\n',
        },
        track=["Makefile", "src/graphics.c"],
        gitignore="*.4bpp\n")
    report = clone_safety.check(root)

    assert report.loose_rules >= 1
    assert _paths(report) == []

    # The loose rule must say where it came from. When somebody reports that
    # this check stayed quiet about a file it should have caught, the rule
    # that silenced it is the first thing to look at, and `Makefile:9` is the
    # difference between answering that in a minute and guessing.
    _rules, loose = clone_safety.parse_rules(root)
    assert any(lr.origin.startswith("Makefile:") for lr in loose)
    assert any(lr.matcher.match("graphics/mystery.4bpp") for lr in loose)


@requires_git
def test_vague_loose_rule_is_discarded(tmp_path):
    """A loose pattern with no real literal must not swallow the check.

    `$(OUT)/$(NAME)` carries nothing to match on; kept as a matcher it would
    reduce to `.*` and mark every asset buildable.
    """
    root = _make_repo(
        tmp_path,
        files={
            "Makefile": _makefile(
                "OUT := $(shell echo a)\n"
                "NAME := $(shell echo b)\n"
                "$(OUT)$(NAME): x\n" + _RECIPE),
            "src/graphics.c": 'INCBIN_U32("graphics/ghost.4bpp")\n',
        },
        track=["Makefile", "src/graphics.c"],
        gitignore="*.4bpp\n")
    assert _paths(clone_safety.check(root)) == ["graphics/ghost.4bpp"]


@requires_git
def test_untracked_but_not_ignored_is_still_reported(tmp_path):
    """Being absent from the clone is what matters, not why.

    A file nobody ever `git add`ed is just as missing as an ignored one; the
    wording changes, the finding does not.
    """
    root = _make_repo(
        tmp_path,
        files={
            "Makefile": _makefile(""),
            "src/graphics.c": 'INCBIN_U32("graphics/forgot.4bpp")\n',
            "graphics/forgot.4bpp": "data",
        },
        track=["Makefile", "src/graphics.c"])
    report = clone_safety.check(root)
    assert _paths(report) == ["graphics/forgot.4bpp"]
    assert report.findings[0].ignored is False
    assert "never added to git" in report.findings[0].describe()


def test_non_repository_reports_nothing_and_says_so(tmp_path):
    """No git means no answer — and the report must admit that.

    An empty finding list that looks like a clean bill of health, when in
    fact nothing was checked, is the failure mode these tests exist to stop.
    """
    report = clone_safety.check(str(tmp_path))
    assert report.findings == []
    assert report.ran is False
    assert "git" in report.skipped_reason


# ───────────────────── against the real project, if present ─────────────────

_REAL = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                     "pokefirered")


@requires_git
@pytest.mark.skipif(not os.path.isdir(os.path.join(_REAL, ".git")),
                    reason="game project not checked out beside PorySuite")
def test_real_project_check_discriminates():
    """On the real project, removing a tracked source must surface its asset.

    Deliberately not asserting a finding COUNT. The owner's project had
    exactly one orphan and will have none once it is committed, so a count
    assertion would start failing the moment the bug is fixed — and a guard
    that fails on success gets deleted. What must hold forever is that the
    check still works at this scale: 8,567 assets, real rule files, and a
    source removed from the tracked set comes back as a finding.
    """
    refs = clone_safety.find_references(_REAL)
    tracked = clone_safety._tracked_files(_REAL)
    rules, loose = clone_safety.parse_rules(_REAL)

    assert tracked is not None
    assert len(refs) > 1000, f"only {len(refs)} assets found — scan broke"
    assert len(rules) > 100, f"only {len(rules)} rules parsed — parse broke"

    def unbuildable(tracked_set):
        ruleset = clone_safety.RuleSet(rules, tracked_set, loose)
        return {a for a in refs if not ruleset.can_build(a)}

    baseline = unbuildable(tracked)

    # Pick a real asset built from a single tracked source, then take that
    # source away. Anything else means the scan or the parse has gone blind.
    probe = next(
        (asset for asset in sorted(refs)
         if asset.endswith(".bin")
         and asset[:-4] + ".wav" in tracked
         and asset not in baseline),
        None)
    assert probe is not None, "no cry with a tracked .wav — fixture assumption broke"

    surfaced = unbuildable(tracked - {probe[:-4] + ".wav"}) - baseline
    assert surfaced == {probe}, (
        f"removing {probe[:-4]}.wav should surface exactly {probe}, "
        f"got {surfaced}")


# ──────────────────────────── the repair ────────────────────────────────────

_PRIOR_GITIGNORE = (
    "# the owner's own file, with their own comments and order\n"
    "build/\n"
    "*.gba\n"
    "\n"
    "sound/**/*.bin\n"
    "!sound/programmable_wave_samples/*.pcm\n"
)


def test_gitignore_repair_only_appends(tmp_path):
    """Pre-existing content must survive byte-identical, in its own order.

    A .gitignore is the project owner's file. The request this helper serves
    is "let these files be committed", which is an ADD — it is not permission
    to tidy, reorder or drop anything already in there, however stale a line
    might look. So the test is not "the new entry is present" but "the old
    bytes are still the start of the file".
    """
    gi = tmp_path / ".gitignore"
    gi.write_text(_PRIOR_GITIGNORE, encoding="utf-8", newline="")

    written = clone_safety.write_gitignore_exceptions(
        str(tmp_path), ["sound/direct_sound_samples/cries/dummy.bin"])

    after = gi.read_text(encoding="utf-8")
    assert written == ["sound/direct_sound_samples/cries/dummy.bin"]
    assert after.startswith(_PRIOR_GITIGNORE), "existing lines were altered"
    assert "!sound/direct_sound_samples/cries/dummy.bin" in after
    # And the owner's own exception line is untouched, not merged into ours.
    assert after.count("!sound/programmable_wave_samples/*.pcm") == 1


def test_gitignore_repair_is_idempotent(tmp_path):
    """Running it twice must not write the same exception again."""
    gi = tmp_path / ".gitignore"
    gi.write_text(_PRIOR_GITIGNORE, encoding="utf-8", newline="")

    clone_safety.write_gitignore_exceptions(str(tmp_path), ["a/b.bin"])
    once = gi.read_text(encoding="utf-8")
    assert clone_safety.write_gitignore_exceptions(str(tmp_path), ["a/b.bin"]) == []
    assert gi.read_text(encoding="utf-8") == once


def test_gitignore_repair_without_an_existing_file(tmp_path):
    """A project with no .gitignore at all still gets a usable one."""
    written = clone_safety.write_gitignore_exceptions(str(tmp_path), ["a/b.bin"])
    assert written == ["a/b.bin"]
    assert "!a/b.bin" in (tmp_path / ".gitignore").read_text(encoding="utf-8")


@requires_git
def test_repair_makes_the_finding_go_away(tmp_path):
    """End to end: warn, repair, commit, and the warning is gone.

    The repair is only worth having if the check agrees afterwards. Asserting
    that here means the two halves cannot drift apart — a repair that writes
    an exception the checker still reports would otherwise look like success.
    """
    root = _make_repo(
        tmp_path,
        files={
            "Makefile": _makefile("sound/%.bin: sound/%.wav\n\t@wav2agb $< $@\n"),
            "sound/data.inc": '.incbin "sound/cries/dummy.bin"\n',
            "sound/cries/dummy.bin": "binary",
        },
        track=["Makefile", "sound/data.inc"],
        gitignore="sound/**/*.bin\n")

    before = clone_safety.check(root)
    assert _paths(before) == ["sound/cries/dummy.bin"]
    assert before.findings[0].ignored is True

    clone_safety.write_gitignore_exceptions(root, ["sound/cries/dummy.bin"])
    staged, error = clone_safety.stage(root, ["sound/cries/dummy.bin"])
    assert staged == ["sound/cries/dummy.bin"], error

    subprocess.run(["git", "commit", "-q", "-m", "add dummy"], cwd=root,
                   capture_output=True, text=True, check=True)
    assert _paths(clone_safety.check(root)) == []


# ───────────────── what the push gate shows and does ────────────────────────
#
# `ui/mainwindow.py::_clone_safety_gate` is now only Qt calls: it asks the
# checker, hands the findings to `warning_html`, and on "Add them to git"
# calls `repair` and `repair_summary`. Those three are tested here.
#
# They live in core for a reason. The first draft built the warning text and
# ran the repair inside the GUI method, where two mistakes went unnoticed — a
# Qt enum that does not exist (`_QtTextFormat_RichText()`) and a refresh
# method that does not exist (`_git_refresh_panel`). Neither is a syntax
# error, so nothing caught them, and a test could not reach them either:
# importing `mainwindow` under this suite kills the interpreter outright
# (pytest exits 127 part-way through collection). Moving the logic out is what
# makes it testable at all.
#
# Still NOT covered, and it needs a person: how the dialog looks, that the
# three buttons are distinguishable, and that the wait cursor clears.


def _finding(path="sound/cries/dummy.bin", *, ignored=True):
    return clone_safety.Finding(path=path, referenced_by="sound/data.inc",
                                ignored=ignored, on_disk=True)


def test_warning_names_the_files_and_the_consequence():
    html = clone_safety.warning_html([_finding()])
    assert "sound/cries/dummy.bin" in html
    assert "1 file(s)" in html
    # The point of the dialog is the consequence, not the file list.
    assert "clones" in html and "make" in html


def test_warning_truncates_a_long_list():
    findings = [_finding(f"a/f{i}.bin") for i in range(9)]
    html = clone_safety.warning_html(findings, limit=4)
    assert "a/f0.bin" in html and "a/f3.bin" in html
    assert "a/f4.bin" not in html
    assert "and 5 more" in html
    assert "9 file(s)" in html


@requires_git
def test_repair_unignores_and_stages_without_committing(tmp_path):
    """The gate's "Add them to git" path, end to end.

    Staging and not committing is deliberate: these are often large binaries
    and whether they belong in the history is the owner's call, not a side
    effect of pressing Push.
    """
    root = _make_repo(
        tmp_path,
        files={
            "Makefile": _makefile("sound/%.bin: sound/%.wav\n\t@wav2agb $< $@\n"),
            "sound/data.inc": '.incbin "sound/cries/dummy.bin"\n',
            "sound/cries/dummy.bin": "binary",
        },
        track=["Makefile", "sound/data.inc"],
        gitignore="sound/**/*.bin\n")

    report = clone_safety.check(root)
    result = clone_safety.repair(root, report.findings)

    assert result.unignored == ["sound/cries/dummy.bin"]
    assert result.staged == ["sound/cries/dummy.bin"]
    assert result.error == ""

    staged = subprocess.run(["git", "diff", "--cached", "--name-only"],
                            cwd=root, capture_output=True, text=True,
                            check=True).stdout.split()
    assert "sound/cries/dummy.bin" in staged

    # Nothing was committed on the project's behalf.
    log = subprocess.run(["git", "log", "--oneline"], cwd=root,
                         capture_output=True, text=True, check=True).stdout
    assert log.count("\n") == 1, "repair must not create a commit"


def test_repair_summary_says_what_happened_and_what_is_left():
    summary = clone_safety.repair_summary(clone_safety.RepairResult(
        unignored=["a.bin"], staged=["a.bin", "b.bin"]))
    assert "1 exception" in summary
    assert "Staged 2 file(s)" in summary
    # The user must be told the push did NOT go ahead, or they will assume it
    # did and wonder why the other machine is still broken.
    assert "did not open" in summary


def test_repair_summary_reports_a_failure():
    summary = clone_safety.repair_summary(clone_safety.RepairResult(
        error="git add failed for a.bin"))
    assert "Stopped early" in summary
    assert "a.bin" in summary
