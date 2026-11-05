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

import shiboken6  # noqa: E402
from PySide6.QtCore import QElapsedTimer, QPoint, Qt  # noqa: E402
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
        for state, text, enabled in (("checking", "Checking for updates…", False), ("current", "Up to Date", True),
                                     ("downloading", "Downloading update…", False),
                                     ("verifying", "Verifying update…", False),
                                     ("preparing", "Preparing update…", False),
                                     ("installing", "Installing update…", False), ("failed", "Try Again", True)):
            with self.subTest(state=state):
                flow._set(state, error="offline" if state == "failed" else "")
                settle(win.btn_update)
                self.assertEqual((win.btn_update._shown, win.btn_update.isEnabled()), (text, enabled))
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
        settle(button)
        self.assertEqual((button._shown, round(button._opacity, 2), button._dx), ("Up to Date", 1.0, 0.0))
        self.assertGreaterEqual(button.width(), button.fontMetrics().horizontalAdvance("Up to Date"))

    def test_escape_closes(self):
        win = self.open("en")
        QTest.keyClick(win, Qt.Key_Escape)
        self.assertTrue(win.closing)

    def test_any_close_counts(self):
        win = self.open("en")
        win.setAttribute(Qt.WA_DeleteOnClose, False)  # Kept to look at it afterwards.
        QApplication.sendEvent(win, QCloseEvent())  # As Alt+F4 sends it.
        self.assertTrue(win.closing)


