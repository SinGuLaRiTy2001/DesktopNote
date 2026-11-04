"""A single floating glass note window, plus the app's glass menu."""
from __future__ import annotations

import html
import math
from ctypes import wintypes
from datetime import datetime

from PySide6.QtCore import QEasingCurve, QEvent, QPoint, QRect, QRectF, QSize, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QCursor, QFont, QLinearGradient, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import (
    QApplication,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QMenu,
    QScrollArea,
    QStackedWidget,
    QToolButton,
    QVBoxLayout,
    QWidget,
    QWidgetAction,
)

from . import icons, motion, win_effects
from .i18n import tr
from .models import Item, Note, now_iso
from .docking import EdgeDock
from .glass import GlassWindow
from .reorder import RowReorder
from .snapping import SnapMove
from .theme import BODY_PT, Theme, glow_color, menu_qss, note_qss, tag_color
from .widgets import (
    ColorPopup,
    DateTimePopup,
    Grabber,
    ItemRow,
    OverlayScrollBar,
    TagPicker,
    TitleEdit,
    css_color,
    default_due,
)

EDGE = 7  # Width in px of the resize-sensitive border.
CORNER = 16  # Larger grab zone at the corners.
MIN_SIZE = QSize(200, 150)  # Wider where the delete confirmation needs it.
GLOW_HEIGHT = 44  # The tag colour fades out before the first row, so text contrast holds.
GLOW_MS = 260  # Cross-fade when the tag changes.
FLASH_MS = 900  # Pulse when a note is picked from the tray list.


class GlassMenu(QMenu):
    """The app's menu: a rounded panel whose current row highlights edge to edge in soft grey.

    There is one current row and the highlight moves with it at once, without
    fading, so it never trails the cursor. Actions with the property keepOpen=True run without
    closing the menu (settings toggles). Actions made switches with set_switch() get a
    switch at the right edge whose knob is eased. Widget rows (tag dots, sliders) bring their own
    hover.

    Attributes:
        theme: The theme the menu paints with; set by glass_menu() and on theme changes.
        RADIUS: Corner radius of the panel.
        SWITCH_W: Switch width; with SWITCH_H, the switch's proportions at menu scale.
        SWITCH_H: Switch height.
        SWITCH_INSET: Distance of the switch from the panel's right edge, like the other controls.
        SWITCH_MS: Duration of the knob animation.
    """

    RADIUS = 12
    SWITCH_W, SWITCH_H = 32, 20
    SWITCH_INSET = 14
    SWITCH_MS = 200

    def __init__(self, parent=None):
        super().__init__(parent)
        self.theme: Theme | None = None
        self._lit = None  # The highlighted (current) row.
        self._switch_level: dict[int, float] = {}
        self._switch_tween: dict[int, motion.Tween] = {}
        self._over_widget = False  # Pointer on a widget row: such rows get the events, not the menu.
        self.hovered.connect(lambda _action: self._sync_hover())

    def actionEvent(self, e):
        """Watches widget rows for Enter/Leave so the row highlight can step aside for them."""
        super().actionEvent(e)
        if e.type() == QEvent.ActionAdded and isinstance(e.action(), QWidgetAction):
            w = e.action().defaultWidget()
            if w is not None:
                w.installEventFilter(self)

    def eventFilter(self, obj, ev):
        if ev.type() in (QEvent.Enter, QEvent.Leave):
            self._over_widget = ev.type() == QEvent.Enter
            self._sync_hover()
        return False

    # --- Hover ---------------------------------------------------------------
    def _sync_hover(self) -> None:
        """Highlights the current row, or none while the pointer is on a widget row."""
        active = None if self._over_widget else self.activeAction()
        if active is not None and (isinstance(active, QWidgetAction) or active.isSeparator() or not active.isEnabled()):
            active = None
        if active is not self._lit:
            self._lit = active
            self.update()

    def _ease(self, levels, tweens, action, target: float, duration: int) -> None:
        """Eases levels[id(action)] to target, keeping its running tween in tweens."""
        key = id(action)
        start = levels.get(key, 0.0)
        if start == target and key not in tweens:
            return
        if key in tweens:
            tweens.pop(key).stop()
        if not self.isVisible():
            levels[key] = target
            return

        def step(v: float) -> None:
            levels[key] = start + (target - start) * v
            self.update()

        def done() -> None:
            tweens.pop(key, None)

        tweens[key] = motion.Tween(self, duration, step, done)
        tweens[key].start()

    def leaveEvent(self, e):
        super().leaveEvent(e)
        QTimer.singleShot(0, self._sync_hover)  # Qt drops the active row unless a submenu is open.

    def set_switch(self, action, on: bool) -> None:
        """Makes action a switch row, or moves its knob (eased) to the new state."""
        action.setProperty("switch", True)
        if id(action) not in self._switch_level:
            self._switch_level[id(action)] = 1.0 if on else 0.0
            self.update()
            return
        self._ease(self._switch_level, self._switch_tween, action, 1.0 if on else 0.0, self.SWITCH_MS)

    # --- Painting ------------------------------------------------------------
    def _panel(self) -> QPainterPath:
        """Returns the rounded outline of the panel, inset half a pixel for a crisp border."""
        path = QPainterPath()
        path.addRoundedRect(QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5), self.RADIUS, self.RADIUS)
        return path

    def paintEvent(self, e):
        """Paints the panel and row highlight under Qt's rows, then the switches over them."""
        theme = self.theme
        if theme is not None:  # Panel and row highlight first; Qt then draws icons and text on top.
            p = QPainter(self)
            p.setRenderHint(QPainter.Antialiasing)
            panel = self._panel()
            p.fillPath(panel, css_color(theme.menu_bg))
            p.setClipPath(panel)
            if self._lit is not None and self._lit in self.actions():
                row_rect = self.actionGeometry(self._lit)
                p.fillRect(QRectF(0, row_rect.top(), self.width(), row_rect.height()), css_color(theme.menu_hover))
            p.setClipping(False)
            p.setPen(QPen(css_color(theme.separator), 1))
            p.drawPath(panel)
            p.end()
        super().paintEvent(e)
        if theme is None or not self._switch_level:
            return
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        for action in self.actions():
            if not action.property("switch") or id(action) not in self._switch_level:
                continue
            level = self._switch_level[id(action)]
            row_rect = self.actionGeometry(action)
            box = QRectF(self.width() - self.SWITCH_INSET - self.SWITCH_W,
                         row_rect.center().y() - self.SWITCH_H / 2 + 1, self.SWITCH_W, self.SWITCH_H)
            off, on = css_color(theme.seg_track), QColor(theme.switch_on)
            track = QColor(*(round(x + (y - x) * level) for x, y in zip(off.getRgb(), on.getRgb())))
            p.setPen(Qt.NoPen)
            p.setBrush(track)
            p.drawRoundedRect(box, self.SWITCH_H / 2, self.SWITCH_H / 2)
            knob_d = self.SWITCH_H - 4
            knob = QRectF(box.left() + 2 + level * (self.SWITCH_W - 4 - knob_d), box.top() + 2, knob_d, knob_d)
            p.setBrush(QColor(0, 0, 0, 30))
            p.drawEllipse(knob.adjusted(-0.5, 0, 0.5, 1))
            p.setBrush(QColor(255, 255, 255))
            p.drawEllipse(knob)

    def _keep_open(self, action) -> bool:
        """Triggers a keepOpen action without closing the menu; returns whether it did."""
        if action is None or not action.isEnabled() or action.menu() is not None or not action.property("keepOpen"):
            return False
        action.trigger()
        return True

    def mouseReleaseEvent(self, e):
        if e.button() == Qt.LeftButton and self._keep_open(self.actionAt(e.position().toPoint())):
            e.accept()
            return
        super().mouseReleaseEvent(e)

    def keyPressEvent(self, e):
        if e.key() in (Qt.Key_Return, Qt.Key_Enter, Qt.Key_Space) and self._keep_open(self.activeAction()):
            e.accept()
            return
        super().keyPressEvent(e)

    def showEvent(self, e):
        """Resets the highlight and pops the menu in, gliding towards the cursor's side."""
        super().showEvent(e)
        self._lit = None  # A fresh highlight for each opening.
        above = self.y() < QCursor.pos().y()  # Popped up above the cursor: rises into place.
        motion.pop_in(self, dy=6 if above else -6)

    def hideEvent(self, e):
        """Leaves a fading ghost behind and refreshes the parent menu's highlight."""
        motion.Ghost.from_render(self, self.RADIUS)
        motion.stop(self)
        super().hideEvent(e)
        parent = self.parentWidget()
        if isinstance(parent, GlassMenu):  # A submenu closed, so its row may no longer be active.
            QTimer.singleShot(0, parent._sync_hover)

    def mousePressEvent(self, e):
        """Passes a press outside every open menu on to the note under it."""
        # Such a press closes the menus and is used up by doing so.
        if not any(m.isVisible() and m.geometry().contains(e.globalPosition().toPoint())
                   for m in QApplication.topLevelWidgets() if isinstance(m, QMenu)):
            self._hand_click_to_note(e.globalPosition().toPoint())
        super().mousePressEvent(e)

    @staticmethod
    def _hand_click_to_note(pos: QPoint) -> None:
        """Activates the note at pos before the menu hides.

        A click on a note that closes the menu is used up by the menu, and once the menu hides
        Windows gives focus back to the app that was active before, which then comes to the front
        over the note (and flashes, even if the note is raised again right after). Activating the
        note now lets focus pass straight from the menu to the note.
        """
        target = QApplication.widgetAt(pos)  # Can be None while a popup grabs the mouse.
        note = target.window() if target is not None else None
        if not isinstance(note, NoteWindow):
            under = [w for w in QApplication.topLevelWidgets()
                     if isinstance(w, NoteWindow) and w.isVisible() and w.frameGeometry().contains(pos)]
            note = under[-1] if under else None
        if note is not None:
            note.raise_()
            note.activateWindow()


