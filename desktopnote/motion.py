"""Window and row transitions: short, eased fades shared by notes, menus and popups.

Windows drops a window's acrylic while its opacity is below 1, so the transitions work around it:

- Appearing: acrylic windows fade in over a blurred snapshot of what lies behind them
  (BackdropFade), which then cross-fades into the live acrylic; menus simply fade in.
- Disappearing: the real window hides at once and a snapshot "ghost" fades out in its place.
"""
from __future__ import annotations

from PySide6.QtCore import QEasingCurve, QElapsedTimer, QEvent, QObject, QPoint, QRect, QRectF, Qt, QTimer
from PySide6.QtGui import QColor, QGuiApplication, QPainter, QPainterPath, QPixmap
from PySide6.QtWidgets import (
    QAbstractButton,
    QGraphicsBlurEffect,
    QGraphicsOpacityEffect,
    QGraphicsPixmapItem,
    QGraphicsScene,
    QWidget,
)

QWIDGETSIZE_MAX = (1 << 24) - 1

EASE_OUT = QEasingCurve.OutCubic
POP_IN_MS = 160  # Menus and popups.
POP_OUT_MS = 120
NOTE_IN_MS = 280
NOTE_OUT_MS = 200
HANDOVER_MS = 180  # Blurred snapshot -> live acrylic.
CROSSFADE_MS = 250  # Theme or language switch.
ITEM_IN_MS = 200  # Text, todo and reminder rows.
ITEM_OUT_MS = 160
BLUR_RADIUS = 28
FRAME_MS = 16


class Tween(QObject):
    """Eased 0 -> 1 progress, ticked once per frame.

    Unlike QVariantAnimation, which is driven by the wall clock, a hitch only delays the tween:
    each tick advances by at most two frames. A window's first show can stall the GUI thread for
    about 50 ms, which would otherwise swallow the first third of a short transition.

    Attributes:
        duration: Length of the transition in milliseconds; may be changed between runs.
        on_value: Called with the eased value (0..1) on every tick, and once with 0 on start.
        on_done: Optional callable invoked once progress reaches 1.
        curve: The easing curve applied to the linear progress.
        progress: Linear progress, 0..1.
    """

    def __init__(self, parent: QObject, duration: int, on_value, on_done=None, easing=EASE_OUT):
        super().__init__(parent)
        self.duration, self.on_value, self.on_done = duration, on_value, on_done
        self.curve = QEasingCurve(easing)
        self.progress = 0.0
        self._clock = QElapsedTimer()
        self._last = 0
        self._timer = QTimer(self)
        self._timer.setTimerType(Qt.PreciseTimer)
        self._timer.setInterval(FRAME_MS)
        self._timer.timeout.connect(self._tick)

    def start(self) -> None:
        """Restarts the tween from 0, reporting the initial value immediately."""
        self.progress = 0.0
        self._clock.start()
        self._last = 0
        self.on_value(self.curve.valueForProgress(0.0))
        self._timer.start()

    def start_after_first_paint(self, w: QWidget) -> None:
        """Starts the tween once w has painted (and flushed) its first frame."""
        _FirstPaint(w, self)

    def stop(self) -> None:
        """Stops the tween where it is, without calling on_done."""
        self._timer.stop()

    def is_running(self) -> bool:
        """Returns whether the tween is still ticking."""
        return self._timer.isActive()

    def _tick(self) -> None:
        now = self._clock.elapsed()
        dt = min(now - self._last, 2 * FRAME_MS)
        self._last = now
        self.progress = min(1.0, self.progress + dt / self.duration)
        self.on_value(self.curve.valueForProgress(self.progress))
        if self.progress >= 1.0:
            self._timer.stop()
            if self.on_done is not None:
                self.on_done()


def display_frame_ms() -> int:
    """Returns the frame time of the fastest screen in ms, between 4 (240 Hz) and FRAME_MS."""
    rate = max((screen.refreshRate() for screen in QGuiApplication.screens()), default=60.0)
    return max(4, min(FRAME_MS, int(1000 / max(rate, 1.0))))


