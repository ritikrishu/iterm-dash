from iterm2 import api_pb2
from iterm2.screen import ScreenContents
from screens import make_screen

from iterm_dash.palette import Palette
from iterm_dash.render import BOLD, FAINT, ITALIC, STRIKE, UNDERLINE, WIDE, diff_rows, screen_rows

P = Palette(ansi=tuple(f"#{i:02x}{i:02x}{i:02x}" for i in range(0x10, 0x20)), fg="#eeeeee", bg="#111111")


def test_plain_text_is_one_default_run_and_trailing_blanks_dropped():
    rows = screen_rows(make_screen([[("h", {}), ("i", {}), (" ", {}), (" ", {})]]), P)
    assert rows == [[["hi", None, None, 0]]]


def test_style_changes_split_runs_and_same_style_merges():
    rows = screen_rows(make_screen([[("a", {"bold": True}), ("b", {"bold": True}), ("c", {"fgStandard": 1})]]), P)
    assert rows[0] == [["ab", None, None, BOLD], ["c", P.ansi[1], None, 0]]


def test_flags_map_to_bits():
    cells = [("x", {k: True}) for k in ("italic", "underline", "faint", "strikethrough")]
    assert [r[3] for r in screen_rows(make_screen([cells]), P)[0]] == [ITALIC, UNDERLINE, FAINT, STRIKE]


def test_inverse_swaps_colors_and_fills_defaults():
    rows = screen_rows(make_screen([[("x", {"inverse": True})]]), P)
    assert rows[0] == [["x", P.bg, P.fg, 0]]


def test_wide_char_is_its_own_run_and_continuation_skipped():
    rows = screen_rows(make_screen([[("界", {}), ("", {}), ("x", {})]]), P)
    assert rows[0] == [["界", None, None, WIDE], ["x", None, None, 0]]


def test_trailing_spaces_with_background_are_kept():
    rows = screen_rows(make_screen([[("a", {}), (" ", {"bgStandard": 4})]]), P)
    assert rows[0][-1] == [" ", None, P.ansi[4], 0]


def test_html_like_text_passes_through_unescaped():  # escaping is the client's job (rows.js)
    assert screen_rows(make_screen([[(c, {}) for c in "<b>"]]), P)[0] == [["<b>", None, None, 0]]


def test_empty_line_is_empty_row():
    assert screen_rows(make_screen([[]]), P) == [[]]


def test_nul_cells_become_spaces():
    # iTerm2 reports cells skipped by a cursor move (never written) as U+0000; browsers drop NULs,
    # which collapsed word gaps ("thereisno") and shifted indented lines left.
    cells = [(c, {}) for c in "\x00there\x00is"]
    assert screen_rows(make_screen([cells]), P)[0] == [[" there is", None, None, 0]]


def test_trailing_nul_cells_are_trimmed_like_spaces():
    cells = [(c, {}) for c in "ab\x00\x00"]
    assert screen_rows(make_screen([cells]), P)[0] == [["ab", None, None, 0]]


def test_diff_first_frame_and_size_change_return_every_row():
    new = [[["a", None, None, 0]], [["b", None, None, 0]]]
    assert diff_rows(None, new) == {0: new[0], 1: new[1]}
    assert diff_rows(new[:1], new) == {0: new[0], 1: new[1]}


def test_diff_returns_only_changed_rows():
    old = [[["a", None, None, 0]], [["b", None, None, 0]]]
    new = [[["a", None, None, 0]], [["c", None, None, 0]]]
    assert diff_rows(old, new) == {1: [["c", None, None, 0]]}


def _width(row):
    return sum(2 if run[3] & WIDE else len(run[0]) for run in row)


def test_empty_cell_at_col0_is_blank_space():
    assert screen_rows(make_screen([[("", {}), ("x", {})]]), P) == [[[" x", None, None, 0]]]


def test_consecutive_wide_chars_stay_separate_runs():
    rows = screen_rows(make_screen([[("界", {}), ("", {}), ("界", {}), ("", {})]]), P)
    assert rows[0] == [["界", None, None, WIDE], ["界", None, None, WIDE]]


def test_double_empty_after_narrow_keeps_alignment():
    rows = screen_rows(make_screen([[("a", {}), ("", {}), ("", {}), ("b", {})]]), P)
    assert _width(rows[0]) == 4


def test_missing_style_is_default():
    response = api_pb2.GetBufferResponse()
    line = response.contents.add(text="ab")
    line.code_points_per_cell.add(num_code_points=1, repeats=2)
    line.style.add(repeats=1, bold=True)  # second cell has no style entry
    assert screen_rows(ScreenContents(response), P)[0] == [["a", None, None, BOLD], ["b", None, None, 0]]
