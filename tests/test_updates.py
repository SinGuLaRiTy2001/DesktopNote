"""Update tests: picking the release, checking the download, and backing up the notes.

Swapping the app files, and recovering a swap cut short, is tested in test_update_recovery.py.
"""
import contextlib
import hashlib
import io
import sys
import tempfile
import threading
import unittest
import zipfile
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from PySide6.QtCore import QUrl  # noqa: E402
from PySide6.QtNetwork import QHostAddress, QTcpServer  # noqa: E402
from PySide6.QtTest import QTest  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from desktopnote import updates  # noqa: E402

_app = QApplication.instance() or QApplication(sys.argv)  # Other test modules need widgets.


def release(tag: str, prerelease: bool = False, draft: bool = False, assets: bool = True) -> dict:
    """Returns a release as GitHub's API lists it."""
    name = f"DesktopNote-{tag.lstrip('v')}-win64.zip"
    files = [{"name": n, "browser_download_url": f"https://example.invalid/{n}"} for n in (name, f"{name}.sha256")]
    return {"tag_name": tag, "prerelease": prerelease, "draft": draft, "html_url": f"https://example.invalid/{tag}",
            "assets": files if assets else []}


class VersionTest(unittest.TestCase):
    """Versions sort like releases: pre-releases before their final release."""

    def test_order(self):
        order = ["0.3.0", "1.0.0a1", "1.0.0b2", "1.0.0rc1", "1.0.0rc2", "1.0.0", "1.0.1", "1.1.0", "2.0.0"]
        parsed = [updates.parse_version(v) for v in order]
        self.assertEqual(parsed, sorted(parsed))
        self.assertEqual(updates.parse_version("v1.0.0"), updates.parse_version("1.0.0"))
        self.assertIsNone(updates.parse_version("latest"))

    def test_prerelease(self):
        self.assertTrue(updates.is_prerelease("1.0.0rc2"))
        self.assertFalse(updates.is_prerelease("1.0.0"))


class PickReleaseTest(unittest.TestCase):
    """The newest release that counts as an update, by the installed kind."""

    releases = [release("v1.0.0rc1", True), release("v1.0.0rc3", True), release("v1.0.0"),
                release("v1.1.0", draft=True), release("v1.0.1", assets=False), release("nightly")]

    def test_a_prerelease_build_also_gets_prereleases(self):
        self.assertEqual(updates.pick_release(self.releases, "1.0.0rc2").version, "1.0.0")
        only_pre = [release("v1.0.0rc1", True), release("v1.0.0rc3", True)]
        self.assertEqual(updates.pick_release(only_pre, "1.0.0rc2").version, "1.0.0rc3")

    def test_a_final_build_only_gets_final_releases(self):
        self.assertIsNone(updates.pick_release([release("v1.0.1rc1", True)], "1.0.0"))
        self.assertEqual(updates.pick_release([release("v1.0.1rc1", True), release("v1.0.1")], "1.0.0").version,
                         "1.0.1")

    def test_drafts_releases_without_files_and_older_ones_are_skipped(self):
        self.assertIsNone(updates.pick_release(self.releases, "1.0.0"), "1.1.0 is a draft; 1.0.1 has no zip")
        self.assertIsNone(updates.pick_release([release("v0.3.0")], "1.0.0rc2"))


