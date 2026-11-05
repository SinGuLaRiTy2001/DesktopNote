"""Update tests: picking the release, downloading and checking it, preparing it, and UpdateFlow.

The Updater runs on real local files; the recovery entry and the install script are mocked, so no
test writes the registry or starts PowerShell. Swapping the app files, and recovering a swap cut
short, is tested in test_update_recovery.py.
"""
import contextlib
import hashlib
import io
import sys
import threading
import unittest
import zipfile
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from PySide6.QtCore import QUrl  # noqa: E402
from PySide6.QtNetwork import QHostAddress, QTcpServer  # noqa: E402
from PySide6.QtTest import QTest  # noqa: E402

from desktopnote import updates  # noqa: E402
from tests import _support  # noqa: E402

_support.qapp()


def release(tag: str, prerelease: bool = False, draft: bool = False, assets: bool = True) -> dict:
    """Returns a release as GitHub's API lists it."""
    name = f"DesktopNote-{tag.lstrip('v')}-win64.zip"
    files = [{"name": n, "browser_download_url": f"https://example.invalid/{n}"} for n in (name, f"{name}.sha256")]
    return {"tag_name": tag, "prerelease": prerelease, "draft": draft, "html_url": f"https://example.invalid/{tag}",
            "assets": files if assets else []}


def local_release(folder: Path, data: bytes, checksum: str | None = None) -> updates.Release:
    """Writes data as a release zip with its checksum file into folder; returns the Release for them.

    Args:
        folder: Where the two files go.
        data: The zip's content.
        checksum: The published SHA-256; defaults to the one of data.
    """
    (folder / "release.zip").write_bytes(data)
    (folder / "release.zip.sha256").write_text(f"{checksum or hashlib.sha256(data).hexdigest()}  release.zip\n")
    url = lambda name: QUrl.fromLocalFile(str(folder / name)).toString()  # noqa: E731
    return updates.Release("9.9.9", "https://example.invalid/page", url("release.zip"), url("release.zip.sha256"))


def zip_with(name: str) -> bytes:
    """Returns a zip archive holding one empty file."""
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as zf:
        zf.writestr(name, "")
    return buffer.getvalue()


RELEASE_ZIP = zip_with(f"{updates.APP_FOLDER}/DesktopNote.exe")  # A release as the build packs it.


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


class InstalledFilesTest(unittest.TestCase):
    """What an update may touch: the notes only through a backup, and only the release's own files."""

    def test_the_notes_are_backed_up_not_changed(self):
        data = _support.temp_dir(self)
        (data / "notes.json").write_text('{"notes": []}', encoding="utf-8")
        backup = updates.backup_notes(data)
        self.assertEqual(backup.read_text(encoding="utf-8"), '{"notes": []}')
        self.assertEqual((data / "notes.json").read_text(encoding="utf-8"), '{"notes": []}')
        self.assertIsNone(updates.backup_notes(_support.temp_dir(self)), "no notes, no backup")

    def test_the_installed_release_files(self):
        app = _support.temp_dir(self)
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


class TimeoutTest(unittest.TestCase):
    """A server that never answers ends the check and the download with a timeout."""

    def setUp(self):
        self.server = QTcpServer()
        self.assertTrue(self.server.listen(QHostAddress.LocalHost))
        self.addCleanup(self.server.close)
        self.url = f"http://127.0.0.1:{self.server.serverPort()}/"
        patcher = mock.patch.object(updates, "TIMEOUT_MS", 300)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_the_check_times_out(self):
        checker, outcome = updates.UpdateChecker("1.0.0", url=self.url), []
        checker.finished.connect(lambda release, error: outcome.append((release, error)))
        checker.check()
        _support.wait_until(lambda: outcome)
        self.assertEqual(len(outcome), 1)
        self.assertIsNone(outcome[0][0])
        self.assertTrue(outcome[0][1], "it says why")
        self.assertFalse(checker.busy())

    def test_the_download_times_out(self):
        updater, outcome = updates.Updater(), []
        updater.failed.connect(outcome.append)
        release = updates.Release("9.9.9", "", self.url, self.url)
        updater.start(release, _support.temp_dir(self), _support.temp_dir(self))
        _support.wait_until(lambda: outcome)
        self.assertEqual(len(outcome), 1)
        self.assertTrue(outcome[0], "it says why")


