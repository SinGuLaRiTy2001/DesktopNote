"""Dock-by-snapping tests: a note let go while snapped to a docked note hides at the same edge.

Builds real note windows; CI runs them with QT_QPA_PLATFORM=offscreen.
"""
import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from desktopnote.models import Note  # noqa: E402
from desktopnote.note_window import NoteWindow  # noqa: E402
from desktopnote.snapping import Stick  # noqa: E402
from desktopnote.theme import LIGHT  # noqa: E402
from tests import _support  # noqa: E402

_support.qapp()


def note_window(test: unittest.TestCase, shown: bool = False, **fields) -> NoteWindow:
    """Returns a note window for a note with fields; it is closed and deleted after the test."""
    win = NoteWindow(Note(w=280, h=220, **fields), LIGHT, 60, 1.0)
    test.addCleanup(win.deleteLater)
    if shown:
        win.show()
        _support.wait_until(win.isVisible)
        test.addCleanup(win.close)
    return win


class HideEdgeTest(unittest.TestCase):
    """NoteWindow._hide_edge: the edge letting go of a dragged note hides it in."""

    def test_which_edge_letting_go_hides_the_note_in(self):
        push = Stick("left", (0, 0, 100, 100), (0, 0, 100, 100))
        cases = (("a free drag", None, False, None, ""),
                 ("snapped to a note docked left", "left", False, None, "left"),
                 ("snapped to a note docked right", "right", False, None, "right"),
                 ("snapped to a note docked top", "top", False, None, "top"),
                 ("snapped to a note that is not docked", "", False, None, ""),
                 ("Esc cancelled the drag", "right", True, None, ""),
                 ("a push into an edge wins", "right", False, push, "left"))
        for name, docked_at, cancelled, pushed, expected in cases:
            with self.subTest(name):
                win = note_window(self)
                if docked_at is not None:
                    docked = note_window(self)
                    docked.dock.edge = docked_at
                    win._snap_target = docked
                win._snap_move.cancelled = cancelled
                with mock.patch.object(win._snap_move, "pushed", return_value=pushed):
                    self.assertEqual(win._hide_edge(), expected)


class DockWhileEditingTest(unittest.TestCase):
    """A note let go at an edge docks and slides in; while it is being edited, it stays out."""

    def test_docks_and_slides_in_unless_being_edited(self):
        for editing in (False, True):
            with self.subTest(editing=editing):
                win = note_window(self, shown=True, x=200, y=200)
                win.set_editing(editing)
                win.dock.drag_finished(True, [], "right")
                self.assertEqual((win.dock.edge, win.dock.shown), ("right", editing))


if __name__ == "__main__":
    unittest.main()
