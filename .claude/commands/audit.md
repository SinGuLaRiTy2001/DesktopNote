---
description: Run every on-screen audit and summarise the results
---

Run the full audit suite and summarise:

1. `.venv/Scripts/python -m unittest discover tests`
2. `.venv/Scripts/python tools/align_audit.py`
3. `.venv/Scripts/python tools/textsize_audit.py --lang en` and `--lang zh`
4. `.venv/Scripts/python tools/render_sheet.py` (open and review `tools/out/sheet.png`)
5. `.venv/Scripts/python tools/startup_perf.py --notes 10 --idle 10`

Report a table: tool, result (pass / fail / numbers), and what to look at for any failure.
Run `tools/contrast_probe.py` too if glass, glow or theme colours changed since the last audit,
and say whether `tests/test_contrast.py` needs new MEASURED values.
