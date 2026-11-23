"""Regression guard: dialogue longer than one textbox must actually render.

THE BUG (reported 2026-11-22, a house map). An NPC was given three
display lines of dialogue in EVENTide's Event Editor. In game the textbox
opened and the third line was simply not there. Shorter dialogue was fine.
The user reported it against EVERY textbox they had written with longer text.

The field message box is FOUR tiles tall (`sStandardTextBox_WindowTemplates`
in `src/new_menu_helpers.c`) and prints at `y = 1` with `lineSpacing = 1`, so
exactly TWO lines of FONT_NORMAL fit. pokefirered therefore has three distinct
break codes, and `RenderText` in `src/text.c` treats them differently:

    \\n   next line down            -> line slot += 1
    \\l   scroll the window up one  -> column resets, SLOT DOES NOT CHANGE
    \\p   clear the box and wait    -> slot returns to 0

So the third line of a page has to be reached with `\\l`. Reached with `\\n` it
is drawn at slot 2, one line below the window floor, where it renders as a
one-pixel sliver -- invisible. Vanilla knows this: Oak's Lab's e-mail text is
`...of the\\lPOKeMON LEAGUE are ready to\\ltake on all comers!\\p...`.

EVENTide got this wrong in two places at once, and both are covered here:

* `eventide_utils.text_body_lines` encoded EVERY single break as `\\n`
  (`text.replace("\\n\\n", "\\\\p").replace("\\n", "\\\\n")`). Nothing it ever
  wrote contained a `\\l`, so any paragraph past two lines lost its tail.
* `game_text_edit.display_to_eventide` flattened `\\l` to a plain newline on
  the way INTO EVENTide's internal form, so merely opening a correct vanilla
  text in the Event Editor and saving the map downgraded its scroll codes to
  `\\n` -- turning working vanilla dialogue into the same broken shape.

THE RULE: one canonical encoder, `core.gba_text_metrics.encode_gba_linebreaks`.
Blank line -> `\\p`; the first break inside a paragraph -> `\\n`; every break
after that -> `\\l`. A `\\l` the author wrote themselves is carried through
untouched, because `\\l` on line 2 is a deliberate effect
("WHAT?\\lUnbelievable!") and re-deriving it as `\\n` would change how the box
animates.
"""
import os
import re

import pytest

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

from core.gba_text_metrics import encode_gba_linebreaks  # noqa: E402
from eventide.backend.eventide_utils import text_body_lines  # noqa: E402

# The window fits two lines. Anything drawn at slot 2 or beyond is off-window.
LINES_PER_PAGE = 2

# Oak's Lab, the reference the bug report named: a long multi-box dialogue
# that renders correctly in vanilla.
OAKS_LAB_EMAIL = (
    r"There's an e-mail message here.\p...\pFinally!\nThe ultimate TRAINERS "
    r"of the\lPOKeMON LEAGUE are ready to\ltake on all comers!\pBring your "
    r"best POKeMON and see\nhow you rate as a TRAINER!$"
)

# The user's own text, as they typed it into the Event Editor: three display
# lines, a paragraph break, then two more.
THREE_LINES_TYPED = (
    "Long ago, farmers lived in the\n"
    "clouds and were close companions\n"
    "with these large birds.\n"
    "\n"
    "Apparently, they could ride them\n"
    "in the sky!$"
)


def rendered_slots(encoded: str):
    """Walk *encoded* the way `RenderText` does and return each line's slot.

    Independent of the production encoder on purpose -- this is the engine's
    model, not a restatement of ours. Returns ``[(slot, text), ...]`` for every
    line that actually draws glyphs.
    """
    out = []
    slot = 0
    for piece in re.split(r'(\\[nlp])', encoded):
        if piece == '\\n':
            slot += 1
        elif piece == '\\p':
            slot = 0
        elif piece == '\\l':
            pass                      # column resets, row does NOT
        elif piece.strip(' $'):
            out.append((slot, piece))
    return out


def inc_body(internal: str) -> str:
    """The bytes `text_body_lines` would put between the quotes, rejoined."""
    body = re.compile(r'^\s*\.(?:string|braille)\s+"(.*)"$')
    parts = []
    for line in text_body_lines(internal):
        m = body.match(line.rstrip('\n'))
        assert m, f'unexpected directive line: {line!r}'
        parts.append(m.group(1))
    return ''.join(parts)


# -- the encoder itself ------------------------------------------------------

def test_third_line_of_a_page_scrolls_instead_of_falling_off():
    """The exact bug: line 3 must be reached with a scroll, not a newline."""
    got = encode_gba_linebreaks(THREE_LINES_TYPED)
    assert got == (
        r"Long ago, farmers lived in the\n"
        r"clouds and were close companions\l"
        r"with these large birds.\p"
        r"Apparently, they could ride them\n"
        r"in the sky!$"
    ), got


