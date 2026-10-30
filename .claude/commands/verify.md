---
description: Verify the current change against the definition of done
---

Verify the change described below (or the current changes if nothing is given) against the
requirements in CLAUDE.md.

1. Run the unit tests: `.venv/Scripts/python -m unittest discover tests`.
2. Pick the tools in `tools/` that cover what changed (see tools/README.md) and run them.
   Render `tools/render_sheet.py` for any visual change and look at the result.
3. For interactive changes, drive the real UI with real mouse input from a worker thread,
   in a temp `DESKTOPNOTE_HOME`.
4. Check light and dark theme, English and Chinese.
5. Report: what was checked, the measured numbers, and anything that failed or could not be
   checked. Do not claim a check you did not run.

$ARGUMENTS
