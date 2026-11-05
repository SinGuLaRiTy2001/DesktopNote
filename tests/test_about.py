"""About window tests: its content, the update button's states and transitions, and closing it.

Builds real windows; CI runs them with QT_QPA_PLATFORM=offscreen.
"""
import sys
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
from tests import _support  # noqa: E402

_support.qapp()

DEVELOPER = {"en": "Jian Wen", "zh": "文简"}
LATER = about.TEXT_HOLD_MS + 150  # Long enough for a pending timer to have fired.


def open_about(test: unittest.TestCase, language: str = "en", theme=LIGHT) -> AboutWindow:
    """Shows an About window in language and theme on its own UpdateFlow; it closes after the test."""
    _support.use_language(test, language)
    win = AboutWindow(theme, UpdateFlow(__version__, _support.temp_dir(test)))
    win.show()
    QApplication.processEvents()
    test.addCleanup(lambda: win.closing or win.close())  # A newer window may have closed it.
    return win


def texts(win: AboutWindow) -> list:
    """Returns the window's labels and buttons that show a text."""
    return [w for w in (*win.findChildren(QLabel), *win.findChildren(QAbstractButton)) if w.text()]


def settle(button) -> None:
    """Waits until the button shows its newest text, faded in and past its hold."""
    _support.wait_until(lambda: button.is_settled() and not button._hold.isActive(), 5000,
                        f"the update button did not settle on {button.text()!r}")


class AboutWindowTest(unittest.TestCase):
    """The About window shows the app's details, in full, in every language and theme."""

    def test_shows_its_details_and_no_copyright_notice(self):
        for language in i18n.LANGUAGES:
            with self.subTest(language=language):
                shown = " ".join(w.text() for w in texts(open_about(self, language)))
                for expected in ("DesktopNote", __version__, DEVELOPER[language], "Claude Code", "GitHub"):
                    self.assertIn(expected, shown)
                self.assertNotIn("©", shown)
                self.assertNotIn("copyright", shown.lower())

    def test_link_opens_the_repository(self):
        with mock.patch.object(about, "QDesktopServices") as services:
            open_about(self).btn_github.click()
        self.assertEqual([call.args[0].toString() for call in services.openUrl.call_args_list], [about.REPO_URL])

    def test_texts_fit(self):
        for language in i18n.LANGUAGES:
            for theme in (LIGHT, DARK):
                with self.subTest(language=language, dark=theme.dark):
                    win = open_about(self, language, theme)
                    for w in texts(win):
                        self.assertGreaterEqual(w.width(), w.sizeHint().width(), f"{w.text()!r} is cut off")
                        self.assertLessEqual(w.mapTo(win, w.rect().topRight()).x(), win.width())

    def test_escape_closes(self):
        win = open_about(self)
        QTest.keyClick(win, Qt.Key_Escape)
        self.assertTrue(win.closing)

    def test_any_close_counts(self):
        win = open_about(self)
        win.setAttribute(Qt.WA_DeleteOnClose, False)  # Kept to look at it afterwards.
        QApplication.sendEvent(win, QCloseEvent())  # As Alt+F4 sends it.
        self.assertTrue(win.closing)


class VersionLineTest(unittest.TestCase):
    """In every update state, the version line shows the state's text, in full, centred, and acts
    only in the states that offer an action. The transitions run quickly here; their timing is
    tested in UpdateStatusTest."""

    STATES = (("checking", "Checking for updates…", False), ("available", "Download 1.0.0rc10", True),
              ("downloading", "Downloading update…", False), ("verifying", "Verifying update…", False),
              ("preparing", "Preparing update…", False), ("installing", "Installing update…", False),
              ("current", "Up to Date", True), ("failed", "Try Again", True))

    def setUp(self):
        for name, value in (("TEXT_OUT_MS", 10), ("TEXT_IN_MS", 10), ("TEXT_HOLD_MS", 10)):
            patcher = mock.patch.object(about, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)

    def test_every_state(self):
        for language in ("en", "zh"):
            with self.subTest(language=language):
                win = open_about(self, language)
                flow, button = win._updates, win.btn_update
                flow.release = Release("1.0.0rc10", "https://example.invalid", "", "")
                version = next(label for label in win.findChildren(QLabel)
                               if label.text() == i18n.tr("about.version", version=__version__))
                gaps = set()
                for state, text, enabled in self.STATES:
                    flow._set(state, error="offline" if state == "failed" else "")
                    settle(button)
                    QApplication.processEvents()
                    if language == "en":
                        self.assertEqual((button._shown, button.isEnabled()), (text, enabled), state)
                    text_w = button.fontMetrics().horizontalAdvance(button._shown)
                    self.assertGreaterEqual(button.width() - button.inset, text_w, f"{state}: the text fits")
                    text_left = button.mapTo(win, QPoint(button.inset, 0)).x()
                    centre = (version.mapTo(win, QPoint(0, 0)).x() + text_left + text_w) / 2
                    self.assertLessEqual(abs(centre - win.width() / 2), 1.0, f"{state}: the line is centred")
                    gaps.add(text_left - version.mapTo(win, QPoint(version.width(), 0)).x())
                self.assertEqual(len(gaps), 1, f"the same gap after the dot in every state: {gaps}")
                self.assertEqual(button.toolTip(), "offline", "a failure says why")