def settle(button, limit_ms=5000):
    """Waits until the button shows its newest text, faded in and past its hold."""
    for _ in range(limit_ms // 20):
        if button.is_settled() and not button._hold.isActive():
            return
        QTest.qWait(20)


class UpdateStatusTest(unittest.TestCase):
    """The update button shows each stage in turn, readable, on a centred version line."""

    def setUp(self):
        i18n.set_language("en")
        self.addCleanup(i18n.set_language, i18n.DEFAULT_LANGUAGE)
        self.flow = UpdateFlow(__version__, Path(tempfile.mkdtemp()))
        self.win = AboutWindow(LIGHT, self.flow)
        self.win.show()
        _app.processEvents()
        self.addCleanup(lambda: self.win.closing or self.win.close())
        self.button = self.win.btn_update
        self.shown: list[tuple[str, float]] = []  # (text, ms since start) each time a text has faded in.
        self.clock = QElapsedTimer()
        self.clock.start()
        faded_in = self.button._faded_in

        def record():
            self.shown.append((self.button._shown, self.clock.elapsed()))
            faded_in()
        self.button._faded_in = record

    def test_quick_stages_are_each_shown_in_order(self):
        for state in ("downloading", "verifying", "preparing", "installing"):
            self.flow._set(state)
        settle(self.button)
        texts = [text for text, _ in self.shown]
        self.assertEqual(texts, ["Downloading update…", "Verifying update…", "Preparing update…",
                                 "Installing update…"])
        gaps = [later - earlier for (_, earlier), (_, later) in zip(self.shown, self.shown[1:])]
        self.assertTrue(all(gap >= about.TEXT_HOLD_MS for gap in gaps), f"each text stays readable: {gaps}")
        self.assertEqual((round(self.button._opacity, 2), self.button._dx), (1.0, 0.0))

    def test_a_failure_drops_the_stages_not_yet_shown(self):
        self.flow._set("downloading")
        settle(self.button)
        for state in ("verifying", "preparing"):
            self.flow._set(state)
        self.flow._set("failed", error="broken")
        settle(self.button)
        self.assertEqual([text for text, _ in self.shown], ["Downloading update…", "Try Again"])
        self.assertEqual((self.button.toolTip(), self.button.isEnabled()), ("broken", True))

    def test_a_retry_that_fails_at_once_shows_try_again(self):
        self.flow._set("failed", error="offline")
        settle(self.button)
        self.flow._set("checking")
        self.flow._set("failed", error="offline")
        settle(self.button)
        self.assertEqual((self.button._shown, round(self.button._opacity, 2)), ("Try Again", 1.0))
        self.assertTrue(self.button.isEnabled())

    def test_the_button_waits_until_its_text_has_caught_up(self):
        self.flow._set("checking")
        self.flow._set("current")
        self.assertFalse(self.button.isEnabled(), "still showing an earlier text")
        settle(self.button)
        self.assertTrue(self.button.isEnabled())

    def wait(self, condition, limit_ms=3000):
        """Processes events until condition() holds, or limit_ms passes.

        Args:
            condition: Called after each short wait; waiting ends once it returns True.
            limit_ms: How long to wait at most.
        """
        for _ in range(limit_ms // 10):
            if condition():
                return
            QTest.qWait(10)

    def test_installing_is_painted_and_shown_before_the_app_quits(self):
        quits = []
        self.flow.quit_requested.connect(lambda: quits.append(
            (self.button._seen, self.button._seen_clock.elapsed(), self.button._opacity, self.button._dx)))
        self.flow._set("preparing")
        settle(self.button)
        self.flow._ready()
        self.assertEqual(quits, [], "not when the text is only scheduled")
        self.wait(lambda: self.button._shown == "Installing update…")
        self.assertTrue(self.button._fading)
        self.assertEqual(quits, [], "not while the text fades in")
        self.wait(lambda: self.button._seen == "Installing update…")
        self.assertEqual(quits, [], "not as soon as it is painted at rest")
        self.wait(lambda: quits)
        QTest.qWait(about.TEXT_HOLD_MS + 100)
        self.assertEqual(len(quits), 1, "the app is asked to quit once")
        seen, shown_ms, opacity, dx = quits[0]
        self.assertEqual((seen, opacity, dx), ("Installing update…", 1.0, 0.0), "painted fully faded in, at rest")
        self.assertGreaterEqual(shown_ms, about.TEXT_HOLD_MS - 1, "and shown for TEXT_HOLD_MS since")

    def test_hiding_the_window_while_waiting_quits_at_once(self):
        quits = []
        self.flow.quit_requested.connect(lambda: quits.append(True))
        self.flow._set("preparing")
        settle(self.button)
        self.flow._ready()
        self.wait(lambda: self.button._seen == "Installing update…")
        self.win.hide()
        self.assertEqual(quits, [True])
        QTest.qWait(about.TEXT_HOLD_MS + 100)
        self.assertEqual(quits, [True], "the pending timer adds no second request")

    def test_closing_the_window_during_the_fade_quits_at_once(self):
        quits = []
        self.flow.quit_requested.connect(lambda: quits.append(True))
        self.flow._set("preparing")
        settle(self.button)
        self.flow._ready()
        self.wait(lambda: self.button._fading)
        self.win.dismiss()
        self.assertEqual(quits, [True])
        QTest.qWait(about.TEXT_HOLD_MS + 300)
        self.assertEqual(quits, [True])

    def test_a_deleted_window_does_not_hold_the_quit(self):
        quits = []
        self.flow.quit_requested.connect(lambda: quits.append(True))
        other = AboutWindow(LIGHT, self.flow)  # Now the window before_quit names.
        other.show()
        shiboken6.delete(other)  # Gone without closing, so before_quit still names it.
        self.flow._ready()
        self.assertEqual(quits, [True])

    def test_closing_the_window_lets_the_update_go_on(self):
        quits = []
        self.flow.quit_requested.connect(lambda: quits.append(True))
        self.flow._set("preparing")
        self.flow._ready()
        self.win.dismiss()
        self.assertEqual(quits, [True])
        self.assertIsNone(self.flow.before_quit)

    def test_a_hidden_window_does_not_hold_the_quit(self):
        quits = []
        self.flow.quit_requested.connect(lambda: quits.append(True))
        self.win.hide()
        self.flow._ready()
        self.assertEqual(quits, [True])

    def test_the_version_line_stays_centred(self):
        for language in ("en", "zh"):
            with self.subTest(language=language):
                i18n.set_language(language)
                self.addCleanup(i18n.set_language, i18n.DEFAULT_LANGUAGE)
                flow = UpdateFlow(__version__, Path(tempfile.mkdtemp()))
                flow.release = Release("1.0.0rc10", "https://example.invalid", "", "")
                win = AboutWindow(LIGHT, flow)
                win.show()
                self.addCleanup(lambda win=win: win.closing or win.close())
                version = next(label for label in win.findChildren(QLabel)
                               if label.text() == i18n.tr("about.version", version=__version__))
                gaps = set()
                for state in ("checking", "available", "downloading", "verifying", "preparing", "installing",
                              "current", "failed"):
                    flow._set(state)
                    settle(win.btn_update)
                    _app.processEvents()
                    button = win.btn_update
                    text_w = button.fontMetrics().horizontalAdvance(button._shown)
                    self.assertGreaterEqual(button.width() - button.inset, text_w, f"{state}: the text fits")
                    version_left = version.mapTo(win, QPoint(0, 0)).x()
                    version_right = version.mapTo(win, QPoint(version.width(), 0)).x()
                    text_left = button.mapTo(win, QPoint(button.inset, 0)).x()
                    centre = (version_left + text_left + text_w) / 2
                    self.assertLessEqual(abs(centre - win.width() / 2), 1.0, f"{state}: the line is centred")
                    gaps.add(text_left - version_right)
                self.assertEqual(len(gaps), 1, f"the same gap after the dot in every state: {gaps}")

    def test_the_text_never_reaches_the_dot(self):
        line = self.win._version_line
        version, dot, button = line._parts
        clearances = []
        self.flow._set("checking")
        self.flow._set("current")
        for _ in range(120):  # Every frame of both changes, measuring the dot each time.
            dot_right = dot.x() + dot.width() - dot.contentsMargins().right()  # The dot's text ends here.
            self.assertGreaterEqual(button.x(), dot_right, "the button stays clear of the dot")
            clearances.append(button.x() + button.inset + button._dx - dot_right)  # The painted text's start.
            QTest.qWait(10)
        self.assertGreater(min(clearances), 0, f"the sliding text stays clear of the dot: {min(clearances)}")

    def test_the_width_eases_between_texts(self):
        self.flow._set("checking")
        self.flow._set("current")
        widths = []
        for _ in range(150):  # Through the fade to the long text, its hold and the fade to the short one.
            widths.append(self.button.sizeHint().width())
            QTest.qWait(10)
        long, short = (self.button.fontMetrics().horizontalAdvance(text)
                       for text in ("Checking for updates…", "Up to Date"))
        self.assertTrue(any(short < w < long for w in widths[widths.index(max(widths)):]),
                        f"the width passes through values in between: {sorted(set(widths))}")

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