class WindowGlide(QObject):
    """Glides a window to a target: its offset from the target eases away to nothing.

    The target may move during the glide, and position() is always current. Frames follow the
    display's refresh rate.

    Attributes:
        duration: Length of a glide in milliseconds.
        target: Where the window is gliding to, as (x, y).
    """

    def __init__(self, parent: QObject, duration: int, apply, easing=QEasingCurve.OutQuint):
        """Initialises the glide.

        Args:
            parent: Owner of the glide.
            duration: Length of a glide in milliseconds.
            apply: Called with (x, y) on every frame to move the window.
            easing: The easing curve of the offset.
        """
        super().__init__(parent)
        self.duration, self._apply = duration, apply
        self.target = (0, 0)
        self._offset = (0, 0)  # Offset from the target when the glide began.
        self._on_done = None
        self._curve = QEasingCurve(easing)
        self._clock = QElapsedTimer()
        self._timer = QTimer(self)
        self._timer.setTimerType(Qt.PreciseTimer)
        self._timer.setInterval(FRAME_MS)
        self._timer.timeout.connect(self._tick)

    def start(self, here: tuple[int, int], target: tuple[int, int], on_done=None) -> None:
        """Starts gliding from here to target; on_done, if given, is called once it arrives."""
        self._on_done = on_done
        self._timer.setInterval(display_frame_ms())
        self.target = target
        self._offset = (here[0] - target[0], here[1] - target[1])
        self._clock.start()
        self._timer.start()
        self._apply(*self.position())

    def stop(self) -> None:
        """Stops the glide where it is."""
        if self._timer.isActive():
            here = self.position()
            self._timer.stop()
            self.target, self._offset = here, (0, 0)

    def is_running(self) -> bool:
        """Returns whether a glide is in progress."""
        return self._timer.isActive()

    def position(self) -> tuple[int, int]:
        """Returns the current position: the target plus what is left of the offset."""
        left = 0.0
        if self._timer.isActive():
            progress = self._clock.elapsed() / self.duration
            left = 0.0 if progress >= 1 else 1.0 - self._curve.valueForProgress(progress)
        return round(self.target[0] + self._offset[0] * left), round(self.target[1] + self._offset[1] * left)

    def _tick(self) -> None:
        done = self._clock.elapsed() >= self.duration
        if done:
            self._timer.stop()
        self._apply(*self.position())
        if done and self._on_done is not None:
            self._on_done()


class _FirstPaint(QObject):
    """One-shot event filter that starts a tween after the widget's first paint."""

    def __init__(self, w: QWidget, tween: Tween):
        super().__init__(tween)
        self.tween = tween
        w.installEventFilter(self)

    def eventFilter(self, obj, ev):
        if ev.type() == QEvent.Paint:
            obj.removeEventFilter(self)
            QTimer.singleShot(0, self.tween.start)  # Deferred until this paint has reached the screen.
        return False


# --- Snapshots ---------------------------------------------------------------
def grab_screen(rect: QRect) -> QPixmap | None:
    """Returns what is currently on screen inside rect, as composited by DWM.

    Args:
        rect: The area to capture, in global coordinates.

    Returns:
        The captured pixmap, or None if rect is not on any screen or the grab failed.
    """
    screen = QGuiApplication.screenAt(rect.center())
    if screen is None:
        return None
    origin = screen.geometry().topLeft()
    pm = screen.grabWindow(0, rect.x() - origin.x(), rect.y() - origin.y(), rect.width(), rect.height())
    return None if pm.isNull() else pm


def blurred_backdrop(rect: QRect, tint: QColor, radius: int = BLUR_RADIUS) -> QPixmap | None:
    """Returns a blurred, tinted snapshot of the screen behind rect.

    The result stands in for the system acrylic while a window cannot show it.

    Args:
        rect: The window area, in global coordinates.
        tint: Colour painted over the blurred snapshot.
        radius: Blur radius in device-independent pixels.

    Returns:
        A pixmap the size of rect, or None if the screen could not be grabbed.
    """
    pad = radius * 2  # Blurs from the real surroundings rather than from transparent edges.
    pm = grab_screen(rect.adjusted(-pad, -pad, pad, pad))
    if pm is None:
        return None
    dpr = pm.devicePixelRatio()
    pm.setDevicePixelRatio(1.0)
    item = QGraphicsPixmapItem(pm)
    effect = QGraphicsBlurEffect()
    effect.setBlurRadius(radius * dpr)
    effect.setBlurHints(QGraphicsBlurEffect.QualityHint)
    item.setGraphicsEffect(effect)
    scene = QGraphicsScene()
    scene.addItem(item)
    out = QPixmap(pm.size())
    out.fill(Qt.transparent)
    p = QPainter(out)
    scene.render(p, QRectF(out.rect()), QRectF(pm.rect()))
    p.fillRect(out.rect(), tint)
    p.end()
    pad_px = round(pad * dpr)
    out = out.copy(pad_px, pad_px, pm.width() - 2 * pad_px, pm.height() - 2 * pad_px)
    out.setDevicePixelRatio(dpr)
    return out


