"""Reorder tests: a held row can be dragged into a new order, in edit and in view mode.

Builds real note windows; CI runs them with QT_QPA_PLATFORM=offscreen.
"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from PySide6.QtCore import QEvent, QPoint, QPointF, Qt  # noqa: E402
from PySide6.QtGui import QMouseEvent  # noqa: E402
from PySide6.QtTest import QTest  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from desktopnote import motion  # noqa: E402
from desktopnote.models import Item, Note  # noqa: E402
from desktopnote.note_window import NoteWindow  # noqa: E402
from desktopnote.theme import LIGHT  # noqa: E402
from tests import _support  # noqa: E402

_support.qapp()


class ReorderCase(unittest.TestCase):
    """A shown note with the rows A (text), B and C (to-dos) and D (a reminder, the tallest).

    An exception raised in a Qt callback during a test fails it.
    """

    def setUp(self):
        items = [Item(type="text", text="A"), Item(type="todo", text="B"), Item(type="todo", text="C"),
                 Item(type="reminder", text="D", due="2030-01-01T09:00")]
        self.win = NoteWindow(Note(w=280, h=320, items=items), LIGHT, 60, 1.0)
        self.win.show()
        _support.wait_until(self.win.isVisible)
        self.addCleanup(self.win.deleteLater)
        self.addCleanup(self.win.close)
        errors = []
        hook, sys.excepthook = sys.excepthook, lambda *exc: errors.append(exc[1])
        self.addCleanup(lambda: self.assertEqual(errors, [], "no errors in Qt callbacks"))
        self.addCleanup(setattr, sys, "excepthook", hook)

    def order(self) -> list[str]:
        """Returns the note's items' texts, in order."""
        return [item.text for item in self.win.note.items]

    def settle(self) -> None:
        """Waits until every glide, fade and lift has ended."""
        _support.wait_until(lambda: not motion.Tween._running and not self.win.reorder.active(), 3000)

    def editing(self) -> None:
        """Switches the note to edit mode and waits for the switch to end."""
        self.win.set_editing(True)
        self.settle()

    def just_past(self, moving: int, other: int) -> int:
        """Returns the top that puts row ``moving``'s bottom 2 px below row ``other``'s centre."""
        rows = self.win.rows
        return rows[other].y() + rows[other].height() // 2 + 2 - rows[moving].height()

    def start_drag(self, index: int, to_y: int | None = None) -> QPoint:
        """Starts dragging row ``index``, moved until its top is at content y ``to_y``; returns the grab."""
        row = self.win.rows[index]
        grab = row.mapToGlobal(QPoint(5, 5))
        self.win.reorder.start(self.win.rows, row, grab)
        if to_y is not None:
            self.win.reorder.move(grab + QPoint(0, to_y - row.y()))
        return grab

    def drag(self, index: int, to_y: int) -> None:
        """Drags row ``index`` until its top is at content y ``to_y``, drops it and waits for it to settle."""
        self.start_drag(index, to_y)
        self.win.reorder.finish(True)
        self.settle()

    def press_text(self, row, hold_ms: int, move=QPoint()) -> None:
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

    def started(self, row) -> list:
        """Returns a list that collects the rows ``row`` starts dragging."""
        started = []
        row.dragStarted.connect(lambda r, _pos: started.append(r))
        return started


class ReorderTest(ReorderCase):
    """Holding a row starts a drag in either mode; drops reorder the rows and the note's items."""

    def test_hold_starts_a_drag_in_both_modes(self):
        row = self.win.rows[1]
        started = self.started(row)
        self.press_text(row, row.HOLD_MS + 100)
        self.assertEqual(started, [row], "view mode reorders too")
        self.settle()
        self.editing()
        self.press_text(row, row.HOLD_MS + 100)
        self.assertEqual(started, [row, row])
        self.settle()

    def test_a_quick_click_does_not_drag(self):
        self.editing()
        row = self.win.rows[1]
        started = self.started(row)
        self.press_text(row, 50)
        QTest.qWait(row.HOLD_MS + 100)
        self.assertEqual(started, [])

    def test_moving_before_the_hold_selects_text_instead(self):
        self.editing()
        row = self.win.rows[1]
        started = self.started(row)
        self.press_text(row, row.HOLD_MS + 100, move=QPoint(20, 0))
        self.assertEqual(started, [])

    def test_drag_down_and_up(self):
        self.editing()
        self.drag(0, self.just_past(0, 2))  # A past B and C, not D.
        self.assertEqual(self.order(), ["B", "C", "A", "D"])
        self.assertEqual([r.item.text for r in self.win.rows], ["B", "C", "A", "D"])
        self.drag(3, 0)  # D, the tallest row, all the way to the top.
        self.assertEqual(self.order(), ["D", "B", "C", "A"])

    def test_rows_line_up_after_a_drop(self):
        self.editing()
        self.drag(1, 10_000)  # Far below the end: clamped to the last slot.
        self.assertEqual(self.order(), ["A", "C", "D", "B"])
        tops = [r.y() for r in self.win.rows]
        bottoms = [r.y() + r.height() for r in self.win.rows]
        self.assertEqual(tops[1:], bottoms[:-1], "rows should stack without gaps or overlaps")

    def test_dropping_where_it_was_changes_nothing(self):
        self.editing()
        changed = []
        self.win.changed.connect(lambda: changed.append(True))
        self.drag(2, self.win.rows[2].y() + 3)
        self.assertEqual(self.order(), ["A", "B", "C", "D"])
        self.assertEqual(changed, [])

    def test_leaving_edit_mode_mid_drag_drops_at_once(self):
        self.editing()
        self.start_drag(0, self.just_past(0, 1))
        self.win.set_editing(False)
        self.assertFalse(self.win.reorder.active())
        self.assertEqual(self.order(), ["B", "A", "C", "D"])

    def test_a_row_removed_during_the_drop_does_not_misplace_it(self):
        self.editing()
        b = self.win.rows[1]
        self.start_drag(0, self.just_past(0, 2))
        self.win.reorder.finish()  # Glides; B goes before the drop is done.
        self.win._remove_row(b)
        self.settle()
        self.assertEqual(self.order(), ["C", "A", "D"])


