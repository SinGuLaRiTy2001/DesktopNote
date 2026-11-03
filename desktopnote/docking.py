"""Dock to edge: a note pushed into a monitor's edge hides there, leaving PEEK px on screen.

A note let go while pushed into a left, right or top edge (see snapping.EdgeSnapper) docks: it
stays above other windows, slides out when the cursor reaches it and back once the cursor has
left. It slides within its window, opaque and in one piece, frosted by a snapshot of what is
behind it, and a ShadowWindow casts its shadow. Pushed into an edge or slid in, it eases to
HIDDEN_OPACITY; slid in, it lets clicks through. While docked, ``note.x`` and ``note.y`` hold
the slid-out position.
"""
from __future__ import annotations

import math

from PySide6.QtCore import QElapsedTimer, QEasingCurve, QObject, QPoint, QRect, QTimer
from PySide6.QtGui import QCursor, QGuiApplication
from PySide6.QtWidgets import QApplication

from . import motion, win_effects
from .glass import ShadowWindow
from .snapping import GAP, HIDE_EDGES, Rect, Stick, continues, overlap, past_edge

PEEK = 12  # Px of a hidden note left on screen.
HIDDEN_OPACITY = 0.8  # Opacity of a note pushed into an edge or slid in.
FADE_MS = 160  # Fade to or from HIDDEN_OPACITY.
KEEP_MARGIN = 8  # Px around a revealed note within which the cursor keeps it out.
HIDE_DELAY_MS = 150  # How long the cursor must be away before a revealed note slides back.
REVEAL_DELAY_MS = 60  # How long the cursor must rest on a note hidden between two monitors.
APPROACH = 64  # Px from a hidden note within which the cursor gets its backdrop ready.
FRESH_MS = 1000  # How long a backdrop snapshot is used for a note sliding out.
HOLD_MS = 3000  # How long a note revealed from the tray stays out if the cursor never comes.
SLIDE_MS = 320  # Slide out.
SLIDE_IN_MS = 240  # Slide back.
SLIDE_EASING = QEasingCurve.OutCubic
POLL_MS = 16  # Cursor checks while docked.


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


def hidden_depth(revealed: QRect, edge: str) -> int:
    """Returns how far a note slides into edge to hide, leaving PEEK px of it."""
    return (revealed.height() if edge == "top" else revealed.width()) - PEEK


def shift_into(edge: str, depth: int) -> QPoint:
    """Returns the shift that moves a note depth px into edge."""
    return {"left": QPoint(-depth, 0), "right": QPoint(depth, 0), "top": QPoint(0, -depth)}.get(edge, QPoint())


def reveal_zone(revealed: QRect, area: QRect, edge: str) -> QRect:
    """Returns the part of a hidden note left on screen, where the cursor reveals it."""
    if edge == "left":
        return QRect(area.left(), revealed.top(), PEEK, revealed.height())
    if edge == "right":
        return QRect(area.right() + 1 - PEEK, revealed.top(), PEEK, revealed.height())
    return QRect(revealed.left(), area.top(), revealed.width(), PEEK)