def test_no_line_is_ever_drawn_below_the_window_floor():
    """The engine invariant, checked by an independent slot walk."""
    for slot, text in rendered_slots(encode_gba_linebreaks(THREE_LINES_TYPED)):
        assert slot < LINES_PER_PAGE, f'slot {slot} is off-window: {text!r}'


def test_a_long_single_paragraph_scrolls_all_the_way_down():
    typed = "\n".join(f"line {i}" for i in range(1, 7))
    encoded = encode_gba_linebreaks(typed)
    assert encoded == r"line 1\nline 2\lline 3\lline 4\lline 5\lline 6"
    for slot, text in rendered_slots(encoded):
        assert slot < LINES_PER_PAGE, f'slot {slot} is off-window: {text!r}'


def test_an_author_written_scroll_is_never_re_derived():
    """A scroll on line 2 is a deliberate effect. Leave it exactly as typed."""
    typed = "WHAT?\\lUnbelievable!\nI picked the wrong POKeMON!"
    assert encode_gba_linebreaks(typed) == (
        r"WHAT?\lUnbelievable!\nI picked the wrong POKeMON!")


def test_encoding_is_idempotent():
    once = encode_gba_linebreaks(THREE_LINES_TYPED)
    assert encode_gba_linebreaks(once) == once
    assert encode_gba_linebreaks(OAKS_LAB_EMAIL) == OAKS_LAB_EMAIL


# -- EVENTide's map writer ---------------------------------------------------

def test_text_body_lines_emits_the_scroll_code():
    assert inc_body(THREE_LINES_TYPED) == (
        r"Long ago, farmers lived in the\n"
        r"clouds and were close companions\l"
        r"with these large birds.\p"
        r"Apparently, they could ride them\n"
        r"in the sky!$"
    )


def test_text_body_lines_still_splits_one_string_per_display_line():
    """Keeps the 2026-11-18 formatting: one `.string` per break, so editing
    one label does not churn the whole map's text.inc in git."""
    lines = text_body_lines(THREE_LINES_TYPED)
    assert len(lines) == 5
    assert lines[1].strip() == r'.string "clouds and were close companions\l"'
    assert all(ln.startswith('    .string "') for ln in lines)


def test_braille_is_not_given_scroll_codes():
    """`.braille` renders in its own taller window; the 2-line rule is wrong
    for it, and the braille branch must stay on plain newlines."""
    out = text_body_lines('__BRAILLE__' + "ABC\nDEF\nGHI")
    assert len(out) == 1
    assert out[0].strip() == r'.braille "ABC\nDEF\nGHI"'


def test_quotes_are_still_escaped():
    assert inc_body('He said "hi"\nthen left\nwithout us') == (
        r'He said \"hi\"\nthen left\lwithout us')


# -- the widget round-trip ---------------------------------------------------

@pytest.fixture(scope='module')
def gte():
    pytest.importorskip('PyQt6.QtWidgets')
    import ui.game_text_edit as mod
    return mod


def test_vanilla_long_dialogue_survives_the_event_editor(gte):
    """Opening Oak's Lab's e-mail text in EVENTide and saving must give the
    file back byte-identical. It used to come back with every scroll code
    downgraded to a newline."""
    plain, ctx, runs = gte.inc_to_display(OAKS_LAB_EMAIL)
    internal = gte.display_to_eventide(plain, runs, ctx)
    assert inc_body(internal) == OAKS_LAB_EMAIL


def test_display_to_eventide_keeps_the_scroll_code(gte):
    raw = r"WHAT?\lUnbelievable!\nI picked the wrong POKeMON!$"
    plain, ctx, runs = gte.inc_to_display(raw)
    internal = gte.display_to_eventide(plain, runs, ctx)
    assert '\\l' in internal, internal
    assert inc_body(internal) == raw


def test_typed_dialogue_round_trips_through_the_widget(gte):
    plain, ctx, runs = gte.inc_to_display(
        encode_gba_linebreaks(THREE_LINES_TYPED))
    internal = gte.display_to_eventide(plain, runs, ctx)
    for slot, text in rendered_slots(inc_body(internal)):
        assert slot < LINES_PER_PAGE, f'slot {slot} is off-window: {text!r}'


# -- the counter the user actually looks at ----------------------------------

def test_counter_shows_the_page_a_line_belongs_to(gte):
    """Per the project's text-field rule, the user must be able to SEE the
    line/page structure -- which line starts a new box and which one scrolls."""
    html = gte._build_counter_html(
        ["one", "two", "three", "", "four"], 36, 20)
    assert 'P1 L1' in html and 'P1 L2' in html and 'P1 L3' in html
    assert 'P2 L1' in html
    assert '↑' in html          # the scroll marker on P1 L3
