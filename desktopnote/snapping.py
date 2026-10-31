"""Snap to align: a note dragged close to another note snaps next to it, edges aligned.

Snapping only happens during a drag; nothing ties the notes together afterwards. Snapper holds
the rules; SnapMove applies them to a window's native drag.
"""
from __future__ import annotations

from PySide6.QtCore import QEasingCurve, QObject

from . import motion, win_effects

DISTANCE = 32  # How close in px, both sideways and up and down, a note must come to a place.
RELEASE = 72  # How far in px, either way, a snapped note must be pulled to come free.
GAP = 4  # Space in px left between snapped notes.
GLIDE_MS = 160  # Glide into and out of a snapped place.
GLIDE_EASING = QEasingCurve.OutQuint

Rect = tuple[int, int, int, int]  # (left, top, right, bottom).
Point = tuple[int, int]


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


class SnapMove(QObject):
    """Snaps a window during its native drag and glides it into and out of place.

    Windows proposes each drag position relative to the last one, so positions are worked out
    from where the drag started. Call begin() on WM_ENTERSIZEMOVE, place() on every WM_MOVING
    and stop() on WM_EXITSIZEMOVE.
    """

    def __init__(self, window):
        """Initialises the helper for window, a top-level QWidget."""
        super().__init__(window)
        self._window = window
        self._origin: tuple[Rect, Point] | None = None  # Window rect and cursor at the drag start.
        self._snapper = Snapper()
        self._glide = motion.WindowGlide(self, GLIDE_MS, self._move, GLIDE_EASING)

    def _hwnd(self) -> int:
        return int(self._window.winId())

    def _move(self, x: int, y: int) -> None:
        win_effects.move_window(self._hwnd(), x, y)

    def begin(self) -> None:
        """Starts a new move or resize from the window's current place."""
        rect, cursor = win_effects.window_rect(self._hwnd()), win_effects.cursor_pos()
        self._origin = (rect, cursor) if rect and cursor else None
        self._snapper = Snapper()
        self._glide.stop()

    def stop(self) -> None:
        """Ends the drag, finishing a running glide at once."""
        if self._glide.is_running():
            target = self._glide.target
            self._glide.stop()
            self._move(*target)
        self._origin = None

    def snapped_to(self) -> int | None:
        """Returns the index in the last ``others`` of the note snapped to, or None."""
        return self._snapper.held_other

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
        before = self._snapper.held
        snapped = self._snapper.update(free, others, self._window.devicePixelRatioF(), aligned)
        target = snapped or free[:2]
        if snapped != before:  # Snapping in, or coming free: glide from where the window is.
            here = win_effects.window_rect(self._hwnd())
            self._glide.start(here[:2] if here else self._glide.position(), target)
        else:
            self._glide.target = target
        x, y = self._glide.position()
        rect.left, rect.top, rect.right, rect.bottom = x, y, x + right - left, y + bottom - top
