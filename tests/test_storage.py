"""Storage tests: unreadable files are kept, bad values do not stop loading, saves are robust."""
import json
import os
import subprocess
import sys
import tempfile
import unittest
from datetime import date, timedelta
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from desktopnote import storage  # noqa: E402
from desktopnote.models import Item, Note, Settings  # noqa: E402
from desktopnote.storage import Store  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
GOOD = {"version": 1, "settings": {"first_run": False, "language": "en"},
        "notes": [{"title": "Keep", "items": [{"type": "todo", "text": "Milk"}]}]}


class StorageTestCase(unittest.TestCase):
    """Gives each test an empty data folder."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.folder = Path(self._tmp.name)
        self.path = self.folder / "notes.json"

    def tearDown(self):
        self._tmp.cleanup()

    def write(self, content, name="notes.json"):
        (self.folder / name).write_text(content if isinstance(content, str) else json.dumps(content),
                                        encoding="utf-8")

    def load(self) -> Store:
        store = Store(self.path)
        store.load()
        return store

    def unreadable(self) -> list[str]:
        return sorted(p.name for p in self.folder.glob("notes-unreadable-*.json"))


class UnreadableFileTest(StorageTestCase):
    """A file that cannot be read is moved aside and never overwritten."""

    def test_wrong_shapes_fall_back_to_the_backup(self):
        for content in ("null", "[]", '"text"', '{"notes": {"a": 1}}', "{ broken"):
            with self.subTest(content=content):
                for path in self.folder.iterdir():
                    path.unlink()
                self.write(content)
                self.write(GOOD, "notes.json.bak")
                store = self.load()
                self.assertTrue(store.from_backup)
                self.assertEqual([n.title for n in store.notes], ["Keep"])
                self.assertEqual(len(self.unreadable()), 1)
                self.assertEqual((self.folder / self.unreadable()[0]).read_text(encoding="utf-8"), content)

    def test_two_unreadable_files_are_kept_and_it_is_no_first_run(self):
        self.write("{ broken")
        self.write("[]", "notes.json.bak")
        store = self.load()
        self.assertEqual(store.notes, [])
        self.assertFalse(store.settings.first_run, "no welcome note over lost notes")
        self.assertEqual(len(store.set_aside), 2)
        store.save()
        store.save()
        kept = sorted((self.folder / name).read_text(encoding="utf-8") for name in self.unreadable())
        self.assertEqual(kept, ["[]", "{ broken"])

    def test_a_file_that_cannot_be_moved_is_never_overwritten(self):
        self.write("{ broken")
        with mock.patch.object(storage.os, "replace", side_effect=PermissionError("in use")):
            store = self.load()
            with self.assertRaises(OSError):
                store.save()
        self.assertEqual(self.path.read_text(encoding="utf-8"), "{ broken")
        store.save()  # Once it can be moved, saving works again.
        self.assertEqual(len(self.unreadable()), 1)
        self.assertEqual(json.loads(self.path.read_text(encoding="utf-8"))["notes"], [])


class InvalidValueTest(StorageTestCase):
    """Values of the wrong type take the field's default; the rest of the note loads."""

    def test_bad_values_keep_their_defaults(self):
        note = Note.from_dict({"x": "oops", "y": 40, "w": True, "pinned": 1, "title": None,
                               "items": [{"type": "todo", "done": "no", "text": "a"}, 7, {"type": "bogus"}]})
        self.assertEqual((note.x, note.y, note.w, note.pinned, note.title), (200, 40, 300, False, ""))
        self.assertEqual([(i.type, i.done, i.text) for i in note.items], [("todo", False, "a"), ("text", False, "")])
        self.assertEqual(Note.from_dict({"items": "x"}).items, [])
        self.assertEqual(Settings.from_dict({"text_scale": 1, "language": 5}).text_scale, 1.0)

    def test_bad_entries_skip_only_themselves(self):
        self.write({"settings": "x", "notes": [{"title": "A"}, 3, {"title": "B"}]})
        store = self.load()
        self.assertEqual([n.title for n in store.notes], ["A", "B"])
        self.assertEqual(store.settings, Settings())


