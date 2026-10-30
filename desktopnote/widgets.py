"""Content widgets and helpers used inside notes.

Provides the auto-growing text editor, title field, item rows, tooltips, the glass popup base
with its date/time, colour, text-size and tag pickers, the overlay scroll indicator, the
segmented control, the grabber, and small date/time formatting helpers.
"""
from __future__ import annotations

import math
from datetime import date, datetime, timedelta
from functools import lru_cache

from PySide6.QtCore import (
    QElapsedTimer,
    QEvent,
    QPoint,
    QPointF,
    QRect,
    QRectF,
    QRegularExpression,
    QSize,
    Qt,
    QTime,
    QTimer,
    Signal,
)
from PySide6.QtGui import (
    QColor,
    QConicalGradient,
    QCursor,
    QFont,
    QFontMetricsF,
    QGuiApplication,
    QIcon,
    QImage,
    QLinearGradient,
    QPainter,
    QPainterPath,
    QPen,
    QPixmap,
    QRegion,
    QRegularExpressionValidator,
    QTextOption,
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
    QScrollArea,
    QSizePolicy,
    QTextEdit,
    QTimeEdit,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from . import icons, motion, win_effects
from .i18n import month_fields, tr
from .models import Item
from .theme import BODY_PT, DUE_PT, TAGS, TEXT_SCALES, TITLE_PT, Theme, is_hex, popup_qss

# --- Helpers -----------------------------------------------------------------
def reminder_state(item: Item, soon_minutes: int, now: datetime | None = None) -> str:
    """Returns the reminder state of an item.

    Args:
        item: The item to classify.
        soon_minutes: How close to the due time an item counts as "soon".
        now: The reference time; defaults to the current time.

    Returns:
        One of "done", "none", "normal", "soon" or "overdue".
    """
    if item.done:
        return "done"
    due = item.due_dt
    if due is None:
        return "none"
    now = now or datetime.now()
    if now >= due:
        return "overdue"
    if due - now <= timedelta(minutes=soon_minutes):
        return "soon"
    return "normal"


def format_due(dt: datetime, now: datetime | None = None) -> str:
    """Returns a localised, relative label for a due time ("Today 14:00", "Tomorrow ...").

    Args:
        dt: The due time to format.
        now: The reference time; defaults to the current time.
    """
    now = now or datetime.now()
    day, today, hm = dt.date(), now.date(), dt.strftime("%H:%M")
    if day == today:
        return tr("date.today", hm=hm)
    if day == today + timedelta(days=1):
        return tr("date.tomorrow", hm=hm)
    if day == today - timedelta(days=1):
        return tr("date.yesterday", hm=hm)
    key = "date.this_year" if day.year == today.year else "date.other_year"
    return tr(key, hm=hm, **month_fields(dt))


def humanize_delta(delta: timedelta) -> str:
    """Returns a short localised duration such as "5 min", "2 h 10 min" or "3 days".

    The duration is rounded up to whole minutes, with a minimum of one minute.
    """
    mins = max(1, math.ceil(delta.total_seconds() / 60))
    if mins < 60:
        return tr("delta.minutes", n=mins)
    hours, rem = divmod(mins, 60)
    if hours < 24:
        return tr("delta.hours", n=hours) + (" " + tr("delta.minutes", n=rem) if rem else "")
    return tr("delta.days", n=hours // 24)


def half_hour_step(minutes: int, steps: int) -> tuple[int, int]:
    """Moves a time of day to the next or previous :00/:30 boundary, ``|steps|`` times.

    Args:
        minutes: Time of day in minutes since midnight.
        steps: Number of boundaries to move; positive moves forward, negative backward.

    Returns:
        A ``(day_change, minutes)`` tuple, where ``day_change`` is the number of days crossed.
    """
    for _ in range(abs(steps)):
        minutes = (minutes // 30 + 1) * 30 if steps > 0 else (minutes - 1) // 30 * 30
    return divmod(minutes, 24 * 60)


def default_due(now: datetime | None = None) -> datetime:
    """Returns the default due time for a new reminder: two hours after the current full hour."""
    now = now or datetime.now()
    return now.replace(minute=0, second=0, microsecond=0) + timedelta(hours=2)


@lru_cache(maxsize=32)
def _midline_offset(family: str, point_size: float, dpr: float) -> float:
    """Measures the digit-ink midline offset by rendering a "0"; see `glyph_midline_offset`."""
    font = QFont(family)
    font.setPointSizeF(point_size)
    fm = QFontMetricsF(font)
    w, h = math.ceil(fm.horizontalAdvance("0") + 4), math.ceil(fm.height())
    img = QImage(round(w * dpr), round(h * dpr), QImage.Format_ARGB32_Premultiplied)
    img.setDevicePixelRatio(dpr)
    img.fill(Qt.transparent)
    p = QPainter(img)
    p.setFont(font)
    p.setPen(Qt.black)
    p.drawText(QPointF(2, fm.ascent()), "0")
    p.end()
    rows = [y for y in range(img.height()) if any(img.pixelColor(x, y).alpha() > 96 for x in range(img.width()))]
    if not rows:
        return 0.0
    ink_mid = (rows[0] + rows[-1] + 1) / 2 / dpr
    return round((ink_mid - fm.height() / 2) * 2) / 2  # Rounded to half pixels.


def glyph_midline_offset(font: QFont) -> float:
    """Returns how far the digits' visual middle sits below the centre of the font's line box.

    The offset is measured from rendered glyphs because hinting makes font metrics drift. Icons
    centred on a text line are moved down by this amount to sit on the text's midline.
    """
    screen = QGuiApplication.primaryScreen()
    dpr = screen.devicePixelRatio() if screen is not None else 1.0
    return _midline_offset(font.family(), font.pointSizeF(), dpr)


# --- Text editor -------------------------------------------------------------
class AutoTextEdit(QTextEdit):
    """Plain-text editor that grows to fit its content and never scrolls itself.

    Signals:
        textEdited: Emitted with the full plain text whenever the text changes.
        returnPressed: Emitted on Enter (without Shift) when ``submit_on_enter`` is set.
        backspaceOnEmpty: Emitted when Backspace is pressed in an empty editor.
        escapePressed: Emitted when Escape is pressed.
        editRequested: Emitted on a double click while read-only.
    """

    textEdited = Signal(str)
    returnPressed = Signal()
    backspaceOnEmpty = Signal()
    escapePressed = Signal()
    editRequested = Signal()

    def __init__(self, text: str = "", placeholder: str = "", submit_on_enter: bool = False, parent=None):
        super().__init__(parent)
        self.submit_on_enter = submit_on_enter
        self.setAcceptRichText(False)
        self.setFrameShape(QFrame.NoFrame)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.setLineWrapMode(QTextEdit.WidgetWidth)
        self.setWordWrapMode(QTextOption.WrapAtWordBoundaryOrAnywhere)
        self.setTabChangesFocus(True)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.setMinimumWidth(40)
        self.document().setDocumentMargin(2)
        self.setPlaceholderText(placeholder)
        self.setPlainText(text)
        self.document().documentLayout().documentSizeChanged.connect(self._fit_height)
        self.textChanged.connect(lambda: self.textEdited.emit(self.toPlainText()))
        self._fit_height()

    def sizeHint(self) -> QSize:
        return QSize(120, self.height())

    def _fit_height(self, *_):
        """Sets a fixed height that fits the document, at least one line tall."""
        doc_h = self.document().size().height()
        line_h = self.fontMetrics().lineSpacing() + 2 * self.document().documentMargin()
        h = math.ceil(max(doc_h, line_h)) + 2
        if h != self.height():
            self.setFixedHeight(h)

    def resizeEvent(self, e):
        super().resizeEvent(e)
        self._fit_height()

    def changeEvent(self, e):
        super().changeEvent(e)
        if e.type() == QEvent.FontChange:
            # The placeholder is drawn with the viewport's font, and with a style sheet on this
            # editor (its text colour) the viewport stops inheriting fonts and would keep its
            # first size forever, so the new font is handed over explicitly.
            self.viewport().setFont(self.font())
            self._fit_height()

    def setReadOnly(self, ro: bool) -> None:
        """Switches read-only mode, keeping text selectable and links clickable while read-only."""
        super().setReadOnly(ro)
        self.viewport().setCursor(Qt.ArrowCursor if ro else Qt.IBeamCursor)
        if ro:
            self.setTextInteractionFlags(Qt.TextSelectableByMouse | Qt.LinksAccessibleByMouse)

    def wheelEvent(self, e):
        e.ignore()  # The note's scroll area handles scrolling.

    def mouseDoubleClickEvent(self, e):
        if self.isReadOnly():
            self.editRequested.emit()
        super().mouseDoubleClickEvent(e)

    def keyPressEvent(self, e):
        key = e.key()
        if key in (Qt.Key_Return, Qt.Key_Enter) and self.submit_on_enter and not (e.modifiers() & Qt.ShiftModifier):
            self.returnPressed.emit()
            return
        if key == Qt.Key_Backspace and not self.toPlainText():
            self.backspaceOnEmpty.emit()
            return
        if key == Qt.Key_Escape:
            self.escapePressed.emit()
            return
        super().keyPressEvent(e)


# --- Title -------------------------------------------------------------------
class TitleEdit(QLineEdit):
    """Optional one-line note title; read-only and unfocusable outside edit mode.

    Signals:
        submitted: Emitted on Enter or Down to continue into the items.
        escapePressed: Emitted when Escape is pressed.
        editRequested: Emitted on a double click while read-only.
    """

    submitted = Signal()
    escapePressed = Signal()
    editRequested = Signal()

    def __init__(self, text: str = "", parent=None):
        super().__init__(text, parent)
        self.setObjectName("title")
        self.setFrame(False)
        self.set_text_scale(1.0)

    def set_text_scale(self, scale: float) -> None:
        """Sets the title font to the demi-bold title size multiplied by ``scale``."""
        font = QFont(self.font())
        font.setPointSizeF(TITLE_PT * scale)
        font.setWeight(QFont.DemiBold)
        self.setFont(font)

    def setReadOnly(self, ro: bool) -> None:
        """Switches read-only mode; a read-only title takes no focus and clears its selection."""
        super().setReadOnly(ro)
        self.setFocusPolicy(Qt.NoFocus if ro else Qt.StrongFocus)
        self.setCursor(Qt.ArrowCursor if ro else Qt.IBeamCursor)
        if ro:
            self.deselect()

    def keyPressEvent(self, e):
        if e.key() in (Qt.Key_Return, Qt.Key_Enter, Qt.Key_Down):
            self.submitted.emit()
            return
        if e.key() == Qt.Key_Escape:
            self.escapePressed.emit()
            return
        super().keyPressEvent(e)

    def mouseDoubleClickEvent(self, e):
        if self.isReadOnly():
            self.editRequested.emit()
            return
        super().mouseDoubleClickEvent(e)


# --- Item rows ---------------------------------------------------------------
class ItemRow(QWidget):
    """One content item (text, todo or reminder) inside a note.

    Hovering eases in a soft rounded highlight; in edit mode the delete button fades in and out
    with it.

    Attributes:
        item: The model item shown by this row.
        theme: The current theme.
        soon_minutes: Lead time, in minutes, at which a reminder counts as "soon".
        editing: Whether the row is in edit mode.
        lead: The checkbox/state button for todos and reminders, or None for text items.
        editor: The item's text editor.
        due_btn: The due-time button for reminders, or None for other types.
        del_btn: The delete button, shown only while editing and hovered.

    Signals:
        changed: Emitted when the item's text, done state or due time changes.
        deleteRequested: Emitted with the row when its delete button is clicked.
        newItemAfter: Emitted with the row when Enter asks for a new item after it.
        focusPrevious: Emitted with the row when Backspace is pressed in its empty editor.
        editRequested: Emitted when a double click asks to enter edit mode.
        finishEditing: Emitted when Escape asks to leave edit mode.
        dueClicked: Emitted with the row when its due-time button is clicked.
    """

    PAD_X, PAD_Y = 4, 2  # Room for the highlight around the content.
    RADIUS = 7
    HOVER_STRENGTH = 0.6  # Fraction of theme.hover: softer than a button, whose hover stacks on top.

    changed = Signal()
    deleteRequested = Signal(object)
    newItemAfter = Signal(object)
    focusPrevious = Signal(object)
    editRequested = Signal()
    finishEditing = Signal()
    dueClicked = Signal(object)

    def __init__(self, item: Item, theme: Theme, soon_minutes: int, parent=None):
        super().__init__(parent)
        self.item = item
        self.theme = theme
        self.soon_minutes = soon_minutes
        self.editing = False

        self._icon_px = self.ICON
        self._editor_qss = ""
        self._due_icon_px = self.DUE_ICON
        self._due_dy = 0.0
        self._hover = 0.0  # Highlight level, eased.
        self._hover_tween: motion.Tween | None = None
        self._edit_level = 0.0  # 0..1, eased with edit mode; scales the delete button.
        self._edit_tween: motion.Tween | None = None

        row = QHBoxLayout(self)
        row.setContentsMargins(self.PAD_X, self.PAD_Y, self.PAD_X, self.PAD_Y)
        row.setSpacing(4)

        self.lead: QToolButton | None = None
        if item.type in ("todo", "reminder"):
            self.lead = QToolButton(self)
            # 21 px tall centres the icon on the first text line (the editor's glyphs sit above
            # its geometric centre), so icon and text share one optical midline.
            self.lead.setFixedSize(24, 21)
            self.lead.setIconSize(QSize(19, 19))
            self.lead.setCursor(Qt.PointingHandCursor)
            self.lead.setStyleSheet("QToolButton{padding:0;border-radius:10px;}")
            self.lead.setProperty("hoverRadius", 10)
            self.lead.clicked.connect(self._toggle_done)
            row.addWidget(self.lead, 0, Qt.AlignTop)

        col = QVBoxLayout()
        col.setContentsMargins(0, 0, 0, 0)
        col.setSpacing(0)
        self.editor = AutoTextEdit(item.text, "", submit_on_enter=item.type != "text", parent=self)
        self.editor.textEdited.connect(self._on_text)
        self.editor.returnPressed.connect(lambda: self.newItemAfter.emit(self))
        self.editor.backspaceOnEmpty.connect(lambda: self.focusPrevious.emit(self))
        self.editor.editRequested.connect(self.editRequested)
        self.editor.escapePressed.connect(self.finishEditing)
        col.addWidget(self.editor)

        self.due_btn: QToolButton | None = None
        if item.type == "reminder":
            self.due_btn = QToolButton(self)
            self.due_btn.setObjectName("dueButton")
            self.due_btn.setToolButtonStyle(Qt.ToolButtonTextBesideIcon)
            self.due_btn.setIconSize(QSize(12, 12))
            self.due_btn.setCursor(Qt.PointingHandCursor)
            self.due_btn.setProperty("hoverRadius", 6)
            self.due_btn.clicked.connect(lambda: self.dueClicked.emit(self))
            col.addWidget(self.due_btn, 0, Qt.AlignLeft)
        row.addLayout(col, 1)

        self.del_btn = QToolButton(self)
        self.del_btn.setFixedSize(22, 22)
        self.del_btn.setIconSize(QSize(15, 15))
        self.del_btn.setCursor(Qt.PointingHandCursor)
        self.del_btn.setProperty("hoverRadius", 11)
        self.del_btn.setStyleSheet(
            "QToolButton{padding:0;border-radius:11px;} QToolButton:hover{background:transparent;}")
        self.del_btn.clicked.connect(lambda: self.deleteRequested.emit(self))
        row.addWidget(self.del_btn, 0, Qt.AlignTop)
        self._del_fx = QGraphicsOpacityEffect(self.del_btn)  # Fades with the row highlight.
        self._del_fx.setOpacity(0.0)
        self.del_btn.setGraphicsEffect(self._del_fx)
        self.del_btn.setVisible(False)
        # Keeps the layout stable while the delete button is hidden.
        policy = self.del_btn.sizePolicy()
        policy.setRetainSizeWhenHidden(True)
        self.del_btn.setSizePolicy(policy)

        self.apply_theme(theme)
        self.set_editing(False)
        self.retranslate()
        motion.HoverFx.attach(self)

    # --- State ---------------------------------------------------------------
    def retranslate(self) -> None:
        """Updates the placeholder, tooltips and due label for the current language."""
        self.editor.setPlaceholderText(tr("item." + self.item.type))
        self.del_btn.setToolTip(tr("item.delete"))
        self.refresh()

    def apply_theme(self, theme: Theme) -> None:
        """Switches the row to ``theme`` and redraws its icons."""
        self.theme = theme
        self.del_btn.setIcon(icons.icon("xmark_circle", theme.tertiary, 15))
        self.refresh()

    def set_editing(self, editing: bool) -> None:
        """Enters or leaves edit mode, easing the delete button's visibility."""
        self.editing = editing
        self.editor.setReadOnly(not editing)
        # Eased so that a row already under the cursor fades its delete button in or out.
        if self._edit_tween is not None:
            self._edit_tween.stop()
        start, end = self._edit_level, 1.0 if editing else 0.0

        def step(v: float) -> None:
            self._edit_level = start + (end - start) * v
            self._sync_delete()

        self._edit_tween = motion.Tween(self, motion.HoverFx.IN_MS if editing else motion.HoverFx.OUT_MS, step)
        self._edit_tween.start()

    def _sync_delete(self) -> None:
        """Shows the delete button only while editing, at the highlight's opacity."""
        level = self._hover * self._edit_level
        self._del_fx.setOpacity(level)
        self.del_btn.setVisible(level > 0.0)

    LEAD_W, LEAD_H, ICON = 24, 21, 19  # At scale 1.0; see the lead button.
    DUE_ICON = 12
    DUE_CHROME = 19  # Due label: 6 px left padding + Qt's icon/text spacing and margins (measured).
    ICON_FOLLOW = 0.6  # Icons grow with the text, a little less than it.

    def set_text_scale(self, scale: float) -> None:
        """Scales the text by ``scale`` and the icons by a damped factor (``ICON_FOLLOW``).

        Icon sizes are nudged to keep an even leftover inside their buttons so they never sit
        on a half pixel.
        """
        font = QFont(self.editor.font())
        base = QFont(font)
        base.setPointSizeF(BODY_PT)
        font.setPointSizeF(BODY_PT * scale)
        self.editor.setFont(font)
        k = 1 + (scale - 1) * self.ICON_FOLLOW
        self._icon_px = round(self.ICON * k)
        self._due_icon_px = round(self.DUE_ICON * k)
        if self.due_btn is not None:
            due = QFont(self.due_btn.font())
            due.setPointSizeF(DUE_PT * scale)
            self.due_btn.setFont(due)
            self._due_dy = glyph_midline_offset(due)
            if (self.due_btn.sizeHint().height() - self._due_icon_px) % 2:  # Odd: icon on a half pixel.
                self._due_icon_px += 1
            self.due_btn.setIconSize(QSize(self._due_icon_px, self._due_icon_px))
        if self.lead is not None:
            # The icon centres on the first line's capitals: their middle (ascent - cap / 2)
            # moves down as the text grows, so the top-aligned lead grows by twice that.
            mid = lambda fm: fm.ascent() - fm.capHeight() / 2  # noqa: E731
            h = round(self.LEAD_H + 2 * (mid(QFontMetricsF(font)) - mid(QFontMetricsF(base))))
            if (h - self._icon_px) % 2:  # An odd leftover would put the icon on a half pixel.
                self._icon_px += 1
            self.lead.setFixedSize(round(self.LEAD_W * k), h)
            self.lead.setIconSize(QSize(self._icon_px, self._icon_px))
            self.lead.setProperty("hoverRadius", self.lead.width() / 2 - 2)
            self.refresh()

    def focus_editor(self, at_end: bool = True) -> None:
        """Focuses the editor, optionally moving the cursor to the end of the text."""
        self.editor.setFocus()
        if at_end:
            cursor = self.editor.textCursor()
            cursor.movePosition(cursor.MoveOperation.End)
            self.editor.setTextCursor(cursor)

    def refresh(self, now: datetime | None = None) -> None:
        """Updates icons, strike-through and the due label for the current state.

        Args:
            now: The reference time for the reminder state; defaults to the current time.
        """
        theme = self.theme
        item = self.item
        font = QFont(self.editor.font())
        font.setStrikeOut(item.done and item.type == "todo")
        self.editor.setFont(font)
        dim = item.done and item.type != "text"
        qss = f"color: {theme.secondary if dim else theme.text};"
        if qss != self._editor_qss:  # A style sheet change restyles the editor, so only when needed.
            self._editor_qss = qss
            self.editor.setStyleSheet(qss)

        if item.type == "todo":
            # Done todos are grey, like done reminders.
            self.lead.setIcon(icons.icon("checkbox_on" if item.done else "checkbox_off",
                                         theme.tertiary if item.done else theme.secondary, self._icon_px))
            self.lead.setToolTip(tr("item.mark_undone" if item.done else "item.mark_done"))
        elif item.type == "reminder":
            state = reminder_state(item, self.soon_minutes, now)
            name, color = {
                "done": ("checkbox_on", theme.tertiary),
                "none": ("bell", theme.secondary),
                "normal": ("bell", theme.secondary),
                "soon": ("clock", theme.orange),
                "overdue": ("warning", theme.red),
            }[state]
            self.lead.setIcon(icons.icon(name, color, self._icon_px))
            self.lead.setToolTip(tr("item.mark_undone" if item.done else "item.mark_done"))
            self._refresh_due(state, now)

    def _refresh_due(self, state: str, now: datetime | None) -> None:
        """Updates the due button's label, tooltip, icon, state colour and width."""
        theme, item = self.theme, self.item
        due = item.due_dt
        now = now or datetime.now()
        if due is None:
            label, tip = tr("item.add_time"), ""
        else:
            label = format_due(due, now)
            if state == "soon":
                label += tr("item.due_in", delta=humanize_delta(due - now))
            elif state == "overdue":
                label += tr("item.overdue")
            tip = due.strftime("%Y-%m-%d %H:%M")
        color = {"soon": theme.orange, "overdue": theme.red}.get(state, theme.secondary)
        self.due_btn.setText(label)
        self.due_btn.setToolTip(tip)
        due_icon = QIcon()  # Drawn _due_dy lower, onto the label's visual midline.
        due_icon.addPixmap(icons.pixmap("calendar", color, self._due_icon_px, dy=self._due_dy))
        self.due_btn.setIcon(due_icon)
        if self.due_btn.property("state") != state:  # Re-polish for the state colour only on change.
            self.due_btn.setProperty("state", state)
            self.due_btn.style().unpolish(self.due_btn)
            self.due_btn.style().polish(self.due_btn)
        # Qt's own size hint leaves 2-3 px extra on the right at larger text sizes; this width
        # centres the icon and date in the hover box (equal gaps on both sides, measured).
        fm = QFontMetricsF(self.due_btn.font())
        self.due_btn.setFixedWidth(self._due_icon_px + math.ceil(fm.horizontalAdvance(label)) + self.DUE_CHROME)

    # --- Events --------------------------------------------------------------
    def _on_text(self, text: str) -> None:
        if text != self.item.text:
            self.item.text = text
            self.changed.emit()

    def _toggle_done(self) -> None:
        self.item.done = not self.item.done
        self.refresh()
        self.changed.emit()

    def set_due(self, dt: datetime | None) -> None:
        """Sets or clears the due time, re-arms the notification and emits ``changed``."""
        self.item.due = dt.isoformat(timespec="minutes") if dt else None
        self.item.notified = False
        self.refresh()
        self.changed.emit()

    def _hover_to(self, target: float, duration: int) -> None:
        """Eases the hover highlight level to ``target`` over ``duration`` milliseconds."""
        if self._hover_tween is not None:
            self._hover_tween.stop()
        start = self._hover

        def step(v: float) -> None:
            self._hover = start + (target - start) * v
            self._sync_delete()
            self.update()

        self._hover_tween = motion.Tween(self, duration, step)
        self._hover_tween.start()

    def enterEvent(self, e):
        self._hover_to(1.0, motion.HoverFx.IN_MS)
        super().enterEvent(e)

    def leaveEvent(self, e):
        self._hover_to(0.0, motion.HoverFx.OUT_MS)
        super().leaveEvent(e)

    def paintEvent(self, _):
        if self._hover <= 0.0:
            return
        color = css_color(self.theme.hover)
        color.setAlphaF(color.alphaF() * self.HOVER_STRENGTH * self._hover)
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.setPen(Qt.NoPen)
        p.setBrush(color)
        p.drawRoundedRect(QRectF(self.rect()), self.RADIUS, self.RADIUS)


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
            self._intro = motion.BackdropFade(self, QColor(base.red(), base.green(), base.blue(), 0xC8))
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
                motion.Tween(self, motion.HoverFx.IN_MS, self._set_hover_level).start()
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


def css_color(css: str) -> QColor:
    """Parses a CSS colour of the form ``rgba(r,g,b,a)`` (alpha in 0..1) or ``#hex``."""
    if css.startswith("rgba"):
        r, g, b, a = (float(v) for v in css[css.index("(") + 1 : css.index(")")].split(","))
        return QColor(int(r), int(g), int(b), int(a * 255))
    return QColor(css)


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


# --- Scroll indicator --------------------------------------------------------
class OverlayScrollBar(QWidget):
    """Overlay scroll indicator for a QScrollArea whose own scroll bar is turned off.

    A thin rounded handle floats over the content, so it never takes width (no re-wrap when it
    appears) and never shows up just because the note was resized. It fades in while scrolling,
    widens under the pointer, can be dragged, and fades out shortly after scrolling stops.

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

    def __init__(self, area: QScrollArea, theme: Theme):
        """Attaches the indicator to ``area``, whose own vertical scroll bar should be off."""
        super().__init__(area)
        self.area, self.theme = area, theme
        self.bar = area.verticalScrollBar()
        self._level = 0.0  # Visibility.
        self._hot = 0.0  # Pointer over it or dragging: wider and darker.
        self._fx: dict[str, motion.Tween] = {}
        self._drag: float | None = None  # Pointer offset inside the handle.
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
        """Flashes the indicator on a scroll, but not on value changes caused by a resize."""
        if self._resized.elapsed() > 200:  # A resize clamping the value is not scrolling.
            self.flash()
        self.update()

    def _place(self) -> None:
        """Places the indicator along the area's right edge; visible only when it can scroll."""
        area = self.area
        self.setGeometry(area.width() - self.W, 0, self.W, area.height())
        self.raise_()
        self.setVisible(self.bar.maximum() > 0)
        self.update()

    def eventFilter(self, obj, ev):
        """Re-places the indicator on area resizes and flashes it on wheel events."""
        kind = ev.type()
        if kind == QEvent.Resize and obj is self.area:
            self._resized.restart()
            self._place()
        elif kind == QEvent.Wheel:
            self.flash()
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
            self._drag = y - handle.top()
        else:  # On the track: one page towards the pointer.
            self.bar.setValue(self.bar.value() + (self.bar.pageStep() if y > handle.bottom() else -self.bar.pageStep()))
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

        self._tween = motion.Tween(self, 180, step)
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
            if i is not None:
                motion.Tween(self, motion.HoverFx.IN_MS, self._set_level).start()
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

    Attributes:
        theme: The theme used for drawing.
        lifted: Whether to draw one grey step stronger, to stay visible over a tag glow.
        level: Hover level, eased from 0 to 1.
    """

    def __init__(self, theme: Theme, parent=None):
        super().__init__(parent)
        self.theme = theme
        self.lifted = False
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
        theme = self.theme
        if self.lifted:
            idle, hot = QColor(theme.secondary), QColor(theme.text)
        else:
            idle, hot = QColor(theme.tertiary), QColor(theme.secondary)
        color = QColor(round(idle.red() + (hot.red() - idle.red()) * level),
                       round(idle.green() + (hot.green() - idle.green()) * level),
                       round(idle.blue() + (hot.blue() - idle.blue()) * level), 220)
        rect = QRectF((self.width() - w) / 2, 6, w, 5)
        p.setPen(Qt.NoPen)
        p.setBrush(color)
        p.drawRoundedRect(rect, 2.5, 2.5)
