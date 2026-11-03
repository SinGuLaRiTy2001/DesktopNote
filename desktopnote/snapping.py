"""Snap to align: a dragged note snaps next to other notes and sticks to the monitors' edges.

Snapping only happens during a drag; nothing ties the notes together afterwards. Snapper and
EdgeSnapper hold the rules; SnapMove applies them to a window's native drag.
"""
from __future__ import annotations

from typing import NamedTuple

from PySide6.QtCore import QEasingCurve, QObject

from . import motion, win_effects

DISTANCE = 32  # How close in px, both sideways and up and down, a note must come to a place.
RELEASE = 72  # How far in px, either way, a snapped note must be pulled to come free.
GAP = 4  # Space in px left between snapped notes.
GLIDE_MS = 160  # Glide into and out of a snapped place.
GLIDE_EASING = QEasingCurve.OutQuint
PUSH = 40  # How far in px past an edge a note stuck to it must be pushed to hide there.
UNPUSH = 12  # How far in px a pushed note must be pulled back to end the push.
CROSS = PUSH + RELEASE  # How far in px past an edge between two monitors a note crosses over.
HIDE_EDGES = ("left", "right", "top")  # The edges a note hides at.

Rect = tuple[int, int, int, int]  # (left, top, right, bottom), right and bottom exclusive.
Point = tuple[int, int]
_AXES = {"x": ("left", "right"), "y": ("top", "bottom")}


def places_around(other: Rect, width: int, height: int, gap: int) -> list[tuple[str, Point]]:
    """Returns the eight places where a note of the given size snaps to other.

    Beside it, to the right and to the left, the top or the bottom edges align; below and above
    it, the left or the right edges align. Every place is gap away from other.

    Returns:
        (aligned edge, (left, top)) pairs; the aligned edge is "top", "bottom", "left" or "right".
    """
    o_left, o_top, o_right, o_bottom = other
    beside = (o_right + gap, o_left - gap - width)
    level = (("top", o_top), ("bottom", o_bottom - height))
    stacked = (o_bottom + gap, o_top - gap - height)
    flush = (("left", o_left), ("right", o_right - width))
    return ([(edge, (x, y)) for x in beside for edge, y in level]
            + [(edge, (x, y)) for y in stacked for edge, x in flush])


class Snapper:
    """Decides, move by move, where a dragged note snaps next to another note.

    A note snaps to the nearest of the places_around() the other notes once the corners that
    meet there are within ``distance`` both sideways and up and down, or once it passes the place
    between two moves. It comes free when pulled more than ``release`` away. Values are in px.

    Attributes:
        distance: How close to a place, in both directions, the note snaps to it.
        release: How far the note must be pulled, in either direction, to come free.
        gap: The space left between snapped notes.
        held: The (left, top) the note is snapped to, or None.
        held_other: The index in ``others`` of the note it is snapped to, or None.
    """

    def __init__(self, distance: int = DISTANCE, release: int = RELEASE, gap: int = GAP):
        self.distance, self.release, self.gap = distance, release, gap
        self.held: Point | None = None
        self.held_other: int | None = None
        self._last: Point | None = None  # The note's (left, top) at the previous move.

    def update(self, rect: Rect, others: list[Rect], scale: float = 1.0,
               aligned: list[str | None] | None = None) -> Point | None:
        """Returns where the note moved to ``rect`` sits: its snapped (left, top), or None.

        Args:
            rect: Where the note would be without snapping.
            others: The other notes.
            scale: Factor applied to distance, release and gap (the display's pixel ratio).
            aligned: Per other note, the only edge ("top", "bottom", "left" or "right") a note
                snapping to it may align, or None for any.
        """
        distance, release, gap = (round(v * scale) for v in (self.distance, self.release, self.gap))
        left, top, right, bottom = rect
        # The corners that meet at a place are as far apart as the note's top-left corner is from
        # the place's, so offsets are taken from the top-left corner throughout.
        places, owners = {}, {}  # Place -> offset of the note from it, and -> index in others.
        for index, other in enumerate(others):
            only = aligned[index] if aligned else None
            for edge, place in places_around(other, right - left, bottom - top, gap):
                if only is None or edge == only:
                    places[place], owners[place] = (left - place[0], top - place[1]), index
        last, self._last = self._last, (left, top)
        if self.held is not None:
            offset = places.get(self.held)
            if offset is not None and abs(offset[0]) <= release and abs(offset[1]) <= release:
                self.held_other = owners[self.held]
                return self.held
            self.held = self.held_other = None

        def caught(place: Point, dx: int, dy: int) -> bool:
            if abs(dx) <= distance and abs(dy) <= distance:
                return True
            if last is None or abs(dx) > release or abs(dy) > release:
                return False
            passed_x = (last[0] - place[0]) * dx < 0 and abs(dy) <= distance
            passed_y = (last[1] - place[1]) * dy < 0 and abs(dx) <= distance
            return passed_x or passed_y

        near = [(dx * dx + dy * dy, place) for place, (dx, dy) in places.items() if caught(place, dx, dy)]
        if near:
            self.held = min(near)[1]
            self.held_other = owners[self.held]
        return self.held


