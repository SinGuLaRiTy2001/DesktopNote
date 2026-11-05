"""Shared test helpers: the Qt application, temporary folders, the language, waiting, the CLI.

Import it as ``from tests import _support`` after putting the repository root on sys.path.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

# The tests' own data folder, set before desktopnote is imported, so no test reaches the real one.
os.environ.setdefault("DESKTOPNOTE_HOME", tempfile.mkdtemp(prefix="desktopnote-tests-"))

from PySide6.QtCore import QElapsedTimer  # noqa: E402
from PySide6.QtTest import QTest  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402


def qapp() -> QApplication:
    """Returns the QApplication every test module shares; windows closing never ends it."""
    app = QApplication.instance() or QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(False)
    return app


def temp_dir(test: unittest.TestCase) -> Path:
    """Returns a new empty folder that is removed after the test."""
    folder = Path(tempfile.mkdtemp(prefix="desktopnote-test-"))
    test.addCleanup(shutil.rmtree, folder, True)
    return folder


def use_language(test: unittest.TestCase, code: str) -> None:
    """Switches the app's language to code for the rest of the test."""
    from desktopnote import i18n

    test.addCleanup(i18n.set_language, i18n.language())
    i18n.set_language(code)


def wait_until(condition, timeout_ms: int = 3000, message: str = "") -> None:
    """Processes Qt events until condition() is true.

    Raises:
        AssertionError: If condition() is still false after timeout_ms.
    """
    clock = QElapsedTimer()
    clock.start()
    while not condition():
        if clock.elapsed() > timeout_ms:
            raise AssertionError(message or f"not reached within {timeout_ms} ms")
        QTest.qWait(5)


def run_cli(home: Path, *args: str) -> subprocess.CompletedProcess:
    """Runs desktopnote-cli with args on the data folder home; returns the finished process."""
    env = {**os.environ, "DESKTOPNOTE_HOME": str(home), "PYTHONIOENCODING": "utf-8"}
    return subprocess.run([sys.executable, "-m", "desktopnote.cli", *args], cwd=ROOT, env=env,
                          capture_output=True, text=True, encoding="utf-8", timeout=60)
