"""Measures startup time, memory and idle CPU of the real app (desktopnote.app.main()).

Runs main() in a throw-away data folder with N generated notes, under a separate
single-instance name so it works while DesktopNote is running, and reports the time to the
first painted note, the working set and private memory, and the CPU used while idle. The
numbers are for a warm start: the first run after a reboot is slower because files are not
cached yet.

    python tools/startup_perf.py --notes 10 --idle 30
"""
import time

T0 = time.perf_counter()  # The interpreter is up; everything after this counts as the app.

import argparse  # noqa: E402
import ctypes  # noqa: E402
import json  # noqa: E402
import sys  # noqa: E402
from ctypes import wintypes  # noqa: E402
from pathlib import Path  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _harness as H  # noqa: E402  (Sets a temporary DESKTOPNOTE_HOME.)

ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
ap.add_argument("--notes", type=int, default=10)
ap.add_argument("--idle", type=float, default=30.0, help="seconds to measure idle CPU")
args = ap.parse_args()
notes = [H.note(100 + 40 * i, 100 + 30 * i, 260, 220, f"Note {i}", [("todo", f"Item {k}") for k in range(5)],
                tint="sky")
         for i in range(args.notes)]
(H.HOME / "notes.json").write_text(
    json.dumps({"notes": notes, "settings": {"first_run": False, "autostart": False}}), encoding="utf-8")

t_import = time.perf_counter()
import desktopnote.app as app_module  # noqa: E402  (PySide6 and the app.)
t_import = time.perf_counter() - t_import
app_module.SERVER_NAME += "-perf"
app_module.win_effects.set_autostart = lambda on: None  # Never write to the user's registry.

from PySide6.QtCore import QEvent, QObject, QTimer  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

kernel32 = ctypes.windll.kernel32
kernel32.GetCurrentProcess.restype = wintypes.HANDLE
kernel32.GetProcessTimes.argtypes = [wintypes.HANDLE] + [ctypes.c_void_p] * 4
ctypes.windll.psapi.GetProcessMemoryInfo.argtypes = [wintypes.HANDLE, ctypes.c_void_p, wintypes.DWORD]


class _ProcessMemoryCounters(ctypes.Structure):
    """The Win32 PROCESS_MEMORY_COUNTERS_EX structure."""

    _fields_ = [("cb", wintypes.DWORD), ("PageFaultCount", wintypes.DWORD)] + [
        (field, ctypes.c_size_t) for field in (
            "PeakWorkingSetSize", "WorkingSetSize", "QuotaPeakPagedPoolUsage", "QuotaPagedPoolUsage",
            "QuotaPeakNonPagedPoolUsage", "QuotaNonPagedPoolUsage", "PagefileUsage", "PeakPagefileUsage",
            "PrivateUsage")]


def memory_mb():
    """Returns (working set, private bytes) of this process in MiB."""
    counters = _ProcessMemoryCounters()
    counters.cb = ctypes.sizeof(counters)
    ctypes.windll.psapi.GetProcessMemoryInfo(kernel32.GetCurrentProcess(), ctypes.byref(counters), counters.cb)
    return counters.WorkingSetSize / 2**20, counters.PrivateUsage / 2**20


def cpu_seconds():
    """Returns the kernel plus user CPU time this process has used, in seconds."""
    times = [wintypes.FILETIME() for _ in range(4)]  # Creation, exit, kernel and user time.
    kernel32.GetProcessTimes(kernel32.GetCurrentProcess(), *[ctypes.byref(t) for t in times])
    seconds = lambda ft: (ft.dwHighDateTime << 32 | ft.dwLowDateTime) / 1e7  # noqa: E731  (100 ns units.)
    return seconds(times[2]) + seconds(times[3])


marks = {}


class FirstPaint(QObject):
    """Event filter that records in marks["paint"] when the first NoteWindow paints."""

    def eventFilter(self, obj, ev):
        if ev.type() == QEvent.Paint and type(obj).__name__ == "NoteWindow" and "paint" not in marks:
            marks["paint"] = time.perf_counter()
        return False


first_paint_filter = FirstPaint()


def started():
    """Installs the first-paint filter and schedules the measurements once startup settles."""
    QApplication.instance().installEventFilter(first_paint_filter)

    def settled():
        """Prints startup time and memory, then starts the idle CPU measurement."""
        # By now the event loop has been idle for a while, so the fade-ins are done.
        ws, priv = memory_mb()
        first = (marks.get("paint", time.perf_counter()) - T0) * 1000
        print(f"notes={args.notes}: import {t_import * 1000:.0f} ms | first note painted {first:.0f} ms after launch | "
              f"memory {ws:.0f} MB working set, {priv:.0f} MB private", flush=True)
        cpu_start, wall_start = cpu_seconds(), time.perf_counter()

        def idle_done():
            """Prints the CPU used while idle and quits."""
            used, wall = cpu_seconds() - cpu_start, time.perf_counter() - wall_start
            print(f"RESULT idle {wall:.0f} s: {used * 1000:.0f} ms CPU = {used / wall * 100:.3f} % of one core",
                  flush=True)
            QApplication.instance().quit()
        QTimer.singleShot(int(args.idle * 1000), idle_done)
    QTimer.singleShot(800, settled)


QTimer.singleShot(0, started)
code = app_module.main()
H.finish()
sys.exit(code)