# --- Appearing ---------------------------------------------------------------
def pop_in(w: QWidget, dy: int = 6, duration: int = POP_IN_MS, on_done=None) -> None:
    """Fades a top-level window in while it glides dy pixels into place.

    Intended to be called from the window's showEvent.
    """
    stop(w)
    end = w.pos()

    def step(v: float) -> None:
        w.setWindowOpacity(v)
        w.move(end + QPoint(0, round(dy * (1.0 - v))))

    tween = Tween(w, duration, step, on_done)
    step(0.0)
    w._pop_tween = tween
    tween.start_after_first_paint(w)


def stop(w: QWidget) -> None:
    """Drops a pop_in still running on w, e.g. when it closes mid-animation."""
    tween = getattr(w, "_pop_tween", None)
    w._pop_tween = None
    if tween is not None:
        tween.stop()
        tween.deleteLater()


def expand_in(w: QWidget, duration: int = ITEM_IN_MS, on_done=None) -> None:
    """Grows a just-inserted layout widget from zero to its natural height while it fades in."""
    effect = QGraphicsOpacityEffect(w)
    w.setGraphicsEffect(effect)

    def step(v: float) -> None:
        w.setMaximumHeight(round(v * w.sizeHint().height()))
        effect.setOpacity(v)

    def done() -> None:
        w.setMaximumHeight(QWIDGETSIZE_MAX)
        w.setGraphicsEffect(None)
        if on_done is not None:
            on_done()

    step(0.0)
    _run_item_tween(w, Tween(w, duration, step, done))


def _run_item_tween(w: QWidget, tween: Tween) -> None:
    """Runs one row animation at a time: a row removed while still expanding switches over."""
    old = getattr(w, "_item_tween", None)
    if old is not None:
        old.stop()
    w._item_tween = tween
    tween.start()


def fold(w: QWidget, visible: bool, duration: int = ITEM_IN_MS) -> None:
    """Shows or hides a layout widget by animating its height and opacity.

    The widget stays alive. The change is instant while the window is not on screen yet.
    """
    if getattr(w, "_fold_target", not w.isHidden()) == visible:
        return
    w._fold_target = visible
    if not w.window().isVisible():
        w.setVisible(visible)
        return
    start_h = 0 if w.isHidden() else w.height()
    effect = w.graphicsEffect()
    if not isinstance(effect, QGraphicsOpacityEffect):  # Reuses one left by an interrupted fold.
        effect = QGraphicsOpacityEffect(w)
        effect.setOpacity(0.0 if w.isHidden() else 1.0)
        w.setGraphicsEffect(effect)
    start_opacity = effect.opacity()
    w.show()

    def step(v: float) -> None:
        end_h = w.sizeHint().height() if visible else 0
        w.setMaximumHeight(round(start_h + (end_h - start_h) * v))
        effect.setOpacity(start_opacity + ((1.0 if visible else 0.0) - start_opacity) * v)

    def done() -> None:
        w.setGraphicsEffect(None)
        w.setMaximumHeight(QWIDGETSIZE_MAX)
        w.setVisible(visible)

    step(0.0)
    _run_item_tween(w, Tween(w, duration, step, done))


def collapse_out(w: QWidget, duration: int = ITEM_OUT_MS) -> None:
    """Fades a layout widget out while its height shrinks to zero, then deletes it.

    The widget is inert meanwhile; callers drop it from their own bookkeeping at once.
    """
    w.blockSignals(True)
    w.setAttribute(Qt.WA_TransparentForMouseEvents)
    if w.focusWidget() is not None:
        w.focusWidget().clearFocus()
    h = w.height()
    effect = QGraphicsOpacityEffect(w)
    w.setGraphicsEffect(effect)

    def step(v: float) -> None:
        w.setMaximumHeight(round(h * (1.0 - v)))
        effect.setOpacity(1.0 - v)

    _run_item_tween(w, Tween(w, duration, step, w.deleteLater))


