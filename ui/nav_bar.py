"""分頁列：手寫字，選到的分頁塗一道青綠色麥克筆，下面一條毛筆線。"""
from __future__ import annotations

from PySide6.QtCore import QPointF, QRectF, Qt, Signal
from PySide6.QtGui import QFontMetrics, QPainter
from PySide6.QtWidgets import QSizePolicy, QWidget

from ui import theme

BAR_H = 50
TAB_PAD = 34
SLANT = 18


class NavBar(QWidget):
    currentChanged = Signal(int)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.items: list[str] = []
        self.current = 0
        self.hover = -1
        self.setFixedHeight(BAR_H)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.setMouseTracking(True)
        self.setFocusPolicy(Qt.StrongFocus)
        self.setAccessibleName("分頁")

    def addTab(self, text: str):
        self.items.append(text)
        self.update()

    def setCurrentIndex(self, index: int):
        index = max(0, min(index, len(self.items) - 1))
        if index != self.current:
            self.current = index
            self.update()
            self.currentChanged.emit(index)

    def currentIndex(self) -> int:
        return self.current

    # ---- 排版 ----
    def _rects(self) -> list[QRectF]:
        fm = QFontMetrics(theme.display_font(13))
        widths = [fm.horizontalAdvance(t) + TAB_PAD * 2 for t in self.items]
        x = (self.width() - sum(widths)) / 2
        rects = []
        for w in widths:
            rects.append(QRectF(x, 0, w, BAR_H))
            x += w
        return rects

    # ---- 滑鼠、鍵盤 ----
    def mouseMoveEvent(self, e):
        idx = next((i for i, r in enumerate(self._rects()) if r.contains(e.position())), -1)
        if idx != self.hover:
            self.hover = idx
            self.setCursor(Qt.PointingHandCursor if idx >= 0 else Qt.ArrowCursor)
            self.update()

    def leaveEvent(self, _):
        self.hover = -1
        self.update()

    def mousePressEvent(self, e):
        for i, r in enumerate(self._rects()):
            if r.contains(e.position()):
                self.setCurrentIndex(i)
                return

    def keyPressEvent(self, e):
        if e.key() == Qt.Key_Left:
            self.setCurrentIndex(self.current - 1)
        elif e.key() == Qt.Key_Right:
            self.setCurrentIndex(self.current + 1)
        else:
            super().keyPressEvent(e)

    # ---- 繪圖 ----
    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        f = theme.display_font(13)
        fm = QFontMetrics(f)
        ink = theme.c("text")
        for i, (text, r) in enumerate(zip(self.items, self._rects())):
            tw = fm.horizontalAdvance(text)
            x = r.center().x() - tw / 2
            if i == self.current:
                # 選到的分頁：青綠色麥克筆塗在字後面，下面一條毛筆線
                theme.draw_marker(p, QRectF(x - 8, r.center().y() - 6, tw + 18, 22), theme.c("teal"),
                                  theme.seed_of(text, "tab"), strokes=3, alpha=200)
                theme.draw_ink_line(p, QPointF(x - 2, r.bottom() - 6), QPointF(x + tw + 4, r.bottom() - 7),
                                    3, theme.seed_of(text))
            elif i == self.hover:
                theme.draw_ink_line(p, QPointF(x, r.bottom() - 7), QPointF(x + tw, r.bottom() - 7),
                                    1.6, theme.seed_of(text))
            p.setFont(f)
            p.setPen(ink if i in (self.current, self.hover) else theme.c("muted"))
            p.drawText(r.adjusted(0, -4, 0, -4), Qt.AlignCenter, text)
        p.end()