class UpdaterTest(unittest.TestCase):
    """The Updater on real files: each stage in turn, every failure reported once and cleaned up
    before the install script starts, a started update kept for the script."""

    def setUp(self):
        self.app, self.data = _support.temp_dir(self), _support.temp_dir(self)
        (self.data / "notes.json").write_text("{}", encoding="utf-8")
        self.run_once = mock.patch.object(updates, "_set_run_once").start()  # Never the real registry.
        self.popen = mock.patch.object(updates.subprocess, "Popen").start()  # Never the real script.
        self.addCleanup(mock.patch.stopall)

    def run_update(self, data: bytes, checksum: str | None = None, during=None) -> list:
        """Runs the Updater on data published as a release; returns what it emitted, in order.

        Args:
            data: The release zip's content.
            checksum: The published SHA-256; defaults to the one of data.
            during: A context manager in force while the update runs, after the release is written.
        """
        release = local_release(_support.temp_dir(self), data, checksum)
        updater, outcome = updates.Updater(), []
        updater.stage.connect(lambda stage: outcome.append(("stage", stage)))
        updater.ready.connect(lambda: outcome.append(("ready",)))
        updater.failed.connect(lambda error: outcome.append(("failed", error)))
        with during or contextlib.nullcontext():
            updater.start(release, self.data, self.app, pid=0)
            _support.wait_until(lambda: outcome and outcome[-1][0] != "stage", 5000, f"stuck at {outcome}")
            QTest.qWait(100)  # Anything emitted late would show up here.
        return outcome

    def test_a_failed_update_is_reported_once_and_leaves_nothing(self):
        prepared = [("stage", "verifying"), ("stage", "preparing")]
        cases = [
            ("checksum mismatch", RELEASE_ZIP, hashlib.sha256(b"other").hexdigest(), None,
             [("stage", "verifying"), ("failed", "the download does not match its checksum")]),
            ("not a zip", b"not a zip archive", None, None, [*prepared, ("failed", "File is not a zip file")]),
            ("no DesktopNote.exe", zip_with("readme.txt"), None, None,
             [*prepared, ("failed", "the release does not contain DesktopNote.exe")]),
            ("unexpected error while checking", RELEASE_ZIP, None,
             mock.patch.object(updates, "_sha256", side_effect=RuntimeError("boom")),
             [("stage", "verifying"), ("failed", "the update could not be prepared")]),
            ("unexpected error while preparing", RELEASE_ZIP, None,
             mock.patch.object(updates, "prepare", side_effect=ValueError("boom")),
             [*prepared, ("failed", "the update could not be prepared")]),
            ("script cannot start", RELEASE_ZIP, None,
             mock.patch.object(self.popen, "side_effect", RuntimeError("boom")),
             [*prepared, ("failed", "the update could not be prepared")]),
        ]
        for name, data, checksum, during, expected in cases:
            with self.subTest(name):
                self.run_once.reset_mock()
                logs = self.assertLogs("desktopnote.updates", "ERROR") if "unexpected" in name or "script" in name \
                    else contextlib.nullcontext()
                with logs as logged:
                    outcome = self.run_update(data, checksum, during)
                self.assertEqual(outcome, expected)
                if logged is not None:
                    self.assertIn("boom", "\n".join(logged.output), "logged with its traceback")
                self.assertFalse((self.app / updates.TRANSACTION).exists(), "no update folder or journal")
                self.assertEqual(sorted(p.name for p in self.data.iterdir()), ["notes.json"], "no notes backup")
                if self.run_once.called:
                    self.assertEqual(self.run_once.call_args, mock.call(*updates.RUN_ONCE, None), "no recovery entry")

    def test_an_unfinished_update_blocks_a_new_one(self):
        journal = self.app / updates.TRANSACTION / updates.JOURNAL
        journal.parent.mkdir()
        journal.write_text('{"state": "moving"}', encoding="utf-8")
        outcome = self.run_update(RELEASE_ZIP)
        self.assertIn("not finished", outcome[-1][1])
        self.assertEqual(journal.read_text(encoding="utf-8"), '{"state": "moving"}', "the earlier update is kept")
        self.run_once.assert_not_called()

    def test_a_started_update_keeps_its_transaction(self):
        outcome = self.run_update(RELEASE_ZIP)
        self.assertEqual(outcome, [("stage", "verifying"), ("stage", "preparing"), ("ready",)])
        tx = self.app / updates.TRANSACTION
        self.assertTrue((tx / updates.JOURNAL).exists(), "the journal stays for the script")
        self.assertTrue((tx / updates.NEW / "DesktopNote.exe").exists(), "the new files stay")
        self.assertTrue((self.data / updates.NOTES_BACKUP).exists(), "the notes backup stays")
        self.assertIsNotNone(self.run_once.call_args.args[2], "the recovery entry stays")
        self.popen.assert_called_once()

    def test_the_flow_hears_the_worker_on_its_own_thread(self):
        flow = updates.UpdateFlow("1.0.0", self.data)
        seen = []
        flow.changed.connect(lambda: seen.append((flow.state, threading.current_thread() is threading.main_thread())))
        flow.release, flow.state = local_release(_support.temp_dir(self), RELEASE_ZIP), "available"
        with mock.patch.object(updates, "can_install", return_value=True), \
                mock.patch.object(updates, "app_folder", return_value=self.app), \
                mock.patch.object(updates, "prepare", side_effect=ValueError("boom")), \
                self.assertLogs("desktopnote.updates", "ERROR"):
            flow.act()
            _support.wait_until(lambda: flow.state == "failed", 5000)
            QTest.qWait(100)
        self.assertEqual([state for state, _ in seen], ["downloading", "verifying", "preparing", "failed"])
        self.assertTrue(all(on_gui for _, on_gui in seen), "every state change runs on the GUI thread")


