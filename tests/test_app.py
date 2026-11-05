"""App tests: the local server, keeping notes reachable, and saving when the disk says no."""
import json
import os
import sys
import time
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from PySide6.QtCore import QRect, QTimer  # noqa: E402
from PySide6.QtGui import QGuiApplication  # noqa: E402
from PySide6.QtNetwork import QLocalServer, QLocalSocket  # noqa: E402
from PySide6.QtTest import QTest  # noqa: E402

from desktopnote import app  # noqa: E402
from desktopnote.models import Note  # noqa: E402
from tests import _support  # noqa: E402

_app = _support.qapp()


class ServerTest(unittest.TestCase):
    """The instance's local server answers without blocking and always replies to a request."""

    def setUp(self):
        self.name = f"DesktopNote-test-server-{os.getpid()}"
        self.server = QLocalServer()
        self.assertTrue(self.server.listen(self.name))
        self.requests, self.shown = [], []
        self.handle = lambda request: self.requests.append(request) or {"ok": True}
        self.answered = []

        def connected():
            started = time.monotonic()
            app.answer(self.server.nextPendingConnection(), lambda r: self.handle(r), lambda: self.shown.append(1))
            self.answered.append(time.monotonic() - started)

        self.server.newConnection.connect(connected)

    def tearDown(self):
        self.server.close()

    def client(self, message: bytes | None, close: bool = False) -> QLocalSocket:
        sock = QLocalSocket()
        sock.connectToServer(self.name)
        self.assertTrue(sock.waitForConnected(1000))
        if message is not None:
            sock.write(message)
            sock.flush()
        if close:
            sock.disconnectFromServer()
        return sock

    def reply(self, sock: QLocalSocket) -> dict:
        data = b""
        for _ in range(100):
            QTest.qWait(10)
            data += bytes(sock.readAll())
            if data.endswith(b"\n"):
                break
        return json.loads(data)

    def test_a_request_is_carried_out(self):
        sock = self.client(app.COMMAND_PREFIX + b'{"command": "list"}\n')
        self.assertEqual(self.reply(sock), {"ok": True})
        self.assertEqual(self.requests, [{"command": "list"}])

    def test_a_slow_client_does_not_block(self):
        sock = self.client(app.COMMAND_PREFIX + b'{"command": ')
        QTest.qWait(100)
        self.assertLess(max(self.answered), 0.05, "answer() returned at once")
        sock.write(b'"list"}\n')
        self.assertEqual(self.reply(sock), {"ok": True})

    def test_requests_that_are_not_objects_get_an_error(self):
        for body in (b"5", b"[]", b"{nope"):
            with self.subTest(body=body):
                self.assertEqual(self.reply(self.client(app.COMMAND_PREFIX + body + b"\n")),
                                 {"ok": False, "error": "unreadable request"})
        self.assertEqual(self.requests, [])

    def test_a_failing_command_still_answers(self):
        self.handle = lambda request: 1 / 0
        with self.assertLogs("desktopnote.app", "ERROR"):
            reply = self.reply(self.client(app.COMMAND_PREFIX + b'{"command": "list"}\n'))
        self.assertFalse(reply["ok"])
        self.assertIn("division by zero", reply["error"])

    def test_a_second_launch_shows_the_notes(self):
        self.client(b"show", close=True)
        QTest.qWait(100)
        self.assertEqual(self.shown, [1])
        self.client(None, close=True)  # An empty message counts as one too.
        QTest.qWait(100)
        self.assertEqual(self.shown, [1, 1])

    def test_a_silent_connection_is_closed(self):
        with mock.patch.object(app, "REQUEST_MS", 100):
            sock = self.client(app.COMMAND_PREFIX + b"{")
            QTest.qWait(400)
        self.assertEqual(sock.state(), QLocalSocket.UnconnectedState)
        self.assertEqual(self.requests, [])


class OnScreenTest(unittest.TestCase):
    """A note's drag handle stays reachable; a lost note comes back."""

    def setUp(self):
        self.area = QGuiApplication.primaryScreen().availableGeometry()

    def placed(self, x, y, w=300, h=340) -> tuple[int, int, int, int]:
        note = Note(x=x, y=y, w=w, h=h)
        app.DesktopNoteApp._ensure_on_screen(note)
        return note.x, note.y, note.w, note.h

    def test_a_reachable_note_stays_put(self):
        a = self.area
        self.assertEqual(self.placed(a.left() + 10, a.top() + 10), (a.left() + 10, a.top() + 10, 300, 340))
        bottom = a.bottom() - 30  # Mostly below the screen, but its handle shows.
        self.assertEqual(self.placed(a.left() + 10, bottom), (a.left() + 10, bottom, 300, 340))

    def test_a_note_with_its_handle_above_the_screen_moves_in(self):
        a = self.area
        self.assertEqual(self.placed(a.left() + 10, a.top() - 150), (a.left() + 10, a.top(), 300, 340))

    def test_a_note_off_every_screen_is_centred(self):
        a = self.area
        x, y, w, h = self.placed(a.right() + 5000, a.top())
        self.assertEqual((w, h), (300, 340))
        self.assertTrue(a.contains(QRect(x, y, w, h)))

    def test_a_note_larger_than_the_screen_shrinks(self):
        a = self.area
        x, y, w, h = self.placed(a.left(), a.top() - 100, a.width() + 50, a.height() + 50)
        self.assertEqual(QRect(x, y, w, h), a)


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
        _support.use_language(self, "en")
        for from_backup, names, expected in ((True, ["notes-unreadable-1.json"], "restored from the backup"),
                                             (False, ["notes-unreadable-1.json"], "could not be read"),
                                             (False, [], None)):
            with self.subTest(from_backup=from_backup, names=names):
                owner, messages = self.owner(mock.Mock())
                owner.store.from_backup, owner.store.set_aside = from_backup, [Path(n) for n in names]
                app.DesktopNoteApp._report_unreadable(owner)
                if expected is None:
                    self.assertEqual(messages, [])
                else:
                    self.assertEqual(len(messages), 1)
                    self.assertIn(expected, messages[0])
                    self.assertIn("notes-unreadable-1.json", messages[0])


if __name__ == "__main__":
    unittest.main()
