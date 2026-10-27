"""Turn iTerm2 colour values into ``#rrggbb`` strings."""

from __future__ import annotations

from dataclasses import dataclass

from iterm2.screen import CellStyle

_CUBE_LEVELS = (0, 95, 135, 175, 215, 255)

_DEFAULT_ANSI = (
    "#000000", "#c91b00", "#00c200", "#c7c400",
    "#0225c7", "#ca30c7", "#00c5c7", "#c7c7c7",
    "#686868", "#ff6e67", "#5ff967", "#fefb67",
    "#6871ff", "#ff76ff", "#5ffdff", "#ffffff",
)


def _hex(r: float, g: float, b: float) -> str:
    return f"#{round(r):02x}{round(g):02x}{round(b):02x}"


def xterm_256(index: int) -> str:
    """Hex colour of an xterm 256-colour index in the 16-255 range (cube and grays)."""
    if 16 <= index <= 231:
        n = index - 16
        return _hex(_CUBE_LEVELS[n // 36], _CUBE_LEVELS[n // 6 % 6], _CUBE_LEVELS[n % 6])
    if 232 <= index <= 255:
        v = 8 + 10 * (index - 232)
        return _hex(v, v, v)
    raise ValueError(f"xterm index out of range: {index}")


@dataclass(frozen=True)
class Palette:
    ansi: tuple[str, ...]
    fg: str
    bg: str

    @classmethod
    def from_profile(cls, profile, dark: bool) -> Palette:
        """Build a palette from an iterm2 Profile, using defaults for unset colours."""
        if profile.use_separate_colors_for_light_and_dark_mode:
            suffix = "_dark" if dark else "_light"
        else:
            suffix = ""

        def get(name: str, default: str) -> str:
            color = getattr(profile, name + suffix)
            return default if color is None else _hex(color.red, color.green, color.blue)

        ansi = tuple(get(f"ansi_{i}_color", _DEFAULT_ANSI[i]) for i in range(16))
        return cls(
            ansi=ansi,
            fg=get("foreground_color", DEFAULT_PALETTE.fg),
            bg=get("background_color", DEFAULT_PALETTE.bg),
        )


DEFAULT_PALETTE = Palette(ansi=_DEFAULT_ANSI, fg="#c7c7c7", bg="#000000")


def resolve_color(color: CellStyle.Color | None, palette: Palette, *, is_fg: bool) -> str | None:
    """Hex for a cell colour, or None when the cell uses the default fg/bg."""
    if color is None:
        return None
    if color.is_rgb:
        rgb = color.rgb
        return _hex(rgb.red, rgb.green, rgb.blue)
    if color.is_standard:
        index = color.standard
        return palette.ansi[index] if index < 16 else xterm_256(index)
    if color.is_alternate:
        if color.alternate is CellStyle.AlternateColor.REVERSED_DEFAULT:
            return palette.bg if is_fg else palette.fg
        return None  # DEFAULT and SYSTEM_MESSAGE
    return None  # placement
