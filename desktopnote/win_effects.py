"""Windows-only window effects: acrylic blur, rounded corners, autostart and dark mode.

Every function degrades gracefully: on failure, or on another platform, it returns False (or does
nothing) and the caller falls back to an opaque painted background.
"""
from __future__ import annotations

import ctypes
import sys
from ctypes import POINTER, Structure, byref, c_int, sizeof, wintypes

IS_WINDOWS = sys.platform == "win32"
WIN_BUILD = sys.getwindowsversion().build if IS_WINDOWS else 0
IS_WIN11 = WIN_BUILD >= 22000
# Windows 11 22H2+ provides the DWM-composited acrylic backdrop (DWMWA_SYSTEMBACKDROP_TYPE).
HAS_SYSTEM_BACKDROP = WIN_BUILD >= 22621

# AccentState values for SetWindowCompositionAttribute.
ACCENT_DISABLED = 0
ACCENT_ENABLE_BLURBEHIND = 3
ACCENT_ENABLE_ACRYLICBLURBEHIND = 4

WCA_ACCENT_POLICY = 19
DWMWA_WINDOW_CORNER_PREFERENCE = 33
DWMWA_BORDER_COLOR = 34
DWMWCP_ROUND = 2
DWMWCP_ROUNDSMALL = 3
DWMWA_COLOR_NONE = 0xFFFFFFFE
DWMWA_USE_IMMERSIVE_DARK_MODE = 20
DWMWA_SYSTEMBACKDROP_TYPE = 38
DWMSBT_TRANSIENTWINDOW = 3  # Acrylic.
DWMWA_NCRENDERING_POLICY = 2
DWMNCRP_DISABLED = 1

WM_NCCALCSIZE = 0x0083
WM_NCACTIVATE = 0x0086

WM_ENTERSIZEMOVE = 0x0231
WM_EXITSIZEMOVE = 0x0232


class ACCENT_POLICY(Structure):
    """The ACCENT_POLICY structure passed to SetWindowCompositionAttribute."""

    _fields_ = [
        ("AccentState", wintypes.DWORD),
        ("AccentFlags", wintypes.DWORD),
        ("GradientColor", wintypes.DWORD),
        ("AnimationId", wintypes.DWORD),
    ]


class MARGINS(Structure):
    """The Win32 MARGINS structure used by DwmExtendFrameIntoClientArea."""

    _fields_ = [("cxLeftWidth", c_int), ("cxRightWidth", c_int), ("cyTopHeight", c_int), ("cyBottomHeight", c_int)]


class WINDOWCOMPOSITIONATTRIBDATA(Structure):
    """The attribute/data pair passed to SetWindowCompositionAttribute."""

    _fields_ = [
        ("Attribute", wintypes.DWORD),
        ("Data", POINTER(ACCENT_POLICY)),
        ("SizeOfData", ctypes.c_size_t),
    ]


if IS_WINDOWS:
    _user32 = ctypes.WinDLL("user32")
    _dwmapi = ctypes.WinDLL("dwmapi")
    _SetWCA = getattr(_user32, "SetWindowCompositionAttribute", None)
    if _SetWCA is not None:
        _SetWCA.argtypes = [wintypes.HWND, POINTER(WINDOWCOMPOSITIONATTRIBDATA)]
        _SetWCA.restype = wintypes.BOOL
    _DwmSetWindowAttribute = _dwmapi.DwmSetWindowAttribute
    _DwmSetWindowAttribute.argtypes = [wintypes.HWND, wintypes.DWORD, ctypes.c_void_p, wintypes.DWORD]
    _DwmSetWindowAttribute.restype = ctypes.c_long
    _DwmExtendFrameIntoClientArea = _dwmapi.DwmExtendFrameIntoClientArea
    _DwmExtendFrameIntoClientArea.argtypes = [wintypes.HWND, POINTER(MARGINS)]
    _DwmExtendFrameIntoClientArea.restype = ctypes.c_long
    _DefWindowProc = _user32.DefWindowProcW
    _DefWindowProc.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
    _DefWindowProc.restype = wintypes.LPARAM
    _SetWindowPos = _user32.SetWindowPos
    _SetWindowPos.argtypes = [wintypes.HWND, wintypes.HWND, c_int, c_int, c_int, c_int, wintypes.UINT]
    _SetWindowPos.restype = wintypes.BOOL
