"""Dock to edge: a note dropped against a screen edge slides off it, leaving PEEK px.

The note docks to the left, right or top edge unless another monitor continues there. It stays
above other windows, slides out when the cursor reaches it and back once the cursor has left.
While docked, ``note.x`` and ``note.y`` hold the slid-out position.
"""
from __future__ import annotations

from PySide6.QtCore import QElapsedTimer, QEasingCurve, QObject, QPoint, QRect, QTimer
from PySide6.QtGui import QCursor, QGuiApplication
from PySide6.QtWidgets import QApplication

from . import motion, win_effects
from .snapping import GAP

EDGES = ("left", "right", "top")  # The bottom edge usually holds the taskbar.
PEEK = 12  # Px of a hidden note left on screen; its texts start further in.
HIDDEN_OPACITY = 0.8
FADE_MS = 160  # Fade into HIDDEN_OPACITY when a note opens hidden.
TOUCH = 3  # Px from an edge that count as touching it; Windows stops a drag just below the top.
KEEP_MARGIN = 8  # Px around a revealed note within which the cursor keeps it out.
HIDE_DELAY_MS = 150  # How long the cursor must be away before a revealed note slides back.
HOLD_MS = 3000  # How long a note revealed from the tray stays out if the cursor never comes.
SLIDE_MS = 320  # Slide out.
SLIDE_IN_MS = 240  # Slide back.
SLIDE_EASING = QEasingCurve.OutCubic
POLL_MS = 50  # Cursor checks while docked.


def dock_edge(rect: QRect, area: QRect, closed: set[str] = frozenset()) -> str:
    """Returns the edge of area that rect touches or crosses, or "" if none.

    At a corner, the edge crossed further wins; left and right win ties with the top.

    Args:
        rect: The note's rect.
        area: The screen's available area.
        closed: Edges another monitor continues past.
    """
    reach = {"left": area.left() - rect.left(), "right": rect.right() - area.right(), "top": area.top() - rect.top()}
    open_edges = [edge for edge in EDGES if edge not in closed and reach[edge] >= -TOUCH]
    return max(open_edges, key=lambda edge: (reach[edge], edge != "top"), default="")


def revealed_rect(rect: QRect, area: QRect, edge: str) -> QRect:
    """Returns rect moved inside area and flush against its edge."""
    out = QRect(rect)
    out.moveLeft(max(area.left(), min(out.left(), area.right() + 1 - out.width())))
    out.moveTop(max(area.top(), min(out.top(), area.bottom() + 1 - out.height())))
    if edge == "left":
        out.moveLeft(area.left())
    elif edge == "right":
        out.moveRight(area.right())
    elif edge == "top":
        out.moveTop(area.top())
    return out


def hidden_pos(revealed: QRect, area: QRect, edge: str) -> QPoint:
    """Returns the top-left of a note hidden at edge, with PEEK px of it left on screen."""
    if edge == "left":
        return QPoint(area.left() - revealed.width() + PEEK, revealed.top())
    if edge == "right":
        return QPoint(area.right() + 1 - PEEK, revealed.top())
    return QPoint(revealed.left(), area.top() - revealed.height() + PEEK)


def reveal_zone(revealed: QRect, area: QRect, edge: str) -> QRect:
    """Returns the part of a hidden note left on screen, where the cursor reveals it."""
    if edge == "left":
        return QRect(area.left(), revealed.top(), PEEK, revealed.height())
    if edge == "right":
        return QRect(area.right() + 1 - PEEK, revealed.top(), PEEK, revealed.height())
    return QRect(revealed.left(), area.top(), revealed.width(), PEEK)


def closed_edges(screen_rect: QRect, other_screens: list[QRect], rect: QRect) -> set[str]:
    """Returns the edges of screen_rect that another monitor continues past along rect."""
    closed = set()
    for other in other_screens:
        beside = other.top() <= rect.bottom() and other.bottom() >= rect.top()
        above = other.left() <= rect.right() and other.right() >= rect.left()
        if beside and other.right() + 1 == screen_rect.left():
            closed.add("left")
        if beside and other.left() == screen_rect.right() + 1:
            closed.add("right")
        if above and other.bottom() + 1 == screen_rect.top():
            closed.add("top")
    return closed


