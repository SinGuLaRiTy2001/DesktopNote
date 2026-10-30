"""Snap to align: a note dragged close to another note snaps next to it, edges aligned.

Snapping only happens while a note is being dragged; nothing ties the notes together afterwards,
and resizing never snaps. Snapper holds the rules and has no Qt or Windows dependencies;
SnapMove applies them to a window's native drag and glides the window into and out of place.
"""
from __future__ import annotations

from PySide6.QtCore import QEasingCurve, QElapsedTimer, QObject, Qt, QTimer

from . import motion, win_effects

DISTANCE = 32  # How close in px, both sideways and up and down, a note must come to a place.
RELEASE = 72  # How far in px, either way, a snapped note must be pulled to come free.
GAP = 4  # Space in px left between snapped notes.
GLIDE_MS = 160  # Glide into and out of a snapped place.
# A strong ease-out: the note leaps most of the way at once and settles softly, without overshoot.
GLIDE_EASING = QEasingCurve.OutQuint

Rect = tuple[int, int, int, int]  # (left, top, right, bottom).
Point = tuple[int, int]


def places_around(other: Rect, width: int, height: int, gap: int) -> list[Point]:
    """Returns the eight places, as (left, top), where a note of the given size snaps to other.

    Beside it, to the right and to the left, the top or the bottom edges align; below and above
    it, the left or the right edges align. Every place is gap away from other.
    """
    o_left, o_top, o_right, o_bottom = other
    beside = (o_right + gap, o_left - gap - width)
    level = (o_top, o_bottom - height)
    stacked = (o_bottom + gap, o_top - gap - height)
    flush = (o_left, o_right - width)
    return [(x, y) for x in beside for y in level] + [(x, y) for y in stacked for x in flush]


class Snapper:
    """Decides, move by move, where a dragged note snaps next to another note.

    The note snaps to one of the places_around() each other note. Distances are measured between
    the corners that meet there, for example the dragged note's top-left corner and the point gap
    to the right of the other note's top-right corner. The note snaps when that corner comes
    within ``distance`` of the place both sideways and up and down, or passes the place between
    two moves (a fast drag); the nearest place wins. It stays there until it is pulled more than
    ``release`` away in either direction.

    All values are in pixels.

    Attributes:
        distance: How close to a place, in both directions, the note snaps to it.
        release: How far the note must be pulled, in either direction, to come free.
        gap: The space left between snapped notes.
        held: The (left, top) the note is snapped to, or None.
    """

    def __init__(self, distance: int = DISTANCE, release: int = RELEASE, gap: int = GAP):
        self.distance, self.release, self.gap = distance, release, gap
        self.held: Point | None = None
        self._last: Point | None = None  # The note's (left, top) at the previous move.

    def update(self, rect: Rect, others: list[Rect], scale: float = 1.0) -> Point | None:
        """Returns where the note moved to ``rect`` sits: its snapped (left, top), or None.

        Args:
            rect: Where the note would be without snapping.
            others: The other notes.
            scale: Factor applied to distance, release and gap (the display's pixel ratio).
        """
        distance, release, gap = (round(v * scale) for v in (self.distance, self.release, self.gap))
        left, top, right, bottom = rect
        # The corners that meet at a place are as far apart as the note's top-left corner is from
        # the place's, so offsets are taken from the top-left corner throughout.
        places = {place: (left - place[0], top - place[1])
                  for other in others for place in places_around(other, right - left, bottom - top, gap)}
        last, self._last = self._last, (left, top)
        if self.held is not None:
            offset = places.get(self.held)
            if offset is not None and abs(offset[0]) <= release and abs(offset[1]) <= release:
                return self.held
            self.held = None

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
        return self.held


class SnapMove(QObject):
    """Snaps a window during its native (system) drag and glides it into and out of place.

    Windows proposes each drag position relative to the last one, so a snapped window would never
    come free. The free position is therefore worked out from where the drag started, and only
    then snapped.

    A glide is the window's offset from where it belongs, easing away to nothing. Where it
    belongs is the snapped place, or, for a window coming free, under the cursor, so a window
    pulled free follows the cursor at once while it eases off its old place. The position is
    worked out afresh on every drag message and every frame, so it never lags behind either.

    Call begin() on WM_ENTERSIZEMOVE and place() on every WM_MOVING.
    """

    def __init__(self, window):
        """Initialises the helper for window, a top-level QWidget."""
        super().__init__(window)
        self._window = window
        self._origin: tuple[Rect, Point] | None = None  # Window rect and cursor at the drag start.
        self._snapper = Snapper()
        self._target: Point = (0, 0)  # Where the window belongs.
        self._offset: Point = (0, 0)  # The window's offset from its target when the glide began.
        self._curve = QEasingCurve(GLIDE_EASING)
        self._clock = QElapsedTimer()
        self._timer = QTimer(self)  # Keeps the glide going while the mouse rests.
        self._timer.setTimerType(Qt.PreciseTimer)
        self._timer.setInterval(motion.FRAME_MS)
        self._timer.timeout.connect(self._tick)

    def _hwnd(self) -> int:
        return int(self._window.winId())

    def begin(self) -> None:
        """Starts a new move or resize from the window's current place."""
        rect, cursor = win_effects.window_rect(self._hwnd()), win_effects.cursor_pos()
        self._origin = (rect, cursor) if rect and cursor else None
        self._snapper = Snapper()
        self._timer.stop()
        self._offset = (0, 0)

    def place(self, rect, others: list[Rect]) -> None:
        """Sets the window rect proposed by WM_MOVING (a wintypes.RECT) to the snapped place.

        Args:
            rect: The proposed rect, changed in place.
            others: The windows to snap to, in physical pixels.
        """
        cursor = win_effects.cursor_pos()
        if self._origin is None or cursor is None:
            return
        (left, top, right, bottom), (cursor_x, cursor_y) = self._origin
        dx, dy = cursor[0] - cursor_x, cursor[1] - cursor_y
        free = (left + dx, top + dy, right + dx, bottom + dy)
        before = self._snapper.held
        snapped = self._snapper.update(free, others, self._window.devicePixelRatioF())
        target = snapped or free[:2]
        if snapped != before:  # Snapping in, or coming free: glide from where the window is.
            here = win_effects.window_rect(self._hwnd())
            x, y = here[:2] if here else self._position()
            self._offset = (x - target[0], y - target[1])
            self._clock.start()
            self._timer.start()
        self._target = target
        x, y = self._position()
        rect.left, rect.top, rect.right, rect.bottom = x, y, x + right - left, y + bottom - top

    def _remaining(self) -> float:
        """Returns how much of the glide's offset is left: 1 when it begins, 0 once it is over."""
        if not self._timer.isActive():
            return 0.0
        progress = self._clock.elapsed() / GLIDE_MS
        return 0.0 if progress >= 1 else 1.0 - self._curve.valueForProgress(progress)

    def _position(self) -> Point:
        """Returns where the window is now: its target plus what is left of the offset."""
        left = self._remaining()
        return round(self._target[0] + self._offset[0] * left), round(self._target[1] + self._offset[1] * left)

    def _tick(self) -> None:
        """Moves the window one frame further, ending the glide once it is in place."""
        done = self._clock.elapsed() >= GLIDE_MS
        if done:
            self._timer.stop()
        win_effects.move_window(self._hwnd(), *self._position())
