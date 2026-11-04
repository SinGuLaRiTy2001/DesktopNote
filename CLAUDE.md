# CLAUDE.md

Requirements set by the product owner for DesktopNote, a sticky-notes app for the Windows
desktop. They are binding for every change.

## Look and feel

- Keep the app clean, simple and good-looking: minimal elements, no "square in square"
  compositions, no decorative frames.
- Notes are frosted glass with a soft blur; the glass must not flicker, lag behind the cursor
  or lose its blur when moving, resizing or losing focus.
- The shadow around a note stays small, especially below it.
- No loud blue: use neutral greys for selection and hover states.
- Every element must be clearly visible in light and dark mode, on white and black backgrounds
  alike; check contrast for all elements in both modes.
- In light mode, secondary texts (hints, placeholders, due-soon orange) are dark enough to read.
- Refine, don't redesign: keep the frosted-glass look, palette, fonts and proportions.
- Parts with the same role look the same (corner radius, padding, hover and pressed shape).
  Reuse the shared values in `desktopnote/theme.py` instead of new literals; keep the radii
  Windows dictates and shapes derived from another (circles, pills, concentric insets).
- Check every visual change in light and dark, English and Chinese, at every text size and at
  100 %, 125 % and 150 % display scaling; sizes stay in logical pixels.

## Motion and interaction

- Use non-linear (eased) animations for creating and deleting notes, opening and closing menus,
  adding and removing items, month changes in the date picker and its height changes.
- Add a transition when switching between dark/light mode and between languages.
- Interactive elements (buttons, items, the delete button, the text-size knob) have hover
  effects with transitions in and out; the knob also has a grab effect.
- Menu hover follows the macOS model: a grey highlight that moves instantly to the current row,
  with no fade, reaching the panel edges and the separators.
- The icon colour of a menu option follows its text colour.
- Tooltips must not flicker.

## Alignment

- Icons and text are vertically aligned, in menus and in note items, in English and Chinese.
- Content inside boxes (e.g. the reminder's date box) is centred correctly.
- Keep optical corrections (uneven padding for low-sitting glyphs, icon nudges) and their
  comments; judge alignment by measuring the rendered result, not widget rectangles.

## Notes and items

- Notes hold text, to-do and reminder items, and an optional title.
- A ticked to-do uses the same grey check as a finished reminder.
- A reminder due soon turns orange; the reminder notification shows the app icon.
- The date picker is simple and neutral; the "Remove Time" option is available as soon as a
  date has been chosen, without reopening the picker.
- Resizing a note must not show stray edges (no scroll bar popping in).

## Colour tags

- Colour tags tint the note (not the drag bar, which stays neutral grey) so notes are easy to
  tell apart and find; offer macaron-style colours that are easy to distinguish, plus any
  custom colour via a palette or hex input.
- The tray menu lists the notes so a user can jump to the one they want.

## Tray menu and settings

- Clicking or double-clicking the tray icon shows every note and brings it to the front.
- Clicking a note while the tray menu is open keeps the note visible, without flicker.
- Settings live in a submenu and stay open while they are changed (theme, language,
  start at login).
- Settings use iOS-style controls: segmented controls, a sliding bar for text size, a switch.
- Text size has five steps, applies to all notes, defaults to the middle step on first start,
  and changes predictably whatever the input (dragging, resting near a step, rapid clicks).
- Languages: English and Simplified Chinese; themes: light, dark and follow-Windows.

## Quality

- Lightweight and quick to start; displays correctly on high- and low-resolution screens.
- Use only fonts that are safe for an open-source project.

## Code and repository

- Docstrings and comments in all code (app, dev tools, tests, scripts) follow the Google style
  guide; naming, coding conventions and formatting are consistent, clean and professional.
- The repository has three parts: application files, development files, and files showing a
  defined, standardised AI-assisted workflow (this file and `.claude/`). Keep documentation
  lean - don't explain every detail.
- Versions follow Semantic Versioning: 0.x while features are added (each feature series
  raises the minor version), 1.0.0 for the first release. The version is set in
  `pyproject.toml` and `desktopnote/__init__.py` and changes only at a milestone.
- Reply in the language the owner writes in.
