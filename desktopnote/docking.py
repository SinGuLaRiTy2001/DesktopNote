"""Dock to edge: a note pushed into a monitor's edge hides there, leaving PEEK px on screen.

A note let go while pushed into a left, right or top edge (see snapping.EdgeSnapper) docks: it
stays above other windows, slides out when the cursor reaches it and back once the cursor has
left. While slid in, it is solid and clipped to its monitor's work area. While docked, ``note.x``
and ``note.y`` hold the slid-out position.
"""
from __future__ import annotations

import math

from PySide6.QtCore import QElapsedTimer, QEasingCurve, QObject, QPoint, QRect, QTimer
from PySide6.QtGui import QCursor, QGuiApplication
from PySide6.QtWidgets import QApplication

from . import motion, win_effects
from .snapping import GAP, HIDE_EDGES, SINK, Rect, Stick, continues, overlap, past_edge

PEEK = 12  # Px of a hidden note left on screen; its texts start further in.
HIDDEN_OPACITY = 0.8
FADE_MS = 160  # Fade into HIDDEN_OPACITY when a note opens hidden.
KEEP_MARGIN = 8  # Px around a revealed note within which the cursor keeps it out.
HIDE_DELAY_MS = 150  # How long the cursor must be away before a revealed note slides back.
REVEAL_DELAY_MS = 120  # How long the cursor must rest on a note hidden between two monitors.
HOLD_MS = 3000  # How long a note revealed from the tray stays out if the cursor never comes.
SLIDE_MS = 320  # Slide out.
SLIDE_IN_MS = 240  # Slide back.
SLIDE_EASING = QEasingCurve.OutCubic
POLL_MS = 50  # Cursor checks while docked.


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


def _shown_area(clip: Rect | None, rect: Rect) -> int:
    """Returns how much of a window at rect a clip shows: all of it if clip is None."""
    left, top, right, bottom = clip or (0, 0, rect[2] - rect[0], rect[3] - rect[1])
    return (right - left) * (bottom - top)


def _within(a: Rect | None, b: Rect | None) -> Rect | None:
    """Returns the part of a window two clips share; None stands for all of it."""
    if a is None or b is None:
        return a or b
    left, top = max(a[0], b[0]), max(a[1], b[1])
    return left, top, max(left, min(a[2], b[2])), max(top, min(a[3], b[3]))


