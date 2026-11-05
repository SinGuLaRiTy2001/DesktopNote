"""Command-line tests: agents and scripts look at notes, make notes, and add, change or delete items.

The commands run against a temporary data folder (DESKTOPNOTE_HOME), never the user's notes.
"""
import getpass
import json
import os
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ["DESKTOPNOTE_HOME"] = tempfile.mkdtemp(prefix="desktopnote-cli-test-")  # Before desktopnote loads.

from PySide6.QtNetwork import QLocalServer  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from desktopnote import commands, instance  # noqa: E402
from desktopnote.models import Item, Note  # noqa: E402
from desktopnote.storage import Store  # noqa: E402

_app = QApplication.instance() or QApplication(sys.argv)  # A QApplication, as the widget tests need.
_app.setQuitOnLastWindowClosed(False)


def cli(*args: str) -> tuple[int, dict]:
    """Runs the command line in its own process, as an agent would, and returns (exit code, JSON)."""
    done = subprocess.run([sys.executable, "-m", "desktopnote.cli", *args], cwd=ROOT, capture_output=True,
                          text=True, encoding="utf-8", env={**os.environ, "PYTHONIOENCODING": "utf-8"})
    return done.returncode, json.loads(done.stdout) if done.stdout.strip() else {}


class CommandsTest(unittest.TestCase):
    """The operations themselves, on plain notes."""

    def test_add_goes_to_the_named_note_or_creates_it(self):
        notes = [Note(title="Work", items=[Item(type="todo", text="A")])]
        result, change = commands.run({"command": "add", "kind": "todo", "text": "B", "note": "work"}, notes)
        self.assertEqual([i.text for i in notes[0].items], ["A", "B"], "titles match case-insensitively")
        self.assertFalse(change.new_note)
        result, change = commands.run({"command": "add", "kind": "text", "text": "C"}, notes)
        self.assertTrue(change.new_note)
        self.assertEqual(notes[1].title, commands.DEFAULT_NOTE)
        self.assertEqual(result["item"]["text"], "C")

    def test_reminders_take_a_due_time(self):
        notes = []
        result, _ = commands.run({"command": "add", "kind": "reminder", "text": "Dentist",
                                  "due": "2026-11-05 09:00"}, notes)
        self.assertEqual(result["item"]["due"], "2026-11-05T09:00")
        for bad in ({"kind": "reminder", "text": "x", "due": "tomorrow"},
                    {"kind": "todo", "text": "x", "due": "2026-11-05 09:00"},
                    {"kind": "todo", "text": "  "}, {"kind": "list-item", "text": "x"}):
            with self.subTest(request=bad), self.assertRaises(commands.CommandError):
                commands.run({"command": "add", **bad}, notes)

    def test_done_ticks_a_todo_or_reminder_by_id(self):
        todo, text = Item(type="todo", text="A"), Item(type="text", text="B")
        notes = [Note(items=[todo, text])]
        commands.run({"command": "done", "item": todo.id}, notes)
        self.assertTrue(todo.done)
        with self.assertRaises(commands.CommandError):
            commands.run({"command": "done", "item": text.id}, notes)
        with self.assertRaises(commands.CommandError):
            commands.run({"command": "done", "item": "nope"}, notes)

    def test_delete_removes_exactly_one_item_by_id(self):
        a, b = Item(type="todo", text="A"), Item(type="text", text="B")
        notes = [Note(title="Work", items=[a, b])]
        result, change = commands.run({"command": "delete", "item": b.id}, notes)
        self.assertEqual(notes[0].items, [a])
        self.assertEqual((change.action, result["deleted"]["text"]), ("delete", "B"))
        self.assertEqual(len(notes), 1, "the note stays, even when it becomes empty")
        with self.assertRaises(commands.CommandError):
            commands.run({"command": "delete", "item": b.id}, notes)


