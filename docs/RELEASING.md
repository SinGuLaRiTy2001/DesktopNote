# Releasing

Releases are portable folder apps for 64-bit Windows 10 and 11, published as GitHub Releases.

## Build locally

```powershell
.\.venv\Scripts\pip install -r scripts\release-requirements.txt
.\.venv\Scripts\python scripts\build.py
```

This writes `dist\DesktopNote\` and `dist\DesktopNote-<version>-win64.zip` with a `.sha256` file.
The build packs only the Qt modules the app uses and leaves out unused Qt plugins and standard
library modules (see the lists at the top of `scripts/build.py`).

## Check before a release

- Unit tests pass, and `tools/align_audit.py` reports every pair within 1 px.
- The built `DesktopNote.exe` starts, shows its notes and hides at an edge; a second launch shows
  the running app's notes. `tools/startup_perf.py` stays near its last numbers.
- Explorer shows the exe's version and icon (Properties > Details).
- `desktopnote-cli.exe list` prints the notes as JSON, and `desktopnote-cli.exe add todo test`
  shows the item at once in the running app.

## Publish

1. Set the version in `pyproject.toml` and `desktopnote/__init__.py`.
2. In `CHANGELOG.md`, rename `[Unreleased]` to the version with today's date, and update the links.
3. Commit, tag `vX.Y.Z` and push the tag:

   ```powershell
   git tag -a vX.Y.Z -m "DesktopNote X.Y.Z"
   git push origin dev vX.Y.Z
   ```

The `Release` workflow then checks that the tag matches the version, runs the tests, builds the zip
with the pinned tools and publishes the GitHub Release with the changelog section as its notes.
Tags such as `v1.0.0rc1` are published as pre-releases.
