"""Item delete tests: deleting an item takes two clicks, × and then Delete.

Builds real note windows; CI runs them with QT_QPA_PLATFORM=offscreen.
"""
import os
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from PySide6.QtCore import QEvent, QPointF, Qt  # noqa: E402
from PySide6.QtGui import QMouseEvent  # noqa: E402
from PySide6.QtTest import QTest  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from desktopnote.models import Item, Note  # noqa: E402
from desktopnote.note_window import NoteWindow  # noqa: E402
from desktopnote.theme import LIGHT  # noqa: E402

_app = QApplication.instance() or QApplication(sys.argv)
_app.setQuitOnLastWindowClosed(False)


class ItemDeleteTest(unittest.TestCase):
    """One click on × stretches it into the Delete pill; the pill deletes; it shrinks back on its own."""

    def setUp(self):
        items = [Item(type="todo", text="A"), Item(type="todo", text="B")]
        self.win = NoteWindow(Note(w=280, h=240, items=items), LIGHT, 60, 1.0)
        self.win.show()
        self.win.set_editing(True)
        QTest.qWait(300)
        self.row = self.win.rows[0]
        self.row._hover = 1.0  # As if the pointer were over it.
        self.row._sync_delete()
        self.deleted = []
        self.row.deleteRequested.connect(self.deleted.append)

    def tearDown(self):
        self.win.close()
        self.win.deleteLater()

    def arm(self):
        QTest.mouseClick(self.row.del_btn, Qt.LeftButton)
        QTest.qWait(self.row.CONFIRM_IN_MS + 150)

    def test_one_click_only_arms(self):
        self.arm()
        self.assertEqual(self.deleted, [])
        self.assertTrue(self.row.del_confirm.isVisible())
        self.assertEqual(self.row._armed, 1.0)

    def test_the_pill_deletes(self):
        self.arm()
        QTest.qWait(QApplication.doubleClickInterval())
        QTest.mouseClick(self.row.del_confirm, Qt.LeftButton)
        self.assertEqual(self.deleted, [self.row])

    def test_a_deleted_item_fades_under_the_rows_below(self):
        self.arm()
        QTest.qWait(QApplication.doubleClickInterval())
        x, height = self.row.x(), self.row.height()
        QTest.mouseClick(self.row.del_confirm, Qt.LeftButton)
        self.assertEqual([item.text for item in self.win.note.items], ["B"])
        QTest.qWait(140)
        self.assertLess(self.row.graphicsEffect().opacity(), 1.0, "it fades")
        self.assertLess(self.row.maximumHeight(), height, "the rows below move up over it")
        self.assertEqual(self.row.x(), x, "it does not slide sideways")

    def test_a_double_click_on_the_x_does_not_delete(self):
        QTest.mouseClick(self.row.del_btn, Qt.LeftButton)
        QTest.qWait(60)
        # Windows delivers the second click of a double click as a double-click press, here on
        # the Delete pill that has just appeared under the pointer.
        box = self.row.del_confirm
        at = QPointF(box.rect().center())
        for kind, held in ((QEvent.MouseButtonDblClick, Qt.LeftButton), (QEvent.MouseButtonRelease, Qt.NoButton)):
            event = QMouseEvent(kind, at, QPointF(box.mapToGlobal(at)), Qt.LeftButton, held, Qt.NoModifier)
            QApplication.sendEvent(box, event)
        self.assertEqual(self.deleted, [])

    def test_a_quick_deliberate_second_click_deletes(self):
        QTest.mouseClick(self.row.del_btn, Qt.LeftButton)
        QTest.qWait(80)  # Well inside the double-click time, but a separate click.
        QTest.mouseClick(self.row.del_confirm, Qt.LeftButton)
        self.assertEqual(self.deleted, [self.row])

    def test_it_turns_back_after_a_while(self):
        self.row._disarm.setInterval(300)
        self.arm()
        QTest.qWait(300 + self.row.CONFIRM_OUT_MS + 150)
        self.assertFalse(self.row.del_confirm.isVisible())
        self.assertEqual(self.deleted, [])

    def test_leaving_edit_mode_turns_it_back(self):
        self.arm()
        self.win.set_editing(False)
        QTest.qWait(self.row.CONFIRM_OUT_MS + 150)
        self.assertFalse(self.row.del_confirm.isVisible())

    def test_the_pill_fits_the_row_at_every_text_size(self):
        for scale in (0.85, 1.0, 1.3):
            with self.subTest(scale=scale):
                self.win.set_text_scale(scale)
                self.arm()
                box, row = self.row.del_confirm.geometry(), self.row.rect()
                self.assertTrue(row.contains(box), f"{box} outside {row}")
                self.assertGreaterEqual(box.width(), self.row.del_confirm.sizeHint().width())
                self.row._set_armed(False)
                QTest.qWait(self.row.CONFIRM_OUT_MS + 100)


if __name__ == "__main__":
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    unittest.main()
