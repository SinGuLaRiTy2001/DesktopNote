"""Update recovery tests: an update cut short at any step is undone, or finished if it had started.

Runs the real update script (updates.UPDATE_SCRIPT) on a stand-in app folder. The script stops at a
named step when DESKTOPNOTE_TEST_STOP_AT is set, as a power cut would stop it; the recovery then
runs as it does at the next login.
"""
import json
import os
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from desktopnote import i18n, updates  # noqa: E402

TEST_RUN_ONCE = (r"HKCU:\Software\DesktopNoteTests\RunOnce", "DesktopNoteUpdateTest")
OLD_FILES = {"DesktopNote.cmd": None, "_internal/version.txt": b"old", "_internal/lib/core.bin": b"old core",
             "_internal/release-files.txt": b"DesktopNote.cmd\n_internal\nLICENSE\nretired.txt\n",
             "LICENSE": b"old licence", "retired.txt": b"only in the old release"}
NEW_FILES = {"DesktopNote.cmd": None, "_internal/version.txt": b"new", "_internal/lib/core.bin": b"new core",
             "_internal/release-files.txt": b"DesktopNote.cmd\n_internal\nLICENSE\nadded.txt\n",
             "LICENSE": b"new licence", "added.txt": b"only in the new release"}
USER_FILE = ("my-file.txt", b"the user's own file")


_GONE_PID: int | None = None


def gone_pid() -> int:
    """Returns the id of a process that has ended, for the script to wait for (made once per run)."""
    global _GONE_PID
    if _GONE_PID is None:
        gone = subprocess.Popen([sys.executable, "-c", "pass"])
        gone.wait()
        _GONE_PID = gone.pid
    return _GONE_PID


def run_once_value() -> str | None:
    """Returns the test RunOnce value, or None."""
    import winreg

    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, TEST_RUN_ONCE[0].split(":\\", 1)[1]) as key:
            return winreg.QueryValueEx(key, TEST_RUN_ONCE[1])[0]
    except OSError:
        return None


@unittest.skipUnless(sys.platform == "win32", "the update script is PowerShell")
class RecoveryTestCase(unittest.TestCase):
    """A stand-in installation with an update ready to be swapped in."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        root = Path(self._tmp.name) / "O’Neil's apps [1]"
        self.app, self.data = root / "DesktopNote", root / "data"
        self.tx = self.app / updates.TRANSACTION
        self.started = root / "started.txt"  # Where the stand-in app notes each start.
        self.write(self.app, OLD_FILES, "old", confirms=False)
        (self.app / USER_FILE[0]).write_bytes(USER_FILE[1])
        self.write(self.tx / updates.NEW, NEW_FILES, "new", confirms=True)
        self.data.mkdir(parents=True)
        (self.data / "notes.json").write_bytes(b'{"notes": []}')
        (self.data / updates.NOTES_BACKUP).write_bytes(b'{"notes": []}')
        self.old_snapshot = self.snapshot()
        self.new_snapshot = {**self.files_of(self.tx / updates.NEW), USER_FILE[0]: USER_FILE[1]}
        self.data_snapshot = self.files_of(self.data)
        self.command = updates.prepare(self.app, "9.9.9", self.data, exe="DesktopNote.cmd", started_s=4,
                                       run_once=TEST_RUN_ONCE)

    def tearDown(self):
        updates._set_run_once(*TEST_RUN_ONCE, None)
        self._tmp.cleanup()

    def write(self, folder: Path, files: dict, name: str, confirms: bool) -> None:
        """Writes a release's files; DesktopNote.cmd notes its start and may confirm it."""
        for rel, data in files.items():
            path = folder / rel
            path.parent.mkdir(parents=True, exist_ok=True)
            if data is None:
                lines = [f'@echo {name}>> "%~dp0..\\started.txt"']
                if confirms:
                    lines.append(f'@del "%~dp0{updates.TRANSACTION}\\{updates.PENDING}"')
                data = ("\r\n".join(lines) + "\r\n").encode("utf-8")
            path.write_bytes(data)

    @staticmethod
    def files_of(folder: Path) -> dict[str, bytes]:
        """Returns every file under folder by its relative path, with its content."""
        return {p.relative_to(folder).as_posix(): p.read_bytes() for p in folder.rglob("*") if p.is_file()}

    def snapshot(self) -> dict[str, bytes]:
        """Returns the app folder's files, without the update folder."""
        return {rel: data for rel, data in self.files_of(self.app).items()
                if not rel.startswith(updates.TRANSACTION + "/")}

    def run_script(self, stop_at: str | None = None, recover: bool = False) -> int:
        """Runs the update script's swap (or its recovery) and returns its exit code.

        Args:
            stop_at: The step at which the script stops, as a power cut would stop it.
            recover: Runs the recovery, as at the next login, instead of the swap.
        """
        env = {k: v for k, v in os.environ.items() if k != "DESKTOPNOTE_TEST_STOP_AT"}
        if stop_at:
            env["DESKTOPNOTE_TEST_STOP_AT"] = stop_at
        if recover:
            args = updates._powershell(self.tx / updates.SCRIPT, "-Recover")
        else:
            args = [*self.command, str(gone_pid())]
        return subprocess.run(args, env=env, timeout=120).returncode

    def journal(self) -> dict:
        """Returns the update's journal."""
        return json.loads((self.tx / updates.JOURNAL).read_text(encoding="utf-8"))

    def starts(self, count: int) -> list[str]:
        """Waits for count starts of the stand-in app and returns which versions started."""
        for _ in range(100 if count else 0):
            if self.started.exists() and len(self.started.read_text().split()) >= count:
                break
            time.sleep(0.05)
        time.sleep(0.2)  # A further start would show up by now.
        return self.started.read_text().split() if self.started.exists() else []

    def assert_old_installed(self, starts: list[str]):
        self.assertEqual(self.snapshot(), self.old_snapshot, "the installed version is back, byte for byte")
        self.assertFalse(self.tx.exists(), "nothing of the update is left")
        self.assertIsNone(run_once_value(), "no recovery at the next login")
        self.assertEqual(self.notes(), self.data_snapshot, "the notes and their backup are untouched")
        self.assertEqual(self.starts(len(starts)), starts)

    def assert_new_installed(self, starts: list[str]):
        self.assertEqual(self.snapshot(), self.new_snapshot)
        self.assertFalse(self.tx.exists())
        self.assertIsNone(run_once_value())
        self.assertEqual(self.notes(), {"notes.json": b'{"notes": []}'}, "the notes are untouched, their backup gone")
        self.assertEqual(self.starts(len(starts)), starts)

    def notes(self) -> dict[str, bytes]:
        """Returns the data folder's files apart from the log."""
        return {rel: data for rel, data in self.files_of(self.data).items() if rel != "desktopnote.log"}

    def assert_starts_as_is(self, snapshot: dict[str, bytes]):
        """The next start finds nothing to recover and changes nothing."""
        self.assertTrue(updates.ready_to_start(self.app, message=self.fail))
        self.assertEqual(self.snapshot(), snapshot)

    def assert_old_files_safe(self):
        """Every file of the installed version is in the app folder or kept in the update folder."""
        for rel, data in self.old_snapshot.items():
            name = rel.split("/", 1)[0]
            in_app, kept = self.app / rel, self.tx / updates.OLD / rel
            here = in_app.read_bytes() if in_app.exists() and not (self.tx / updates.OLD / name).exists() else None
            self.assertIn(data, (here, kept.read_bytes() if kept.exists() else None), f"{rel} is lost")


