"""Minimal line icons, drawn as inline SVG and recoloured on demand."""
from __future__ import annotations

import base64
import math
from functools import lru_cache

from PySide6.QtCore import QBuffer, QByteArray, QIODevice, QRectF, QSize, Qt
from PySide6.QtGui import QColor, QGuiApplication, QIcon, QPainter, QPen, QPixmap
from PySide6.QtSvg import QSvgRenderer

# --- SVG sources -------------------------------------------------------------


def _stroke(width: float = 1.8) -> str:
    """Returns the SVG attributes of a rounded line of the given width in colour "{c}"."""
    return f'fill="none" stroke="{{c}}" stroke-width="{width}" stroke-linecap="round" stroke-linejoin="round"'


_STROKE = _stroke()

# SVG bodies on a 24 x 24 view box; "{c}" is replaced with the requested colour.
_PATHS: dict[str, str] = {
    "plus": f'<path {_STROKE} d="M12 5v14M5 12h14"/>',
    "text": f'<path {_STROKE} d="M5 6h14M5 10.5h14M5 15h9M5 19.5h6"/>',
    "checklist": (
        f'<path {_STROKE} d="M4 6.5l1.6 1.6L8.5 5M4 13.5l1.6 1.6 2.9-3.1M11.5 7h8.5M11.5 14h8.5"/>'
        f'<circle cx="6" cy="19.5" r="1.4" fill="{{c}}"/><path {_STROKE} d="M11.5 19.5h8.5"/>'
    ),
    "bell": (
        f'<path {_STROKE} d="M6 16.5V11a6 6 0 0 1 12 0v5.5l1.5 1.8h-15z"/>'
        f'<path {_STROKE} d="M10 20.5a2.2 2.2 0 0 0 4 0"/>'
    ),
    "clock": f'<circle {_STROKE} cx="12" cy="12" r="8.5"/><path {_STROKE} d="M12 7.5V12l3 2"/>',
    "warning": (
        '<path fill="{c}" d="M10.3 3.9a2 2 0 0 1 3.4 0l8 13.8A2 2 0 0 1 20 20.7H4a2 2 0 0 1-1.7-3z"/>'
        '<path fill="none" stroke="#fff" stroke-width="2" stroke-linecap="round" d="M12 9v4.6"/>'
        '<circle cx="12" cy="17" r="1.2" fill="#fff"/>'
    ),
    "pencil": (
        f'<path {_STROKE} d="M15.5 4.5l4 4L9 19l-5 1 1-5z"/><path {_STROKE} d="M13.5 6.5l4 4"/>'
    ),
    "check": f'<path {_stroke(2.2)} d="M5 12.5l4.5 4.5L19 7.5"/>',
    "ellipsis": (
        '<circle cx="5.5" cy="12" r="1.7" fill="{c}"/><circle cx="12" cy="12" r="1.7" fill="{c}"/>'
        '<circle cx="18.5" cy="12" r="1.7" fill="{c}"/>'
    ),
    "trash": (
        f'<path {_STROKE} d="M4.5 6.5h15M9.5 6.5V4.8c0-.7.5-1.3 1.3-1.3h2.4c.8 0 1.3.6 1.3 1.3v1.7'
        f'M6.5 6.5l.9 12.3c.1 1 .9 1.7 1.9 1.7h5.4c1 0 1.8-.7 1.9-1.7l.9-12.3M10 10.5v6M14 10.5v6"/>'
    ),
    "xmark_circle": (
        '<circle cx="12" cy="12" r="9" fill="{c}"/>'
        '<path fill="none" stroke="#fff" stroke-width="2" stroke-linecap="round" d="M9 9l6 6M15 9l-6 6"/>'
    ),
    "pin": (
        f'<path {_STROKE} d="M9 3.5h6l-1 5.5 3.5 3.5v1.5h-11v-1.5L10 9z"/><path {_STROKE} d="M12 14v6.5"/>'
    ),
    "calendar": (
        f'<rect {_STROKE} x="3.5" y="5" width="17" height="15.5" rx="3"/>'
        f'<path {_STROKE} d="M3.5 10h17M8 3v4M16 3v4"/>'
    ),
    "note": (
        f'<rect {_STROKE} x="4" y="3.5" width="16" height="17" rx="3.5"/>'
        f'<path {_STROKE} d="M8 9h8M8 13h8M8 17h5"/>'
    ),
    "circle": '<circle cx="12" cy="12" r="8" fill="{c}" stroke="rgba(0,0,0,.18)" stroke-width="1"/>',
    "settings": (
        f'<path {_STROKE} d="M4 7.5h8.5M16.5 7.5H20M4 16.5h3.5M11.5 16.5H20"/>'
        f'<circle {_STROKE} cx="14.5" cy="7.5" r="2"/><circle {_STROKE} cx="9.5" cy="16.5" r="2"/>'
    ),
    "theme_auto": (
        f'<circle {_STROKE} cx="12" cy="12" r="8"/><path fill="{{c}}" d="M12 4a8 8 0 0 1 0 16z"/>'
    ),
    "sun": (
        f'<circle {_STROKE} cx="12" cy="12" r="3.8"/>'
        f'<path {_STROKE} d="M12 3v1.8M12 19.2V21M3 12h1.8M19.2 12H21M5.6 5.6l1.3 1.3M17.1 17.1l1.3 1.3'
        f'M5.6 18.4l1.3-1.3M17.1 6.9l1.3-1.3"/>'
    ),
    "moon": f'<path {_STROKE} d="M19.5 14.5A8 8 0 0 1 9.5 4.5a8 8 0 1 0 10 10z"/>',
    "globe": (
        f'<circle {_STROKE} cx="12" cy="12" r="8.5"/>'
        f'<path {_STROKE} d="M3.5 12h17M12 3.5c2.4 2.5 3.5 5.3 3.5 8.5s-1.1 6-3.5 8.5'
        f'M12 3.5C9.6 6 8.5 8.8 8.5 12s1.1 6 3.5 8.5"/>'
    ),
    "power": f'<path {_STROKE} d="M12 3.5v8M7.2 6.3a7.5 7.5 0 1 0 9.6 0"/>',
    "folder": (
        f'<path {_STROKE} d="M3.5 7.5a2 2 0 0 1 2-2h3.7l2 2h7.3a2 2 0 0 1 2 2v8a2 2 0 0 1-2 2h-13'
        f'a2 2 0 0 1-2-2z"/>'
    ),
    "chevron_left": f'<path {_stroke(2)} d="M14.5 6L8.5 12l6 6"/>',
    "chevron_right": f'<path {_stroke(2)} d="M9.5 6l6 6-6 6"/>',
    "chevron_up": f'<path {_stroke(2.2)} d="M6.5 14.5L12 9l5.5 5.5"/>',
    "chevron_down": f'<path {_stroke(2.2)} d="M6.5 9.5L12 15l5.5-5.5"/>',
    "quit": (
        f'<path {_STROKE} d="M13.5 4.5h-7a2 2 0 0 0-2 2v11a2 2 0 0 0 2 2h7M10.5 12h9M16.5 8.5L20 12l-3.5 3.5"/>'
    ),
    "checkbox_off": f'<circle {_stroke(1.6)} cx="12" cy="12" r="8.5"/>',
    "checkbox_on": (
        '<circle cx="12" cy="12" r="9.3" fill="{c}"/>'
        '<path fill="none" stroke="#fff" stroke-width="2.2" stroke-linecap="round" '
        'stroke-linejoin="round" d="M7.8 12.3l2.8 2.8 5.6-5.8"/>'
    ),
    # Pastel tile lit from the upper left (gradient axis 20 degrees off vertical), pink to purple,
    # with violet lines. It ignores the requested colour.
    "app": (
        '<defs><linearGradient id="g" x1="0.318" y1="0" x2="0.682" y2="1">'
        '<stop offset="0" stop-color="#FFB3DA"/><stop offset="1" stop-color="#9C8CFF"/></linearGradient></defs>'
        '<rect x="2" y="2" width="20" height="20" rx="5.5" fill="url(#g)"/>'
        '<path fill="none" stroke="#4A2C96" stroke-opacity="0.9" stroke-width="1.6" stroke-linecap="round" d="M7 9h10M7 12.5h10M7 16h6"/>'
    ),
}