class InstallTest(unittest.TestCase):
    """What runs before the app files are swapped: nothing touches the notes but a backup."""

    def test_the_notes_are_backed_up_not_changed(self):
        data = Path(tempfile.mkdtemp())
        (data / "notes.json").write_text('{"notes": []}', encoding="utf-8")
        backup = updates.backup_notes(data)
        self.assertEqual(backup.read_text(encoding="utf-8"), '{"notes": []}')
        self.assertEqual((data / "notes.json").read_text(encoding="utf-8"), '{"notes": []}')
        self.assertIsNone(updates.backup_notes(Path(tempfile.mkdtemp())), "no notes, no backup")

    def test_the_installed_release_files(self):
        app = Path(tempfile.mkdtemp())
        for name in ("DesktopNote.exe", "my-file.txt", "_internal"):
            (app / name).mkdir() if name == "_internal" else (app / name).write_text("x")
        self.assertEqual(updates.release_files(app), ["DesktopNote.exe", "_internal"],
                         "without a manifest: the early releases' files, but never the user's")
        (app / "_internal" / "release-files.txt").write_text("DesktopNote.exe\n_internal\nold-tool.exe\n")
        (app / "old-tool.exe").write_text("x")
        self.assertEqual(updates.release_files(app), ["DesktopNote.exe", "_internal", "old-tool.exe"],
                         "with a manifest: exactly what that release shipped")

    def test_from_source_it_does_not_install(self):
        self.assertIsNone(updates.app_folder())
        self.assertFalse(updates.can_install())

    def test_a_download_that_does_not_match_its_checksum_changes_nothing(self):
        folder = Path(tempfile.mkdtemp())
        (folder / "release.zip").write_bytes(b"not the real release")
        (folder / "release.zip.sha256").write_text(f"{hashlib.sha256(b'other').hexdigest()}  release.zip\n")
        app, data = Path(tempfile.mkdtemp()), Path(tempfile.mkdtemp())
        (data / "notes.json").write_text("{}", encoding="utf-8")
        updater, outcome = updates.Updater(), []
        updater.ready.connect(lambda: outcome.append("ready"))
        updater.failed.connect(outcome.append)
        updater.start(updates.Release("9.9.9", "", QUrl.fromLocalFile(str(folder / "release.zip")).toString(),
                                      QUrl.fromLocalFile(str(folder / "release.zip.sha256")).toString()), data, app)
        for _ in range(100):
            if outcome:
                break
            QTest.qWait(20)
        self.assertEqual(outcome, ["the download does not match its checksum"])
        self.assertEqual(sorted(p.name for p in data.iterdir()), ["notes.json"], "no backup, no log")
        self.assertEqual(list(app.iterdir()), [], "the app folder is untouched")

    def test_an_unfinished_update_blocks_a_new_one(self):
        app, data = Path(tempfile.mkdtemp()), Path(tempfile.mkdtemp())
        (app / updates.TRANSACTION).mkdir()
        (app / updates.TRANSACTION / updates.JOURNAL).write_text('{"state": "moving"}', encoding="utf-8")
        updater = updates.Updater()
        updater._app, updater._data, updater._pid = app, data, 0
        updater._release = updates.Release("9.9.9", "", "", "")
        journal = app / updates.TRANSACTION / updates.JOURNAL
        with self.assertRaisesRegex(OSError, "not finished"):
            updater._install(b"")
        self.assertEqual(journal.read_text(encoding="utf-8"), '{"state": "moving"}', "the earlier update is kept")


class TimeoutTest(unittest.TestCase):
    """A server that never answers ends the check and the download with a timeout."""

    def setUp(self):
        self.server = QTcpServer()
        self.assertTrue(self.server.listen(QHostAddress.LocalHost))
        self.url = f"http://127.0.0.1:{self.server.serverPort()}/"
        self._timeout, updates.TIMEOUT_MS = updates.TIMEOUT_MS, 300

    def tearDown(self):
        updates.TIMEOUT_MS = self._timeout
        self.server.close()

    def wait_for(self, outcome: list) -> None:
        for _ in range(150):
            if outcome:
                return
            QTest.qWait(20)

    def test_the_check_times_out(self):
        checker, outcome = updates.UpdateChecker("1.0.0", url=self.url), []
        checker.finished.connect(lambda release, error: outcome.append((release, error)))
        checker.check()
        self.wait_for(outcome)
        self.assertEqual(len(outcome), 1)
        self.assertIsNone(outcome[0][0])
        self.assertTrue(outcome[0][1], "it says why")
        self.assertFalse(checker.busy())

    def test_the_download_times_out(self):
        updater, outcome = updates.Updater(), []
        updater.failed.connect(outcome.append)
        release = updates.Release("9.9.9", "", self.url, self.url)
        updater.start(release, Path(tempfile.mkdtemp()), Path(tempfile.mkdtemp()))
        self.wait_for(outcome)
        self.assertEqual(len(outcome), 1)
        self.assertTrue(outcome[0], "it says why")


def local_release(folder: Path, data: bytes, checksum: str | None = None) -> updates.Release:
    """Writes data as a release zip with its checksum file into folder; returns the Release for them."""
    (folder / "release.zip").write_bytes(data)
    digest = checksum or hashlib.sha256(data).hexdigest()
    (folder / "release.zip.sha256").write_text(f"{digest}  release.zip\n")
    url = lambda name: QUrl.fromLocalFile(str(folder / name)).toString()  # noqa: E731
    return updates.Release("9.9.9", "https://example.invalid/page", url("release.zip"), url("release.zip.sha256"))


def zip_with(name: str) -> bytes:
    """Returns a zip archive holding one empty file."""
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as zf:
        zf.writestr(name, "")
    return buffer.getvalue()


