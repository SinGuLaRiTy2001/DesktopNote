"""Unit tests for the non-GUI core: reminders, date formatting, notes, i18n and storage.

Run from the repository root with ``python -m unittest discover tests``.
"""
import os
import sys
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from desktopnote import i18n  # noqa: E402
from desktopnote.models import Item, Note  # noqa: E402
from desktopnote.note_window import Snapper  # noqa: E402
from desktopnote.storage import Store  # noqa: E402
from desktopnote.widgets import format_due, half_hour_step, humanize_delta, reminder_state  # noqa: E402


class ReminderStateTest(unittest.TestCase):
    """Tests reminder states and due-date formatting against a fixed clock."""

    now = datetime(2026, 10, 21, 12, 0)

    def state(self, **kw):
        """Returns the state of a reminder built from kw, with a 60-minute "soon" window."""
        return reminder_state(Item(type="reminder", **kw), 60, self.now)

    def test_states(self):
        """Covers none, normal, soon, overdue and done."""
        self.assertEqual(self.state(), "none")
        self.assertEqual(self.state(due="2026-10-21T15:00"), "normal")
        self.assertEqual(self.state(due="2026-10-21T12:30"), "soon")
        self.assertEqual(self.state(due="2026-10-21T11:59"), "overdue")
        self.assertEqual(self.state(due="2026-10-21T11:59", done=True), "done")

    def test_format(self):
        """Formats due dates and durations in Chinese (the default language)."""
        self.assertEqual(format_due(datetime(2026, 10, 21, 18, 5), self.now), "今天 18:05")
        self.assertEqual(format_due(datetime(2026, 10, 22, 9, 0), self.now), "明天 09:00")
        self.assertEqual(format_due(datetime(2026, 12, 1, 9, 0), self.now), "12月1日 09:00")
        self.assertEqual(format_due(datetime(2027, 1, 2, 9, 0), self.now), "2027年1月2日 09:00")
        self.assertEqual(humanize_delta(timedelta(minutes=25)), "25 分钟")
        self.assertEqual(humanize_delta(timedelta(minutes=90)), "1 小时 30 分钟")

    def test_format_english(self):
        """Formats due dates and durations in English."""
        i18n.set_language("en")
        try:
            self.assertEqual(format_due(datetime(2026, 10, 21, 18, 5), self.now), "Today 18:05")
            self.assertEqual(format_due(datetime(2026, 10, 22, 9, 0), self.now), "Tomorrow 09:00")
            self.assertEqual(format_due(datetime(2026, 12, 1, 9, 0), self.now), "Dec 1, 09:00")
            self.assertEqual(format_due(datetime(2027, 1, 2, 9, 0), self.now), "Jan 2, 2027, 09:00")
            self.assertEqual(humanize_delta(timedelta(minutes=90)), "1 h 30 min")
        finally:
            i18n.set_language("zh")


class HalfHourStepTest(unittest.TestCase):
    """Tests stepping a time of day by half hours."""

    def step(self, hhmm: str, steps: int) -> str:
        """Returns hhmm stepped by steps half hours, prefixed with "+" or "-" on a day change."""
        hours, minutes = map(int, hhmm.split(":"))
        days, mins = half_hour_step(hours * 60 + minutes, steps)
        return f"{'+' if days > 0 else '-' if days < 0 else ''}{mins // 60:02d}:{mins % 60:02d}"

    def test_snaps_to_half_hours(self):
        self.assertEqual(self.step("10:30", 1), "11:00")
        self.assertEqual(self.step("11:00", 1), "11:30")
        self.assertEqual(self.step("10:40", 1), "11:00")
        self.assertEqual(self.step("10:40", -1), "10:30")
        self.assertEqual(self.step("10:30", -1), "10:00")
        self.assertEqual(self.step("10:30", 3), "12:00")

    def test_wraps_across_midnight(self):
        self.assertEqual(self.step("23:30", 1), "+00:00")
        self.assertEqual(self.step("00:00", -1), "-23:30")


class NoteLabelTest(unittest.TestCase):
    """Tests Note.label()."""

    def test_title_then_first_line(self):
        """Uses the trimmed title, else the first non-empty line with collapsed spaces."""
        self.assertEqual(Note(title="  Groceries ").label(), "Groceries")
        note = Note(items=[Item(text=""), Item(text="\n  Call   dentist \nlater")])
        self.assertEqual(note.label(), "Call dentist")
        self.assertEqual(Note().label(), "")


class I18nTest(unittest.TestCase):
    """Tests the string table and language selection."""

    def test_every_string_has_both_languages(self):
        for key, (zh, en) in i18n._STRINGS.items():
            self.assertTrue(zh and en, key)

    def test_unknown_language_falls_back(self):
        """Falls back to Chinese for an unsupported language code."""
        i18n.set_language("fr")
        self.assertEqual(i18n.language(), "zh")


