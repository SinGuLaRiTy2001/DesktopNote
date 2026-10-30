---
description: Fix a reported bug, reproduction first
---

Fix this bug: $ARGUMENTS

1. **Reproduce it first** with the real app in a temp `DESKTOPNOTE_HOME`, using input and data
   as close to the report as possible (real mouse, empty and filled notes, both languages).
   If it does not reproduce, say so and ask for details before changing code.
2. **Find the root cause** and state it in one or two sentences. Avoid fixes that only hide
   the symptom.
3. **Fix** with the smallest change that matches the surrounding code.
4. **Re-run the same reproduction** and show it now passes, then run `/verify`.
5. Report the cause, the fix and the before/after measurements.
