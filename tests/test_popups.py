"""Popup tests: the date and colour pickers fade in over the acrylic; the time field's hover eases.

Builds real popups; CI runs them with QT_QPA_PLATFORM=offscreen.
"""
import sys
import unittest
from datetime import datetime
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from PySide6.QtCore import QEvent, QPoint, QPointF  # noqa: E402
from PySide6.QtGui import QEnterEvent  # noqa: E402
from PySide6.QtTest import QTest  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from desktopnote import win_effects  # noqa: E402
from desktopnote.popups import ColorPopup, DateTimePopup  # noqa: E402
from desktopnote.theme import DARK, LIGHT  # noqa: E402
from tests import _support  # noqa: E402

_support.qapp()


class PopupFadeInTest(unittest.TestCase):
    """With the acrylic available, a popup shows a stand-in backdrop while it fades in."""

    def test_each_popup_fades_in_over_a_stand_in(self):
        for name, make in (("date", lambda theme: DateTimePopup(theme, datetime(2030, 1, 1, 9, 0))),
                           ("colour", lambda theme: ColorPopup(theme, "#9FCBEF"))):
            for theme in (LIGHT, DARK):
                with self.subTest(popup=name, dark=theme.dark):
                    popup = make(theme)
                    self.addCleanup(popup.close)
                    with mock.patch.object(win_effects, "set_acrylic", return_value=True), \
                            mock.patch.object(win_effects, "set_rounded_corners"):
                        popup.popup_at(QPoint(200, 200))
                        QTest.qWait(20)
                    self.assertIsNotNone(popup._intro)


class TimeFieldHoverTest(unittest.TestCase):
    """The time field's background eases in on hover or focus and out afterwards."""

    def setUp(self):
        self.popup = DateTimePopup(LIGHT, datetime(2030, 1, 1, 9, 0))
        self.popup.show()
        self.addCleanup(self.popup.close)
        self.edit = self.popup.time_edit

    def levels_until(self, target: float) -> list[float]:
        """Records the background's level every few ms until it reaches target; returns them."""
        levels = []

        def reached() -> bool:
            levels.append(self.edit._level)
            return self.edit._level == target
        _support.wait_until(reached, 2000, f"the background never reached {target}")
        return levels

    def test_hover_eases_in_and_out(self):
        QApplication.sendEvent(self.edit, QEnterEvent(QPointF(5, 5), QPointF(5, 5), QPointF(5, 5)))
        self.assertTrue(any(0.0 < level < 1.0 for level in self.levels_until(1.0)), "it eases in")
        QApplication.sendEvent(self.edit, QEvent(QEvent.Leave))
        self.assertTrue(any(0.0 < level < 1.0 for level in self.levels_until(0.0)), "it eases out")

    def test_stays_while_focused(self):
        self.popup.activateWindow()
        self.edit.setFocus()
        self.levels_until(1.0)
        self.assertTrue(self.edit.hasFocus())
        QTest.qWait(300)
        self.assertEqual(self.edit._level, 1.0, "it stays while the field has focus")


if __name__ == "__main__":
    unittest.main()