class UpdateStatusTest(unittest.TestCase):
    """The update button shows each stage in turn, readable, and the app quits only once
    "Installing update…" has been painted and shown."""

    def setUp(self):
        self.win = open_about(self)
        self.flow, self.button = self.win._updates, self.win.btn_update
        self.shown: list[tuple[str, float]] = []  # (text, ms since start) each time a text has faded in.
        self.quits: list[tuple] = []  # (seen text, ms it was shown, opacity, slide) each time the app is asked to quit.
        self.clock = QElapsedTimer()
        self.clock.start()
        faded_in = self.button._faded_in

        def record():
            self.shown.append((self.button._shown, self.clock.elapsed()))
            faded_in()
        self.button._faded_in = record
        self.flow.quit_requested.connect(lambda: self.quits.append(
            (self.button._seen, self.button._seen_clock.elapsed(), self.button._opacity, self.button._dx)))

    def install_ready(self) -> None:
        """Shows "Preparing update…", then reports the install script as ready."""
        self.flow._set("preparing")
        settle(self.button)
        self.flow._ready()

    def test_quick_stages_are_each_shown_in_order(self):
        for state in ("downloading", "verifying", "preparing", "installing"):
            self.flow._set(state)
        settle(self.button)
        self.assertEqual([text for text, _ in self.shown], ["Downloading update…", "Verifying update…",
                                                           "Preparing update…", "Installing update…"])
        gaps = [later - earlier for (_, earlier), (_, later) in zip(self.shown, self.shown[1:])]
        self.assertTrue(all(gap >= about.TEXT_HOLD_MS for gap in gaps), f"each text stays readable: {gaps}")
        self.assertEqual((round(self.button._opacity, 2), self.button._dx), (1.0, 0.0))

    def test_the_newest_text_fades_in_and_the_button_waits_for_it(self):
        self.flow._set("checking")
        self.flow._set("current")
        self.assertEqual(self.button.text(), "Up to Date", "text() is the new text at once")
        self.assertFalse(self.button.isEnabled(), "still showing an earlier text")
        settle(self.button)
        self.assertEqual((self.button._shown, round(self.button._opacity, 2), self.button._dx),
                         ("Up to Date", 1.0, 0.0))
        self.assertTrue(self.button.isEnabled())

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

    def test_installing_is_painted_and_shown_before_the_app_quits(self):
        self.install_ready()
        self.assertEqual(self.quits, [], "not when the text is only scheduled")
        _support.wait_until(lambda: self.button._shown == "Installing update…")
        self.assertTrue(self.button._fading)
        self.assertEqual(self.quits, [], "not while the text fades in")
        _support.wait_until(lambda: self.button._seen == "Installing update…")
        self.assertEqual(self.quits, [], "not as soon as it is painted at rest")
        _support.wait_until(lambda: self.quits)
        QTest.qWait(LATER)
        self.assertEqual(len(self.quits), 1, "the app is asked to quit once")
        seen, shown_ms, opacity, dx = self.quits[0]
        self.assertEqual((seen, opacity, dx), ("Installing update…", 1.0, 0.0), "painted fully faded in, at rest")
        self.assertGreaterEqual(shown_ms, about.TEXT_HOLD_MS - 1, "and shown for TEXT_HOLD_MS since")

    def test_hiding_the_window_while_waiting_quits_at_once(self):
        self.install_ready()
        _support.wait_until(lambda: self.button._seen == "Installing update…")
        self.win.hide()
        self.assertEqual(len(self.quits), 1)
        QTest.qWait(LATER)
        self.assertEqual(len(self.quits), 1, "the pending timer adds no second request")

    def test_closing_the_window_during_the_fade_quits_at_once(self):
        self.install_ready()
        _support.wait_until(lambda: self.button._fading)
        self.win.dismiss()
        self.assertEqual(len(self.quits), 1)
        self.assertIsNone(self.flow.before_quit, "the closed window no longer holds the quit")
        QTest.qWait(LATER)
        self.assertEqual(len(self.quits), 1)

    def test_a_window_that_is_hidden_or_deleted_does_not_hold_the_quit(self):
        for gone in ("hidden", "deleted"):
            with self.subTest(gone):
                self.quits.clear()
                self.flow._quit_asked = False
                win = AboutWindow(LIGHT, self.flow)  # Now the window before_quit names.
                win.show()
                if gone == "hidden":
                    win.hide()
                    self.addCleanup(lambda win=win: win.closing or win.close())
                else:
                    shiboken6.delete(win)  # Gone without closing, so before_quit still names it.
                self.flow._ready()
                self.assertEqual(len(self.quits), 1)

    def test_the_text_never_reaches_the_dot(self):
        version, dot, button = self.win._version_line._parts
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

    def test_which_note_is_activated(self):
        note = NoteWindow(Note(w=280, h=220), LIGHT, 60, 1.0)
        note.show()
        self.addCleanup(note.deleteLater)
        self.addCleanup(note.close)
        mine = int(note.winId())
        cases = (("the closest note behind", [mine], (), True, True),
                 ("other apps' topmost windows are skipped", [111, mine], {111}, True, True),
                 ("notes behind another app stay behind", [222, mine], (), True, False),
                 ("a click elsewhere hands nothing over", [mine], (), False, False))
        for name, behind, topmost, hand_over, activated in cases:
            with self.subTest(name):
                win = open_about(self)  # After the note: About closes when another window becomes active.
                self.assertFalse(win.closing)
                with (mock.patch.object(win_effects, "windows_behind", return_value=behind),
                      mock.patch.object(win_effects, "is_topmost", side_effect=lambda hwnd: hwnd in topmost),
                      mock.patch.object(NoteWindow, "activateWindow") as activate):
                    win.dismiss(hand_over=hand_over)
                self.assertEqual(activate.called, activated)


if __name__ == "__main__":
    unittest.main()
