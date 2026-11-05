"""Popup tests: the date and colour pickers open with their fade-in over the acrylic.

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

_app = QApplication.instance() or QApplication(sys.argv)
_app.setQuitOnLastWindowClosed(False)


class PopupFadeInTest(unittest.TestCase):
    """With the acrylic available, a popup shows a stand-in backdrop while it fades in."""

    def open(self, popup):
        with mock.patch.object(win_effects, "set_acrylic", return_value=True), \
                mock.patch.object(win_effects, "set_rounded_corners"):
            popup.popup_at(QPoint(200, 200))
            QTest.qWait(20)
        self.addCleanup(popup.close)
        return popup

    def test_date_popup_fades_in_over_a_stand_in(self):
        for theme in (LIGHT, DARK):
            with self.subTest(dark=theme.dark):
                popup = self.open(DateTimePopup(theme, datetime(2030, 1, 1, 9, 0)))
                self.assertIsNotNone(popup._intro)
                self.assertIsNotNone(popup._pop_tween)

    def test_colour_popup_fades_in_over_a_stand_in(self):
        popup = self.open(ColorPopup(LIGHT, "#9FCBEF"))
        self.assertIsNotNone(popup._intro)
        self.assertIsNotNone(popup._pop_tween)


class TimeFieldHoverTest(unittest.TestCase):
    """The time field's background eases in on hover or focus and out afterwards."""

    def setUp(self):
        self.popup = DateTimePopup(LIGHT, datetime(2030, 1, 1, 9, 0))
        self.popup.show()
        self.addCleanup(self.popup.close)
        self.edit = self.popup.time_edit

    def test_hover_eases_in_and_out(self):
        QApplication.sendEvent(self.edit, QEnterEvent(QPointF(5, 5), QPointF(5, 5), QPointF(5, 5)))
        QTest.qWait(40)
        self.assertGreater(self.edit._level, 0.0)
        self.assertLess(self.edit._level, 1.0)  # Still easing in.
        QTest.qWait(300)
        self.assertEqual(self.edit._level, 1.0)
        QApplication.sendEvent(self.edit, QEvent(QEvent.Leave))
        QTest.qWait(400)
        self.assertEqual(self.edit._level, 0.0)

    def test_stays_while_focused(self):
        self.popup.activateWindow()
        self.edit.setFocus()
        QTest.qWait(300)
        self.assertTrue(self.edit.hasFocus())
        self.assertEqual(self.edit._level, 1.0)


if __name__ == "__main__":
    unittest.main()