class SwapTest(RecoveryTestCase):
    """Without interruption, an update installs, or rolls back when the new version does not start."""

    def test_a_new_version_that_does_not_start_is_rolled_back(self):
        self.write(self.tx / updates.NEW, {"DesktopNote.cmd": None}, "new", confirms=False)
        self.command = updates.prepare(self.app, "9.9.9", self.data, exe="DesktopNote.cmd", started_s=1,
                                       run_once=TEST_RUN_ONCE)  # Nothing confirms: a short deadline will do.
        self.assertEqual(self.run_script(), 0)
        self.assert_old_installed(["new", "old"])
        self.assertIn("did not start", (self.data / "desktopnote.log").read_text(encoding="utf-8-sig"))

    def test_an_update_that_starts_is_kept_even_by_a_later_recovery(self):
        self.assertEqual(run_once_value(), self.journal()["run_once_command"], "recovery is set up first")
        self.assertEqual(self.run_script(), 0)
        self.assert_new_installed(["new"])
        self.assert_starts_as_is(self.new_snapshot)


class InterruptedSwapTest(RecoveryTestCase):
    """A swap cut short at any step before the new version confirmed is undone, however often."""

    STOPS = ["moving", "old-1", "old-2", "old-3", "old-4", "installing", "new-1", "new-2", "new-3", "new-4",
             "installed"]

    def test_every_step(self):
        for stop in self.STOPS:
            with self.subTest(stop=stop):
                self.tearDown()
                self.setUp()
                self.assertEqual(self.run_script(stop_at=stop), 99)
                self.assert_old_files_safe()
                self.assertTrue(run_once_value(), "the next login recovers it")
                self.assertEqual(self.run_script(recover=True), 0)
                self.assert_old_installed(["old"])
                self.assert_starts_as_is(self.old_snapshot)

    def test_a_confirmed_update_cut_short_before_it_was_committed_is_kept(self):
        self.assertEqual(self.run_script(stop_at="installed"), 99)
        (self.tx / updates.PENDING).unlink()  # The new version had started and confirmed.
        self.assertEqual(self.run_script(recover=True), 0)
        self.assert_new_installed(["new"])