class Stick(NamedTuple):
    """A monitor edge that a dragged note sticks to.

    Attributes:
        edge: "left", "right", "top" or "bottom".
        monitor: The monitor's rect.
        work: The monitor's work area, the side of which is the edge.
    """

    edge: str
    monitor: Rect
    work: Rect


def past_edge(rect: Rect, work: Rect, edge: str) -> int:
    """Returns how far rect reaches past an edge of work: negative while inside, 0 when flush."""
    if edge == "left":
        return work[0] - rect[0]
    if edge == "right":
        return rect[2] - work[2]
    if edge == "top":
        return work[1] - rect[1]
    return rect[3] - work[3]


def continues(monitor: Rect, edge: str, rect: Rect, monitors: list[tuple[Rect, Rect]]) -> bool:
    """Returns whether another monitor continues past an edge of monitor, alongside rect.

    Args:
        monitor: The monitor's rect.
        edge: "left", "right", "top" or "bottom".
        rect: The note, whose span along the edge counts.
        monitors: Each monitor's rect and work area.
    """
    for other, _ in monitors:
        along_y = other[1] < rect[3] and other[3] > rect[1]
        along_x = other[0] < rect[2] and other[2] > rect[0]
        if ((edge == "left" and other[2] == monitor[0] and along_y)
                or (edge == "right" and other[0] == monitor[2] and along_y)
                or (edge == "top" and other[3] == monitor[1] and along_x)
                or (edge == "bottom" and other[1] == monitor[3] and along_x)):
            return True
    return False


