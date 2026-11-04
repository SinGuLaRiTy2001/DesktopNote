"""Checks GitHub for a newer release and installs it into the app folder.

The release is downloaded, checked against its published SHA-256 and unpacked into the app
folder (STAGING). Once the app has quit, a hidden PowerShell script replaces the files listed in
the installed release's manifest (MANIFEST) with the new ones and starts the new version, which
confirms with confirm_started(). If the swap fails, or the new version does not confirm within
STARTED_S, the script puts the old files back and starts the old version. The notes in the data
folder (storage.data_dir) are not changed.

Only a release build (PyInstaller) installs updates; from source, the release page is opened.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import zipfile
from dataclasses import dataclass
from pathlib import Path

from PySide6.QtCore import QByteArray, QCryptographicHash, QObject, QUrl, Signal
from PySide6.QtGui import QDesktopServices
from PySide6.QtNetwork import QNetworkAccessManager, QNetworkReply, QNetworkRequest

from .win_effects import CREATE_NO_WINDOW

REPO = "SinGuLaRiTy2001/DesktopNote"
RELEASES_API = f"https://api.github.com/repos/{REPO}/releases?per_page=20"
ZIP_NAME = "DesktopNote-{version}-win64.zip"
APP_FOLDER = "DesktopNote"  # The folder inside the release zip.
MANIFEST = "_internal/release-files.txt"  # A release's top-level files (scripts/build.py).
# The top-level files of releases from before the manifest (1.0.0rc1, 1.0.0rc2).
EARLY_RELEASE_FILES = ["DesktopNote.exe", "desktopnote-cli.exe", "_internal", "LICENSE", "THIRD_PARTY_NOTICES.md",
                       "licenses"]
NOTES_BACKUP = "notes.before-update.json"  # Kept until the new version has started.
STAGING = ".update-new"  # The unpacked release, in the app folder.
OLD = ".update-old"  # The replaced files, in the app folder, until the new version has started.
PENDING = "pending"  # In OLD while the new version has not confirmed that it started.
STARTED_S = 60  # How long the new version has to confirm that it started.
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


_PS_QUOTES = "'‘’‚‛"  # The characters PowerShell reads as a single quote.


def _ps(text: str | Path) -> str:
    """Quotes a value as a PowerShell string literal, doubling every kind of single quote in it."""
    return "'" + "".join(c * 2 if c in _PS_QUOTES else c for c in str(text)) + "'"


def install_script(app: Path, new: Path, staging: Path, pid: int, old_names: list[str], data: Path,
                   version: str, exe: str = "DesktopNote.exe", started_s: int = STARTED_S) -> str:
    """Returns the PowerShell script that swaps the app files once the app has quit.

    It moves the old release's files and the ones the new release replaces into OLD, moves the
    new files in and starts the new version. Once that version confirms (it deletes PENDING), the
    script removes OLD and the notes backup. If the swap fails, or there is no confirmation
    within ``started_s``, it stops the new version, moves the old files back, notes the error in
    desktopnote.log and starts the old version; the notes backup is kept. Either way it removes
    the download.

    Args:
        app: The app folder to update.
        new: The unpacked release's app folder.
        staging: The download's folder, removed in any case.
        pid: The running app's process id, to wait for.
        old_names: The installed release's top-level files (release_files()).
        data: The data folder, holding the notes backup and desktopnote.log.
        version: The new version, for the log.
        exe: The executable to start afterwards.
        started_s: How long the new version has to confirm that it started.
    """
    names = ", ".join(_ps(name) for name in old_names) or "@()"
    return f"""$ErrorActionPreference = 'Stop'
$app = {_ps(app)}; $new = {_ps(new)}; $old = Join-Path $app {_ps(OLD)}; $pending = Join-Path $old {_ps(PENDING)}
$exe = Join-Path $app {_ps(exe)}
$replace = @({names}) + @(Get-ChildItem -LiteralPath $new -Force | ForEach-Object {{ $_.Name }}) | Sort-Object -Unique
$moved = @(); $added = @()

function Write-Log($text) {{
    $line = "$(Get-Date -Format 'yyyy-MM-dd HH:mm:ss,fff') WARNING desktopnote.updates: update to {version} failed, "
    try {{ Add-Content -LiteralPath {_ps(data / "desktopnote.log")} -Value ($line + $text) -Encoding utf8 }} catch {{ }}
}}

function Move-Entry($from, $to) {{  # A rename: the whole file or folder moves, or nothing does.
    if (Test-Path -LiteralPath $from -PathType Container) {{ [IO.Directory]::Move($from, $to) }}
    else {{ [IO.File]::Move($from, $to) }}
}}

function Restore-Old {{
    Remove-Item -LiteralPath $pending -Force -ErrorAction SilentlyContinue
    foreach ($name in $script:added) {{
        Remove-Item -LiteralPath (Join-Path $app $name) -Recurse -Force -ErrorAction SilentlyContinue
    }}
    foreach ($name in $script:moved) {{
        try {{ Move-Entry (Join-Path $old $name) (Join-Path $app $name) }} catch {{ }}
    }}
    if ((Test-Path -LiteralPath $old) -and -not (Get-ChildItem -LiteralPath $old -Force)) {{
        Remove-Item -LiteralPath $old -Force -ErrorAction SilentlyContinue
    }}
}}

