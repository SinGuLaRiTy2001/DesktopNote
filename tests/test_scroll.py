"""Scroll tests: a note with more items than fit scrolls smoothly, not in jumps.

Builds real note windows; CI runs them with QT_QPA_PLATFORM=offscreen.
"""
import os
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from PySide6.QtCore import QElapsedTimer, QPoint, QPointF, Qt  # noqa: E402
from PySide6.QtGui import QWheelEvent  # noqa: E402
from PySide6.QtTest import QTest  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from desktopnote.models import Item, Note  # noqa: E402
from desktopnote.note_window import NoteWindow  # noqa: E402
from desktopnote.theme import LIGHT  # noqa: E402
from desktopnote.widgets import OverlayScrollBar  # noqa: E402

_app = QApplication.instance() or QApplication(sys.argv)
_app.setQuitOnLastWindowClosed(False)


def _wheel(widget, notches: int) -> None:
    """Sends a mouse-wheel event of ``notches`` notches (negative = down) to ``widget``."""
    pos = QPointF(widget.width() / 2, widget.height() / 2)
    event = QWheelEvent(pos, widget.mapToGlobal(pos), QPoint(), QPoint(0, 120 * notches),
                        Qt.NoButton, Qt.NoModifier, Qt.NoScrollPhase, False)
    QApplication.sendEvent(widget, event)


class SmoothScrollTest(unittest.TestCase):
    """The wheel, the track and new rows glide the content; dragging the handle does not."""

    def setUp(self):
        items = [Item(type="todo", text=f"Item {i}") for i in range(30)]
        self.win = NoteWindow(Note(w=260, h=260, items=items), LIGHT, 60, 1.0)
        self.win.show()
        QTest.qWait(50)
        self.bar = self.win.scroll.verticalScrollBar()
        self.assertGreater(self.bar.maximum(), 0, "the note should be able to scroll")

    def tearDown(self):
        self.win.close()
        self.win.deleteLater()

    def record(self, ms: int) -> list[int]:
        """Returns the scroll value sampled every few milliseconds for ``ms`` milliseconds."""
        values, clock = [], QElapsedTimer()
        clock.start()
        while clock.elapsed() < ms:
            QTest.qWait(4)
            values.append(self.bar.value())
        return values

    def assert_glides(self, values: list[int], target: int) -> None:
        """Checks that ``values`` move monotonically to ``target`` in several steps."""
        self.assertEqual(values[-1], target)
        self.assertGreater(len(set(values)), 3, f"jumped instead of gliding: {values}")
        steps = [b - a for a, b in zip(values, values[1:])]
        self.assertTrue(all(s >= 0 for s in steps) or all(s <= 0 for s in steps), values)

    def test_wheel_glides(self):
        _wheel(self.win.scroll.viewport(), -1)
        self.assertLess(self.bar.value(), 60, "the first frame should not be the destination")
        self.assert_glides(self.record(OverlayScrollBar.SCROLL_MS + 150), 60)

    def test_quick_notches_add_up(self):
        for _ in range(3):
            _wheel(self.win.scroll.viewport(), -1)
            QTest.qWait(30)
        self.assert_glides(self.record(OverlayScrollBar.SCROLL_MS + 150), 180)

    def test_wheel_stops_at_the_ends(self):
        _wheel(self.win.scroll.viewport(), 3)
        self.assertEqual(self.record(OverlayScrollBar.SCROLL_MS + 100)[-1], 0)
        for _ in range(100):
            _wheel(self.win.scroll.viewport(), -1)
        self.assertEqual(self.record(OverlayScrollBar.SCROLL_MS + 100)[-1], self.bar.maximum())

    def test_track_click_glides_one_page(self):
        indicator = self.win.scroll_indicator
        indicator.flash()
        QTest.qWait(OverlayScrollBar.FADE_IN_MS + 50)
        below_handle = QPoint(indicator.width() // 2, indicator.height() - 4)
        QTest.mouseClick(indicator, Qt.LeftButton, Qt.NoModifier, below_handle)
        self.assert_glides(self.record(OverlayScrollBar.SCROLL_MS + 150), self.bar.pageStep())

    def test_setting_the_value_stops_a_glide(self):
        _wheel(self.win.scroll.viewport(), -3)
        QTest.qWait(40)
        self.bar.setValue(5)  # As when dragging the handle.
        self.assertEqual(self.record(OverlayScrollBar.SCROLL_MS + 100)[-1], 5)

    def test_new_row_at_the_end_glides_into_view(self):
        row = self.win.add_item("todo")
        values = self.record(OverlayScrollBar.SCROLL_MS + 200)
        self.assert_glides(values, self.bar.maximum())
        bottom = row.mapTo(self.win.scroll.viewport(), QPoint(0, row.height())).y()
        self.assertLessEqual(bottom, self.win.scroll.viewport().height())


if __name__ == "__main__":
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    unittest.main()
