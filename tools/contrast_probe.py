"""Measures the real glass backgrounds that tests/test_contrast.py checks colours against.

The note glass is translucent, so the background behind text depends on the desktop. This
script puts notes (untagged and with every tag) and a date popup over pure black, pure white
and a saturated striped desktop, in the light and the dark theme, hides their contents so only
glass is left, and samples the screen. It prints the extremes per zone in the MEASURED format.

Re-run it when Theme.fill_alpha, the tag glow (theme.glow_color / GLOW_HEIGHT) or the popup
backdrop change, and paste the result into tests/test_contrast.py.

    python tools/contrast_probe.py
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _harness as H  # noqa: E402
from PySide6.QtCore import Qt  # noqa: E402
from PySide6.QtGui import QColor, QPainter  # noqa: E402
from PySide6.QtWidgets import QWidget  # noqa: E402

DESKTOPS = ("black", "white", "stripes")


class Desktop(QWidget):
    """A stand-in desktop behind the notes.

    Attributes:
        kind: What to paint: "black", "white" or "stripes".
    """

    def __init__(self):
        super().__init__(None, Qt.WindowStaysOnTopHint | Qt.FramelessWindowHint | Qt.Tool)
        self.kind = "black"

    def paintEvent(self, _):
        p = QPainter(self)
        # Saturated colours, alternately bright and dark: the worst cases for hue shifts.
        if self.kind == "stripes":
            for i in range(0, self.width(), 20):
                stripe = QColor.fromHsv((i * 7) % 360, 255, 255 if (i // 20) % 2 else 90)
                p.fillRect(i, 0, 20, self.height(), stripe)
        else:
            p.fillRect(self.rect(), QColor(self.kind))


def main() -> int:
    """Samples every theme and desktop combination, prints MEASURED and returns the exit code."""
    argparse.ArgumentParser(description=__doc__.split("\n\n")[0]).parse_args()  # Only --help.
    from desktopnote.theme import TAGS
    from desktopnote.widgets import DateTimePopup
    tints = list(TAGS)
    H.qapp()
    desk = Desktop()  # Created first, so the pinned notes open above it.
    desk.setGeometry(380, 260, 250 * len(tints) + 80, 620)
    desk.show()
    reminder = ("reminder", "x", {"due": "2030-01-01T09:00"})
    app = H.start_app([H.note(420 + i * 250, 300, 230, 220, items=[reminder], tint=tint)
                       for i, tint in enumerate(tints)], theme="light", language="en")
    wins = list(app.windows.values())
    samples: dict[str, dict[str, list[str]]] = {"light": {}, "dark": {}}
    plan = [(theme, desktop) for theme in ("light", "dark") for desktop in DESKTOPS]

    def screen():
        """Returns a pixel reader for the stand-in desktop's area, in global logical coordinates."""
        from desktopnote import motion
        area = desk.geometry()
        img = motion.grab_screen(area).toImage()
        dpr = img.width() / area.width()
        return lambda x, y: QColor(img.pixel(int((x - area.x()) * dpr), int((y - area.y()) * dpr))).name()

    def step(i: int):
        """Sets up the i-th theme and desktop combination and opens the date popup."""
        if i == len(plan):
            report()
            return
        theme, kind = plan[i]
        app.set_theme_mode(theme)
        desk.kind = kind
        desk.update()
        for win in wins:
            for child in win.findChildren(QWidget):
                child.setVisible(True)
        wins[0]._pick_due(wins[0].rows[0])
        H.after(800, lambda: sample(i, theme))

    def sample(i: int, theme: str):
        """Samples the date popup, then hides all note contents so only glass is left."""
        popups = [x for x in H.qapp().topLevelWidgets() if isinstance(x, DateTimePopup) and x.isVisible()]
        pixel = screen()
        if popups:
            frame = popups[0].frameGeometry()
            samples[theme].setdefault("popup", []).extend(
                pixel(frame.x() + x, frame.y() + y)
                for x in (6, 8, frame.width() - 8) for y in (frame.height() // 2, frame.height() - 30))
            popups[0].close()
        for win in wins:
            for child in win.findChildren(QWidget):
                child.setVisible(False)  # Glass only: no text or icons in the samples.
        H.after(500, lambda: sample_glass(i, theme))

    def sample_glass(i: int, theme: str):
        """Samples the body, row and drag-pill zones of every note, then moves to the next step."""
        pixel = screen()
        for win, tint in zip(wins, tints):
            frame = win.geometry()
            zone = "untagged" if tint == "none" else "tagged"

            def zone_pixels(xs, ys, frame=frame):
                """Returns the colours at the note-relative points xs x ys."""
                return (pixel(frame.x() + x, frame.y() + y) for x in xs for y in ys)
            samples[theme].setdefault("body", []).extend(zone_pixels(range(20, 210, 10), (60, 110, 150, 200)))
            samples[theme].setdefault(f"rows_{zone}", []).extend(zone_pixels(range(20, 210, 15), (20, 28, 36)))
            samples[theme].setdefault(f"pill_{zone}", []).extend(zone_pixels((80, 95, 135, 150), (6, 9, 12)))
        step(i + 1)

    def report():
        """Prints the darkest and lightest sample per zone as MEASURED and ends the run."""
        lightness = lambda c: H.QColor(c).lightnessF()  # noqa: E731
        print("MEASURED = {")
        for theme, zones in samples.items():
            print(f'    "{theme}": {{')
            for zone, colors in sorted(zones.items()):
                darkest, lightest = min(colors, key=lightness), max(colors, key=lightness)
                print(f'        "{zone}": ["{darkest}", "{lightest}"],  # darkest, lightest of {len(colors)}')
            print("    },")
        print("}")
        desk.close()
        H.finish(app)

    H.after(1500, lambda: step(0))
    return H.qapp().exec()


if __name__ == "__main__":
    sys.exit(main())
