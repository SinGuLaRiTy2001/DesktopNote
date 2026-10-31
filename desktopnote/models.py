"""Plain data model for notes, serialised to JSON by :mod:`desktopnote.storage`."""
from __future__ import annotations

import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime
from typing import Literal

ItemType = Literal["text", "todo", "reminder"]


def new_id() -> str:
    """Returns a new random 12-character hexadecimal identifier."""
    return uuid.uuid4().hex[:12]


def now_iso() -> str:
    """Returns the current local time as an ISO 8601 string with second precision."""
    return datetime.now().isoformat(timespec="seconds")


@dataclass
class Item:
    """A single entry in a note: a text block, a to-do or a reminder.

    Attributes:
        type: The kind of item: "text", "todo" or "reminder".
        text: The item's text.
        done: Whether a to-do or reminder is completed.
        due: Reminders only: the due time as a local ISO string, e.g. "2026-10-21T18:00:00".
        notified: Whether the overdue notification for a reminder has already been shown.
        id: Unique identifier of the item.
    """

    type: ItemType = "text"
    text: str = ""
    done: bool = False
    due: str | None = None
    notified: bool = False
    id: str = field(default_factory=new_id)

    @property
    def due_dt(self) -> datetime | None:
        """The due time as a datetime, or None if it is unset or not a valid ISO string."""
        if not self.due:
            return None
        try:
            return datetime.fromisoformat(self.due)
        except ValueError:
            return None

    @classmethod
    def from_dict(cls, d: dict) -> "Item":
        """Builds an item from a serialised dict, ignoring unknown keys."""
        known = {k: d[k] for k in cls.__dataclass_fields__ if k in d}
        return cls(**known)


# Earlier tint names (panel tints, then the first tag set) mapped to the nearest current tag.
LEGACY_TINTS = {
    # Panel tints.
    "default": "none", "yellow": "lemon", "green": "pistachio", "blue": "sky", "purple": "lavender",
    "pink": "strawberry",
    # First tag set.
    "navy": "lavender", "cream": "lemon",
}


@dataclass
class Note:
    """A sticky note: its window geometry, appearance and items.

    Attributes:
        x: Left edge of the note window, in screen pixels.
        y: Top edge of the note window, in screen pixels.
        w: Width of the note window, in pixels.
        h: Height of the note window, in pixels.
        tint: A ``theme.TAGS`` key or a custom "#RRGGBB" colour, shown as a soft wash at the
            top of the note.
        pinned: Whether the note is kept on top of other windows.
        dock: The screen edge the note is hidden at ("left", "right" or "top"), or "". While
            docked, x and y keep the position where it shows when slid out.
        title: Optional title shown above the items.
        items: The note's items, in display order.
        id: Unique identifier of the note.
        created: Creation time as a local ISO string.
        updated: Last modification time as a local ISO string.
    """

    x: int = 200
    y: int = 200
    w: int = 300
    h: int = 340
    tint: str = "none"
    pinned: bool = False
    dock: str = ""
    title: str = ""
    items: list[Item] = field(default_factory=list)
    id: str = field(default_factory=new_id)
    created: str = field(default_factory=now_iso)
    updated: str = field(default_factory=now_iso)

    def is_empty(self) -> bool:
        """Returns whether the title and every item's text are blank."""
        return not self.title.strip() and all(not item.text.strip() for item in self.items)

    def label(self) -> str:
        """Returns the title, else the first non-empty line of the items, or "" if empty.

        Runs of whitespace in the returned line are collapsed to single spaces.
        """
        for text in [self.title] + [item.text for item in self.items]:
            for line in text.splitlines():
                if line.strip():
                    return " ".join(line.split())
        return ""

    def to_dict(self) -> dict:
        """Returns the note, including its items, as a JSON-serialisable dict."""
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "Note":
        """Builds a note from a serialised dict.

        Unknown keys are ignored and legacy tint names are migrated via ``LEGACY_TINTS``.
        """
        known = {k: d[k] for k in cls.__dataclass_fields__ if k in d and k != "items"}
        note = cls(**known)
        note.tint = LEGACY_TINTS.get(note.tint, note.tint)
        note.items = [Item.from_dict(item_dict) for item_dict in d.get("items", [])]
        return note


@dataclass
class Settings:
    """Application-wide user settings.

    Attributes:
        autostart: Whether DesktopNote starts at login.
        due_soon_minutes: Reminders within this many minutes of their due time show a clock icon.
        first_run: Whether the next start is the first one; it then applies the autostart
            setting and creates a welcome note if there are no notes.
        language: UI language code: "zh" or "en".
        theme: "auto" (follow Windows), "light" or "dark".
        text_scale: Note text size; one of ``theme.TEXT_SCALES``.
    """

    autostart: bool = True
    due_soon_minutes: int = 60
    first_run: bool = True
    language: str = "zh"
    theme: str = "auto"
    text_scale: float = 1.0

    @classmethod
    def from_dict(cls, d: dict) -> "Settings":
        """Builds settings from a serialised dict, ignoring unknown keys."""
        return cls(**{k: d[k] for k in cls.__dataclass_fields__ if k in d})