class EdgeDock(QObject):
    """Docks a note window at a screen edge and slides it out and back.

    Attributes:
        edge: The edge the note is docked to ("left", "right" or "top"), or "" if none.
        shown: Whether a docked note is slid out.
    """

    def __init__(self, window):
        """Initialises docking for window, a NoteWindow."""
        super().__init__(window)
        self._window = window
        self.edge = ""
        self.shown = False
        self._area = QRect()
        self._screen_name = ""
        self._dragging = False
        self._snapped_to = False  # A dragged note is snapped to this one.
        self._away = QElapsedTimer()  # Runs while the cursor is away from a revealed note.
        self._hold = QElapsedTimer()  # Runs while a tray reveal holds the note out.
        self._glide = motion.WindowGlide(self, SLIDE_MS, self._glide_step, SLIDE_EASING)
        self._fades_with_slide = False  # The glide is a slide, not settling after a drop.
        self._fade: motion.Tween | None = None
        self._poll = QTimer(self)
        self._poll.setInterval(POLL_MS)
        self._poll.timeout.connect(self._check_cursor)

    # --- Geometry ------------------------------------------------------------

    def _move_to(self, x: int, y: int) -> None:
        """Moves the note to (x, y); move() would misplace these frameless windows by 8 px."""
        self._window.setGeometry(x, y, self._window.width(), self._window.height())

    def _glide_step(self, x: int, y: int) -> None:
        """Moves the window one frame; during a slide, its opacity follows its position."""
        self._move_to(x, y)
        if not self._fades_with_slide:
            return
        revealed = self._revealed()
        hidden = hidden_pos(revealed, self._area, self.edge)
        if self.edge == "top":
            span, gone = hidden.y() - revealed.y(), y - revealed.y()
        else:
            span, gone = hidden.x() - revealed.x(), x - revealed.x()
        hidden_part = max(0.0, min(1.0, gone / span)) if span else 0.0
        self._window.setWindowOpacity(1.0 - (1.0 - HIDDEN_OPACITY) * hidden_part)

    def _revealed(self) -> QRect:
        note = self._window.note
        return QRect(note.x, note.y, note.w, note.h)

    def _screen_for(self, rect: QRect):
        """Returns the screen showing most of rect and that screen's closed edges."""
        screens = QGuiApplication.screens()
        best = max(screens, key=lambda s: _area_of(s.geometry().intersected(rect)), default=None)
        if best is None or _area_of(best.geometry().intersected(rect)) == 0:
            best = QGuiApplication.screenAt(rect.center()) or QGuiApplication.primaryScreen()
        others = [s.geometry() for s in screens if s is not best]
        return best, closed_edges(best.geometry(), others, rect)

    def is_hidden(self) -> bool:
        """Returns whether the note is docked and slid in."""
        return bool(self.edge) and not self.shown

    def place_beside(self, width: int, height: int) -> QPoint | None:
        """Returns where a note made from this one opens, so this one cannot cover it.

        That is next to it on the inner side of the screen, or None if this note is not docked.
        """
        if not self.edge:
            return None
        revealed, area = self._revealed(), self._area
        if self.edge == "right":
            pos = QPoint(revealed.left() - GAP - width, revealed.top())
        elif self.edge == "left":
            pos = QPoint(revealed.right() + 1 + GAP, revealed.top())
        else:
            pos = QPoint(revealed.left(), revealed.bottom() + 1 + GAP)
        pos.setX(max(area.left(), min(pos.x(), area.right() + 1 - width)))
        pos.setY(max(area.top(), min(pos.y(), area.bottom() + 1 - height)))
        return pos

    def snap_alignment(self) -> str | None:
        """Returns the edge a note snapping to this one must align: its screen edge, or None."""
        return self.edge or None

    def snap_rect(self) -> tuple[int, int, int, int] | None:
        """Returns the rect other notes snap to, in physical pixels; the slid-out one if docked."""
        rect = win_effects.window_rect(int(self._window.winId()))
        if rect is None or not self.edge:
            return rect
        geo, revealed, dpr = self._window.geometry(), self._revealed(), self._window.devicePixelRatioF()
        dx, dy = round((revealed.x() - geo.x()) * dpr), round((revealed.y() - geo.y()) * dpr)
        return rect[0] + dx, rect[1] + dy, rect[2] + dx, rect[3] + dy

    # --- Docking -------------------------------------------------------------

    def restore(self) -> None:
        """Puts a note saved as docked into its hidden place before it is shown."""
        if self._window.note.dock and self._dock_to(self._revealed(), self._window.note.dock):
            self.shown = False
            self._move_to(*hidden_pos(self._revealed(), self._area, self.edge).toTuple())
        else:
            self._window.note.dock = ""

    def drag_started(self) -> None:
        """Pauses sliding while the note is moved or resized."""
        self._dragging = True
        self._glide.stop()

    def drag_finished(self, moved: bool) -> None:
        """Docks, re-docks or undocks the note after a move or resize.

        Args:
            moved: Whether the note was moved, not only resized.
        """
        self._dragging = False
        if not moved and not self.edge:
            return
        geo = self._window.geometry()
        screen, closed = self._screen_for(geo)
        edge = dock_edge(geo, screen.availableGeometry(), closed)
        if not edge:
            self._undock()
            return
        self._window.note.dock = edge
        area = screen.availableGeometry()
        revealed = revealed_rect(geo, area, edge)
        note = self._window.note
        note.x, note.y, note.w, note.h = revealed.x(), revealed.y(), revealed.width(), revealed.height()
        self._dock_to(revealed, edge)
        self.shown = True
        self._away.invalidate()
        self._fades_with_slide = False  # Settles against the edge fully opaque.
        self._glide.duration = SLIDE_MS
        self._glide.start((geo.x(), geo.y()), (revealed.x(), revealed.y()))
        self._window.changed.emit()

    def _dock_to(self, revealed: QRect, edge: str) -> bool:
        """Docks to edge of the screen showing revealed; returns False if the edge is closed."""
        screen, closed = self._screen_for(revealed)
        if edge not in EDGES or edge in closed:
            return False
        self.edge, self._area, self._screen_name = edge, screen.availableGeometry(), screen.name()
        win_effects.set_topmost(int(self._window.winId()), True)
        self._poll.start()
        return True

    def keep_on_top(self) -> None:
        """Puts a docked note back above other windows, e.g. after its flags changed."""
        if self.edge:
            win_effects.set_topmost(int(self._window.winId()), True)

    def settle(self) -> None:
        """Fades a note that opened hidden once it has appeared."""
        if self.is_hidden():
            self._fade_to(HIDDEN_OPACITY)

    def _undock(self) -> None:
        """Turns the note back into a normal one where it is."""
        if self.edge or self._window.note.dock:
            self.edge, self.shown = "", False
            self._window.note.dock = ""
            self._poll.stop()
            self._fade_to(1.0)
            win_effects.set_topmost(int(self._window.winId()), self._window.note.pinned)
            self._window.changed.emit()

    def validate(self) -> bool:
        """Re-docks after a screen change; undocks and returns False if the edge is gone."""
        if not self.edge:
            return False
        edge, self.edge = self.edge, ""
        if not self._dock_to(self._revealed(), edge):
            self._undock()
            self._move_to(*self._revealed().topLeft().toTuple())
            return False
        revealed = revealed_rect(self._revealed(), self._area, edge)
        note = self._window.note
        note.x, note.y = revealed.x(), revealed.y()
        self._move_to(*(revealed.topLeft() if self.shown else hidden_pos(revealed, self._area, edge)).toTuple())
        return True

    # --- Sliding -------------------------------------------------------------

    def reveal(self, hold: bool = False) -> None:
        """Slides a docked note out.

        Args:
            hold: Keeps it out for HOLD_MS even if the cursor never comes (tray reveals).
        """
        if not self.edge:
            return
        if hold:
            self._hold.start()
        if not self.shown:
            self.shown = True
            self._away.invalidate()
            self._window.raise_()
            self.keep_on_top()
            self._window.unsetCursor()
            self._slide(self._revealed().topLeft(), self._window.reapply_glass)

    def hide(self) -> None:
        """Slides a docked note back to the edge."""
        if self.edge and self.shown:
            self.shown = False
            self._hold.invalidate()
            self._slide(hidden_pos(self._revealed(), self._area, self.edge), duration=SLIDE_IN_MS)

    def hold_for_snap(self, on: bool) -> None:
        """Keeps a docked note slid out while a dragged note is snapped to it (on)."""
        if on == self._snapped_to:
            return
        self._snapped_to = on
        if on:
            self.reveal()
        else:
            self._away.invalidate()

    def _slide(self, to: QPoint, on_done=None, duration: int = SLIDE_MS) -> None:
        """Slides the window to to; its opacity follows (see _glide_step)."""
        if self._fade is not None:
            self._fade.stop()
        self._fades_with_slide = True
        self._glide.duration = duration
        geo = self._window.geometry()
        here = self._glide.position() if self._glide.is_running() else (geo.x(), geo.y())
        self._glide.start(here, (to.x(), to.y()), on_done)

    def _fade_to(self, opacity: float) -> None:
        """Eases the window opacity to opacity, restoring the acrylic at 1.

        Windows drops the acrylic of a window that is not fully opaque.
        """
        if self._fade is not None:
            self._fade.stop()
        start = self._window.windowOpacity()
        if abs(start - opacity) < 0.01:
            return

        def step(v: float) -> None:
            self._window.setWindowOpacity(start + (opacity - start) * v)

        def done() -> None:
            if opacity >= 1.0:
                self._window.reapply_glass()

        self._fade = motion.Tween(self, FADE_MS, step, done)
        self._fade.start()

    def _keeps_out(self, cursor: QPoint) -> bool:
        """Returns whether a revealed note should stay out now."""
        if self._snapped_to or self._window.editing or QApplication.activePopupWidget() is not None:
            return True
        near = self._revealed().adjusted(-KEEP_MARGIN, -KEEP_MARGIN, KEEP_MARGIN, KEEP_MARGIN)
        if near.contains(cursor) or reveal_zone(self._revealed(), self._area, self.edge).contains(cursor):
            self._hold.invalidate()  # The cursor came, so leaving now hides it.
            return True
        return self._hold.isValid() and self._hold.elapsed() < HOLD_MS

    def _check_cursor(self) -> None:
        if self._dragging or not self._window.isVisible():
            return
        cursor = QCursor.pos()
        if not self.shown:
            screen = QGuiApplication.screenAt(cursor)
            on_screen = screen is not None and screen.name() == self._screen_name
            if (on_screen and reveal_zone(self._revealed(), self._area, self.edge).contains(cursor)
                    and not win_effects.left_button_down()):
                self.reveal()
        elif self._keeps_out(cursor):
            self._away.invalidate()
        elif not self._away.isValid():
            self._away.start()
        elif self._away.elapsed() >= HIDE_DELAY_MS:
            self.hide()


def _area_of(rect: QRect) -> int:
    return max(0, rect.width()) * max(0, rect.height())
