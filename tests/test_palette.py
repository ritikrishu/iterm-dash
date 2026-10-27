import iterm2
from iterm2 import api_pb2
from iterm2.screen import CellStyle

from iterm_dash.palette import DEFAULT_PALETTE, Palette, resolve_color, xterm_256


def rgb(r, g, b):
    return CellStyle.RGBColor(api_pb2.RGBColor(red=r, green=g, blue=b))


class FakeProfile:
    """Exposes the colour getters of iterm2.Profile; unset ones are None."""

    def __init__(self, separate, **colors):
        self.use_separate_colors_for_light_and_dark_mode = separate
        names = ["foreground_color", "background_color"] + [f"ansi_{i}_color" for i in range(16)]
        for name in names:
            for suffix in ("", "_dark", "_light"):
                setattr(self, name + suffix, None)
        for key, value in colors.items():
            base, suffix = key, ""
            for s in ("_dark", "_light"):
                if key.endswith(s):
                    base, suffix = key[: -len(s)], s
            attr = {"fg": "foreground_color", "bg": "background_color"}.get(base, base) + suffix
            setattr(self, attr, iterm2.Color(*value))


def test_xterm_cube_and_grays():
    assert xterm_256(196) == "#ff0000"
    assert xterm_256(16) == "#000000"
    assert xterm_256(231) == "#ffffff"
    assert xterm_256(244) == "#808080"   # 232+12 → 8+10*12 = 128


def test_resolve_standard_ansi_uses_palette():
    pal = Palette(ansi=tuple(f"#a0000{i:x}" for i in range(16)), fg="#c7c7c7", bg="#000000")
    assert resolve_color(CellStyle.Color(standard=1), pal, is_fg=True) == "#a00001"
    assert resolve_color(CellStyle.Color(standard=196), pal, is_fg=True) == "#ff0000"


def test_resolve_rgb_and_defaults():
    pal = DEFAULT_PALETTE
    assert resolve_color(CellStyle.Color(rgb=rgb(1, 2, 3)), pal, is_fg=True) == "#010203"
    assert resolve_color(None, pal, is_fg=True) is None
    assert resolve_color(CellStyle.Color(alternate=CellStyle.AlternateColor.DEFAULT), pal, is_fg=False) is None
    assert resolve_color(CellStyle.Color(alternate=CellStyle.AlternateColor.REVERSED_DEFAULT), pal, is_fg=True) == pal.bg


def test_from_profile_prefers_dark_variants_when_separate():
    prof = FakeProfile(separate=True, fg=(1, 1, 1), fg_dark=(2, 2, 2), fg_light=(1, 1, 1),
                       bg=(0, 0, 0), bg_dark=(9, 9, 9), bg_light=(0, 0, 0))
    pal = Palette.from_profile(prof, dark=True)
    assert pal.fg == "#020202" and pal.bg == "#090909"
    assert Palette.from_profile(prof, dark=False).fg == "#010101"


def test_from_profile_falls_back_to_default_for_missing_colors():
    assert Palette.from_profile(FakeProfile(separate=False), dark=True).ansi == DEFAULT_PALETTE.ansi