def glass_menu(theme: Theme, parent: QWidget) -> QMenu:
    """Returns a new frameless, translucent GlassMenu styled with theme."""
    menu = GlassMenu(parent)
    menu.setWindowFlags(menu.windowFlags() | Qt.FramelessWindowHint | Qt.NoDropShadowWindowHint)
    menu.setAttribute(Qt.WA_TranslucentBackground)
    menu.setStyleSheet(menu_qss(theme))
    menu.theme = theme
    return menu


class _ModeSlot(QWidget):
    """The pencil and Done in one place: the pencil's background stretches into Done's.

    The slot paints one shared hover background, translucent like any button hover (so the
    buttons opt out of HoverFx). As the mode switches, that background stretches leftwards from
    the pencil's width to Done's while its right edge stays put, the pencil fades out, and Done
    slides in from the right while it fades in; switching back plays the same in reverse.

    The slot is as wide as the wider of the two and both stay right-aligned in it, so neither is
    ever cut off and the toolbar's other buttons never move. The one fading out stops taking
    clicks halfway through.
    """

    RADIUS = 7  # HoverFx's corner radius, as on the toolbar's other buttons.
    PENCIL_END, DONE_START = 0.4, 0.35  # Share of the morph by which the pencil is gone / Done starts.
    DONE_SLIDE = 10  # Px Done slides in from the right while it fades in.

    def __init__(self, edit_btn: QToolButton, done_btn: QToolButton, parent: QWidget):
        super().__init__(parent)
        self._edit, self._done = edit_btn, done_btn
        self._fx = []
        for button in (edit_btn, done_btn):
            button.setParent(self)
            button.setProperty("hoverFx", False)  # The slot paints the hover for both.
            button.installEventFilter(self)
            fx = motion.ShiftFade(button)
            button.setGraphicsEffect(fx)
            self._fx.append(fx)
        self._level = 0.0
        self._hover = 0.0
        self._hover_on = False
        self._hover_tween: motion.Tween | None = None
        self.set_level(0.0)

    @staticmethod
    def _size(button: QToolButton) -> QSize:
        """Returns the size the button takes: its size hint within its own size limits."""
        return button.sizeHint().expandedTo(button.minimumSize()).boundedTo(button.maximumSize())

    def set_level(self, level: float) -> None:
        """Shows the pencil at 0, Done at 1, and the morph from one to the other in between."""
        self._level = level
        edit, done = self._size(self._edit), self._size(self._done)
        self.setFixedSize(max(edit.width(), done.width()), max(edit.height(), done.height()))
        out = QEasingCurve(QEasingCurve.OutCubic)
        pencil = 1.0 - out.valueForProgress(min(1.0, level / self.PENCIL_END))
        done_in = out.valueForProgress(min(1.0, max(0.0, (level - self.DONE_START) / (1.0 - self.DONE_START))))
        self._fx[0].set(pencil)
        self._fx[1].set(done_in, dx=self.DONE_SLIDE * (1.0 - done_in))
        self._edit.setVisible(level < 1.0)
        self._done.setVisible(level > 0.0)
        self._edit.setAttribute(Qt.WA_TransparentForMouseEvents, level >= 0.5)
        self._done.setAttribute(Qt.WA_TransparentForMouseEvents, level < 0.5)
        self._place()
        self.update()

    def _place(self) -> None:
        """Right-aligns both buttons in the slot, vertically centred."""
        for button in (self._edit, self._done):
            size = self._size(button)
            button.setGeometry(self.width() - size.width(), (self.height() - size.height()) // 2,
                               size.width(), size.height())

    def _background(self) -> QRectF:
        """Returns the shared background at the current level: the pencil's, stretched to Done's."""
        edit, done = QRectF(self._edit.geometry()), QRectF(self._done.geometry())
        t = self._level
        left = edit.left() + (done.left() - edit.left()) * t
        top = edit.top() + (done.top() - edit.top()) * t
        bottom = edit.bottom() + (done.bottom() - edit.bottom()) * t
        return QRectF(left, top, self.width() - left, bottom - top + 1)

    def eventFilter(self, obj, ev):
        """Eases the shared hover in while the pointer is over either button."""
        if ev.type() in (QEvent.Enter, QEvent.Leave):
            on = self._edit.underMouse() or self._done.underMouse()
            if ev.type() == QEvent.Enter:
                on = True
            if on != self._hover_on:
                self._hover_on = on
                self._ease_hover(1.0 if on else 0.0)
        return False

    def _ease_hover(self, target: float) -> None:
        if self._hover_tween is not None:
            self._hover_tween.stop()
        start = self._hover

        def step(v: float) -> None:
            self._hover = start + (target - start) * v
            self.update()

        duration = motion.HoverFx.IN_MS if target > start else motion.HoverFx.OUT_MS
        self._hover_tween = motion.Tween(self, duration, step)
        self._hover_tween.start()

    def paintEvent(self, _):
        """Paints the shared hover background behind the buttons."""
        if self._hover <= 0.0:
            return
        color = QColor(motion.HoverFx.color)
        color.setAlphaF(color.alphaF() * self._hover)
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.setPen(Qt.NoPen)
        p.setBrush(color)
        p.drawRoundedRect(self._background(), self.RADIUS, self.RADIUS)

    def resizeEvent(self, e):
        super().resizeEvent(e)
        self._place()


