"""Small controls: scroll indicator, segmented control, text-size slider, tag picker, grabber."""
from __future__ import annotations

from PySide6.QtCore import (
    QElapsedTimer,
    QEvent,
    QPoint,
    QPointF,
    QRectF,
    Qt,
    QTimer,
    Signal,
)
from PySide6.QtGui import (
    QColor,
    QConicalGradient,
    QFont,
    QPainter,
    QPen,
)
from PySide6.QtWidgets import (
    QApplication,
    QScrollArea,
    QWidget,
)

from . import motion
from .i18n import tr
from .theme import TAGS, TEXT_SCALES, Theme, css_color, is_hex


# --- Scroll indicator --------------------------------------------------------
class OverlayScrollBar(QWidget):
    """Overlay scroll indicator for a QScrollArea whose own scroll bar is turned off.

    A thin rounded handle floats over the content, so it never takes width (no re-wrap when it
    appears) and never shows up just because the note was resized. It fades in while scrolling,
    widens under the pointer, can be dragged, and fades out shortly after scrolling stops.

    It also scrolls the area smoothly: the wheel, a click on the track and ensure_visible() glide
    the content and the handle to their new position; dragging the handle follows the pointer.

    Attributes:
        area: The scroll area this indicator belongs to.
        theme: The theme used for drawing.
        bar: The area's (hidden) vertical scroll bar, which holds the scroll state.
    """

    W = 10  # Hit width; the handle is drawn thinner, against the right edge.
    THIN, THICK = 4.0, 6.0
    MARGIN = 2
    MIN_HANDLE = 28
    HOLD_MS, FADE_IN_MS, FADE_OUT_MS = 900, 120, 260
    SCROLL_MS = 220
    LINE_PX = 20  # One wheel line; a notch scrolls QApplication.wheelScrollLines() lines.
    VISIBLE_MARGIN = 12  # Room kept around a row that ensure_visible() brings into view.

    def __init__(self, area: QScrollArea, theme: Theme):
        """Attaches the indicator to ``area``, whose own vertical scroll bar should be off."""
        super().__init__(area)
        self.area, self.theme = area, theme
        self.bar = area.verticalScrollBar()
        self._level = 0.0  # Visibility.
        self._hot = 0.0  # Pointer over it or dragging: wider and darker.
        self._fx: dict[str, motion.Tween] = {}
        self._drag: float | None = None  # Pointer offset inside the handle.
        self._glide = motion.WindowGlide(self, self.SCROLL_MS, self._glide_step, motion.EASE_OUT)
        self._gliding_value = False  # True while the glide itself sets the bar's value.
        self._resized = QElapsedTimer()
        self._resized.start()
        self._hold = QTimer(self, singleShot=True, interval=self.HOLD_MS)
        self._hold.timeout.connect(self._fade_out)
        self.setCursor(Qt.ArrowCursor)
        self.setAttribute(Qt.WA_TransparentForMouseEvents)  # Until it is shown.
        self.bar.valueChanged.connect(self._scrolled)
        self.bar.rangeChanged.connect(lambda *_: self._place())
        area.installEventFilter(self)
        area.viewport().installEventFilter(self)
        self._place()

    # --- Visibility ----------------------------------------------------------
    def _to(self, name: str, target: float, duration: int) -> None:
        """Eases the level attribute ``_<name>`` ("level" or "hot") to ``target``.

        The indicator only takes mouse input while it is at least half visible.
        """
        if name in self._fx:
            self._fx.pop(name).stop()
        attr = "_" + name
        start = getattr(self, attr)
        if start == target:
            return

        def step(v: float) -> None:
            setattr(self, attr, start + (target - start) * v)
            self.setAttribute(Qt.WA_TransparentForMouseEvents, self._level < 0.5)
            self.update()

        self._fx[name] = motion.Tween(self, duration, step)
        self._fx[name].start()

    def flash(self) -> None:
        """Fades the indicator in and restarts the hold timer, if there is anything to scroll."""
        if self.bar.maximum() <= 0:
            return
        self._to("level", 1.0, self.FADE_IN_MS)
        self._hold.start()

    def _fade_out(self) -> None:
        """Fades the indicator out unless it is being dragged or hovered."""
        if self._drag is None and not self.underMouse():
            self._to("level", 0.0, self.FADE_OUT_MS)
            self._to("hot", 0.0, self.FADE_OUT_MS)

    def _scrolled(self, _value: int) -> None:
        """Flashes the indicator on a scroll, but not on value changes caused by a resize.

        A value set by anything but the glide (dragging, keyboard focus) stops the glide.
        """
        if not self._gliding_value:
            self._glide.stop()
        if self._resized.elapsed() > 200:  # A resize clamping the value is not scrolling.
            self.flash()
        self.update()

    # --- Smooth scrolling ----------------------------------------------------
    def scroll_to(self, value: float) -> None:
        """Glides the content to scroll position ``value`` (not below 0).

        A target beyond the current range is kept, so while the content is still growing (a row
        expanding at the end) the view follows it down.
        """
        value = max(0.0, value)
        here = self.bar.value()
        heading = self._glide.target[1] if self._glide.is_running() else here
        if round(value) == round(heading):  # Already there, or already on the way.
            return
        self._glide.start((0, here), (0, value))

    def scroll_by(self, delta: float) -> None:
        """Glides the content ``delta`` pixels on from where it is heading, within the range."""
        base = self._glide.target[1] if self._glide.is_running() else self.bar.value()
        self.scroll_to(min(max(0.0, base + delta), self.bar.maximum()))

    def ensure_visible(self, w: QWidget, height: int | None = None) -> None:
        """Glides the content so that ``w`` is in view, with a little room around it.

        Args:
            w: A widget inside the area's content.
            height: The height ``w`` will have, if it is still growing; defaults to its height.
        """
        content = self.area.widget()
        top = w.mapTo(content, QPoint(0, 0)).y()
        h = w.height() if height is None else height
        view = self.area.viewport().height()
        margin = max(0, min(self.VISIBLE_MARGIN, (view - h) // 2))
        base = self._glide.target[1] if self._glide.is_running() else self.bar.value()
        value = base
        if top + h + margin > value + view:
            value = top + h + margin - view
        if top - margin < value:  # A row taller than the view shows its top.
            value = top - margin
        self.scroll_to(value)

    def _wheel(self, e) -> None:
        """Turns a wheel or touchpad event into a glide; a notch scrolls at most one page."""
        if self._drag is not None:
            return
        if not e.pixelDelta().isNull():
            delta = -e.pixelDelta().y()
        else:
            step = min(max(1, QApplication.wheelScrollLines()) * self.LINE_PX, self.bar.pageStep())
            delta = -e.angleDelta().y() / 120 * step
        if delta:
            self.flash()
            self.scroll_by(delta)

    def _glide_step(self, _x: int, y: int) -> None:
        """Sets the bar's value for one frame of the glide."""
        self._gliding_value = True
        try:
            self.bar.setValue(y)
        finally:
            self._gliding_value = False

    def _place(self) -> None:
        """Places the indicator along the area's right edge; visible only when it can scroll."""
        area = self.area
        self.setGeometry(area.width() - self.W, 0, self.W, area.height())
        self.raise_()
        self.setVisible(self.bar.maximum() > 0)
        self.update()

    def eventFilter(self, obj, ev):
        """Re-places the indicator on area resizes and scrolls smoothly on wheel events."""
        kind = ev.type()
        if kind == QEvent.Resize and obj is self.area:
            self._resized.restart()
            self._place()
        elif kind == QEvent.Wheel:
            self._wheel(ev)
            return True  # The area's own wheel handling would jump.
        return False

    # --- Geometry ------------------------------------------------------------
    def _handle(self) -> QRectF:
        """Returns the handle rectangle for the current scroll position and hover level."""
        track = self.height() - 2 * self.MARGIN
        page, top = self.bar.pageStep(), self.bar.maximum()
        length = max(self.MIN_HANDLE, track * page / max(1, page + top))
        y = self.MARGIN + (track - length) * (self.bar.value() / top if top else 0.0)
        w = self.THIN + (self.THICK - self.THIN) * self._hot
        return QRectF(self.width() - self.MARGIN - w, y, w, length)

    # --- Events --------------------------------------------------------------
    def enterEvent(self, e):
        self._to("hot", 1.0, motion.HoverFx.IN_MS)
        self.flash()

    def leaveEvent(self, e):
        if self._drag is None:
            self._to("hot", 0.0, motion.HoverFx.OUT_MS)
        self._hold.start()

    def mousePressEvent(self, e):
        """Starts dragging on the handle; elsewhere on the track, scrolls one page towards it."""
        if e.button() != Qt.LeftButton:
            return
        handle = self._handle()
        y = e.position().y()
        if handle.top() <= y <= handle.bottom():
            self._glide.stop()
            self._drag = y - handle.top()
        else:  # On the track: one page towards the pointer.
            self.scroll_by(self.bar.pageStep() if y > handle.bottom() else -self.bar.pageStep())
        self.flash()

    def mouseMoveEvent(self, e):
        if self._drag is None:
            return
        track = self.height() - 2 * self.MARGIN - self._handle().height()
        fraction = (e.position().y() - self._drag - self.MARGIN) / max(1.0, track)
        self.bar.setValue(round(min(1.0, max(0.0, fraction)) * self.bar.maximum()))

    def mouseReleaseEvent(self, e):
        self._drag = None
        if not self.underMouse():
            self._to("hot", 0.0, motion.HoverFx.OUT_MS)
        self._hold.start()

    def paintEvent(self, _):
        if self._level <= 0.0 or self.bar.maximum() <= 0:
            return
        theme = self.theme
        idle, hot = QColor(theme.tertiary), QColor(theme.secondary)
        color = QColor(*(round(a + (b - a) * self._hot) for a, b in zip(idle.getRgb()[:3], hot.getRgb()[:3])))
        color.setAlphaF((0.55 + 0.35 * self._hot) * self._level)
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.setPen(Qt.NoPen)
        p.setBrush(color)
        handle = self._handle()
        p.drawRoundedRect(handle, handle.width() / 2, handle.width() / 2)


# --- Segmented control -------------------------------------------------------
class SegmentedControl(QWidget):
    """Segmented control for one-of-several settings in the tray menu.

    Draws a rounded track, a raised pill that eases to the chosen segment, thin dividers that
    fade out next to the pill, and a soft hover on the other segments.

    Attributes:
        theme: The theme used for drawing.
        options: ``(key, label)`` pairs, one per segment.
        keys: The option keys, in order.
        index: Index of the chosen segment.
        pos_f: Pill position in segments, eased towards ``index``.
        hover: Index of the segment under the cursor, or None.

    Signals:
        changed: Emitted with the key of the segment the user chose.
    """

    changed = Signal(str)

    W, H = 248, 40
    # Track inset: the left edge lines up with the menu icons' left, the right with the switches'.
    INSET_L, INSET_R = 14, 14
    TRACK_H, RADIUS = 28, 8
    SLIDE_MS = 220

    def __init__(self, theme: Theme, options: list[tuple[str, str]], current: str, parent=None):
        """Builds the control.

        Args:
            theme: The theme to draw with.
            options: ``(key, label)`` pairs, one per segment.
            current: Key of the initially chosen segment; the first one if unknown.
            parent: Optional parent widget.
        """
        super().__init__(parent)
        self.theme = theme
        self.options = options
        self.keys = [k for k, _ in options]
        self.index = self.keys.index(current) if current in self.keys else 0
        self.pos_f = float(self.index)  # Pill position in segments, eased.
        self._tween: motion.Tween | None = None
        self.hover: int | None = None
        self._hover_level = 0.0
        self._hover_tween: motion.Tween | None = None
        self.setFixedSize(self.W, self.H)
        self.setMouseTracking(True)
        self.setCursor(Qt.PointingHandCursor)

    # --- State ---------------------------------------------------------------
    def set_options(self, options: list[tuple[str, str]]) -> None:
        """Replaces the segment labels (on a language switch); the keys stay the same."""
        self.options = options
        self.update()

    def set_current(self, key: str, animate: bool = True) -> None:
        """Selects the segment for ``key``, sliding the pill there unless ``animate`` is False."""
        if key not in self.keys:
            return
        i = self.keys.index(key)
        if i == self.index:
            return
        self.index = i
        if not animate:
            self.pos_f = float(i)
            self.update()
            return
        if self._tween is not None:
            self._tween.stop()
        start = self.pos_f

        def step(v: float) -> None:
            self.pos_f = start + (i - start) * v
            self.update()

        self._tween = motion.Tween(self, self.SLIDE_MS, step)
        self._tween.start()

    # --- Geometry ------------------------------------------------------------
    def _track(self) -> QRectF:
        """Returns the rounded track rectangle."""
        return QRectF(self.INSET_L, (self.H - self.TRACK_H) / 2, self.W - self.INSET_L - self.INSET_R, self.TRACK_H)

    def _seg_w(self) -> float:
        """Returns the width of one segment."""
        return self._track().width() / len(self.keys)

    def _index_at(self, x: float) -> int | None:
        """Returns the segment index at ``x``, or None outside the track."""
        track = self._track()
        if not track.left() <= x < track.right():
            return None
        return min(len(self.keys) - 1, int((x - track.left()) / self._seg_w()))

    # --- Events --------------------------------------------------------------
    def mouseMoveEvent(self, e):
        i = self._index_at(e.position().x())
        if i != self.hover:
            self.hover = i
            if self._hover_tween is not None:
                self._hover_tween.stop()
            if i is not None:
                self._hover_tween = motion.Tween(self, motion.HoverFx.IN_MS, self._set_hover)
                self._hover_tween.start()
            self.update()

    def _set_hover(self, v: float) -> None:
        self._hover_level = v
        self.update()

    def leaveEvent(self, e):
        self.hover = None
        self.update()

    def mouseReleaseEvent(self, e):
        i = self._index_at(e.position().x())
        if i is not None and i != self.index:
            self.set_current(self.keys[i])
            self.changed.emit(self.keys[i])

    # --- Painting ------------------------------------------------------------
    def paintEvent(self, _):
        theme = self.theme
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        track = self._track()
        w = self._seg_w()
        p.setPen(Qt.NoPen)
        p.setBrush(css_color(theme.seg_track))
        p.drawRoundedRect(track, self.RADIUS, self.RADIUS)

        if self.hover is not None and self.hover != self.index:
            color = css_color(theme.hover)
            color.setAlphaF(color.alphaF() * self._hover_level)
            p.setBrush(color)
            hover_rect = QRectF(track.left() + self.hover * w, track.top(), w, track.height()).adjusted(2, 2, -2, -2)
            p.drawRoundedRect(hover_rect, self.RADIUS - 2, self.RADIUS - 2)

        divider = css_color(theme.separator)
        for i in range(1, len(self.keys)):  # Dividers fade out where the pill is next to them.
            near = min(abs(i - self.pos_f), abs(i - self.pos_f - 1))  # Distance to the pill's edges.
            alpha = min(1.0, near * 2)
            if alpha > 0:
                color = QColor(divider)
                color.setAlphaF(color.alphaF() * alpha)
                p.fillRect(QRectF(track.left() + i * w - 0.5, track.center().y() - 7, 1, 14), color)

        pill = QRectF(track.left() + self.pos_f * w, track.top(), w, track.height()).adjusted(2, 2, -2, -2)
        for grow, alpha in ((1.5, 10), (0.5, 18)):  # Soft shadow under the raised pill.
            p.setBrush(QColor(0, 0, 0, alpha))
            radius = self.RADIUS - 2 + grow
            p.drawRoundedRect(pill.adjusted(-grow, -grow + 1, grow, grow + 1), radius, radius)
        p.setBrush(css_color(theme.seg_pill))
        p.drawRoundedRect(pill, self.RADIUS - 2, self.RADIUS - 2)

        normal = QFont(self.font())
        normal.setPointSizeF(9)
        bold = QFont(normal)
        bold.setWeight(QFont.DemiBold)
        p.setPen(QColor(theme.text))
        for i, (_, text) in enumerate(self.options):
            p.setFont(bold if i == self.index else normal)
            p.drawText(QRectF(track.left() + i * w, track.top(), w, track.height() - 1), Qt.AlignCenter, text)


# --- Text size ---------------------------------------------------------------
class TextSizeSlider(QWidget):
    """Text size slider for the tray menu.

    Shows a small A, a track with one tick per step and a round white knob, and a large A.
    A press anywhere on the bar grabs the knob, which then follows the pointer; the size changes
    only when the knob is close to a tick, so a hand resting between two ticks cannot flip it.
    On release the knob eases onto the nearest step. The mouse wheel moves one step per full
    notch. The knob grows a little under the cursor and a little more while held, both eased.

    Attributes:
        theme: The theme used for drawing.
        index: Index into ``TEXT_SCALES`` of the current step.
        pos_f: Knob position in steps, eased or following the pointer.

    Signals:
        changed: Emitted with the scale of each newly reached step.
    """

    changed = Signal(float)

    W, H = 248, 40
    LEFT, RIGHT = 37, 42  # Track inset from each side, where the A's sit (equal gaps, measured).
    KNOB = 22
    HOVER_GROW, GRAB_GROW = 0.08, 0.16  # Knob scale added on hover and while held.
    GRAB_MS = 140
    SETTLE_MS = 180  # The knob easing onto a step.

    def __init__(self, theme: Theme, scale: float, parent=None):
        """Builds the slider at the ``TEXT_SCALES`` step nearest to ``scale``."""
        super().__init__(parent)
        self.theme = theme
        self.index = min(range(len(TEXT_SCALES)), key=lambda i: abs(TEXT_SCALES[i] - scale))
        self.pos_f = float(self.index)  # Knob position in steps, eased.
        self._tween: motion.Tween | None = None
        self._grab: float | None = None  # Pointer offset from the knob centre while dragging.
        self._wheel = 0  # Wheel delta not yet turned into a step.
        self._knob_hover = self._knob_held = 0.0  # Eased levels.
        self._fx: dict[str, motion.Tween] = {}
        self.setFixedSize(self.W, self.H)
        self.setMouseTracking(True)
        self.setCursor(Qt.PointingHandCursor)
        self.setAccessibleName(tr("settings.text_size"))

    def _x(self, pos: float) -> float:
        """Returns the x coordinate of a position on the track, measured in steps."""
        return self.LEFT + (self.W - self.LEFT - self.RIGHT) * pos / (len(TEXT_SCALES) - 1)

    def _pos_at(self, x: float) -> float:
        """Returns the track position in steps at ``x``, clamped to the track."""
        span = self.W - self.LEFT - self.RIGHT
        return min(len(TEXT_SCALES) - 1.0, max(0.0, (x - self.LEFT) / span * (len(TEXT_SCALES) - 1)))

    def _on_knob(self, x: float) -> bool:
        """Returns whether ``x`` is on the knob, with 2 px of slack."""
        return abs(x - self._x(self.pos_f)) <= self.KNOB / 2 + 2

    def _level_to(self, name: str, target: float, duration: int) -> None:
        """Eases the knob level ``_knob_<name>`` ("hover" or "held") to ``target``."""
        if name in self._fx:
            self._fx.pop(name).stop()
        attr = "_knob_" + name
        start = getattr(self, attr)
        if start == target:
            return

        def step(v: float) -> None:
            setattr(self, attr, start + (target - start) * v)
            self.update()

        self._fx[name] = motion.Tween(self, duration, step)
        self._fx[name].start()

    def _reach(self, index: int) -> None:
        """Makes ``index`` the current step, emitting ``changed`` if it is new."""
        if index != self.index:
            self.index = index
            self.changed.emit(TEXT_SCALES[index])

    def _ease_to(self, index: int) -> None:
        """Eases the knob onto step ``index`` and makes it the current step."""
        if self._tween is not None:
            self._tween.stop()
        start = self.pos_f

        def step(v: float) -> None:
            self.pos_f = start + (index - start) * v
            self.update()

        self._tween = motion.Tween(self, self.SETTLE_MS, step)
        self._tween.start()
        self._reach(index)

    STICKY = 0.3  # While dragging, a step is taken only within this distance (in steps) of its tick.

    def _follow(self, x: float) -> None:
        """Moves the knob with the pointer, taking a step only when it is clearly at a tick.

        This keeps a hand resting between two ticks from flipping the size back and forth.
        """
        self.pos_f = self._pos_at(x - self._grab)
        nearest = round(self.pos_f)
        if abs(self.pos_f - nearest) <= self.STICKY:
            self._reach(nearest)
        self.update()

    def mousePressEvent(self, e):
        if e.button() != Qt.LeftButton:
            return
        if self._tween is not None:
            self._tween.stop()
        x = e.position().x()
        # On the knob, the grip offset is kept. Elsewhere on the bar, the knob jumps under the
        # pointer and the same press keeps dragging it.
        self._grab = x - self._x(self.pos_f) if self._on_knob(x) else 0.0
        self.setCursor(Qt.ClosedHandCursor)
        self._level_to("held", 1.0, self.GRAB_MS)
        self._follow(x)

    def mouseMoveEvent(self, e):
        x = e.position().x()
        if self._grab is None:  # Just hovering: an open hand and a slight lift over the knob.
            over_knob = self._on_knob(x)
            self.setCursor(Qt.OpenHandCursor if over_knob else Qt.PointingHandCursor)
            self._level_to("hover", 1.0 if over_knob else 0.0,
                           motion.HoverFx.IN_MS if over_knob else motion.HoverFx.OUT_MS)
            return
        self._follow(x)

    def mouseReleaseEvent(self, e):
        if self._grab is not None:
            self._grab = None
            self._ease_to(round(self.pos_f))
            self._level_to("held", 0.0, self.GRAB_MS)
            self.setCursor(Qt.OpenHandCursor if self.rect().contains(e.position().toPoint()) else Qt.PointingHandCursor)

    def leaveEvent(self, e):
        if self._grab is None:
            self._level_to("hover", 0.0, motion.HoverFx.OUT_MS)

    def wheelEvent(self, e):
        """Takes one step per full wheel notch (120 units of accumulated delta).

        Touchpads send many small deltas, which would otherwise each jump a whole size.
        """
        dy = e.angleDelta().y()
        if dy and self._wheel and (dy > 0) != (self._wheel > 0):
            self._wheel = 0  # The direction changed, so accumulation starts over.
        self._wheel += dy
        while abs(self._wheel) >= 120:
            step = 1 if self._wheel > 0 else -1
            self._wheel -= 120 * step
            self._ease_to(min(len(TEXT_SCALES) - 1, max(0, self.index + step)))
        e.accept()

    def paintEvent(self, _):
        theme = self.theme
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        cy = self.H / 2
        p.setPen(QColor(theme.secondary))
        # The A's span the same edges as the segmented tracks and the switch (measured); the
        # large glyph sits low, so it is raised to centre on the track.
        labels = (
            (8.5, QRectF(SegmentedControl.INSET_L - 0.5, 0, 20, self.H), Qt.AlignLeft),
            (14.5, QRectF(self.W - 30, -1.5, 30 - SegmentedControl.INSET_R + 1.5, self.H), Qt.AlignRight),
        )
        for size, rect, align in labels:
            font = QFont(self.font())
            font.setPointSizeF(size)
            p.setFont(font)
            p.drawText(rect, align | Qt.AlignVCenter, "A")
        track = QColor(theme.tertiary)
        track.setAlphaF(0.45)
        x0, x1 = self._x(0), self._x(len(TEXT_SCALES) - 1)
        p.setPen(Qt.NoPen)
        p.setBrush(track)
        p.drawRoundedRect(QRectF(x0, cy - 1.5, x1 - x0, 3), 1.5, 1.5)
        for i in range(len(TEXT_SCALES)):  # One tick per step.
            p.drawRoundedRect(QRectF(self._x(i) - 1, cy - 5, 2, 10), 1, 1)
        knob = QPointF(self._x(self.pos_f), cy)
        radius = self.KNOB / 2 * (1 + self.HOVER_GROW * self._knob_hover + self.GRAB_GROW * self._knob_held)
        lift = self._knob_held  # While held, the shadow spreads and drops a little, as if picked up.
        for grow, alpha in ((2.0 + 2 * lift, 14 + 6 * lift), (1.0 + lift, 22 + 6 * lift)):  # Soft shadow.
            p.setBrush(QColor(0, 0, 0, round(alpha)))
            p.drawEllipse(knob + QPointF(0, 1 + lift), radius + grow * 0.5, radius + grow * 0.5)
        p.setBrush(QColor(255, 255, 255))
        p.setPen(QPen(QColor(0, 0, 0, 25), 0.5))
        p.drawEllipse(knob, radius, radius)


# --- Tag picker --------------------------------------------------------------
class TagPicker(QWidget):
    """One row of colour dots (without names) for choosing a note's tag.

    The current tag gets a ring; hovering fades in a lighter ring. The last dot, a colour wheel,
    picks any colour and shows the custom colour in its centre once one is chosen.

    Attributes:
        theme: The theme used for drawing.
        custom: The current custom colour as "#RRGGBB", or None.
        current: The current tag key, or ``CUSTOM``.
        keys: The tag keys in display order, ending with ``CUSTOM``.
        hover: Index of the dot under the cursor, or None.

    Signals:
        picked: Emitted with the clicked ``TAGS`` key, or ``CUSTOM``.
    """

    picked = Signal(str)
    CUSTOM = "custom"

    DOT, GAP, PAD = 16, 12, 18

    def __init__(self, theme: Theme, current: str, parent=None):
        """Builds the picker; ``current`` is a ``TAGS`` key or a "#RRGGBB" custom colour."""
        super().__init__(parent)
        self.theme = theme
        self.custom = current.upper() if is_hex(current) else None
        self.current = self.CUSTOM if self.custom else current
        self.keys = [*TAGS, self.CUSTOM]
        self.hover: int | None = None
        self._level = 0.0
        self._hover_tween = motion.Tween(self, motion.HoverFx.IN_MS, self._set_level)
        self.setMouseTracking(True)
        self.setCursor(Qt.PointingHandCursor)
        count = len(self.keys)
        self.setFixedSize(2 * self.PAD + count * self.DOT + (count - 1) * self.GAP, self.DOT + 18)

    def _dot(self, i: int) -> QRectF:
        """Returns the rectangle of dot ``i``."""
        return QRectF(self.PAD + i * (self.DOT + self.GAP), (self.height() - self.DOT) / 2, self.DOT, self.DOT)

    def _index_at(self, pos) -> int | None:
        """Returns the index of the dot whose padded hit area contains ``pos``, or None."""
        for i in range(len(self.keys)):
            if self._dot(i).adjusted(-self.GAP / 2, -6, self.GAP / 2, 6).contains(pos):
                return i
        return None

    def mouseMoveEvent(self, e):
        i = self._index_at(e.position())
        if i != self.hover:
            self.hover = i
            if i is not None:  # The hover ring fades in on each new dot.
                self._hover_tween.start()
            self.update()

    def _set_level(self, v: float) -> None:
        self._level = v
        self.update()

    def leaveEvent(self, e):
        self.hover = None
        self.update()

    def mouseReleaseEvent(self, e):
        i = self._index_at(e.position())
        if i is not None:
            self.picked.emit(self.keys[i])

    def paintEvent(self, _):
        theme = self.theme
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        for i, key in enumerate(self.keys):
            dot = self._dot(i)
            color = TAGS.get(key)
            if key == self.CUSTOM:  # A soft colour wheel; a chosen custom colour sits inside.
                wheel = QConicalGradient(dot.center(), 90)
                for j in range(7):
                    wheel.setColorAt(j / 6, QColor.fromHsvF(min(j / 6, 0.999), 0.45, 0.97))
                p.setPen(QPen(QColor(0, 0, 0, 30), 1))
                p.setBrush(wheel)
                p.drawEllipse(dot)
                if self.custom:
                    p.setBrush(QColor(self.custom))
                    p.drawEllipse(dot.adjusted(4, 4, -4, -4))
            elif color is None:  # Untagged: a quiet ring.
                p.setPen(QPen(QColor(theme.tertiary), 1.5))
                p.setBrush(Qt.NoBrush)
                p.drawEllipse(dot.adjusted(1, 1, -1, -1))
            else:
                p.setPen(QPen(QColor(0, 0, 0, 30), 1))  # Keeps the pale cream visible.
                p.setBrush(QColor(color))
                p.drawEllipse(dot)
            ring = dot.adjusted(-3.5, -3.5, 3.5, 3.5)
            if key == self.current:
                p.setPen(QPen(QColor(theme.text), 1.5))
                p.setBrush(Qt.NoBrush)
                p.drawEllipse(ring)
            elif i == self.hover:
                ring_color = QColor(theme.tertiary)
                ring_color.setAlphaF(self._level)
                p.setPen(QPen(ring_color, 1.5))
                p.setBrush(Qt.NoBrush)
                p.drawEllipse(ring)


# --- Grabber -----------------------------------------------------------------
class Grabber(QWidget):
    """Pill-shaped drag handle; dragging it moves the whole note window.

    The pill is a translucent shade over the glass and its tag colour. Hovered or held, it grows
    wider and stronger.

    Attributes:
        theme: The theme used for drawing.
        level: Hover level, eased from 0 to 1.
    """

    def __init__(self, theme: Theme, parent=None):
        super().__init__(parent)
        self.theme = theme
        self.level = 0.0
        self._tween: motion.Tween | None = None
        self.setFixedSize(120, 18)
        self.setCursor(Qt.OpenHandCursor)

    def _hover_to(self, target: float, duration: int) -> None:
        """Eases the hover level to ``target`` over ``duration`` milliseconds."""
        if self._tween is not None:
            self._tween.stop()
        start = self.level

        def step(v: float) -> None:
            self.level = start + (target - start) * v
            self.update()

        self._tween = motion.Tween(self, duration, step)
        self._tween.start()

    def enterEvent(self, e):
        self._hover_to(1.0, motion.HoverFx.IN_MS)

    def leaveEvent(self, e):
        self._hover_to(0.0, motion.HoverFx.OUT_MS)

    def mousePressEvent(self, e):
        """Starts a native system move of the window, which is smooth and works across monitors."""
        if e.button() == Qt.LeftButton:
            handle = self.window().windowHandle()
            if handle is not None:
                handle.startSystemMove()
            e.accept()

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        level = self.level
        w = 36 + 8 * level
        idle, hot = self.theme.pill_alpha
        color = QColor(self.theme.pill_shade)
        color.setAlphaF(idle + (hot - idle) * level)
        rect = QRectF((self.width() - w) / 2, 6, w, 5)
        p.setPen(Qt.NoPen)
        p.setBrush(color)
        p.drawRoundedRect(rect, 2.5, 2.5)
