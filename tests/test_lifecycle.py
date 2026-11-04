"""Lifecycle tests: hovering and opening menus leave no objects behind in a long session.

Builds real note windows; CI runs them with QT_QPA_PLATFORM=offscreen.
"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from PySide6.QtCore import QEvent, QObject, QPointF  # noqa: E402
from PySide6.QtGui import QEnterEvent  # noqa: E402
from PySide6.QtTest import QTest  # noqa: E402
from PySide6.QtWidgets import QApplication, QMenu  # noqa: E402

from desktopnote import motion  # noqa: E402
from desktopnote.models import Item, Note  # noqa: E402
from desktopnote.note_window import NoteWindow  # noqa: E402
from desktopnote.theme import LIGHT  # noqa: E402

_app = QApplication.instance() or QApplication(sys.argv)
_app.setQuitOnLastWindowClosed(False)


class LifecycleTest(unittest.TestCase):
    """Repeated interaction keeps the number of objects under a note steady."""

    def setUp(self):
        self.win = NoteWindow(Note(items=[Item(text="a"), Item(type="todo", text="b")]), LIGHT, 60, 1.0)
        self.win.show()
        QTest.qWait(100)

    def tearDown(self):
        self.win.close()
        self.win.deleteLater()

    def objects(self) -> int:
        QTest.qWait(300)  # Lets running transitions end and deferred deletes happen.
        return len(self.win.findChildren(QObject))

    def test_hovering_leaves_nothing_behind(self):
        buttons = [self.win.btn_more, self.win.rows[0]]
        before = self.objects()
        for _ in range(100):
            for button in buttons:
                _app.sendEvent(button, QEnterEvent(QPointF(1, 1), QPointF(1, 1), QPointF(1, 1)))
                _app.sendEvent(button, QEvent(QEvent.Leave))
        self.assertEqual(self.objects(), before)
        self.assertEqual(motion.Tween._running, set())

    def test_the_note_menu_is_deleted_when_it_closes(self):
        before = self.objects()
        for _ in range(10):
            self.win._show_menu()
            QTest.qWait(20)
            for menu in self.win.findChildren(QMenu):
                menu.close()
        self.assertEqual(self.objects(), before)
        self.assertEqual(self.win.findChildren(QMenu), [])


if __name__ == "__main__":
    unittest.main()
