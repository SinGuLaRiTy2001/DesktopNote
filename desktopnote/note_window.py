"""A single floating glass note window, plus the app's glass menu."""
from __future__ import annotations

import html
import math
from ctypes import wintypes
from datetime import datetime

from PySide6.QtCore import QEasingCurve, QEvent, QPoint, QRectF, QSize, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QCursor, QFont, QLinearGradient, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import (
    QApplication,
    QFrame,
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
RADIUS = 8  # Matches Windows 11's corner rounding, so the painted border and DWM's clip coincide.
MIN_SIZE = QSize(200, 150)  # Wider where the delete confirmation needs it.
GLOW_HEIGHT = 44  # The tag colour fades out before the first row, so text contrast holds.
GLOW_MS = 260  # Cross-fade when the tag changes.
FLASH_MS = 900  # Pulse when a note is picked from the tray list.
# A non-layered window with DWM acrylic moves and resizes in lockstep with the cursor. Without it,
# Qt makes the frameless translucent window layered, which flickers on resize.
NATIVE_BACKDROP = win_effects.HAS_SYSTEM_BACKDROP


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


class NoteWindow(QWidget):
    """A single floating glass note: a title, a scrollable list of item rows and a toolbar.

    The window is frameless and paints its own rounded glass over the system acrylic (or a blur
    fallback), with an optional tag-colour glow along the top. It resizes from its edges, moves by
    its grabber (snapping next to nearby notes, hiding at a screen edge), and switches between viewing and editing mode.

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

    changed = Signal()
    newNoteRequested = Signal(object)
    deleteRequested = Signal(object)

    def __init__(self, note: Note, theme: Theme, soon_minutes: int, text_scale: float = 1.0):
        super().__init__(None)
        self.text_scale = text_scale
        self.note = note
        self.theme = theme
        self.soon_minutes = soon_minutes
        self.editing = False
        self.rows: list[ItemRow] = []
        self._glass = False  # Acrylic (or blur) is active.
        self._moving = False
        self._snap_move = SnapMove(self)  # Snap to align while dragged.
        self.dock = EdgeDock(self)
        self._moved = False  # Whether the current move or resize moved the window.
        self._snap_target: NoteWindow | None = None  # The note this one is snapped to.
        self._cursor_watch = QTimer(self)  # Runs while a resize cursor is shown.
        self._cursor_watch.setInterval(40)
        self._cursor_watch.timeout.connect(self._check_resize_cursor)
        self._intro: motion.BackdropFade | None = None  # Stand-in acrylic while fading in.
        self._glow = self._glow_target()  # Tag colour washed over the top of the glass.
        self._glow_tween: motion.Tween | None = None
        self._flash = 0.0  # Tray-list highlight, 0..1.
        self._flash_tween: motion.Tween | None = None

        self.setWindowTitle("DesktopNote")
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setMouseTracking(True)
        self.setMinimumSize(MIN_SIZE)
        self._apply_flags()

        root = QVBoxLayout(self)
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
        self.placeholder = QLabel(self.content)
        self.placeholder.setContentsMargins(4, 1, 4, 1)
        self._scale_hint()
        self.placeholder.setObjectName("placeholder")
        self.placeholder.setTextFormat(Qt.RichText)  # The hint embeds the toolbar's pencil icon.
        self.placeholder.setAlignment(Qt.AlignCenter)
        self.placeholder.setWordWrap(True)
        self.content_lay.addWidget(self.placeholder)
        self.content_lay.addStretch(1)
        self.scroll.setWidget(self.content)
        root.addWidget(self.scroll, 1)
        self.scroll_indicator = OverlayScrollBar(self.scroll, theme)

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
        """Sets the empty-note hint for the current mode, embedding the pencil icon in view mode."""
        if self.editing:
            self.placeholder.setText(html.escape(tr("note.placeholder_edit")))
        else:
            icon_scale = 1 + (self.text_scale - 1) * ItemRow.ICON_FOLLOW  # Grows with the hint's text.
            # Middle-aligned images sit 1-2 px below the hint's visual midline (measured at all
            # sizes, for Latin and CJK alike), hence the lift.
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
        for w in (self.btn_text, self.btn_todo, self.btn_reminder):
            lay.addWidget(w)
        lay.addStretch(1)
        lay.addWidget(self.btn_edit)
        lay.addWidget(self.btn_done)
        lay.addWidget(self.btn_more)
        return bar

    def _fit_minimum_width(self) -> None:
        """Keeps the note at least as wide as its delete confirmation in this language."""
        bar = self.bottom.widget(1).layout()
        margins, root = bar.contentsMargins(), self.layout().contentsMargins()
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
        self.pin_mark.setVisible(self.note.pinned)
        self._update_placeholder()  # The inline icon is tinted with the theme.
        for row in self.rows:
            row.apply_theme(theme)
        self._apply_glass()
        self.update()

    def _apply_flags(self) -> None:
        """Sets the window flags for the backdrop in use and the note's pinned state."""
        if NATIVE_BACKDROP:
            # Keeps a native frame, so the window is not layered; WM_NCCALCSIZE strips it again.
            flags = Qt.Tool | Qt.CustomizeWindowHint
        else:
            flags = Qt.FramelessWindowHint | Qt.Tool | Qt.NoDropShadowWindowHint
        if self.note.pinned:
            flags |= Qt.WindowStaysOnTopHint
        self.setWindowFlags(flags)

    def _apply_glass(self) -> None:
        """Applies the system acrylic (or an acrylic/blur fallback) and rounded corners."""
        if not self.isVisible():
            return
        hwnd = int(self.winId())
        rgba = self.theme.acrylic_rgba()
        if NATIVE_BACKDROP:
            self._glass = win_effects.set_system_acrylic(hwnd, self.theme.dark)
            # Notes that were never focused would otherwise get the flat "inactive" backdrop.
            win_effects.def_window_proc(hwnd, win_effects.WM_NCACTIVATE, 1, -1)
        elif self._moving:
            # Acrylic lags behind the cursor while dragging; plain blur does not.
            self._glass = win_effects.set_blur(hwnd, rgba)
        else:
            self._glass = win_effects.set_acrylic(hwnd, rgba)
        win_effects.set_rounded_corners(hwnd)
        self.update()

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
        self.placeholder.setVisible(False)
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
        motion.expand_in(row, on_done=lambda: self.scroll.ensureWidgetVisible(row))
        QTimer.singleShot(0, lambda: self.scroll.ensureWidgetVisible(row))
        self._touch()
        return row

    def _remove_row(self, row: ItemRow) -> int:
        """Removes row and its item, collapsing the widget away; returns its former index."""
        idx = self.rows.index(row)
        self.rows.pop(idx)
        self.note.items.remove(row.item)
        motion.collapse_out(row)  # Deletes the widget when done.
        self.placeholder.setVisible(not self.rows)
        self._touch()
        return idx

    def _delete_row(self, row: ItemRow) -> None:
        self._remove_row(row)

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
        if not editing:
            for row in [row for row in self.rows if not row.item.text.strip()]:
                self._remove_row(row)
        for row in self.rows:
            row.set_editing(editing)
        self.title_edit.setReadOnly(not editing)
        motion.fold(self.title_edit, editing or bool(self.note.title.strip()))
        for button in (self.btn_text, self.btn_todo, self.btn_reminder, self.btn_done):
            button.setVisible(editing)
        self.btn_edit.setVisible(not editing)
        self.placeholder.setVisible(not self.rows)
        self._update_placeholder()
        if not editing and self.focusWidget():
            self.focusWidget().clearFocus()

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
        tags = TagPicker(theme, self.note.tint)
        tags.picked.connect(
            lambda key: (menu.close(), self._pick_custom() if key == TagPicker.CUSTOM else self.set_tint(key)))
        row = QWidgetAction(menu)
        row.setDefaultWidget(tags)
        menu.addAction(row)
        menu.addSeparator()
        menu.addAction(icons.menu_icon("note", theme.text), tr("menu.new"), lambda: self.newNoteRequested.emit(self))
        menu.addAction(icons.menu_icon("pin", theme.text), tr("menu.unpin" if self.note.pinned else "menu.pin"),
                       self.toggle_pin)
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
        """Lifts the grey pill and pin a step over a tag glow, so they don't sink into it."""
        lifted = tag_color(self.note.tint) is not None
        self.grabber.lifted = lifted
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
        geo = self.geometry()
        self._apply_flags()  # Changing the flags hides the window.
        self.setGeometry(geo)
        self.show()
        self.dock.keep_on_top()
        self.pin_mark.setVisible(self.note.pinned)
        self._touch()

    def request_delete(self) -> None:
        """Deletes an empty note at once; otherwise asks for confirmation in the bottom bar."""
        if self.note.is_empty():
            self.deleteRequested.emit(self)
        else:
            self.bottom.setCurrentIndex(1)

    # --- Window events -------------------------------------------------------
    def show_animated(self) -> None:
        """Fades in over a blurred snapshot of what is behind, then hands over to live acrylic."""
        if NATIVE_BACKDROP:
            self._intro = motion.BackdropFade(self, QColor(self.theme.system_tint))
        self.setWindowOpacity(0.0)
        self.show()
        motion.Tween(self, motion.NOTE_IN_MS, self.setWindowOpacity, self._hand_over).start_after_first_paint(self)

    def _hand_over(self) -> None:
        if self._intro is not None:
            # Opacity is back at 1, so the window is no longer layered and DWM draws the acrylic.
            self._apply_glass()
            self._intro.hand_over()
        self.dock.settle()

    def reapply_glass(self) -> None:
        """Restores the acrylic, which Windows drops while the window is translucent."""
        self._apply_glass()

    def showEvent(self, e):
        super().showEvent(e)
        self._apply_glass()

    def paintEvent(self, _):
        """Paints the glass fill, tag glow, flash highlight and border within the outline."""
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        if self._glass:
            # An alpha-1 base keeps the rounded-off corners hit-testable for resizing, since fully
            # transparent layered-window pixels are click-through.
            p.fillRect(self.rect(), QColor(0, 0, 0, 1))
        rect = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        path = QPainterPath()
        path.addRoundedRect(rect, RADIUS, RADIUS)
        if self._intro is not None:
            self._intro.paint(p, path)
        fill = self.theme.glass_color()
        if not self._glass:
            fill.setAlpha(242)
        elif NATIVE_BACKDROP:
            fill.setAlpha(self.theme.fill_alpha)  # Note tint over the system acrylic.
        elif self._moving:
            fill.setAlpha(self.theme.glass_alpha)  # Stand-in for the acrylic tint while dragging.
        else:
            # Must be non-zero: fully transparent pixels of a layered window are click-through.
            fill.setAlpha(14)
        p.fillPath(path, fill)
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
        edges = self._edges_at(e.position().toPoint())
        cursor = self._CURSORS.get(edges)
        if cursor is None:
            self.unsetCursor()
            self._cursor_watch.stop()
        else:
            self.setCursor(cursor)
            # Rows reach into the resize zone and would otherwise inherit this cursor.
            self._cursor_watch.start()
        super().mouseMoveEvent(e)

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
        self._sync_geometry()

    def resizeEvent(self, e):
        super().resizeEvent(e)
        self._sync_geometry()

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
        """Handles the Windows messages behind the frameless acrylic window.

        With the native backdrop, the non-client area is removed and the window always reports
        itself active so DWM keeps the acrylic. Otherwise the glass switches to plain blur while
        the window is being moved or resized.
        """
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
                self._snap_move.stop()
                self._show_snap_target(None)
                self.dock.drag_finished(self._moved)
            if NATIVE_BACKDROP:
                if msg.message == win_effects.WM_NCCALCSIZE and msg.wParam:
                    return True, 0  # Client area = whole window: no title bar or borders.
                if msg.message == win_effects.WM_NCACTIVATE:
                    # Always reports "active", because DWM turns the acrylic flat for inactive
                    # windows. lParam -1 skips the frame repaint that would draw a white border
                    # over the note.
                    win_effects.def_window_proc(msg.hWnd, msg.message, 1, -1)
                    return True, 1
                if msg.message == win_effects.WM_EXITSIZEMOVE:
                    self._sync_geometry()
            elif msg.message == win_effects.WM_ENTERSIZEMOVE:
                self._moving = True
                self._apply_glass()
            elif msg.message == win_effects.WM_EXITSIZEMOVE:
                self._moving = False
                self._apply_glass()
                self._sync_geometry()
        return super().nativeEvent(event_type, message)
