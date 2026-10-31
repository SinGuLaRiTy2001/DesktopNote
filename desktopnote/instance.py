"""Single instance: only one DesktopNote runs per user; a second launch shows its notes and exits.

Imports only QtCore and QtNetwork, so a second launch can hand over before loading the app.
"""
from __future__ import annotations

import ctypes
import getpass
import sys
import time

from PySide6.QtNetwork import QLocalSocket

SERVER_NAME = f"DesktopNote-{getpass.getuser()}"  # Per user: each Windows user gets their own instance.
HANDOVER_WAIT_S = 5.0  # How long a second launch waits for an instance that is still starting.
ERROR_ALREADY_EXISTS = 183

_claimed: set[str] = set()
_handles: list[int] = []  # Mutexes held for the life of the process.


def claim(name: str = SERVER_NAME, wait: float = HANDOVER_WAIT_S) -> bool:
    """Claims the single instance for this process, or hands over to the instance that has it.

    A named mutex makes the claim atomic, so launches at the same moment cannot both start.
    Windows releases it when the process ends, even after a crash.

    Args:
        name: The instance name; also the name of the local server it listens on.
        wait: How long to keep asking an instance that has not started listening yet.

    Returns:
        True if this process is the instance; False if another one is (and was asked to show
        its notes).
    """
    if name in _claimed:
        return True
    handle = _create_mutex(f"Global\\{name}") if sys.platform == "win32" else None
    if handle is None:  # No mutex: fall back to asking for a running instance.
        if signal_running_instance(name):
            return False
    elif handle[1]:  # The mutex exists: another instance has claimed it.
        ctypes.windll.kernel32.CloseHandle(ctypes.c_void_p(handle[0]))
        deadline = time.monotonic() + wait
        while not signal_running_instance(name) and time.monotonic() < deadline:
            time.sleep(0.1)
        return False
    else:
        _handles.append(handle[0])
    _claimed.add(name)
    return True


def _create_mutex(name: str) -> tuple[int, bool] | None:
    """Returns (handle, whether the mutex already existed), or None if it cannot be created."""
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.CreateMutexW.restype = ctypes.c_void_p
    kernel32.CreateMutexW.argtypes = [ctypes.c_void_p, ctypes.c_bool, ctypes.c_wchar_p]
    handle = kernel32.CreateMutexW(None, False, name)
    if not handle:
        return None
    return handle, ctypes.get_last_error() == ERROR_ALREADY_EXISTS


def signal_running_instance(name: str = SERVER_NAME) -> bool:
    """Asks a running instance to show its notes; returns True if one answered."""
    sock = QLocalSocket()
    sock.connectToServer(name)
    if sock.waitForConnected(300):
        sock.write(b"show")
        sock.flush()
        sock.waitForBytesWritten(300)
        sock.disconnectFromServer()
        return True
    return False
