"""Notification tests: a reminder's two lines, and the app icon Windows shows above them."""
import struct
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from PySide6.QtGui import QGuiApplication  # noqa: E402

from desktopnote import icons, win_effects  # noqa: E402

_app = QGuiApplication.instance() or QGuiApplication(sys.argv)


class NotificationTest(unittest.TestCase):
    """The notification has the title, then the text, and no picture of its own."""

    def test_two_lines_and_no_picture(self):
        xml = win_effects.toast_xml("Work", "Call Anna")
        self.assertIn("<text>Work</text><text>Call Anna</text>", xml)
        self.assertNotIn("<image", xml)

    def test_text_is_escaped(self):
        xml = win_effects.toast_xml("A & B", "<b>'x'</b>")
        self.assertIn("A &amp; B", xml)
        self.assertNotIn("<b>", xml)

    def test_the_icon_holds_every_size(self):
        data = icons.app_ico()
        reserved, kind, count = struct.unpack("<HHH", data[:6])
        self.assertEqual((reserved, kind, count), (0, 1, len(icons.ICO_SIZES)))
        sides = [data[6 + 16 * i] or 256 for i in range(count)]  # 0 stands for 256.
        self.assertEqual(sides, list(icons.ICO_SIZES))


if __name__ == "__main__":
    unittest.main()