def visible_part(rect: Rect, work: Rect) -> Rect | None:
    """Returns the part of rect inside work, relative to rect's top-left; None if it is all inside.

    Rects are (left, top, right, bottom), right and bottom exclusive.
    """
    left, top = max(rect[0], work[0]), max(rect[1], work[1])
    right, bottom = max(left, min(rect[2], work[2])), max(top, min(rect[3], work[3]))
    if (left, top, right, bottom) == tuple(rect):
        return None
    return left - rect[0], top - rect[1], right - rect[0], bottom - rect[1]


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
        self._work: Rect | None = None  # The work area of the dock's monitor, in physical px.
        self._shared = False  # Another monitor continues past the dock's edge.
        self._clip: Rect | None = None  # The part of the window shown, or None for all of it.
        self._ahead: Rect | None = None  # Where a gliding window will be a frame later.
        self._dragging = False
        self._sinking: Stick | None = None  # The edge the dragged note sinks into.
        self._snapped_to = False  # A dragged note is snapped to this one.
        self._wait_for_leave = False  # Hidden by a push: the cursor must leave before it reveals.
        self._away = QElapsedTimer()  # Runs while the cursor is away from a revealed note.
        self._hold = QElapsedTimer()  # Runs while a tray reveal holds the note out.
        self._resting = QElapsedTimer()  # Runs while the cursor rests on a hidden note.
        self._glide = motion.WindowGlide(self, SLIDE_MS, self._glide_step, SLIDE_EASING)
        self._fades_with_slide = False  # The glide is a slide, not settling after a drop.
        self._fade: motion.Tween | None = None
        self._poll = QTimer(self)
        self._poll.setInterval(POLL_MS)
        self._poll.timeout.connect(self._check_cursor)

    # --- Geometry ------------------------------------------------------------

    def _hwnd(self) -> int:
        return int(self._window.winId())

    def _move_to(self, x: int, y: int) -> None:
        """Moves the note to (x, y); move() would misplace these frameless windows by 8 px."""
        self._window.setGeometry(x, y, self._window.width(), self._window.height())

    def _glide_step(self, x: int, y: int) -> None:
        """Moves the window one frame; during a slide, its opacity follows its position."""
        size = self._window.size()
        target = self._physical(QRect(QPoint(x, y), size))
        self._ahead = None
        if self._glide.is_running():
            self._ahead = self._physical(QRect(QPoint(*self._glide.position(self._glide.frame_ms())), size))
        if target is not None:
            self._update_clip(target, shrink_only=True)
        self._move_to(x, y)
        if not self._glide.is_running():
            self._keep_on_monitor()
            self._update_clip()
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

    def _set_revealed(self, rect: QRect) -> None:
        note = self._window.note
        note.x, note.y, note.w, note.h = rect.x(), rect.y(), rect.width(), rect.height()

    def _physical(self, rect: QRect) -> Rect | None:
        """Returns rect in physical pixels, measured from where the window is."""
        native = win_effects.window_rect(self._hwnd())
        if native is None:
            return None
        geo, dpr = self._window.geometry(), self._window.devicePixelRatioF()
        dx, dy = round((rect.x() - geo.x()) * dpr), round((rect.y() - geo.y()) * dpr)
        dw, dh = round((rect.width() - geo.width()) * dpr), round((rect.height() - geo.height()) * dpr)
        return native[0] + dx, native[1] + dy, native[2] + dx + dw, native[3] + dy + dh

    def _keep_on_monitor(self) -> None:
        """Moves a slid-out note inside its monitor's work area if it reaches past the edge."""
        here, work = win_effects.window_rect(self._hwnd()), self._work
        if not (self.edge and self.shown and here and work):
            return
        past = past_edge(here, work, self.edge)
        if past > 0:
            step = math.ceil(past / self._window.devicePixelRatioF())
            inward = {"left": (step, 0), "right": (-step, 0), "top": (0, step)}[self.edge]
            revealed = self._revealed().translated(*inward)
            self._set_revealed(revealed)
            self._move_to(revealed.x(), revealed.y())
            self._window.changed.emit()

    def _screen_for(self, rect: QRect):
        """Returns the screen showing most of rect."""
        screens = QGuiApplication.screens()
        best = max(screens, key=lambda s: _area_of(s.geometry().intersected(rect)), default=None)
        if best is None or _area_of(best.geometry().intersected(rect)) == 0:
            best = QGuiApplication.screenAt(rect.center()) or QGuiApplication.primaryScreen()
        return best

    def _update_clip(self, rect: Rect | None = None, shrink_only: bool = False, force: bool = False) -> None:
        """Clips a solid window to its monitor's work area.

        A gliding window is also clipped to where it will be a frame later.

        Args:
            rect: Where the window is, in physical px; by default where it is now.
            shrink_only: Only clips more of the window away; for calls just before the window
                moves to rect.
            force: Sets the clip even if it seems unchanged, e.g. after the window's flags changed.
        """
        rect = rect or win_effects.window_rect(self._hwnd())
        work = self._sinking.work if self._sinking is not None else self._work
        clip = None
        if self._window.solid and rect and work:
            clip = visible_part(rect, work)
            if self._ahead is not None:
                clip = _within(clip, visible_part(self._ahead, work))
        if shrink_only and _shown_area(clip, rect) >= _shown_area(self._clip, rect):
            return
        if clip != self._clip or force:
            self._clip = clip
            win_effects.clip_window(self._hwnd(), clip)

    def window_moved(self) -> None:
        """Fits the clip of a solid or clipped window to where it is; call on WM_WINDOWPOSCHANGED."""
        if self._window.solid or self._clip is not None:
            self._update_clip()

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
        if not self.edge:
            return win_effects.window_rect(self._hwnd())
        return self._physical(self._revealed())

    # --- Docking -------------------------------------------------------------

    def restore(self) -> None:
        """Puts a note saved as docked into its hidden place before it is shown."""
        revealed = self._dock_to(self._revealed(), self._window.note.dock)
        if revealed is None:
            self._window.note.dock = ""
            return
        self._set_revealed(revealed)
        self.shown = False
        self._window.set_solid(True)  # Clipped once the window is shown (see window_moved).
        self._move_to(*hidden_pos(revealed, self._area, self.edge).toTuple())

    def drag_started(self) -> None:
        """Pauses sliding while the note is moved or resized."""
        self._dragging = True
        self._glide.stop()

    def follow_drag(self, rect: Rect, pushed: Stick | None, stuck: list[Stick], ahead: Rect | None = None) -> None:
        """Shows, while the note is dragged, whether letting go hides it.

        Pushed into an edge, the note turns into a hidden note as it sinks in, and back as it
        comes out again. Call just before each move of the drag.

        Args:
            rect: Where the drag moves the window next, in physical px.
            pushed: The edge the note is pushed into, or None.
            stuck: The edges the note sticks to.
            ahead: Where the window will be a frame later while it glides, else None.
        """
        self._ahead = ahead
        if pushed is not None:
            self._sinking = pushed
            self._window.set_solid(True)
        sinking = self._sinking
        if sinking is None:
            return
        depth = past_edge(rect, sinking.work, sinking.edge)
        if pushed is None and (depth <= 0 or sinking not in stuck):  # Out again, or came free.
            self._sinking = None
            self._window.setWindowOpacity(1.0)
            self._window.set_solid(False)
            self._update_clip(rect)
            return
        sunk = max(0.0, min(1.0, depth / (SINK * self._window.devicePixelRatioF())))
        self._window.setWindowOpacity(1.0 - (1.0 - HIDDEN_OPACITY) * sunk)
        self._update_clip(rect, shrink_only=True)

    def drag_finished(self, moved: bool, stuck: list[Stick], pushed: Stick | None) -> None:
        """Docks, keeps or undocks the note after a move or resize.

        Let go while pushed into an edge, the note docks there and slides in at once. A docked
        note moved along its edge or resized stays docked; moved anywhere else, it undocks.

        Args:
            moved: Whether the note was moved, not only resized.
            stuck: The edges the note sticks to where it was let go.
            pushed: The edge it was pushed into, or None.
        """
        self._dragging = False
        sinking, self._sinking, self._ahead = self._sinking, None, None
        here = win_effects.window_rect(self._hwnd())
        if pushed is not None and (here is None or past_edge(here, pushed.work, pushed.edge) <= 0):
            pushed = None  # Esc ended the drag and put the note back.
        if pushed is None and sinking is not None:
            self._window.setWindowOpacity(1.0)
            self._window.set_solid(False)
            self._update_clip()
        geo = self._window.geometry()
        revealed = self._dock_to(geo, pushed.edge) if pushed is not None else None
        if revealed is not None:
            self._set_revealed(revealed)
            self.shown = False
            self._wait_for_leave = True
            self._hold.invalidate()
            self._slide(hidden_pos(revealed, self._area, self.edge), duration=SLIDE_IN_MS, fade=False)
            self._fade_to(HIDDEN_OPACITY)
            self._window.changed.emit()
            return
        if not self.edge:
            return
        if moved and not any(stick.edge == self.edge and stick.work == self._work for stick in stuck):
            self._undock()
            return
        revealed = revealed_rect(geo, self._area, self.edge)
        self._set_revealed(revealed)
        self.shown = True
        self._away.invalidate()
        self._fades_with_slide = False  # Settles against the edge fully opaque.
        self._glide.duration = SLIDE_MS
        self._glide.start((geo.x(), geo.y()), (revealed.x(), revealed.y()))
        self._window.changed.emit()

    def _dock_to(self, rect: QRect, edge: str) -> QRect | None:
        """Docks to an edge of the screen showing most of rect.

        Returns:
            Where the note is slid out, flush against that edge; None if edge is not one to dock to.
        """
        if edge not in HIDE_EDGES:
            return None
        screen = self._screen_for(rect)
        self.edge, self._area, self._screen_name = edge, screen.availableGeometry(), screen.name()
        self._window.note.dock = edge
        revealed = revealed_rect(rect, self._area, edge)
        physical, monitors = self._physical(revealed), win_effects.monitors()
        monitor = max(monitors, key=lambda m: overlap(physical, m[0]), default=None) if physical else None
        self._work = monitor[1] if monitor else None
        self._shared = monitor is not None and continues(monitor[0], edge, physical, monitors)
        win_effects.set_topmost(self._hwnd(), True)
        self._poll.start()
        return revealed

    def keep_on_top(self) -> None:
        """Puts a docked note back above other windows, e.g. after its flags changed."""
        if self.edge:
            win_effects.set_topmost(self._hwnd(), True)
            self._update_clip(force=True)

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
            win_effects.set_topmost(self._hwnd(), self._window.note.pinned)
            self._window.changed.emit()

    def validate(self) -> bool:
        """Re-docks to the screen that shows the note after a screen change.

        Returns:
            Whether the note is docked.
        """
        if not self.edge:
            return False
        revealed = self._dock_to(self._revealed(), self.edge)
        self._set_revealed(revealed)
        self._move_to(*(revealed.topLeft() if self.shown else hidden_pos(revealed, self._area, self.edge)).toTuple())
        self._update_clip()
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
            self._slide(self._revealed().topLeft(), self._show_glass)

    def hide(self) -> None:
        """Slides a docked note back to the edge."""
        if self.edge and self.shown:
            self.shown = False
            self._hold.invalidate()
            self._window.set_solid(True)
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

    def _slide(self, to: QPoint, on_done=None, duration: int = SLIDE_MS, fade: bool = True) -> None:
        """Slides the window to to.

        Args:
            to: The top-left to slide to.
            on_done: Called once it is there.
            duration: Length of the slide in milliseconds.
            fade: Whether the opacity follows the position (see _glide_step).
        """
        if self._fade is not None:
            self._fade.stop()
        self._fades_with_slide = fade
        self._glide.duration = duration
        geo = self._window.geometry()
        here = self._glide.position() if self._glide.is_running() else (geo.x(), geo.y())
        self._glide.start(here, (to.x(), to.y()), on_done)

    def _show_glass(self) -> None:
        """Gives a slid-out note its frosted glass back, which Windows drops while translucent."""
        if self._window.solid:
            self._window.set_solid(False)
            self._update_clip()
        else:
            self._window.reapply_glass()

    def _fade_to(self, opacity: float) -> None:
        """Eases the window opacity to opacity, restoring the glass at 1."""
        if self._fade is not None:
            self._fade.stop()
        start = self._window.windowOpacity()

        def step(v: float) -> None:
            self._window.setWindowOpacity(start + (opacity - start) * v)

        def done() -> None:
            if opacity >= 1.0:
                self._show_glass()

        if abs(start - opacity) < 0.01:
            done()
            return
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
            if not (on_screen and reveal_zone(self._revealed(), self._area, self.edge).contains(cursor)):
                self._wait_for_leave = False
                self._resting.invalidate()
            elif self._wait_for_leave or win_effects.left_button_down():
                pass
            elif self._shared and not self._resting.isValid():
                self._resting.start()
            elif not self._shared or self._resting.elapsed() >= REVEAL_DELAY_MS:
                self._resting.invalidate()
                self.reveal()
        elif self._keeps_out(cursor):
            self._away.invalidate()
        elif not self._away.isValid():
            self._away.start()
        elif self._away.elapsed() >= HIDE_DELAY_MS:
            self.hide()


def _area_of(rect: QRect) -> int:
    return max(0, rect.width()) * max(0, rect.height())
