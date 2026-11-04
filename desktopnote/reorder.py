"""Drag to reorder the rows of a note."""
from __future__ import annotations

from typing import TYPE_CHECKING

import shiboken6
from PySide6.QtCore import QObject, QPoint, QRect, QRectF, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QPainter, QPixmap, QRegion
from PySide6.QtWidgets import QScrollArea, QVBoxLayout, QWidget

from . import motion

if TYPE_CHECKING:
    from .controls import OverlayScrollBar
    from .items import ItemRow


class RowReorder(QObject):
    """Moves a dragged row with the pointer while the other rows glide out of its way.

    While a drag runs, the content layout is switched off and the rows are placed by hand. On the
    drop, the row glides into its slot, ``moved`` reports the new order, and the layout takes
    over again. Dragging near the top or bottom of the view scrolls it.

    The lifted row is frosted glass. When a hold starts, a blurred snapshot of the note's glass
    is taken (prepare()); while the row is lifted, each frame it gets that snapshot with the rows
    beneath it drawn in, blurred again (ItemRow.set_backdrop).

    Signals:
        moved: Emitted when a drop changed the order, with the dragged row and the row it now
            comes before (None at the end).
    """

    GLIDE_MS = 180  # Rows moving aside, and the dropped row gliding into its slot.
    SCROLL_EDGE = 28  # Px from the view's top or bottom at which dragging starts to scroll.
    SCROLL_MAX = 14  # Px scrolled per frame with the pointer at (or past) the edge.
    FROST_RADIUS = 8  # Blur of the rows seen through the lifted row.

    moved = Signal(object, object)

    def __init__(self, area: QScrollArea, layout: QVBoxLayout, indicator: OverlayScrollBar):
        """Initialises the reorder helper.

        Args:
            area: The scroll area holding the rows.
            layout: The layout the rows live in, inside the area's content widget.
            indicator: The area's OverlayScrollBar, flashed while dragging scrolls.
        """
        super().__init__(area)
        self._area, self._layout, self._indicator = area, layout, indicator
        self._rows: list[ItemRow] = []  # All rows, in their order when the drag began.
        self._row: ItemRow | None = None  # The dragged row.
        self._from = self._index = 0  # The dragged row's index when the drag began, and its slot.
        self._top = 0  # Content y of the first row.
        self._above: list[float] = []  # Centres of the rows above the dragged one at the start.
        self._below: list[float] = []  # Centres of the rows below it.
        self._grab_dy = 0  # Pointer offset from the dragged row's top.
        self._pointer = QPoint()
        self._dropping = False
        self._glides: dict[ItemRow, motion.WindowGlide] = {}
        self._watched: set[ItemRow] = set()  # Rows whose deletion _forget() handles.
        self._scroll_timer = QTimer(self)
        self._scroll_timer.timeout.connect(self._auto_scroll)
        self._glass: QPixmap | None = None  # Blurred snapshot of the view, taken when a hold starts.
        self._glass_rect = QRect()  # Where that snapshot was taken, in global coordinates.
        self._frost_timer = QTimer(self)
        self._frost_timer.timeout.connect(self._frost)
        self._frost_key: tuple | None = None  # What the lifted row's backdrop last showed.

    def prepare(self) -> None:
        """Takes the blurred snapshot of the note's glass behind the rows, before a drag.

        Called when a hold starts, while nothing has moved or lifted yet.
        """
        if self._row is not None:
            return
        view = self._area.viewport()
        self._glass_rect = QRect(view.mapToGlobal(QPoint(0, 0)), view.size())
        self._glass = motion.blurred_backdrop(self._glass_rect, QColor(0, 0, 0, 0))

    def active(self) -> bool:
        """Returns whether a drag (or its drop) is in progress."""
        return self._row is not None

    def start(self, rows: list[ItemRow], row: ItemRow, pointer: QPoint) -> None:
        """Starts dragging ``row``, one of ``rows``, with the pointer at global ``pointer``."""
        if self._row is not None or row not in rows:
            return
        self._rows, self._row = list(rows), row
        for r in rows:
            if r not in self._watched:
                self._watched.add(r)
                r.destroyed.connect(lambda _=None, r=r: self._forget(r))
        self._from = self._index = rows.index(row)
        self._top = rows[0].y()
        centres = [r.y() + r.height() / 2 for r in rows]
        self._above, self._below = centres[:self._from], centres[self._from + 1:]
        self._grab_dy = self._area.widget().mapFromGlobal(pointer).y() - row.y()
        self._pointer = pointer
        self._layout.setEnabled(False)
        row.raise_()
        row.set_lifted(True)
        self._scroll_timer.setInterval(motion.display_frame_ms())
        self._scroll_timer.start()
        if self._glass is None:
            self.prepare()
        self._frost_timer.setInterval(motion.display_frame_ms())
        self._frost_timer.start()
        self._frost()

    def move(self, pointer: QPoint) -> None:
        """Follows the pointer, at global ``pointer``, with the dragged row."""
        if self._row is not None and not self._dropping:
            self._pointer = pointer
            self._follow()

    def finish(self, animate: bool = True) -> None:
        """Drops the row: it settles while gliding into its slot, then the layout takes over.

        Args:
            animate: Whether the row glides into its slot; otherwise it is dropped at once.
        """
        if self._row is None or (self._dropping and animate):
            return
        if not self._dropping:
            self._row.set_lifted(False)  # Settles while it glides.
        self._dropping = True
        self._scroll_timer.stop()
        if animate:
            self._glide(self._row, self._tops(self._index)[self._row], self._dropped)
        else:
            self._dropped()

    # --- Placement -----------------------------------------------------------
    def _order(self, index: int) -> list[ItemRow]:
        """Returns the rows in the order they would have with the dragged row at ``index``."""
        order = [r for r in self._rows if r is not self._row]
        order.insert(index, self._row)
        return order

    def _tops(self, index: int) -> dict[ItemRow, int]:
        """Returns each row's top, in content coordinates, with the dragged row at ``index``."""
        tops, y = {}, self._top
        for r in self._order(index):
            tops[r] = y
            y += r.height()
        return tops

    def _follow(self) -> None:
        """Moves the dragged row under the pointer and, past a slot, the others aside."""
        row = self._row
        bottom = self._top + sum(r.height() for r in self._rows)
        y = self._area.widget().mapFromGlobal(self._pointer).y() - self._grab_dy
        y = max(self._top, min(y, bottom - row.height()))
        row.move(row.x(), y)
        # Moving up, it passes a row once its top is above that row's centre; moving down, once
        # its bottom is below it (centres as the rows lay at the start). Rows of any height can
        # reach either end.
        index = sum(1 for c in self._above if c <= y) + sum(1 for c in self._below if c < y + row.height())
        if index != self._index:
            self._index = index
            tops = self._tops(index)
            for r in self._rows:
                if r is not row:
                    self._glide(r, tops[r])

    def _glide(self, w: ItemRow, y: int, on_done=None) -> None:
        """Glides ``w`` to content y ``y``; a glide already heading there carries on."""
        glide = self._glides.get(w)
        if glide is None:
            glide = motion.WindowGlide(self, self.GLIDE_MS, lambda x, top, w=w: w.move(x, top), motion.EASE_OUT)
            self._glides[w] = glide
        if glide.is_running() and glide.target == (w.x(), y) and on_done is None:
            return
        glide.start((w.x(), w.y()), (w.x(), y), on_done)

    def _forget(self, w: ItemRow) -> None:
        """Stops tracking a deleted row; if it is the dragged row, the drag ends without a move."""
        self._watched.discard(w)
        if not shiboken6.isValid(self._layout):  # The whole note is being deleted.
            return
        glide = self._glides.pop(w, None)
        if glide is not None:
            glide.stop()
        if w is self._row:
            self._end()
            self._layout.activate()
        elif w in self._rows:
            if self._order(self._index).index(w) < self._index:
                self._index -= 1  # A row above the dragged one's slot is gone.
            self._rows.remove(w)

    def _dropped(self) -> None:
        """Ends the drop: reports the new order and hands the rows back to the layout."""
        row, order, moved = self._row, self._order(self._index), self._index != self._from
        self._frost()  # The last view beneath; the row keeps it while it settles.
        self._end()
        if moved:
            after = order.index(row) + 1
            self.moved.emit(row, order[after] if after < len(order) else None)
        self._layout.activate()

    def _end(self) -> None:
        """Stops the drag's glides and timers and gives the rows back to the layout."""
        for glide in self._glides.values():
            glide.stop()
        self._glides.clear()
        self._scroll_timer.stop()
        self._frost_timer.stop()
        self._glass, self._frost_key = None, None
        self._row, self._rows, self._dropping = None, [], False
        self._layout.setEnabled(True)

    def _frost(self) -> None:
        """Hands the lifted row a blurred view of the glass and the rows beneath it.

        Nothing is redrawn while the row and the rows beneath it stay where they are.
        """
        row = self._row
        if row is None or self._glass is None:
            return
        beneath = [r for r in self._rows if r is not row and r.geometry().intersects(row.geometry())]
        key = (row.mapToGlobal(QPoint(0, 0)), row.size(), [(r, r.geometry()) for r in beneath])
        if key == self._frost_key:
            return
        self._frost_key = key
        dpr = self._glass.devicePixelRatio()
        pm = QPixmap(round(row.width() * dpr), round(row.height() * dpr))
        pm.setDevicePixelRatio(dpr)
        pm.fill(Qt.transparent)
        p = QPainter(pm)
        at = row.mapToGlobal(QPoint(0, 0)) - self._glass_rect.topLeft()
        p.drawPixmap(QRectF(0, 0, row.width(), row.height()), self._glass,
                     QRectF(at.x() * dpr, at.y() * dpr, row.width() * dpr, row.height() * dpr))
        for r in beneath:
            r.render(p, r.pos() - row.pos(), QRegion(), QWidget.DrawChildren)
        p.end()
        row.set_backdrop(motion.blur(pm, self.FROST_RADIUS))

    def _auto_scroll(self) -> None:
        """Scrolls while the pointer is near the view's top or bottom, faster closer to it."""
        view = self._area.viewport()
        y = view.mapFromGlobal(self._pointer).y()
        if y < self.SCROLL_EDGE:
            direction, depth = -1, self.SCROLL_EDGE - y  # Up.
        elif y > view.height() - self.SCROLL_EDGE:
            direction, depth = 1, y - (view.height() - self.SCROLL_EDGE)  # Down.
        else:
            return
        step = round(self.SCROLL_MAX * min(1.0, depth / self.SCROLL_EDGE))
        bar = self._area.verticalScrollBar()
        before = bar.value()
        bar.setValue(before + direction * step)
        if bar.value() != before:
            self._indicator.flash()
            self._follow()
