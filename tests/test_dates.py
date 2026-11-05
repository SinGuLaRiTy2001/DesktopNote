"""Date tests: reminder states, due dates and durations in both languages, half-hour steps."""
import sys
import unittest
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from desktopnote.dates import format_due, half_hour_step, humanize_delta, reminder_state  # noqa: E402
from desktopnote.models import Item  # noqa: E402
from tests import _support  # noqa: E402

NOW = datetime(2026, 10, 21, 12, 0)


class ReminderStateTest(unittest.TestCase):
    """A reminder's state against a fixed clock and a 60-minute "soon" window."""

    def test_states(self):
        for fields, state in (({}, "none"), ({"due": "2026-10-21T15:00"}, "normal"),
                              ({"due": "2026-10-21T12:30"}, "soon"), ({"due": "2026-10-21T11:59"}, "overdue"),
                              ({"due": "2026-10-21T11:59", "done": True}, "done")):
            with self.subTest(fields=fields):
                self.assertEqual(reminder_state(Item(type="reminder", **fields), 60, NOW), state)


class FormatTest(unittest.TestCase):
    """Due dates and durations read naturally in each language."""

    DUES = ((datetime(2026, 10, 21, 18, 5), "今天 18:05", "Today 18:05"),
            (datetime(2026, 10, 22, 9, 0), "明天 09:00", "Tomorrow 09:00"),
            (datetime(2026, 12, 1, 9, 0), "12月1日 09:00", "Dec 1, 09:00"),
            (datetime(2027, 1, 2, 9, 0), "2027年1月2日 09:00", "Jan 2, 2027, 09:00"))
    DURATIONS = ((timedelta(minutes=25), "25 分钟", "25 min"), (timedelta(minutes=90), "1 小时 30 分钟", "1 h 30 min"))

    def test_due_dates(self):
        for language, column in (("zh", 1), ("en", 2)):
            _support.use_language(self, language)
            for row in self.DUES:
                with self.subTest(language=language, due=row[0]):
                    self.assertEqual(format_due(row[0], NOW), row[column])

    def test_durations(self):
        for language, column in (("zh", 1), ("en", 2)):
            _support.use_language(self, language)
            for row in self.DURATIONS:
                with self.subTest(language=language, delta=row[0]):
                    self.assertEqual(humanize_delta(row[0]), row[column])


class HalfHourStepTest(unittest.TestCase):
    """A time of day steps to the next or previous half hour, and wraps across midnight."""

    def test_steps(self):
        for start, steps, expected in (("10:30", 1, "11:00"), ("11:00", 1, "11:30"), ("10:40", 1, "11:00"),
                                       ("10:40", -1, "10:30"), ("10:30", -1, "10:00"), ("10:30", 3, "12:00"),
                                       ("23:30", 1, "+00:00"), ("00:00", -1, "-23:30")):
            with self.subTest(start=start, steps=steps):
                hours, minutes = map(int, start.split(":"))
                days, mins = half_hour_step(hours * 60 + minutes, steps)
                day = "+" if days > 0 else "-" if days < 0 else ""
                self.assertEqual(f"{day}{mins // 60:02d}:{mins % 60:02d}", expected)


if __name__ == "__main__":
    unittest.main()
