"""Snapping and docking tests: notes snap next to notes and to monitor edges, and hide at an edge."""
import sys
import unittest
from ctypes import wintypes
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from PySide6.QtCore import QPoint, QRect  # noqa: E402
from PySide6.QtWidgets import QWidget  # noqa: E402

from desktopnote import snapping  # noqa: E402
from desktopnote.docking import hidden_depth, reveal_zone, revealed_rect, shift_into  # noqa: E402
from desktopnote.snapping import EdgeSnapper, Snapper  # noqa: E402
from tests import _support  # noqa: E402


class SnapperTest(unittest.TestCase):
    """A dragged note snaps next to a nearby note, edges aligned, and comes free when pulled."""

    OTHER = (100, 100, 300, 400)  # (left, top, right, bottom); snapping to its right is (308, 100).

    def setUp(self):
        self.snapper = Snapper(distance=12, release=30, gap=8)

    def move(self, left, top, width=200, height=200, others=None):
        return self.snapper.update((left, top, left + width, top + height), others or [self.OTHER])

    def test_one_move_near_a_place(self):
        cases = (("right, tops aligned", (318, 110), {}, (308, 100)),
                 ("left", (-102, 90), {}, (-108, 100)),
                 ("below, left edges aligned", (110, 420), {}, (100, 408)),
                 ("below, right edges aligned", (115, 400), {"width": 180}, (120, 408)),
                 ("right, bottoms aligned", (315, 210), {}, (308, 200)),
                 ("left, bottoms aligned", (-110, 195), {}, (-108, 200)),
                 ("above", (104, -100), {"height": 180}, (100, -88)),
                 ("too far sideways", (330, 100), {}, None),
                 ("too far up or down", (308, 120), {}, None),
                 ("the nearest note wins", (311, 108), {"others": [self.OTHER, (520, 110, 700, 300)]}, (312, 110)))
        for name, (left, top), options, expected in cases:
            with self.subTest(name):
                self.setUp()
                self.assertEqual(self.move(left, top, **options), expected)

    def test_aligned_edge_limits_the_places(self):
        # Hidden at the right screen edge: only above or below it, right edges aligned.
        narrow = (115, 410, 295, 610)  # 180 px wide, nearer the right-aligned place (120, 408).
        for aligned, rect, expected in ((["right"], (310, 100, 510, 300), None),  # Beside it.
                                        (["right"], narrow, (120, 408)),
                                        (["left"], narrow, None)):  # (100, 408) is 15 px off.
            with self.subTest(aligned=aligned, rect=rect):
                self.setUp()
                self.assertEqual(self.snapper.update(rect, [self.OTHER], aligned=aligned), expected)

    def test_reports_the_note_snapped_to(self):
        others = [(900, 900, 1000, 1000), self.OTHER]
        self.move(310, 100, others=others)
        self.assertEqual(self.snapper.held_other, 1)
        self.move(400, 100, others=others)  # Pulled free.
        self.assertIsNone(self.snapper.held_other)

    def test_a_fast_move_across_the_place_snaps(self):
        for start, end in (((250, 105), (330, 105)), ((310, 70), (310, 125))):  # Passed 308, or 100, in one move.
            with self.subTest(start=start, end=end):
                self.setUp()
                self.assertIsNone(self.move(*start))
                self.assertEqual(self.move(*end), (308, 100))

    def test_holds_until_pulled_either_way(self):
        self.move(310, 100)
        self.assertEqual(self.move(335, 125), (308, 100))
        self.assertIsNone(self.move(340, 100))  # Pulled sideways.
        self.move(310, 100)
        self.assertIsNone(self.move(310, 135))  # Pulled down.


class SnapMoveTest(unittest.TestCase):
    """A dragged window keeps the size Windows proposes, e.g. after moving to another scale."""

    def test_the_proposed_size_is_kept(self):
        _support.qapp()
        window = QWidget()
        self.addCleanup(window.deleteLater)
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


if __name__ == "__main__":
    unittest.main()
