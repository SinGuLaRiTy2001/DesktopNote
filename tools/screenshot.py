"""Takes the README screenshots: a few sample notes on a soft wallpaper, light and dark.

The notes are real app windows over a stand-in wallpaper, captured from the screen, so the
frosted glass shows as it does on a desktop. The scene is placed on the monitor with the highest
Windows display scaling, which sets the images' resolution (1.5x the logical size at 150 %).
Images go to docs/.

    python tools/screenshot.py                    # -> docs/screenshot-light.png, -dark.png
    python tools/screenshot.py --language zh
"""
from __future__ import annotations

import argparse
import sys
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _harness as H  # noqa: E402
from PySide6.QtCore import QPointF, QRect, Qt  # noqa: E402
from PySide6.QtGui import QColor, QGuiApplication, QLinearGradient, QPainter, QRadialGradient  # noqa: E402
from PySide6.QtWidgets import QWidget  # noqa: E402

DOCS = H.REPO / "docs"
SCENE_W, SCENE_H = 860, 460  # Captured area in logical px.
# Wallpaper per theme: base gradient top and bottom, then soft blobs (x, y, radius, colour).
WALLPAPERS = {
    "light": ("#e9eef6", "#f6eef2", [(0.15, 0.25, 0.45, "#a9c8f0"), (0.80, 0.20, 0.40, "#f3b6cf"),
                                     (0.55, 0.95, 0.50, "#c9b8f2"), (0.05, 0.95, 0.35, "#bfe3c8")]),
    "dark": ("#141824", "#1d1622", [(0.15, 0.25, 0.45, "#2c4f86"), (0.80, 0.20, 0.40, "#7a3557"),
                                    (0.55, 0.95, 0.50, "#4a3a86"), (0.05, 0.95, 0.35, "#245a45")]),
}


def content(language: str) -> list[tuple[str, str, list]]:
    """Returns (title, tint, items) for each sample note in language."""
    now = datetime.now().replace(second=0, microsecond=0)
    soon = (now + timedelta(minutes=40 - now.minute % 5)).isoformat()  # A round time, about 40 min away.
    later = (now + timedelta(days=1)).replace(hour=9, minute=30).isoformat()
    if language == "zh":
        return [
            ("本周", "sky", [("todo", "整理会议纪要", {"done": True}), ("todo", "回复设计反馈"),
                            ("reminder", "提交报告", {"due": soon}), ("reminder", "预约牙医", {"due": later})]),
            ("购物清单", "strawberry", [("todo", "牛奶"), ("todo", "面包", {"done": True}), ("todo", "咖啡豆")]),
            ("", "lemon", [("text", "灵感：把记事板做成像玻璃一样轻盈。\n用颜色标签区分每一张。")]),
        ]
    return [
        ("This week", "sky", [("todo", "Tidy up meeting notes", {"done": True}), ("todo", "Reply to design feedback"),
                              ("reminder", "Hand in the report", {"due": soon}),
                              ("reminder", "Book the dentist", {"due": later})]),
        ("Groceries", "strawberry", [("todo", "Milk"), ("todo", "Bread", {"done": True}), ("todo", "Coffee beans")]),
        ("", "lemon", [("text", "Idea: notes as light as a sheet of glass.\nColour tags tell them apart.")]),
    ]


class Wallpaper(QWidget):
    """A stand-in desktop: a soft gradient with blurred colour blobs.

    Attributes:
        theme: Which wallpaper of WALLPAPERS to paint.
    """

    def __init__(self):
        super().__init__(None, Qt.WindowStaysOnTopHint | Qt.FramelessWindowHint | Qt.Tool)
        self.theme = "light"

    def paintEvent(self, _):
        top, bottom, blobs = WALLPAPERS[self.theme]
        w, h = self.width(), self.height()
        p = QPainter(self)
        base = QLinearGradient(0, 0, 0, h)
        base.setColorAt(0, QColor(top))
        base.setColorAt(1, QColor(bottom))
        p.fillRect(self.rect(), base)
        for x, y, r, colour in blobs:
            blob = QRadialGradient(QPointF(x * w, y * h), r * w)
            inner, outer = QColor(colour), QColor(colour)
            outer.setAlpha(0)
            blob.setColorAt(0, inner)
            blob.setColorAt(1, outer)
            p.fillRect(self.rect(), blob)


def main() -> int:
    """Captures the scene in both themes, saves the images and returns the exit code."""
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--language", choices=("en", "zh"), default="en")
    args = ap.parse_args()
    H.qapp()
    screen = max(QGuiApplication.screens(), key=lambda s: s.devicePixelRatio())
    centre = screen.availableGeometry().center()
    scene = QRect(centre.x() - SCENE_W // 2, centre.y() - SCENE_H // 2, SCENE_W, SCENE_H)
    wall = Wallpaper()  # Created first, so the pinned notes open above it.
    wall.setGeometry(scene)
    wall.show()
    x0, y0 = scene.x(), scene.y()
    layout = [(x0 + 40, y0 + 50, 300, 254), (x0 + 380, y0 + 40, 272, 182), (x0 + 540, y0 + 270, 272, 112)]
    notes = [H.note(x, y, w, h, title, items, tint=tint)
             for (x, y, w, h), (title, tint, items) in zip(layout, content(args.language))]
    app = H.start_app(notes, theme="light", language=args.language)
    from desktopnote.motion import grab_screen
    themes = ["light", "dark"]

    def step(i: int):
        """Switches to the i-th theme and captures it once the crossfade has settled."""
        if i == len(themes):
            H.finish(app)
            return
        wall.theme = themes[i]
        wall.update()
        app.set_theme_mode(themes[i])
        H.after(1200, lambda: shoot(i))

    def shoot(i: int):
        """Saves the screen inside the scene as docs/screenshot-<theme>.png."""
        pm = grab_screen(scene)
        if pm is None:
            print("FAILED: scene is not on a screen")
            H.finish(app, 1)
            return
        DOCS.mkdir(exist_ok=True)
        path = DOCS / f"screenshot-{themes[i]}.png"
        pm.save(str(path))
        print(f"RESULT {path}")
        step(i + 1)

    H.after(1500, lambda: step(0))
    return H.qapp().exec()


if __name__ == "__main__":
    sys.exit(main())
