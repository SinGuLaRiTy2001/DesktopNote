"""Checks GitHub for a newer release and installs it into the app folder.

The release is downloaded, checked against its published SHA-256 and unpacked into the update
folder (TRANSACTION) in the app folder. Once the app has quit, a hidden PowerShell script
(UPDATE_SCRIPT) replaces the files listed in the installed release's manifest (MANIFEST) with
the new ones and starts the new version, which confirms with confirm_started(). If the swap
fails, or the new version does not confirm within STARTED_S, the script puts the old files back
and starts the old version. A journal in the update folder records each step, so an update cut
short (for example by a power cut) is undone, or finished if the new version had started, at
the next login or the next start (ready_to_start). The notes in the data folder
(storage.data_dir) are not changed.

Only a release build (PyInstaller) installs updates; from source, the release page is opened.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import zipfile
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from PySide6.QtCore import QLocale, QObject, QUrl, Signal
from PySide6.QtGui import QDesktopServices
from PySide6.QtNetwork import QNetworkAccessManager, QNetworkReply, QNetworkRequest

from . import i18n, win_effects
from .i18n import tr
from .win_effects import CREATE_NO_WINDOW

log = logging.getLogger(__name__)

REPO = "SinGuLaRiTy2001/DesktopNote"
RELEASES_API = f"https://api.github.com/repos/{REPO}/releases?per_page=20"
ZIP_NAME = "DesktopNote-{version}-win64.zip"
APP_FOLDER = "DesktopNote"  # The folder inside the release zip.
MANIFEST = "_internal/release-files.txt"  # A release's top-level files (scripts/build.py).
# The top-level files of releases from before the manifest (1.0.0rc1, 1.0.0rc2).
EARLY_RELEASE_FILES = ["DesktopNote.exe", "desktopnote-cli.exe", "_internal", "LICENSE", "THIRD_PARTY_NOTICES.md",
                       "licenses"]
NOTES_BACKUP = "notes.before-update.json"  # Kept until the new version has started.
TRANSACTION = ".update"  # In the app folder: the update in progress.
# In TRANSACTION: the unpacked release, the replaced files, the journal, the marker the new version
# deletes once it has started, the update script, and the lock the running script holds.
NEW, OLD, JOURNAL, PENDING, SCRIPT, SCRIPT_LOCK = "new", "old", "journal.json", "pending", "update.ps1", "script.lock"
STARTED_S = 60  # How long the new version has to confirm that it started.
# Where the recovery runs at the next login while an update is not finished.
RUN_ONCE = (r"HKCU:\Software\Microsoft\Windows\CurrentVersion\RunOnce", "DesktopNoteUpdate")
STAGES = {"a": 0, "b": 1, "rc": 2}  # Pre-release stages, before the final release.
TIMEOUT_MS = 30_000  # A request stops when no data arrives for this long.
_VERSION = re.compile(r"^v?(\d+)\.(\d+)\.(\d+)(?:(a|b|rc)(\d+))?$")


@dataclass
class Release:
    """A release that counts as an update.

    Attributes:
        version: Its version, e.g. "1.0.0".
        page: Its GitHub page.
        zip_url: Where its zip downloads from.
        sha_url: Where the zip's SHA-256 checksum file downloads from.
    """

    version: str
    page: str
    zip_url: str
    sha_url: str


# --- Versions --------------------------------------------------------------------------
def parse_version(text: str) -> tuple[int, int, int, int, int] | None:
    """Returns a version as a tuple that sorts like the versions do, or None if it is not one.

    "1.0.0rc2" < "1.0.0" < "1.0.1": a final release comes after its pre-releases.
    """
    match = _VERSION.match(text.strip())
    if match is None:
        return None
    major, minor, patch, stage, number = match.groups()
    rank = STAGES[stage] if stage else len(STAGES)
    return int(major), int(minor), int(patch), rank, int(number or 0)


def is_prerelease(version: str) -> bool:
    """Returns whether a version is a pre-release (a, b or rc)."""
    parsed = parse_version(version)
    return parsed is not None and parsed[3] < len(STAGES)


def pick_release(releases: list[dict], current: str) -> Release | None:
    """Returns the newest release that counts as an update for ``current``, or None.

    A pre-release build is also offered newer pre-releases; a final build only final releases.
    Drafts, releases without the zip and its checksum, and versions that cannot be read are
    skipped.

    Args:
        releases: Releases as GitHub's API lists them.
        current: The running version.
    """
    mine = parse_version(current)
    allow_pre = is_prerelease(current)
    best, best_version = None, mine
    for release in releases:
        version = parse_version(release.get("tag_name", ""))
        if (version is None or release.get("draft") or (release.get("prerelease") and not allow_pre)
                or (best_version is not None and version <= best_version)):
            continue
        name = release["tag_name"].lstrip("v")
        assets = {a.get("name"): a.get("browser_download_url") for a in release.get("assets", [])}
        zip_name = ZIP_NAME.format(version=name)
        if zip_name in assets and f"{zip_name}.sha256" in assets:
            best, best_version = Release(name, release.get("html_url", ""), assets[zip_name],
                                         assets[f"{zip_name}.sha256"]), version
    return best


# --- Network -------------------------------------------------------------------------
def _request(url: str) -> QNetworkRequest:
    """Returns a GET request for url that times out after TIMEOUT_MS without data."""
    request = QNetworkRequest(QUrl(url))
    request.setRawHeader(b"User-Agent", b"DesktopNote")
    request.setTransferTimeout(TIMEOUT_MS)
    return request


# --- Installing ----------------------------------------------------------------------
def app_folder() -> Path | None:
    """Returns the folder of the running release build, or None when running from source."""
    return Path(sys.executable).parent if getattr(sys, "frozen", False) else None


def can_install(folder: Path | None = None) -> bool:
    """Returns whether updates can be installed: a release build in a folder it may write to."""
    folder = folder or app_folder()
    if folder is None:
        return False
    try:
        with tempfile.TemporaryFile(dir=folder):
            return True
    except OSError:
        return False


def release_files(app: Path) -> list[str]:
    """Returns the top-level names in the app folder that belong to the installed release."""
    manifest = app / MANIFEST
    try:
        names = manifest.read_text(encoding="utf-8").split()
    except OSError:
        names = EARLY_RELEASE_FILES
    return [name for name in names if (app / name).exists()]


def backup_notes(data: Path) -> Path | None:
    """Copies notes.json to NOTES_BACKUP in the data folder for the update; returns its path."""
    notes = data / "notes.json"
    if not notes.exists():
        return None
    target = data / NOTES_BACKUP
    shutil.copy2(notes, target)
    return target


# Swaps the files (-WaitPid <the app's process id>) or recovers an update cut short (-Recover);
# everything else comes from the journal (see prepare). States: prepared, moving, installing,
# installed, committed, rolling_back.
UPDATE_SCRIPT = r"""param([int]$WaitPid = 0, [switch]$Recover)
$ErrorActionPreference = 'Stop'
$tx = $PSScriptRoot
$app = Split-Path -Parent $tx
$journalPath = Join-Path $tx 'journal.json'
$pending = Join-Path $tx 'pending'
$newDir = Join-Path $tx 'new'
$oldDir = Join-Path $tx 'old'

