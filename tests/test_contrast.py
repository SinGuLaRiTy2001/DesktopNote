"""Contrast tests: every foreground colour must stay legible on the glass over any desktop.

The backgrounds in MEASURED were sampled from screenshots of real notes (with and without a
tag glow) and date popups over pure black, pure white and saturated striped desktops; each list
holds the extremes, as printed by tools/contrast_probe.py. Re-measure them when fill_alpha or
GLOW_HEIGHT change. Tag glows are computed (theme.glow_color), so they are checked for every
preset and for extreme custom colours.
"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from desktopnote.theme import (  # noqa: E402
    DARK, GLOW_PILL_FACTOR, GLOW_TITLE_FACTOR, LIFTED_ALPHA, LIGHT, TAGS, blend, glow_color,
)
from desktopnote.widgets import DeletePill, ItemRow  # noqa: E402

EXTREME_CUSTOM = ["#000000", "#FFFFFF", "#FF0000", "#00FF00", "#0000FF", "#FFFF00", "#808080", "#1B2A5A", "#FFF6DC"]

TEXT, UI = 4.5, 3.0  # WCAG AA minimums for normal text and for icons and controls.

MEASURED = {
    "light": {
        "rows": ["#c8d2da", "#d8d8da", "#f1f1f3"],  # Text and row icons, glow tail.
        "pill_untagged": ["#d8d8da", "#f1f1f3"],
        "popup": ["#e0e0e2", "#f5f5f7"],
    },
    "dark": {
        "rows": ["#222224", "#39393b", "#4d4a54"],
        "pill_untagged": ["#222224", "#39393b"],
        "popup": ["#1b1b1d", "#303032"],
    },
}


def luminance(color: str) -> float:
    """Returns the WCAG relative luminance of a hex colour (#RRGGBB or #AARRGGBB)."""
    hex_digits = color.lstrip("#")[-6:]
    channels = [int(hex_digits[i:i + 2], 16) / 255 for i in (0, 2, 4)]
    channels = [x / 12.92 if x <= 0.04045 else ((x + 0.055) / 1.055) ** 2.4 for x in channels]
    return 0.2126 * channels[0] + 0.7152 * channels[1] + 0.0722 * channels[2]


def contrast(a: str, b: str) -> float:
    """Returns the WCAG contrast ratio between two hex colours (1 to 21)."""
    hi, lo = sorted((luminance(a), luminance(b)), reverse=True)
    return (hi + 0.05) / (lo + 0.05)


def over(rgba: str, bg: str) -> str:
    """Returns a CSS rgba(...) colour composited onto an opaque hex colour, as hex."""
    r, g, b, a = (float(x) for x in rgba[rgba.index("(") + 1:-1].split(","))
    under = [int(bg.lstrip("#")[i:i + 2], 16) for i in (0, 2, 4)]
    return "#" + "".join(f"{round(x * a + y * (1 - a)):02x}" for x, y in zip((r, g, b), under))


def css(rgba: str, strength: float = 1.0) -> tuple[str, float]:
    """Returns a CSS rgba(...) colour as (hex colour, alpha), with its alpha scaled by strength."""
    r, g, b, a = (float(x) for x in rgba[rgba.index("(") + 1:-1].split(","))
    return f"#{round(r):02x}{round(g):02x}{round(b):02x}", a * strength


class ContrastTest(unittest.TestCase):
    """Checks foreground colours of both themes against the measured glass backgrounds."""

    def check(self, theme, cases):
        """Asserts that each (label, foreground, backgrounds, minimum ratio) case passes."""
        for label, fg, bgs, need in cases:
            for bg in bgs:
                with self.subTest(theme="dark" if theme.dark else "light", element=label, bg=bg):
                    self.assertGreaterEqual(round(contrast(fg, bg), 2), need, f"{fg} on {bg}")

    def test_all_elements_both_themes(self):
        """Checks text, icons, popups and menus of the light and the dark theme."""
        for name, t in (("light", LIGHT), ("dark", DARK)):
            m = MEASURED[name]
            menu = [over(t.menu_bg, "#000000"), over(t.menu_bg, "#ffffff")]
            menu_hovered = [over(t.menu_hover, bg) for bg in menu]  # The grey row highlight.
            # A row held for a drag: its tint over the (frosted) glass replaces the highlight.
            lifted = [blend(t.lifted_color(), LIFTED_ALPHA, bg) for bg in m["rows"]]
            # An item's Delete pill: idle, hovered and pressed, over the hovered row on the glass.
            row_hover = css(t.hover, ItemRow.HOVER_STRENGTH)
            pills = [DeletePill.fill(t, hover, down) for hover, down in ((0.0, False), (1.0, False), (1.0, True))]
            pill_fills = [blend(pill.name(), pill.alphaF(), blend(*row_hover, bg))
                          for pill in pills for bg in m["rows"]]
            self.check(t, [
                ("text, title", t.text, m["rows"], TEXT),
                ("secondary text: done items, due dates, hints, title placeholder", t.secondary, m["rows"], TEXT),
                ("due soon (orange)", t.orange, m["rows"], TEXT),
                ("overdue (red)", t.red, m["rows"], TEXT),
                ("Done button (accent)", t.accent, m["rows"], TEXT),
                ("toolbar and row icons (secondary)", t.secondary, m["rows"], UI),
                ("small icons: row delete, untagged ring (tertiary)", t.tertiary, m["rows"], UI),
                ("lifted row: text", t.text, lifted, TEXT),
                ("lifted row: done items, due dates (secondary)", t.secondary, lifted, TEXT),
                ("lifted row: due soon (orange)", t.orange, lifted, TEXT),
                ("lifted row: overdue (red)", t.red, lifted, TEXT),
                ("lifted row: done check (tertiary)", t.tertiary, lifted, UI),
                ("item Delete pill: text, idle and hovered", t.text, pill_fills, TEXT),
                ("pin, untagged (tertiary)", t.tertiary, m["pill_untagged"], UI),
                ("popup text", t.text, m["popup"], TEXT),
                ("popup secondary text, weekdays", t.secondary, m["popup"], TEXT),
                ("popup tertiary icons", t.tertiary, m["popup"], UI),
                ("popup delete (red)", t.red, m["popup"], TEXT),
                ("menu text", t.text, menu, TEXT),
                ("menu delete (red)", t.red, menu, TEXT),
                ("hovered menu row: text and icons", t.text, menu_hovered, TEXT),
                ("hovered menu row: delete (red)", t.red, menu_hovered, TEXT),
                ("white on highlight / selection", "#ffffff", [t.accent_fill], TEXT),
            ])

    def test_drag_pill_shows_on_any_glass(self):
        """Keeps the translucent drag pill visible, idle and held, over plain and tagged glass."""
        for name, t in (("light", LIGHT), ("dark", DARK)):
            backgrounds = list(MEASURED[name]["pill_untagged"])
            for color in [c for c in TAGS.values() if c] + EXTREME_CUSTOM:
                glow = glow_color(t, color)
                backgrounds += [blend(glow.name(), glow.alphaF() * GLOW_PILL_FACTOR, glass)
                                for glass in MEASURED[name]["pill_untagged"]]
            for state, alpha in zip(("idle", "held"), t.pill_alpha):
                for bg in backgrounds:
                    with self.subTest(theme=name, state=state, bg=bg):
                        pill = blend(t.pill_shade, alpha, bg)
                        self.assertGreaterEqual(round(contrast(pill, bg), 2), UI)

    def test_tag_glows_keep_pill_and_title_readable(self):
        """Keeps the pin mark and the title legible under every preset and extreme tag glow."""
        for name, t in (("light", LIGHT), ("dark", DARK)):
            for color in [c for c in TAGS.values() if c] + EXTREME_CUSTOM:
                glow = glow_color(t, color)
                # Plain glass extremes, so no previously measured glow is counted twice.
                for glass in MEASURED[name]["pill_untagged"]:
                    pill_bg = blend(glow.name(), glow.alphaF() * GLOW_PILL_FACTOR, glass)
                    title_bg = blend(glow.name(), glow.alphaF() * GLOW_TITLE_FACTOR, glass)
                    with self.subTest(theme=name, color=color, glass=glass):
                        self.assertGreaterEqual(round(contrast(t.secondary, pill_bg), 2), UI, "lifted pin")
                        self.assertGreaterEqual(round(contrast(t.text, title_bg), 2), TEXT, "title")


if __name__ == "__main__":
    unittest.main()
