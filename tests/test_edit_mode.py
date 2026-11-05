"""Edit-mode tests: switching between viewing and editing is eased, not instant.

Builds real note windows; CI runs them with QT_QPA_PLATFORM=offscreen. Each switch is recorded
frame by frame; the tests check what held while it ran and how it ended.
"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from desktopnote.models import Note  # noqa: E402
from desktopnote.note_window import NoteWindow  # noqa: E402
from desktopnote.theme import LIGHT  # noqa: E402
from tests import _support  # noqa: E402

_support.qapp()


class EditModeTest(unittest.TestCase):
    """The pencil turns into Done, the add buttons rise in and out, the hint cross-fades."""

    def setUp(self):
        self.win = NoteWindow(Note(w=280, h=220), LIGHT, 60, 1.0)
        self.win.show()
        _support.wait_until(self.win.isVisible)
        self.addCleanup(self.win.deleteLater)
        self.addCleanup(self.win.close)
        self.adds = (self.win.btn_text, self.win.btn_todo, self.win.btn_reminder)

    @staticmethod
    def opacity(widget) -> float:
        return widget.graphicsEffect().opacity()

    def frame(self) -> dict:
        """Returns what the switch shows now."""
        win = self.win
        return {"adds": [self.opacity(b) for b in self.adds], "adds_shown": all(b.isVisible() for b in self.adds),
                "edit": self.opacity(win.btn_edit), "done": self.opacity(win.btn_done),
                "both_shown": win.btn_edit.isVisible() and win.btn_done.isVisible(),
                "view_hint": self.opacity(win.placeholder), "edit_hint": self.opacity(win.placeholder_edit),
                "hints_aligned": win.placeholder.geometry().topLeft() == win.placeholder_edit.geometry().topLeft()}

    def switch(self, editing: bool) -> list[dict]:
        """Switches the mode; returns a frame every few ms until the switch has ended."""
        frames = []
        target = 1.0 if editing else 0.0
        self.win.set_editing(editing)

        def ended() -> bool:
            frames.append(self.frame())
            tween = self.win._mode_tween
            return self.win._mode == target and (tween is None or not tween.is_running())
        _support.wait_until(ended, 3000, "the switch did not end")
        return frames

    def test_starts_in_view_mode(self):
        self.assertFalse(any(b.isVisible() for b in self.adds))
        self.assertTrue(self.win.btn_edit.isVisible())
        self.assertFalse(self.win.btn_done.isVisible())

    def test_entering_edit_mode_is_eased(self):
        frames = self.switch(True)
        fading = [f for f in frames if 0.0 < f["done"] < 1.0]
        self.assertTrue(fading, "Done fades in")
        self.assertTrue(all(f["edit"] < f["done"] for f in fading[len(fading) // 2:]), "the pencil goes first")
        self.assertTrue(any(f["adds"][0] > f["adds"][2] for f in frames), "the add buttons follow one another")
        self.assertTrue(all(f["adds_shown"] for f in fading), "the add buttons show while they rise in")
        self.assertEqual([self.opacity(b) for b in self.adds], [1.0, 1.0, 1.0])
        self.assertTrue(self.win.btn_done.isVisible())
        self.assertFalse(self.win.btn_edit.isVisible())
        self.assertTrue(self.win.mode_slot.rect().contains(self.win.btn_done.geometry()), "Done is never cut off")

    def test_leaving_edit_mode_is_eased(self):
        self.switch(True)
        frames = self.switch(False)
        self.assertTrue(any(f["both_shown"] for f in frames), "the pencil and Done cross-fade")
        self.assertFalse(any(b.isVisible() for b in self.adds))
        self.assertFalse(self.win.btn_done.isVisible())
        self.assertEqual(self.win.btn_edit.geometry().right(), self.win.mode_slot.rect().right(),
                         "the pencil stays put")

    def test_the_hint_cross_fades_in_place(self):
        self.assertEqual((self.opacity(self.win.placeholder), self.opacity(self.win.placeholder_edit)), (1.0, 0.0))
        frames = self.switch(True)
        fading = [f for f in frames if 0.0 < f["edit_hint"] < 1.0]
        self.assertTrue(fading, "the new text fades in")
        for f in fading:
            self.assertAlmostEqual(f["view_hint"], (1.0 - f["edit_hint"]) ** 2, places=6,
                                   msg="the old text fades out faster")
            self.assertTrue(f["hints_aligned"], "in the same place")
        self.assertEqual((self.opacity(self.win.placeholder), self.opacity(self.win.placeholder_edit)), (0.0, 1.0))
        self.switch(False)
        self.assertEqual((self.opacity(self.win.placeholder), self.opacity(self.win.placeholder_edit)), (1.0, 0.0))


if __name__ == "__main__":
    unittest.main()