# One script at a time: the lock is held until this process ends.
try { $guard = [IO.File]::Open((Join-Path $tx 'script.lock'), 'OpenOrCreate', 'ReadWrite', 'None') } catch { exit 0 }

function Stop-Here($point) {  # Tests stop the script here, as a power cut would.
    if ($env:DESKTOPNOTE_TEST_STOP_AT -eq $point) { [Environment]::Exit(99) }
}

function Read-Journal {
    if (-not (Test-Path -LiteralPath $journalPath)) { return $null }
    return [IO.File]::ReadAllText($journalPath, [Text.Encoding]::UTF8) | ConvertFrom-Json
}

function Save-Journal($j) {  # Written to disk, then put in place in one step.
    $tmp = "$journalPath.tmp"
    $bytes = [Text.Encoding]::UTF8.GetBytes(($j | ConvertTo-Json -Depth 4))
    $fs = [IO.File]::Open($tmp, 'Create', 'Write', 'None')
    try { $fs.Write($bytes, 0, $bytes.Length); $fs.Flush($true) } finally { $fs.Dispose() }
    if (Test-Path -LiteralPath $journalPath) { [IO.File]::Replace($tmp, $journalPath, [NullString]::Value) }
    else { [IO.File]::Move($tmp, $journalPath) }
}