class StageTest(unittest.TestCase):
    """The Updater reports verifying once the download is in, and preparing once it matches."""

    def setUp(self):
        mock.patch.object(updates, "_set_run_once").start()  # Never the real registry.
        mock.patch.object(updates.subprocess, "Popen").start()  # Never the real script.
        self.addCleanup(mock.patch.stopall)

    def run_update(self, release: updates.Release) -> list:
        app, data = Path(tempfile.mkdtemp()), Path(tempfile.mkdtemp())
        updater, outcome = updates.Updater(), []
        updater.stage.connect(lambda stage: outcome.append(("stage", stage)))
        updater.ready.connect(lambda: outcome.append(("ready",)))
        updater.failed.connect(lambda error: outcome.append(("failed", error)))
        updater.start(release, data, app)
        for _ in range(100):
            if outcome and outcome[-1][0] != "stage":
                break
            QTest.qWait(20)
        return outcome

    def test_a_checksum_mismatch_stops_while_verifying(self):
        folder = Path(tempfile.mkdtemp())
        outcome = self.run_update(local_release(folder, b"x", hashlib.sha256(b"other").hexdigest()))
        self.assertEqual(outcome, [("stage", "verifying"), ("failed", "the download does not match its checksum")])

    def test_a_bad_release_stops_while_preparing(self):
        folder = Path(tempfile.mkdtemp())
        outcome = self.run_update(local_release(folder, zip_with("readme.txt")))
        self.assertEqual(outcome, [("stage", "verifying"), ("stage", "preparing"),
                                   ("failed", "the release does not contain DesktopNote.exe")])


class FlowTest(unittest.TestCase):
    """UpdateFlow: checking only finds an update; a second act() installs it, stage by stage."""

    def setUp(self):
        self.flow = updates.UpdateFlow("1.0.0", Path(tempfile.mkdtemp()))
        self.states: list[str] = []
        self.flow.changed.connect(lambda: self.states.append(self.flow.state))
        self.found = updates.Release("1.0.1", "https://example.invalid/page", "zip", "sha")
        self.start = mock.patch.object(self.flow._updater, "start").start()
        self.addCleanup(mock.patch.stopall)

    def check(self, release=None, error=""):
        with mock.patch.object(self.flow._checker, "check",
                               side_effect=lambda: self.flow._checker.finished.emit(release, error)):
            self.flow.act()

    def test_a_found_update_waits_for_the_second_act(self):
        self.check(self.found)
        self.assertEqual(self.states, ["checking", "available"])
        self.start.assert_not_called()
        with mock.patch.object(updates, "can_install", return_value=True):
            self.flow.act()
        self.assertEqual(self.flow.state, "downloading")
        self.start.assert_called_once()

    def test_from_source_it_opens_the_release_page(self):
        self.check(self.found)
        with mock.patch.object(updates.QDesktopServices, "openUrl") as open_url:
            self.flow.act()
        open_url.assert_called_once()
        self.assertEqual(self.flow.state, "available")
        self.start.assert_not_called()

    def test_no_newer_release_is_up_to_date(self):
        self.check(None)
        self.assertEqual(self.states, ["checking", "current"])

    def test_a_failed_check_offers_another_try(self):
        self.check(None, "offline")
        self.assertEqual((self.flow.state, self.flow.error), ("failed", "offline"))

    def test_the_install_goes_through_each_stage(self):
        quits = []
        self.flow.quit_requested.connect(lambda: quits.append(True))
        self.check(self.found)
        with mock.patch.object(updates, "can_install", return_value=True):
            self.flow.act()
        self.flow._updater.stage.emit("verifying")
        self.flow._updater.stage.emit("preparing")
        self.flow._updater.ready.emit()
        self.assertEqual(self.states, ["checking", "available", "downloading", "verifying", "preparing", "installing"])
        self.assertEqual(quits, [True], "without an About window shown, the app quits at once")

    def test_a_failure_in_any_stage_offers_another_try(self):
        for stages in ([], ["verifying"], ["verifying", "preparing"]):
            with self.subTest(failed_after=stages):
                self.check(self.found)
                with mock.patch.object(updates, "can_install", return_value=True):
                    self.flow.act()
                for stage in stages:
                    self.flow._updater.stage.emit(stage)
                self.flow._updater.failed.emit("broken")
                self.assertEqual((self.flow.state, self.flow.error), ("failed", "broken"))

    def test_before_quit_can_hold_the_quit(self):
        quits, held = [], []
        self.flow.quit_requested.connect(lambda: quits.append(True))
        self.flow.before_quit = lambda quit: held.append(quit) or True
        self.flow._updater.ready.emit()
        self.assertEqual((self.flow.state, quits), ("installing", []))
        held[0]()
        held[0]()
        self.assertEqual(quits, [True], "quit_requested is emitted once")