class EdgeSnapper:
    """Decides, move by move, whether a dragged note sticks to a monitor edge or is pushed into it.

    The edges are those of each monitor's work area, the taskbar's edge included. A note sticks
    flush to an edge once it comes within ``distance`` of it, or passes it between two moves, and
    comes free when pulled more than ``release`` back. Pushed ``push`` past a left, right or top
    edge, or with the cursor against the end of the screen there, it is pushed into that edge,
    where letting go hides it; pulled ``unpush`` back, the push ends. Pushed ``cross`` past an
    edge that another monitor continues, it crosses over, and that line does not catch it again
    until the note is clear of it. A bottom edge lets go ``release`` past it. Values are in px.

    Attributes:
        stuck: Per axis, "x" or "y", the edge the note sticks to.
        pushed: The edge the note is pushed into, or None.
    """

    def __init__(self, distance: int = DISTANCE, release: int = RELEASE, push: int = PUSH,
                 cross: int = CROSS, unpush: int = UNPUSH):
        self.distance, self.release, self.push = distance, release, push
        self.cross, self.unpush = cross, unpush
        self.stuck: dict[str, Stick] = {}
        self.pushed: str | None = None
        self._last: Rect | None = None  # The note's rect at the previous move.
        self._crossed: set[tuple[str, int]] = set()  # (axis, x or y) of edges crossed.

    def update(self, rect: Rect, monitors: list[tuple[Rect, Rect]], cursor: Point,
               scale: float = 1.0) -> tuple[int | None, int | None]:
        """Returns where the note moved to ``rect`` sits: its left and top, each None while free.

        Args:
            rect: Where the note would be without sticking.
            monitors: Each monitor's rect and work area, as from win_effects.monitors().
            cursor: The cursor position.
            scale: Factor applied to all distances (the display's pixel ratio).
        """
        distance, release, push, cross, unpush = (
            round(v * scale) for v in (self.distance, self.release, self.push, self.cross, self.unpush))
        last, self._last = self._last, rect

        def frees_at(stick: Stick) -> float:
            """Returns how far past its edge a note stuck there comes free."""
            if stick.edge not in HIDE_EDGES:
                return release
            return cross if continues(stick.monitor, stick.edge, rect, monitors) else float("inf")

        for axis, edges in _AXES.items():
            low, high = (0, 2) if axis == "x" else (1, 3)
            # A crossed edge catches the note again once it is clear of it.
            self._crossed = {(a, line) for a, line in self._crossed
                             if a != axis or rect[low] - distance <= line <= rect[high] + distance}
            stick = self.stuck.get(axis)
            if stick is not None:
                past = past_edge(rect, stick.work, stick.edge)
                if past < -release or past >= frees_at(stick):
                    del self.stuck[axis]
                    if past > 0:
                        self._crossed.add((axis, _line(stick.work, stick.edge)))
                continue
            home = _home(rect, cursor, monitors)
            if home is None:
                continue
            near = []
            for edge in edges:
                candidate = Stick(edge, *home)
                if (axis, _line(candidate.work, edge)) in self._crossed:
                    continue
                past = past_edge(rect, candidate.work, edge)
                passed = (last is not None and past_edge(last, candidate.work, edge) < -distance
                          and distance < past < frees_at(candidate))
                if abs(past) <= distance or passed:
                    near.append((abs(past), edge, candidate))
            if near:
                self.stuck[axis] = min(near)[2]

        pushed = []
        for stick in self.stuck.values():
            if stick.edge in HIDE_EDGES:
                slack = unpush if stick.edge == self.pushed else 0
                past = past_edge(rect, stick.work, stick.edge)
                if past >= push - slack or _at_end(stick, cursor, monitors, slack):
                    pushed.append((past, stick.edge != "top", stick.edge))  # Left and right win ties.
        self.pushed = max(pushed)[2] if pushed else None

        place: list[int | None] = [None, None]
        width, height = rect[2] - rect[0], rect[3] - rect[1]
        for stick in self.stuck.values():
            work, edge = stick.work, stick.edge
            if edge == "left":
                place[0] = work[0]
            elif edge == "right":
                place[0] = work[2] - width
            elif edge == "top":
                place[1] = work[1]
            else:
                place[1] = work[3] - height
        return place[0], place[1]

    def pushed_stick(self) -> Stick | None:
        """Returns the edge the note is pushed into, or None."""
        return next((stick for stick in self.stuck.values() if stick.edge == self.pushed), None)


def _line(work: Rect, edge: str) -> int:
    """Returns the x (left and right edges) or the y (top and bottom) of an edge of work."""
    return work[("left", "top", "right", "bottom").index(edge)]


def overlap(a: Rect, b: Rect) -> int:
    """Returns the area two rects share."""
    return max(0, min(a[2], b[2]) - max(a[0], b[0])) * max(0, min(a[3], b[3]) - max(a[1], b[1]))


def _contains(rect: Rect, x: int, y: int) -> bool:
    return rect[0] <= x < rect[2] and rect[1] <= y < rect[3]


def _home(rect: Rect, cursor: Point, monitors: list[tuple[Rect, Rect]]) -> tuple[Rect, Rect] | None:
    """Returns the monitor and work area showing most of rect, else the one under the cursor."""
    best = max(monitors, key=lambda monitor: overlap(rect, monitor[1]), default=None)
    if best is not None and overlap(rect, best[1]):
        return best
    return next((monitor for monitor in monitors if _contains(monitor[0], *cursor)), None)


def _at_end(stick: Stick, cursor: Point, monitors: list[tuple[Rect, Rect]], slack: int) -> bool:
    """Returns whether the cursor is against the end of the screen at a stuck edge.

    Args:
        stick: The edge.
        cursor: The cursor position.
        monitors: Each monitor's rect and work area.
        slack: How far in px from the end still counts.
    """
    (left, top, right, _), (x, y) = stick.monitor, cursor
    if stick.edge == "left":
        at_end, beyond = x <= left + slack, (left - 1, y)
    elif stick.edge == "right":
        at_end, beyond = x >= right - 1 - slack, (right, y)
    elif stick.edge == "top":
        at_end, beyond = y <= top + slack, (x, top - 1)
    else:
        return False
    return at_end and not any(_contains(monitor[0], *beyond) for monitor in monitors)


