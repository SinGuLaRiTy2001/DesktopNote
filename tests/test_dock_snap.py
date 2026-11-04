"""Dock-by-snapping tests: a note let go while snapped to a docked note hides at the same edge.

Builds real note windows; CI runs them with QT_QPA_PLATFORM=offscreen.
"""
import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from PySide6.QtWidgets import QApplication  # noqa: E402

from desktopnote.models import Note  # noqa: E402
from desktopnote.note_window import NoteWindow  # noqa: E402
from desktopnote.snapping import Stick  # noqa: E402
from desktopnote.theme import LIGHT  # noqa: E402

_app = QApplication.instance() or QApplication(sys.argv)
_app.setQuitOnLastWindowClosed(False)


class HideEdgeTest(unittest.TestCase):
    """NoteWindow._hide_edge: the edge letting go of a dragged note hides it in."""

    def setUp(self):
        self.win = NoteWindow(Note(w=280, h=220), LIGHT, 60, 1.0)
        self.docked = NoteWindow(Note(w=280, h=220), LIGHT, 60, 1.0)
        self.addCleanup(self.win.deleteLater)
        self.addCleanup(self.docked.deleteLater)

    def test_free_drag_hides_nowhere(self):
        self.assertEqual(self.win._hide_edge(), "")

    def test_snapped_to_a_docked_note_hides_at_its_edge(self):
        for edge in ("left", "right", "top"):
            with self.subTest(edge=edge):
                self.docked.dock.edge = edge
                self.win._snap_target = self.docked
                self.assertEqual(self.win._hide_edge(), edge)

    def test_snapped_to_a_note_that_is_not_docked_hides_nowhere(self):
        self.docked.dock.edge = ""
        self.win._snap_target = self.docked
        self.assertEqual(self.win._hide_edge(), "")

    def test_esc_cancels_hiding(self):
        self.docked.dock.edge = "right"
        self.win._snap_target = self.docked
        self.win._snap_move.cancelled = True
        self.assertEqual(self.win._hide_edge(), "")

    def test_a_push_into_an_edge_wins(self):
        self.docked.dock.edge = "right"
        self.win._snap_target = self.docked
        push = Stick("left", (0, 0, 100, 100), (0, 0, 100, 100))
        with mock.patch.object(self.win._snap_move, "pushed", return_value=push):
            self.assertEqual(self.win._hide_edge(), "left")


if __name__ == "__main__":
    unittest.main()