function Write-Log($j, $level, $text) {
    $line = "$(Get-Date -Format 'yyyy-MM-dd HH:mm:ss,fff') $level desktopnote.updates: $text"
    try { Add-Content -LiteralPath (Join-Path $j.data 'desktopnote.log') -Value $line -Encoding utf8 } catch { }
}

function Move-Entry($from, $to) {  # A rename: the whole file or folder moves, or nothing does.
    if (Test-Path -LiteralPath $from -PathType Container) { [IO.Directory]::Move($from, $to) }
    else { [IO.File]::Move($from, $to) }
}

function Start-App($j) {
    try { return [Diagnostics.Process]::Start((Join-Path $app $j.exe)) } catch { return $null }
}

function Set-Recovery($j) {  # Recovery at the next login, for as long as the transaction is open.
    try {
        if (-not (Test-Path -LiteralPath $j.run_once_key)) { [void](New-Item -Path $j.run_once_key -ErrorAction Stop) }
        $command = $j.run_once_command
        Set-ItemProperty -LiteralPath $j.run_once_key -Name $j.run_once_name -Value $command -ErrorAction Stop
    } catch { }
}

function Close-Transaction($j) {  # Removes the update folder, the journal last.
    Remove-Item -LiteralPath $newDir -Recurse -Force -ErrorAction SilentlyContinue
    Remove-Item -LiteralPath $pending -Force -ErrorAction SilentlyContinue
    try { Remove-ItemProperty -LiteralPath $j.run_once_key -Name $j.run_once_name -ErrorAction Stop } catch { }
    Remove-Item -LiteralPath $journalPath -Force
    $guard.Dispose()
    Remove-Item -LiteralPath $tx -Recurse -Force -ErrorAction SilentlyContinue
}

function Invoke-Swap($j) {
    $j.state = 'moving'; Save-Journal $j; Stop-Here 'moving'
    [void][IO.Directory]::CreateDirectory($oldDir)
    $k = 0
    foreach ($name in $j.old) {
        $at = Join-Path $app $name
        if (Test-Path -LiteralPath $at) { Move-Entry $at (Join-Path $oldDir $name) }
        $k++; Stop-Here "old-$k"
    }
    $j.state = 'installing'; Save-Journal $j; Stop-Here 'installing'
    $k = 0
    foreach ($name in $j.new) {
        Move-Entry (Join-Path $newDir $name) (Join-Path $app $name)
        $k++; Stop-Here "new-$k"
    }
    $j.state = 'installed'; Save-Journal $j; Stop-Here 'installed'
}

# Puts the installed release back as it was, deciding for each file from where it is, and can run
# again after an interruption. New files go back to the staging folder; nothing is deleted.
# Returns the files that could not be put back.
function Invoke-Rollback($j) {
    if ($j.state -ne 'rolling_back') { $j.state = 'rolling_back'; Save-Journal $j }
    $old = @($j.old)
    $problems = @()
    $k = 0
    foreach ($name in $j.new) {
        $at = Join-Path $app $name
        $staged = Join-Path $newDir $name
        $oldMoved = Test-Path -LiteralPath (Join-Path $oldDir $name)
        $oldReplaced = ($old -notcontains $name) -or $oldMoved  # What is at $at now came from the release.
        $installed = (Test-Path -LiteralPath $at) -and -not (Test-Path -LiteralPath $staged) -and $oldReplaced
        if ($installed) {
            try { [void][IO.Directory]::CreateDirectory($newDir); Move-Entry $at $staged }
            catch { $problems += "$name ($($_.Exception.Message))" }
        }
        $k++; Stop-Here "rollback-new-$k"
    }
    $k = 0
    foreach ($name in $old) {
        $at = Join-Path $app $name
        $kept = Join-Path $oldDir $name
        if (Test-Path -LiteralPath $kept) {
            if (Test-Path -LiteralPath $at) { $problems += "$name (a newer copy is in the way)" }
            else { try { Move-Entry $kept $at } catch { $problems += "$name ($($_.Exception.Message))" } }
        } elseif (-not (Test-Path -LiteralPath $at)) {
            $problems += "$name (missing)"
        }
        $k++; Stop-Here "rollback-old-$k"
    }
    if (-not $problems -and (Test-Path -LiteralPath $oldDir) -and -not (Get-ChildItem -LiteralPath $oldDir -Force)) {
        Remove-Item -LiteralPath $oldDir -Force
    }
    return $problems
}

