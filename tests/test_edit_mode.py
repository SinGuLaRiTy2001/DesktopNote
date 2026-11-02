"""Edit-mode tests: switching between viewing and editing is eased, not instant.

Builds real note windows; CI runs them with QT_QPA_PLATFORM=offscreen.
"""
import os
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from PySide6.QtTest import QTest  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from desktopnote import motion  # noqa: E402
from desktopnote.models import Note  # noqa: E402
from desktopnote.note_window import NoteWindow  # noqa: E402
from desktopnote.theme import LIGHT  # noqa: E402

_app = QApplication.instance() or QApplication(sys.argv)
_app.setQuitOnLastWindowClosed(False)


class EditModeTest(unittest.TestCase):
    """The pencil turns into Done, the add buttons rise in and out, the hint cross-fades."""

    def setUp(self):
        self.win = NoteWindow(Note(w=280, h=220), LIGHT, 60, 1.0)
        self.win.show()
        QTest.qWait(50)
        self.adds = (self.win.btn_text, self.win.btn_todo, self.win.btn_reminder)

    def tearDown(self):
        self.win.close()
        self.win.deleteLater()

    def opacity(self, widget):
        return widget.graphicsEffect().opacity()

    def test_starts_in_view_mode(self):
        self.assertFalse(any(b.isVisible() for b in self.adds))
        self.assertTrue(self.win.btn_edit.isVisible())
        self.assertFalse(self.win.btn_done.isVisible())

    def test_entering_edit_mode_is_eased(self):
        self.win.set_editing(True)
        QTest.qWait(motion.MODE_MS * 3 // 5)
        mid = [self.opacity(b) for b in self.adds]
        self.assertTrue(all(b.isVisible() for b in self.adds))
        self.assertTrue(0.0 < self.opacity(self.win.btn_done) < 1.0, "Done fades in")
        self.assertLess(self.opacity(self.win.btn_edit), self.opacity(self.win.btn_done), "the pencil goes first")
        self.assertGreater(mid[0], mid[2], "the add buttons follow one another")
        QTest.qWait(motion.MODE_MS)
        self.assertEqual([self.opacity(b) for b in self.adds], [1.0, 1.0, 1.0])
        self.assertTrue(self.win.btn_done.isVisible())
        self.assertFalse(self.win.btn_edit.isVisible())
        self.assertTrue(self.win.mode_slot.rect().contains(self.win.btn_done.geometry()), "Done is never cut off")

    def test_leaving_edit_mode_is_eased(self):
        self.win.set_editing(True)
        QTest.qWait(motion.MODE_MS + 100)
        self.win.set_editing(False)
        QTest.qWait(motion.MODE_MS // 3)
        self.assertTrue(self.win.btn_edit.isVisible() and self.win.btn_done.isVisible(), "they cross-fade")
        QTest.qWait(motion.MODE_MS)
        self.assertFalse(any(b.isVisible() for b in self.adds))
        self.assertFalse(self.win.btn_done.isVisible())
        self.assertEqual(self.win.btn_edit.geometry().right(), self.win.mode_slot.rect().right(),
                         "the pencil stays put")

    def test_the_hint_swaps_text_while_hidden(self):
        view_text = self.win.placeholder.text()
        self.win.set_editing(True)
        QTest.qWait(motion.MODE_MS // 4)
        self.assertEqual(self.win.placeholder.text(), view_text, "the old text fades out first")
        QTest.qWait(motion.MODE_MS)
        self.assertNotEqual(self.win.placeholder.text(), view_text)
        self.assertEqual(self.opacity(self.win.placeholder), 1.0)


if __name__ == "__main__":
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    unittest.main()
