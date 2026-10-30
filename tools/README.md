# Tools

On-screen audits that check what unit tests can't. Each runs the real app against a temp data
folder, so your notes and settings are never touched, and works while DesktopNote is running.
Images go to `tools/out/`.

| Tool | Checks |
|---|---|
| `align_audit.py` | Icon/text alignment in menus and notes, English and Chinese, every text size (fails above 1 px) |
| `textsize_audit.py` | Text-size switching: fonts, typed text, box heights, placeholder size and colour |
| `contrast_probe.py` | Real glass backgrounds over black/white/striped desktops, in the format `tests/test_contrast.py` uses |
| `render_sheet.py` | Contact sheet: light/dark × English/Chinese × every text size |
| `startup_perf.py` | Startup time, memory, idle CPU |
| `screenshot.py` | README screenshots in `docs/`, light and dark (run with `QT_SCALE_FACTOR=2`) |

```powershell
.\.venv\Scripts\python tools\align_audit.py
$env:QT_SCALE_FACTOR = "1.5"; .\.venv\Scripts\python tools\render_sheet.py   # high-DPI check
```

New audits: import `_harness` first; it provides `start_app`, `note`, `grab` and measuring helpers.
