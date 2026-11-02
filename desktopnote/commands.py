"""The operations behind the command line (cli.py).

They look at notes, make notes, and add, change, tick or delete items. They work on plain Note
objects, so the running app applies them to its open notes and the command line applies them to
the data file when the app is not running. Results are plain dicts that serialise to JSON.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from .models import Item, Note, now_iso

KINDS = ("text", "todo", "reminder")
DEFAULT_NOTE = "Inbox"  # Where items go when no note is named.
NEW_NOTE_W, NEW_NOTE_H = 300, 340  # Every new note's size, here and in the app.


class CommandError(ValueError):
    """A request that cannot be carried out, e.g. an unknown item or an invalid due time."""


@dataclass
class Change:
    """What a request changed, so the running app can update its windows.

    Attributes:
        note: The note that changed.
        item: The item that was added, changed or deleted; None for a new, empty note.
        action: What happened: "add", "edit", "delete" (to the item) or "new-note".
        new_note: Whether the note was created for the request.
    """

    note: Note
    item: Item | None
    action: str
    new_note: bool = False


# --- JSON ------------------------------------------------------------------------------
def item_dict(item: Item) -> dict:
    """Returns an item as a JSON-ready dict."""
    return {"id": item.id, "type": item.type, "text": item.text, "done": item.done, "due": item.due}


def note_dict(note: Note) -> dict:
    """Returns a note and its items as a JSON-ready dict."""
    return {"id": note.id, "title": note.title, "label": note.label(),
            "items": [item_dict(item) for item in note.items]}


def note_summary(note: Note) -> dict:
    """Returns a note without its items, with how many it has and how many are still open."""
    open_items = sum(1 for item in note.items if item.type != "text" and not item.done)
    return {"id": note.id, "title": note.title, "label": note.label(), "item_count": len(note.items),
            "open_count": open_items}


# --- Lookups and checks --------------------------------------------------------------
def parse_due(text: str | None) -> str | None:
    """Returns a due time as the app stores it ("YYYY-MM-DDTHH:MM"), or None for none.

    Args:
        text: A local date and time such as "2026-11-05 09:00" or "2026-11-05T09:00".

    Raises:
        CommandError: If the text is not a valid date and time.
    """
    if not text:
        return None
    try:
        return datetime.fromisoformat(text.strip()).isoformat(timespec="minutes")
    except ValueError:
        raise CommandError(f'invalid due time "{text}"; use e.g. "2026-11-05 09:00"') from None


def find_note(notes: list[Note], ref: str) -> Note | None:
    """Returns the note with the id ``ref``, else the one titled ``ref`` (case-insensitive).

    Returns:
        The note, or None if none matches.

    Raises:
        CommandError: If several notes have that title.
    """
    ref = ref.strip()
    by_id = next((note for note in notes if note.id == ref), None)
    if by_id is not None:
        return by_id
    titled = [note for note in notes if note.title.strip().casefold() == ref.casefold()]
    if len(titled) > 1:
        raise CommandError(f'{len(titled)} notes are titled "{ref}"; use the note\'s id (see `notes`)')
    return titled[0] if titled else None


def need_note(notes: list[Note], ref: str | None) -> Note:
    """Returns the note ``ref`` names (id or title).

    Raises:
        CommandError: If no note matches, or the title is ambiguous.
    """
    note = find_note(notes, ref or "")
    if note is None:
        raise CommandError(f"no note with id or title {ref!r}; see `notes`")
    return note


def find_item(notes: list[Note], item_id: str | None) -> tuple[Note, Item]:
    """Returns the item with the id ``item_id`` and its note.

    Raises:
        CommandError: If no note has such an item.
    """
    for note in notes:
        for item in note.items:
            if item.id == item_id:
                return note, item
    raise CommandError(f"no item with id {item_id!r}; see the ids in `show` or `list`")


def _text(request: dict) -> str:
    """Returns the request's item text, which must not be empty."""
    text = (request.get("text") or "").strip()
    if not text:
        raise CommandError("the item's text is empty")
    return text


# --- Commands ------------------------------------------------------------------------
def _notes(request: dict, notes: list[Note]) -> tuple[dict, Change | None]:
    """Lists every note without its items (the "notes" command)."""
    return {"ok": True, "notes": [note_summary(note) for note in notes]}, None