function Finish-Rollback($j, $problems, $reason) {
    if ($problems) {
        $j.failed = "could not put back " + ($problems -join ', ')
        Save-Journal $j
        Write-Log $j 'ERROR' "undoing the update to $($j.version) is not finished, $($j.failed)"
        return
    }
    Close-Transaction $j
    Write-Log $j 'WARNING' "update to $($j.version) failed, the installed version stays: $reason"
    [void](Start-App $j)
}

function Invoke-Commit($j) {  # The new version has started: the replaced files can go.
    if ($j.state -ne 'committed') { $j.state = 'committed'; Save-Journal $j }
    Stop-Here 'committed'
    Remove-Item -LiteralPath $oldDir -Recurse -Force -ErrorAction SilentlyContinue
    Stop-Here 'cleaned'
    if (Test-Path -LiteralPath $oldDir) {
        Write-Log $j 'WARNING' "the files the update to $($j.version) replaced could not all be removed yet"
        return
    }
    Remove-Item -LiteralPath (Join-Path $j.data 'notes.before-update.json') -Force -ErrorAction SilentlyContinue
    Close-Transaction $j
}

$j = Read-Journal
if ($null -eq $j) { exit 0 }
Set-Recovery $j

if ($Recover) {
    if ($j.state -eq 'committed' -or ($j.state -eq 'installed' -and -not (Test-Path -LiteralPath $pending))) {
        Invoke-Commit $j
        [void](Start-App $j)
    } else {
        $problems = Invoke-Rollback $j
        Finish-Rollback $j $problems "the update was interrupted"
    }
    exit 0
}

try { Wait-Process -Id $WaitPid -Timeout 60 } catch { }
Start-Sleep -Milliseconds 300
try {
    Invoke-Swap $j
} catch {
    $reason = $_.Exception.Message
    $problems = Invoke-Rollback $j
    Finish-Rollback $j $problems $reason
    exit 0
}
$started = Start-App $j
$deadline = (Get-Date).AddSeconds($j.started_s)
while ($started -and (Test-Path -LiteralPath $pending) -and (Get-Date) -lt $deadline) { Start-Sleep -Milliseconds 250 }
if (-not (Test-Path -LiteralPath $pending)) {
    Invoke-Commit $j
    exit 0
}
if ($started) { try { $started.Kill(); [void]$started.WaitForExit(10000) } catch { } }
$problems = Invoke-Rollback $j
Finish-Rollback $j $problems "the new version did not start"
"""


def _powershell(script: Path, *args: str) -> list[str]:
    """Returns the command line that runs a PowerShell script file hidden."""
    return ["powershell.exe", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-WindowStyle", "Hidden",
            "-File", str(script), *args]


def _write_journal(path: Path, journal: dict) -> None:
    """Writes the journal to disk and then puts it in place in one step."""
    tmp = path.with_name(path.name + ".tmp")
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(journal, fh, indent=2)
        fh.flush()
        os.fsync(fh.fileno())
    os.replace(tmp, path)


def _set_run_once(key: str, name: str, command: str | None) -> None:
    """Sets (or with command None, removes) a value under a HKCU registry key in PowerShell form."""
    import winreg

    path = key.split(":\\", 1)[1]
    if command is None:
        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, path, 0, winreg.KEY_SET_VALUE) as handle:
                winreg.DeleteValue(handle, name)
        except FileNotFoundError:
            pass
        return
    with winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER, path, 0, winreg.KEY_SET_VALUE) as handle:
        winreg.SetValueEx(handle, name, 0, winreg.REG_SZ, command)


def prepare(app: Path, version: str, data: Path, exe: str = "DesktopNote.exe", started_s: int = STARTED_S,
            run_once: tuple[str, str] = RUN_ONCE) -> list[str]:
    """Readies the update of the release unpacked into TRANSACTION/NEW and registers its recovery.

    Writes the update script, the PENDING marker and the journal (state "prepared"), then sets the
    RunOnce value that recovers the update at the next login.

    Args:
        app: The app folder to update.
        version: The new version, for the log.
        data: The data folder, holding the notes backup and desktopnote.log.
        exe: The executable to start afterwards.
        started_s: How long the new version has to confirm that it started.
        run_once: The registry key (PowerShell form) and value name for the recovery.

    Returns:
        The arguments that start the script's swap, to which the app's process id is added.
    """
    tx = app / TRANSACTION
    new = sorted(entry.name for entry in (tx / NEW).iterdir())
    old = sorted(set(release_files(app)) | {name for name in new if (app / name).exists()})
    script = tx / SCRIPT
    script.write_text(UPDATE_SCRIPT, encoding="utf-8-sig")
    (tx / PENDING).write_bytes(b"")
    recovery = subprocess.list2cmdline(_powershell(script, "-Recover"))
    _write_journal(tx / JOURNAL, {
        "version": version, "state": "prepared", "old": old, "new": new, "exe": exe, "data": str(data),
        "started_s": started_s, "run_once_key": run_once[0], "run_once_name": run_once[1],
        "run_once_command": recovery, "failed": ""})
    _set_run_once(run_once[0], run_once[1], recovery)
    return _powershell(script, "-WaitPid")


def confirm_started(folder: Path | None = None) -> None:
    """Tells a running update script that the new version has started (see UPDATE_SCRIPT)."""
    folder = folder or app_folder()
    if folder is not None:
        try:
            (folder / TRANSACTION / PENDING).unlink(missing_ok=True)
        except OSError:
            pass


def _script_running(tx: Path) -> bool:
    """Returns whether an update script holds the transaction's lock."""
    lock = tx / SCRIPT_LOCK
    if not lock.exists():
        return False
    try:
        with open(lock, "a"):
            return False
    except PermissionError:
        return True


