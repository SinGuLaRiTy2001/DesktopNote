"""Glass popups: the tooltip, and the date/time and colour pickers on the popup base."""
from __future__ import annotations

import math
from datetime import date, datetime, timedelta

from PySide6.QtCore import (
    QEvent,
    QPoint,
    QPointF,
    QRect,
    QRectF,
    QRegularExpression,
    QSize,
    Qt,
    QTime,
    Signal,
)
from PySide6.QtGui import (
    QColor,
    QCursor,
    QFont,
    QGuiApplication,
    QLinearGradient,
    QPainter,
    QPainterPath,
    QPen,
    QPixmap,
    QRegion,
    QRegularExpressionValidator,
)
from PySide6.QtWidgets import (
    QAbstractSpinBox,
    QApplication,
    QFrame,
    QGraphicsOpacityEffect,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMenu,
    QPushButton,
    QTimeEdit,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from . import icons, motion, win_effects
from .dates import default_due, half_hour_step
from .i18n import month_fields, tr
from .theme import Theme, css_color, is_hex, popup_qss


# --- Tooltips ----------------------------------------------------------------
class GlassToolTip(QWidget):
    """The app's tooltip: a small rounded label in the menu colours that eases in and out.

    Replaces Qt's native tooltip, whose fade flickers (blank box, half-drawn text, popping
    shadow) over the app's glass windows. `install` routes every widget's tooltip here.

    Attributes:
        theme: The theme used for drawing; set by the app on start and on theme changes.
        label: The label holding the tooltip text.
        owner: The widget whose tooltip is showing, or None.
    """

    DELAY_OUT_MS = 120
    OFFSET = QPoint(2, 22)  # Below the cursor.

    _instance: GlassToolTip | None = None
    theme: Theme | None = None

    def __init__(self):
        super().__init__(None, Qt.ToolTip | Qt.FramelessWindowHint | Qt.NoDropShadowWindowHint
                         | Qt.WindowDoesNotAcceptFocus | Qt.WindowTransparentForInput)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setAttribute(Qt.WA_ShowWithoutActivating)
        self.setAttribute(Qt.WA_TransparentForMouseEvents)
        self.label = QLabel(self)
        self.label.setTextFormat(Qt.PlainText)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(8, 4, 8, 5)  # Uneven because the glyphs sit low.
        lay.addWidget(self.label)
        self.owner: QWidget | None = None
        self._tween: motion.Tween | None = None

    @classmethod
    def install(cls, app: QApplication, theme: Theme) -> None:
        """Sets the tooltip theme and, on first call, installs the app-wide tooltip filter."""
        cls.theme = theme
        if cls._instance is None:
            cls._instance = cls()
            app.installEventFilter(cls._instance)

    # --- Showing -------------------------------------------------------------
    def _fade(self, target: float, duration: int, on_done=None) -> None:
        """Eases the window opacity to ``target``, then calls ``on_done`` if given."""
        if self._tween is not None:
            self._tween.stop()
        start = self.windowOpacity()

        def step(v: float) -> None:
            self.setWindowOpacity(start + (target - start) * v)

        self._tween = motion.Tween(self, duration, step, on_done)
        self._tween.start()

    def show_for(self, owner: QWidget, text: str) -> None:
        """Shows ``text`` near the cursor for ``owner``, kept on screen, and fades it in."""
        theme = self.theme
        if theme is not None:
            self.label.setStyleSheet(f"color: {theme.text}; font-size: 8.5pt;")
        self.owner = owner
        self.label.setText(text)
        self.adjustSize()
        pos = QCursor.pos() + self.OFFSET
        screen = QGuiApplication.screenAt(QCursor.pos()) or QGuiApplication.primaryScreen()
        area = screen.availableGeometry()
        if pos.y() + self.height() > area.bottom():
            pos.setY(QCursor.pos().y() - self.height() - 6)
        pos.setX(max(area.left(), min(pos.x(), area.right() - self.width())))
        self.move(pos)
        if not self.isVisible():
            self.setWindowOpacity(0.0)
            self.show()
        self.raise_()
        self.update()
        self._fade(1.0, motion.POP_IN_MS)

    def hide_tip(self) -> None:
        """Fades the tooltip out and hides it."""
        self.owner = None
        if self.isVisible():
            self._fade(0.0, self.DELAY_OUT_MS, self.hide)

    # --- Routing -------------------------------------------------------------
    def eventFilter(self, obj, ev):
        """Shows this tooltip in place of Qt's and hides it when its owner is left or used."""
        kind = ev.type()
        if kind == QEvent.ToolTip and isinstance(obj, QWidget) and obj.window() is not self:
            text = obj.toolTip()
            if not text or isinstance(obj.window(), QMenu):
                return False  # Nothing to show, or a menu, which keeps its native behaviour.
            self.show_for(obj, text)
            return True  # Swallowed, so no native tooltip appears.
        if self.owner is not None and obj is self.owner and kind in (
                QEvent.Leave, QEvent.MouseButtonPress, QEvent.Wheel, QEvent.Hide, QEvent.KeyPress):
            self.hide_tip()
        elif kind == QEvent.ApplicationDeactivate and self.owner is not None:
            self.hide_tip()
        return False

    def paintEvent(self, _):
        theme = self.theme
        if theme is None:
            return
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        path = QPainterPath()
        path.addRoundedRect(QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5), 6, 6)
        p.fillPath(path, css_color(theme.menu_bg))
        p.setPen(QPen(css_color(theme.separator), 1))
        p.drawPath(path)