class OutsideDeleteTest(ReorderCase):
    """An item deleted from outside while a row is dragged or settles (e.g. by the command line)."""

    def test_a_row_that_has_not_moved_is_dropped_from_the_drag(self):
        grab = self.start_drag(0)
        row = self.win.rows[0]
        self.win.forget_item(self.win.note.items.pop(3))  # D: below every slot, so it never glides.
        _support.wait_until(lambda: len(self.win.content.findChildren(type(row))) == 3, 3000)
        self.win.reorder.move(grab + QPoint(0, self.just_past(0, 1) - row.y()))
        self.win.reorder.finish()
        self.settle()
        self.assertEqual(self.order(), ["B", "A", "C"])
        self.assertTrue(self.win.content_lay.isEnabled())

    def test_deleting_the_dragged_row_ends_the_drag(self):
        self.start_drag(0)
        self.win.forget_item(self.win.note.items.pop(0))
        self.settle()
        self.assertFalse(self.win.reorder.active())
        self.assertTrue(self.win.content_lay.isEnabled())
        self.assertEqual(self.order(), ["B", "C", "D"])

    def test_a_row_deleted_while_it_settles(self):
        row = self.win.rows[1]
        self.press_text(row, row.HOLD_MS + 300)  # Lifts it with its shadow, then drops it.
        self.win._remove_row(row, deleted=True)  # The shadow is still settling.
        self.settle()  # Including the deleted row's fade.
        self.assertEqual(self.order(), ["A", "C", "D"])


if __name__ == "__main__":
    unittest.main()
