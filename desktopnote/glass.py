"""Frosted-glass top-level windows: the system acrylic under a painted tint.

Notes and the About window share this. With the native backdrop, the window keeps a native frame
so it is not layered (layered windows flicker on resize and the acrylic lags behind the cursor);
WM_NCCALCSIZE strips the frame again and WM_NCACTIVATE keeps the acrylic "active".
"""
from __future__ import annotations

from ctypes import wintypes

from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QColor, QPainter, QPainterPath
from PySide6.QtWidgets import QWidget

from . import motion, win_effects
from .theme import Theme

RADIUS = 8  # Matches Windows 11's corner rounding, so the painted border and DWM's clip coincide.
NATIVE_BACKDROP = win_effects.HAS_SYSTEM_BACKDROP


class GlassWindow(QWidget):
    """Top-level window with frosted glass that fades in over a blurred snapshot.

    Subclasses call paint_glass() at the start of their paintEvent and may extend
    nativeEvent(), calling the base class for the messages behind the glass.

    Attributes:
        theme: The theme used for the glass.
    """

    def __init__(self, theme: Theme):
        super().__init__(None)
        self.theme = theme
        self._glass = False  # Acrylic (or blur) is active.
        self._moving = False  # Being moved or resized (legacy backdrop only).
        self._intro: motion.BackdropFade | None = None  # Stand-in acrylic while fading in.
        self.setAttribute(Qt.WA_TranslucentBackground)

    @staticmethod
    def glass_flags() -> Qt.WindowFlags:
        """Returns the window flags the backdrop in use needs."""
        if NATIVE_BACKDROP:
            # Keeps a native frame, so the window is not layered; WM_NCCALCSIZE strips it again.
            return Qt.Tool | Qt.CustomizeWindowHint
        return Qt.FramelessWindowHint | Qt.Tool | Qt.NoDropShadowWindowHint

    def _apply_glass(self) -> None:
        """Applies the system acrylic (or an acrylic/blur fallback) and rounded corners."""
        if not self.isVisible():
            return
        hwnd = int(self.winId())
        rgba = self.theme.acrylic_rgba()
        if NATIVE_BACKDROP:
            self._glass = win_effects.set_system_acrylic(hwnd, self.theme.dark)
            # Windows that were never focused would otherwise get the flat "inactive" backdrop.
            win_effects.def_window_proc(hwnd, win_effects.WM_NCACTIVATE, 1, -1)
        elif self._moving:
            # Acrylic lags behind the cursor while dragging; plain blur does not.
            self._glass = win_effects.set_blur(hwnd, rgba)
        else:
            self._glass = win_effects.set_acrylic(hwnd, rgba)
        win_effects.set_rounded_corners(hwnd)
        self.update()

    def reapply_glass(self) -> None:
        """Restores the acrylic, which Windows drops while the window is translucent."""
        self._apply_glass()

    def show_animated(self) -> None:
        """Fades in over a blurred snapshot of what is behind, then hands over to live acrylic."""
        if NATIVE_BACKDROP:
            self._intro = motion.BackdropFade(self, QColor(self.theme.system_tint))
        self.setWindowOpacity(0.0)
        self.show()
        motion.Tween(self, motion.NOTE_IN_MS, self.setWindowOpacity, self._hand_over).start_after_first_paint(self)

    def _hand_over(self) -> None:
        """Called once the fade-in is done: cross-fades the snapshot into the live acrylic."""
        if self._intro is not None:
            # Opacity is back at 1, so the window is no longer layered and DWM draws the acrylic.
            self._apply_glass()
            self._intro.hand_over()

    def showEvent(self, e):
        super().showEvent(e)
        self._apply_glass()

    def glass_path(self) -> QPainterPath:
        """Returns the window's rounded outline, on the pixel grid for a 1 px border."""
        path = QPainterPath()
        path.addRoundedRect(QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5), RADIUS, RADIUS)
        return path

    def paint_glass(self, p: QPainter) -> QPainterPath:
        """Paints the glass fill (and the fade-in snapshot) and returns the outline.

        Args:
            p: An antialiased painter on this window.

        Returns:
            The outline, for painting more layers and the border on top.
        """
        if self._glass:
            # An alpha-1 base keeps the rounded-off corners hit-testable (notes resize from them),
            # since fully transparent layered-window pixels are click-through.
            p.fillRect(self.rect(), QColor(0, 0, 0, 1))
        path = self.glass_path()
        if self._intro is not None:
            self._intro.paint(p, path)
        fill = self.theme.glass_color()
        if not self._glass:
            fill.setAlpha(242)
        elif NATIVE_BACKDROP:
            fill.setAlpha(self.theme.fill_alpha)  # Tint over the system acrylic.
        elif self._moving:
            fill.setAlpha(self.theme.glass_alpha)  # Stand-in for the acrylic tint while dragging.
        else:
            # Must be non-zero: fully transparent pixels of a layered window are click-through.
            fill.setAlpha(14)
        p.fillPath(path, fill)
        return path

    def nativeEvent(self, event_type, message):
        """Handles the Windows messages behind the glass.

        With the native backdrop, the non-client area is removed and the window always reports
        itself active so DWM keeps the acrylic. Otherwise the glass switches to plain blur while
        the window is being moved or resized.
        """
        if event_type == b"windows_generic_MSG" or event_type == "windows_generic_MSG":
            msg = wintypes.MSG.from_address(int(message))
            if NATIVE_BACKDROP:
                if msg.message == win_effects.WM_NCCALCSIZE and msg.wParam:
                    return True, 0  # Client area = whole window: no title bar or borders.
                if msg.message == win_effects.WM_NCACTIVATE:
                    # Always reports "active", because DWM turns the acrylic flat for inactive
                    # windows. lParam -1 skips the frame repaint that would draw a white border.
                    win_effects.def_window_proc(msg.hWnd, msg.message, 1, -1)
                    return True, 1
            elif msg.message in (win_effects.WM_ENTERSIZEMOVE, win_effects.WM_EXITSIZEMOVE):
                self._moving = msg.message == win_effects.WM_ENTERSIZEMOVE
                self._apply_glass()
        return super().nativeEvent(event_type, message)