class FlowTest(unittest.TestCase):
    """UpdateFlow: checking only finds an update; a second act() installs it, stage by stage."""

    def setUp(self):
        self.flow = updates.UpdateFlow("1.0.0", _support.temp_dir(self))
        self.states: list[str] = []
        self.flow.changed.connect(lambda: self.states.append(self.flow.state))
        self.found = updates.Release("1.0.1", "https://example.invalid/page", "zip", "sha")
        self.start = mock.patch.object(self.flow._updater, "start").start()
        self.addCleanup(mock.patch.stopall)

    def check(self, release=None, error=""):
        """Runs act() with a check that finds release, or fails with error."""
        with mock.patch.object(self.flow._checker, "check",
                               side_effect=lambda: self.flow._checker.finished.emit(release, error)):
            self.flow.act()

    def install(self):
        """Finds the update, then acts on it as a release build."""
        self.check(self.found)
        with mock.patch.object(updates, "can_install", return_value=True):
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

    def test_a_check_without_an_update(self):
        for error, state in (("", "current"), ("offline", "failed")):
            with self.subTest(error=error):
                self.states.clear()
                self.check(None, error)
                self.assertEqual((self.states, self.flow.error), (["checking", state], error))

    def test_the_install_goes_through_each_stage(self):
        quits = []
        self.flow.quit_requested.connect(lambda: quits.append(True))
        self.install()
        self.flow._updater.stage.emit("verifying")
        self.flow._updater.stage.emit("preparing")
        self.flow._updater.ready.emit()
        self.assertEqual(self.states, ["checking", "available", "downloading", "verifying", "preparing", "installing"])
        self.assertEqual(quits, [True], "without an About window shown, the app quits at once")

    def test_a_failure_in_any_stage_offers_another_try(self):
        for stages in ([], ["verifying"], ["verifying", "preparing"]):
            with self.subTest(failed_after=stages):
                self.install()
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


if __name__ == "__main__":
    unittest.main()