class BackdropFade:
    """Blurred, tinted snapshot standing in for a window's acrylic while it fades in.

    Create it before the window is on screen, paint() it under the window's content, and call
    hand_over() once the window is opaque again to cross-fade into the live acrylic.

    Attributes:
        w: The window the backdrop belongs to.
        pixmap: The blurred snapshot, or None once handed over (or if the grab failed).
        alpha: Current opacity of the snapshot, 0..1.
    """

    def __init__(self, w: QWidget, tint: QColor):
        self.w = w
        self.pixmap = blurred_backdrop(w.geometry(), tint)
        self.alpha = 1.0

    def hand_over(self) -> None:
        """Fades the snapshot out over the live acrylic, then releases it."""
        if self.pixmap is not None:
            Tween(self.w, HANDOVER_MS, self._set_alpha, self._done, QEasingCurve.InOutQuad).start()

    def _set_alpha(self, v: float) -> None:
        self.alpha = 1.0 - v
        self.w.update()

    def _done(self) -> None:
        self.pixmap = None
        self.w.update()

    def paint(self, p: QPainter, clip: QPainterPath) -> None:
        """Draws the snapshot at its current opacity, clipped to the window's shape."""
        if self.pixmap is None:
            return
        p.save()
        p.setClipPath(clip)
        p.setOpacity(self.alpha)
        p.drawPixmap(self.w.rect(), self.pixmap)
        p.restore()


# --- Disappearing ------------------------------------------------------------
class Ghost(QWidget):
    """Snapshot of a just-hidden window that shrinks slightly and fades away.

    Ghost windows are pooled and stay shown (fully transparent and click-through) between uses:
    a freshly created window only paints after its first expose, which would leave a blank frame
    between the real window hiding and its ghost appearing.
    """

    _idle: list[Ghost] = []
    _busy: set[Ghost] = set()

    def __init__(self):
        super().__init__(
            None,
            Qt.Tool | Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.NoDropShadowWindowHint
            | Qt.WindowDoesNotAcceptFocus | Qt.WindowTransparentForInput,
        )
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setAttribute(Qt.WA_TransparentForMouseEvents)
        self.setAttribute(Qt.WA_ShowWithoutActivating)
        self._pm: QPixmap | None = None
        self._radius, self._opacity, self._t = 0.0, 1.0, 0.0
        self._shrink = 0.04  # Scale lost by the end of the fade.
        self._tween = Tween(self, POP_OUT_MS, self._step, self._release)
        self.setGeometry(0, 0, 1, 1)
        self.show()

    @classmethod
    def prewarm(cls, count: int = 1) -> None:
        """Creates idle ghosts up front, since a new window only paints after its first expose."""
        while len(cls._idle) < count:
            cls._idle.append(cls())

    @classmethod
    def play(cls, pixmap: QPixmap, geometry: QRect, radius: float, duration: int, opacity: float = 1.0,
             shrink: float = 0.04) -> None:
        """Shows pixmap at geometry and fades it out, using a pooled ghost window.

        Args:
            pixmap: The snapshot to show.
            geometry: Where to show it, in global coordinates.
            radius: Corner radius the snapshot is clipped to.
            duration: Length of the fade in milliseconds.
            opacity: Starting opacity.
            shrink: Fraction of the size lost by the end of the fade.
        """
        ghost = cls._idle.pop() if cls._idle else cls()
        cls._busy.add(ghost)
        ghost._pm, ghost._radius, ghost._opacity, ghost._t, ghost._shrink = pixmap, radius, opacity, 0.0, shrink
        ghost.setGeometry(geometry)
        ghost.raise_()
        ghost.repaint()  # Synchronous, so it is on screen before the real window disappears.
        ghost._tween.duration = duration
        ghost._tween.start()

    @classmethod
    def from_screen(cls, w: QWidget, radius: float, duration: int = NOTE_OUT_MS, shrink: float = 0.04) -> None:
        """Plays a ghost of what is on screen over w, keeping the live acrylic look.

        Must be called before hiding w.
        """
        pm = grab_screen(w.geometry())
        if pm is not None:
            cls.play(pm, w.geometry(), radius, duration, shrink=shrink)

    @classmethod
    def from_render(cls, w: QWidget, radius: float, duration: int = POP_OUT_MS) -> None:
        """Plays a ghost from the widget's own rendering, with no system backdrop involved."""
        cls.play(w.grab(), w.geometry(), radius, duration, w.windowOpacity())

    def _release(self) -> None:
        """Clears the ghost and returns it to the idle pool."""
        self._pm = None
        self.repaint()  # Transparent again.
        self.setGeometry(self.x(), self.y(), 1, 1)
        Ghost._busy.discard(self)
        Ghost._idle.append(self)

    def _step(self, t: float) -> None:
        self._t = t
        self.update()

    def paintEvent(self, _):
        if self._pm is None:
            return
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.setRenderHint(QPainter.SmoothPixmapTransform)
        p.setOpacity(self._opacity * (1.0 - self._t))
        scale = 1.0 - self._shrink * self._t
        rect = QRectF(self.rect())
        p.translate(rect.center())
        p.scale(scale, scale)
        p.translate(-rect.center())
        path = QPainterPath()
        path.addRoundedRect(rect, self._radius, self._radius)
        p.setClipPath(path)
        p.drawPixmap(rect, self._pm, QRectF(self._pm.rect()))