class NoteWindow(GlassWindow):
    """A single floating glass note: a title, a scrollable list of item rows and a toolbar.

    The window is frameless and paints its own rounded glass over the system acrylic (or a blur
    fallback), with an optional tag-colour glow along the top. It resizes from its edges, moves by
    its grabber (snapping next to nearby notes and monitor edges, hiding at an edge), and switches
    between viewing and editing mode.

    Signals:
        changed: Content or geometry changed and should be persisted.
        newNoteRequested: A new note was requested from this note's menu; carries this window.
        deleteRequested: Deleting this note was requested (and confirmed if it has content);
            carries this window.

    Attributes:
        note: The note model shown and edited by the window.
        theme: The current theme.
        soon_minutes: How long before a reminder's due time it counts as due soon.
        text_scale: Scale factor applied to the note's text.
        editing: Whether the note is in editing mode.
        rows: The item rows, in note order (excluding rows still collapsing away).
    """

    MODE_STAGGER = 0.2  # Share of the mode switch by which each add button follows the one before.
    MODE_RISE = 6  # Px the add buttons rise into place as they fade in.

    changed = Signal()
    newNoteRequested = Signal(object)
    deleteRequested = Signal(object)

    def __init__(self, note: Note, theme: Theme, soon_minutes: int, text_scale: float = 1.0):
        super().__init__(theme)
        self.text_scale = text_scale
        self.note = note
        self.soon_minutes = soon_minutes
        self.editing = False
        self.rows: list[ItemRow] = []
        self._snap_move = SnapMove(self)  # Snap to align while dragged.
        self.dock = EdgeDock(self)
        self._moved = False  # Whether the current move or resize moved the window.
        self._snap_target: NoteWindow | None = None  # The note this one is snapped to.
        self._cursor_watch = QTimer(self)  # Runs while a resize cursor is shown.
        self._cursor_watch.setInterval(40)
        self._cursor_watch.timeout.connect(self._check_resize_cursor)
        self._glow = self._glow_target()  # Tag colour washed over the top of the glass.
        self._glow_tween: motion.Tween | None = None
        self._flash = 0.0  # Tray-list highlight, 0..1.
        self._flash_tween: motion.Tween | None = None
        self._mode = 0.0  # 0 viewing .. 1 editing, at an even pace; each part eases on its own.
        self._mode_from = self._mode_to = 0.0  # The mode level a switch runs from and to.
        self._hint_mix = 0.0  # How far the empty-note hint shows its editing text, 0..1.
        self._hint_mix_from = 0.0  # _hint_mix where the switch started.
        self._mode_tween: motion.Tween | None = None

        self.setWindowTitle("DesktopNote")
        self.setMouseTracking(True)
        self.setMinimumSize(MIN_SIZE)
        self.setWindowFlags(self._window_flags())

        self.body = QWidget(self)  # Holds everything; slides within the window (see set_content_shift).
        self.body.setMouseTracking(True)
        root = QVBoxLayout(self.body)
        root.setContentsMargins(EDGE, 2, EDGE - 2, EDGE - 1)
        root.setSpacing(0)

        # Top bar, with the drag handle in the middle.
        top = QHBoxLayout()
        top.setContentsMargins(0, 0, 0, 0)
        self.pin_mark = QLabel(self)
        self.pin_mark.setFixedSize(18, 18)
        policy = self.pin_mark.sizePolicy()
        policy.setRetainSizeWhenHidden(True)  # Unpinned notes must not shift the grabber.
        self.pin_mark.setSizePolicy(policy)
        self._pin_fx = motion.ShiftFade(self.pin_mark)
        self.pin_mark.setGraphicsEffect(self._pin_fx)
        self._pin_shown = note.pinned
        self._pin_tween: motion.Tween | None = None
        self._pin_fx.set(1.0 if note.pinned else 0.0)
        self.pin_mark.setVisible(note.pinned)
        self.grabber = Grabber(theme, self)
        # The pin takes 18 px on the right and this spacing 16 px on the left: with root margins of
        # 7 px left and 5 px right, this centres the grabber on the window, not the content area.
        top.addSpacing(16)
        top.addStretch(1)
        top.addWidget(self.grabber)
        top.addStretch(1)
        top.addWidget(self.pin_mark)
        root.addLayout(top)

        # Optional title: always offered while editing, otherwise shown only when filled in.
        self.title_edit = TitleEdit(note.title, self)
        self.title_edit.set_text_scale(text_scale)
        self.title_edit.textEdited.connect(self._on_title)
        self.title_edit.submitted.connect(self._title_submitted)
        self.title_edit.escapePressed.connect(lambda: self.set_editing(False))
        self.title_edit.editRequested.connect(lambda: self._edit_title())
        root.addWidget(self.title_edit)

        # Scrollable content.
        self.scroll = QScrollArea(self)
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QFrame.NoFrame)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        # OverlayScrollBar is used instead, because it takes no width.
        self.scroll.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.content = QWidget()
        self.content_lay = QVBoxLayout(self.content)
        # Rows carry 4 px side and 2 px vertical padding of their own (room for the hover
        # highlight); these margins plus that padding keep the text in its established position.
        self.content_lay.setContentsMargins(2, 1, 2, 7)
        self.content_lay.setSpacing(0)
        # The empty-note hint: a text for each mode in the same place, cross-faded as it switches.
        self.hint = QWidget(self.content)
        hint_lay = QGridLayout(self.hint)
        hint_lay.setContentsMargins(0, 0, 0, 0)
        self.placeholder = QLabel(self.hint)  # Viewing mode.
        self.placeholder_edit = QLabel(self.hint)
        self._hint_fx = []
        for label in (self.placeholder, self.placeholder_edit):
            label.setContentsMargins(4, 1, 4, 1)
            label.setObjectName("placeholder")
            label.setTextFormat(Qt.RichText)  # The viewing hint embeds the toolbar's pencil icon.
            label.setAlignment(Qt.AlignHCenter | Qt.AlignTop)
            label.setWordWrap(True)
            hint_lay.addWidget(label, 0, 0, Qt.AlignTop)
            fx = motion.ShiftFade(label)
            label.setGraphicsEffect(fx)
            self._hint_fx.append(fx)
        self._hint_fx[1].set(0.0)
        self._scale_hint()
        self.content_lay.addWidget(self.hint)
        self.content_lay.addStretch(1)
        self.scroll.setWidget(self.content)
        root.addWidget(self.scroll, 1)
        self.scroll_indicator = OverlayScrollBar(self.scroll, theme)
        self.reorder = RowReorder(self.scroll, self.content_lay, self.scroll_indicator)
        self.reorder.moved.connect(self._move_row)

        self.sep = QFrame(self)
        self.sep.setObjectName("separator")
        root.addWidget(self.sep)

        # Bottom bar: the toolbar, or the delete confirmation.
        self.bottom = QStackedWidget(self)
        self.bottom.setFixedHeight(38)
        self.bottom.addWidget(self._build_toolbar())
        self.bottom.addWidget(self._build_confirm())
        root.addWidget(self.bottom)

        for item in note.items:
            self._add_row(item)

        self.apply_theme(theme)
        self.setGeometry(note.x, note.y, note.w, note.h)
        self.set_editing(False)
        self.retranslate()
        motion.HoverFx.attach(self)

    # --- UI ------------------------------------------------------------------
    def retranslate(self) -> None:
        """Applies all texts again in the current language."""
        for button, key in ((self.btn_text, "note.add_text"), (self.btn_todo, "note.add_todo"),
                            (self.btn_reminder, "note.add_reminder"), (self.btn_edit, "note.edit"),
                            (self.btn_more, "note.more")):
            button.setToolTip(tr(key))
        self.btn_done.setText(tr("note.done"))
        self.mode_slot.set_level(self._mode)  # Done's width depends on the language.
        self.confirm_label.setText(tr("note.confirm_delete"))
        self.btn_cancel.setText(tr("note.cancel"))
        self.btn_confirm_delete.setText(tr("note.delete"))
        self.title_edit.setPlaceholderText(tr("note.title"))
        self.grabber.setToolTip(tr("note.drag"))
        self._fit_minimum_width()
        self._update_placeholder()
        for row in self.rows:
            row.retranslate()

    def _update_placeholder(self) -> None:
        """Sets the empty-note hints of both modes, embedding the pencil icon in the viewing one."""
        self.placeholder_edit.setText(html.escape(tr("note.placeholder_edit")))
        icon_scale = 1 + (self.text_scale - 1) * ItemRow.ICON_FOLLOW  # Grows with the hint's text.
        # Lifted onto the middle of the hint's text.
        pencil = icons.inline_img("pencil", self.theme.secondary, round(15 * icon_scale), lift=2.0)
        self.placeholder.setText(html.escape(tr("note.placeholder_view")).replace("{icon}", pencil))

    def _tool(self, slot) -> QToolButton:
        """Returns a toolbar icon button that calls slot when clicked."""
        button = QToolButton(self)
        button.setIconSize(icons.ICON_SIZE)
        button.setFixedSize(30, 30)
        button.setCursor(Qt.PointingHandCursor)
        button.clicked.connect(slot)
        return button

    def _build_toolbar(self) -> QWidget:
        bar = QWidget(self)
        lay = QHBoxLayout(bar)
        lay.setContentsMargins(2, 4, 2, 2)
        lay.setSpacing(2)
        self.btn_text = self._tool(lambda: self.add_item("text"))
        self.btn_todo = self._tool(lambda: self.add_item("todo"))
        self.btn_reminder = self._tool(lambda: self.add_item("reminder"))
        self.btn_edit = self._tool(lambda: self.set_editing(True))
        self.btn_done = QToolButton(self)
        self.btn_done.setObjectName("doneButton")
        self.btn_done.setCursor(Qt.PointingHandCursor)
        self.btn_done.clicked.connect(lambda: self.set_editing(False))
        self.btn_more = self._tool(self._show_menu)
        self._add_fx = []
        for w in (self.btn_text, self.btn_todo, self.btn_reminder):
            lay.addWidget(w)
            fx = motion.ShiftFade(w)  # They rise into place as they fade in.
            w.setGraphicsEffect(fx)
            self._add_fx.append(fx)
        lay.addStretch(1)
        self.mode_slot = _ModeSlot(self.btn_edit, self.btn_done, bar)
        lay.addWidget(self.mode_slot)
        lay.addWidget(self.btn_more)
        return bar

    def _fit_minimum_width(self) -> None:
        """Keeps the note at least as wide as its delete confirmation in this language."""
        bar = self.bottom.widget(1).layout()
        margins, root = bar.contentsMargins(), self.body.layout().contentsMargins()
        needed = (margins.left() + margins.right() + root.left() + root.right() + 2 * bar.spacing()
                  + sum(w.sizeHint().width() for w in (self.confirm_label, self.btn_cancel, self.btn_confirm_delete)))
        width = max(MIN_SIZE.width(), needed)
        self.setMinimumWidth(width)
        if self.note.w < width and self.dock.edge:  # The dock owns a docked note's geometry.
            self.note.w = width
            self.dock.validate()

    def _build_confirm(self) -> QWidget:
        bar = QWidget(self)
        lay = QHBoxLayout(bar)
        lay.setContentsMargins(8, 4, 2, 2)
        self.confirm_label = QLabel(bar)
        self.btn_cancel = QToolButton(bar)
        self.btn_cancel.clicked.connect(lambda: self.bottom.setCurrentIndex(0))
        self.btn_confirm_delete = QToolButton(bar)
        self.btn_confirm_delete.setObjectName("confirmDelete")
        self.btn_confirm_delete.clicked.connect(lambda: self.deleteRequested.emit(self))
        lay.addWidget(self.confirm_label)
        lay.addStretch(1)
        lay.addWidget(self.btn_cancel)
        lay.addWidget(self.btn_confirm_delete)
        return bar

    def apply_theme(self, theme: Theme) -> None:
        """Restyles the note, its rows and its glass for theme."""
        self.theme = theme
        self.grabber.theme = theme
        self.scroll_indicator.theme = theme
        self._glow = self._glow_target()
        self._sync_top_bar()
        self.setStyleSheet(note_qss(theme))
        motion.HoverFx.color = css_color(theme.hover)
        icon_color = theme.secondary
        self.btn_text.setIcon(icons.icon("text", icon_color))
        self.btn_todo.setIcon(icons.icon("checklist", icon_color))
        self.btn_reminder.setIcon(icons.icon("bell", icon_color))
        self.btn_edit.setIcon(icons.icon("pencil", icon_color))
        self.btn_more.setIcon(icons.icon("ellipsis", icon_color))
        self._update_placeholder()  # The inline icon is tinted with the theme.
        for row in self.rows:
            row.apply_theme(theme)
        self._apply_glass()
        self.update()

    def _window_flags(self) -> Qt.WindowFlags:
        """Returns the window flags for the backdrop in use and the note's pinned state."""
        flags = self.glass_flags()
        if self.note.pinned:
            flags |= Qt.WindowStaysOnTopHint
        return flags

    # --- Items ---------------------------------------------------------------
    def _add_row(self, item: Item, index: int | None = None) -> ItemRow:
        """Creates and wires up a row for item and inserts it at index (default: the end)."""
        row = ItemRow(item, self.theme, self.soon_minutes, self.content)
        row.set_text_scale(self.text_scale)
        row.changed.connect(self._touch)
        row.deleteRequested.connect(self._delete_row)
        row.newItemAfter.connect(self._new_after)
        row.focusPrevious.connect(self._backspace_row)
        row.editRequested.connect(lambda: self.set_editing(True))
        row.finishEditing.connect(lambda: self.set_editing(False))
        row.dueClicked.connect(self._pick_due)
        row.dragStarted.connect(lambda r, pointer: self.reorder.start(self.rows, r, pointer))
        row.dragMoved.connect(self.reorder.move)
        row.dragFinished.connect(self.reorder.finish)
        row.holdStarted.connect(lambda _row: self.reorder.prepare())
        if index is None:
            index = len(self.rows)
        # Anchors on the neighbouring row rather than on index, because rows still collapsing
        # away stay in the layout for a moment. The layout ends with a stretch.
        if index < len(self.rows):
            pos = self.content_lay.indexOf(self.rows[index])
        else:
            pos = self.content_lay.count() - 1
        self.rows.insert(index, row)
        self.content_lay.insertWidget(pos, row)
        row.set_editing(self.editing)
        self.hint.setVisible(False)
        return row

    def add_item(self, kind: str, index: int | None = None) -> ItemRow:
        """Adds a new item and focuses its row, switching to editing mode first if needed.

        Args:
            kind: The item type: "text", "todo" or "reminder". Reminders get a default due time.
            index: Position in the note; defaults to the end.

        Returns:
            The new row, which expands into view.
        """
        if not self.editing:
            self.set_editing(True)
        item = Item(type=kind)
        if kind == "reminder":
            item.due = default_due().isoformat(timespec="minutes")
        if index is None:
            index = len(self.rows)
        self.note.items.insert(index, item)
        row = self._add_row(item, index)
        row.focus_editor()
        motion.expand_in(row, on_done=lambda: self.scroll_indicator.ensure_visible(row))
        # Glide towards the row's full height while it expands; the view follows it down.
        QTimer.singleShot(0, row, lambda: self.scroll_indicator.ensure_visible(row, row.sizeHint().height()))
        self._touch()
        return row

    def show_new_item(self, item: Item) -> None:
        """Shows an item that was just added to the end of the note, expanding it into view."""
        row = self._add_row(item)
        motion.expand_in(row)
        QTimer.singleShot(0, row, lambda: self.scroll_indicator.ensure_visible(row, row.sizeHint().height()))

    def refresh_item(self, item: Item) -> None:
        """Shows the current text, due time and done state of an item changed from outside."""
        for row in self.rows:
            if row.item is item:
                if row.editor.toPlainText() != item.text:
                    row.editor.setPlainText(item.text)
                row.refresh()

    def forget_item(self, item: Item) -> None:
        """Fades out the row of an item that was deleted from outside (already gone from the note)."""
        for row in self.rows:
            if row.item is item:
                self.rows.remove(row)
                motion.fade_out(row)  # Deletes the widget when done.
                self.hint.setVisible(not self.rows)
                return

    def _remove_row(self, row: ItemRow, deleted: bool = False) -> int:
        """Removes row and its item, animating the widget away; returns its former index.

        Args:
            row: The row to remove.
            deleted: Whether it was deleted on purpose: it fades while the rows below cover it.
                Otherwise (an emptied row) it collapses quickly.
        """
        idx = self.rows.index(row)
        self.rows.pop(idx)
        self.note.items.remove(row.item)
        if deleted:
            motion.fade_out(row)  # Deletes the widget when done.
        else:
            motion.collapse_out(row)
        self.hint.setVisible(not self.rows)
        self._touch()
        return idx

    def _move_row(self, row: ItemRow, before: ItemRow | None) -> None:
        """Moves ``row`` (and its item) in front of ``before``, or to the end, as dropped.

        Rows removed since the drag began are skipped; a removed ``before`` means the end.
        """
        if row not in self.rows:
            return
        self.rows.remove(row)
        self.rows.insert(self.rows.index(before) if before in self.rows else len(self.rows), row)
        self.note.items = [r.item for r in self.rows]
        first = min(self.content_lay.indexOf(r) for r in self.rows)
        for r in self.rows:
            self.content_lay.removeWidget(r)
        for i, r in enumerate(self.rows):
            self.content_lay.insertWidget(first + i, r)
        self._touch()

    def _delete_row(self, row: ItemRow) -> None:
        self._remove_row(row, deleted=True)

    def _new_after(self, row: ItemRow) -> None:
        """Handles Enter in row: continues the list, or ends it on an empty row."""
        idx = self.rows.index(row)
        if not row.item.text.strip():
            # Enter on an empty to-do ends the list.
            self._remove_row(row)
            self.add_item("text", idx)
            return
        kind = "todo" if row.item.type == "todo" else "text"
        self.add_item(kind, idx + 1)

    def _backspace_row(self, row: ItemRow) -> None:
        """Removes an emptied row on Backspace and focuses the row before it."""
        idx = self._remove_row(row)
        if self.rows:
            self.rows[max(0, idx - 1)].focus_editor()

    def _pick_due(self, row: ItemRow) -> None:
        """Opens the date and time picker below row's due button."""
        popup = DateTimePopup(self.theme, row.item.due_dt, self)
        popup.chosen.connect(row.set_due)
        btn = row.due_btn
        popup.popup_at(btn.mapToGlobal(QPoint(0, btn.height())))

    def _on_title(self, text: str) -> None:
        self.note.title = text
        self._touch()

    def _title_submitted(self) -> None:
        """Moves on from the title to the first row, creating one if the note is empty."""
        if self.rows:
            self.rows[0].focus_editor()
        else:
            self.add_item("text")

    def _edit_title(self) -> None:
        self.set_editing(True)
        self.title_edit.setFocus()

    def set_editing(self, editing: bool) -> None:
        """Switches between editing and viewing mode.

        Leaving editing mode removes empty rows and folds the title away if it is blank.
        """
        self.editing = editing
        self.reorder.finish(animate=False)  # A held row drops at once, before empty rows go.
        if not editing:
            for row in [row for row in self.rows if not row.item.text.strip()]:
                self._remove_row(row)
        for row in self.rows:
            row.set_editing(editing)
        self.title_edit.setReadOnly(not editing)
        motion.fold(self.title_edit, editing or bool(self.note.title.strip()), motion.MODE_MS)
        self.hint.setVisible(not self.rows)
        self._ease_mode(1.0 if editing else 0.0)
        if not editing and self.focusWidget():
            self.focusWidget().clearFocus()

    def _ease_mode(self, target: float) -> None:
        """Eases the toolbar and the empty-note hint to editing (1) or viewing (0) mode.

        The pencil's background stretches into Done's, the add buttons rise and fade in one after
        another from the left (and leave in reverse), and the hint cross-fades into the other
        mode's text, in step with the title folding in or out. The switch runs at an even pace and
        each part eases on its own. It is instant while the window is not on screen yet.
        """
        if self._mode_tween is not None:
            self._mode_tween.stop()
        start = self._mode
        self._mode_from, self._mode_to, self._hint_mix_from = start, target, self._hint_mix

        def step(v: float) -> None:
            self._set_mode(start + (target - start) * v)

        if not self.isVisible():
            step(1.0)
            return
        self._mode_tween = motion.Tween(self, motion.MODE_MS, step, easing=QEasingCurve.Linear)
        self._mode_tween.start()

    def _set_mode(self, level: float) -> None:
        """Lays out the mode transition at ``level``, 0 (viewing) to 1 (editing), in even time.

        Each part eases on its own: the pencil's background stretches into Done's (InOutCubic,
        with the pencil and Done easing out within it, see _ModeSlot), each add button eases out
        over its own, overlapping share of the switch, and the hint cross-fades over the whole
        switch (OutCubic, as the title folds), the outgoing text faster.
        """
        self._mode = level
        cross, rise = QEasingCurve(QEasingCurve.InOutCubic), QEasingCurve(QEasingCurve.OutCubic)
        self.mode_slot.set_level(cross.valueForProgress(level))
        share = 1.0 - 2 * self.MODE_STAGGER  # Each add button's part of the switch.
        buttons = (self.btn_text, self.btn_todo, self.btn_reminder)
        for i, (button, fx) in enumerate(zip(buttons, self._add_fx)):
            # From the left when entering; the reverse when leaving, as the level runs back.
            local = min(1.0, max(0.0, (level - i * self.MODE_STAGGER) / share))
            v = rise.valueForProgress(local)
            fx.set(v, dy=self.MODE_RISE * (1.0 - v))
            button.setVisible(level > 0.0)
        span = self._mode_to - self._mode_from
        done = 1.0 if not span else max(0.0, min(1.0, (level - self._mode_from) / span))
        mix = self._hint_mix_from + (self._mode_to - self._hint_mix_from) * rise.valueForProgress(done)
        self._hint_mix = mix
        if self._mode_to:  # The outgoing text fades faster than the new one comes in.
            view, edit = (1.0 - mix) ** 2, mix
        else:
            view, edit = 1.0 - mix, mix ** 2
        self._hint_fx[0].set(view)
        self._hint_fx[1].set(edit)

    def refresh_reminders(self, now: datetime | None = None) -> None:
        """Updates the due state of every reminder row relative to now (default: current time)."""
        for row in self.rows:
            if row.item.type == "reminder":
                row.refresh(now)

    def _touch(self) -> None:
        """Stamps the note as updated and emits changed."""
        self.note.updated = now_iso()
        self.changed.emit()

    # --- Menu ----------------------------------------------------------------
    def _show_menu(self) -> None:
        """Opens the note menu (tag colours, new note, pin, delete) above the More button."""
        theme = self.theme
        menu = glass_menu(theme, self)
        menu.aboutToHide.connect(menu.deleteLater)
        tags = TagPicker(theme, self.note.tint)
        tags.picked.connect(
            lambda key: (menu.close(), self._pick_custom() if key == TagPicker.CUSTOM else self.set_tint(key)))
        row = QWidgetAction(menu)
        row.setDefaultWidget(tags)
        menu.addAction(row)
        menu.addSeparator()
        menu.addAction(icons.menu_icon("note", theme.text), tr("menu.new"), lambda: self.newNoteRequested.emit(self))
        pin = menu.addAction(icons.menu_icon("pin", theme.text, theme.tertiary),
                             tr("menu.unpin" if self.note.pinned else "menu.pin"), self.toggle_pin)
        pin.setEnabled(not self.dock.edge)
        menu.addSeparator()
        delete = menu.addAction(icons.menu_icon("trash", theme.red), tr("menu.delete"), self.request_delete)
        delete.setData("danger")
        pos = self.btn_more.mapToGlobal(QPoint(self.btn_more.width(), 0))
        menu.adjustSize()
        menu.popup(QPoint(pos.x() - menu.sizeHint().width(), pos.y() - menu.sizeHint().height() - 4))

    def set_tint(self, tint: str) -> None:
        """Sets the note's tag colour (a tag key or a hex colour) and cross-fades the glow to it."""
        self.note.tint = tint
        self._fade_glow()
        self._sync_top_bar()
        self._touch()

    def _pick_custom(self) -> None:
        """Opens the colour picker: any colour, previewed live, kept on Done, reverted otherwise."""
        original = self.note.tint
        popup = ColorPopup(self.theme, tag_color(original) or "#9FCBEF", self)

        def preview(hex_: str) -> None:
            self.note.tint = hex_
            if self._glow_tween is not None:
                self._glow_tween.stop()
            self._glow = self._glow_target()  # Follows the drag directly.
            self._sync_top_bar()
            self.update()

        def revert() -> None:
            self.note.tint = original
            self._fade_glow()
            self._sync_top_bar()

        popup.preview.connect(preview)
        popup.chosen.connect(self.set_tint)
        popup.cancelled.connect(revert)
        btn = self.btn_more
        popup.popup_at(btn.mapToGlobal(QPoint(btn.width() - popup.sizeHint().width(), btn.height())))

    def _sync_top_bar(self) -> None:
        """Draws the pin mark a grey step stronger over a tag glow."""
        lifted = tag_color(self.note.tint) is not None
        self.grabber.update()
        self.pin_mark.setPixmap(icons.pixmap("pin", self.theme.secondary if lifted else self.theme.tertiary, 13))

    def _glow_target(self) -> QColor:
        """Returns the top-edge colour of the tag glow for the current tint.

        An untagged note gets the current colour made fully transparent, so a cross-fade to or
        from "none" only changes the alpha.
        """
        glow = glow_color(self.theme, tag_color(self.note.tint))
        if glow is None:
            clear = QColor(getattr(self, "_glow", QColor(0, 0, 0)))
            clear.setAlpha(0)
            return clear
        return QColor(glow)

    def _fade_glow(self) -> None:
        """Cross-fades the glow from its current colour to the target for the current tint."""
        if self._glow_tween is not None:
            self._glow_tween.stop()
        start, end = QColor(self._glow), self._glow_target()
        if start.alpha() == 0:  # Fading in from nothing: keeps the hue fixed.
            start = QColor(end.red(), end.green(), end.blue(), 0)

        def step(v: float) -> None:
            self._glow = QColor(*(round(x + (y - x) * v) for x, y in zip(start.getRgb(), end.getRgb())))
            self.update()

        self._glow_tween = motion.Tween(self, GLOW_MS, step)
        self._glow_tween.start()

    def flash(self) -> None:
        """Pulses the outline briefly, so a note raised from the tray list is easy to spot."""
        if self._flash_tween is not None:
            self._flash_tween.stop()

        def step(v: float) -> None:
            self._flash = math.sin(math.pi * v) ** 2  # Eases in and out.
            self.update()

        self._flash_tween = motion.Tween(self, FLASH_MS, step, easing=QEasingCurve.Linear)
        self._flash_tween.start()

    def set_text_scale(self, scale: float) -> None:
        """Scales the note's text (title, items, due dates, hint); icons and toolbar stay put."""
        self.text_scale = scale
        self.title_edit.set_text_scale(scale)
        for row in self.rows:
            row.set_text_scale(scale)
        self._scale_hint()
        self._update_placeholder()

    def _scale_hint(self) -> None:
        """Sizes the empty-note hint's font to the body size times the text scale."""
        font = QFont(self.placeholder.font())
        font.setPointSizeF(BODY_PT * self.text_scale)
        self.placeholder.setFont(font)
        self.placeholder_edit.setFont(font)

    def bring_to_front(self) -> None:
        """Shows the note above other apps' windows.

        Works from a tray click, when this app is not in the foreground. Pinned notes stay on top.
        """
        self.showNormal()
        self.raise_()
        win_effects.bring_to_front(int(self.winId()), self.note.pinned)

    def toggle_pin(self) -> None:
        """Toggles whether the note stays on top of other windows."""
        self.note.pinned = not self.note.pinned
        self.overrideWindowFlags(self._window_flags())
        win_effects.set_topmost(int(self.winId()), self.note.pinned)
        self.update_pin_mark()
        self._touch()

    def update_pin_mark(self) -> None:
        """Fades the pin mark in or out: it shows while the note stays on top."""
        shown = self.note.pinned or bool(self.dock.edge)
        if shown == self._pin_shown:
            return
        self._pin_shown = shown
        if self._pin_tween is not None:
            self._pin_tween.stop()
        if not self.isVisible():
            self._pin_fx.set(1.0 if shown else 0.0)
            self.pin_mark.setVisible(shown)
            return
        start = self._pin_fx.opacity()
        self.pin_mark.setVisible(True)

        def step(v: float) -> None:
            self._pin_fx.set(start + ((1.0 if shown else 0.0) - start) * v)

        def done() -> None:
            self.pin_mark.setVisible(shown)

        self._pin_tween = motion.Tween(self, motion.POP_IN_MS, step, done)
        self._pin_tween.start()

    def request_delete(self) -> None:
        """Deletes an empty note at once; otherwise asks for confirmation in the bottom bar."""
        if self.note.is_empty():
            self.deleteRequested.emit(self)
        else:
            self.bottom.setCurrentIndex(1)

    # --- Window events -------------------------------------------------------
    def _hand_over(self) -> None:
        super()._hand_over()
        self.dock.settle()

    def paintEvent(self, _):
        """Paints the glass fill, tag glow, flash highlight and border within the outline."""
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        path = self.paint_glass(p)
        if self._glow.alpha():
            grad = QLinearGradient(0, 0, 0, GLOW_HEIGHT)
            for i in range(9):  # Quadratic falloff leaves no visible line where the wash ends.
                stop = i / 8
                color = QColor(self._glow)
                color.setAlphaF(self._glow.alphaF() * (1 - stop) ** 2)
                grad.setColorAt(stop, color)
            p.fillPath(path, grad)
        if self._flash:
            level = self._flash
            hi = QColor(self.theme.text)
            hi.setAlphaF(0.07 * level)
            p.fillPath(path, hi)
            hi.setAlphaF(0.55 * level)
            p.setPen(QPen(hi, 1.5))
            p.drawPath(path)
        p.setPen(QPen(css_color(self.theme.border), 1))
        p.drawPath(path)

    def _edges_at(self, pos: QPoint) -> Qt.Edge:
        """Returns the window edges a press at pos would resize; corners have a larger zone.

        Hidden notes do not resize, nor do docked notes on their screen-edge side.
        """
        if self.dock.is_hidden():
            return Qt.Edge(0)
        edges = self._edges_in_zone(pos)
        return edges & ~self._DOCK_SIDE.get(self.dock.edge, Qt.Edge(0))

    _DOCK_SIDE = {"left": Qt.LeftEdge, "right": Qt.RightEdge, "top": Qt.TopEdge}

    def _edges_in_zone(self, pos: QPoint) -> Qt.Edge:
        """Returns the edges whose resize zone pos lies in."""
        x, y, w, h = pos.x(), pos.y(), self.width(), self.height()
        near_l, near_r = x <= CORNER, x >= w - CORNER
        near_t, near_b = y <= CORNER, y >= h - CORNER
        edges = Qt.Edge(0)
        if x <= EDGE or (near_l and (near_t or near_b)):
            edges |= Qt.LeftEdge
        elif x >= w - EDGE or (near_r and (near_t or near_b)):
            edges |= Qt.RightEdge
        if y <= EDGE - 3 or (near_t and (near_l or near_r)):
            edges |= Qt.TopEdge
        elif y >= h - EDGE or (near_b and (near_l or near_r)):
            edges |= Qt.BottomEdge
        return edges

    _CURSORS = {
        Qt.LeftEdge: Qt.SizeHorCursor,
        Qt.RightEdge: Qt.SizeHorCursor,
        Qt.TopEdge: Qt.SizeVerCursor,
        Qt.BottomEdge: Qt.SizeVerCursor,
        Qt.LeftEdge | Qt.TopEdge: Qt.SizeFDiagCursor,
        Qt.RightEdge | Qt.BottomEdge: Qt.SizeFDiagCursor,
        Qt.RightEdge | Qt.TopEdge: Qt.SizeBDiagCursor,
        Qt.LeftEdge | Qt.BottomEdge: Qt.SizeBDiagCursor,
    }

    def mouseMoveEvent(self, e):
        """Shows a resize cursor over the edges and corners."""
        self._show_resize_cursor(e.position().toPoint())
        super().mouseMoveEvent(e)

    def enterEvent(self, e):
        """Shows a resize cursor if the cursor comes in over an edge or a corner."""
        self._show_resize_cursor(self.mapFromGlobal(QCursor.pos()))
        super().enterEvent(e)

    def _show_resize_cursor(self, pos: QPoint) -> None:
        """Sets the resize cursor for the edges at pos, or the default cursor away from them."""
        cursor = self._CURSORS.get(self._edges_at(pos))
        if cursor is None:
            self.unsetCursor()
            self._cursor_watch.stop()
        else:
            self.setCursor(cursor)
            self._cursor_watch.start()  # Unsets it once the cursor is off the edges.

    def _check_resize_cursor(self) -> None:
        """Drops the resize cursor once the cursor has left the edges."""
        pos = self.mapFromGlobal(QCursor.pos())
        if not self.rect().contains(pos) or not self._edges_at(pos):
            self.unsetCursor()
            self._cursor_watch.stop()

    def mousePressEvent(self, e):
        """Starts a system resize when the left button is pressed on an edge or corner."""
        if e.button() == Qt.LeftButton:
            edges = self._edges_at(e.position().toPoint())
            if edges and self.windowHandle():
                self.windowHandle().startSystemResize(edges)
                return
        super().mousePressEvent(e)

    def mouseDoubleClickEvent(self, e):
        """Enters editing mode, adding a first text row to an empty note."""
        if not self.editing:
            self.set_editing(True)
        if not self.rows:
            self.add_item("text")
        super().mouseDoubleClickEvent(e)

    def leaveEvent(self, e):
        self.unsetCursor()
        super().leaveEvent(e)

    def moveEvent(self, e):
        super().moveEvent(e)
        self.dock.window_moved()
        self._sync_geometry()

    def resizeEvent(self, e):
        super().resizeEvent(e)
        self.body.setGeometry(QRect(self.content_shift, self.size()))
        self._sync_geometry()

    def set_content_shift(self, shift: QPoint) -> None:
        """Draws the note moved by shift within its window, cut off at the window's edges.

        Widgets with a graphics effect are redrawn at their new place.
        """
        self.content_shift = QPoint(shift)
        self.body.move(shift)
        for widget in self.body.findChildren(QWidget):
            if widget.graphicsEffect() is not None:
                widget.update()
        self.update()

    def _sync_geometry(self) -> None:
        """Copies the window geometry into the note and emits changed if it differs.

        Skipped while docked: the dock keeps the slid-out position in the note.
        """
        if self.dock.edge:
            return
        geo = self.geometry()
        if (geo.x(), geo.y(), geo.width(), geo.height()) != (self.note.x, self.note.y, self.note.w, self.note.h):
            self.note.x, self.note.y, self.note.w, self.note.h = geo.x(), geo.y(), geo.width(), geo.height()
            self.changed.emit()

    def _other_notes(self) -> list["NoteWindow"]:
        """Returns the other notes to snap to."""
        return [win for win in QApplication.topLevelWidgets()
                if isinstance(win, NoteWindow) and win is not self and win.isVisible()]

    def _snap(self, rect: wintypes.RECT) -> None:
        """Snaps the drag rect proposed by WM_MOVING; a hidden note snapped to slides out."""
        notes = [(win, win.dock.snap_rect()) for win in self._other_notes()]
        notes = [(win, note_rect) for win, note_rect in notes if note_rect is not None]
        self._snap_move.place(rect, [note_rect for _, note_rect in notes],
                              [win.dock.snap_alignment() for win, _ in notes])
        index = self._snap_move.snapped_to()
        self._show_snap_target(notes[index][0] if index is not None else None)
        self.dock.follow_drag(self._snap_move.pushed(), self._snap_move.stuck())

    def _show_snap_target(self, target: "NoteWindow | None") -> None:
        """Keeps the note snapped to slid out while it is the target."""
        if target is self._snap_target:
            return
        if self._snap_target is not None:
            self._snap_target.dock.hold_for_snap(False)
        self._snap_target = target
        if target is not None:
            target.dock.hold_for_snap(True)

    def nativeEvent(self, event_type, message):
        """Snaps the note while it is dragged and docks it when the drag ends.

        The messages behind the glass are left to GlassWindow.
        """
        exit_move = False
        if event_type == b"windows_generic_MSG" or event_type == "windows_generic_MSG":
            msg = wintypes.MSG.from_address(int(message))
            if msg.message == win_effects.WM_ENTERSIZEMOVE:
                self._moved = False
                self._snap_move.begin()
                self.dock.drag_started()
            if msg.message == win_effects.WM_MOVING:
                self._moved = True
                self._snap(wintypes.RECT.from_address(msg.lParam))
                return True, 1
            if msg.message == win_effects.WM_EXITSIZEMOVE:
                exit_move = True
                self._snap_move.stop()
                self._show_snap_target(None)
                self.dock.drag_finished(self._moved, self._snap_move.stuck(), self._snap_move.pushed())
        result = super().nativeEvent(event_type, message)
        if exit_move:
            self._sync_geometry()
        return result