else:  # pragma: no cover
    _SetWCA = None
    _SetWindowPos = None

HWND_TOPMOST, HWND_NOTOPMOST = -1, -2
SWP_NOSIZE, SWP_NOMOVE, SWP_NOACTIVATE, SWP_SHOWWINDOW = 0x0001, 0x0002, 0x0010, 0x0040


def _abgr(rgba: int) -> int:
    """Converts 0xRRGGBBAA to the 0xAABBGGRR layout expected by the accent policy."""
    r, g, b, a = (rgba >> 24) & 0xFF, (rgba >> 16) & 0xFF, (rgba >> 8) & 0xFF, rgba & 0xFF
    return (a << 24) | (b << 16) | (g << 8) | r


def set_accent(hwnd: int, state: int, rgba: int = 0xF2F2F2CC) -> bool:
    """Applies an accent policy (blur or acrylic) to a window via SetWindowCompositionAttribute.

    Args:
        hwnd: Native window handle.
        state: One of the ``ACCENT_*`` values.
        rgba: Tint colour as 0xRRGGBBAA.

    Returns:
        True if the call succeeded; False if it failed or the API is unavailable.
    """
    if _SetWCA is None:
        return False
    policy = ACCENT_POLICY()
    policy.AccentState = state
    # 0x20|0x40|0x80|0x100 draws on all borders; 0x2 makes GradientColor apply.
    policy.AccentFlags = 0x2 if state == ACCENT_ENABLE_BLURBEHIND else 0x1E0
    policy.GradientColor = _abgr(rgba)
    data = WINDOWCOMPOSITIONATTRIBDATA(WCA_ACCENT_POLICY, ctypes.pointer(policy), sizeof(policy))
    return bool(_SetWCA(wintypes.HWND(hwnd), byref(data)))


def set_acrylic(hwnd: int, rgba: int) -> bool:
    """Applies the accent-policy acrylic blur with the given 0xRRGGBBAA tint."""
    return set_accent(hwnd, ACCENT_ENABLE_ACRYLICBLURBEHIND, rgba)


def set_blur(hwnd: int, rgba: int) -> bool:
    """Applies the accent-policy plain blur with the given 0xRRGGBBAA tint."""
    return set_accent(hwnd, ACCENT_ENABLE_BLURBEHIND, rgba)


def bring_to_front(hwnd: int, stay_on_top: bool) -> bool:
    """Raises a window above the windows of every other app without taking focus.

    Windows ignores a plain raise from an app that is not in the foreground (a tray click leaves
    Explorer in front), so the window is briefly made topmost, which is always allowed, and then,
    unless it is pinned, dropped to the top of the normal windows.

    Args:
        hwnd: Native window handle.
        stay_on_top: Whether the window should remain topmost afterwards.

    Returns:
        True if every SetWindowPos call succeeded.
    """
    if _SetWindowPos is None:
        return False
    flags = SWP_NOMOVE | SWP_NOSIZE | SWP_NOACTIVATE | SWP_SHOWWINDOW
    ok = bool(_SetWindowPos(hwnd, HWND_TOPMOST, 0, 0, 0, 0, flags))
    if not stay_on_top:
        ok = bool(_SetWindowPos(hwnd, HWND_NOTOPMOST, 0, 0, 0, 0, flags)) and ok
    return ok


def def_window_proc(hwnd: int, msg: int, wparam: int, lparam: int) -> int:
    """Passes a window message to the default Win32 window procedure (DefWindowProcW)."""
    return _DefWindowProc(hwnd, msg, wparam, lparam)