try {{ Wait-Process -Id {pid} -Timeout 60 }} catch {{ }}
Start-Sleep -Milliseconds 300
$swapped = $false
try {{
    if (Test-Path -LiteralPath $old) {{ Remove-Item -LiteralPath $old -Recurse -Force }}
    New-Item -ItemType Directory -Path $old | Out-Null
    New-Item -ItemType File -Path $pending | Out-Null
    foreach ($name in $replace) {{
        $target = Join-Path $app $name
        if (Test-Path -LiteralPath $target) {{ Move-Entry $target (Join-Path $old $name); $moved += $name }}
    }}
    foreach ($item in Get-ChildItem -LiteralPath $new -Force) {{
        Move-Entry $item.FullName (Join-Path $app $item.Name); $added += $item.Name
    }}
    $swapped = $true
}} catch {{
    $err = $_.Exception.Message
    Restore-Old
    Write-Log "the installed version stays: $err"
}}
Remove-Item -LiteralPath {_ps(staging)} -Recurse -Force -ErrorAction SilentlyContinue
if ($swapped) {{
    $started = $null
    try {{ $started = Start-Process -FilePath $exe -PassThru }} catch {{ }}
    $deadline = (Get-Date).AddSeconds({started_s})
    while ($started -and (Test-Path -LiteralPath $pending) -and (Get-Date) -lt $deadline) {{
        Start-Sleep -Milliseconds 250
    }}
    if (Test-Path -LiteralPath $pending) {{
        if ($started) {{
            try {{ Stop-Process -Id $started.Id -Force; $started.WaitForExit(10000) | Out-Null }} catch {{ }}
        }}
        Restore-Old
        Write-Log "the new version did not start, so the installed version stays"
    }} else {{
        Remove-Item -LiteralPath $old -Recurse -Force -ErrorAction SilentlyContinue
        Remove-Item -LiteralPath {_ps(data / NOTES_BACKUP)} -Force -ErrorAction SilentlyContinue
        exit
    }}
}}
Start-Process -FilePath $exe
"""


def confirm_started(folder: Path | None = None) -> None:
    """Tells a running install script that the new version has started (see install_script)."""
    folder = folder or app_folder()
    if folder is not None:
        try:
            (folder / OLD / PENDING).unlink(missing_ok=True)
        except OSError:
            pass


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

    The caller quits the app when ``ready`` is emitted; the script waits for that.

    Signals:
        ready: Emitted once the install script runs and waits for the app to quit.
        failed: Emitted with a message if the update stopped; nothing was changed then.
    """

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
        """Checks the download against the SHA-256 and installs it."""
        reply.deleteLater()
        if reply.error() != QNetworkReply.NoError:
            self.failed.emit(reply.errorString())
            return
        data = reply.readAll()
        digest = bytes(QCryptographicHash.hash(QByteArray(data), QCryptographicHash.Sha256).toHex()).decode("ascii")
        if digest != self._expected:
            self.failed.emit("the download does not match its checksum")
            return
        try:
            self._install(bytes(data))
        except (OSError, zipfile.BadZipFile) as exc:
            self.failed.emit(str(exc))

    def _install(self, data: bytes) -> None:
        """Unpacks the release into STAGING, backs up the notes and starts the install script.

        STAGING and the notes backup are removed again if this stops before the script runs.
        """
        staging = self._app / STAGING
        shutil.rmtree(staging, ignore_errors=True)
        try:
            staging.mkdir()
            archive = staging / "release.zip"
            archive.write_bytes(data)
            with zipfile.ZipFile(archive) as zf:
                zf.extractall(staging / "unpacked")
            archive.unlink()
            new = staging / "unpacked" / APP_FOLDER
            if not (new / "DesktopNote.exe").exists():
                raise OSError("the release does not contain DesktopNote.exe")
            backup_notes(self._data)
            script = staging / "install.ps1"
            script.write_text(install_script(self._app, new, staging, self._pid, release_files(self._app),
                                             self._data, self._release.version), encoding="utf-8-sig")
            subprocess.Popen(["powershell.exe", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
                              "-File", str(script)], creationflags=CREATE_NO_WINDOW, close_fds=True)
        except (OSError, zipfile.BadZipFile):
            # Removes the download and the notes backup; the app folder is unchanged.
            shutil.rmtree(staging, ignore_errors=True)
            (self._data / NOTES_BACKUP).unlink(missing_ok=True)
            raise
        self.ready.emit()


class UpdateFlow(QObject):
    """The state of checking for and installing an update, shown by the About window.

    It belongs to the app, so an update continues after the About window closes.

    Attributes:
        state: "idle", "checking", "current" (up to date), "available", "installing" or
            "failed".
        release: The update found, if any.
        error: Why the last check or update failed.

    Signals:
        changed: Emitted whenever the state changes.
        quit_requested: Emitted when the install script waits for the app to quit.
    """

    changed = Signal()
    quit_requested = Signal()

    def __init__(self, current: str, data: Path, parent: QObject | None = None):
        super().__init__(parent)
        self.state, self.release, self.error = "idle", None, ""
        self._data = data
        self._checker = UpdateChecker(current, self)
        self._checker.finished.connect(self._checked)
        self._updater = Updater(self)
        self._updater.ready.connect(self.quit_requested)
        self._updater.failed.connect(lambda error: self._set("failed", error=error))

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
                self._set("installing")
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

    def _set(self, state: str, error: str = "") -> None:
        """Sets the state and tells the About window."""
        self.state, self.error = state, error
        self.changed.emit()