class SnapMove(QObject):
    """Snaps a window during its native drag and glides it into and out of place.

    Positions are worked out from where the drag started. Call begin() on WM_ENTERSIZEMOVE,
    place() on every WM_MOVING and stop() on WM_EXITSIZEMOVE. Snapping next to a note wins over
    sticking to an edge.

    Attributes:
        cancelled: Whether Esc ended the last drag, putting the window back.
    """

    def __init__(self, window):
        """Initialises the helper for window, a top-level QWidget."""
        super().__init__(window)
        self._window = window
        self.cancelled = False
        self._at: Point | None = None  # Where the drag last put the window.
        self._origin: tuple[Rect, Point] | None = None  # Window rect and cursor at the drag start.
        self._snapper = Snapper()
        self._edges = EdgeSnapper()
        self._monitors: list[tuple[Rect, Rect]] = []
        self._glide = motion.WindowGlide(self, GLIDE_MS, self._move, GLIDE_EASING)

    def _hwnd(self) -> int:
        return int(self._window.winId())

    def _move(self, x: int, y: int) -> None:
        self._at = (x, y)
        win_effects.move_window(self._hwnd(), x, y)

    def begin(self) -> None:
        """Starts a new move or resize from the window's current place."""
        rect, cursor = win_effects.window_rect(self._hwnd()), win_effects.cursor_pos()
        self._origin = (rect, cursor) if rect and cursor else None
        self._snapper = Snapper()
        self._edges = EdgeSnapper()
        self._monitors = win_effects.monitors()
        self.cancelled, self._at = False, None
        self._glide.stop()

    def stop(self) -> None:
        """Ends the drag, finishing a running glide at once unless Esc put the window back."""
        here = win_effects.window_rect(self._hwnd())
        self.cancelled = self._at is not None and here is not None and here[:2] != self._at
        if self._glide.is_running():
            target = self._glide.target
            self._glide.stop()
            if not self.cancelled:
                self._move(*target)
        self._origin = None

    def snapped_to(self) -> int | None:
        """Returns the index in the last ``others`` of the note snapped to, or None."""
        return self._snapper.held_other

    def stuck(self) -> list[Stick]:
        """Returns the monitor edges the window sticks to; none while snapped to a note."""
        return [] if self._snapper.held or self.cancelled else list(self._edges.stuck.values())

    def pushed(self) -> Stick | None:
        """Returns the edge the window is pushed into, where letting go hides it, or None."""
        return None if self._snapper.held or self.cancelled else self._edges.pushed_stick()

    def _state(self) -> tuple:
        """Returns what, apart from the cursor, decides where the window goes."""
        if self._snapper.held:
            return (self._snapper.held,)
        return None, tuple(sorted(self._edges.stuck.items())), self._edges.pushed

    def place(self, rect, others: list[Rect], aligned: list[str | None] | None = None) -> None:
        """Sets the window rect proposed by WM_MOVING (a wintypes.RECT) to the snapped place.

        Args:
            rect: The proposed rect, changed in place.
            others: The windows to snap to, in physical pixels.
            aligned: Per window, the only edge a note snapping to it may align (see Snapper).
        """
        cursor = win_effects.cursor_pos()
        if self._origin is None or cursor is None:
            return
        (left, top, right, bottom), (cursor_x, cursor_y) = self._origin
        dx, dy = cursor[0] - cursor_x, cursor[1] - cursor_y
        free = (left + dx, top + dy, right + dx, bottom + dy)
        scale = self._window.devicePixelRatioF()
        before = self._state()
        snapped = self._snapper.update(free, others, scale, aligned)
        edge_x, edge_y = self._edges.update(free, self._monitors, cursor, scale)
        target = snapped or (free[0] if edge_x is None else edge_x, free[1] if edge_y is None else edge_y)
        if self._state() != before:  # Snapping in, sticking or coming free: glide from where it is.
            here = win_effects.window_rect(self._hwnd())
            self._glide.start(here[:2] if here else self._glide.position(), target)
        else:
            self._glide.target = target
        x, y = self._glide.position()
        rect.left, rect.top, rect.right, rect.bottom = x, y, x + right - left, y + bottom - top
        self._at = (x, y)
