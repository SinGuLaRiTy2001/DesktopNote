# DesktopNote

![Platform](https://img.shields.io/badge/platform-Windows%2010%20%7C%2011-lightgrey)
![Python](https://img.shields.io/badge/python-3.10%2B-lightgrey)
![Version](https://img.shields.io/badge/dynamic/toml?url=https%3A%2F%2Fraw.githubusercontent.com%2FSinGuLaRiTy2001%2FDesktopNote%2Fdev%2Fpyproject.toml&query=%24.project.version&label=version&color=lightgrey)
[![CI](https://github.com/SinGuLaRiTy2001/DesktopNote/actions/workflows/ci.yml/badge.svg)](https://github.com/SinGuLaRiTy2001/DesktopNote/actions/workflows/ci.yml)
[![License](https://img.shields.io/badge/license-MIT-lightgrey)](LICENSE)

Minimal frosted-glass sticky notes for the Windows desktop: text, to-dos and reminders that
stay exactly where you left them.

<picture>
  <source media="(prefers-color-scheme: dark)" srcset="docs/screenshot-dark.png">
  <img src="docs/screenshot-light.png" width="860" alt="DesktopNote notes on the desktop">
</picture>

## Features

- **Glass notes**: notes blur the desktop behind them, follow Windows' light or dark mode, and
  stay readable on any wallpaper.
- **Three kinds of items**: free text, to-dos you tick off, and reminders with a date and time.
  Click and hold an item to lift it, then drag it to reorder.
- **Reminders**: a reminder turns orange when it is due within the hour, and a Windows
  notification appears when it is due.
- **Colour tags**: tint a note with one of the preset colours or any custom colour, so notes
  are easy to tell apart.
- **Titles and keep-on-top**: give a note an optional title, or keep it above other windows.
- **Snap to align**: drag a note close to another and it snaps next to it, edges aligned; near
  the edge of a monitor, it sticks to that edge.
- **Dock to edge**: push a note into the left, right or top edge of any monitor to tuck it away,
  leaving a thin strip. It slides out when the cursor comes to the strip and stays above other
  windows.
- **Tray menu**: create notes, bring them all to the front, jump to one note by name, and change
  settings.
- **Scripts and AI agents**: a command line shows notes as JSON, makes notes and adds, changes or
  deletes items, so an agent can turn your email or calendar into to-dos and reminders.
- **Settings**: theme (light, dark or match Windows), language (English or Simplified Chinese),
  text size for all notes, and start at login.
- **Automatic saving**: changes are saved as you type, with a backup of the previous version and
  one copy per day for the last week. If saving keeps failing, the tray icon tells you.
- **Updates**: check for a new version in the About window and install it with one click; notes
  and settings are kept. If the new version does not start, the installed one comes back. A
  pre-release is also offered newer pre-releases. When run from source, the release page opens
  instead.

## Getting started

Requirements: 64-bit Windows 10 or 11 (full glass on Windows 11 22H2 and later).

1. Download `DesktopNote-<version>-win64.zip` from [Releases](https://github.com/SinGuLaRiTy2001/DesktopNote/releases).
2. Extract it anywhere, for example to `%LOCALAPPDATA%\Programs\`, and run `DesktopNote\DesktopNote.exe`.
   No installation is needed. Windows may warn about an app from an unknown publisher the first
   time; choose **More info** → **Run anyway**.

DesktopNote runs in the system tray. On first start it opens a welcome note that walks you
through the basics. To remove it, turn off **Start at Login**, quit, and delete the folder.

### Run from source

Requires Python 3.10+.

```powershell
git clone https://github.com/SinGuLaRiTy2001/DesktopNote.git
cd DesktopNote
py -3 -m venv .venv
.\.venv\Scripts\pip install -r requirements.txt
.\.venv\Scripts\pythonw DesktopNote.pyw
```

To build the release zip, see [docs/RELEASING.md](docs/RELEASING.md).

## Usage

### Notes

| Action | How |
|---|---|
| New note | Tray menu → **New Note**, or **⋯** → **New Note** on any note |
| Move | Drag the bar at the top of the note |
| Tuck away at an edge | Drag the note against the left, right or top edge of a monitor and push on until it turns see-through, then let go |
| Show a tucked-away note | Move the cursor onto its strip at the edge (on an edge between two monitors, rest it there a moment), or pick the note in the tray menu |
| Untuck a note | Drag it away from the edge |
| Move to another monitor | Drag it across; at the edge between the monitors, push on through |
| Resize | Drag an edge or a corner |
| Edit | Double-click the note, or tap the pencil at the bottom right |
| Finish editing | Tap **Done**, or press `Esc` |
| Colour tag | **⋯** → pick a colour, or the last swatch for a palette and hex input |
| Keep on top | **⋯** → **Keep on Top** (always on while a note is tucked away) |
| Delete | **⋯** → **Delete Note**, then confirm |

### Items

In edit mode, add items with the buttons at the bottom of the note.

| Action | How |
|---|---|
| Next item | `Enter` in a to-do or reminder adds another one below it |
| End a list | `Enter` on an empty to-do |
| Line break | `Enter` in a text item; `Shift` + `Enter` in a to-do or reminder |
| Remove an empty item | `Backspace` on an empty item |
| Delete an item | Hover over it, tap the **×** on the right, then **Delete** |
| Reorder items | Click and hold an item until it lifts, then drag it up or down (also outside edit mode) |
| Tick a to-do | Tap the circle on the left |
| Set a reminder's time | Tap **Add Time** (or the date) and pick a day and time; **Remove Time** clears it |

### Tray menu

- **Click or double-click** the tray icon to show every note and bring them to the front.
- **Right-click** it to open the menu. Every note is listed there by its title or first line;
  choose one to jump to it.
- **Settings** stay open while you change them. **Open Data Folder** shows where your notes are
  stored.
- **About DesktopNote** shows the version, **Check for Updates** and a link to this repository.

### Scripts and AI agents

`desktopnote-cli.exe`, next to `DesktopNote.exe` (from source: `python -m desktopnote.cli`), lets
scripts and AI agents manage notes and items, for example from your email or calendar. While DesktopNote runs,
a change shows at once; otherwise it is saved for the next start.

```
desktopnote-cli notes                                            # every note: id, title, counts
desktopnote-cli show Work                                        # one note and its items, with ids
desktopnote-cli new-note --title Work                            # a note; --title is optional
desktopnote-cli add todo "Reply to Anna" --note Work             # into "Work", made if missing
desktopnote-cli add reminder "Dentist" --due "2026-11-05 09:00"  # into "Inbox"
desktopnote-cli edit <item id> --text "Reply by Friday" --done   # also --due, --no-due, --undone
desktopnote-cli delete <item id>                                 # delete one item
```

A note is named by its id or its title, so notes without a title work too. `list` prints every
note with its items, and `done <item id>` is short for `edit <item id> --done`. Every command
prints one JSON object, `{"ok": true, ...}` or `{"ok": false, "error": "..."}`, and exits with 0,
1 on an error or 2 for invalid arguments. Commands run at the same time take turns. If the error
says DesktopNote did not answer in time, check with `list` before trying again: the change may
still be made.

## Data

Notes and settings are stored in `notes.json` in `%APPDATA%\DesktopNote\`, with the previous
version kept as `notes.json.bak` and the first version of each of the last seven days in
`backups\`. To go back to one of them, quit DesktopNote and copy it over `notes.json`. To move your
notes to another computer, copy this folder.

A file that cannot be read is never overwritten: it is renamed to `notes-unreadable-<time>.json`,
the notes come from the backup if possible, and the tray icon says so. Warnings and errors, if
any, are logged to `desktopnote.log`, and `DesktopNote.ico` is the icon on DesktopNote's
notifications. During an update, the notes are also copied to `notes.before-update.json` until the
new version has started.

DesktopNote connects to the internet only when you click **Check for Updates**: it asks GitHub
for the latest release and, if you install it, downloads it from there.

## How it's built

Built with [Claude Code](https://claude.com/claude-code) under a defined scope and workflow:

| Path | Contents |
|---|---|
| [`desktopnote/`](desktopnote/) | Application package |
| [`tests/`](tests/) | Unit tests, run in [CI](.github/workflows/ci.yml) on Python 3.10–3.13 |
| [`tools/`](tools/README.md) | On-screen audits for alignment, text size, contrast and startup time |
| [`scripts/`](scripts/) | Release build ([docs/RELEASING.md](docs/RELEASING.md)); changes in [CHANGELOG.md](CHANGELOG.md) |
| [`CLAUDE.md`](CLAUDE.md) | Product requirements that every change must meet |
| [`.claude/`](.claude/) | Claude Code settings and workflow commands (`/verify`, `/bugfix`, `/audit`) |

## License

[MIT](LICENSE). The Windows release bundles Qt, PySide6 and Python; see
[THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md).
