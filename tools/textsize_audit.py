"""Audits switching the text size of notes with typed text, empty items and placeholders.

Builds notes with typed text (a title plus text, to-do and reminder items), empty items (only
their placeholders show) and an item added at a non-default size, then switches the text size
in order and at random. After every switch it checks that:

  * widget fonts match: title = TITLE_PT x scale, items = BODY_PT x scale;
  * typed text carries no stale font size of its own;
  * every editor box fits its text (no clipping, no gaps);
  * empty-item placeholders render at the new size, not at the first size used;
  * placeholders keep the readable secondary colour, which style sheets can reset.

    python tools/textsize_audit.py [--lang en|zh] [--switches 30]
"""
from __future__ import annotations

import argparse
import math
import random
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _harness as H  # noqa: E402

from PySide6.QtCore import Qt  # noqa: E402
from PySide6.QtGui import QTextCursor  # noqa: E402
from PySide6.QtTest import QTest  # noqa: E402


def first_glyph_height(img, r) -> int:
    """Returns the height in pixels of the first glyph in r, or 0 if r holds no ink.

    In the placeholders the first glyph is a capital, so descenders do not count.
    """
    dark = lambda x, y: H.QColor(img.pixel(x, y)).lightness() < 175  # noqa: E731
    cols = [x for x in range(r.left(), r.right()) if any(dark(x, y) for y in range(r.top(), r.bottom()))]
    if not cols:
        return 0
    x0 = x1 = cols[0]
    while x1 + 1 in cols:
        x1 += 1
    rows = [y for y in range(r.top(), r.bottom()) if any(dark(x, y) for x in range(x0, x1 + 1))]
    return rows[-1] - rows[0] + 1


def main() -> int:
    """Runs the audit and returns the exit code (0 when no problem was found)."""
    ap =argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--lang", choices=["en", "zh"], default="en")
    ap.add_argument("--switches", type=int, default=30)
    args = ap.parse_args()
    from desktopnote.theme import BODY_PT, TEXT_SCALES, TITLE_PT

    zh = args.lang == "zh"
    app = H.start_app([
        H.note(300, 150, 340, 320, "购物" if zh else "Groceries",
               [("text", "正文" if zh else "Body"), ("todo", "牛奶" if zh else "Milk"),
                ("reminder", "牙医" if zh else "Dentist", {"due": "2030-01-01T09:00"})]),
        H.note(700, 150, 320, 300),  # Gets empty items, so only placeholders show.
    ], theme="light", language=args.lang, text_scale=1.0)
    problems: list[str] = []
    placeholder_heights: dict[float, set[tuple]] = {}

    def check(tag: str) -> None:
        """Checks fonts, typed text, editor heights and placeholders at the current size."""
        scale = app.store.settings.text_scale
        want_title, want_body = TITLE_PT * scale, BODY_PT * scale
        secondary = H.QColor(app.theme.secondary).name()
        for wi, win in enumerate(app.windows.values()):
            title_pt = win.title_edit.font().pointSizeF()
            if abs(title_pt - want_title) > 0.01:
                problems.append(f"{tag}: note {wi} title {title_pt:.2f}pt, want {want_title:.2f}")
            for ri, row in enumerate(win.rows):
                editor = row.editor
                if abs(editor.font().pointSizeF() - want_body) > 0.01:
                    problems.append(
                        f"{tag}: note {wi} row {ri} font {editor.font().pointSizeF():.2f}pt, want {want_body:.2f}")
                block = editor.document().begin()
                while block.isValid():
                    fragments = block.begin()
                    while not fragments.atEnd():
                        point_size = fragments.fragment().charFormat().fontPointSize()
                        if point_size and abs(point_size - want_body) > 0.01:
                            problems.append(f"{tag}: note {wi} row {ri} typed text stuck at {point_size}pt")
                        fragments += 1
                    block = block.next()
                need = math.ceil(max(editor.document().size().height(),
                                     editor.fontMetrics().lineSpacing() + 2 * editor.document().documentMargin())) + 2
                if editor.height() != need:
                    problems.append(f"{tag}: note {wi} row {ri} box {editor.height()}px, text needs {need}px")
        # Empty-note placeholders: the size follows the scale, the colour stays secondary.
        empty = list(app.windows.values())[1]
        img, dpr = H.grab(empty)
        heights = []
        for widget in [empty.title_edit] + [row.editor for row in empty.rows]:
            rect = H.rect_in(widget, empty, dpr)
            glyph_area = H.QRect(rect.x(), rect.y(), min(rect.width(), round(120 * dpr)), rect.height())
            heights.append(first_glyph_height(img, glyph_area))
            darkest = min((H.QColor(img.pixel(x, y)).lightness(), H.QColor(img.pixel(x, y)).name())
                          for x in range(rect.left(), rect.left() + round(60 * dpr))
                          for y in range(rect.top(), rect.bottom()))[1]
            if darkest != secondary:
                problems.append(f"{tag}: placeholder colour {darkest}, want {secondary}")
        placeholder_heights.setdefault(scale, set()).add(tuple(heights))

    def setup():
        """Types into the first note, adds empty items to the second and starts switching."""
        win, empty = list(app.windows.values())
        win.set_editing(True)
        QTest.mouseClick(win.title_edit, Qt.LeftButton)
        win.title_edit.end(False)
        QTest.keyClicks(win.title_edit, " x")  # Real typing can bake a font size into the text.
        for row in win.rows:
            row.editor.setFocus()
            row.editor.moveCursor(QTextCursor.End)
            QTest.keyClicks(row.editor, " typed")
        empty.set_editing(True)
        for kind in ("text", "todo", "reminder"):
            empty.add_item(kind)
        app.set_text_scale(1.15)
        win.add_item("todo")  # A row created at a non-default size.
        random.seed(1)
        order = list(TEXT_SCALES) + [random.choice(TEXT_SCALES) for _ in range(args.switches)]
        H.after(600, lambda: step(order, 0))

    def step(order, i):
        """Switches to the i-th text size of order and checks it, then continues."""
        if i == len(order):
            report()
            return
        app.set_text_scale(order[i])
        H.after(300, lambda: (check(f"switch {i + 1} -> {order[i]:g}"), step(order, i + 1)))

    def report():
        """Checks placeholder heights across sizes, prints the problems and ends the run."""
        for scale, seen in sorted(placeholder_heights.items()):
            if len(seen) > 1:
                problems.append(f"placeholders render differently at the same size {scale:g}: {sorted(seen)}")
        # The first item placeholder's height per scale.
        sizes = [next(iter(placeholder_heights[s]))[1] for s in sorted(placeholder_heights)]
        if sizes != sorted(sizes) or sizes[0] == sizes[-1]:
            problems.append(
                f"item placeholder does not follow the text size: {sizes} px across {sorted(placeholder_heights)}")
        for problem in problems[:30]:
            print("  " + problem)
        print(f"RESULT {'OK' if not problems else 'FAIL'}: {len(problems)} problem(s); "
              f"placeholder cap heights per size {sizes}")
        H.finish(app, 1 if problems else 0)

    H.after(1300, setup)
    return H.qapp().exec()


if __name__ == "__main__":
    sys.exit(main())