class StoreTest(unittest.TestCase):
    """Tests saving, loading and recovering notes.json."""

    def test_roundtrip_and_backup(self):
        """Keeps every field through a save and load, and writes a .bak on the second save."""
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "notes.json"
            store = Store(path)
            note = Note(x=-1500, y=40, w=320, h=280, tint="sky")
            note.items = [Item(type="todo", text="买牛奶", done=True), Item(type="reminder", due="2026-10-22T09:00")]
            store.notes = [note]
            store.save()
            store.save()  # The second save creates the .bak file.
            self.assertTrue(path.with_suffix(".json.bak").exists())

            loaded = Store(path)
            loaded.load()
            restored = loaded.notes[0]
            self.assertEqual((restored.x, restored.y, restored.w, restored.h, restored.tint),
                             (-1500, 40, 320, 280, "sky"))
            self.assertEqual(restored.items[0].text, "买牛奶")
            self.assertTrue(restored.items[0].done)
            self.assertEqual(restored.items[1].due_dt, datetime(2026, 10, 22, 9, 0))

    def test_legacy_tints_map_to_tags(self):
        """Maps old tint names to tag keys and keeps custom hex colours."""
        self.assertEqual(Note.from_dict({"tint": "yellow"}).tint, "lemon")
        self.assertEqual(Note.from_dict({"tint": "navy"}).tint, "lavender")  # First tag set.
        self.assertEqual(Note.from_dict({"tint": "#12ab9F"}).tint, "#12ab9F")  # Custom kept.
        self.assertEqual(Note.from_dict({"tint": "default"}).tint, "none")
        self.assertEqual(Note.from_dict({}).tint, "none")

    def test_corrupt_file_falls_back_to_backup(self):
        """Loads the .bak file when notes.json cannot be parsed."""
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "notes.json"
            store = Store(path)
            store.notes = [Note(items=[Item(text="keep me")])]
            store.save()
            store.save()
            path.write_text("{ broken", encoding="utf-8")
            loaded = Store(path)
            loaded.load()
            self.assertEqual(loaded.notes[0].items[0].text, "keep me")


class SnapperTest(unittest.TestCase):
    """A dragged note snaps beside a nearby note, top edges aligned, and comes free when pulled."""

    OTHER = (100, 100, 300, 400)  # (left, top, right, bottom); snapping to its right is (308, 100).

    def setUp(self):
        self.snapper = Snapper(distance=12, release=30, gap=8)

    def move(self, left, top, width=200, height=200, others=None):
        return self.snapper.update((left, top, left + width, top + height), others or [self.OTHER])

    def test_snaps_to_the_right_and_aligns_top(self):
        self.assertEqual(self.move(318, 110), (308, 100))

    def test_snaps_to_the_left(self):
        self.assertEqual(self.move(-102, 90), (-108, 100))

    def test_needs_both_directions_close(self):
        self.assertIsNone(self.move(330, 100))  # Too far sideways.
        self.assertIsNone(self.move(308, 120))  # Too far up or down.

    def test_nearest_note_wins(self):
        others = [self.OTHER, (520, 110, 700, 300)]
        self.assertEqual(self.move(311, 108, others=others), (312, 110))

    def test_fast_move_across_the_place_snaps(self):
        self.assertIsNone(self.move(250, 105))  # Inside the other note.
        self.assertEqual(self.move(330, 105), (308, 100))  # Passed 308 in one move.

    def test_fast_move_down_across_the_place_snaps(self):
        self.assertIsNone(self.move(310, 70))
        self.assertEqual(self.move(310, 125), (308, 100))  # Passed 100 in one move.

    def test_holds_until_pulled_either_way(self):
        self.move(310, 100)
        self.assertEqual(self.move(335, 125), (308, 100))
        self.assertIsNone(self.move(340, 100))  # Pulled sideways.
        self.move(310, 100)
        self.assertIsNone(self.move(310, 135))  # Pulled down.


class VersionTest(unittest.TestCase):
    """The package version matches the one declared in pyproject.toml."""

    def test_version_matches_pyproject(self):
        try:
            import tomllib
        except ImportError:  # Python 3.10 has no tomllib.
            self.skipTest("tomllib needs Python 3.11+")
        import desktopnote
        pyproject = Path(__file__).resolve().parents[1] / "pyproject.toml"
        declared = tomllib.loads(pyproject.read_text(encoding="utf-8"))["project"]["version"]
        self.assertEqual(desktopnote.__version__, declared)


if __name__ == "__main__":
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    unittest.main()