def ready_to_start(folder: Path | None = None, message=None) -> bool:
    """Returns whether this process may start the app, given an update that may be in progress.

    Without an update it returns True, after removing what an unstarted update left behind. While
    an update script runs, it returns True only for the version the script installed (state
    "installed" or "committed"). An update cut short is handed to the recovery script, which
    starts the app when it is done, and False is returned; if an earlier recovery could not finish,
    or the update files are incomplete, ``message`` first tells the user what to do.

    Args:
        folder: The app folder; defaults to the running build's.
        message: Called with a title and a text to show; defaults to win_effects.message_box.
    """
    folder = folder or app_folder()
    if folder is None:
        return True
    tx = folder / TRANSACTION
    if not (tx / JOURNAL).exists():
        if tx.exists() and not _script_running(tx):
            shutil.rmtree(tx, ignore_errors=True)
        return True
    try:
        state = json.loads((tx / JOURNAL).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        state = {}
    if _script_running(tx):
        return state.get("state") in ("installed", "committed")
    show = message or win_effects.message_box
    i18n.set_language("zh" if QLocale.system().language() == QLocale.Language.Chinese else "en")
    if not (tx / SCRIPT).exists() or not state:
        show("DesktopNote", tr("update.broken", folder=str(folder)))
        return False
    if state.get("failed"):
        show("DesktopNote", tr("update.unfinished", problem=state["failed"], folder=str(folder)))
    subprocess.Popen(_powershell(tx / SCRIPT, "-Recover"), creationflags=CREATE_NO_WINDOW, close_fds=True)
    return False


class UpdateChecker(QObject):
    """Asks GitHub for the newest release that counts as an update for this version.

    Signals:
        finished: Emitted with the Release found (or None) and an error message ("" if none).
    """

    finished = Signal(object, str)

    def __init__(self, current: str, parent: QObject | None = None, url: str = RELEASES_API):
        super().__init__(parent)
        self.current, self.url = current, url
        self._net = QNetworkAccessManager(self)
        self._reply: QNetworkReply | None = None

    def busy(self) -> bool:
        """Returns whether a check is under way."""
        return self._reply is not None

    def check(self) -> None:
        """Starts a check; ``finished`` reports the result."""
        if self._reply is not None:
            return
        request = _request(self.url)
        request.setRawHeader(b"Accept", b"application/vnd.github+json")
        self._reply = self._net.get(request)
        self._reply.finished.connect(self._done)

    def _done(self) -> None:
        """Picks the release from the reply and reports it, or the error."""
        reply, self._reply = self._reply, None
        reply.deleteLater()
        if reply.error() != QNetworkReply.NoError:
            self.finished.emit(None, reply.errorString())
            return
        try:
            releases = json.loads(bytes(reply.readAll()))
        except ValueError:
            self.finished.emit(None, "GitHub sent an unreadable answer")
            return
        self.finished.emit(pick_release(releases if isinstance(releases, list) else [], self.current), "")


class Updater(QObject):
    """Downloads a release, checks and unpacks it, and starts the script that installs it.

    The caller quits the app when ``ready`` is emitted; the script waits for that. Checking the
    download and unpacking it run on a worker thread, which emits stage("preparing"), ready and
    failed: connect them to methods of an object on the GUI thread to receive them there.

    Signals:
        stage: Emitted with "verifying" once the release is downloaded and with "preparing" once it
            matches its checksum, before it is unpacked.
        ready: Emitted once the install script runs and waits for the app to quit.
        failed: Emitted with a message if the update stopped; nothing was changed then.
    """

    stage = Signal(str)
    ready = Signal()
    failed = Signal(str)

    def __init__(self, parent: QObject | None = None):
        super().__init__(parent)
        self._net = QNetworkAccessManager(self)
        self._release: Release | None = None
        self._expected = ""
        self._app: Path | None = None
        self._pid = 0
        self._data: Path | None = None

    def start(self, release: Release, data: Path, app: Path | None = None, pid: int | None = None) -> None:
        """Starts updating to ``release``.

        Args:
            release: The release to install.
            data: The data folder: the notes are backed up there, and a failed install is noted in
                its desktopnote.log.
            app: The app folder to update; defaults to the running build's.
            pid: The process to wait for before installing; defaults to this one.
        """
        self._release, self._data = release, data
        self._app, self._pid = app or app_folder(), pid if pid is not None else os.getpid()
        self._get(release.sha_url, self._got_checksum)

    def _get(self, url: str, on_done) -> None:
        """Downloads ``url`` and passes the finished reply to ``on_done``."""
        reply = self._net.get(_request(url))
        reply.finished.connect(lambda: on_done(reply))

    def _got_checksum(self, reply: QNetworkReply) -> None:
        """Keeps the published SHA-256 and downloads the release."""
        reply.deleteLater()
        if reply.error() != QNetworkReply.NoError:
            self.failed.emit(reply.errorString())
            return
        words = bytes(reply.readAll()).decode("ascii", "replace").split()  # "<sha256>  <file name>"
        self._expected = words[0].lower() if words else ""
        self._get(self._release.zip_url, self._got_zip)

    def _got_zip(self, reply: QNetworkReply) -> None:
        """Checks the download against the SHA-256 and installs it, on a worker thread."""
        reply.deleteLater()
        if reply.error() != QNetworkReply.NoError:
            self.failed.emit(reply.errorString())
            return
        data = bytes(reply.readAll())
        self.stage.emit("verifying")
        threading.Thread(target=self._verify_and_install, args=(data,), name="DesktopNote update").start()

    def _verify_and_install(self, data: bytes) -> None:
        """Checks data against the SHA-256, then installs it; runs on the worker thread.

        Emits failed once if this stops for any ordinary exception; an unexpected one is logged
        with its traceback and reported as "the update could not be prepared".
        """
        try:
            if hashlib.sha256(data).hexdigest() != self._expected:
                self.failed.emit("the download does not match its checksum")
                return
            self.stage.emit("preparing")
            self._install(data)
        except (OSError, zipfile.BadZipFile) as exc:
            self.failed.emit(str(exc))
        except Exception:
            log.exception("The update could not be prepared")
            self.failed.emit("the update could not be prepared")

    def _install(self, data: bytes) -> None:
        """Unpacks the release into TRANSACTION, backs up the notes and starts the update script.

        The update folder, the notes backup and the recovery entry are removed again if any
        ordinary exception stops this before the script has started; once it has started, they stay
        for the script, and ready is emitted.

        Raises:
            OSError: If an earlier update is not finished, or the release cannot be unpacked.
            zipfile.BadZipFile: If the download is not a zip archive.
            Exception: Anything else that stopped the preparation, after the cleanup.
        """
        tx = self._app / TRANSACTION
        if (tx / JOURNAL).exists():
            raise OSError("an earlier update is not finished; start DesktopNote again first")
        shutil.rmtree(tx, ignore_errors=True)
        try:
            tx.mkdir()
            archive = tx / "release.zip"
            archive.write_bytes(data)
            with zipfile.ZipFile(archive) as zf:
                zf.extractall(tx / "unpacked")
            archive.unlink()
            if not (tx / "unpacked" / APP_FOLDER / "DesktopNote.exe").exists():
                raise OSError("the release does not contain DesktopNote.exe")
            os.replace(tx / "unpacked" / APP_FOLDER, tx / NEW)
            shutil.rmtree(tx / "unpacked")
            backup_notes(self._data)
            command = prepare(self._app, self._release.version, self._data)
            subprocess.Popen([*command, str(self._pid)], creationflags=CREATE_NO_WINDOW, close_fds=True)
        except Exception:
            # Removes the download, the recovery entry and the notes backup; the app folder is unchanged.
            self._discard(tx)
            raise
        self.ready.emit()

    def _discard(self, tx: Path) -> None:
        """Removes the recovery entry, the update folder and the notes backup, logging what fails."""
        for step in (lambda: _set_run_once(*RUN_ONCE, None), lambda: shutil.rmtree(tx, ignore_errors=True),
                     lambda: (self._data / NOTES_BACKUP).unlink(missing_ok=True)):
            try:
                step()
            except Exception:
                log.exception("Removing an unfinished update failed")


class UpdateFlow(QObject):
    """The state of checking for and installing an update, shown by the About window.

    It belongs to the app, so an update continues after the About window closes.

    Checking only looks for an update; installing it starts with a second act(). An install
    passes through "downloading", "verifying", "preparing" and "installing".

    Attributes:
        state: "idle", "checking", "current" (up to date), "available", "downloading",
            "verifying", "preparing", "installing" or "failed".
        release: The update found, if any.
        error: Why the last check or update failed.
        before_quit: Set by the shown About window. Called with a quit function once the install
            script waits; returns True if it calls that function itself (once "installing" is on
            screen), or False if the app should quit at once.

    Signals:
        changed: Emitted whenever the state changes.
        quit_requested: Emitted once when the install script waits for the app to quit.
    """

    changed = Signal()
    quit_requested = Signal()

    def __init__(self, current: str, data: Path, parent: QObject | None = None):
        super().__init__(parent)
        self.state, self.release, self.error = "idle", None, ""
        self.before_quit: Callable[[Callable[[], None]], bool] | None = None
        self._data = data
        self._quit_asked = False
        self._checker = UpdateChecker(current, self)
        self._checker.finished.connect(self._checked)
        self._updater = Updater(self)
        self._updater.stage.connect(self._set)
        self._updater.ready.connect(self._ready)
        self._updater.failed.connect(self._failed)

    def installs(self) -> bool:
        """Returns whether an update found can be installed here (a release build)."""
        return can_install()

    def act(self) -> None:
        """Does what the About window's update button offers in the current state."""
        if self.state in ("idle", "current", "failed"):
            self._set("checking")
            self._checker.check()
        elif self.state == "available":
            if self.installs():
                self._set("downloading")
                self._updater.start(self.release, self._data)
            else:
                QDesktopServices.openUrl(QUrl(self.release.page))

    def _checked(self, release: Release | None, error: str) -> None:
        """Moves to the state the check found."""
        if error:
            self._set("failed", error=error)
        else:
            self.release = release
            self._set("available" if release else "current")

    def _failed(self, error: str) -> None:
        """Moves to "failed" and keeps the error."""
        self._set("failed", error=error)

    def _ready(self) -> None:
        """Shows the "installing" state, then asks the app to quit.

        With before_quit set, the app quits once the About window shows that state.
        """
        self._set("installing")
        hold = self.before_quit
        try:
            held = hold is not None and hold(self._quit)
        except RuntimeError:  # The About window that set before_quit is already deleted.
            held = False
        if not held:
            self._quit()

    def _quit(self) -> None:
        """Emits quit_requested, the first time only."""
        if not self._quit_asked:
            self._quit_asked = True
            self.quit_requested.emit()

    def _set(self, state: str, error: str = "") -> None:
        """Sets the state and tells the About window."""
        self.state, self.error = state, error
        self.changed.emit()