class WorkerTest(unittest.TestCase):
    """The worker thread reports every failure once, cleans up before the hand-off, and keeps a started update."""

    def setUp(self):
        self.app, self.data = Path(tempfile.mkdtemp()), Path(tempfile.mkdtemp())
        (self.data / "notes.json").write_text("{}", encoding="utf-8")
        self.run_once = mock.patch.object(updates, "_set_run_once").start()  # Never the real registry.
        self.popen = mock.patch.object(updates.subprocess, "Popen").start()  # Never the real script.
        self.addCleanup(mock.patch.stopall)

    def run_update(self, data: bytes, during=None) -> list:
        """Runs the Updater on data published as a release.

        Args:
            data: The release zip's content.
            during: A context manager in force while the update runs, after the release is written.

        Returns:
            What the Updater emitted, in order: ("stage", name), ("ready",) or ("failed", error).
        """
        release = local_release(Path(tempfile.mkdtemp()), data)
        updater, outcome = updates.Updater(), []
        updater.stage.connect(lambda stage: outcome.append(("stage", stage)))
        updater.ready.connect(lambda: outcome.append(("ready",)))
        updater.failed.connect(lambda error: outcome.append(("failed", error)))
        with during or contextlib.nullcontext():
            updater.start(release, self.data, self.app, pid=0)
            for _ in range(150):
                if outcome and outcome[-1][0] != "stage":
                    break
                QTest.qWait(20)
            QTest.qWait(100)  # Anything emitted late would show up here.
        return outcome

    def assert_cleaned_up(self):
        """Asserts that no update folder, journal, notes backup or recovery entry is left."""
        self.assertFalse((self.app / updates.TRANSACTION).exists(), "no update folder or journal")
        self.assertFalse((self.data / updates.NOTES_BACKUP).exists(), "no notes backup")
        self.assertEqual(self.run_once.call_args_list[-1], mock.call(*updates.RUN_ONCE, None), "no recovery entry")

    def test_an_unexpected_error_while_checking_fails_once(self):
        with self.assertLogs("desktopnote.updates", "ERROR") as logged:
            outcome = self.run_update(zip_with(f"{updates.APP_FOLDER}/DesktopNote.exe"),
                                      mock.patch.object(updates, "_sha256", side_effect=RuntimeError("boom")))
        self.assertEqual(outcome, [("stage", "verifying"), ("failed", "the update could not be prepared")])
        self.assertIn("Traceback", logged.output[0])
        self.assertIn("boom", logged.output[0])

    def test_an_unexpected_error_while_preparing_fails_once_and_cleans_up(self):
        with mock.patch.object(updates, "prepare", side_effect=ValueError("boom")), \
                self.assertLogs("desktopnote.updates", "ERROR"):
            outcome = self.run_update(zip_with(f"{updates.APP_FOLDER}/DesktopNote.exe"))
        self.assertEqual(outcome, [("stage", "verifying"), ("stage", "preparing"),
                                   ("failed", "the update could not be prepared")])
        self.assert_cleaned_up()
        self.popen.assert_not_called()

    def test_a_script_that_cannot_start_leaves_nothing_behind(self):
        self.popen.side_effect = RuntimeError("cannot start")
        with self.assertLogs("desktopnote.updates", "ERROR"):
            outcome = self.run_update(zip_with(f"{updates.APP_FOLDER}/DesktopNote.exe"))
        self.assertEqual(outcome[-1], ("failed", "the update could not be prepared"))
        self.assertEqual([kind for kind, *_ in outcome].count("failed"), 1)
        self.assert_cleaned_up()

    def test_a_started_update_keeps_its_transaction(self):
        outcome = self.run_update(zip_with(f"{updates.APP_FOLDER}/DesktopNote.exe"))
        self.assertEqual(outcome, [("stage", "verifying"), ("stage", "preparing"), ("ready",)])
        tx = self.app / updates.TRANSACTION
        self.assertTrue((tx / updates.JOURNAL).exists(), "the journal stays for the script")
        self.assertTrue((tx / updates.NEW / "DesktopNote.exe").exists(), "the new files stay")
        self.assertTrue((self.data / updates.NOTES_BACKUP).exists(), "the notes backup stays")
        self.assertIsNotNone(self.run_once.call_args_list[-1].args[2], "the recovery entry stays")
        self.popen.assert_called_once()

    def test_the_flow_hears_a_failure_on_its_own_thread(self):
        flow = updates.UpdateFlow("1.0.0", self.data)
        seen = []
        flow.changed.connect(lambda: seen.append((flow.state, threading.current_thread() is threading.main_thread())))
        flow.release = local_release(Path(tempfile.mkdtemp()), zip_with(f"{updates.APP_FOLDER}/DesktopNote.exe"))
        flow.state = "available"
        with mock.patch.object(updates, "can_install", return_value=True), \
                mock.patch.object(updates, "app_folder", return_value=self.app), \
                mock.patch.object(updates, "prepare", side_effect=ValueError("boom")), \
                self.assertLogs("desktopnote.updates", "ERROR"):
            flow.act()
            for _ in range(150):
                if flow.state == "failed":
                    break
                QTest.qWait(20)
            QTest.qWait(100)
        self.assertEqual([state for state, _ in seen], ["downloading", "verifying", "preparing", "failed"])
        self.assertTrue(all(on_gui for _, on_gui in seen), "every state change runs on the GUI thread")


if __name__ == "__main__":
    unittest.main()
