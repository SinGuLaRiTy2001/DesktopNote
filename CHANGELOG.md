# Changelog

All notable changes to DesktopNote. The format follows [Keep a Changelog](https://keepachangelog.com/),
and versions follow [Semantic Versioning](https://semver.org/).

## [Unreleased]

### Added

- An About window in the tray menu, with the version and developer.

### Changed

- Scrolling a note glides smoothly: the wheel, a click on the scroll track and a new row coming into view.
- A note made from another note's menu starts fresh, with the default size and no colour tag;
  it still opens next to that note.

## [1.0.0rc1] - 2026-10-31

### Added

- Windows release: a portable folder app (`DesktopNote.exe`) in a zip, with a SHA-256 checksum.
- Warnings and errors are logged to `desktopnote.log` in the data folder.

### Changed

- A second launch hands over to the running app about a quarter faster.
- Switching between light and dark mode is about a third faster.
- Start at login follows the app when its folder is moved.

### Fixed

- Starting the app several times at once no longer opens more than one copy.

## [0.3.0] - 2026-10-31

### Added

- Dock to edge: drag a note against the left, right or top edge of the screen to tuck it away; it
  slides out when the cursor comes to it and is remembered across restarts.

### Fixed

- The delete confirmation is no longer cut off in narrow notes.
- To-do and reminder icons line up with text items and with their own text.

## [0.2.0] - 2026-10-30

### Added

- Snap to align: a note dragged close to another snaps next to it, edges aligned.

## [0.1.0] - 2026-10-30

### Added

- Frosted-glass notes with text, to-do and reminder items, titles and colour tags.
- Reminder notifications, keep-on-top, and a tray menu listing every note.
- Settings for theme, language (English and Simplified Chinese), text size and start at login.

[Unreleased]: https://github.com/SinGuLaRiTy2001/DesktopNote/compare/v1.0.0rc1...HEAD
[1.0.0rc1]: https://github.com/SinGuLaRiTy2001/DesktopNote/compare/v0.3.0...v1.0.0rc1
[0.3.0]: https://github.com/SinGuLaRiTy2001/DesktopNote/compare/v0.2.0...v0.3.0
[0.2.0]: https://github.com/SinGuLaRiTy2001/DesktopNote/compare/v0.1.0...v0.2.0
[0.1.0]: https://github.com/SinGuLaRiTy2001/DesktopNote/releases/tag/v0.1.0