def svg(name: str, color: str) -> bytes:
    """Returns the complete SVG document of an icon drawn in the given colour."""
    body = _PATHS[name].replace("{c}", color)
    return f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24">{body}</svg>'.encode()


# --- Rendering ---------------------------------------------------------------

# Menu rows centre their icon box on the line box, which sits a little above the text's visual
# midline (cap middle for Latin, character middle for CJK): measured 1-1.5 px in both languages,
# so menu icons are drawn this much lower inside their box.
MENU_ICON_DY = 1
MENU_ICON = 16  # Size of menu icons in logical pixels.


def render_dpr() -> float:
    """Returns the pixel density to render icons at.

    This is the density of the sharpest connected screen, rounded up to whole steps and at
    least 2, so icons stay crisp at 250-300 % scaling and on mixed-DPI setups.
    """
    screens = QGuiApplication.screens() if QGuiApplication.instance() is not None else []
    return float(max([2, *(math.ceil(screen.devicePixelRatio()) for screen in screens)]))


def pixmap(name: str, color: str, size: int = 18, dpr: float | None = None, dy: float = 0.0) -> QPixmap:
    """Returns an icon rendered as a (cached) pixmap.

    Args:
        name: Key of the icon in _PATHS.
        color: Colour the icon is drawn in.
        size: Edge length in logical pixels.
        dpr: Device pixel ratio to render at; defaults to render_dpr().
        dy: Logical pixels the drawing is shifted down inside the pixmap.
    """
    return _pixmap(name, color, size, dpr or render_dpr(), dy)


