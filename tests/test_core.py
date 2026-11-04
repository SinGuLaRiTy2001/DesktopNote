"""Unit tests for the non-GUI core: reminders, dates, notes, i18n, storage, snapping and docking.

Run from the repository root with ``python -m unittest discover tests``.
"""
import os
import sys
import tempfile
import unittest
from ctypes import wintypes
from datetime import datetime, timedelta
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from PySide6.QtCore import QPoint, QRect  # noqa: E402

from desktopnote import i18n, snapping  # noqa: E402
from desktopnote.dates import format_due, half_hour_step, humanize_delta, reminder_state  # noqa: E402
from desktopnote.docking import hidden_depth, reveal_zone, revealed_rect, shift_into  # noqa: E402
from desktopnote.instance import claim  # noqa: E402
from desktopnote.models import Item, Note  # noqa: E402
from desktopnote.snapping import EdgeSnapper, Snapper  # noqa: E402
from desktopnote.storage import Store  # noqa: E402


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
    """A dragged note snaps next to a nearby note, edges aligned, and comes free when pulled."""

    OTHER = (100, 100, 300, 400)  # (left, top, right, bottom); snapping to its right is (308, 100).

    def setUp(self):
        self.snapper = Snapper(distance=12, release=30, gap=8)

    def move(self, left, top, width=200, height=200, others=None):
        return self.snapper.update((left, top, left + width, top + height), others or [self.OTHER])

    def test_snaps_to_the_right_and_aligns_top(self):
        self.assertEqual(self.move(318, 110), (308, 100))

    def test_snaps_to_the_left(self):
        self.assertEqual(self.move(-102, 90), (-108, 100))

    def test_snaps_below_aligned_left_or_right(self):
        self.assertEqual(self.move(110, 420), (100, 408))  # Top-left near its bottom-left.
        self.snapper.held = None
        self.assertEqual(self.move(115, 400, width=180), (120, 408))  # Top-right near its bottom-right.

    def test_snaps_beside_with_bottom_edges_aligned(self):
        self.assertEqual(self.move(315, 210), (308, 200))  # Bottom-left near its bottom-right.
        self.snapper.held = None
        self.assertEqual(self.move(-110, 195), (-108, 200))  # Bottom-right near its bottom-left.

    def test_snaps_above(self):
        self.assertEqual(self.move(104, -100, height=180), (100, -88))

    def test_needs_both_directions_close(self):
        self.assertIsNone(self.move(330, 100))  # Too far sideways.
        self.assertIsNone(self.move(308, 120))  # Too far up or down.

    def test_nearest_note_wins(self):
        others = [self.OTHER, (520, 110, 700, 300)]
        self.assertEqual(self.move(311, 108, others=others), (312, 110))

    def test_aligned_edge_limits_the_places(self):
        # Hidden at the right screen edge: only above or below it, right edges aligned.
        self.assertIsNone(self.snapper.update((310, 100, 510, 300), [self.OTHER], aligned=["right"]))  # Beside.
        narrow = (115, 410, 295, 610)  # 180 px wide, nearer the right-aligned place (120, 408).
        self.assertEqual(self.snapper.update(narrow, [self.OTHER], aligned=["right"]), (120, 408))
        self.snapper.held = None
        self.assertIsNone(self.snapper.update(narrow, [self.OTHER], aligned=["left"]))  # (100, 408) is 15 px off.

    def test_reports_the_note_snapped_to(self):
        others = [(900, 900, 1000, 1000), self.OTHER]
        self.move(310, 100, others=others)
        self.assertEqual(self.snapper.held_other, 1)
        self.move(400, 100, others=others)  # Pulled free.
        self.assertIsNone(self.snapper.held_other)

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


class SnapMoveTest(unittest.TestCase):
    """A dragged window keeps the size Windows proposes, e.g. after moving to another scale."""

    def test_the_proposed_size_is_kept(self):
        from PySide6.QtWidgets import QApplication, QWidget

        _app = QApplication.instance() or QApplication(sys.argv)
        window = QWidget()
        cursor = [(100, 10)]
        with (mock.patch.object(snapping.win_effects, "window_rect", return_value=(0, 0, 300, 340)),
              mock.patch.object(snapping.win_effects, "cursor_pos", side_effect=lambda: cursor[0]),
              mock.patch.object(snapping.win_effects, "monitors", return_value=[])):
            move = snapping.SnapMove(window)
            move.begin()
            cursor[0] = (110, 20)
            rect = wintypes.RECT(10, 10, 460, 520)  # 1.5 times the size it started with.
            move.place(rect, [])
        self.assertEqual((rect.left, rect.top, rect.right, rect.bottom), (10, 10, 460, 520))


