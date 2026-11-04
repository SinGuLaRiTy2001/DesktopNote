"""Update tests: picking the release, checking the download, and backing up the notes.

Swapping the app files, and recovering a swap cut short, is tested in test_update_recovery.py.
"""
import hashlib
import sys
import tempfile
import unittest
from pathlib import Path

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


if __name__ == "__main__":
    unittest.main()
