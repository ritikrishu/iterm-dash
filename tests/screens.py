"""Build synthetic iTerm2 ScreenContents for tests (never real terminal content)."""

from __future__ import annotations

from iterm2 import api_pb2
from iterm2.screen import ScreenContents


def make_screen(lines: list[list[tuple[str, dict]]]) -> ScreenContents:
    """Each line is a list of (cell_text, CellStyle kwargs); "" is a wide-char continuation cell."""
    return ScreenContents(make_buffer(lines))


def make_buffer(lines: list[list[tuple[str, dict]]]) -> api_pb2.GetBufferResponse:
    """The raw buffer proto behind make_screen (status OK)."""
    response = api_pb2.GetBufferResponse()
    for cells in lines:
        line = response.contents.add()
        line.text = "".join(text for text, _ in cells)
        line.continuation = api_pb2.LineContents.CONTINUATION_HARD_EOL
        for text, style in cells:
            line.code_points_per_cell.add(num_code_points=len(text), repeats=1)
            line.style.add(repeats=1, **style)
    return response