class EdgeDock(QObject):
    """Docks a note window at a screen edge and slides the note out and back.

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
        self._depth = 0  # How far the note is slid into its edge, in px.
        self._pushed = ""  # The edge a dragged note is pushed into.
        self._dragging = False
        self._snapped_to = False  # A dragged note is snapped to this one.
        self._wait_for_leave = False  # Hidden by a push: the cursor must leave before it reveals.
        self._shadow: ShadowWindow | None = None
        self._casting = False  # The ShadowWindow casts the note's shadow, not the note itself.
        self._away = QElapsedTimer()  # Runs while the cursor is away from a revealed note.
        self._hold = QElapsedTimer()  # Runs while a tray reveal holds the note out.
        self._resting = QElapsedTimer()  # Runs while the cursor rests on a hidden note.
        self._frosted = QElapsedTimer()  # Runs from the last backdrop snapshot.
        self._slide_glide = motion.WindowGlide(self, SLIDE_MS, self._slide_step, SLIDE_EASING)
        self._settle = motion.WindowGlide(self, SLIDE_MS, self._move_to, SLIDE_EASING)
        self._fade: motion.Tween | None = None
        self._poll = QTimer(self)
        self._poll.setInterval(POLL_MS)
        self._poll.timeout.connect(self._check_cursor)

    # --- Geometry ------------------------------------------------------------

    def _hwnd(self) -> int:
        return int(self._window.winId())

    def _move_to(self, x: int, y: int) -> None:
        """Moves the note to (x, y), keeping its size."""
        self._window.setGeometry(x, y, self._window.width(), self._window.height())

    def _revealed(self) -> QRect:
        note = self._window.note
        return QRect(note.x, note.y, note.w, note.h)

    def _set_revealed(self, rect: QRect) -> None:
        note = self._window.note
        note.x, note.y, note.w, note.h = rect.x(), rect.y(), rect.width(), rect.height()

    def _place(self, rect: QRect) -> None:
        """Keeps rect as the slid-out place and moves the window there."""
        self._set_revealed(rect)
        if self._window.geometry().topLeft() != rect.topLeft():
            self._move_to(rect.x(), rect.y())

    def _physical(self, rect: QRect) -> Rect | None:
        """Returns rect in physical pixels, relative to where the window is now."""
        native = win_effects.window_rect(self._hwnd())
        if native is None:
            return None
        geo, dpr = self._window.geometry(), self._window.devicePixelRatioF()
        dx, dy = round((rect.x() - geo.x()) * dpr), round((rect.y() - geo.y()) * dpr)
        dw, dh = round((rect.width() - geo.width()) * dpr), round((rect.height() - geo.height()) * dpr)
        return native[0] + dx, native[1] + dy, native[2] + dx + dw, native[3] + dy + dh

    def _keep_on_monitor(self) -> None:
        """Moves a docked window inside its monitor's work area if it reaches past the edge."""
        here, work = win_effects.window_rect(self._hwnd()), self._work
        if not (self.edge and here and work):
            return
        past = past_edge(here, work, self.edge)
        if past > 0:
            step = math.ceil(past / self._window.devicePixelRatioF())
            self._place(self._revealed().translated(-shift_into(self.edge, step)))
            self._window.changed.emit()

    def _screen_for(self, rect: QRect):
        """Returns the screen showing most of rect."""
        screens = QGuiApplication.screens()
        best = max(screens, key=lambda s: _area_of(s.geometry().intersected(rect)), default=None)
        if best is None or _area_of(best.geometry().intersected(rect)) == 0:
            best = QGuiApplication.screenAt(rect.center()) or QGuiApplication.primaryScreen()
        return best

    def is_hidden(self) -> bool:
        """Returns whether the note is docked and slid in."""
        return bool(self.edge) and not self.shown

    def place_beside(self, width: int, height: int) -> QPoint | None:
        """Returns where a note made from this one opens.

        Args:
            width: The new note's width.
            height: The new note's height.

        Returns:
            Its top-left, next to this note on the inner side of the screen; None if this note
            is not docked.
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
        """Returns the rect other notes snap to, in physical pixels: the window's, docked or not."""
        return win_effects.window_rect(self._hwnd())

    # --- Frosted look --------------------------------------------------------

    def _frost(self, rect: QRect) -> None:
        """Takes a frosted snapshot of what is behind rect, the note's backdrop while solid."""
        hwnds = [self._hwnd()]
        if self._shadow is not None:
            hwnds.append(int(self._shadow.winId()))
        for hwnd in hwnds:
            win_effects.set_capture_excluded(hwnd, True)
        pm = motion.acrylic_stand_in(rect, self._window.theme.acrylic_saturation)
        for hwnd in hwnds:
            win_effects.set_capture_excluded(hwnd, False)
        self._window.backdrop = (QRect(rect), pm) if pm is not None else None
        self._frosted.start()

    def _frost_fresh(self) -> None:
        """Takes a backdrop snapshot of the slid-out place unless one was taken just now."""
        backdrop = self._window.backdrop
        if (backdrop is None or backdrop[0] != self._revealed() or not self._frosted.isValid()
                or self._frosted.elapsed() > FRESH_MS):
            self._frost(self._revealed())

    def _band(self, edge: str) -> QRect:
        """Returns the strip of the screen along edge that a note pushed into it can move in."""
        geo = self._window.geometry()
        area = self._screen_for(geo).availableGeometry()
        if edge == "top":
            return QRect(area.left(), geo.y(), area.width(), geo.height())
        return QRect(geo.x(), area.top(), geo.width(), area.height())

    def _shadow_window(self) -> ShadowWindow:
        """Returns the note's ShadowWindow, creating it on first use."""
        if self._shadow is None:
            self._shadow = ShadowWindow(self._window.theme)
            self._window.destroyed.connect(self._shadow.close)
        return self._shadow

    def _cast_shadow(self) -> None:
        """Casts the shadow of the part of the note on screen, fading as it slides in.

        The ShadowWindow takes over the note's own shadow once the note starts sliding.
        """
        if self._window.solid and self._depth > 0 and not self._casting:
            self._casting = True
        if not (self._window.solid and self._casting):
            if self._shadow is not None:
                self._shadow.cast(QRect(), 0.0, self._hwnd())
            return
        geo = self._window.geometry()
        part = geo.translated(self._window.content_shift).intersected(geo)
        span = hidden_depth(geo, self.edge)
        strength = self._window.windowOpacity() * (1.0 - self._depth / span if span else 1.0)
        self._shadow_window().cast(part, strength, self._hwnd())
        self._window.set_solid(True, shadow=False)

    def _go_solid(self) -> None:
        """Turns the glass into its frosted stand-in, keeping its own shadow until it slides."""
        if not self._window.solid:
            self._window.set_solid(True, shadow=True)
            self._cast_shadow()

    def _show_glass(self) -> None:
        """Gives the note its frosted glass and its own shadow back."""
        if self._window.solid:
            self._casting = False
            self._window.set_solid(False)
            self._cast_shadow()
        else:
            self._window.reapply_glass()

    def _set_opacity(self, opacity: float) -> None:
        """Sets the window opacity; the shadow follows."""
        self._window.setWindowOpacity(opacity)
        self._cast_shadow()

    def window_moved(self) -> None:
        """Keeps the backdrop and the shadow of a solid note in place while it is dragged."""
        if self._window.solid:
            self._window.update()
            self._cast_shadow()

    # --- Docking -------------------------------------------------------------

    def restore(self) -> None:
        """Docks a note saved as docked, slid in, before it is shown."""
        revealed = self._dock_to(self._revealed(), self._window.note.dock)
        if revealed is None:
            self._window.note.dock = ""
            return
        self._place(revealed)
        self.shown = False
        self._frost(revealed)
        self._depth = hidden_depth(revealed, self.edge)
        self._casting = True
        self._window.set_solid(True)
        self._set_depth(self._depth)
        win_effects.set_click_through(self._hwnd(), True)

    def drag_started(self) -> None:
        """Pauses sliding while the note is moved or resized; the note comes out as live glass."""
        self._dragging = True
        self._settle.stop()
        if self._slide_glide.is_running() or self._depth or self._window.solid:
            self._slide_glide.stop()
            self._set_depth(0)
            self._set_opacity(1.0)
            self._show_glass()

    def follow_drag(self, pushed: Stick | None, stuck: list[Stick]) -> None:
        """Shows, while the note is dragged, whether letting go hides it.

        Pushed into an edge, the note eases to HIDDEN_OPACITY; when the push ends, it eases back.

        Args:
            pushed: The edge the note is pushed into, or None.
            stuck: The edges the note sticks to.
        """
        if stuck:
            self._shadow_window()
        edge = pushed.edge if pushed is not None else ""
        if edge == self._pushed:
            return
        self._pushed = edge
        if edge:
            self._frost(self._band(edge))
            self._go_solid()
            self._fade_to(HIDDEN_OPACITY)
        else:
            self._fade_to(1.0, self._show_glass)

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
        self._pushed = ""
        geo = self._window.geometry()
        revealed = self._dock_to(geo, pushed.edge) if pushed is not None else None
        if revealed is not None:
            self._place(revealed)
            self.shown = False
            self._wait_for_leave = True
            self._hold.invalidate()
            win_effects.set_click_through(self._hwnd(), True)
            self._go_solid()
            self._slide(hidden_depth(revealed, self.edge), self._fade_in_place, SLIDE_IN_MS)
            self._window.changed.emit()
            return
        if self._window.solid:
            self._fade_to(1.0, self._show_glass)  # The push ended without hiding the note.
        if not self.edge:
            return
        if moved and not any(stick.edge == self.edge and stick.work == self._work for stick in stuck):
            self._undock()
            return
        revealed = revealed_rect(geo, self._area, self.edge)
        self._set_revealed(revealed)
        self.shown = True
        self._away.invalidate()
        self._settle.start((geo.x(), geo.y()), (revealed.x(), revealed.y()), self._keep_on_monitor)
        self._window.changed.emit()

    def _dock_to(self, rect: QRect, edge: str) -> QRect | None:
        """Docks to an edge of the screen showing most of rect.

        Args:
            rect: Where the note is.
            edge: "left", "right" or "top".

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
        self._window.update_pin_mark()
        self._poll.start()
        return revealed

    def keep_on_top(self) -> None:
        """Puts a docked note back above other windows, e.g. after its flags changed."""
        if self.edge:
            win_effects.set_topmost(self._hwnd(), True)
            win_effects.set_click_through(self._hwnd(), self.is_hidden())
            self._cast_shadow()

    def settle(self) -> None:
        """Fades a note that opened hidden once it has appeared."""
        if self.is_hidden():
            self._shadow_window()
            self._fade_to(HIDDEN_OPACITY)
            self._keep_on_monitor()

    def _undock(self) -> None:
        """Turns the note back into a normal one where it is."""
        if self.edge or self._window.note.dock:
            self.edge, self.shown = "", False
            self._window.note.dock = ""
            self._poll.stop()
            self._slide_glide.stop()
            self._set_depth(0)
            win_effects.set_click_through(self._hwnd(), False)
            self._fade_to(1.0, self._show_glass)
            win_effects.set_topmost(self._hwnd(), self._window.note.pinned)
            self._window.update_pin_mark()
            self._window.changed.emit()

    def validate(self) -> bool:
        """Re-docks to the screen that shows the note after a screen change.

        Returns:
            Whether the note is docked.
        """
        if not self.edge:
            return False
        revealed = self._dock_to(self._revealed(), self.edge)
        self._place(revealed)
        if not self._slide_glide.is_running():
            self._set_depth(0 if self.shown else hidden_depth(revealed, self.edge))
        self._keep_on_monitor()
        return True

    # --- Sliding -------------------------------------------------------------

    def _set_depth(self, depth: int) -> None:
        """Slides the note depth px into its edge within the window."""
        self._depth = depth
        self._window.set_content_shift(shift_into(self.edge, depth))
        self._cast_shadow()

    def _slide_step(self, depth: int, _unused: int) -> None:
        """Moves the note one frame of a slide."""
        self._set_depth(depth)

    def _slide(self, depth: int, on_done=None, duration: int = SLIDE_MS) -> None:
        """Slides the note to depth px into its edge, fully opaque.

        Args:
            depth: How far into the edge it slides.
            on_done: Called once it is there.
            duration: Length of the slide in milliseconds.
        """
        self._slide_glide.duration = duration
        here = self._slide_glide.position() if self._slide_glide.is_running() else (self._depth, 0)
        if self._fade is not None:
            self._fade.stop()
        self._set_opacity(1.0)
        self._slide_glide.start(here, (depth, 0), on_done)

    def _fade_in_place(self) -> None:
        """Eases a note that has slid in to HIDDEN_OPACITY."""
        if self.is_hidden():
            self._fade_to(HIDDEN_OPACITY)

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
            win_effects.set_click_through(self._hwnd(), False)
            self._frost_fresh()
            self._window.update()
            self._window.raise_()
            self.keep_on_top()
            self._window.unsetCursor()
            self._slide(0)

    def hide(self) -> None:
        """Slides a docked note back into its edge."""
        if self.edge and self.shown:
            self.shown = False
            self._hold.invalidate()
            win_effects.set_click_through(self._hwnd(), True)
            if not self._window.solid:  # Live glass after a move.
                self._frost(self._revealed())
                self._go_solid()
            self._slide(hidden_depth(self._revealed(), self.edge), self._fade_in_place, SLIDE_IN_MS)

    def hold_for_snap(self, on: bool) -> None:
        """Keeps a docked note slid out while a dragged note is snapped to it (on)."""
        if on == self._snapped_to:
            return
        self._snapped_to = on
        if on:
            self.reveal()
        else:
            self._away.invalidate()

    def _fade_to(self, opacity: float, on_done=None) -> None:
        """Eases the window opacity to opacity, then calls on_done if given."""
        if self._fade is not None:
            self._fade.stop()
        start = self._window.windowOpacity()

        def step(v: float) -> None:
            self._set_opacity(start + (opacity - start) * v)

        if abs(start - opacity) < 0.01:
            self._set_opacity(opacity)
            if on_done is not None:
                on_done()
            return
        self._fade = motion.Tween(self, FADE_MS, step, on_done)
        self._fade.start()

    def _keeps_out(self, cursor: QPoint) -> bool:
        """Returns whether a revealed note should stay out now."""
        if self._snapped_to or self._window.editing or QApplication.activePopupWidget() is not None:
            return True
        near = self._revealed().adjusted(-KEEP_MARGIN, -KEEP_MARGIN, KEEP_MARGIN, KEEP_MARGIN)
        if near.contains(cursor) or reveal_zone(self._revealed(), self._area, self.edge).contains(cursor):
            self._hold.invalidate()  # From now on, the cursor leaving hides it.
            return True
        return self._hold.isValid() and self._hold.elapsed() < HOLD_MS

    def _check_cursor(self) -> None:
        """Slides the note out or back as the cursor comes to it or leaves it."""
        if self._dragging or not self._window.isVisible():
            return
        cursor = QCursor.pos()
        if not self.shown:
            screen = QGuiApplication.screenAt(cursor)
            on_screen = screen is not None and screen.name() == self._screen_name
            zone = reveal_zone(self._revealed(), self._area, self.edge)
            if zone.adjusted(-APPROACH, -APPROACH, APPROACH, APPROACH).contains(cursor):
                self._frost_fresh()
            if not (on_screen and zone.contains(cursor)):
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
