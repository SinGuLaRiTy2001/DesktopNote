"""Frosted-glass top-level windows: the system acrylic under a painted tint.

Notes and the About window share this. With the native backdrop, the window keeps a native frame
so it is not layered (layered windows flicker on resize and the acrylic lags behind the cursor);
WM_NCCALCSIZE strips the frame again and WM_NCACTIVATE keeps the acrylic "active".
"""
from __future__ import annotations

from ctypes import wintypes

from PySide6.QtCore import QPoint, QRect, QRectF, Qt
from PySide6.QtGui import QColor, QPainter, QPainterPath, QPixmap
from PySide6.QtWidgets import QWidget

from . import motion, win_effects
from .theme import WINDOW_RADIUS, Theme

NATIVE_BACKDROP = win_effects.HAS_SYSTEM_BACKDROP


class GlassWindow(QWidget):
    """Top-level window with frosted glass that fades in over a blurred snapshot.

    Subclasses call paint_glass() at the start of their paintEvent and may extend
    nativeEvent(), calling the base class for the messages behind the glass.

    Attributes:
        theme: The theme used for the glass.
        solid: Whether the window is drawn without the system acrylic.
        solid_shadow: Whether a solid window keeps its own shadow and DWM's rounded corners.
        backdrop: The screen rect behind the window and an untinted stand-in for the acrylic there
            (see motion.acrylic_stand_in), shown while solid and moved with content_shift; without
            it, a solid window is opaque.
        content_shift: How far the glass is drawn moved within the window (a QPoint).
    """

    def __init__(self, theme: Theme):
        super().__init__(None)
        self.theme = theme
        self._glass = False  # Acrylic (or blur) is active.
        self.solid = False
        self.solid_shadow = False
        self.backdrop: tuple[QRect, QPixmap] | None = None
        self.content_shift = QPoint()
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

    def set_solid(self, solid: bool, shadow: bool = False) -> None:
        """Draws the glass without the system acrylic (solid), or with it.

        A solid window shows its backdrop in place of the acrylic. Where it paints nothing, what is
        behind it shows.

        Args:
            solid: Whether to draw without the acrylic.
            shadow: Whether a solid window keeps its own shadow and DWM's rounded corners.
        """
        if (solid, shadow) != (self.solid, self.solid_shadow):
            was_solid = self.solid
            self.solid, self.solid_shadow = solid, shadow
            self._apply_glass(repaint=not was_solid)

    def _apply_glass(self, repaint: bool = True) -> None:
        """Applies the system acrylic (or an acrylic/blur fallback) and rounded corners.

        A solid window gets neither, and DWM's rounded corners and shadow only if solid_shadow.

        Args:
            repaint: For a solid window, repaints it before the acrylic is turned off.
        """
        if not self.isVisible():
            return
        hwnd = int(self.winId())
        if self.solid:
            self._glass = False
            if repaint:
                self.repaint()
            if NATIVE_BACKDROP:
                win_effects.set_solid_backdrop(hwnd, rounded=self.solid_shadow)
            else:
                win_effects.set_accent(hwnd, win_effects.ACCENT_DISABLED)
            return
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
            self._intro = motion.BackdropFade(self, QColor(self.theme.system_tint), self.theme.acrylic_saturation)
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
        path.addRoundedRect(QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5), WINDOW_RADIUS, WINDOW_RADIUS)
        return path

    def paint_glass(self, p: QPainter) -> QPainterPath:
        """Paints the glass fill (and the fade-in snapshot) and returns the outline.

        The glass is drawn moved by content_shift, and the painter stays translated by it.

        Args:
            p: An antialiased painter on this window.

        Returns:
            The outline, for painting more layers and the border on top.
        """
        if self._glass or self.solid:
            # An alpha-1 base keeps the rounded-off corners hit-testable (notes resize from them),
            # since fully transparent layered-window pixels are click-through.
            p.fillRect(self.rect(), QColor(0, 0, 0, 1))
        p.translate(self.content_shift)
        path = self.glass_path()
        if self._intro is not None:
            self._intro.paint(p, path)
        fill = self.theme.glass_color()
        if self.solid and self.backdrop is not None:
            rect, pm = self.backdrop
            frame = QPainterPath()  # The window's rounded outline, up to its edge.
            frame.addRoundedRect(QRectF(self.rect()), WINDOW_RADIUS, WINDOW_RADIUS)
            p.save()
            p.setClipPath(frame)
            p.setRenderHint(QPainter.SmoothPixmapTransform)
            p.drawPixmap(rect.translated(-self.geometry().topLeft()), pm)
            p.fillPath(frame, QColor(self.theme.system_tint))
            p.restore()
            fill.setAlpha(self.theme.fill_alpha)
        elif self.solid:
            fill = QColor(self.theme.solid_fill)
        elif not self._glass:
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


SHADOW_OPACITY = 0.99  # The most a ShadowWindow's opacity gets; below 1, clicks pass through it.


class ShadowWindow(GlassWindow):
    """Empty window that casts only the shadow of a glass window; it fades with its opacity.

    It stays shown between uses, fully transparent and click-through.
    """

    def __init__(self, theme: Theme):
        """Initialises the window and shows it, fully transparent."""
        super().__init__(theme)
        self.setWindowFlags(self.glass_flags() | Qt.WindowStaysOnTopHint | Qt.WindowDoesNotAcceptFocus)
        self.setAttribute(Qt.WA_ShowWithoutActivating)
        self.setWindowOpacity(0.0)
        self.setGeometry(0, 0, 1, 1)
        self.show()

    def _apply_glass(self, repaint: bool = True) -> None:
        """Turns off the acrylic, keeping DWM's rounded corners and their shadow."""
        if self.isVisible():
            hwnd = int(self.winId())
            win_effects.set_solid_backdrop(hwnd, rounded=True)
            win_effects.set_click_through(hwnd, True)

    def paintEvent(self, _):
        pass

    def cast(self, rect: QRect, strength: float, above: int) -> None:
        """Casts the shadow of rect, just below another window.

        Args:
            rect: The area to cast the shadow of, in global coordinates; empty for none.
            strength: How strong the shadow is, 0..1.
            above: Handle of the window to stay just below.
        """
        if rect.isEmpty() or strength <= 0.0:
            if self.windowOpacity() > 0.0:
                self.setWindowOpacity(0.0)
            return
        if self.geometry() != rect:
            self.setGeometry(rect)
        self.setWindowOpacity(min(strength, SHADOW_OPACITY))
        win_effects.place_below(int(self.winId()), above)
