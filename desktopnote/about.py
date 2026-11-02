"""The About window: app icon, name, version (with Check for Updates) and developer on frosted glass."""
from __future__ import annotations

from PySide6.QtCore import QEvent, QSize, Qt, QUrl
from PySide6.QtGui import QColor, QCursor, QDesktopServices, QFont, QGuiApplication, QPainter, QPen
from PySide6.QtWidgets import (
    QAbstractButton, QHBoxLayout, QLabel, QLayout, QSizePolicy, QSpacerItem, QToolButton, QVBoxLayout, QWidget,
)

from . import __version__, icons, motion, win_effects
from .glass import RADIUS, GlassWindow
from .i18n import tr
from .theme import Theme
from .updates import UpdateFlow
from .widgets import css_color

REPO_URL = "https://github.com/SinGuLaRiTy2001/DesktopNote"

MIN_WIDTH = 300  # Wider when a line needs it (fonts and languages vary).
MARGIN = 24
CLOSE_PX, CLOSE_INSET = 24, 10
ICON_PX = 64
NAME_PT, INFO_PT = 15.0, 9.0
TEXT_OUT_MS, TEXT_IN_MS = 120, 160  # The update button's text fading out, then the new one in.


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

    A new text fades in after the old one has faded out; text() returns the new text at once.
    """

    def __init__(self, parent: QWidget):
        super().__init__(parent)
        self.setProperty("hoverFx", False)  # Draws its own hover colour, not HoverFx's background.
        self.setCursor(Qt.PointingHandCursor)
        self._rest, self._hover = QColor(), QColor()
        self._shown = ""  # The text painted, which lags behind text() while it fades.
        self._hover_level, self._opacity = 0.0, 1.0
        self._hover_tween: motion.Tween | None = None
        self._fade: motion.Tween | None = None
        self._fading_out = False

    def set_colors(self, rest: str, hover: str) -> None:
        """Sets the colours at rest and on hover (CSS colours)."""
        self._rest, self._hover = css_color(rest), css_color(hover)
        self.update()

    def set_text(self, text: str) -> None:
        """Shows ``text``, fading it in after the current text if the button is visible."""
        if text == self.text():
            return
        self.setText(text)
        if not self.isVisible() or not self._shown:
            self._show(text)
        elif not self._fading_out:  # A running fade-out ends with the newest text.
            self._fade_out()

    def _show(self, text: str) -> None:
        """Paints ``text`` from now on and resizes the button to it."""
        self._shown = text
        self.updateGeometry()
        self.update()

    def _fade_out(self) -> None:
        """Fades the text out from its current opacity, then fades the new text in."""
        self._fading_out = True
        start = self._opacity
        self._run_fade(TEXT_OUT_MS, lambda value: start * (1.0 - value), self._fade_in)

    def _fade_in(self) -> None:
        """Swaps in the new text and fades it in."""
        self._fading_out = False
        self._show(self.text())
        self._run_fade(TEXT_IN_MS, lambda value: value)

    def _run_fade(self, duration: int, opacity, on_done=None) -> None:
        """Eases the text's opacity, as ``opacity`` gives it for the eased progress."""
        if self._fade is not None:
            self._fade.stop()

        def step(value: float) -> None:
            self._opacity = opacity(value)
            self.update()

        self._fade = motion.Tween(self, duration, step, on_done)
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
        metrics = self.fontMetrics()
        return QSize(metrics.horizontalAdvance(self._shown), metrics.height())

    def changeEvent(self, e):
        """Eases the colour to rest when the button is disabled, and back when it is enabled."""
        super().changeEvent(e)
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
        p.drawText(self.rect(), Qt.AlignCenter, self._shown)


class AboutWindow(GlassWindow):
    """Frosted-glass About window, centred on the screen under the cursor.

    It fades in like a note. The close button, Esc or a click elsewhere closes it, and a snapshot
    of it fades out in its place. It can be moved by dragging its background.

    The button next to the version checks for an update and installs it (see updates.UpdateFlow).

    Attributes:
        closing: Whether dismiss() has run; the window is then closed and deleted.
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
        version = QHBoxLayout()
        version.setSpacing(0)
        version.addStretch(1)
        version.addWidget(self._label(tr("about.version", version=__version__), INFO_PT, secondary=True))
        dot = self._label("·", INFO_PT, secondary=True)
        dot.setContentsMargins(6, 0, 6, 0)
        version.addWidget(dot)
        self.btn_update = self._link("", INFO_PT, updates.act)
        version.addWidget(self.btn_update)
        version.addStretch(1)
        root.addLayout(version)
        updates.changed.connect(self._sync_update)
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
        self.btn_close.clicked.connect(self.dismiss)

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

    def _sync_update(self) -> None:
        """Shows what the update button offers in the update's current state."""
        flow, button = self._updates, self.btn_update
        state = flow.state
        if state == "available":
            text = tr("update.install" if flow.installs() else "update.download", version=flow.release.version)
        else:
            text = tr({"idle": "update.check", "checking": "update.checking", "current": "update.current",
                       "installing": "update.installing", "failed": "update.retry"}[state])
        button.set_text(text)
        button.setToolTip(flow.error if state == "failed" else "")
        button.setEnabled(state not in ("checking", "installing"))

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

    def dismiss(self) -> None:
        """Closes the window, leaving a snapshot of it that fades out."""
        if self.closing:
            return
        self.closing = True
        motion.Ghost.from_screen(self, RADIUS)
        self.close()

    # --- Events --------------------------------------------------------------
    def changeEvent(self, e):
        """Closes the window once another window becomes active (a click elsewhere)."""
        super().changeEvent(e)
        if e.type() == QEvent.ActivationChange and not self.isActiveWindow() and self.isVisible():
            self.dismiss()

    def resizeEvent(self, e):
        super().resizeEvent(e)
        self.btn_close.move(self.width() - CLOSE_PX - CLOSE_INSET, CLOSE_INSET)

    def keyPressEvent(self, e):
        if e.key() == Qt.Key_Escape:
            self.dismiss()
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