# --- Popup base --------------------------------------------------------------
POPUP_RADIUS = 8  # Windows 11's corner rounding, so the drawn border and DWM's clip coincide.


class GlassPopup(QWidget):
    """Frameless acrylic popup with rounded corners and the native shadow.

    Subclasses put their content into ``panel``. The popup fades in on show and leaves a
    screen-grabbed ghost that fades out on close.

    Attributes:
        theme: The theme the popup was built with.
        panel: The styled container widget for the popup's content.
    """

    def __init__(self, theme: Theme, parent=None):
        super().__init__(parent, Qt.Popup | Qt.FramelessWindowHint)
        self.theme = theme
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setAttribute(Qt.WA_DeleteOnClose)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        self.panel = QWidget(self)
        self.panel.setObjectName("popup")
        self.panel.setAttribute(Qt.WA_StyledBackground)
        outer.addWidget(self.panel)
        self.setStyleSheet(popup_qss(theme))
        self._intro: motion.BackdropFade | None = None
        self._opens_up = False  # Placed above its anchor, so it grows and shrinks at the top edge.

    def showEvent(self, e):
        """Applies rounded corners and acrylic, then fades the popup in."""
        super().showEvent(e)
        hwnd = int(self.winId())
        base = QColor(self.theme.base)
        rgba = (base.red() << 24) | (base.green() << 16) | (base.blue() << 8) | 0xC8
        win_effects.set_rounded_corners(hwnd)
        if win_effects.set_acrylic(hwnd, rgba):
            # A faint fill keeps the window hit-testable; the acrylic provides the colour.
            self.panel.setStyleSheet(
                f"QWidget#popup {{ background: rgba({base.red()},{base.green()},{base.blue()},0.5); }}")
            # The acrylic drops out while the popup fades in, so a painted backdrop stands in.
            self._intro = motion.BackdropFade(self, QColor(base.red(), base.green(), base.blue(), 0xC8),
                                              self.theme.acrylic_saturation)
        motion.pop_in(self, on_done=self._intro.hand_over if self._intro else None)

    def paintEvent(self, _):
        if self._intro is not None:
            p = QPainter(self)
            p.setRenderHint(QPainter.Antialiasing)
            path = QPainterPath()
            path.addRoundedRect(QRectF(self.rect()), POPUP_RADIUS, POPUP_RADIUS)
            self._intro.paint(p, path)

    def closeEvent(self, e):
        """Leaves a fading ghost grabbed from the screen, so it keeps the acrylic."""
        # A widget render would lack the acrylic.
        motion.Ghost.from_screen(self, POPUP_RADIUS, motion.POP_OUT_MS)
        motion.stop(self)
        super().closeEvent(e)

    def refit(self) -> None:
        """Resizes the popup to its content, keeping the edge next to the anchor in place."""
        bottom = self.geometry().bottom()
        self.adjustSize()
        if self._opens_up:
            self.move(self.x(), bottom - self.height() + 1)

    def popup_at(self, global_pos: QPoint) -> None:
        """Shows the popup below ``global_pos``, or above it if there is no room, on screen."""
        self.adjustSize()
        screen = QGuiApplication.screenAt(global_pos) or QGuiApplication.primaryScreen()
        area = screen.availableGeometry()
        x, y = global_pos.x(), global_pos.y() + 4
        if y + self.height() > area.bottom():
            y = global_pos.y() - self.height() - 28
            self._opens_up = True
        x = max(area.left(), min(x, area.right() - self.width()))
        y = max(area.top(), min(y, area.bottom() - self.height()))
        self.move(x, y)
        self.show()