class EdgeSnapperTest(unittest.TestCase):
    """A dragged note sticks to monitor edges, hides when pushed in and crosses to the next monitor."""

    # Two monitors side by side, the left one with a taskbar 40 px high.
    LEFT, RIGHT = (0, 0, 1920, 1080), (1920, 0, 3840, 1080)
    MONITORS = [(LEFT, (0, 0, 1920, 1040)), (RIGHT, RIGHT)]
    SIZE, GRAB = (300, 200), (150, 10)  # The note, and where the cursor holds it.

    def setUp(self):
        self.edges = EdgeSnapper(distance=32, release=72, push=40, cross=112, unpush=12)

    def move(self, left, top, cursor=None):
        """Drags the note's top-left to (left, top); returns its place and the edges in play."""
        width, height = self.SIZE
        if cursor is None:
            cursor = (min(left + self.GRAB[0], 3839), max(top + self.GRAB[1], 0))
        place = self.edges.update((left, top, left + width, top + height), self.MONITORS, cursor)
        return place, {stick.edge for stick in self.edges.stuck.values()}, self.edges.pushed

    def test_sticks_flush_when_close(self):
        self.assertEqual(self.move(1600, 500), ((1620, None), {"right"}, None))  # 20 px short.
        self.assertEqual(self.move(1580, 500), ((1620, None), {"right"}, None))  # Held.
        self.assertEqual(self.move(1540, 500), ((None, None), set(), None))  # Pulled 80 px back.

    def test_sticks_to_the_taskbar_edge_and_to_a_corner(self):
        self.assertEqual(self.move(20, 830)[:2], ((0, 840), {"left", "bottom"}))

    def test_push_into_an_edge_and_pull_back_ends_it(self):
        self.move(1610, 500)
        self.assertEqual(self.move(1650, 500), ((1620, None), {"right"}, None))  # 30 px past.
        self.assertEqual(self.move(1660, 500), ((1620, None), {"right"}, "right"))  # 40 px past.
        self.assertEqual(self.move(1650, 500)[2], "right")  # Pulled back less than 12 px.
        self.assertEqual(self.move(1640, 500), ((1620, None), {"right"}, None))

    def test_cursor_at_the_end_of_the_screen_pushes(self):
        self.move(500, 20)
        self.assertEqual(self.move(500, -5, cursor=(650, 5)), ((None, 0), {"top"}, None))
        self.assertEqual(self.move(500, -10, cursor=(650, 0)), ((None, 0), {"top"}, "top"))

    def test_outer_edge_holds_however_far_pushed(self):
        self.move(-20, 500, cursor=(130, 510))
        self.assertEqual(self.move(-150, 500, cursor=(0, 510)), ((0, None), {"left"}, "left"))

    def test_shared_edge_crosses_and_does_not_catch_again(self):
        self.move(1610, 500)
        self.assertEqual(self.move(1731, 500)[1:], ({"right"}, "right"))  # 111 px past.
        self.assertEqual(self.move(1732, 500), ((None, None), set(), None))  # 112 px past: crossed.
        self.assertEqual(self.move(1900, 500), ((None, None), set(), None))  # The next left edge lets it by.
        self.move(1960, 500)  # Clear of the edge.
        self.assertEqual(self.move(1930, 500)[:2], ((1920, None), {"left"}))  # Now it catches.

    def test_fast_move_past_an_edge_sticks(self):
        self.move(1500, 500)
        self.assertEqual(self.move(1660, 500)[1:], ({"right"}, "right"))  # 40 px past in one move.

    def test_bottom_edge_lets_go_when_pushed(self):
        self.move(500, 830)
        self.assertEqual(self.move(500, 900)[1], {"bottom"})
        self.assertEqual(self.move(500, 920)[1], set())  # 80 px past the taskbar edge.


class DockingTest(unittest.TestCase):
    """A note pushed into a screen edge hides there, peeking out."""

    AREA = QRect(0, 0, 1920, 1040)  # A screen's available area.

    def test_revealed_rect_is_flush_and_on_screen(self):
        self.assertEqual(revealed_rect(QRect(1700, -30, 300, 200), self.AREA, "right"), QRect(1620, 0, 300, 200))
        self.assertEqual(revealed_rect(QRect(-80, 300, 300, 200), self.AREA, "left"), QRect(0, 300, 300, 200))

    def test_hidden_note_peeks_out(self):
        revealed = QRect(1620, 300, 300, 200)
        self.assertEqual(hidden_depth(revealed, "right"), 288)
        self.assertEqual(hidden_depth(revealed, "top"), 188)

    def test_note_slides_into_its_edge(self):
        self.assertEqual(shift_into("right", 288), QPoint(288, 0))
        self.assertEqual(shift_into("left", 288), QPoint(-288, 0))
        self.assertEqual(shift_into("top", 188), QPoint(0, -188))

    def test_reveal_zone_runs_along_the_note_at_the_edge(self):
        zone = reveal_zone(QRect(1620, 300, 300, 200), self.AREA, "right")
        self.assertTrue(zone.contains(QPoint(1919, 400)))
        self.assertFalse(zone.contains(QPoint(1919, 520)))  # Below the note.
        self.assertFalse(zone.contains(QPoint(1900, 400)))  # Too far from the edge.


class DockPersistenceTest(unittest.TestCase):
    """A note hidden at a screen edge is saved and loaded with its edge and its shown position."""

    def test_dock_round_trip(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "notes.json"
            store = Store(path)
            store.notes = [Note(x=2260, y=300, dock="right"), Note(x=600, y=500)]
            store.save()
            loaded = Store(path)
            loaded.load()
            self.assertEqual([(n.dock, n.x, n.y) for n in loaded.notes], [("right", 2260, 300), ("", 600, 500)])


class SingleInstanceTest(unittest.TestCase):
    """Only one process claims an instance; others hand over to it."""

    NAME = "DesktopNote-test-single-instance"

    def test_first_claim_wins_and_others_hand_over(self):
        import subprocess
        import time
        start = time.perf_counter()
        self.assertTrue(claim(self.NAME))
        self.assertLess(time.perf_counter() - start, 0.2)  # A first launch is not delayed.
        self.assertTrue(claim(self.NAME))  # Claiming again in the same process keeps it.
        other = subprocess.run([sys.executable, "-c", "import sys; sys.path.insert(0, sys.argv[1]); "
                                "from desktopnote.instance import claim; sys.exit(0 if claim(sys.argv[2], 0.2) else 1)",
                                str(Path(__file__).resolve().parents[1]), self.NAME], timeout=30)
        self.assertEqual(other.returncode, 1)  # Another process does not get it.


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
