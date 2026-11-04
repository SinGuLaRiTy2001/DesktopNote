"""Startup lock tests: the notes are only read while holding the data folder's lock.

Another process holding the lock (a command line run, a starting DesktopNote) is stood in for by a
second Store in this process: the lock is a byte-range lock, so a second handle cannot take it.
"""
import json
import os
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from PySide6.QtCore import QTimer  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from desktopnote import app, cli, storage  # noqa: E402
from desktopnote.models import Note  # noqa: E402
from desktopnote.storage import DataFolderBusy, Store  # noqa: E402

_app = QApplication.instance() or QApplication(sys.argv)
ROOT = Path(__file__).resolve().parents[1]
NOTES = json.dumps({"settings": {"first_run": False}, "notes": [{"title": "Keep me", "items": []}]})


class LockTestCase(unittest.TestCase):
    """A data folder with notes, and a holder for its lock."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.folder = Path(self._tmp.name)
        (self.folder / "notes.json").write_text(NOTES, encoding="utf-8")
        self.holder = Store(self.folder / "notes.json")
        env = mock.patch.dict(os.environ, {"DESKTOPNOTE_HOME": str(self.folder)})
        env.start()
        self.addCleanup(env.stop)
        self.loads = []
        real_load = Store.load

        def load(store):
            self.loads.append(store._lock is not None)  # Whether this store held the lock while reading.
            real_load(store)

        patch = mock.patch.object(Store, "load", load)
        patch.start()
        self.addCleanup(patch.stop)

    def tearDown(self):
        if self.holder._lock is not None:
            self.holder._unlock()
        self._tmp.cleanup()

    def hold(self, release_after_ms: int | None = None) -> None:
        """Takes the lock, and gives it up after release_after_ms."""
        self.assertTrue(self.holder._try_lock())
        if release_after_ms is not None:
            QTimer.singleShot(release_after_ms, self.holder._unlock)

    def notes_file(self) -> str:
        return (self.folder / "notes.json").read_text(encoding="utf-8")


class StoreLockTest(LockTestCase):
    """Store reads only with the lock, waits a bounded time, and says when it gave up."""

    def test_try_load_reads_nothing_while_the_lock_is_taken(self):
        self.hold()
        store = Store()
        self.assertFalse(store.try_load())
        self.assertEqual((self.loads, store.notes), ([], []))
        self.holder._unlock()
        self.assertTrue(store.try_load())
        self.assertEqual((self.loads, [n.title for n in store.notes]), ([True], ["Keep me"]))
        self.assertTrue(store._try_lock(), "and lets go of the lock afterwards")
        store._unlock()

    def test_locked_waits_then_gives_up(self):
        self.hold()
        started = time.monotonic()
        with self.assertRaises(DataFolderBusy):
            with Store().locked(timeout_s=0.3):
                self.fail("ran without the lock")
        self.assertGreaterEqual(time.monotonic() - started, 0.3)

    def test_locked_goes_ahead_once_the_lock_is_free(self):
        self.hold()
        threading.Timer(0.3, self.holder._unlock).start()
        with Store().locked(timeout_s=5):
            pass

    def test_the_app_never_falls_back_to_reading_without_the_lock(self):
        self.hold()
        with mock.patch.object(storage, "LOCK_WAIT_S", 0.2), self.assertRaises(DataFolderBusy):
            app.DesktopNoteApp(_app)
        self.assertEqual(self.loads, [])


class OpenStoreTest(LockTestCase):
    """At startup the app waits for the lock with the event loop running, then reads the notes."""

    def test_the_lock_free_at_once(self):
        store = app.open_store(ask=self.fail, error=self.fail)
        self.assertEqual([n.title for n in store.notes], ["Keep me"])
        self.assertEqual(self.loads, [True])

    def test_it_waits_for_the_lock_to_come_free(self):
        self.hold(release_after_ms=400)
        ticks = []
        timer = QTimer(interval=20, timeout=lambda: ticks.append(1))
        timer.start()
        store = app.open_store(wait_ms=5000, ask=self.fail, error=self.fail)
        timer.stop()
        self.assertEqual([n.title for n in store.notes], ["Keep me"])
        self.assertEqual(self.loads, [True], "read once, with the lock")
        self.assertGreater(len(ticks), 5, "the event loop kept running while it waited")

    def test_retry_waits_again(self):
        self.hold()
        asked = []

        def ask(folder, answer):
            asked.append(folder)
            if len(asked) == 1:
                QTimer.singleShot(50, lambda: answer(True))  # Retry: another wait_ms, then asked again.
            else:
                QTimer.singleShot(50, lambda: (self.holder._unlock(), answer(True)))
            return lambda: None

        store = app.open_store(wait_ms=200, ask=ask, error=self.fail)
        self.assertEqual(asked, [str(self.folder)] * 2)
        self.assertIsNotNone(store)

    def test_the_question_goes_away_when_the_lock_comes_free(self):
        self.hold()
        withdrawn = []

        def ask(folder, answer):
            QTimer.singleShot(300, self.holder._unlock)  # Freed while the question is open.
            return lambda: withdrawn.append(True)

        store = app.open_store(wait_ms=200, ask=ask, error=self.fail)
        self.assertEqual([n.title for n in store.notes], ["Keep me"])
        self.assertEqual(withdrawn, [True])

    def test_the_default_question_is_a_dialog_that_does_not_block(self):
        self.hold()
        QTimer.singleShot(700, self.holder._unlock)
        store = app.open_store(wait_ms=200, error=self.fail)
        self.assertIsNotNone(store, "the lock came free with the dialog open, and it went away")
        self.assertEqual([w for w in _app.topLevelWidgets() if w.isVisible() and w.windowTitle() == "DesktopNote"
                          and type(w).__name__ == "QMessageBox"], [])

    def test_giving_up_reads_and_writes_nothing(self):
        self.hold()
        before = self.notes_file()
        with self.assertLogs("desktopnote.app", "ERROR"):
            self.assertIsNone(app.open_store(wait_ms=200, ask=lambda folder, answer: answer(False), error=self.fail))
        self.assertEqual(self.loads, [])
        self.assertEqual(self.notes_file(), before)
        self.assertEqual(sorted(p.name for p in self.folder.iterdir()), ["notes.json", "notes.lock"])

    def test_a_data_folder_that_cannot_be_opened_is_reported(self):
        shown = []
        with mock.patch.object(Store, "_try_lock", side_effect=PermissionError("access denied")), \
                self.assertLogs("desktopnote.app", "ERROR"):
            self.assertIsNone(app.open_store(ask=self.fail, error=shown.append))
        self.assertEqual(len(shown), 1)
        self.assertIn("access denied", shown[0])

    def test_without_the_notes_nothing_starts(self):
        """main() returns 1 before the local server, the tray or any window exists."""
        with mock.patch.object(app, "claim", return_value=True), \
                mock.patch.object(app, "ready_to_start", return_value=True), \
                mock.patch.object(app, "_setup_logging"), \
                mock.patch.object(app, "QApplication", return_value=_app), \
                mock.patch.object(app.QGuiApplication, "setHighDpiScaleFactorRoundingPolicy"), \
                mock.patch.object(app.win_effects, "register_app_id"), \
                mock.patch.object(app, "open_store", return_value=None), \
                mock.patch.object(app, "QLocalServer") as server, \
                mock.patch.object(app, "DesktopNoteApp") as desktop_note:
            self.assertEqual(app.main(), 1)
        server.assert_not_called()
        desktop_note.assert_not_called()


class CommandLineLockTest(LockTestCase):
    """The command line takes the same lock: it waits for it, or says the notes are in use."""

    def test_it_waits_for_the_lock(self):
        self.hold(release_after_ms=None)
        threading.Timer(0.5, self.holder._unlock).start()
        env = {**os.environ, "DESKTOPNOTE_HOME": str(self.folder), "PYTHONIOENCODING": "utf-8"}
        done = subprocess.run([sys.executable, "-m", "desktopnote.cli", "add", "todo", "Waited"], cwd=ROOT, env=env,
                              capture_output=True, encoding="utf-8", timeout=60)
        self.assertTrue(json.loads(done.stdout)["ok"], done.stderr)
        titles = [n["title"] for n in json.loads(self.notes_file())["notes"]]
        self.assertEqual(titles, ["Keep me", "Inbox"], "the earlier notes are kept")

    def test_a_lock_that_stays_taken_is_a_clear_error(self):
        self.hold()
        before = self.notes_file()
        with mock.patch.object(storage, "LOCK_WAIT_S", 0.2):
            result = cli.run_offline({"command": "add", "kind": "todo", "text": "x"})
        self.assertFalse(result["ok"])
        self.assertIn("in use by another program", result["error"])
        self.assertEqual(self.notes_file(), before)

    def test_a_note_saved_meanwhile_is_not_lost(self):
        """A command that waited for the lock reads what the holder saved, not an older copy."""
        self.hold()
        self.holder.load()
        self.holder.notes.append(Note(title="Saved while the command waited"))
        threading.Timer(0.3, lambda: (self.holder.save(), self.holder._unlock())).start()
        request = {"command": "add", "kind": "todo", "text": "x", "note": "Saved while the command waited"}
        result = cli.run_offline(request)
        self.assertTrue(result["ok"])
        self.assertFalse(result["new_note"], "it found the note the holder had just saved")


if __name__ == "__main__":
    unittest.main()
