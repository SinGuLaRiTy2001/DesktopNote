"""The About window: app icon, name, version and developer on frosted glass."""
from __future__ import annotations

from PySide6.QtCore import QEvent, Qt, QUrl
from PySide6.QtGui import QCursor, QDesktopServices, QFont, QGuiApplication, QPainter, QPen
from PySide6.QtWidgets import QLabel, QLayout, QSizePolicy, QSpacerItem, QToolButton, QVBoxLayout

from . import __version__, icons, motion, win_effects
from .glass import RADIUS, GlassWindow
from .i18n import tr
from .theme import Theme
from .widgets import css_color

REPO_URL = "https://github.com/SinGuLaRiTy2001/DesktopNote"

MIN_WIDTH = 300  # Wider when a line needs it (fonts and languages vary).
MARGIN = 24
CLOSE_PX, CLOSE_INSET = 24, 10
ICON_PX = 64
NAME_PT, INFO_PT = 15.0, 9.0


def _qss(t: Theme) -> str:
    """Returns the About window's style sheet."""
    return f"""
    QWidget {{ color: {t.text}; background: transparent; }}
    QLabel#secondary {{ color: {t.secondary}; }}
    QToolButton {{ border: none; background: transparent; }}
    QToolButton:pressed {{ background: {t.pressed}; }}
    QToolButton#close {{ border-radius: {CLOSE_PX // 2}px; }}
    QToolButton#link {{ color: {t.text}; padding: 2px 6px 3px 6px; border-radius: 6px; }}
    """


class AboutWindow(GlassWindow):
    """Frosted-glass About window, centred on the screen under the cursor.

    It fades in like a note. The close button, Esc or a click elsewhere closes it, and a snapshot
    of it fades out in its place. It can be moved by dragging its background.

    It shows no copyright notice: Qt's licence (LGPL-3.0, section 4(c)) would then require Qt's
    copyright notice among them. The copyright is in the LICENSE file.

    Attributes:
        closing: Whether dismiss() has run; the window is then closed and deleted.
    """

    def __init__(self, theme: Theme):
        super().__init__(theme)
        self.closing = False
        self.setWindowTitle(tr("tray.about"))
        self.setWindowFlags(self.glass_flags() | Qt.WindowStaysOnTopHint)
        self.setAttribute(Qt.WA_DeleteOnClose)

        root = QVBoxLayout(self)
        root.setSizeConstraint(QLayout.SetFixedSize)  # Sized to the content; the spacer sets the minimum width.
        root.setContentsMargins(MARGIN, 30, MARGIN, 22)
        root.setSpacing(0)
        root.addItem(QSpacerItem(MIN_WIDTH - 2 * MARGIN, 0, QSizePolicy.Fixed, QSizePolicy.Fixed))

        self.icon = QLabel(self)
        self.icon.setAlignment(Qt.AlignCenter)
        root.addWidget(self.icon)
        root.addSpacing(14)
        root.addWidget(self._label("DesktopNote", NAME_PT, weight=QFont.DemiBold))
        root.addSpacing(2)
        root.addWidget(self._label(tr("about.version", version=__version__), INFO_PT, secondary=True))
        root.addSpacing(18)
        root.addWidget(self._label(tr("about.developer"), INFO_PT, secondary=True))
        root.addSpacing(10)
        root.addWidget(self._link("GitHub", REPO_URL, INFO_PT), 0, Qt.AlignHCenter)

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

    def _link(self, text: str, url: str, pt: float) -> QToolButton:
        """Returns a text button that opens ``url`` in the browser."""
        button = QToolButton(self)
        button.setObjectName("link")
        button.setText(text)
        button.setToolTip(url)
        button.setCursor(Qt.PointingHandCursor)
        button.setProperty("hoverRadius", 6)
        font = button.font()
        font.setPointSizeF(pt)
        button.setFont(font)
        button.clicked.connect(lambda: QDesktopServices.openUrl(QUrl(url)))
        return button

    def apply_theme(self, theme: Theme) -> None:
        """Restyles the window and its glass for ``theme``."""
        self.theme = theme
        self.setStyleSheet(_qss(theme))
        self.icon.setPixmap(icons.pixmap("app", "#000", ICON_PX))
        self.btn_close.setIcon(icons.icon("xmark", theme.secondary, 18))
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