def _show(request: dict, notes: list[Note]) -> tuple[dict, Change | None]:
    """Shows one note and its items ("show")."""
    return {"ok": True, "note": note_dict(need_note(notes, request.get("note")))}, None


def _list(request: dict, notes: list[Note]) -> tuple[dict, Change | None]:
    """Lists every note and its items ("list")."""
    return {"ok": True, "notes": [note_dict(note) for note in notes]}, None


def _new_note(request: dict, notes: list[Note]) -> tuple[dict, Change | None]:
    """Makes a note, with the given title or none ("new-note")."""
    note = Note(w=NEW_NOTE_W, h=NEW_NOTE_H, title=(request.get("title") or "").strip())
    notes.append(note)
    return {"ok": True, "note": note_dict(note)}, Change(note, None, "new-note", new_note=True)


def _add(request: dict, notes: list[Note]) -> tuple[dict, Change | None]:
    """Adds an item at the end of a note, making the note if no note has that title yet ("add")."""
    kind = request.get("kind")
    if kind not in KINDS:
        raise CommandError(f"unknown item kind {kind!r}; use one of {', '.join(KINDS)}")
    text, due = _text(request), parse_due(request.get("due"))
    if due and kind != "reminder":
        raise CommandError("only reminders have a due time")
    ref = request.get("note") or DEFAULT_NOTE
    note, new_note = find_note(notes, ref), False
    if note is None:  # A title nobody has yet: the note is made with it.
        note, new_note = Note(w=NEW_NOTE_W, h=NEW_NOTE_H, title=ref.strip()), True
        notes.append(note)
    item = Item(type=kind, text=text, due=due)
    note.items.append(item)
    note.updated = now_iso()
    return ({"ok": True, "note": note_dict(note), "item": item_dict(item), "new_note": new_note},
            Change(note, item, "add", new_note=new_note))


def _edit(request: dict, notes: list[Note]) -> tuple[dict, Change | None]:
    """Changes an item's text, due time or done state in place ("edit")."""
    note, item = find_item(notes, request.get("item"))
    text, due, clear_due, done = request.get("text"), request.get("due"), request.get("clear_due"), request.get("done")
    if text is None and due is None and not clear_due and done is None:
        raise CommandError("nothing to change; give --text, --due, --no-due, --done or --undone")
    if (due is not None or clear_due) and item.type != "reminder":
        raise CommandError("only reminders have a due time")
    if done is not None and item.type == "text":
        raise CommandError("text items cannot be marked done")
    if text is not None:
        item.text = _text(request)
    if due is not None or clear_due:
        item.due = None if clear_due else parse_due(due)
        item.notified = False  # A new time is notified again, as when set in the app.
    if done is not None:
        item.done = bool(done)
    note.updated = now_iso()
    return {"ok": True, "note": note_dict(note), "item": item_dict(item)}, Change(note, item, "edit")


def _done(request: dict, notes: list[Note]) -> tuple[dict, Change | None]:
    """Marks an item done; short for edit with done set ("done")."""
    return _edit({"item": request.get("item"), "done": True}, notes)


def _delete(request: dict, notes: list[Note]) -> tuple[dict, Change | None]:
    """Deletes one item; its note stays, even when it becomes empty ("delete")."""
    note, item = find_item(notes, request.get("item"))
    note.items.remove(item)
    note.updated = now_iso()
    return {"ok": True, "note": note_dict(note), "deleted": item_dict(item)}, Change(note, item, "delete")


COMMANDS = {"notes": _notes, "show": _show, "list": _list, "new-note": _new_note, "add": _add,
            "edit": _edit, "done": _done, "delete": _delete}


def run(request: dict, notes: list[Note]) -> tuple[dict, Change | None]:
    """Carries out a request on ``notes``, changing them in place.

    Args:
        request: {"command": <a COMMANDS key>, ...} with that command's fields: "note" (an id or
            title) for show and add; "title" for new-note; "kind", "text" and "due" for add; and
            "item" (an item id) for edit, done and delete, plus "text", "due", "clear_due" and
            "done" for edit.
        notes: The notes to work on.

    Returns:
        The JSON-ready result, and what changed (None if nothing did).

    Raises:
        CommandError: If the request is invalid or names an unknown note or item.
    """
    command = COMMANDS.get(request.get("command"))
    if command is None:
        raise CommandError(f"unknown command {request.get('command')!r}")
    return command(request, notes)