@lru_cache(maxsize=256)
def _pixmap(name: str, color: str, size: int, dpr: float, dy: float) -> QPixmap:
    renderer = QSvgRenderer(QByteArray(svg(name, color)))
    pm = QPixmap(int(size * dpr), int(size * dpr))
    pm.fill(Qt.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing)
    renderer.render(p, QRectF(0, dy * dpr, pm.width(), pm.height()))
    p.end()
    pm.setDevicePixelRatio(dpr)
    return pm


def icon(name: str, color: str, size: int = 18) -> QIcon:
    """Returns an icon as a QIcon of the given logical size."""
    qicon = QIcon()
    qicon.addPixmap(pixmap(name, color, size))
    return qicon


def menu_icon(name: str, color: str) -> QIcon:
    """Returns an icon for a menu row, sitting on the row text's midline."""
    qicon = QIcon()
    qicon.addPixmap(pixmap(name, color, MENU_ICON, dy=MENU_ICON_DY))
    return qicon


def inline_img(name: str, color: str, size: int = 16, lift: float = 0.0) -> str:
    """Returns an <img> tag embedding the icon, for use inside rich-text labels.

    Args:
        name: Key of the icon in _PATHS.
        color: Colour the icon is drawn in.
        size: Edge length in logical pixels.
        lift: Logical pixels (in half steps) the glyph is raised above where
            vertical-align: middle would put it.
    """
    return _inline_img(name, color, size, lift, render_dpr())


@lru_cache(maxsize=32)
def _inline_img(name: str, color: str, size: int, lift: float, dpr: float) -> str:
    # Transparent rows below the glyph: middle-aligned, the glyph then rises by lift.
    pad = round(2 * lift * dpr)
    img = QPixmap(round(size * dpr), round(size * dpr) + pad)
    img.setDevicePixelRatio(dpr)
    img.fill(Qt.transparent)
    p = QPainter(img)
    p.drawPixmap(0, 0, pixmap(name, color, size, dpr))
    p.end()
    buffer = QBuffer()
    buffer.open(QIODevice.WriteOnly)
    img.toImage().save(buffer, "PNG")
    data = base64.b64encode(bytes(buffer.data())).decode()
    height = size + pad / dpr
    return f'<img src="data:image/png;base64,{data}" width="{size}" height="{height:g}" style="vertical-align: middle">'


def dot(fill: str | None, ring: str, size: int = MENU_ICON) -> QIcon:
    """Returns a note's tag as a menu icon: a filled dot, or a quiet ring when untagged.

    Args:
        fill: Tag colour, or None for an untagged note.
        ring: Colour of the ring drawn for an untagged note.
        size: Edge length of the icon in logical pixels; the dot is half of it.
    """
    return _dot(fill, ring, size, render_dpr())


@lru_cache(maxsize=32)
def _dot(fill: str | None, ring: str, size: int, dpr: float) -> QIcon:
    pm = QPixmap(int(size * dpr), int(size * dpr))
    pm.fill(Qt.transparent)
    pm.setDevicePixelRatio(dpr)
    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing)
    rect = QRectF(0, MENU_ICON_DY, size, size).adjusted(size * 0.25, size * 0.25, -size * 0.25, -size * 0.25)
    if fill is None:
        p.setPen(QPen(QColor(ring), 1.5))
        p.setBrush(Qt.NoBrush)
        p.drawEllipse(rect.adjusted(0.75, 0.75, -0.75, -0.75))
    else:
        p.setPen(QPen(QColor(0, 0, 0, 30), 1))  # Keeps pale tag colours visible.
        p.setBrush(QColor(fill))
        p.drawEllipse(rect)
    p.end()
    qicon = QIcon()
    qicon.addPixmap(pm)
    return qicon


def app_icon() -> QIcon:
    """Returns the application icon with pixmaps for the common Windows icon sizes."""
    qicon = QIcon()
    for size in (16, 24, 32, 48, 64, 128, 256):
        qicon.addPixmap(pixmap("app", "#000", size, 1.0))
    return qicon


ICON_SIZE = QSize(18, 18)  # Icon size of a note's tool buttons.