class InterruptedRollbackTest(RecoveryTestCase):
    """A recovery cut short is finished by the next one."""

    def test_every_rollback_step(self):
        for stop in ["rollback-new-1", "rollback-new-2", "rollback-new-4", "rollback-old-1", "rollback-old-3"]:
            with self.subTest(stop=stop):
                self.tearDown()
                self.setUp()
                self.run_script(stop_at="new-3")
                self.assertEqual(self.run_script(stop_at=stop, recover=True), 99)
                self.assertEqual(self.journal()["state"], "rolling_back")
                self.assert_old_files_safe()
                self.assertEqual(self.run_script(recover=True), 0)
                self.assert_old_installed(["old"])


class InterruptedCleanupTest(RecoveryTestCase):
    """Once the new version confirmed, an interruption never brings the old one back."""

    def test_cut_short_while_committing(self):
        for stop in ["committed", "cleaned"]:
            with self.subTest(stop=stop):
                self.tearDown()
                self.setUp()
                self.assertEqual(self.run_script(stop_at=stop), 99)
                self.assertEqual(self.journal()["state"], "committed")
                self.assertEqual(self.run_script(recover=True), 0)
                self.assert_new_installed(["new", "new"])


class BlockedRecoveryTest(RecoveryTestCase):
    """A file that cannot be moved stops the recovery without losing anything, until it can."""

    def test_a_file_in_use(self):
        self.run_script(stop_at="installed")
        with open(self.app / "_internal" / "version.txt", "rb"):  # The new release's file, in use.
            self.run_script(recover=True)
            self.assertIn("_internal", self.journal()["failed"])
            self.assert_old_files_safe()
            self.assertTrue(run_once_value(), "the next login tries again")
            self.assertIn("is not finished", (self.data / "desktopnote.log").read_text(encoding="utf-8-sig"))
        self.assertEqual(self.starts(0), [], "no version was started while the files were mixed")
        self.assertEqual(self.run_script(recover=True), 0)
        self.assert_old_installed(["old"])


class StartupTest(unittest.TestCase):
    """At start, the app hands an update cut short to the recovery and starts only when it may."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.app = Path(self._tmp.name)
        self.tx = self.app / updates.TRANSACTION
        self.messages, self.started = [], []
        self.addCleanup(i18n.set_language, i18n.language())

    def tearDown(self):
        self._tmp.cleanup()

    def ready(self) -> bool:
        with mock.patch.object(updates.subprocess, "Popen", side_effect=lambda args, **kw: self.started.append(args)):
            return updates.ready_to_start(self.app, message=lambda title, text: self.messages.append(text))

    def transaction(self, state: str, failed: str = "", script: bool = True) -> None:
        self.tx.mkdir(exist_ok=True)
        (self.tx / updates.JOURNAL).write_text(json.dumps({"state": state, "failed": failed}), encoding="utf-8")
        if script:
            (self.tx / updates.SCRIPT).write_text(updates.UPDATE_SCRIPT, encoding="utf-8-sig")

    def test_without_an_update_it_starts(self):
        self.assertTrue(self.ready())
        (self.tx / updates.NEW).mkdir(parents=True)  # A download that never started to install.
        self.assertTrue(self.ready())
        self.assertFalse(self.tx.exists())

    def test_an_update_cut_short_is_recovered_first(self):
        self.transaction("installing")
        self.assertFalse(self.ready())
        self.assertEqual([args[-1] for args in self.started], ["-Recover"])
        self.assertEqual(self.messages, [])

    def test_a_recovery_that_could_not_finish_says_what_to_do(self):
        self.transaction("rolling_back", failed="could not put back _internal")
        self.assertFalse(self.ready())
        self.assertEqual(len(self.messages), 1)
        self.assertIn("could not put back _internal", self.messages[0])
        self.assertEqual(len(self.started), 1, "and tries again")

    def test_incomplete_update_files_say_what_to_do(self):
        self.transaction("moving", script=False)
        self.assertFalse(self.ready())
        self.assertEqual((len(self.messages), self.started), (1, []))

    @unittest.skipUnless(sys.platform == "win32", "the script lock is a Windows file share mode")
    def test_while_a_script_runs_only_the_installed_version_starts(self):
        from ctypes import windll, wintypes

        for state, allowed in (("moving", False), ("rolling_back", False), ("installed", True), ("committed", True)):
            with self.subTest(state=state):
                self.transaction(state)
                create = windll.kernel32.CreateFileW
                create.restype = wintypes.HANDLE
                handle = create(str(self.tx / updates.SCRIPT_LOCK), 0xC0000000, 0, None, 4, 0x80, None)
                try:
                    self.assertEqual(self.ready(), allowed)
                finally:
                    windll.kernel32.CloseHandle(handle)
                self.assertEqual(self.started, [], "no second script")


def tearDownModule():
    """Removes the registry keys the tests' RunOnce values were kept in."""
    if sys.platform != "win32":
        return
    import winreg

    for key in (r"Software\DesktopNoteTests\RunOnce", r"Software\DesktopNoteTests"):
        try:
            winreg.DeleteKey(winreg.HKEY_CURRENT_USER, key)
        except OSError:
            pass


if __name__ == "__main__":
    unittest.main()
