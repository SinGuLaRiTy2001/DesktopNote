"""The About window: app icon, name, version (with Check for Updates) and developer on frosted glass."""
from __future__ import annotations

import math
from collections.abc import Callable

from PySide6.QtCore import QEasingCurve, QElapsedTimer, QEvent, QRectF, QSize, Qt, QTimer, QUrl, Signal
from PySide6.QtGui import QColor, QCursor, QDesktopServices, QFont, QGuiApplication, QPainter, QPen
from PySide6.QtWidgets import (
    QAbstractButton,
    QApplication,
    QLabel,
    QLayout,
    QSizePolicy,
    QSpacerItem,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from . import __version__, icons, motion, win_effects
from .glass import GlassWindow
from .i18n import tr
from .note_window import NoteWindow
from .theme import WINDOW_RADIUS, Theme, css_color
from .updates import UpdateFlow

REPO_URL = "https://github.com/SinGuLaRiTy2001/DesktopNote"

MIN_WIDTH = 300  # Wider when a line needs it (fonts and languages vary).
MARGIN = 24
CLOSE_PX, CLOSE_INSET = 24, 10
ICON_PX = 64
NAME_PT, INFO_PT = 15.0, 9.0
TEXT_OUT_MS, TEXT_IN_MS = 120, 160  # The update button's text fading out, then the new one in.
TEXT_SLIDE = 6  # Px a new text slides in from the right while it fades in.
TEXT_SLIDE_OUT = 4  # Px the old text slides left, into the button's inset, while it fades out.
TEXT_HOLD_MS = 450  # The least time a text stays shown before the next one replaces it.


def _qss(t: Theme) -> str:
    """Returns the About window's style sheet."""
    return f"""
    QWidget {{ color: {t.text}; background: transparent; }}
    QLabel#secondary {{ color: {t.secondary}; }}
    QToolButton {{ border: none; background: transparent; }}
    QToolButton:pressed {{ background: {t.pressed}; }}
    QToolButton#close {{ border-radius: {CLOSE_PX // 2}px; }}
    """


class TextButton(QAbstractButton):
    """A text-only button whose colour eases between two colours on hover.

    New texts are shown one after another: the old text fades out sliding left, the new one fades
    in sliding in from the right, and each stays at least TEXT_HOLD_MS before the next replaces it.
    Over the whole change, eased_width() eases from the old text's width to the new one's; the
    button itself is always wide enough for the text it paints. text() returns the newest text at
    once.

    Attributes:
        alignment: Where the text sits in the button (Qt.AlignCenter by default).
        inset: Px of room left of a left-aligned text, into which the old text slides out.

    Signals:
        settled: Emitted when the newest text has faded in.
        width_eased: Emitted whenever eased_width() changes.
    """

    settled = Signal()
    width_eased = Signal()

    def __init__(self, parent: QWidget):
        super().__init__(parent)
        self.setProperty("hoverFx", False)  # Draws its own hover colour, not HoverFx's background.
        self.setCursor(Qt.PointingHandCursor)
        self._rest, self._hover = QColor(), QColor()
        self.alignment = Qt.AlignCenter
        self.inset = 0
        self._shown = ""  # The text painted, which lags behind text() while texts change.
        self._queue: list[str] = []  # Texts waiting to be shown, oldest first.
        self._waiting: list[tuple[str, Callable[[], None]]] = []  # (text, function) to call once text has faded in.
        self._hover_level, self._opacity, self._dx = 0.0, 1.0, 0.0
        self._width = 0.0  # eased_width(): the shown text's width, eased between texts.
        self._hover_tween: motion.Tween | None = None
        self._fade: motion.Tween | None = None
        self._resize: motion.Tween | None = None
        self._busy = False  # A text is fading out or in, or holding before the next one.
        self._fading = False  # A text is fading out or in.
        self._leaving = False  # The shown text is fading out.
        self._hold = QTimer(self, singleShot=True, interval=TEXT_HOLD_MS)
        self._hold.timeout.connect(self._next)
        self._seen = ""  # The shown text, once painted fully faded in and at rest.
        self._seen_clock = QElapsedTimer()  # Since _seen was painted.
        self._seen_timer = QTimer(self, singleShot=True)  # Calls what waits for _seen, TEXT_HOLD_MS after.
        self._seen_timer.setTimerType(Qt.PreciseTimer)
        self._seen_timer.timeout.connect(self._call_seen)

    def set_colors(self, rest: str, hover: str) -> None:
        """Sets the colours at rest and on hover (CSS colours)."""
        self._rest, self._hover = css_color(rest), css_color(hover)
        self.update()

    def set_text(self, text: str, replace: bool = False) -> None:
        """Shows ``text`` after the texts before it, or at once while the button is hidden.

        Args:
            text: The text to show.
            replace: Drops the texts still waiting to be shown; the text on screen finishes its
                fade-in and its TEXT_HOLD_MS first.
        """
        if replace:
            self._queue.clear()
        last = self._queue[-1] if self._queue else None if self._leaving else self._shown
        self.setText(text)
        if not self.isVisible() or not self._shown:
            self._queue.clear()
            self._hold.stop()
            for tween in (self._fade, self._resize):
                if tween is not None:
                    tween.stop()
            self._busy = self._fading = self._leaving = False
            self._opacity, self._dx = 1.0, 0.0
            self._width = self._advance(text)
            self._show(text)
            self.width_eased.emit()
            self.settled.emit()
            return
        if text == last:
            return
        self._queue.append(text)
        if replace and self._leaving:
            self._resize_to(self._advance(text), TEXT_OUT_MS + TEXT_IN_MS)  # Towards this text's width.
        if not self._busy:
            self._next()

    def is_settled(self) -> bool:
        """Returns whether the newest text has faded in."""
        return not self._queue and not self._fading and self._shown == self.text()

    def call_when_shown(self, text: str, fn: Callable[[], None]) -> None:
        """Calls ``fn`` once ``text`` has been painted fully faded in and then shown TEXT_HOLD_MS."""
        self._waiting.append((text, fn))
        self._arm_seen()

    def flush_waiting(self) -> None:
        """Calls every function still waiting for its text, at once."""
        self._seen_timer.stop()
        waiting, self._waiting = self._waiting, []
        for _, fn in waiting:
            fn()

    def _show(self, text: str) -> None:
        """Paints ``text`` from now on."""
        self._shown = text
        self._seen = ""
        self.updateGeometry()
        self.update()

    def _next(self) -> None:
        """Fades the shown text out and the next waiting one in; stops when none is waiting."""
        if not self._queue:
            self._busy = False
            return
        self._busy = self._fading = self._leaving = True
        self._resize_to(self._advance(self._queue[0]), TEXT_OUT_MS + TEXT_IN_MS)
        start = self._opacity

        def step(value: float) -> None:
            self._opacity, self._dx = start * (1.0 - value), -TEXT_SLIDE_OUT * value

        self._run(TEXT_OUT_MS, step, self._fade_in, QEasingCurve.InCubic)

    def _fade_in(self) -> None:
        """Swaps in the next text (the newest if none waits) and fades it in, then holds it."""
        self._leaving = False
        self._show(self._queue.pop(0) if self._queue else self.text())
        if self._advance(self._shown) != self._width and not (self._resize and self._resize.is_running()):
            self._resize_to(self._advance(self._shown), TEXT_IN_MS)

        def step(value: float) -> None:
            self._opacity, self._dx = value, TEXT_SLIDE * (1.0 - value)

        self._run(TEXT_IN_MS, step, self._faded_in)

    def _faded_in(self) -> None:
        """Calls what waited for this text and starts its hold."""
        self._fading = False
        self._hold.start()
        if not self._queue:
            self.settled.emit()
        self.update()  # The next paint shows the text at rest and marks it seen (paintEvent).

    def _arm_seen(self) -> None:
        """Starts the timer that calls what waits for the seen text, TEXT_HOLD_MS after its paint."""
        if self._seen and any(text == self._seen for text, _ in self._waiting):
            self._seen_timer.start(max(0, TEXT_HOLD_MS - self._seen_clock.elapsed()))

    def _call_seen(self) -> None:
        """Calls what waits for the text that is seen."""
        ready = [fn for text, fn in self._waiting if text == self._seen]
        self._waiting = [(text, fn) for text, fn in self._waiting if text != self._seen]
        for fn in ready:
            fn()

    def _advance(self, text: str) -> float:
        """Returns the width of text in the button's font."""
        return float(self.fontMetrics().horizontalAdvance(text))

    def eased_width(self) -> float:
        """Returns the shown text's width, eased from the previous text's while texts change."""
        return self._width

    def _resize_to(self, width: float, duration: int) -> None:
        """Eases eased_width() to ``width``."""
        if self._resize is not None:
            self._resize.stop()
        start = self._width

        def step(value: float) -> None:
            self._width = start + (width - start) * value
            self.width_eased.emit()

        self._resize = motion.Tween(self, duration, step, easing=QEasingCurve.InOutCubic)
        self._resize.start()

    def _run(self, duration: int, step, on_done, easing=motion.EASE_OUT) -> None:
        """Runs one fade with ``step`` taking the eased progress, repainting each frame."""
        if self._fade is not None:
            self._fade.stop()

        def frame(value: float) -> None:
            step(value)
            self.update()

        self._fade = motion.Tween(self, duration, frame, on_done, easing)
        self._fade.start()

    def _hover_to(self, target: float, duration: int) -> None:
        """Eases the hover level from where it is to ``target``."""
        if self._hover_tween is not None:
            self._hover_tween.stop()
        start = self._hover_level

        def step(value: float) -> None:
            self._hover_level = start + (target - start) * value
            self.update()

        self._hover_tween = motion.Tween(self, duration, step)
        self._hover_tween.start()

    def sizeHint(self) -> QSize:
        """Returns the painted text's size, plus inset and TEXT_SLIDE when the text is left-aligned."""
        room = self.inset + TEXT_SLIDE if self.alignment & Qt.AlignLeft else 0
        return QSize(math.ceil(max(self._width, self._advance(self._shown))) + room, self.fontMetrics().height())

    def changeEvent(self, e):
        """Eases the colour to rest when the button is disabled, and back when it is enabled."""
        super().changeEvent(e)
        if e.type() == QEvent.FontChange and not self._fading:
            self._width = self._advance(self._shown)
            self.updateGeometry()
            self.width_eased.emit()
        if e.type() == QEvent.EnabledChange:
            on = self.isEnabled() and self.underMouse()
            self._hover_to(1.0 if on else 0.0, motion.HoverFx.IN_MS if on else motion.HoverFx.OUT_MS)

    def enterEvent(self, e):
        super().enterEvent(e)
        if self.isEnabled():
            self._hover_to(1.0, motion.HoverFx.IN_MS)

    def leaveEvent(self, e):
        super().leaveEvent(e)
        self._hover_to(0.0, motion.HoverFx.OUT_MS)

    def paintEvent(self, _):
        level = self._hover_level
        if self.isDown():
            level *= 0.5  # Pressed: halfway between rest and hover.
        color = QColor.fromRgbF(*(rest + (hover - rest) * level
                                  for rest, hover in zip(self._rest.getRgbF(), self._hover.getRgbF())))
        color.setAlphaF(color.alphaF() * self._opacity)
        p = QPainter(self)
        p.setPen(color)
        p.setFont(self.font())
        p.drawText(QRectF(self.rect()).adjusted(self.inset, 0, 0, 0).translated(self._dx, 0), self.alignment,
                   self._shown)
        if self._shown and not self._seen and not self._fading and self._opacity == 1.0 and self._dx == 0.0:
            self._seen = self._shown
            self._seen_clock.start()
            self._arm_seen()


class _VersionLine(QWidget):
    """The version, a dot and the update button on one line.

    The line, with the button's text taken at its eased_width(), is centred and moves as that width
    eases. The button follows the dot at its own size hint. The minimum width fits the widest
    update text.

    Attributes:
        widest: Px of the widest text the update button can show.
    """

    def __init__(self, version: QLabel, dot: QLabel, button: TextButton, parent: QWidget):
        super().__init__(parent)
        self._parts = (version, dot, button)
        for part in self._parts:
            part.setParent(self)
        self.widest = 0.0
        button.width_eased.connect(self._place)

    def sizeHint(self) -> QSize:
        version, dot, button = self._parts
        width = version.sizeHint().width() + dot.sizeHint().width() + math.ceil(self.widest)
        return QSize(width, max(part.sizeHint().height() for part in self._parts))

    def minimumSizeHint(self) -> QSize:
        return self.sizeHint()

    def event(self, e):
        if e.type() == QEvent.LayoutRequest:  # A part changed its size hint.
            self._place()
        return super().event(e)

    def resizeEvent(self, e):
        super().resizeEvent(e)
        self._place()

    def _place(self) -> None:
        """Centres the line and places version, dot and button on it, each at its size hint."""
        version, dot, button = self._parts
        line = version.sizeHint().width() + dot.sizeHint().width() + button.eased_width()
        x = round((self.width() - line) / 2)
        for part, left in ((version, x), (dot, x + version.sizeHint().width()),
                           (button, x + version.sizeHint().width() + dot.sizeHint().width() - button.inset)):
            hint = part.sizeHint()
            part.setGeometry(left, (self.height() - hint.height()) // 2, hint.width(), hint.height())


class AboutWindow(GlassWindow):
    """Frosted-glass About window, centred on the screen under the cursor.

    It fades in like a note. The close button, Esc or a click elsewhere closes it, and a snapshot
    of it fades out in its place. It can be moved by dragging its background.

    The button next to the version checks for an update and installs it (see updates.UpdateFlow).

    Attributes:
        closing: Whether the window has closed (or dismiss() has run); it is then deleted.
    """

    def __init__(self, theme: Theme, updates: UpdateFlow):
        super().__init__(theme)
        self.closing = False
        self._updates = updates
        self.setWindowTitle(tr("tray.about"))
        self.setWindowFlags(self.glass_flags() | Qt.WindowStaysOnTopHint)
        self.setAttribute(Qt.WA_DeleteOnClose)

        root = QVBoxLayout(self)
        root.setSizeConstraint(QLayout.SetFixedSize)  # Sized to the content; the spacer sets the minimum width.
        root.setContentsMargins(MARGIN, 30, MARGIN, 25)
        root.setSpacing(0)
        root.addItem(QSpacerItem(MIN_WIDTH - 2 * MARGIN, 0, QSizePolicy.Fixed, QSizePolicy.Fixed))

        self.icon = QLabel(self)
        self.icon.setAlignment(Qt.AlignCenter)
        root.addWidget(self.icon)
        root.addSpacing(14)
        root.addWidget(self._label("DesktopNote", NAME_PT, weight=QFont.DemiBold))
        root.addSpacing(2)
        dot = self._label("·", INFO_PT, secondary=True)
        dot.setContentsMargins(6, 0, 6, 0)
        self.btn_update = self._link("", INFO_PT, updates.act)
        self.btn_update.alignment = Qt.AlignLeft | Qt.AlignVCenter
        self.btn_update.inset = TEXT_SLIDE_OUT
        self.btn_update.settled.connect(self._sync_update_enabled)
        self._version_line = _VersionLine(self._label(tr("about.version", version=__version__), INFO_PT,
                                                      secondary=True), dot, self.btn_update, self)
        root.addWidget(self._version_line)
        updates.changed.connect(self._sync_update)
        updates.before_quit = self._quit_once_shown
        self._sync_update()
        root.addSpacing(18)
        root.addWidget(self._label(tr("about.developer"), INFO_PT, secondary=True))
        root.addSpacing(12)
        self.btn_github = self._link("GitHub", INFO_PT, lambda: QDesktopServices.openUrl(QUrl(REPO_URL)))
        self.btn_github.setToolTip(REPO_URL)
        root.addWidget(self.btn_github, 0, Qt.AlignHCenter)

        # The close button floats in the top-right corner, outside the centred layout.
        self.btn_close = QToolButton(self)
        self.btn_close.setObjectName("close")
        self.btn_close.setFixedSize(CLOSE_PX, CLOSE_PX)
        self.btn_close.setIconSize(icons.ICON_SIZE * 0.75)
        self.btn_close.setCursor(Qt.PointingHandCursor)
        self.btn_close.setToolTip(tr("about.close"))
        self.btn_close.setProperty("hoverRadius", CLOSE_PX // 2)
        self.btn_close.clicked.connect(lambda: self.dismiss(hand_over=True))

        self.apply_theme(theme)
        motion.HoverFx.attach(self)
        self.adjustSize()

    # --- Content -------------------------------------------------------------
    def _label(self, text: str, pt: float, secondary: bool = False, weight=QFont.Normal) -> QLabel:
        """Returns a centred label in the given point size."""
        label = QLabel(text, self)
        label.setAlignment(Qt.AlignCenter)
        if secondary:
            label.setObjectName("secondary")
        font = label.font()
        font.setPointSizeF(pt)
        font.setWeight(weight)
        label.setFont(font)
        return label

    def _link(self, text: str, pt: float, on_click) -> TextButton:
        """Returns a text button in the given point size that calls ``on_click``."""
        button = TextButton(self)
        button.set_text(text)
        font = button.font()
        font.setPointSizeF(pt)
        button.setFont(font)
        button.clicked.connect(on_click)
        return button

    _STATE_TEXT = {"idle": "update.check", "checking": "update.checking", "current": "update.current",
                   "downloading": "update.downloading", "verifying": "update.verifying",
                   "preparing": "update.preparing", "installing": "update.installing",
                   "failed": "update.retry"}
    _ACTIONS = ("idle", "current", "available", "failed")  # States in which the update button acts.

    def _update_text(self, state: str, version: str | None = None) -> str:
        """Returns the update button's text for a state; version names the update found."""
        if state == "available":
            return tr("update.install" if self._updates.installs() else "update.download", version=version)
        return tr(self._STATE_TEXT[state])

    def _fit_version_line(self) -> None:
        """Sets the version line's width for the widest update text in the current language."""
        versions = {__version__} | ({self._updates.release.version} if self._updates.release else set())
        texts = [self._update_text(state) for state in self._STATE_TEXT]
        texts += [self._update_text("available", version) for version in versions]
        widest = max(self.btn_update.fontMetrics().horizontalAdvance(text) for text in texts)
        if widest != self._version_line.widest:
            self._version_line.widest = widest
            self._version_line.updateGeometry()

    def _sync_update(self) -> None:
        """Queues the text for the update's current state; a failure or a new check drops older ones."""
        flow, button = self._updates, self.btn_update
        version = flow.release.version if flow.release else None
        self._fit_version_line()
        button.set_text(self._update_text(flow.state, version), replace=flow.state in ("checking", "failed"))
        button.setToolTip(flow.error if flow.state == "failed" else "")
        self._sync_update_enabled()

    def _sync_update_enabled(self) -> None:
        """Lets the update button act once it shows the text of an actionable state."""
        self.btn_update.setEnabled(self._updates.state in self._ACTIONS and self.btn_update.is_settled())

    def _quit_once_shown(self, quit: Callable[[], None]) -> bool:
        """Calls ``quit`` once the "installing" text has been seen (see TextButton.call_when_shown).

        Returns False, and calls nothing, while the window is closing or hidden.
        """
        if self.closing or not self.isVisible():
            return False
        self.btn_update.call_when_shown(tr("update.installing"), quit)
        return True

    def apply_theme(self, theme: Theme) -> None:
        """Restyles the window and its glass for ``theme``."""
        self.theme = theme
        self.setStyleSheet(_qss(theme))
        self.icon.setPixmap(icons.pixmap("app", "#000", ICON_PX))
        self.btn_close.setIcon(icons.icon("xmark", theme.secondary, 18))
        for button in (self.btn_update, self.btn_github):
            button.set_colors(theme.secondary, theme.text)
        self._apply_glass()

    # --- Showing and closing -------------------------------------------------
    def popup(self) -> None:
        """Fades the window in, centred on the screen under the cursor, and focuses it."""
        screen = QGuiApplication.screenAt(QCursor.pos()) or QGuiApplication.primaryScreen()
        area = screen.availableGeometry()
        self.setGeometry(area.center().x() - self.width() // 2, area.center().y() - self.height() // 2,
                         self.width(), self.height())
        self.show_animated()
        self.raise_()
        win_effects.bring_to_front(int(self.winId()), True)
        self.activateWindow()

    def dismiss(self, hand_over: bool = False) -> None:
        """Closes the window, leaving a snapshot of it that fades out.

        Args:
            hand_over: Whether to first activate the closest note behind the window in the
                z-order (see _activate_note_behind).
        """
        if self.closing:
            return
        self.closing = True
        if hand_over:
            self._activate_note_behind()
        motion.Ghost.from_screen(self, WINDOW_RADIUS)
        self.close()

    def _activate_note_behind(self) -> None:
        """Activates the closest note behind the window in the z-order.

        Nothing is activated when a window of another app comes before that note; other apps'
        always-on-top windows, such as the taskbar, are skipped.
        """
        own = {int(w.winId()): w for w in QApplication.topLevelWidgets() if w.isVisible() and w is not self}
        for hwnd in win_effects.windows_behind(int(self.winId())):
            widget = own.get(hwnd)
            if widget is None:
                if win_effects.is_topmost(hwnd):
                    continue
                return  # Another app's window comes before any note.
            if isinstance(widget, NoteWindow):
                widget.activateWindow()
                return

    # --- Events --------------------------------------------------------------
    def changeEvent(self, e):
        """Closes the window once another window becomes active (a click elsewhere)."""
        super().changeEvent(e)
        if e.type() == QEvent.ActivationChange and not self.isActiveWindow() and self.isVisible():
            self.dismiss()

    def closeEvent(self, e):
        """Marks the window as closing however it closes, e.g. with Alt+F4.

        An update waiting for the "installing" text to show goes on at once.
        """
        self.closing = True
        if self._updates.before_quit == self._quit_once_shown:
            self._updates.before_quit = None
        self.btn_update.flush_waiting()
        super().closeEvent(e)

    def hideEvent(self, e):
        """Calls at once what waits for the update button's text, such as a waiting update's quit."""
        self.btn_update.flush_waiting()
        super().hideEvent(e)

    def resizeEvent(self, e):
        super().resizeEvent(e)
        self.btn_close.move(self.width() - CLOSE_PX - CLOSE_INSET, CLOSE_INSET)

    def keyPressEvent(self, e):
        if e.key() == Qt.Key_Escape:
            self.dismiss(hand_over=True)
        else:
            super().keyPressEvent(e)

    def mousePressEvent(self, e):
        """Moves the window when its background is dragged."""
        if e.button() == Qt.LeftButton and self.windowHandle() is not None:
            self.windowHandle().startSystemMove()

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        path = self.paint_glass(p)
        p.setPen(QPen(css_color(self.theme.border), 1))
        p.drawPath(path)
