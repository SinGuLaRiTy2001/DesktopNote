"""Layout tests: texts of a note show in full at its narrowest, in every language and text size.

Builds real note windows; CI runs them with QT_QPA_PLATFORM=offscreen.
"""
import os
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from PySide6.QtWidgets import QAbstractButton, QApplication, QLabel, QWidget  # noqa: E402

from desktopnote import i18n  # noqa: E402
from desktopnote.models import Item, Note  # noqa: E402
from desktopnote.note_window import NoteWindow  # noqa: E402
from desktopnote.theme import DARK, LIGHT, TEXT_SCALES  # noqa: E402

_app = QApplication.instance() or QApplication(sys.argv)
_app.setQuitOnLastWindowClosed(False)


class NarrowNoteTextTest(unittest.TestCase):
    """At its minimum width, a note shows every text of its bottom bars without cutting it."""

    def tearDown(self):
        i18n.set_language(i18n.DEFAULT_LANGUAGE)

    def test_bottom_bars_fit_at_minimum_width(self):
        for language in i18n.LANGUAGES:
            for scale in TEXT_SCALES:
                for theme in (LIGHT, DARK):
                    with self.subTest(language=language, scale=scale, dark=theme.dark):
                        self.assert_bottom_bars_fit(language, scale, theme)

    def assert_bottom_bars_fit(self, language, scale, theme):
        i18n.set_language(language)
        win = NoteWindow(Note(w=100, items=[Item(type="todo", text="x")]), theme, 60, scale)
        try:
            win.retranslate()
            win.show()
            win.resize(win.minimumWidth(), win.height())  # As narrow as the note can be.
            for page in range(win.bottom.count()):
                win.bottom.setCurrentIndex(page)
                win.bottom.widget(page).layout().activate()
                _app.processEvents()
                for widget in win.bottom.widget(page).findChildren(QWidget):
                    if isinstance(widget, (QLabel, QAbstractButton)) and widget.isVisibleTo(win) and widget.text():
                        self.assertGreaterEqual(widget.width(), widget.sizeHint().width(),
                                                f"{widget.text()!r} is cut off at width {win.width()}")
        finally:
            win.close()
            win.deleteLater()


if __name__ == "__main__":
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    unittest.main()
