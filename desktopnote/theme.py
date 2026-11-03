"""Colours (light and dark), sizes and Qt style sheets.

Foreground colours are tuned to stay legible on the glass over any desktop, pure black or pure
white (measured): text roles reach WCAG 4.5:1, icons and the drag pill 3:1.
tests/test_contrast.py guards this.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from functools import lru_cache

from PySide6.QtGui import QColor

# --- Tags and text sizes -----------------------------------------------------

# Note tag colours: macaron pastels with hues spread around the colour wheel so they are easy to
# tell apart. They are washed softly over the top of the glass; the rest stays neutral.
# A note's tint is one of these keys, "none", or any custom "#RRGGBB".
TAGS: dict[str, str | None] = {
    "none": None,
    "strawberry": "#F4A7B9",
    "apricot": "#F8C08F",
    "lemon": "#F3E08A",
    "pistachio": "#B4DBA0",
    "sky": "#9FCBEF",
    "lavender": "#C6B3EA",
}
# Note text sizes (points) at scale 1.0, and the text size steps offered in the tray menu.
# The designed size (1.0) is the middle step and the default.
BODY_PT, TITLE_PT, DUE_PT = 10.0, 11.5, 8.5
TEXT_SCALES = (0.85, 0.92, 1.0, 1.15, 1.3)


def nearest_text_scale(scale: float) -> float:
    """Returns the step in TEXT_SCALES closest to the given scale."""
    return min(TEXT_SCALES, key=lambda step: abs(step - scale))


_HEX = re.compile(r"#[0-9A-Fa-f]{6}")


def is_hex(text: str) -> bool:
    """Returns True if the text is a "#RRGGBB" colour."""
    return bool(_HEX.fullmatch(text or ""))


def tag_color(tint: str) -> str | None:
    """Returns the colour a tint stands for.

    Args:
        tint: A key of TAGS or a custom "#RRGGBB" colour.

    Returns:
        The preset colour, the custom colour in upper case, or None when the note is untagged
        (or the key is unknown).
    """
    return tint.upper() if is_hex(tint) else TAGS.get(tint)


FONT_FAMILIES = ["Segoe UI Variable Text", "Segoe UI", "Microsoft YaHei UI"]  # System fonts, by name only.


# --- Theme -------------------------------------------------------------------


@dataclass(frozen=True)
class Theme:
    """The colours of one appearance (light or dark).

    Colour values are CSS strings ("#RRGGBB", "#AARRGGBB" or "rgba(...)") usable both in style
    sheets and with QColor.

    Attributes:
        dark: Whether this is the dark appearance.
        text: Primary text colour.
        secondary: Secondary text colour (placeholders, due dates, the drag pill).
        tertiary: Icons and decoration only; any text uses secondary or darker.
        separator: Hairlines between sections.
        accent: Accent as a foreground (Done, ticked boxes).
        accent_fill: Accent behind white text (menu highlight, text selection).
        orange: Reminder that is due soon.
        red: Overdue reminder and destructive actions.
        hover: Background of hovered controls.
        pressed: Background of pressed controls.
        border: Outline of a note window.
        base: Base glass colour (RGB hex).
        glass_alpha: Alpha of the acrylic tint.
        system_tint: "#AARRGGBB" approximating the Windows 11 acrylic tint, used by the fade-in
            stand-in.
        fill_alpha: Alpha of the note's own fill over the system acrylic (higher is more opaque).
        solid_fill: Opaque glass colour of a note hidden at an edge.
        glow_ref: Typical glass colour the tag glow is judged against.
        glow_delta_e: Perceived difference (CIE76 delta E) from glow_ref the tag glow aims for at
            the top edge, so any tag colour reads equally strong.
        menu_bg: Panel colour of menus, popups and tooltips.
        menu_hover: Menu row highlight (grey, eased in and out).
        seg_track: Segmented control track, and the switch track when off.
        seg_pill: The raised, selected pill of a segmented control.
        switch_on: Switch track colour when on.
    """

    dark: bool
    text: str
    secondary: str
    tertiary: str
    separator: str
    accent: str
    accent_fill: str
    orange: str
    red: str
    hover: str
    pressed: str
    border: str
    base: str
    glass_alpha: int
    system_tint: str
    fill_alpha: int
    solid_fill: str
    # Tag glow: any colour is fitted so it reads equally strong while the pill and the title keep
    # their contrast (see glow_color).
    glow_ref: str
    glow_delta_e: float
    menu_bg: str
    menu_hover: str
    seg_track: str
    seg_pill: str
    switch_on: str

    def glass_color(self) -> QColor:
        """Returns the base glass colour as a QColor."""
        return QColor(self.base)

    def lifted_color(self) -> str:
        """Returns the colour that tints an item row lifted for a drag, at LIFTED_ALPHA.

        A raised surface reads lighter than the glass: the light base already is lighter, the
        dark base is mixed a step towards the text colour (checked in tests/test_contrast.py).
        """
        return blend(self.text, LIFTED_DARK_TINT, self.base) if self.dark else self.base

    def acrylic_rgba(self) -> int:
        """Returns the acrylic tint packed as 0xRRGGBBAA (base colour plus glass_alpha)."""
        glass = self.glass_color()
        return (glass.red() << 24) | (glass.green() << 16) | (glass.blue() << 8) | self.glass_alpha


LIFTED_DARK_TINT = 0.18  # Text colour mixed into the dark base for a lifted row.
LIFTED_ALPHA = 0.55  # The lifted row's tint over its frosted backdrop: the glass shows through.

LIGHT = Theme(
    dark=False,
    text="#1C1C1E",
    secondary="#56565A",
    tertiary="#717177",
    separator="rgba(60,60,67,0.16)",
    accent="#0054B0",
    accent_fill="#0062CC",
    orange="#923D00",
    red="#B10900",
    hover="rgba(0,0,0,0.06)",
    pressed="rgba(0,0,0,0.12)",
    border="rgba(255,255,255,0.55)",
    base="#F7F7FA",
    glass_alpha=0x9A,
    system_tint="#78F3F3F3",
    # Opaque enough to stay readable on any desktop (measured), yet still visibly glass.
    fill_alpha=0xB8,  # About #D8 over a black desktop.
    solid_fill="#E4E4E6",
    glow_ref="#E6E6E8",
    glow_delta_e=20.0,  # The strength of the earlier hand-tuned glows.
    menu_bg="rgba(246,246,248,0.97)",
    menu_hover="rgba(0,0,0,0.07)",
    seg_track="rgba(118,118,128,0.14)",
    seg_pill="#FFFFFF",
    switch_on="#34C759",
)

DARK = Theme(
    dark=True,
    text="#F2F2F7",
    secondary="#C0C0C3",
    tertiary="#9B9B9E",
    separator="rgba(84,84,88,0.55)",
    accent="#85C2FF",
    accent_fill="#0A64D6",
    orange="#FFAD2F",
    red="#FFA59D",
    hover="rgba(255,255,255,0.08)",
    pressed="rgba(255,255,255,0.16)",
    border="rgba(255,255,255,0.14)",
    base="#1C1C1E",
    glass_alpha=0xB0,
    system_tint="#663A3A3A",
    fill_alpha=0xC0,  # About #39 over a white desktop.
    solid_fill="#2D2D2F",
    glow_ref="#2C2C2E",
    glow_delta_e=36.0,
    menu_bg="rgba(44,44,46,0.97)",
    menu_hover="rgba(255,255,255,0.10)",
    seg_track="rgba(118,118,128,0.26)",
    seg_pill="#636366",
    switch_on="#30D158",
)


# --- Tag glow ----------------------------------------------------------------

GLOW_PILL_FACTOR = 0.75  # Glow strength left where the drag pill sits (GLOW_HEIGHT falloff).
GLOW_TITLE_FACTOR = 0.30  # Glow strength left where the title's glyphs start.
WORST_GLASS = {False: "#D8D8DA", True: "#39393B"}  # Least favourable measured glass per theme.


def luminance(color: str) -> float:
    """Returns the WCAG relative luminance of a "#RRGGBB" (or "#AARRGGBB") colour."""
    hex6 = color.lstrip("#")[-6:]
    channels = [int(hex6[i:i + 2], 16) / 255 for i in (0, 2, 4)]
    channels = [x / 12.92 if x <= 0.04045 else ((x + 0.055) / 1.055) ** 2.4 for x in channels]
    return 0.2126 * channels[0] + 0.7152 * channels[1] + 0.0722 * channels[2]


def contrast(a: str, b: str) -> float:
    """Returns the WCAG contrast ratio between two colours (1.0 to 21.0)."""
    hi, lo = sorted((luminance(a), luminance(b)), reverse=True)
    return (hi + 0.05) / (lo + 0.05)


def blend(top: str, alpha: float, under: str) -> str:
    """Returns the "#rrggbb" colour of top painted with the given alpha over under."""
    top_rgb = [int(top.lstrip("#")[-6:][i:i + 2], 16) for i in (0, 2, 4)]
    under_rgb = [int(under.lstrip("#")[-6:][i:i + 2], 16) for i in (0, 2, 4)]
    return "#" + "".join(f"{round(x * alpha + y * (1 - alpha)):02x}" for x, y in zip(top_rgb, under_rgb))


def _lab(color: str) -> tuple[float, float, float]:
    """Returns the CIE L*a*b* coordinates of an sRGB colour (D65 white point)."""
    hex6 = color.lstrip("#")[-6:]
    r, g, b = (x / 12.92 if x <= 0.04045 else ((x + 0.055) / 1.055) ** 2.4
               for x in (int(hex6[i:i + 2], 16) / 255 for i in (0, 2, 4)))
    x = (0.4124 * r + 0.3576 * g + 0.1805 * b) / 0.95047
    y = 0.2126 * r + 0.7152 * g + 0.0722 * b
    z = (0.0193 * r + 0.1192 * g + 0.9505 * b) / 1.08883
    f = lambda v: v ** (1 / 3) if v > 0.008856 else 7.787 * v + 16 / 116  # noqa: E731
    return 116 * f(y) - 16, 500 * (f(x) - f(y)), 200 * (f(y) - f(z))


def _delta_e(a: str, b: str) -> float:
    """Returns the CIE76 perceived difference between two colours."""
    return sum((p - q) ** 2 for p, q in zip(_lab(a), _lab(b))) ** 0.5


@lru_cache(maxsize=64)
def glow_color(t: Theme, color: str | None) -> QColor | None:
    """Returns the top-edge colour of a tag glow, alpha included, for any tag colour.

    Pastels are deepened on light glass and dark colours lifted on dark glass, so the hue stays
    recognisable. The alpha is then solved for an equal perceived strength (t.glow_delta_e) and
    capped where the drag pill (lifted grey) or the title would lose contrast over the worst glass.

    Args:
        t: The theme the glow is drawn on.
        color: The tag colour, or None for an untagged note.

    Returns:
        The glow colour, or None when color is None.
    """
    if color is None:
        return None
    source = QColor(color)
    hue, sat, light = source.hslHueF(), source.hslSaturationF(), source.lightnessF()
    light = min(light, 0.66) if not t.dark else max(light, 0.62)
    if sat > 0.08:  # A real hue (not grey): keep it vivid enough to read as that colour.
        sat = max(sat, 0.55)
    glow_hex = QColor.fromHslF(max(hue, 0.0), sat, light).name()

    def keeps_contrast(alpha: float) -> bool:
        worst = WORST_GLASS[t.dark]
        pill_bg = blend(glow_hex, alpha * GLOW_PILL_FACTOR, worst)
        title_bg = blend(glow_hex, alpha * GLOW_TITLE_FACTOR, worst)
        return contrast(t.secondary, pill_bg) >= 3.0 and contrast(t.text, title_bg) >= 4.5

    lo, hi = 0.0, 0.85
    for _ in range(24):  # Bisection: the strongest alpha not above the target strength.
        mid = (lo + hi) / 2
        if _delta_e(blend(glow_hex, mid, t.glow_ref), t.glow_ref) < t.glow_delta_e:
            lo = mid
        else:
            hi = mid
    alpha = lo
    while alpha > 0.05 and not keeps_contrast(alpha):
        alpha -= 0.01
    out = QColor(glow_hex)
    out.setAlphaF(alpha)
    return out


# --- Style sheets ------------------------------------------------------------


def note_qss(t: Theme) -> str:
    """Returns the style sheet of a note window and its rows."""
    return f"""
    QWidget {{ color: {t.text}; background: transparent; }}
    /* Placeholders: set here, not via the palette - a style sheet resets the palette's colour to Qt's faded default. */
    QTextEdit {{ border: none; background: transparent;
        selection-background-color: {t.accent_fill}; selection-color: white;
        placeholder-text-color: {t.secondary}; }}
    QTextEdit[dim="true"] {{ color: {t.secondary}; }}  /* Done to-dos and reminders. */
    QLineEdit#title {{
        border: none; background: transparent; color: {t.text}; padding: 3px 0 1px 6px;
        selection-background-color: {t.accent_fill}; selection-color: white; placeholder-text-color: {t.secondary};
    }}
    QToolButton {{ border: none; border-radius: 8px; padding: 4px; background: transparent; }}
    QToolButton:pressed {{ background: {t.pressed}; }}
    /* font: ItemRow.set_text_scale */
    QToolButton#dueButton {{ color: {t.secondary}; padding: 0px 0px 2px 6px; border-radius: 6px; }}
    QToolButton#dueButton[state="soon"] {{ color: {t.orange}; }}
    QToolButton#dueButton[state="overdue"] {{ color: {t.red}; }}
    QToolButton#confirmDelete {{ color: {t.red}; font-weight: 600; padding: 4px 10px; }}
    QToolButton#doneButton {{ color: {t.accent}; font-weight: 600; padding: 3px 10px 5px 10px; }}  /* glyphs sit low */
    QLabel#placeholder {{ color: {t.secondary}; }}  /* font: NoteWindow._scale_hint */
    QFrame#separator {{ background: {t.separator}; max-height: 1px; min-height: 1px; }}
    QScrollArea, QScrollArea > QWidget > QWidget {{ background: transparent; border: none; }}
    """


def menu_qss(t: Theme) -> str:
    """Returns the style sheet of a GlassMenu (rows only; the menu paints its own panel)."""
    return f"""
    /* GlassMenu paints the panel and the eased row highlight; rows run edge to edge. */
    QMenu {{ background: transparent; color: {t.text}; border: none; padding: 0; }}
    QMenu::item {{ padding: 8px 34px 8px 16px; background: transparent; }}
    QMenu::item:selected {{ background: transparent; color: {t.text}; }}
    QMenu::item:disabled {{ color: {t.tertiary}; }}
    QMenu::icon {{ padding-left: 21px; }}  /* icons' left edge on x = 14, like the controls (measured) */
    QMenu::separator {{ height: 1px; background: {t.separator}; margin: 0; }}  /* highlights meet it directly */
    QMenu::indicator {{ width: 0; }}
    """


def popup_qss(t: Theme) -> str:
    """Returns the style sheet of a GlassPopup (date and time picker, colour picker)."""
    return f"""
    QWidget#popup {{ background: {t.menu_bg}; border: 1px solid {t.separator}; border-radius: 8px; }}
    QWidget {{ color: {t.text}; }}
    QLabel#secondary {{ color: {t.secondary}; }}
    QFrame#hairline {{ background: {t.separator}; max-height: 1px; min-height: 1px; margin: 2px 4px; }}
    QDateTimeEdit {{
        background: transparent; border: none; border-radius: 6px; padding: 1px 6px 5px 6px;  /* digits sit low */
        selection-background-color: {t.pressed}; selection-color: {t.text}; color: {t.text};
    }}
    QDateTimeEdit:hover, QDateTimeEdit:focus {{ background: {t.hover}; }}
    QDateTimeEdit::up-button, QDateTimeEdit::down-button {{ width: 0; border: none; }}
    QPushButton {{ border: none; border-radius: 6px; padding: 4px 8px; background: transparent; }}
    QPushButton:pressed {{ background: {t.pressed}; }}
    QPushButton#preset {{ color: {t.secondary}; padding: 4px 6px; }}
    QPushButton#preset:hover {{ color: {t.text}; }}
    QPushButton#primary {{ color: {t.text}; font-weight: 600; }}
    QToolButton {{ border: none; background: transparent; }}
    QToolButton:pressed {{ background: {t.pressed}; border-radius: 4px; }}
    QPushButton#danger {{ color: {t.red}; }}
    QLineEdit#hex {{
        background: {t.hover}; border: none; border-radius: 6px; padding: 3px 6px 4px 6px;
        color: {t.text}; selection-background-color: {t.accent_fill}; selection-color: white;
    }}
    """
