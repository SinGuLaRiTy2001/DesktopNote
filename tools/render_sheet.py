"""Renders a contact sheet of the same notes in every theme, language and text size.

Rows are text sizes; columns are theme x language (light and dark, English and Chinese). Each
cell is the note's own rendering composited on a mid grey (no desktop behind it), so sheets are
comparable between runs. For a high-DPI check, set QT_SCALE_FACTOR first:

    python tools/render_sheet.py                  # -> tools/out/sheet.png
    set QT_SCALE_FACTOR=2 && python tools/render_sheet.py --out sheet@2x.png
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _harness as H  # noqa: E402
from PySide6.QtGui import QColor, QImage, QPainter  # noqa: E402

CONTENT = {
    "en": ("Groceries", [("todo", "Buy milk"), ("todo", "Bread", {"done": True}),
                         ("reminder", "Call dentist", {"due": "2030-01-01T09:00"}),
                         ("text", "Some longer text that wraps onto a second line")]),
    "zh": ("购物清单", [("todo", "买牛奶"), ("todo", "面包", {"done": True}),
                     ("reminder", "给牙医打电话", {"due": "2030-01-01T09:00"}),
                     ("text", "一段比较长的文字，会自动换到第二行显示")]),
}
NOTE_W, NOTE_H = 280, 260  # Note size in logical px.


def main() -> int:
    """Renders every cell, saves the sheet and returns the exit code."""
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--out", default="sheet.png")
    ap.add_argument("--tint", default="sky", help="tag colour key for the notes (or 'none')")
    args = ap.parse_args()
    from desktopnote.theme import TEXT_SCALES
    cols = [(theme, lang) for theme in ("light", "dark") for lang in CONTENT]
    app = H.start_app([H.note(100 + i * (NOTE_W + 20), 100, NOTE_W, NOTE_H, CONTENT[lang][0], CONTENT[lang][1],
                              tint=args.tint)
                       for i, (_theme, lang) in enumerate(cols)], theme="light", language="en")
    wins = list(app.windows.values())
    cells: dict[tuple[int, int], QImage] = {}
    plan = [(row, theme, lang) for row in range(len(TEXT_SCALES)) for theme in ("light", "dark") for lang in CONTENT]

    def step(i: int):
        """Switches to the theme, language and text size of the i-th plan entry."""
        if i == len(plan):
            compose()
            return
        row, theme, lang = plan[i]
        app.set_theme_mode(theme)
        app.set_language(lang)  # App-wide, so dates and labels follow it.
        app.set_text_scale(TEXT_SCALES[row])
        H.after(400, lambda: shoot(i, row, theme, lang))

    def shoot(i: int, row: int, theme: str, lang: str):
        """Grabs the note of the matching column into its cell, then moves to the next step."""
        for col, (col_theme, col_lang) in enumerate(cols):
            if (col_theme, col_lang) == (theme, lang):
                img = wins[col].grab().toImage()
                img.setDevicePixelRatio(1.0)
                cells[(row, col)] = img
        step(i + 1)

    def compose():
        """Lays the cells out on a grey sheet, saves it and ends the run."""
        cell_w = max(img.width() for img in cells.values())
        cell_h = max(img.height() for img in cells.values())
        sheet = QImage((cell_w + 8) * len(cols), (cell_h + 8) * len(TEXT_SCALES), QImage.Format_ARGB32)
        sheet.fill(QColor("#7f7f7f"))
        p = QPainter(sheet)
        for (row, col), img in cells.items():
            p.drawImage(col * (cell_w + 8), row * (cell_h + 8), img)
        p.end()
        sizes = ", ".join(f"{s:g}" for s in TEXT_SCALES)
        columns = ", ".join(f"{theme}/{lang}" for theme, lang in cols)
        print(f"RESULT {H.save(sheet, args.out)}  (rows: text sizes {sizes}; columns: {columns})")
        H.finish(app)

    H.after(1300, lambda: step(0))
    return H.qapp().exec()


if __name__ == "__main__":
    sys.exit(main())
