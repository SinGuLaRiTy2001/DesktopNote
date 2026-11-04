"""App tests: saving when the disk says no, and telling the user about unreadable notes."""
import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from PySide6.QtCore import QTimer  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from desktopnote import app  # noqa: E402

_app = QApplication.instance() or QApplication(sys.argv)


class SaveTest(unittest.TestCase):
    """Failed saves are retried, and the tray says so when they keep failing."""

    def owner(self, save):
        messages = []
        owner = mock.Mock(store=mock.Mock(save=save), save_timer=QTimer(singleShot=True), _save_failures=0,
                          _tray_message=lambda heading, text: messages.append(text))
        return owner, messages

    def test_a_failed_save_is_retried_and_reported_once(self):
        owner, messages = self.owner(mock.Mock(side_effect=PermissionError("in use")))
        with self.assertLogs("desktopnote.app", "ERROR"):
            for _ in range(app.SAVE_WARN_AFTER + 2):
                app.DesktopNoteApp.save_now(owner)
        self.assertTrue(owner.save_timer.isActive())
        self.assertEqual(owner.save_timer.interval(), app.SAVE_RETRY_MS)
        self.assertEqual(len(messages), 1)
        self.assertIn("in use", messages[0])

    def test_a_good_save_clears_the_failures(self):
        owner, messages = self.owner(mock.Mock())
        owner._save_failures = 2
        app.DesktopNoteApp.save_now(owner)
        self.assertEqual((owner._save_failures, owner.save_timer.isActive(), messages), (0, False, []))

    def test_unreadable_files_are_reported(self):
        for from_backup, names, expected in ((True, ["notes-unreadable-1.json"], "restored from the backup"),
                                             (False, ["notes-unreadable-1.json"], "could not be read"),
                                             (False, [], None)):
            with self.subTest(from_backup=from_backup, names=names):
                owner, messages = self.owner(mock.Mock())
                owner.store.from_backup, owner.store.set_aside = from_backup, [Path(n) for n in names]
                app.i18n.set_language("en")
                app.DesktopNoteApp._report_unreadable(owner)
                if expected is None:
                    self.assertEqual(messages, [])
                else:
                    self.assertEqual(len(messages), 1)
                    self.assertIn(expected, messages[0])
                    self.assertIn("notes-unreadable-1.json", messages[0])


if __name__ == "__main__":
    unittest.main()
