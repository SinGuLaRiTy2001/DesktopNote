"""Crash-safe JSON persistence in %APPDATA%\\DesktopNote."""
from __future__ import annotations

import contextlib
import json
import logging
import os
import shutil
import tempfile
import time
from datetime import date, datetime
from pathlib import Path
from typing import IO, Iterator

from .models import Note, Settings

try:
    import msvcrt
except ImportError:  # Not Windows: the data folder is not locked.
    msvcrt = None

log = logging.getLogger(__name__)

SCHEMA_VERSION = 1
LOCK_FILE = "notes.lock"
SNAPSHOT_FOLDER = "backups"  # One copy of notes.json per day, in the data folder.
SNAPSHOT_DAYS = 7  # How many daily copies are kept.
REPLACE_TRIES = 3  # Attempts at replacing notes.json while another program holds it open.
REPLACE_WAIT_S = 0.1
LOCK_WAIT_S = 10.0  # How long locked() waits for another process to release the data folder.
LOCK_POLL_S = 0.05


class DataFolderBusy(OSError):
    """Another process holds the data folder's lock."""


def data_dir() -> Path:
    """Returns the data folder, creating it if needed.

    The ``DESKTOPNOTE_HOME`` environment variable overrides the default of
    ``%APPDATA%\\DesktopNote`` (or ``~/DesktopNote`` when APPDATA is not set).
    """
    base = os.environ.get("DESKTOPNOTE_HOME") or os.path.join(
        os.environ.get("APPDATA", str(Path.home())), "DesktopNote"
    )
    path = Path(base)
    path.mkdir(parents=True, exist_ok=True)
    return path


