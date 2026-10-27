"""Convert iTerm2 screen snapshots into compact rows of styled runs for the wire."""

from __future__ import annotations

from iterm2.screen import LineContents, ScreenContents

from iterm_dash.palette import Palette, resolve_color

BOLD = 1
ITALIC = 2
UNDERLINE = 4
FAINT = 8
STRIKE = 16
WIDE = 32

Run = list  # [text, fg | None, bg | None, flags]
Row = list[Run]


def _flags(style) -> int:
    return (
        (BOLD if style.bold else 0)
        | (ITALIC if style.italic else 0)
        | (UNDERLINE if style.underline else 0)
        | (FAINT if style.faint else 0)
        | (STRIKE if style.strikethrough else 0)
    )


def _line_cells(line):
    """Yield (text, style) for each cell until the line's known cells run out."""
    x = 0
    while True:
        try:
            text = line.string_at(x)
        except IndexError:
            return
        yield text, line.style_at(x)
        x += 1


def line_row(line: LineContents, palette: Palette) -> Row:
    """One screen line as styled runs (trailing default-background blanks dropped)."""
    cells = list(_line_cells(line))
    row: Row = []
    for x, (text, style) in enumerate(cells):
        if text == "\x00":  # a cell skipped by a cursor move; iTerm2 shows it blank, browsers would drop it
            text = " "
        if text == "":
            if x > 0 and cells[x - 1][0] != "":  # continuation of the wide lead before it
                continue
            text = " "  # no lead: a blank cell, keep the grid aligned
        if style is None:
            fg = bg = None
            flags = 0
        else:
            fg = resolve_color(style.fg_color, palette, is_fg=True)
            bg = resolve_color(style.bg_color, palette, is_fg=False)
            flags = _flags(style)
            if style.inverse:
                fg, bg = (bg or palette.bg), (fg or palette.fg)
        wide = cells[x][0] != "" and x + 1 < len(cells) and cells[x + 1][0] == ""
        if wide:
            flags |= WIDE
        last = row[-1] if row else None
        if last and not wide and last[1:] == [fg, bg, flags]:
            last[0] += text
        else:
            row.append([text, fg, bg, flags])
    while row and row[-1][2] is None:  # trailing blanks on the default background are invisible
        row[-1][0] = row[-1][0].rstrip(" ")
        if row[-1][0]:
            break
        row.pop()
    return row


def screen_rows(screen: ScreenContents, palette: Palette) -> list[Row]:
    return [line_row(screen.line(i), palette) for i in range(screen.number_of_lines)]


def diff_rows(prev: list[Row] | None, new: list[Row]) -> dict[int, Row]:
    """Rows that differ from prev; every row on the first frame or when the size changed."""
    if prev is None or len(prev) != len(new):
        return dict(enumerate(new))
    return {i: row for i, row in enumerate(new) if row != prev[i]}