class MiniCalendar(QWidget):
    """Compact month grid showing only the current month's days, in as many week rows as needed.

    Colours are neutral: the selection is an inverted (text-coloured) circle and today is bold
    with a dot. Changing month slides the grid sideways; when the new month needs a different
    number of week rows, the height eases to fit over the same slide.

    Attributes:
        theme: The theme used for drawing.
        selected: The selected day.
        month: The first day of the shown month.
        hover: The day under the cursor, or None.
        prev_btn: Button that shows the previous month.
        next_btn: Button that shows the next month.

    Signals:
        resized: Emitted when the height changes, on every frame while it eases to a new
            row count.
        picked: Emitted when a day is clicked.
    """

    resized = Signal()
    picked = Signal()

    CELL = 36
    HEADER = 34
    SLIDE_MS = 260
    SLIDE_DISTANCE = 1.0  # Fraction of the width: months sit side by side and never overlap.

    def __init__(self, theme: Theme, selected: date, parent=None):
        super().__init__(parent)
        self.theme = theme
        self.selected = selected
        self.month = selected.replace(day=1)
        self.hover: date | None = None
        self._hover_level = 1.0
        self._hover_tween = motion.Tween(self, motion.HoverFx.IN_MS, self._set_hover_level)
        self._slide: tuple[QPixmap, int] | None = None  # (Outgoing month image, direction).
        self._slide_t = 0.0
        self._slide_tween: motion.Tween | None = None
        self._height_tween: motion.Tween | None = None
        self.setMouseTracking(True)
        self.setFixedSize(self.CELL * 7, self._height_for())

        self.prev_btn = QToolButton(self)
        self.next_btn = QToolButton(self)
        for btn, name, step in ((self.prev_btn, "chevron_left", -1), (self.next_btn, "chevron_right", 1)):
            btn.setIcon(icons.icon(name, theme.secondary, 14))
            btn.setIconSize(QSize(14, 14))
            btn.setCursor(Qt.PointingHandCursor)
            btn.setFixedSize(28, 28)
            btn.setProperty("hoverRadius", 14)
            btn.setStyleSheet("QToolButton{border:none;background:transparent;}")
            btn.clicked.connect(lambda _=False, months=step: self.shift(months))
        self.prev_btn.move(self.width() - 62, 3)
        self.next_btn.move(self.width() - 30, 3)

    # --- Navigation ----------------------------------------------------------
    def shift(self, months: int) -> None:
        """Moves the shown month by ``months``, sliding the grid and easing its height."""
        if not months:
            return
        outgoing = self._render_month(self.month)
        year_carry, month_index = divmod(self.month.month - 1 + months, 12)
        self.month = date(self.month.year + year_carry, month_index + 1, 1)
        self.hover = None
        self._ease_height()
        self._slide = (outgoing, 1 if months > 0 else -1)
        if self._slide_tween is not None:
            self._slide_tween.stop()
        self._slide_tween = motion.Tween(self, self.SLIDE_MS, self._set_slide, self._end_slide)
        self._slide_tween.start()

    def set_selected(self, d: date) -> None:
        """Selects day ``d``, sliding to its month if it is not the one shown."""
        self.selected = d
        months = (d.year - self.month.year) * 12 + d.month - self.month.month
        if months:
            self.shift(months)
        else:
            self.update()

    def _set_slide(self, t: float) -> None:
        self._slide_t = t
        self.update()

    def _end_slide(self) -> None:
        self._slide = None
        self.update()

    def _render_month(self, month: date) -> QPixmap:
        """Renders a month's title and grid into a pixmap, used as the outgoing slide image."""
        dpr = self.devicePixelRatioF()
        pixmap = QPixmap(round(self.width() * dpr), round(self.height() * dpr))
        pixmap.setDevicePixelRatio(dpr)
        pixmap.fill(Qt.transparent)
        p = QPainter(pixmap)
        p.setRenderHint(QPainter.Antialiasing)
        self._paint_month(p, month, hover=None)
        p.end()
        return pixmap

    # --- Geometry ------------------------------------------------------------
    def _rows(self, month: date | None = None) -> int:
        """Returns the number of week rows ``month`` (default: the shown one) needs."""
        month = month or self.month
        next_month = date(month.year + month.month // 12, month.month % 12 + 1, 1)
        return math.ceil((month.weekday() + (next_month - month).days) / 7)

    def _height_for(self, month: date | None = None) -> int:
        """Returns the widget height for ``month``'s week rows, below the header and weekdays."""
        return self.HEADER + 22 + self.CELL * self._rows(month)

    def _ease_height(self) -> None:
        """Grows or shrinks to the current month's rows, in step with the slide.

        The outgoing month's extra row fades out as it is clipped; a new extra row is revealed.
        """
        if self._height_tween is not None:
            self._height_tween.stop()
        start, end = self.height(), self._height_for()
        if start == end:
            return

        def step(v: float) -> None:
            self.setFixedHeight(round(start + (end - start) * v))
            self.resized.emit()

        self._height_tween = motion.Tween(self, self.SLIDE_MS, step)
        self._height_tween.start()

    def _date_at(self, pos) -> date | None:
        """Returns the shown day at ``pos``, or None outside the grid or during a slide."""
        top = self.HEADER + 22
        if pos.y() < top or self._slide is not None:
            return None
        row, col = int((pos.y() - top) // self.CELL), int(pos.x() // self.CELL)
        if 0 <= row < self._rows() and 0 <= col < 7:
            day = self.month - timedelta(days=self.month.weekday()) + timedelta(days=row * 7 + col)
            return day if day.month == self.month.month else None  # Other months are not shown.
        return None

    # --- Events --------------------------------------------------------------
    def mouseMoveEvent(self, e):
        day = self._date_at(e.position())
        if day != self.hover:
            self.hover = day
            self.setCursor(Qt.PointingHandCursor if day else Qt.ArrowCursor)
            if day is not None:  # The hover circle fades in on each new day.
                self._hover_tween.start()
            self.update()

    def _set_hover_level(self, v: float) -> None:
        self._hover_level = v
        self.update()

    def leaveEvent(self, e):
        self.hover = None
        self.update()

    def mousePressEvent(self, e):
        day = self._date_at(e.position())
        if day:
            self.selected = day
            self.update()
            self.picked.emit()

    def wheelEvent(self, e):
        """Shows the previous month on wheel up and the next month on wheel down."""
        self.shift(-1 if e.angleDelta().y() > 0 else 1)

    # --- Painting ------------------------------------------------------------
    def paintEvent(self, _):
        """Paints the fixed weekday row, then the month, cross-fading two months while sliding."""
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        font = QFont(self.font())
        font.setPointSizeF(font.pointSizeF() - 1.5)
        p.setFont(font)
        p.setPen(QColor(self.theme.secondary))  # Weekday initials are text, so secondary colour.
        for i, weekday in enumerate(tr("date.weekdays").split()):
            p.drawText(QRectF(i * self.CELL, self.HEADER, self.CELL, 22), Qt.AlignCenter, weekday)

        if self._slide is None:
            self._paint_month(p, self.month, self.hover)
            return
        outgoing, direction = self._slide
        progress, travel = self._slide_t, self.width() * self.SLIDE_DISTANCE
        # Keeps the sliding month title from passing underneath the arrows.
        arrows = QRect(self.prev_btn.x() - 4, 0, self.width() - self.prev_btn.x() + 4, self.HEADER)
        p.setClipRegion(QRegion(self.rect()).subtracted(QRegion(arrows)))
        p.save()
        p.setOpacity(1.0 - progress)
        p.drawPixmap(QPointF(-direction * travel * progress, 0), outgoing)
        p.restore()
        p.setOpacity(progress)
        p.translate(direction * travel * (1.0 - progress), 0)
        self._paint_month(p, self.month, hover=None)

    def _paint_month(self, p: QPainter, month: date, hover: date | None) -> None:
        """Paints the month title and day grid, the part that slides."""
        theme = self.theme
        font = QFont(self.font())
        font.setBold(True)
        font.setPointSizeF(font.pointSizeF() + 1)
        p.setFont(font)
        p.setPen(QColor(theme.text))
        p.drawText(QRectF(4, 0, 160, self.HEADER), Qt.AlignVCenter | Qt.AlignLeft,
                   tr("date.month", **month_fields(month)))

        today = date.today()
        top = self.HEADER + 22
        first = month - timedelta(days=month.weekday())
        normal, bold = QFont(self.font()), QFont(self.font())
        bold.setBold(True)
        for i in range(self._rows(month) * 7):
            day = first + timedelta(days=i)
            if day.month != month.month:
                continue
            cell = QRectF((i % 7) * self.CELL, top + (i // 7) * self.CELL, self.CELL, self.CELL)
            circle = cell.adjusted(3, 3, -3, -3)
            selected = day == self.selected
            if selected:
                p.setPen(Qt.NoPen)
                p.setBrush(QColor(theme.text))
                p.drawEllipse(circle)
            elif day == hover:
                color = css_color(theme.hover)
                color.setAlphaF(color.alphaF() * self._hover_level)
                p.setPen(Qt.NoPen)
                p.setBrush(color)
                p.drawEllipse(circle)
            p.setFont(bold if day == today or selected else normal)
            p.setPen(QColor(theme.base if selected else theme.text))
            p.drawText(cell, Qt.AlignCenter, str(day.day))
            if day == today and not selected:  # A quiet marker instead of colour.
                p.setPen(Qt.NoPen)
                p.setBrush(QColor(theme.secondary))
                p.drawEllipse(QPointF(cell.center().x(), cell.bottom() - 7), 1.6, 1.6)


class HalfHourTimeEdit(QTimeEdit):
    """HH:mm editor whose steps (arrow keys, wheel, stepper buttons) snap to :00 and :30.

    For example 10:30 -> 11:00 -> 11:30, and 10:40 steps up to 11:00 or down to 10:30. Typing
    stays free-form. Stepping past midnight wraps the time and reports the day change.

    Signals:
        dayChanged: Emitted with the day change (+1 or -1) when stepping wraps past midnight.
    """

    dayChanged = Signal(int)

    def stepBy(self, steps: int) -> None:
        """Steps the time to the next or previous half hour, ``|steps|`` times."""
        time = self.time()
        days, minutes = half_hour_step(time.hour() * 60 + time.minute(), steps)
        self.setTime(QTime(minutes // 60, minutes % 60))
        if days:
            self.dayChanged.emit(days)

    def stepEnabled(self):
        """Returns both step directions as always enabled, since stepping wraps."""
        return QAbstractSpinBox.StepUpEnabled | QAbstractSpinBox.StepDownEnabled


class DateTimePopup(GlassPopup):
    """Popup for picking a reminder's due date and time, or clearing it.

    Attributes:
        calendar: The month grid for the date.
        time_edit: The half-hour stepping time editor.
        clear_btn: Button that removes the due time; offered only once there is one to remove.

    Signals:
        chosen: Emitted with the chosen datetime, or None when cleared, before closing.
    """

    chosen = Signal(object)

    def __init__(self, theme: Theme, current: datetime | None, parent=None):
        """Builds the popup.

        Args:
            theme: The theme to draw with.
            current: The current due time, or None to start from `default_due` with the Clear
                button hidden until something is picked.
            parent: Optional parent widget.
        """
        super().__init__(theme, parent)
        had_due = current is not None
        current = current or default_due()
        lay = QVBoxLayout(self.panel)
        lay.setContentsMargins(12, 10, 12, 10)
        lay.setSpacing(6)

        self.calendar = MiniCalendar(theme, current.date(), self.panel)
        self.calendar.resized.connect(self._refit)
        lay.addWidget(self.calendar, 0, Qt.AlignHCenter)
        lay.addWidget(self._hairline())

        # Time: label, value and stepper share one vertical centre line.
        time_row = QHBoxLayout()
        time_row.setContentsMargins(4, 0, 0, 0)
        time_row.setSpacing(2)
        label = QLabel(tr("popup.time"), self.panel)
        label.setObjectName("secondary")
        time_row.addWidget(label, 0, Qt.AlignVCenter)
        time_row.addStretch(1)
        self.time_edit = HalfHourTimeEdit(QTime(current.hour, current.minute), self.panel)
        self.time_edit.setDisplayFormat("HH:mm")
        self.time_edit.setAlignment(Qt.AlignCenter)
        self.time_edit.setFixedSize(56, 28)
        self.time_edit.dayChanged.connect(
            lambda days: self.calendar.set_selected(self.calendar.selected + timedelta(days=days)))
        time_row.addWidget(self.time_edit, 0, Qt.AlignVCenter)
        stepper = QVBoxLayout()
        stepper.setContentsMargins(0, 0, 0, 0)
        stepper.setSpacing(0)
        for name, steps in (("chevron_up", 1), ("chevron_down", -1)):
            btn = QToolButton(self.panel)
            btn.setIcon(icons.icon(name, theme.secondary, 12))
            btn.setIconSize(QSize(12, 12))
            btn.setFixedSize(22, 14)
            btn.setProperty("hoverRadius", 4)
            btn.setCursor(Qt.PointingHandCursor)
            btn.setAutoRepeat(True)
            btn.clicked.connect(lambda _=False, count=steps: self.time_edit.stepBy(count))
            stepper.addWidget(btn)
        time_row.addLayout(stepper)
        lay.addLayout(time_row)
        lay.addWidget(self._hairline())

        buttons = QHBoxLayout()
        self.clear_btn = QPushButton(tr("popup.clear"), self.panel)
        self.clear_btn.setObjectName("danger")
        self.clear_btn.setCursor(Qt.PointingHandCursor)
        self.clear_btn.clicked.connect(lambda: self._emit(None))
        buttons.addWidget(self.clear_btn)
        if not had_due:
            # Nothing to remove yet: the button fades in once a day or time is picked, so it is
            # available without reopening the popup. The Done button keeps its place either way.
            policy = self.clear_btn.sizePolicy()
            policy.setRetainSizeWhenHidden(True)
            self.clear_btn.setSizePolicy(policy)
            self.clear_btn.hide()
            self.calendar.picked.connect(self._offer_clear)
            self.time_edit.timeChanged.connect(self._offer_clear)
        buttons.addStretch(1)
        done_btn = QPushButton(tr("popup.done"), self.panel)
        done_btn.setObjectName("primary")
        done_btn.setCursor(Qt.PointingHandCursor)
        done_btn.setDefault(True)
        done_btn.clicked.connect(self._accept)
        buttons.addWidget(done_btn)
        lay.addLayout(buttons)
        motion.HoverFx.attach(self)

    def _refit(self) -> None:
        """Refits the popup after the calendar's height changed."""
        self.panel.layout().activate()
        self.refit()

    def _offer_clear(self, *_) -> None:
        """Fades in the hidden Clear button once a due time has been picked."""
        btn = self.clear_btn
        if not btn.isHidden():
            return
        fx = QGraphicsOpacityEffect(btn)
        fx.setOpacity(0.0)
        btn.setGraphicsEffect(fx)
        btn.show()
        btn._fade = motion.Tween(btn, motion.POP_IN_MS, fx.setOpacity, lambda: btn.setGraphicsEffect(None))
        btn._fade.start()

    def _hairline(self) -> QFrame:
        """Returns a new horizontal separator line for the panel."""
        line = QFrame(self.panel)
        line.setObjectName("hairline")
        return line

    def _accept(self) -> None:
        """Emits the selected day combined with the chosen time."""
        time = self.time_edit.time()
        self._emit(datetime.combine(self.calendar.selected, datetime.min.time()).replace(
            hour=time.hour(), minute=time.minute()))

    def _emit(self, dt: datetime | None) -> None:
        """Emits ``chosen`` with ``dt`` and closes the popup."""
        self.chosen.emit(dt)
        self.close()


# --- Colour popup ------------------------------------------------------------
class _SVField(QWidget):
    """Saturation (x) and value (y) square for the picker's current hue.

    Signals:
        changed: Emitted after a press or drag has updated the picker's saturation and value.
    """

    changed = Signal()

    def __init__(self, picker: "ColorPopup", parent=None):
        super().__init__(parent)
        self.picker = picker
        self.setFixedSize(216, 132)
        self.setCursor(Qt.CrossCursor)

    def _set_from(self, pos) -> None:
        """Sets the picker's saturation and value from a position in the square."""
        self.picker.s = min(1.0, max(0.0, pos.x() / self.width()))
        self.picker.v = min(1.0, max(0.0, 1.0 - pos.y() / self.height()))
        self.changed.emit()

    def mousePressEvent(self, e):
        self._set_from(e.position())

    def mouseMoveEvent(self, e):
        if e.buttons() & Qt.LeftButton:
            self._set_from(e.position())

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        rect = QRectF(self.rect())
        path = QPainterPath()
        path.addRoundedRect(rect, 6, 6)
        p.setClipPath(path)
        p.fillRect(rect, QColor.fromHsvF(self.picker.h, 1.0, 1.0))
        across = QLinearGradient(rect.topLeft(), rect.topRight())
        across.setColorAt(0, QColor(255, 255, 255))
        across.setColorAt(1, QColor(255, 255, 255, 0))
        p.fillRect(rect, across)
        down = QLinearGradient(rect.topLeft(), rect.bottomLeft())
        down.setColorAt(0, QColor(0, 0, 0, 0))
        down.setColorAt(1, QColor(0, 0, 0))
        p.fillRect(rect, down)
        p.setClipping(False)
        _knob(p, QPointF(self.picker.s * rect.width(), (1.0 - self.picker.v) * rect.height()), self.picker.color())


class _HueStrip(QWidget):
    """Horizontal rainbow strip that sets the picker's hue.

    Signals:
        changed: Emitted after a press or drag has updated the picker's hue.
    """

    changed = Signal()

    def __init__(self, picker: "ColorPopup", parent=None):
        super().__init__(parent)
        self.picker = picker
        self.setFixedSize(216, 14)
        self.setCursor(Qt.PointingHandCursor)

    def _set_from(self, pos) -> None:
        """Sets the picker's hue from a horizontal position on the strip."""
        self.picker.h = min(0.999, max(0.0, pos.x() / self.width()))
        self.changed.emit()

    def mousePressEvent(self, e):
        self._set_from(e.position())

    def mouseMoveEvent(self, e):
        if e.buttons() & Qt.LeftButton:
            self._set_from(e.position())

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        rect = QRectF(self.rect()).adjusted(0, 2, 0, -2)
        grad = QLinearGradient(rect.topLeft(), rect.topRight())
        for i in range(7):
            grad.setColorAt(i / 6, QColor.fromHsvF(min(i / 6, 0.999), 1.0, 1.0))
        p.setPen(Qt.NoPen)
        p.setBrush(grad)
        p.drawRoundedRect(rect, rect.height() / 2, rect.height() / 2)
        _knob(p, QPointF(self.picker.h * self.width(), self.height() / 2), QColor.fromHsvF(self.picker.h, 1.0, 1.0))


def _knob(p: QPainter, at: QPointF, fill: QColor) -> None:
    """Draws a picker knob: a white ring with a soft dark edge, readable on any colour."""
    p.setBrush(fill)
    p.setPen(QPen(QColor(0, 0, 0, 70), 3.5))
    p.drawEllipse(at, 6, 6)
    p.setPen(QPen(QColor(255, 255, 255), 2))
    p.drawEllipse(at, 6, 6)


class ColorPopup(GlassPopup):
    """Free colour picker for a note: hue strip, saturation/brightness square and a hex field.

    The note previews the colour live; clicking outside cancels.

    Attributes:
        h: Current hue, 0..1.
        s: Current saturation, 0..1.
        v: Current value (brightness), 0..1.
        field: The saturation/value square.
        strip: The hue strip.
        swatch: Small preview of the current colour.
        hex_edit: Field showing and accepting the colour as ``#RRGGBB``.

    Signals:
        preview: Emitted with the colour as "#RRGGBB" on every change while choosing.
        chosen: Emitted with the final "#RRGGBB" colour when Done (or Enter) is pressed.
        cancelled: Emitted when the popup closes without a choice.
    """

    preview = Signal(str)
    chosen = Signal(str)
    cancelled = Signal()

    def __init__(self, theme: Theme, start: str, parent=None):
        """Builds the popup, starting from the colour ``start`` (any string QColor accepts)."""
        super().__init__(theme, parent)
        start_color = QColor(start)
        self.h, self.s, self.v = (max(start_color.hsvHueF(), 0.0), start_color.hsvSaturationF(),
                                  start_color.valueF())
        self._done = False
        lay = QVBoxLayout(self.panel)
        lay.setContentsMargins(12, 12, 12, 10)
        lay.setSpacing(10)
        self.field = _SVField(self, self.panel)
        self.strip = _HueStrip(self, self.panel)
        lay.addWidget(self.field)
        lay.addWidget(self.strip)
        lay.addWidget(self._hairline())

        row = QHBoxLayout()
        row.setSpacing(8)
        self.swatch = QLabel(self.panel)
        self.swatch.setFixedSize(18, 18)
        row.addWidget(self.swatch, 0, Qt.AlignVCenter)
        self.hex_edit = QLineEdit(self.panel)
        self.hex_edit.setObjectName("hex")
        self.hex_edit.setMaxLength(7)
        self.hex_edit.setFixedWidth(84)
        self.hex_edit.setValidator(
            QRegularExpressionValidator(QRegularExpression(r"#?[0-9A-Fa-f]{0,6}"), self.hex_edit))
        self.hex_edit.textEdited.connect(self._typed)
        self.hex_edit.editingFinished.connect(lambda: self.hex_edit.setText(self.color().name().upper()))
        self.hex_edit.returnPressed.connect(self._accept)
        row.addWidget(self.hex_edit, 0, Qt.AlignVCenter)
        row.addStretch(1)
        done_btn = QPushButton(tr("popup.done"), self.panel)
        done_btn.setObjectName("primary")
        done_btn.setCursor(Qt.PointingHandCursor)
        done_btn.setDefault(True)
        done_btn.clicked.connect(self._accept)
        row.addWidget(done_btn)
        lay.addLayout(row)

        self.field.changed.connect(self._dragged)
        self.strip.changed.connect(self._dragged)
        self._sync(update_text=True)
        motion.HoverFx.attach(self)

    def color(self) -> QColor:
        """Returns the current colour."""
        return QColor.fromHsvF(self.h, self.s, self.v)

    def _hairline(self) -> QFrame:
        """Returns a new horizontal separator line for the panel."""
        line = QFrame(self.panel)
        line.setObjectName("hairline")
        return line

    def _sync(self, update_text: bool) -> None:
        """Updates the swatch, field and strip and emits ``preview``; optionally the hex text."""
        hex_name = self.color().name().upper()
        if update_text:
            self.hex_edit.setText(hex_name)
        self.swatch.setStyleSheet(f"background:{hex_name}; border-radius:9px; border:1px solid rgba(0,0,0,0.12);")
        self.field.update()
        self.strip.update()
        self.preview.emit(hex_name)

    def _dragged(self) -> None:
        """Syncs everything, including the hex text, after the square or strip changed."""
        self._sync(update_text=True)

    def _typed(self, text: str) -> None:
        """Applies a typed hex colour once it is complete, leaving the text as typed."""
        text = text if text.startswith("#") else "#" + text
        if is_hex(text):
            typed = QColor(text)
            hue = typed.hsvHueF()  # -1 for greys, which keep the strip where it is.
            self.h, self.s, self.v = hue if hue >= 0 else self.h, typed.hsvSaturationF(), typed.valueF()
            self._sync(update_text=False)

    def _accept(self) -> None:
        """Emits ``chosen`` with the current colour and closes the popup."""
        self._done = True
        self.chosen.emit(self.color().name().upper())
        self.close()

    def closeEvent(self, e):
        """Emits ``cancelled`` when the popup closes without Done."""
        if not self._done:
            self.cancelled.emit()
        super().closeEvent(e)
