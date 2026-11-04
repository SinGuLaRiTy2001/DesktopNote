"""Plain data model for notes, serialised to JSON by :mod:`desktopnote.storage`.

Reading is lenient: a value of the wrong type is replaced by the field's default, and keys this
version does not know are kept in ``extra`` and written back unchanged.
"""
from __future__ import annotations

import functools
import logging
import types
import typing
import uuid
from dataclasses import dataclass, field, fields
from datetime import datetime
from typing import Literal

log = logging.getLogger(__name__)

ItemType = Literal["text", "todo", "reminder"]


def new_id() -> str:
    """Returns a new random 12-character hexadecimal identifier."""
    return uuid.uuid4().hex[:12]


def now_iso() -> str:
    """Returns the current local time as an ISO 8601 string with second precision."""
    return datetime.now().isoformat(timespec="seconds")


# --- Serialisation -----------------------------------------------------------
@functools.cache
def _hints(cls: type) -> dict[str, object]:
    """Returns the resolved field annotations of a dataclass."""
    return typing.get_type_hints(cls)


def _fits(value: object, hint: object) -> bool:
    """Returns whether a JSON value is valid for a field annotated with ``hint``."""
    origin = typing.get_origin(hint)
    if origin is Literal:
        return value in typing.get_args(hint)
    if origin in (typing.Union, types.UnionType):
        return any(_fits(value, arg) for arg in typing.get_args(hint))
    if origin is not None:  # A generic such as list[Item]: only the container is checked.
        return isinstance(value, origin)
    if hint is type(None):
        return value is None
    if hint in (int, float):
        return isinstance(value, (int, float) if hint is float else int) and not isinstance(value, bool)
    return isinstance(value, hint)


def _split(cls: type, d: dict, skip: tuple[str, ...] = ()) -> tuple[dict, dict]:
    """Splits a serialised dict into valid field values of ``cls`` and the keys it does not know.

    Values of the wrong type are logged and dropped, so those fields keep their defaults.

    Args:
        cls: The dataclass to build.
        d: The serialised dict.
        skip: Keys the caller reads itself; they are in neither result.

    Returns:
        The field values, and the unknown keys with their values.
    """
    hints = _hints(cls)
    known, extra = {}, {}
    for key, value in d.items():
        if key in skip:
            continue
        if key == "extra" or key not in hints:
            extra[key] = value
        elif _fits(value, hints[key]):
            known[key] = float(value) if hints[key] is float else value
        else:
            log.warning("Ignored the invalid %s.%s value %r", cls.__name__, key, value)
    return known, extra


def _to_dict(obj, **values) -> dict:
    """Returns a dataclass that has an ``extra`` field as a JSON-serialisable dict.

    Args:
        obj: The dataclass instance.
        **values: Serialised values that replace the fields of the same name.

    Returns:
        The keys in ``obj.extra``, then the fields, then ``values``; later keys replace earlier
        ones of the same name.
    """
    known = {f.name: getattr(obj, f.name) for f in fields(obj) if f.name != "extra"}
    return {**obj.extra, **known, **values}


# --- Model -------------------------------------------------------------------
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
        extra: Keys this version does not know, written back unchanged.
    """

    type: ItemType = "text"
    text: str = ""
    done: bool = False
    due: str | None = None
    notified: bool = False
    id: str = field(default_factory=new_id)
    extra: dict = field(default_factory=dict, repr=False, compare=False)

    @property
    def due_dt(self) -> datetime | None:
        """The due time as a datetime, or None if it is unset or not a valid ISO string."""
        if not self.due:
            return None
        try:
            return datetime.fromisoformat(self.due)
        except ValueError:
            return None

    def to_dict(self) -> dict:
        """Returns the item as a JSON-serialisable dict."""
        return _to_dict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "Item":
        """Builds an item from a serialised dict, keeping unknown keys in ``extra``."""
        known, extra = _split(cls, d)
        return cls(**known, extra=extra)


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
        x: Left edge of the note window, in Qt's device-independent screen coordinates.
        y: Top edge of the note window, in the same coordinates.
        w: Width of the note window, in device-independent pixels.
        h: Height of the note window, in device-independent pixels.
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
        extra: Keys this version does not know, written back unchanged.
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
    extra: dict = field(default_factory=dict, repr=False, compare=False)

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
        return _to_dict(self, items=[item.to_dict() for item in self.items])

    @classmethod
    def from_dict(cls, d: dict) -> "Note":
        """Builds a note from a serialised dict, keeping unknown keys in ``extra``.

        Items that are not dicts are skipped, and legacy tint names are migrated via
        ``LEGACY_TINTS``.
        """
        known, extra = _split(cls, d, skip=("items",))
        note = cls(**known, extra=extra)
        note.tint = LEGACY_TINTS.get(note.tint, note.tint)
        items = d.get("items", [])
        if not isinstance(items, list):
            log.warning("Ignored the invalid Note.items value %r", items)
            items = []
        note.items = [Item.from_dict(item) for item in items if isinstance(item, dict)]
        return note


@dataclass
class Settings:
    """Application-wide user settings.

    Attributes:
        autostart: Whether DesktopNote starts at login.
        due_soon_minutes: Reminders within this many minutes of their due time show a clock icon.
        first_run: Whether the next start is the first one; it then applies the autostart
            setting, picks the language from Windows and creates a welcome note if there are no
            notes.
        language: UI language code: "zh" or "en".
        theme: "auto" (follow Windows), "light" or "dark".
        text_scale: Note text size; one of ``theme.TEXT_SCALES``.
        extra: Keys this version does not know, written back unchanged.
    """

    autostart: bool = True
    due_soon_minutes: int = 60
    first_run: bool = True
    language: str = "zh"
    theme: str = "auto"
    text_scale: float = 1.0
    extra: dict = field(default_factory=dict, repr=False, compare=False)

    def to_dict(self) -> dict:
        """Returns the settings as a JSON-serialisable dict."""
        return _to_dict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "Settings":
        """Builds settings from a serialised dict, keeping unknown keys in ``extra``."""
        known, extra = _split(cls, d)
        return cls(**known, extra=extra)
