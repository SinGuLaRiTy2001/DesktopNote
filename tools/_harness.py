"""Shared helpers for the visual and behavioural audits in tools/.

Every audit runs the real app code against a throw-away data folder (DESKTOPNOTE_HOME), so the
user's notes, settings and autostart entry are never touched. Audits also skip the
single-instance check, so they can run while DesktopNote itself is running.

Import this module before anything from desktopnote: it points DESKTOPNOTE_HOME at a temporary
folder first.
"""
from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
OUT = REPO / "tools" / "out"
sys.path.insert(0, str(REPO))

HOME = Path(tempfile.mkdtemp(prefix="desktopnote-audit-"))
os.environ["DESKTOPNOTE_HOME"] = str(HOME)

from PySide6.QtCore import QPoint, QRect, QTimer  # noqa: E402
from PySide6.QtGui import QColor, QFont, QImage, QPainter  # noqa: E402
from PySide6.QtWidgets import QApplication, QWidget  # noqa: E402

_qapp: QApplication | None = None


def qapp() -> QApplication:
    """Returns the QApplication, set up as desktopnote.app.main() does (font, quit behaviour)."""
    global _qapp
    if _qapp is None:
        _qapp = QApplication.instance() or QApplication(sys.argv)
        _qapp.setQuitOnLastWindowClosed(False)  # Closing fading ghost windows must not end the run.
        from desktopnote.theme import FONT_FAMILIES
        font = QFont()
        font.setFamilies(FONT_FAMILIES)
        font.setPointSizeF(10)
        _qapp.setFont(font)
    return _qapp


def start_app(notes: list[dict] | None = None, **settings):
    """Writes notes and settings to the temporary data folder and starts a DesktopNoteApp on them.

    Args:
        notes: Plain dicts in the notes.json format (see desktopnote.models.Note).
        **settings: Settings fields, e.g. theme="dark", language="en", text_scale=1.15.

    Returns:
        The started DesktopNoteApp.
    """
    qapp()
    from desktopnote import win_effects
    win_effects.set_autostart = lambda on: None  # Never write to the user's registry.
    data = {"notes": notes or [], "settings": {"first_run": False, "autostart": False, **settings}}
    (HOME / "notes.json").write_text(json.dumps(data), encoding="utf-8")
    from desktopnote.app import DesktopNoteApp
    app = DesktopNoteApp(qapp())
    app.start()
    return app


def note(x: int = 300, y: int = 200, w: int = 300, h: int = 260, title: str = "", items=(), **extra) -> dict:
    """Returns a pinned note dict in the notes.json format.

    Args:
        items: (type, text) or (type, text, extra_fields) tuples, one per item.
        **extra: Further note fields, e.g. tint="sky".
    """
    item_dicts = []
    for item in items:
        kind, text, *rest = item
        item_dicts.append({"type": kind, "text": text, **(rest[0] if rest else {})})
    return {"x": x, "y": y, "w": w, "h": h, "title": title, "pinned": True, "items": item_dicts, **extra}


def after(ms: int, fn) -> None:
    """Calls fn once on the event loop after ms milliseconds."""
    QTimer.singleShot(ms, fn)


def finish(app=None, code: int = 0) -> None:
    """Hides the tray icon, quits the event loop with code and removes the temporary data folder."""
    if app is not None:
        app.tray.hide()
    qapp().exit(code)
    shutil.rmtree(HOME, ignore_errors=True)


def on_white(img: QImage) -> QImage:
    """Returns a widget grab composited onto white, in physical pixels (DPR 1) for measuring."""
    img.setDevicePixelRatio(1.0)
    out = QImage(img.size(), QImage.Format_ARGB32)
    out.fill(QColor("#ffffff"))
    p = QPainter(out)
    p.drawImage(0, 0, img)
    p.end()
    return out


def grab(widget: QWidget) -> tuple[QImage, float]:
    """Returns (image on white, device pixel ratio) of a widget's own rendering."""
    img = widget.grab().toImage()
    dpr = img.width() / max(1, widget.width())
    return on_white(img), dpr


def rect_in(widget: QWidget, top: QWidget, dpr: float) -> QRect:
    """Returns the rectangle of widget in the coordinates of top, in physical pixels."""
    origin = widget.mapTo(top, QPoint(0, 0))
    return QRect(round(origin.x() * dpr), round(origin.y() * dpr),
                 round(widget.width() * dpr), round(widget.height() * dpr))


def ink_rows(img: QImage, r: QRect, threshold: int = 150, dark_on_light: bool = True) -> dict[int, int]:
    """Returns {row: number of ink pixels} for the rows inside r that contain ink.

    Ink is any pixel darker than threshold, or lighter than it when dark_on_light is False
    (dark themes).
    """
    rows: dict[int, int] = {}
    for y in range(max(0, r.top()), min(img.height(), r.bottom() + 1)):
        count = 0
        for x in range(max(0, r.left()), min(img.width(), r.right() + 1)):
            lightness = QColor(img.pixel(x, y)).lightness()
            count += (lightness < threshold) if dark_on_light else (lightness > threshold)
        if count:
            rows[y] = count
    return rows


def ink_centre(img: QImage, r: QRect, **kw) -> float | None:
    """Returns the vertical middle of the ink box inside r, or None without ink (kw: ink_rows)."""
    rows = ink_rows(img, r, **kw)
    return (min(rows) + max(rows)) / 2 if rows else None


def text_centre(img: QImage, r: QRect, cjk: bool, **kw) -> float | None:
    """Returns the visual vertical middle of a text line inside r, or None without ink.

    For Latin text this is the middle of cap top to baseline (descenders ignored); for CJK text
    it is the middle of the ink box. kw is passed on to ink_rows.
    """
    rows = ink_rows(img, r, **kw)
    if not rows:
        return None
    if cjk:
        return (min(rows) + max(rows)) / 2
    peak = max(rows.values())
    baseline = max(y for y, n in rows.items() if n >= 0.25 * peak)  # Descender rows are sparse.
    return (min(rows) + baseline) / 2


def save(img: QImage, name: str) -> Path:
    """Saves img as tools/out/<name> and returns its path."""
    OUT.mkdir(parents=True, exist_ok=True)
    path = OUT / name
    img.save(str(path))
    return path
