"""Notification tests: a reminder's two lines, how they reach Windows, and the app icon."""
import base64
import os
import struct
import subprocess
import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from PySide6.QtTest import QTest  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from desktopnote import app, icons, win_effects  # noqa: E402

_app = QApplication.instance() or QApplication(sys.argv)

# Texts that end a PowerShell string literal if they are pasted into the script.
QUOTES = ["Don't", "Don’t forget", "‘报告’", "x’) ; Write-Output INJECTED ; $z=(‘", "‚‛`$(1)\n\""]


def powershell_parse(script: str) -> tuple[int, list[str]]:
    """Returns the parse error count and the command names of a PowerShell script, without running it."""
    probe = ("$e=$null;$t=$null;"
             "$a=[System.Management.Automation.Language.Parser]::ParseInput($env:SCRIPT,[ref]$t,[ref]$e);"
             "$c=$a.FindAll({param($n) $n -is [System.Management.Automation.Language.CommandAst]},$true)|"
             "%{$_.GetCommandName()};\"$($e.Count)|$($c -join ',')\"")
    done = subprocess.run(["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", probe], capture_output=True,
                          text=True, env={**os.environ, "SCRIPT": script}, check=True)
    errors, commands = done.stdout.strip().split("|")
    return int(errors), commands.split(",")


class NotificationTest(unittest.TestCase):
    """The notification has the title, then the text, and no picture of its own."""

    def test_two_lines_and_no_picture(self):
        xml = win_effects.toast_xml("Work", "Call Anna")
        self.assertIn("<text>Work</text><text>Call Anna</text>", xml)
        self.assertNotIn("<image", xml)

    def test_text_is_escaped(self):
        xml = win_effects.toast_xml("A & B", "<b>'x'</b>")
        self.assertIn("A &amp; B", xml)
        self.assertNotIn("<b>", xml)

    def test_the_icon_holds_every_size(self):
        data = icons.app_ico()
        reserved, kind, count = struct.unpack("<HHH", data[:6])
        self.assertEqual((reserved, kind, count), (0, 1, len(icons.ICO_SIZES)))
        sides = [data[6 + 16 * i] or 256 for i in range(count)]  # 0 stands for 256.
        self.assertEqual(sides, list(icons.ICO_SIZES))


@unittest.skipUnless(win_effects.IS_WINDOWS, "PowerShell notifications are Windows-only")
class ToastScriptTest(unittest.TestCase):
    """Any title and text reach Windows as data: the script itself never changes."""

    def shown(self, title: str, text: str) -> tuple[str, dict]:
        """Returns the script and environment show_toast starts PowerShell with."""
        with mock.patch("subprocess.Popen") as popen:
            self.assertIsNotNone(win_effects.show_toast(title, text))
        args, kwargs = popen.call_args
        script = base64.b64decode(args[0][args[0].index("-EncodedCommand") + 1]).decode("utf-16-le")
        return script, kwargs["env"]

    def test_quotes_in_the_text_stay_text(self):
        plain, _ = self.shown("Work", "Call Anna")
        for text in QUOTES:
            with self.subTest(text=text):
                script, env = self.shown(text, text)
                self.assertEqual(script, plain)
                self.assertEqual(env["DESKTOPNOTE_TOAST_XML"], win_effects.toast_xml(text, text))
                self.assertEqual(env["DESKTOPNOTE_TOAST_APP_ID"], win_effects.APP_ID)

    def test_the_script_parses_and_runs_only_its_own_commands(self):
        script, _ = self.shown("Work", "Call Anna")
        errors, commands = powershell_parse(script)
        self.assertEqual(errors, 0)
        self.assertEqual(sorted(set(commands)), ["New-Object", "Out-Null"])

    def test_a_process_that_cannot_start_reports_none(self):
        with mock.patch("subprocess.Popen", side_effect=OSError("no PowerShell")):
            self.assertIsNone(win_effects.show_toast("Work", "Call Anna"))


class FallbackTest(unittest.TestCase):
    """A notification Windows does not show becomes a tray message."""

    def notify(self, proc) -> list[tuple[str, str]]:
        """Returns the tray messages shown for a notification whose process is ``proc``."""
        shown = []
        owner = mock.Mock(_tray_message=lambda heading, text: shown.append((heading, text)))
        with (mock.patch.object(app.win_effects, "show_toast", return_value=proc),
              mock.patch.object(app, "TOAST_CHECK_MS", 0)):
            app.DesktopNoteApp._notify(owner, "Work", "Call Anna")
            QTest.qWait(50)
        return shown

    def test_shown_notifications_need_no_tray_message(self):
        self.assertEqual(self.notify(mock.Mock(poll=lambda: 0)), [])
        self.assertEqual(self.notify(mock.Mock(poll=lambda: None)), [], "still showing")

    def test_failed_notifications_show_in_the_tray(self):
        self.assertEqual(self.notify(mock.Mock(poll=lambda: 1)), [("Work", "Call Anna")])
        self.assertEqual(self.notify(None), [("Work", "Call Anna")], "PowerShell did not start")


if __name__ == "__main__":
    unittest.main()
