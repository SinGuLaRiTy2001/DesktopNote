"""About window tests: its content, its link and that every text shows in full.

Builds real windows; CI runs them with QT_QPA_PLATFORM=offscreen.
"""
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from PySide6.QtCore import Qt  # noqa: E402
from PySide6.QtGui import QCloseEvent  # noqa: E402
from PySide6.QtTest import QTest  # noqa: E402
from PySide6.QtWidgets import QAbstractButton, QApplication, QLabel  # noqa: E402

from desktopnote import __version__, about, i18n, win_effects  # noqa: E402
from desktopnote.about import AboutWindow  # noqa: E402
from desktopnote.models import Note  # noqa: E402
from desktopnote.note_window import NoteWindow  # noqa: E402
from desktopnote.theme import DARK, LIGHT  # noqa: E402
from desktopnote.updates import Release, UpdateFlow  # noqa: E402

_app = QApplication.instance() or QApplication(sys.argv)
_app.setQuitOnLastWindowClosed(False)

DEVELOPER = {"en": "Jian Wen", "zh": "文简"}


class AboutWindowTest(unittest.TestCase):
    """The About window shows the app's details, in full, in every language and theme."""

    def tearDown(self):
        i18n.set_language(i18n.DEFAULT_LANGUAGE)

    def open(self, language, theme=LIGHT):
        i18n.set_language(language)
        self.flow = UpdateFlow(__version__, Path(tempfile.mkdtemp()))
        win = AboutWindow(theme, self.flow)
        win.show()
        _app.processEvents()
        self.addCleanup(lambda: win.closing or win.close())  # A newer window may have closed it.
        return win

    @staticmethod
    def widgets(win):
        return [w for w in (*win.findChildren(QLabel), *win.findChildren(QAbstractButton)) if w.text()]

    def test_shows_name_version_developer_and_link(self):
        for language in i18n.LANGUAGES:
            with self.subTest(language=language):
                texts = " ".join(w.text() for w in self.widgets(self.open(language)))
                for expected in ("DesktopNote", __version__, DEVELOPER[language], "Claude Code", "GitHub"):
                    self.assertIn(expected, texts)

    def test_no_copyright_notice(self):
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
            win.btn_github.click()
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

    def test_the_version_row_offers_updates(self):
        win = self.open("en")
        flow = self.flow
        self.assertEqual(win.btn_update.text(), "Check for Updates")
        for state, text, enabled in (("checking", "Checking…", False), ("current", "Up to Date", True),
                                     ("installing", "Updating…", False), ("failed", "Try Again", True)):
            with self.subTest(state=state):
                flow._set(state, error="offline" if state == "failed" else "")
                self.assertEqual((win.btn_update.text(), win.btn_update.isEnabled()), (text, enabled))
        self.assertEqual(win.btn_update.toolTip(), "offline", "a failure says why")
        flow.release = Release("9.9.9", "https://example.invalid", "", "")
        flow._set("available")
        self.assertEqual(win.btn_update.text(), "Download 9.9.9", "from source it offers the download page")

    def test_update_text_fades_to_the_newest(self):
        win = self.open("en")
        button = win.btn_update
        self.flow._set("checking")
        self.flow._set("current")
        self.assertEqual(button.text(), "Up to Date", "text() is the new text at once")
        QTest.qWait(about.TEXT_OUT_MS + about.TEXT_IN_MS + 200)
        self.assertEqual((button._shown, round(button._opacity, 2)), ("Up to Date", 1.0))
        self.assertGreaterEqual(button.width(), button.sizeHint().width())

    def test_escape_closes(self):
        win = self.open("en")
        QTest.keyClick(win, Qt.Key_Escape)
        self.assertTrue(win.closing)

    def test_any_close_counts(self):
        win = self.open("en")
        win.setAttribute(Qt.WA_DeleteOnClose, False)  # Kept to look at it afterwards.
        QApplication.sendEvent(win, QCloseEvent())  # As Alt+F4 sends it.
        self.assertTrue(win.closing)


class HandOverTest(unittest.TestCase):
    """Closing About with its button or Esc activates the closest note behind it in the z-order.

    Nothing is activated when another app's window comes before that note.
    """

    def setUp(self):
        self.note = NoteWindow(Note(w=280, h=220), LIGHT, 60, 1.0)
        self.note.show()
        self.win = AboutWindow(LIGHT, UpdateFlow(__version__, Path(tempfile.mkdtemp())))
        self.win.show()  # After the note: About closes when another window becomes active.
        _app.processEvents()
        self.assertFalse(self.win.closing)
        self.addCleanup(self.note.close)
        self.addCleanup(lambda: self.win.closing or self.win.close())

    def dismiss(self, behind, topmost=(), hand_over=True):
        """Closes About in front of the given windows; returns whether a note was activated.

        Args:
            behind: The window handles behind About in the z-order, closest first.
            topmost: The handles among them that are always on top.
            hand_over: Passed to AboutWindow.dismiss.
        """
        with (mock.patch.object(win_effects, "windows_behind", return_value=list(behind)),
              mock.patch.object(win_effects, "is_topmost", side_effect=lambda hwnd: hwnd in topmost),
              mock.patch.object(NoteWindow, "activateWindow") as activate):
            self.win.dismiss(hand_over=hand_over)
        return activate.called

    def test_closest_note_behind_is_activated(self):
        self.assertTrue(self.dismiss([int(self.note.winId())]))

    def test_other_apps_topmost_windows_are_skipped(self):
        self.assertTrue(self.dismiss([111, int(self.note.winId())], topmost={111}))

    def test_notes_behind_another_app_stay_behind(self):
        self.assertFalse(self.dismiss([222, int(self.note.winId())]))

    def test_a_click_elsewhere_hands_nothing_over(self):
        self.assertFalse(self.dismiss([int(self.note.winId())], hand_over=False))


if __name__ == "__main__":
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    unittest.main()