def crossfade(windows: list[tuple[QWidget, float]], apply, duration: int = CROSSFADE_MS) -> None:
    """Changes how windows look with a cross-fade.

    Each visible window is covered by a snapshot of its current look, apply() restyles the
    windows underneath, and the snapshots fade out. Ghosts must have been exposed once to paint on
    their first frame, so missing ones are created first and the switch waits a moment for them.

    Args:
        windows: Pairs of a window and its corner radius.
        apply: Callable that restyles the windows.
        duration: Length of the fade in milliseconds.
    """
    visible = [(w, radius) for w, radius in windows if w.isVisible()]
    missing = len(visible) - len(Ghost._idle)
    for _ in range(max(0, missing)):
        Ghost._idle.append(Ghost())

    def go() -> None:
        for w, radius in visible:
            Ghost.from_screen(w, radius, duration, shrink=0.0)
        apply()

    if missing > 0:
        QTimer.singleShot(40, go)
    else:
        go()


# --- Hover -------------------------------------------------------------------
class HoverFx(QObject):
    """Animated hover background for buttons; style sheets can only switch it instantly.

    A single shared event filter: on Enter/Leave it eases a per-button level 0..1, and on each
    Paint it draws a rounded background at that level *before* the button paints its icon or text
    over it. Buttons opt out with the dynamic property hoverFx=False and choose a corner radius
    with hoverRadius.

    Attributes:
        color: Full-strength hover colour (theme.hover); set by the windows on theme changes.
        IN_MS: Fade-in duration in milliseconds.
        OUT_MS: Fade-out duration in milliseconds.
    """

    color = QColor(0, 0, 0, 15)
    IN_MS, OUT_MS = 120, 180
    _instance: HoverFx | None = None

    @classmethod
    def attach(cls, root: QWidget) -> None:
        """Animates every button inside root, and root itself if it is one."""
        if cls._instance is None:
            cls._instance = cls()
        fx = cls._instance
        for button in [root, *root.findChildren(QAbstractButton)]:
            if (isinstance(button, QAbstractButton) and button.property("hoverFx") is not False
                    and not hasattr(button, "_hover_level")):
                button._hover_level = 0.0
                button._hover_tween = None
                button.installEventFilter(fx)

    def eventFilter(self, obj, ev):
        """Eases the hover level on Enter/Leave and paints the background before the button."""
        kind = ev.type()
        if kind == QEvent.Enter:
            self._to(obj, 1.0, self.IN_MS)
        elif kind == QEvent.Leave:
            self._to(obj, 0.0, self.OUT_MS)
        elif kind == QEvent.Paint and obj._hover_level > 0.0 and obj.isEnabled():
            color = QColor(self.color)
            color.setAlphaF(color.alphaF() * obj._hover_level)
            radius = obj.property("hoverRadius")
            p = QPainter(obj)
            p.setRenderHint(QPainter.Antialiasing)
            p.setPen(Qt.NoPen)
            p.setBrush(color)
            p.drawRoundedRect(QRectF(obj.rect()), radius if radius is not None else 7,
                              radius if radius is not None else 7)
            p.end()
        return False

    @staticmethod
    def _to(b: QAbstractButton, target: float, duration: int) -> None:
        """Eases b's hover level from its current value to target."""
        if b._hover_tween is not None:
            b._hover_tween.stop()
        start = b._hover_level

        def step(v: float) -> None:
            b._hover_level = start + (target - start) * v
            b.update()

        b._hover_tween = Tween(b, duration, step)
        b._hover_tween.start()
