"""Reorder tests: a held row can be dragged into a new order, in edit and in view mode.

Builds real note windows; CI runs them with QT_QPA_PLATFORM=offscreen.
"""
import os
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from PySide6.QtCore import QEvent, QPoint, QPointF, Qt  # noqa: E402
from PySide6.QtGui import QMouseEvent  # noqa: E402
from PySide6.QtTest import QTest  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from desktopnote.models import Item, Note  # noqa: E402
from desktopnote.note_window import NoteWindow  # noqa: E402
from desktopnote.theme import LIGHT  # noqa: E402

_app = QApplication.instance() or QApplication(sys.argv)
_app.setQuitOnLastWindowClosed(False)


class ReorderTest(unittest.TestCase):
    """Holding a row starts a drag in either mode; drops reorder the rows and the note's items."""

    def setUp(self):
        items = [Item(type="text", text="A"), Item(type="todo", text="B"), Item(type="todo", text="C"),
                 Item(type="reminder", text="D", due="2030-01-01T09:00")]
        self.win = NoteWindow(Note(w=280, h=320, items=items), LIGHT, 60, 1.0)
        self.win.show()
        QTest.qWait(50)

    def tearDown(self):
        self.win.close()
        self.win.deleteLater()

    def order(self):
        return [item.text for item in self.win.note.items]

    def settle(self, ms=300):
        QTest.qWait(ms)

    def just_past(self, moving, other):
        """Returns the top that puts row ``moving``'s bottom 2 px below row ``other``'s centre."""
        rows = self.win.rows
        return rows[other].y() + rows[other].height() // 2 + 2 - rows[moving].height()

    def drag(self, index, to_y, animate=True):
        """Drags row ``index`` until its top is at content y ``to_y``, then drops it."""
        win = self.win
        row = win.rows[index]
        grab = row.mapToGlobal(QPoint(5, 5))
        win.reorder.start(win.rows, row, grab)
        win.reorder.move(grab + QPoint(0, to_y - row.y()))
        win.reorder.finish(animate)
        if animate:
            self.settle()

    def press_text(self, row, hold_ms, move=QPoint()):
        """Presses on ``row``'s text, optionally moves by ``move``, holds, then releases."""
        view = row.editor.viewport()
        at = QPoint(10, view.height() // 2)
        QTest.mousePress(view, Qt.LeftButton, Qt.NoModifier, at)
        if not move.isNull():
            to = QPointF(at + move)
            QApplication.sendEvent(view, QMouseEvent(QEvent.MouseMove, to, QPointF(view.mapToGlobal(to)),
                                                     Qt.NoButton, Qt.LeftButton, Qt.NoModifier))
        QTest.qWait(hold_ms)
        QTest.mouseRelease(view, Qt.LeftButton, Qt.NoModifier, at + move)

    def started(self, row):
        """Returns a list that collects the rows ``row`` starts dragging."""
        started = []
        row.dragStarted.connect(lambda r, _pos: started.append(r))
        return started

    def test_hold_starts_a_drag_in_both_modes(self):
        row = self.win.rows[1]
        started = self.started(row)
        self.press_text(row, row.HOLD_MS + 100)
        self.assertEqual(started, [row], "view mode reorders too")
        self.settle()
        self.win.set_editing(True)
        self.settle()
        self.press_text(row, row.HOLD_MS + 100)
        self.assertEqual(started, [row, row])

    def test_a_quick_click_does_not_drag(self):
        self.win.set_editing(True)
        self.settle()
        row = self.win.rows[1]
        started = self.started(row)
        self.press_text(row, 50)
        QTest.qWait(row.HOLD_MS + 100)
        self.assertEqual(started, [])

    def test_moving_before_the_hold_selects_text_instead(self):
        self.win.set_editing(True)
        self.settle()
        row = self.win.rows[1]
        started = self.started(row)
        self.press_text(row, row.HOLD_MS + 100, move=QPoint(20, 0))
        self.assertEqual(started, [])

    def test_drag_down_and_up(self):
        self.win.set_editing(True)
        self.settle()
        self.drag(0, self.just_past(0, 2))  # A past B and C, not D.
        self.assertEqual(self.order(), ["B", "C", "A", "D"])
        self.assertEqual([r.item.text for r in self.win.rows], ["B", "C", "A", "D"])
        self.drag(3, 0)  # D, the tallest row, all the way to the top.
        self.assertEqual(self.order(), ["D", "B", "C", "A"])

    def test_rows_line_up_after_a_drop(self):
        self.win.set_editing(True)
        self.settle()
        self.drag(1, 10_000)  # Far below the end: clamped to the last slot.
        self.assertEqual(self.order(), ["A", "C", "D", "B"])
        tops = [r.y() for r in self.win.rows]
        bottoms = [r.y() + r.height() for r in self.win.rows]
        self.assertEqual(tops[1:], bottoms[:-1], "rows should stack without gaps or overlaps")

    def test_dropping_where_it_was_changes_nothing(self):
        self.win.set_editing(True)
        self.settle()
        changed = []
        self.win.changed.connect(lambda: changed.append(True))
        self.drag(2, self.win.rows[2].y() + 3)
        self.assertEqual(self.order(), ["A", "B", "C", "D"])
        self.assertEqual(changed, [])

    def test_leaving_edit_mode_mid_drag_drops_at_once(self):
        self.win.set_editing(True)
        self.settle()
        rows = self.win.rows
        row = rows[0]
        grab = row.mapToGlobal(QPoint(5, 5))
        self.win.reorder.start(rows, row, grab)
        self.win.reorder.move(grab + QPoint(0, self.just_past(0, 1) - row.y()))
        self.win.set_editing(False)
        self.assertFalse(self.win.reorder.active())
        self.assertEqual(self.order(), ["B", "A", "C", "D"])

    def test_a_row_removed_during_the_drop_does_not_misplace_it(self):
        self.win.set_editing(True)
        self.settle()
        rows = self.win.rows
        row, b = rows[0], rows[1]
        grab = row.mapToGlobal(QPoint(5, 5))
        self.win.reorder.start(rows, row, grab)
        self.win.reorder.move(grab + QPoint(0, self.just_past(0, 2) - row.y()))
        self.win.reorder.finish()  # Glides; B goes before the drop is done.
        self.win._remove_row(b)
        self.settle()
        self.assertEqual(self.order(), ["C", "A", "D"])


if __name__ == "__main__":
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    unittest.main()
