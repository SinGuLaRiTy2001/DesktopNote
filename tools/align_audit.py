"""Audits the vertical alignment of every icon with its text.

Covers the tray menu, Settings menu and note menu rows, the to-do and reminder icons, the
due-date calendar icon and the pencil in the empty-note hint. For each pair the icon's ink
centre is compared with the text's visual middle (Latin: cap top to baseline; CJK: ink box).
Prints the offset in logical px (positive means the icon sits too low) and fails if any pair is
off by more than 1 px.

    python tools/align_audit.py                 # English + Chinese, all text sizes
    python tools/align_audit.py --lang zh --scale 1.15
    set QT_SCALE_FACTOR=1.5 && python tools/align_audit.py   # fractional DPI
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _harness as H  # noqa: E402  (Sets DESKTOPNOTE_HOME first.)
from PySide6.QtCore import QPoint, QRect  # noqa: E402
from PySide6.QtWidgets import QMenu, QWidgetAction  # noqa: E402

TOLERANCE = 1.0  # Largest allowed offset, in logical px.


def is_cjk(text: str) -> bool:
    """Returns whether text contains a CJK unified ideograph."""
    return any("一" <= ch <= "鿿" for ch in text)


def audit(lang: str, scale: float) -> list[tuple[str, str, float]]:
    """Runs the app in one language and text size and measures every icon and text pair.

    Returns:
        (where, text, offset) tuples; offset is in logical px, rounded to 0.5 px.
    """
    from desktopnote import icons
    zh = lang == "zh"
    # The hint's pencil is drawn in a marker colour so it can be told apart from the text.
    real_inline = icons.inline_img
    icons.inline_img = lambda name, color, size=16, **kw: real_inline(name, "#FF0000", size, **kw)
    app = H.start_app([
        H.note(300, 200, 320, 300, "购物清单" if zh else "Groceries", [
            ("todo", "买牛奶" if zh else "Buy milk"),
            ("todo", "已完成" if zh else "Done item", {"done": True}),
            ("reminder", "给牙医打电话" if zh else "Call dentist", {"due": "2030-01-01T09:00"})]),
        H.note(700, 200, 320, 200),  # Empty, so it shows the hint with the pencil.
    ], theme="light", language=lang, text_scale=scale)
    results: list[tuple[str, str, float]] = []

    def is_marker(img, x, y) -> bool:
        """Returns whether the pixel at (x, y) has the red marker colour of the pencil."""
        color = H.QColor(img.pixel(x, y))
        return color.red() > 150 and color.green() < 110

    def pair(where, name, img, icon_rect, text_rect, dpr, icon_is_red=False):
        """Records the offset between the icon in icon_rect and the text in text_rect."""
        if icon_is_red:
            marker = [(x, y) for x in range(icon_rect.left(), icon_rect.right())
                      for y in range(icon_rect.top(), icon_rect.bottom()) if is_marker(img, x, y)]
            icon_centre = (min(pt[1] for pt in marker) + max(pt[1] for pt in marker)) / 2 if marker else None
        else:
            icon_centre = H.ink_centre(img, icon_rect, threshold=175)
        text_centre = H.text_centre(img, text_rect, is_cjk(name), threshold=175)
        if icon_centre is not None and text_centre is not None:
            results.append((where, name, round((icon_centre - text_centre) / dpr * 2) / 2))

    def notes():
        """Measures the item icons, the due-date icons and the empty-note hint."""
        win = list(app.windows.values())[0]
        img, dpr = H.grab(win)
        for row in win.rows:
            if row.lead is not None:
                editor = row.editor
                origin = editor.mapTo(win, QPoint(0, 0))
                line = QRect(round((origin.x() + 2) * dpr), round(origin.y() * dpr), round(120 * dpr),
                             round((editor.fontMetrics().lineSpacing() + 4) * dpr))
                pair("note item", row.item.text, img, H.rect_in(row.lead, win, dpr), line, dpr)
            if row.due_btn is not None:
                button = H.rect_in(row.due_btn, win, dpr)
                icon_w = round((row._due_icon_px + 10) * dpr)
                pair("due date", row.due_btn.text(), img, QRect(button.x(), button.y(), icon_w, button.height()),
                     QRect(button.x() + icon_w, button.y(), button.width() - icon_w, button.height()), dpr)
        empty = list(app.windows.values())[1]
        img, dpr = H.grab(empty)
        hint = H.rect_in(empty.placeholder, empty, dpr)
        marker_cols = [x for x in range(hint.left(), hint.right()) for y in range(hint.top(), hint.bottom())
                       if is_marker(img, x, y)]
        if marker_cols:
            x0, x1 = min(marker_cols), max(marker_cols)
            marker_rows = [y for y in range(hint.top(), hint.bottom())
                           if any(is_marker(img, x, y) for x in range(x0, x1 + 1))]
            # The text band left of the pencil, padded vertically so the whole line fits inside.
            band = QRect(hint.left(), min(marker_rows) - 8, x0 - hint.left() - 3,
                         max(marker_rows) - min(marker_rows) + 16)
            pair("empty-note hint", empty.placeholder.text()[:0] + ("hint" if not zh else "提示"), img,
                 QRect(x0, hint.top(), x1 - x0 + 1, hint.height()), band, dpr, icon_is_red=True)

    def menu_rows(where, m: QMenu):
        """Measures every menu row that has an icon."""
        img, dpr = H.grab(m)
        for act in m.actions():
            if act.isSeparator() or isinstance(act, QWidgetAction) or act.icon().isNull():
                continue
            row_rect = m.actionGeometry(act)

            def band(x0, x1):
                """Returns the physical-pixel band of this row between logical x0 and x1."""
                return QRect(round(x0 * dpr), round(row_rect.top() * dpr), round((x1 - x0) * dpr),
                             round(row_rect.height() * dpr))
            pair(where, act.text(), img, band(8, 32), band(36, 200), dpr)

    def run():
        """Measures the notes, then opens the tray menu."""
        notes()
        app._populate_tray_menu()
        app.tray_menu.popup(QPoint(900, 150))
        H.after(400, menus)

    def menus():
        """Measures the tray menu, then opens and measures its Settings submenu."""
        menu_rows("tray menu", app.tray_menu)
        sub = app.tray_menu.findChildren(QMenu)[-1]
        sub.popup(QPoint(1200, 150))
        H.after(400, lambda: (menu_rows("settings menu", sub), sub.close(), app.tray_menu.close(), note_menu()))

    def note_menu():
        """Opens the first note's menu, measures it and ends the run."""
        win = list(app.windows.values())[0]
        win._show_menu()

        def done():
            """Measures the opened note menu, closes it and ends the run."""
            menu = [x for x in H.qapp().topLevelWidgets() if type(x).__name__ == "GlassMenu" and x.isVisible()][0]
            menu_rows("note menu", menu)
            menu.close()
            H.finish(app)
        H.after(400, done)

    H.after(1300, run)
    H.qapp().exec()
    icons.inline_img = real_inline
    return results


def main() -> int:
    """Runs one audit, or one child process per language and text size; returns the exit code."""
    from desktopnote.theme import TEXT_SCALES
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--lang", choices=["en", "zh"], action="append")
    ap.add_argument("--scale", type=float, action="append")
    args = ap.parse_args()
    worst = 0.0
    # Qt allows one QApplication per process: run each combination in a child process.
    if args.lang and len(args.lang) == 1 and args.scale and len(args.scale) == 1:
        rows = audit(args.lang[0], args.scale[0])
        for where, name, off in rows:
            print(f"  {where:15s} {off:+5.1f} px  {name[:40]}")
            worst = max(worst, abs(off))
        print(f"RESULT {len(rows)} pairs, worst {worst:.1f} px")
        return 1 if worst > TOLERANCE else 0
    import subprocess
    failed = False
    for lang in args.lang or ["en", "zh"]:
        for scale in args.scale or TEXT_SCALES:
            print(f"== {lang} @ {scale:g}", flush=True)
            child = subprocess.run([sys.executable, __file__, "--lang", lang, "--scale", str(scale)])
            failed |= child.returncode != 0
    print("FAIL: some pair is off by more than 1 px" if failed else "OK: every pair within 1 px")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