class NewerVersionTest(StorageTestCase):
    """What a newer DesktopNote wrote survives a save by this one."""

    def test_unknown_keys_and_the_version_are_kept(self):
        self.write({"version": 9, "sync": {"on": True}, "settings": {"first_run": False, "font": "x"},
                    "notes": [{"title": "T", "attachments": ["a.png"], "items": [{"text": "i", "priority": 3}]}]})
        store = self.load()
        store.notes[0].items[0].text = "changed"
        store.save()
        raw = json.loads(self.path.read_text(encoding="utf-8"))
        self.assertEqual((raw["version"], raw["sync"], raw["settings"]["font"]), (9, {"on": True}, "x"))
        self.assertEqual(raw["notes"][0]["attachments"], ["a.png"])
        self.assertEqual(raw["notes"][0]["items"][0], {**raw["notes"][0]["items"][0], "text": "changed", "priority": 3})
        self.assertNotIn("extra", raw["notes"][0])


class SaveTest(StorageTestCase):
    """Saving keeps backups, survives a busy file and leaves no temporary files."""

    def test_backups_and_daily_snapshots(self):
        store = Store(self.path)
        store.notes = [Note(title="v1")]
        store.save()
        store.notes[0].title = "v2"
        store.save()
        store.notes[0].title = "v3"
        store.save()
        self.assertIn('"v2"', store.backup_path.read_text(encoding="utf-8"))
        snapshots = list((self.folder / storage.SNAPSHOT_FOLDER).iterdir())
        self.assertEqual([p.name for p in snapshots], [f"notes-{date.today().isoformat()}.json"])
        self.assertIn('"v1"', snapshots[0].read_text(encoding="utf-8"), "the day's first state")
        self.assertEqual([p.name for p in self.folder.glob("*.tmp")], [])

    def test_only_the_latest_snapshots_are_kept(self):
        folder = self.folder / storage.SNAPSHOT_FOLDER
        folder.mkdir()
        for days in range(1, 10):
            (folder / f"notes-{(date.today() - timedelta(days=days)).isoformat()}.json").write_text("{}")
        store = Store(self.path)
        store.save()
        store.save()
        names = sorted(p.name for p in folder.iterdir())
        self.assertEqual(len(names), storage.SNAPSHOT_DAYS)
        self.assertEqual(names[-1], f"notes-{date.today().isoformat()}.json")

    def test_a_failing_backup_does_not_stop_the_save(self):
        store = Store(self.path)
        store.save()
        store.notes = [Note(title="new")]
        with mock.patch.object(storage.shutil, "copy2", side_effect=PermissionError("read-only")):
            store.save()
        self.assertIn('"new"', self.path.read_text(encoding="utf-8"))

    def test_a_busy_file_is_retried(self):
        store = Store(self.path)
        store.notes = [Note(title="saved")]
        real, calls = os.replace, []

        def busy_once(src, dst):
            calls.append(dst)
            if len(calls) == 1:
                raise PermissionError("in use")
            real(src, dst)

        with mock.patch.object(storage.os, "replace", busy_once):
            store.save()
        self.assertEqual(len(calls), 2)
        self.assertIn('"saved"', self.path.read_text(encoding="utf-8"))

    def test_a_failed_save_leaves_no_temporary_file(self):
        store = Store(self.path)
        store.notes = [Note(items=[Item(text="x")])]
        with mock.patch.object(storage.os, "replace", side_effect=PermissionError("in use")), \
                mock.patch.object(storage, "REPLACE_WAIT_S", 0):
            with self.assertRaises(PermissionError):
                store.save()
        self.assertEqual(list(self.folder.glob("*.tmp")), [])


class ConcurrentCommandLineTest(unittest.TestCase):
    """Command lines run at the same time on the data file take turns: every change is kept."""

    def test_parallel_adds(self):
        with tempfile.TemporaryDirectory() as home:
            env = {**os.environ, "DESKTOPNOTE_HOME": home, "PYTHONIOENCODING": "utf-8"}
            procs = [subprocess.Popen([sys.executable, "-m", "desktopnote.cli", "add", "todo", f"item {i}"], cwd=ROOT,
                                      env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, encoding="utf-8")
                     for i in range(8)]
            outputs = [proc.communicate(timeout=120) for proc in procs]
            for out, err in outputs:
                self.assertTrue(json.loads(out)["ok"], err)
            notes = json.loads((Path(home) / "notes.json").read_text(encoding="utf-8"))["notes"]
            self.assertEqual(len(notes), 1, "one Inbox, not one per command")
            self.assertEqual(sorted(i["text"] for i in notes[0]["items"]), sorted(f"item {i}" for i in range(8)))


if __name__ == "__main__":
    unittest.main()