class Store:
    """Loads and saves all notes and settings as a single JSON file.

    Each save writes a temporary file, keeps the previous file as ``notes.json.bak`` (and the
    first file of each day in ``backups``) and then atomically replaces the original, so a crash
    never leaves a half-written file behind.

    A file that cannot be read is renamed to ``notes-unreadable-<time>.json`` (``-backup`` for
    the .bak) and never overwritten. If no file can be read, the store starts empty but does not
    count as a first run.

    Attributes:
        path: Location of the JSON file (``notes.json`` in :func:`data_dir` by default).
        notes: The loaded notes.
        settings: The loaded settings.
        set_aside: Files that could not be read, under the names they were moved to.
        from_backup: Whether the notes were loaded from ``notes.json.bak``.
    """

    def __init__(self, path: Path | None = None) -> None:
        self.path = path or data_dir() / "notes.json"
        self.notes: list[Note] = []
        self.settings = Settings()
        self.set_aside: list[Path] = []
        self.from_backup = False
        self._version = SCHEMA_VERSION
        self._extra: dict = {}  # Top-level keys this version does not know.
        self._stuck: list[Path] = []  # Unreadable files that could not be moved aside.
        self._snapshot_day: date | None = None
        self._lock: IO[bytes] | None = None  # The lock file while this store holds the lock.

    @property
    def backup_path(self) -> Path:
        """The previous version of the file, ``notes.json.bak``."""
        return self.path.with_suffix(".json.bak")

    @contextlib.contextmanager
    def locked(self, timeout_s: float | None = None) -> Iterator[None]:
        """Holds the data folder's lock while the block runs, waiting for another process to release it.

        Args:
            timeout_s: How long to wait; defaults to LOCK_WAIT_S.

        Raises:
            DataFolderBusy: If another process still holds the lock after timeout_s.
            OSError: If the lock file cannot be opened.
        """
        deadline = time.monotonic() + (LOCK_WAIT_S if timeout_s is None else timeout_s)
        while not self._try_lock():
            if time.monotonic() >= deadline:
                raise DataFolderBusy(f"another program is using {self.path.parent}")
            time.sleep(LOCK_POLL_S)
        try:
            yield
        finally:
            self._unlock()

    def try_load(self) -> bool:
        """Loads notes and settings if the data folder's lock is free now, without waiting.

        Returns:
            Whether the lock was free; if not, nothing was read.

        Raises:
            OSError: If the lock file cannot be opened.
        """
        if not self._try_lock():
            return False
        try:
            self.load()
        finally:
            self._unlock()
        return True

    def _try_lock(self) -> bool:
        """Takes the data folder's lock if it is free; returns whether it was."""
        fh = open(self.path.parent / LOCK_FILE, "a+b")
        if msvcrt is not None:
            fh.seek(0)
            try:
                msvcrt.locking(fh.fileno(), msvcrt.LK_NBLCK, 1)
            except OSError:
                fh.close()
                return False
        self._lock = fh
        return True

    def _unlock(self) -> None:
        """Releases the data folder's lock."""
        fh, self._lock = self._lock, None
        if msvcrt is not None:
            fh.seek(0)
            msvcrt.locking(fh.fileno(), msvcrt.LK_UNLCK, 1)
        fh.close()

    # --- Loading -------------------------------------------------------------
    def load(self) -> None:
        """Loads notes and settings from the JSON file, falling back to the backup.

        Files that cannot be read are moved aside (see the class docstring). If neither file can
        be read, the current (default) notes and settings are kept, with ``first_run`` off.
        """
        for candidate in (self.path, self.backup_path):
            if not candidate.exists():
                continue
            try:
                self._read(candidate)
            except (OSError, ValueError) as exc:
                log.warning("Failed to read %s: %s", candidate, exc)
                self._move_aside(candidate)
                continue
            self.from_backup = candidate == self.backup_path
            return
        if self.set_aside or self._stuck:
            self.settings.first_run = False

    def _read(self, path: Path) -> None:
        """Reads one file into the store.

        Raises:
            OSError: If the file cannot be read.
            ValueError: If it is not JSON, or not an object with a list of notes.
        """
        raw = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(raw, dict):
            raise ValueError("not a JSON object")
        notes = raw.get("notes", [])
        if not isinstance(notes, list):
            raise ValueError('"notes" is not a list')
        settings = raw.get("settings", {})
        if not isinstance(settings, dict):
            log.warning("Ignored the invalid settings %r", settings)
            settings = {}
        version = raw.get("version", SCHEMA_VERSION)
        if isinstance(version, int) and version > SCHEMA_VERSION:
            log.warning("%s is from a newer DesktopNote (data version %d)", path, version)
        self.settings = Settings.from_dict(settings)
        self.notes = [Note.from_dict(note) for note in notes if isinstance(note, dict)]
        self._version = max(version, SCHEMA_VERSION) if isinstance(version, int) else SCHEMA_VERSION
        self._extra = {k: v for k, v in raw.items() if k not in ("version", "settings", "notes")}

    def _move_aside(self, path: Path) -> None:
        """Renames an unreadable file; one that cannot be renamed stops saving until it can."""
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        suffix = "-backup" if path == self.backup_path else ""
        target = path.with_name(f"notes-unreadable-{stamp}{suffix}.json")
        try:
            os.replace(path, target)
        except OSError as exc:
            log.error("Could not move %s aside: %s", path, exc)
            if path not in self._stuck:
                self._stuck.append(path)
            return
        if path in self._stuck:
            self._stuck.remove(path)
        self.set_aside.append(target)

    # --- Saving --------------------------------------------------------------
    def save(self) -> None:
        """Writes all notes and settings to disk atomically, keeping backups of the old file.

        The backups are written on a best-effort basis; failing to write them is logged.

        Raises:
            OSError: If the file cannot be written, or an unreadable file is still in its place.
        """
        for path in list(self._stuck):
            self._move_aside(path)
        if self._stuck:
            raise OSError(f"{self._stuck[0]} could not be read and is not overwritten")
        payload = {**self._extra, "version": self._version, "settings": self.settings.to_dict(),
                   "notes": [note.to_dict() for note in self.notes]}
        fd, tmp_name = tempfile.mkstemp(prefix="notes-", suffix=".tmp", dir=self.path.parent)
        tmp_path = Path(tmp_name)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                json.dump(payload, fh, ensure_ascii=False, indent=2)
                # Flush to disk before the replace so a crash cannot leave an empty file in place.
                fh.flush()
                os.fsync(fh.fileno())
            if self.path.exists():
                self._back_up()
            self._replace(tmp_path)
        except BaseException:
            tmp_path.unlink(missing_ok=True)
            raise

    def _back_up(self) -> None:
        """Copies the current file to notes.json.bak and, once a day, into SNAPSHOT_FOLDER."""
        try:
            shutil.copy2(self.path, self.backup_path)
        except OSError as exc:
            log.warning("Could not write %s: %s", self.backup_path, exc)
        today = date.today()
        if self._snapshot_day == today:
            return
        folder = self.path.parent / SNAPSHOT_FOLDER
        try:
            folder.mkdir(exist_ok=True)
            snapshot = folder / f"notes-{today.isoformat()}.json"
            if not snapshot.exists():
                shutil.copy2(self.path, snapshot)
            for old in sorted(folder.glob("notes-????-??-??.json"))[:-SNAPSHOT_DAYS]:
                old.unlink()
        except OSError as exc:
            log.warning("Could not write the daily backup: %s", exc)
            return
        self._snapshot_day = today

    def _replace(self, tmp_path: Path) -> None:
        """Moves the written file into place, retrying while another program holds the old one."""
        for attempt in range(REPLACE_TRIES):
            try:
                os.replace(tmp_path, self.path)
                return
            except PermissionError:
                if attempt == REPLACE_TRIES - 1:
                    raise
                time.sleep(REPLACE_WAIT_S)