def set_system_acrylic(hwnd: int, dark: bool) -> bool:
    """Applies the native Windows 11 acrylic system backdrop to a window.

    DWM composites this backdrop together with the window, so unlike the accent-policy acrylic it
    does not lag behind while the window is moved or resized. The window must not be layered and
    needs its frame extended over the client area, which this function does.

    Args:
        hwnd: Native window handle.
        dark: Whether to use the dark variant of the backdrop.

    Returns:
        True if the backdrop was applied; False on older Windows builds or on failure.
    """
    if not HAS_SYSTEM_BACKDROP:
        return False
    handle = wintypes.HWND(hwnd)
    dark_mode = c_int(1 if dark else 0)
    _DwmSetWindowAttribute(handle, DWMWA_USE_IMMERSIVE_DARK_MODE, byref(dark_mode), sizeof(dark_mode))
    if _DwmExtendFrameIntoClientArea(handle, byref(MARGINS(-1, -1, -1, -1))) != 0:
        return False
    # With non-client rendering disabled, DWM gives the window its small, light shadow instead of
    # the large active-window one (notes are kept "active" for the acrylic, see WM_NCACTIVATE).
    # The acrylic, rounded corners and resizing are unaffected (measured).
    policy = c_int(DWMNCRP_DISABLED)
    _DwmSetWindowAttribute(handle, DWMWA_NCRENDERING_POLICY, byref(policy), sizeof(policy))
    backdrop = c_int(DWMSBT_TRANSIENTWINDOW)
    return _DwmSetWindowAttribute(handle, DWMWA_SYSTEMBACKDROP_TYPE, byref(backdrop), sizeof(backdrop)) == 0


def set_rounded_corners(hwnd: int, small: bool = False) -> bool:
    """Asks DWM (Windows 11+) to clip the window to rounded corners.

    Args:
        hwnd: Native window handle.
        small: Whether to use the small corner radius.

    Returns:
        True if the corner preference was accepted; False before Windows 11 or on failure.
    """
    if not IS_WIN11:
        return False
    corner = c_int(DWMWCP_ROUNDSMALL if small else DWMWCP_ROUND)
    ok = _DwmSetWindowAttribute(
        wintypes.HWND(hwnd), DWMWA_WINDOW_CORNER_PREFERENCE, byref(corner), sizeof(corner)
    ) == 0
    # DWM draws a 1px accent border around rounded windows; it is hidden because the app paints
    # its own.
    no_colour = wintypes.DWORD(DWMWA_COLOR_NONE)
    _DwmSetWindowAttribute(wintypes.HWND(hwnd), DWMWA_BORDER_COLOR, byref(no_colour), sizeof(no_colour))
    return ok


def system_uses_dark_theme() -> bool:
    """Returns whether Windows is set to the dark app theme (False if unknown or not Windows)."""
    if not IS_WINDOWS:
        return False
    import winreg

    try:
        with winreg.OpenKey(
            winreg.HKEY_CURRENT_USER,
            r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize",
        ) as key:
            return winreg.QueryValueEx(key, "AppsUseLightTheme")[0] == 0
    except OSError:
        return False


# --- Autostart ---------------------------------------------------------------
_RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
_RUN_NAME = "DesktopNote"


def autostart_command() -> str:
    """Returns the command line registered for starting DesktopNote at login.

    A frozen (PyInstaller) build runs its own executable. Otherwise the ``DesktopNote.pyw``
    launcher is run with ``pythonw.exe`` from the current interpreter's folder when it exists, so
    no console window appears, falling back to the current interpreter.
    """
    import os

    if getattr(sys, "frozen", False):  # PyInstaller build.
        return f'"{sys.executable}"'
    exe = sys.executable
    pythonw = os.path.join(os.path.dirname(exe), "pythonw.exe")
    if os.path.exists(pythonw):
        exe = pythonw
    launcher = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "DesktopNote.pyw")
    return f'"{exe}" "{launcher}"'


def set_autostart(enabled: bool) -> None:
    """Adds or removes the DesktopNote entry under the current user's Run registry key.

    Does nothing outside Windows. Removing an entry that does not exist is not an error.

    Raises:
        OSError: If the Run key cannot be opened or written.
    """
    if not IS_WINDOWS:
        return
    import winreg

    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, _RUN_KEY, 0, winreg.KEY_SET_VALUE) as key:
        if enabled:
            winreg.SetValueEx(key, _RUN_NAME, 0, winreg.REG_SZ, autostart_command())
        else:
            try:
                winreg.DeleteValue(key, _RUN_NAME)
            except FileNotFoundError:
                pass


def is_autostart_enabled() -> bool:
    """Returns whether a DesktopNote entry exists under the current user's Run registry key."""
    if not IS_WINDOWS:
        return False
    import winreg

    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, _RUN_KEY) as key:
            winreg.QueryValueEx(key, _RUN_NAME)
            return True
    except OSError:
        return False
