"""Crash-safe JSON persistence in %APPDATA%\\DesktopNote."""
from __future__ import annotations

import json
import logging
import os
import shutil
from dataclasses import asdict
from pathlib import Path

from .models import Note, Settings

log = logging.getLogger(__name__)

SCHEMA_VERSION = 1


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

    Each save writes to a temporary file, keeps the previous file as ``notes.json.bak`` and then
    atomically replaces the original, so a crash never leaves a half-written file behind.

    Attributes:
        path: Location of the JSON file (``notes.json`` in :func:`data_dir` by default).
        notes: The loaded notes.
        settings: The loaded settings.
    """

    def __init__(self, path: Path | None = None) -> None:
        self.path = path or data_dir() / "notes.json"
        self.notes: list[Note] = []
        self.settings = Settings()

    def load(self) -> None:
        """Loads notes and settings from the JSON file, falling back to the backup.

        Unreadable files are logged and skipped; if neither file can be read, the current
        (default) notes and settings are kept.
        """
        for candidate in (self.path, self.path.with_suffix(".json.bak")):
            if not candidate.exists():
                continue
            try:
                raw = json.loads(candidate.read_text(encoding="utf-8"))
                self.settings = Settings.from_dict(raw.get("settings", {}))
                self.notes = [Note.from_dict(note_dict) for note_dict in raw.get("notes", [])]
                return
            except (OSError, ValueError, TypeError) as exc:
                log.warning("Failed to read %s: %s", candidate, exc)

    def save(self) -> None:
        """Writes all notes and settings to disk atomically, keeping a backup of the old file.

        Raises:
            OSError: If the file cannot be written.
        """
        payload = {
            "version": SCHEMA_VERSION,
            "settings": asdict(self.settings),
            "notes": [note.to_dict() for note in self.notes],
        }
        tmp_path = self.path.with_suffix(".json.tmp")
        with open(tmp_path, "w", encoding="utf-8") as fh:
            json.dump(payload, fh, ensure_ascii=False, indent=2)
            # Flush to disk before the replace so a crash cannot leave an empty file in place.
            fh.flush()
            os.fsync(fh.fileno())
        if self.path.exists():
            shutil.copy2(self.path, self.path.with_suffix(".json.bak"))
        os.replace(tmp_path, self.path)
