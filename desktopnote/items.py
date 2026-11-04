"""A note's content: the auto-growing text editor, the title field and the item rows."""
from __future__ import annotations

import math
from datetime import datetime
from functools import lru_cache

from PySide6.QtCore import (
    QEasingCurve,
    QEvent,
    QPoint,
    QPointF,
    QRectF,
    QSize,
    Qt,
    QTimer,
    Signal,
)
from PySide6.QtGui import (
    QColor,
    QCursor,
    QFont,
    QFontMetrics,
    QFontMetricsF,
    QGuiApplication,
    QIcon,
    QImage,
    QPainter,
    QPainterPath,
    QPixmap,
    QTextOption,
)
from PySide6.QtWidgets import (
    QApplication,
    QFrame,
    QGraphicsDropShadowEffect,
    QGraphicsOpacityEffect,
    QHBoxLayout,
    QLineEdit,
    QSizePolicy,
    QTextEdit,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from . import icons, motion
from .dates import format_due, humanize_delta, reminder_state
from .i18n import tr
from .models import Item
from .theme import BODY_PT, CONTROL_RADIUS, DUE_PT, FIELD_RADIUS, LIFTED_ALPHA, TITLE_PT, Theme, css_color

DOC_MARGIN = 2  # Px between an item editor's edge and its text.



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

    Measured from rendered glyphs, because hinting makes font metrics drift. Icons centred on a
    text line are moved down by this amount to sit on the text's midline.
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
        self.document().setDocumentMargin(DOC_MARGIN)
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
class DeletePill(QToolButton):
    """An item's second delete step: the ×'s hover circle stretched into a "Delete" pill.

    At progress 0 it looks exactly like the hovered × (HoverFx's circle in theme.hover, with the
    "xmark_circle" icon inside). Towards 1 the circle stretches leftwards into a pill of the same
    height whose right end stays put, the × fades away, and the text slides in from the right
    while it fades in. Shrinking back plays the same in reverse. The pill is the translucent
    hover grey, so it stays part of the glass; under the pointer it deepens part of the way to
    theme.pressed (HOVER_DEEPEN, PRESSED_DEEPEN: the text keeps 4.5:1 over tag glows). A
    double click counts as one click, so the second click of a double click on the × cannot reach
    it.

    Attributes:
        theme: The theme used for drawing.
        progress: How far the hover circle has stretched into the pill, 0..1.
        icon_size: Size of the × icon drawn inside, matching the delete button's.
    """

    PAD_X = 10
    CROSS_END, TEXT_START = 0.35, 0.4  # Progress by which the × is gone, and the text starts.
    TEXT_SLIDE = 10  # Px the text slides in from the right while it fades in.
    HOVER_DEEPEN, PRESSED_DEEPEN = 0.5, 0.75  # How far from theme.hover towards theme.pressed.

    def __init__(self, theme: Theme, parent=None):
        super().__init__(parent)
        self.theme = theme
        self.progress = 0.0
        self.icon_size = 15
        self.setProperty("hoverFx", False)  # It paints its own hover.
        self._hover = 0.0
        self._tween: motion.Tween | None = None

    def sizeHint(self) -> QSize:
        fm = QFontMetricsF(self.font())
        return QSize(math.ceil(fm.horizontalAdvance(self.text())) + 2 * self.PAD_X, math.ceil(fm.height()) + 4)

    def set_progress(self, progress: float) -> None:
        """Sets how far the hover circle has stretched into the pill, and repaints."""
        self.progress = progress
        self.update()

    @classmethod
    def fill(cls, theme: Theme, hover: float, down: bool) -> QColor:
        """Returns the pill's colour: the hover grey, deepening towards the pressed grey."""
        idle, hot = css_color(theme.hover), css_color(theme.pressed)
        level = cls.PRESSED_DEEPEN if down else cls.HOVER_DEEPEN * hover
        color = QColor(hot)
        color.setAlphaF(idle.alphaF() + (hot.alphaF() - idle.alphaF()) * level)
        return color

    def mouseDoubleClickEvent(self, e):
        e.accept()  # The second click of a double click: ignored, so it cannot confirm.

    def _hover_to(self, target: float, duration: int) -> None:
        """Eases the hover level to ``target`` over ``duration`` milliseconds."""
        if self._tween is not None:
            self._tween.stop()
        start = self._hover

        def step(v: float) -> None:
            self._hover = start + (target - start) * v
            self.update()

        self._tween = motion.Tween(self, duration, step)
        self._tween.start()

    def enterEvent(self, e):
        self._hover_to(1.0, motion.HoverFx.IN_MS)
        super().enterEvent(e)

    def leaveEvent(self, e):
        self._hover_to(0.0, motion.HoverFx.OUT_MS)
        super().leaveEvent(e)

    def paintEvent(self, _):
        """Paints the stretching circle, the fading × and the sliding text."""
        h, w = float(self.height()), float(self.width())
        t = self.progress
        left = (w - h) * (1.0 - t)  # Starts as the circle at the right end, the ×'s place.
        shape = QPainterPath()
        shape.addRoundedRect(QRectF(left, 0, w - left, h), h / 2, h / 2)
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.fillPath(shape, self.fill(self.theme, self._hover, self.isDown()))
        cross = max(0.0, 1.0 - t / self.CROSS_END)
        if cross > 0.0:  # The × itself, where the delete button draws it.
            pm = icons.pixmap("xmark_circle", self.theme.tertiary, self.icon_size)
            p.setOpacity(cross)
            p.drawPixmap(QPointF(w - h / 2 - self.icon_size / 2, (h - self.icon_size) / 2), pm)
        text = min(1.0, max(0.0, (t - self.TEXT_START) / (1.0 - self.TEXT_START)))
        if text > 0.0:  # Slides in from the right, clipped by the pill; never squeezed.
            p.setClipPath(shape)
            p.setOpacity(text)
            p.setPen(QColor(self.theme.text))
            p.setFont(self.font())
            # Qt centres the font's box; this centres the glyphs themselves (see glyph_midline_offset).
            dx = self.TEXT_SLIDE * (1.0 - text)
            p.drawText(QRectF(dx, -glyph_midline_offset(self.font()), w, h), Qt.AlignCenter, self.text())


class ItemRow(QWidget):
    """One content item (text, todo or reminder) inside a note.

    Hovering eases in a soft rounded highlight; in edit mode the delete button fades in and out
    with it. Deleting takes two clicks: the ×'s hover circle stretches into a Delete pill
    (del_confirm) over the row's end, and that deletes; leaving the row, waiting CONFIRM_MS,
    lifting the row or leaving edit mode shrinks it back into the ×.

    A press on the text or background held still for HOLD_MS lifts the row, in either mode, and
    emits the drag signals; the note then moves it (see reorder.RowReorder). From HINT_MS on, a
    translucent tint fills in as the hold builds up, so the row shows it is about to move; moving
    or releasing sooner lets it fade back, and moving selects text as usual. Lifted, the row is a
    pane of frosted glass: the note hands it a blurred view of what lies beneath (set_backdrop).

    Attributes:
        item: The model item shown by this row.
        theme: The current theme.
        soon_minutes: Lead time, in minutes, at which a reminder counts as "soon".
        editing: Whether the row is in edit mode.
        lead: The checkbox/state button for todos and reminders, or None for text items.
        editor: The item's text editor.
        due_btn: The due-time button for reminders, or None for other types.
        del_btn: The delete button, shown only while editing and hovered.
        del_confirm: The Delete pill the delete button stretches into; it deletes the item.

    Signals:
        changed: Emitted when the item's text, done state or due time changes.
        deleteRequested: Emitted with the row when its Delete pill is clicked.
        newItemAfter: Emitted with the row when Enter asks for a new item after it.
        focusPrevious: Emitted with the row when Backspace is pressed in its empty editor.
        editRequested: Emitted when a double click asks to enter edit mode.
        finishEditing: Emitted when Escape asks to leave edit mode.
        dueClicked: Emitted with the row when its due-time button is clicked.
        dragStarted: Emitted with the row and the global pointer position once a press has been
            held long enough.
        dragMoved: Emitted with the global pointer position while the row is dragged.
        dragFinished: Emitted when the dragged row is released.
        holdStarted: Emitted with the row when a press has been held long enough to show the
            build-up, before anything has changed on screen.
    """

    PAD_X, PAD_Y = 4, 2  # Room for the highlight around the content.
    HOLD_MS = 500  # A press held still this long starts a drag (the usual long-press time).
    HINT_MS = 150  # The hold shows from here on, so plain clicks never flicker.
    HINT_LIFT = 0.6  # Lift level the hold builds up to; reaching 1 means the row moves.
    RISE_MS, SETTLE_MS = 220, 260  # Lifting for a drag, and settling back after the drop.
    SHADOW_BLUR, SHADOW_DY = 18, 4  # At full lift; both grow from 0 as the row rises.
    SHADOW_ALPHA = {False: 0.18, True: 0.45}  # By theme.dark: light glass needs a fainter one.
    CONFIRM_MS = 3000  # The Delete pill shrinks back into the × after this long unclicked.
    CONFIRM_IN_MS, CONFIRM_OUT_MS = 240, 200  # The × stretching into the pill, and back.
    DEL_ICON = 15  # The ×'s icon size.
    RADIUS = CONTROL_RADIUS
    HOVER_STRENGTH = 0.6  # Fraction of theme.hover: softer than a button, whose hover stacks on top.

    changed = Signal()
    deleteRequested = Signal(object)
    newItemAfter = Signal(object)
    focusPrevious = Signal(object)
    editRequested = Signal()
    finishEditing = Signal()
    dueClicked = Signal(object)
    dragStarted = Signal(object, QPoint)
    dragMoved = Signal(QPoint)
    dragFinished = Signal()
    holdStarted = Signal(object)

    def __init__(self, item: Item, theme: Theme, soon_minutes: int, parent=None):
        super().__init__(parent)
        self.item = item
        self.theme = theme
        self.soon_minutes = soon_minutes
        self.editing = False

        self._icon_px = self.ICON
        self._due_icon_px = self.DUE_ICON
        self._hover = 0.0  # Highlight level, eased.
        self._hover_tween: motion.Tween | None = None
        self._edit_level = 0.0  # 0..1, eased with edit mode; scales the delete button.
        self._edit_tween: motion.Tween | None = None
        self._lift = 0.0  # Hold build-up (to HINT_LIFT) and drag (1), eased: the lifted look.
        self._lift_tween: motion.Tween | None = None
        self._press: QPoint | None = None  # Global position of a press that may become a drag.
        self._dragging = False
        self._hold = QTimer(self, singleShot=True, interval=self.HOLD_MS)
        self._hold.timeout.connect(self._start_drag)
        self._hint = QTimer(self, singleShot=True, interval=self.HINT_MS)
        self._hint.timeout.connect(self._show_hold)
        self._shadow: QGraphicsDropShadowEffect | None = None  # Only while lifted for a drag.
        self._backdrop: QPixmap | None = None  # Frosted view of what lies beneath, while lifted.
        self._armed = 0.0  # How far the × has stretched into the Delete pill, eased.
        self._armed_tween: motion.Tween | None = None
        self._armed_on = False  # Where _armed is heading.
        self._disarm = QTimer(self, singleShot=True, interval=self.CONFIRM_MS)
        self._disarm.timeout.connect(lambda: self._set_armed(False))
        # Leaving while the pill shows: the highlight and the × fade once the pill has shrunk back.
        self._leave_later = QTimer(self, singleShot=True, interval=self.CONFIRM_OUT_MS)
        self._leave_later.timeout.connect(lambda: self._ease("_hover", 0.0, motion.HoverFx.OUT_MS))

        row = QHBoxLayout(self)
        row.setContentsMargins(self.PAD_X, self.PAD_Y, self.PAD_X, self.PAD_Y)
        row.setSpacing(4)
        self._row_layout = row

        self.lead: QToolButton | None = None
        if item.type in ("todo", "reminder"):
            self.lead = QToolButton(self)
            # 21 px tall centres the icon on the first text line (the editor's glyphs sit above
            # its geometric centre), so icon and text share one optical midline.
            self.lead.setFixedSize(24, 21)
            self.lead.setIconSize(QSize(19, 19))
            self.lead.setCursor(Qt.PointingHandCursor)
            self._round_lead(10)
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
        self.editor.viewport().installEventFilter(self)  # A press on the text may become a drag.
        col.addWidget(self.editor)

        self.due_btn: QToolButton | None = None
        if item.type == "reminder":
            self.due_btn = QToolButton(self)
            self.due_btn.setObjectName("dueButton")
            self.due_btn.setToolButtonStyle(Qt.ToolButtonTextBesideIcon)
            self.due_btn.setIconSize(QSize(12, 12))
            self.due_btn.setCursor(Qt.PointingHandCursor)
            self.due_btn.setProperty("hoverRadius", FIELD_RADIUS)  # The radius of its box in note_qss.
            self.due_btn.clicked.connect(lambda: self.dueClicked.emit(self))
            col.addWidget(self.due_btn, 0, Qt.AlignLeft)
        row.addLayout(col, 1)

        self.del_btn = QToolButton(self)
        self.del_btn.setFixedSize(22, 22)
        self.del_btn.setIconSize(QSize(self.DEL_ICON, self.DEL_ICON))
        self.del_btn.setCursor(Qt.PointingHandCursor)
        self.del_btn.setProperty("hoverRadius", 11)
        self.del_btn.setStyleSheet(
            "QToolButton{padding:0;border-radius:11px;} QToolButton:hover{background:transparent;}")
        self.del_btn.clicked.connect(lambda: self._set_armed(True))
        row.addWidget(self.del_btn, 0, Qt.AlignTop)
        self._del_fx = QGraphicsOpacityEffect(self.del_btn)  # Fades with the row highlight.
        self._del_fx.setOpacity(0.0)
        self.del_btn.setGraphicsEffect(self._del_fx)
        self.del_btn.setVisible(False)
        # Keeps the layout stable while the delete button is hidden.
        policy = self.del_btn.sizePolicy()
        policy.setRetainSizeWhenHidden(True)
        self.del_btn.setSizePolicy(policy)
        # The second step floats over the row's end, so the text never re-wraps for it.
        self.del_confirm = DeletePill(theme, self)
        self.del_confirm.icon_size = self.DEL_ICON
        self.del_confirm.setCursor(Qt.PointingHandCursor)
        self.del_confirm.clicked.connect(self._confirm_delete)
        self._confirm_fx = QGraphicsOpacityEffect(self.del_confirm)
        self.del_confirm.setGraphicsEffect(self._confirm_fx)
        self.del_confirm.hide()

        self.apply_theme(theme)
        self.set_editing(False)
        self.retranslate()
        motion.HoverFx.attach(self)

    # --- State ---------------------------------------------------------------
    def retranslate(self) -> None:
        """Updates the placeholder, tooltips and due label for the current language."""
        self.editor.setPlaceholderText(tr("item." + self.item.type))
        self.del_btn.setToolTip(tr("item.delete"))
        self.del_confirm.setText(tr("note.delete"))
        self._place_confirm()
        self.refresh()

    def apply_theme(self, theme: Theme) -> None:
        """Switches the row to ``theme`` and redraws its icons."""
        self.theme = theme
        self.del_btn.setIcon(icons.icon("xmark_circle", theme.tertiary, self.DEL_ICON))
        self.del_confirm.theme = theme
        self.refresh()

    def set_editing(self, editing: bool) -> None:
        """Enters or leaves edit mode, easing the delete button's visibility."""
        self.editing = editing
        self.editor.setReadOnly(not editing)
        if not editing:
            self._set_armed(False)
        # Eased so that a row already under the cursor fades its delete button in or out.
        if self._edit_tween is not None:
            self._edit_tween.stop()
        start, end = self._edit_level, 1.0 if editing else 0.0

        def step(v: float) -> None:
            self._edit_level = start + (end - start) * v
            self._sync_delete()

        self._edit_tween = motion.Tween(self, motion.HoverFx.IN_MS if editing else motion.HoverFx.OUT_MS, step)
        self._edit_tween.start()

    def set_lifted(self, lifted: bool) -> None:
        """Eases the dragged look in or out: a frosted, raised pane and a soft shadow."""
        if lifted and self._shadow is None:
            self._shadow = QGraphicsDropShadowEffect(self)
            self.setGraphicsEffect(self._shadow)
            self._sync_shadow()
        self._ease("_lift", 1.0 if lifted else 0.0, self.RISE_MS if lifted else self.SETTLE_MS)

    def set_backdrop(self, pm: QPixmap | None) -> None:
        """Sets the blurred view of what lies beneath the row, shown while it is lifted."""
        self._backdrop = pm
        self.update()

    def _rise(self) -> float:
        """Returns how far the row has risen: 0 up to the hold's build-up, 1 when lifted."""
        return max(0.0, (self._lift - self.HINT_LIFT) / (1.0 - self.HINT_LIFT))

    def _sync_shadow(self) -> None:
        """Grows the shadow with the lift beyond the hold's build-up; drops it once gone.

        Its size, softness and offset grow together, so the row visibly rises off the note and
        sinks back.
        """
        if self._shadow is not None and self.graphicsEffect() is not self._shadow:
            self._shadow = None  # Replaced, e.g. by the fade of a deleted row.
        if self._shadow is None:
            return
        level = self._rise()
        if level <= 0.0 and not self._dragging:
            self.setGraphicsEffect(None)  # Deletes the effect.
            self._shadow = None
            self._backdrop = None
            return
        color = QColor(0, 0, 0)
        color.setAlphaF(self.SHADOW_ALPHA[self.theme.dark] * level)
        self._shadow.setColor(color)
        self._shadow.setBlurRadius(self.SHADOW_BLUR * level)
        self._shadow.setOffset(0, self.SHADOW_DY * level)

    def _set_armed(self, armed: bool) -> None:
        """Stretches the ×'s hover circle into the Delete pill, or shrinks the pill back."""
        if armed == self._armed_on:
            return
        self._armed_on = armed
        if armed:
            self._disarm.start()
        else:
            self._disarm.stop()
        self._ease("_armed", 1.0 if armed else 0.0, self.CONFIRM_IN_MS if armed else self.CONFIRM_OUT_MS,
                   QEasingCurve.InOutCubic)

    def _confirm_delete(self) -> None:
        """Asks for the item to be deleted; the pill fades away with the row."""
        # A child with an effect of its own would be left out of the row's fade (motion.fade_out).
        self.del_confirm.setGraphicsEffect(None)
        self.deleteRequested.emit(self)

    def _place_confirm(self) -> None:
        """Places the Delete pill over the row's end, its right end on the ×, and sets its morph.

        It has the delete button's height, so it starts as that button's hover circle.
        """
        if self._armed <= 0.0:
            self.del_confirm.hide()
            return
        x = self.del_btn.geometry()
        w = max(self.del_confirm.sizeHint().width(), x.width())
        self.del_confirm.setGeometry(x.right() + 1 - w, x.y(), w, x.height())
        self.del_confirm.set_progress(self._armed)
        if self.del_confirm.graphicsEffect() is not None:
            self._confirm_fx.setOpacity(self._visible_level())
        self.del_confirm.show()
        self.del_confirm.raise_()

    def _sync_delete(self) -> None:
        """Shows the delete button only while editing, at the highlight's opacity.

        It fades out as the row lifts for a drag. While the Delete pill shows, the pill stands in
        for it (it starts as an exact copy of the hovered ×).
        """
        level = self._visible_level() if self._armed <= 0.0 else 0.0
        self._del_fx.setOpacity(level)
        self.del_btn.setVisible(level > 0.0)
        if self._armed > 0.0 and self.del_confirm.graphicsEffect() is not None:
            self._confirm_fx.setOpacity(self._visible_level())

    def _visible_level(self) -> float:
        """Returns how visible the × (or the pill standing in for it) is: hover, edit, not lifted."""
        return self._hover * self._edit_level * (1.0 - self._lift)

    LEAD_W, LEAD_H, ICON = 24, 21, 19  # At scale 1.0; see the lead button.
    DUE_ICON = 12
    DUE_PAD_BOTTOM = 2  # The due button's bottom padding in note_qss.
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
        confirm = QFont(self.del_confirm.font())
        confirm.setPointSizeF(DUE_PT * scale)
        self.del_confirm.setFont(confirm)
        self._place_confirm()
        k = 1 + (scale - 1) * self.ICON_FOLLOW
        self._icon_px = round(self.ICON * k)
        self._due_icon_px = round(self.DUE_ICON * k)
        if self.due_btn is not None:
            due = QFont(self.due_btn.font())
            due.setPointSizeF(DUE_PT * scale)
            self.due_btn.setFont(due)
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
            w = round(self.LEAD_W * k)
            w += (w - self._icon_px) % 2  # Even leftover: the icon centres on a whole pixel.
            self.lead.setFixedSize(w, h)
            self.lead.setIconSize(QSize(self._icon_px, self._icon_px))
            self._round_lead(self.lead.width() / 2 - 2)
            # Shifts the lead left so its circle starts where text items' text does.
            ink = (w - self._icon_px) // 2 + icons.ink_box("checkbox_off", self._icon_px)[0]
            shift = max(0, min(self.PAD_X, math.floor(ink - DOC_MARGIN + 0.5)))  # Halves round up.
            self._row_layout.setContentsMargins(self.PAD_X - shift, self.PAD_Y, self.PAD_X, self.PAD_Y)
            self.refresh()

    def _round_lead(self, radius: float) -> None:
        """Gives the lead button's hover and pressed backgrounds the same corner radius.

        Style sheets take whole pixels only, so the pressed background rounds the radius down.
        """
        self.lead.setProperty("hoverRadius", radius)
        self.lead.setStyleSheet(f"QToolButton{{padding:0;border-radius:{math.floor(radius)}px;}}")

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
        if bool(self.editor.property("dim")) != dim:  # Restyled from note_qss, only on change.
            self.editor.setProperty("dim", dim)
            self.editor.style().unpolish(self.editor)
            self.editor.style().polish(self.editor)

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
            self.lead.setIcon(self._lead_icon(name, color))
            self.lead.setToolTip(tr("item.mark_undone" if item.done else "item.mark_done"))
            self._refresh_due(state, now)

    def _due_icon_dy(self) -> float:
        """Returns how far to draw the calendar icon down so it sits on the label's midline.

        Qt places the icon and the text separately, each rounded to whole pixels, and the icon
        in a box DUE_PAD_BOTTOM px shorter (see note_qss).
        """
        font, ip = self.due_btn.font(), self._due_icon_px
        height = self.due_btn.sizeHint().height()
        text_mid = (height - QFontMetrics(font).height()) // 2 + QFontMetricsF(font).height() / 2
        text_mid += glyph_midline_offset(font)
        _, top, _, bottom = icons.ink_box("calendar", ip)
        icon_mid = (height - self.DUE_PAD_BOTTOM - ip) // 2 + (top + bottom) / 2
        return round((text_mid - icon_mid) * 2) / 2  # To half pixels.

    def _lead_icon(self, name: str, color: str) -> QIcon:
        """Returns a lead icon drawn so its ink centres where the to-do circle's does."""
        _, top, _, bottom = icons.ink_box(name, self._icon_px)
        _, c_top, _, c_bottom = icons.ink_box("checkbox_off", self._icon_px)
        dy = round(((c_top + c_bottom) - (top + bottom)) / 2 * 2) / 2  # To half pixels.
        qicon = QIcon()
        qicon.addPixmap(icons.pixmap(name, color, self._icon_px, dy=dy))
        return qicon

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
        due_icon = QIcon()
        due_icon.addPixmap(icons.pixmap("calendar", color, self._due_icon_px, dy=self._due_icon_dy()))
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

    def _ease(self, attr: str, target: float, duration: int, easing=motion.EASE_OUT) -> None:
        """Eases the level ``attr`` ("_hover", "_lift" or "_armed") to ``target``."""
        tween = getattr(self, attr + "_tween")
        if tween is not None:
            tween.stop()
        start = getattr(self, attr)

        def step(v: float) -> None:
            setattr(self, attr, start + (target - start) * v)
            self._sync_delete()
            self._sync_shadow()
            self._place_confirm()
            self.update()

        tween = motion.Tween(self, duration, step, easing=easing)
        setattr(self, attr + "_tween", tween)
        tween.start()

    def enterEvent(self, e):
        self._leave_later.stop()
        self._ease("_hover", 1.0, motion.HoverFx.IN_MS)
        super().enterEvent(e)

    def leaveEvent(self, e):
        """Eases the highlight out; a Delete pill first shrinks back into the ×, which then fades."""
        if self._armed_on:
            self._set_armed(False)
            self._leave_later.start()
        else:
            self._ease("_hover", 0.0, motion.HoverFx.OUT_MS)
        super().leaveEvent(e)

    def resizeEvent(self, e):
        super().resizeEvent(e)
        self._place_confirm()

    # --- Hold and drag -------------------------------------------------------
    def _pressed(self, e) -> None:
        """Arms the hold, and its build-up, on a left press (in either mode)."""
        if e.button() == Qt.LeftButton:
            self._press = e.globalPosition().toPoint()
            self._hold.start()
            self._hint.start()

    def _moved(self, e) -> bool:
        """Drags the row, or cancels a pending hold once the pointer moves; True if consumed."""
        if self._dragging:
            self.dragMoved.emit(e.globalPosition().toPoint())
            return True
        if self._press is not None:
            if (e.globalPosition().toPoint() - self._press).manhattanLength() > QApplication.startDragDistance():
                self._cancel_hold()  # Moving early selects text instead.
        return False

    def _show_hold(self) -> None:
        """Starts the hold's build-up, once the press has lasted HINT_MS."""
        self.holdStarted.emit(self)
        self._ease("_lift", self.HINT_LIFT, self.HOLD_MS - self.HINT_MS, QEasingCurve.InOutQuad)

    def _cancel_hold(self) -> None:
        """Forgets a pending hold and lets its build-up fade back."""
        self._hold.stop()
        self._hint.stop()
        self._press = None
        if self._lift > 0.0 and not self._dragging:
            self._ease("_lift", 0.0, self.SETTLE_MS)

    def _released(self, e) -> bool:
        """Drops a dragged row or forgets a pending hold; True if consumed."""
        if e.button() != Qt.LeftButton:
            return False
        if not self._dragging:
            self._cancel_hold()
            return False
        self._press = None
        self._dragging = False
        self.unsetCursor()
        self.editor.viewport().setCursor(Qt.IBeamCursor if self.editing else Qt.ArrowCursor)
        self.dragFinished.emit()
        return True

    def _start_drag(self) -> None:
        """Starts the drag once a press has been held still for HOLD_MS."""
        if self._press is None:
            return
        self._dragging = True
        self._set_armed(False)
        # Drops any selection the press began; the caret stays where the press was.
        self.editor.setTextCursor(self.editor.cursorForPosition(self.editor.viewport().mapFromGlobal(self._press)))
        self.setCursor(Qt.ClosedHandCursor)
        self.editor.viewport().setCursor(Qt.ClosedHandCursor)
        self.dragStarted.emit(self, QCursor.pos())

    def eventFilter(self, obj, ev):
        """Watches presses on the text, which keeps its own mouse handling unless dragged."""
        kind = ev.type()
        if kind == QEvent.MouseButtonPress:
            self._pressed(ev)
        elif kind == QEvent.MouseMove:
            return self._moved(ev)
        elif kind == QEvent.MouseButtonRelease:
            return self._released(ev)
        return False

    def mousePressEvent(self, e):
        self._pressed(e)
        super().mousePressEvent(e)

    def mouseMoveEvent(self, e):
        if not self._moved(e):
            super().mouseMoveEvent(e)

    def mouseReleaseEvent(self, e):
        if not self._released(e):
            super().mouseReleaseEvent(e)

    def paintEvent(self, _):
        """Paints the hover highlight, or the frosted pane while the row is held or dragged."""
        if self._hover <= 0.0 and self._lift <= 0.0:
            return
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.setPen(Qt.NoPen)
        rect = QRectF(self.rect())
        if self._lift > 0.0:
            path = QPainterPath()
            path.addRoundedRect(rect, self.RADIUS, self.RADIUS)
            rise = self._rise()
            if self._backdrop is not None and rise > 0.0:
                # Frosted glass: rows gliding underneath show only as soft shapes, not as text.
                p.save()
                p.setClipPath(path)
                p.setOpacity(rise)
                p.drawPixmap(rect.toRect(), self._backdrop)
                p.restore()
            tint = QColor(self.theme.lifted_color())
            tint.setAlphaF(LIFTED_ALPHA * self._lift)
            p.fillPath(path, tint)
        # The highlight gives way to the frosted pane as the row lifts.
        strength = self.HOVER_STRENGTH * self._hover * (1.0 - self._lift)
        if strength > 0.0:
            color = css_color(self.theme.hover)
            color.setAlphaF(color.alphaF() * strength)
            p.setBrush(color)
            p.drawRoundedRect(rect, self.RADIUS, self.RADIUS)