class NotesAndEditsTest(unittest.TestCase):
    """Looking at notes, making notes, naming notes by id, and changing items in place."""

    def setUp(self):
        self.todo = Item(type="todo", text="A")
        self.reminder = Item(type="reminder", text="R", due="2026-11-05T09:00")
        self.untitled = Note(items=[Item(type="text", text="First line")])
        self.notes = [Note(title="Work", items=[self.todo, self.reminder]), self.untitled]

    def test_notes_gives_an_overview(self):
        result, change = commands.run({"command": "notes"}, self.notes)
        self.assertIsNone(change)
        work, untitled = result["notes"]
        self.assertEqual((work["title"], work["item_count"], work["open_count"]), ("Work", 2, 2))
        self.assertEqual((untitled["title"], untitled["label"]), ("", "First line"))
        self.assertNotIn("items", work, "the overview leaves the items out")

    def test_show_names_a_note_by_id_or_title(self):
        by_title, _ = commands.run({"command": "show", "note": "work"}, self.notes)
        by_id, _ = commands.run({"command": "show", "note": self.untitled.id}, self.notes)
        self.assertEqual(by_title["note"]["items"][0]["text"], "A")
        self.assertEqual(by_id["note"]["label"], "First line")
        with self.assertRaises(commands.CommandError):
            commands.run({"command": "show", "note": "nope"}, self.notes)

    def test_a_shared_title_asks_for_the_id(self):
        self.notes.append(Note(title="Work"))
        with self.assertRaises(commands.CommandError) as caught:
            commands.run({"command": "add", "kind": "todo", "text": "x", "note": "Work"}, self.notes)
        self.assertIn("id", str(caught.exception))

    def test_new_notes_with_or_without_a_title(self):
        titled, change = commands.run({"command": "new-note", "title": "Ideas"}, self.notes)
        untitled, _ = commands.run({"command": "new-note"}, self.notes)
        self.assertEqual((titled["note"]["title"], change.action, change.new_note), ("Ideas", "new-note", True))
        self.assertEqual(untitled["note"]["title"], "")
        added, change = commands.run({"command": "add", "kind": "todo", "text": "x", "note": untitled["note"]["id"]},
                                     self.notes)
        self.assertFalse(change.new_note, "an untitled note is found by its id")
        self.assertEqual(self.notes[-1].items[0].text, "x")

    def test_edit_changes_an_item_in_place(self):
        commands.run({"command": "edit", "item": self.todo.id, "text": "A2", "done": True}, self.notes)
        self.assertEqual((self.todo.text, self.todo.done, self.notes[0].items[0]), ("A2", True, self.todo))
        commands.run({"command": "edit", "item": self.todo.id, "done": False}, self.notes)
        self.assertFalse(self.todo.done)
        self.reminder.notified = True
        commands.run({"command": "edit", "item": self.reminder.id, "due": "2026-11-07 12:00"}, self.notes)
        self.assertEqual((self.reminder.due, self.reminder.notified), ("2026-11-07T12:00", False))
        commands.run({"command": "edit", "item": self.reminder.id, "clear_due": True}, self.notes)
        self.assertIsNone(self.reminder.due)

    def test_edit_refuses_what_does_not_fit(self):
        text = self.untitled.items[0]
        for bad in ({"item": self.todo.id}, {"item": self.todo.id, "due": "2026-11-07 12:00"},
                    {"item": text.id, "done": True}, {"item": self.todo.id, "text": " "},
                    {"item": "nope", "text": "x"}):
            with self.subTest(request=bad), self.assertRaises(commands.CommandError):
                commands.run({"command": "edit", **bad}, self.notes)


class OfflineTest(unittest.TestCase):
    """Without a running app, the command line works on the data file."""

    def setUp(self):
        Store().path.unlink(missing_ok=True)

    def test_add_list_done_delete(self):
        code, added = cli("add", "todo", "Reply to Anna", "--note", "Work")
        self.assertEqual((code, added["ok"], added["new_note"]), (0, True, True))
        code, _ = cli("add", "reminder", "牙医", "--due", "2026-11-05 09:00")
        self.assertEqual(code, 0)
        code, listed = cli("list")
        self.assertEqual([n["title"] for n in listed["notes"]], ["Work", "Inbox"])
        self.assertEqual(listed["notes"][1]["items"][0]["text"], "牙医")
        code, _ = cli("done", added["item"]["id"])
        store = Store()
        store.load()
        self.assertTrue(store.notes[0].items[0].done, "saved to the data file")
        code, deleted = cli("delete", added["item"]["id"])
        self.assertEqual((code, deleted["deleted"]["text"]), (0, "Reply to Anna"))
        store.load()
        self.assertEqual(store.notes[0].items, [], "deleted from the data file")

    def test_an_agent_workflow(self):
        code, made = cli("new-note")
        note_id = made["note"]["id"]
        code, added = cli("add", "todo", "Draft", "--note", note_id)
        code, _ = cli("edit", added["item"]["id"], "--text", "Draft the report", "--done")
        code, shown = cli("show", note_id)
        self.assertEqual(code, 0)
        self.assertEqual(shown["note"]["items"], [{**added["item"], "text": "Draft the report", "done": True}])
        code, overview = cli("notes")
        self.assertIn(note_id, [n["id"] for n in overview["notes"]])

    def test_errors_exit_with_1_and_say_why(self):
        for command in ("done", "delete"):
            with self.subTest(command=command):
                code, result = cli(command, "nope")
                self.assertEqual((code, result["ok"]), (1, False))
                self.assertIn("no item", result["error"])

    def test_invalid_arguments_exit_with_2(self):
        done = subprocess.run([sys.executable, "-m", "desktopnote.cli", "add", "bogus", "x"], cwd=ROOT,
                              capture_output=True, text=True)
        self.assertEqual(done.returncode, 2)


class LiveTest(unittest.TestCase):
    """With the app running, its local server answers and the app applies the change."""

    def test_the_running_app_answers(self):
        from desktopnote.app import answer

        # The temporary data folder's own instance name, never the user's everyday one.
        name = instance._server_name()
        if name == f"DesktopNote-{getpass.getuser()}":
            self.skipTest("no separate instance name for the temporary data folder")
        notes = [Note(title="Work")]
        server = QLocalServer()
        self.assertTrue(server.listen(name))
        server.newConnection.connect(lambda: answer(server.nextPendingConnection(),
                                                    lambda request: commands.run(request, notes)[0], lambda: None))
        proc = subprocess.Popen([sys.executable, "-m", "desktopnote.cli", "add", "todo", "Live", "--note", "Work"],
                                cwd=ROOT, stdout=subprocess.PIPE, text=True, encoding="utf-8")
        deadline = time.monotonic() + 15
        while proc.poll() is None and time.monotonic() < deadline:
            _app.processEvents()
            time.sleep(0.01)
        out = proc.stdout.read()
        server.close()
        self.assertEqual(proc.returncode, 0, out)
        self.assertEqual([i.text for i in notes[0].items], ["Live"], "the running app's notes got the item")
        self.assertFalse(Store().path.exists() and "Live" in Store().path.read_text(encoding="utf-8"),
                         "the command did not write the file behind the app's back")


if __name__ == "__main__":
    unittest.main()
