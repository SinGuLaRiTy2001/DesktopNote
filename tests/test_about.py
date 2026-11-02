"""About window tests: its content, its link and that every text shows in full.

Builds real windows; CI runs them with QT_QPA_PLATFORM=offscreen.
"""
import os
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from PySide6.QtCore import Qt  # noqa: E402
from PySide6.QtTest import QTest  # noqa: E402
from PySide6.QtWidgets import QApplication, QLabel, QToolButton  # noqa: E402

from desktopnote import __version__, about, i18n  # noqa: E402
from desktopnote.about import AboutWindow  # noqa: E402
from desktopnote.theme import DARK, LIGHT  # noqa: E402

_app = QApplication.instance() or QApplication(sys.argv)
_app.setQuitOnLastWindowClosed(False)

DEVELOPER = {"en": "Jian Wen", "zh": "文简"}


class AboutWindowTest(unittest.TestCase):
    """The About window shows the app's details, in full, in every language and theme."""

    def tearDown(self):
        i18n.set_language(i18n.DEFAULT_LANGUAGE)

    def open(self, language, theme=LIGHT):
        i18n.set_language(language)
        win = AboutWindow(theme)
        win.show()
        _app.processEvents()
        self.addCleanup(lambda: win.closing or win.close())  # A newer window may have closed it.
        return win

    @staticmethod
    def widgets(win):
        return [w for w in (*win.findChildren(QLabel), *win.findChildren(QToolButton)) if w.text()]

    def test_shows_name_version_developer_and_link(self):
        for language in i18n.LANGUAGES:
            with self.subTest(language=language):
                texts = " ".join(w.text() for w in self.widgets(self.open(language)))
                for expected in ("DesktopNote", __version__, DEVELOPER[language], "Claude Code", "GitHub"):
                    self.assertIn(expected, texts)

    def test_no_copyright_notice(self):
        # LGPL-3.0 section 4(c): showing a copyright notice would require Qt's notice beside it.
        for language in i18n.LANGUAGES:
            with self.subTest(language=language):
                for w in self.widgets(self.open(language)):
                    self.assertNotIn("©", w.text())
                    self.assertNotIn("copyright", w.text().lower())

    def test_link_opens_the_repository(self):
        opened = []

        class Services:
            @staticmethod
            def openUrl(url):
                opened.append(url.toString())

        original, about.QDesktopServices = about.QDesktopServices, Services
        try:
            win = self.open("en")
            for button in win.findChildren(QToolButton):
                if button.objectName() == "link":
                    button.click()
        finally:
            about.QDesktopServices = original
        self.assertEqual(opened, [about.REPO_URL])

    def test_texts_fit(self):
        for language in i18n.LANGUAGES:
            for theme in (LIGHT, DARK):
                with self.subTest(language=language, dark=theme.dark):
                    win = self.open(language, theme)
                    for w in self.widgets(win):
                        self.assertGreaterEqual(w.width(), w.sizeHint().width(), f"{w.text()!r} is cut off")
                        self.assertLessEqual(w.mapTo(win, w.rect().topRight()).x(), win.width())

    def test_escape_closes(self):
        win = self.open("en")
        QTest.keyClick(win, Qt.Key_Escape)
        self.assertTrue(win.closing)


if __name__ == "__main__":
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    unittest.main()
