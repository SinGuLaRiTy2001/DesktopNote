"""Command line for scripts and AI agents.

It looks at notes, makes notes, and adds, changes, ticks or deletes items:

    desktopnote-cli notes                                   # every note: id, title, item counts
    desktopnote-cli show Work                               # one note (id or title) and its items
    desktopnote-cli list                                    # every note and its items
    desktopnote-cli new-note --title Work                   # a note, with or without a title
    desktopnote-cli add todo "Reply to Anna" --note Work    # --note: a note's id or title
    desktopnote-cli add reminder "Dentist" --due "2026-11-05 09:00"
    desktopnote-cli edit <item id> --text "Reply to Anna by Friday" --undone
    desktopnote-cli done <item id>
    desktopnote-cli delete <item id>

Every command prints one JSON object, {"ok": true, ...} or {"ok": false, "error": ...}, and
exits with 0, 1 on an error, or 2 for invalid arguments. While DesktopNote runs, the running app
carries out the command, so the change shows at once; otherwise the data file is updated
directly, one command at a time. In a source checkout it runs as ``python -m desktopnote.cli``.
"""
from __future__ import annotations

import argparse
import json
import sys
import time

from PySide6.QtNetwork import QLocalSocket

from . import commands
from .instance import COMMAND_PREFIX, SERVER_NAME, is_running

CONNECT_MS = 300
REPLY_MS = 5000
STARTING_WAIT_S = 5.0  # How long to wait for an instance that is still starting.
NO_ANSWER = ("DesktopNote did not answer in time; it may still carry out the command, so check with "
             "`list` before trying again")


def parser() -> argparse.ArgumentParser:
    """Returns the argument parser of the command line."""
    p = argparse.ArgumentParser(
        prog="desktopnote-cli",
        description="Looks at DesktopNote's notes, makes notes, and adds, changes, ticks or deletes items. "
                    "Prints JSON.")
    sub = p.add_subparsers(dest="command", required=True)
    note_ref = "a note's id (see notes) or title"
    item_ref = "the item's id (see show or list)"
    sub.add_parser("notes", help="print every note: its id, title, label and how many items it has")
    show = sub.add_parser("show", help="print one note and its items")
    show.add_argument("note", help=note_ref)
    sub.add_parser("list", help="print every note and its items")
    new_note = sub.add_parser("new-note", help="make a note, with or without a title")
    new_note.add_argument("--title", default="", help="the note's title (default: none)")
    add = sub.add_parser("add", help="add an item to a note; a title nobody has yet makes that note")
    add.add_argument("kind", choices=commands.KINDS, help="the kind of item")
    add.add_argument("text", help="the item's text")
    add.add_argument("--note", default=commands.DEFAULT_NOTE, help=f'{note_ref} (default "{commands.DEFAULT_NOTE}")')
    add.add_argument("--due", help='reminders only: local date and time, e.g. "2026-11-05 09:00"')
    edit = sub.add_parser("edit", help="change an item's text, due time or done state")
    edit.add_argument("item", help=item_ref)
    edit.add_argument("--text", help="the new text")
    when = edit.add_mutually_exclusive_group()
    when.add_argument("--due", help='reminders only: the new local date and time')
    when.add_argument("--no-due", dest="clear_due", action="store_true", help="reminders only: clear the time")
    state = edit.add_mutually_exclusive_group()
    state.add_argument("--done", dest="done", action="store_const", const=True, help="mark it done")
    state.add_argument("--undone", dest="done", action="store_const", const=False, help="mark it not done")
    done = sub.add_parser("done", help="mark a to-do or reminder done")
    done.add_argument("item", help=item_ref)
    delete = sub.add_parser("delete", help="delete one item")
    delete.add_argument("item", help=item_ref)
    return p


def send(request: dict) -> dict | None:
    """Sends a request to the running app and returns its reply, or None if none is running."""
    deadline = time.monotonic() + STARTING_WAIT_S
    while True:
        sock = QLocalSocket()
        sock.connectToServer(SERVER_NAME)
        if sock.waitForConnected(CONNECT_MS):
            break
        if not is_running() or time.monotonic() > deadline:
            return None  # No instance, or it never started listening.
        time.sleep(0.1)  # Starting up: its server is not listening yet.
    sock.write(COMMAND_PREFIX + json.dumps(request).encode("utf-8") + b"\n")
    sock.flush()
    reply = b""
    while not reply.endswith(b"\n") and sock.waitForReadyRead(REPLY_MS):
        reply += bytes(sock.readAll())
    sock.disconnectFromServer()
    try:
        return json.loads(reply)
    except ValueError:  # Nothing, or not JSON (e.g. an older DesktopNote, which only shows its notes).
        return {"ok": False, "error": NO_ANSWER}


def run_offline(request: dict) -> dict | None:
    """Carries out a request on the data file, for when the app is not running.

    The data folder is locked from loading to saving: commands run at the same time take turns,
    and the app waits for them before it loads.

    Returns:
        The result, or None if the app started meanwhile and should carry out the request.
    """
    from .storage import DataFolderBusy, Store  # Only needed here.

    store = Store()
    try:
        with store.locked():
            if is_running():
                return None
            store.load()
            result, change = commands.run(request, store.notes)
            if change is not None:
                store.save()
    except commands.CommandError as exc:
        return {"ok": False, "error": str(exc)}
    except DataFolderBusy:
        return {"ok": False, "error": "the notes are in use by another program, e.g. a DesktopNote that is "
                                      "starting or another command; try again"}
    except OSError as exc:
        return {"ok": False, "error": f"cannot read or write the notes: {exc}"}
    if store.set_aside:
        names = ", ".join(path.name for path in store.set_aside)
        result["warning"] = f"notes.json could not be read and was kept as {names}"
    return result


def main(argv: list[str] | None = None) -> int:
    """Runs the command line.

    Args:
        argv: The arguments, without the program name; defaults to sys.argv[1:].

    Returns:
        The exit code: 0 on success, 1 on an error. Invalid arguments end the program with 2
        (argparse).
    """
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")  # Titles and items may be in any language.
    args = parser().parse_args(argv)
    request = {key: value for key, value in vars(args).items() if value is not None}
    result = send(request)
    if result is None:
        result = run_offline(request)
    if result is None:
        result = send(request) or {"ok": False, "error": NO_ANSWER}
    print(json.dumps(result, ensure_ascii=False))
    return 0 if result.get("ok") else 1


if __name__ == "__main__":
    sys.exit(main())
