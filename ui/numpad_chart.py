"""方向數字對照圖（手繪墨線風格）：九宮格，每格一個箭頭和數字，中間是 5（中立）。"""
from __future__ import annotations

import math
import random

from PySide6.QtCore import QRectF, QSize, Qt
from PySide6.QtGui import QColor, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import QSizePolicy, QWidget

from ui import theme

# 數字 → (欄, 列, 角度)；角度 0 是向右，逆時針為正
CELLS = {7: (0, 0, 135), 8: (1, 0, 90), 9: (2, 0, 45),
         4: (0, 1, 180), 5: (1, 1, None), 6: (2, 1, 0),
         1: (0, 2, 225), 2: (1, 2, 270), 3: (2, 2, 315)}


def _arrow_path(cx: float, cy: float, size: float, angle: float, seed: int) -> QPainterPath:
    """粗箭頭（箭身＋三角頭），每個頂點微微抖動，看起來像手畫的。"""
    R = random.Random(seed)
    s = size / 2
    # 以向右為準的箭頭外形（中心在 0,0）
    pts = [(-s * 0.95, -s * 0.40), (s * 0.12, -s * 0.40), (s * 0.12, -s * 0.80), (s * 0.98, 0),
           (s * 0.12, s * 0.80), (s * 0.12, s * 0.40), (-s * 0.95, s * 0.40)]
    a = math.radians(angle)
    ca, sa = math.cos(a), math.sin(a)
    path = QPainterPath()
    for i, (x, y) in enumerate(pts):
        x += R.uniform(-1.2, 1.2)
        y += R.uniform(-1.2, 1.2)
        px, py = cx + x * ca + y * sa, cy - x * sa + y * ca     # 螢幕的 y 往下
        if i == 0:
            path.moveTo(px, py)
        else:
            path.lineTo(px, py)
    path.closeSubpath()
    return path


class NumpadChart(QWidget):
    """放在深色的連段清單面板裡：箭頭用紙色、墨線描邊，數字用墨色手寫字。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumSize(150, 150)
        self.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Fixed)
        self.setAccessibleName("方向數字對照圖")
        self.setToolTip("記法裡的方向數字：以角色面向右為準，6 是往前、4 是往後")

    def sizeHint(self) -> QSize:
        return QSize(220, 220)

    def heightForWidth(self, w: int) -> int:
        return w

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        side = min(self.width(), self.height()) - 8
        x0 = (self.width() - side) / 2
        y0 = (self.height() - side) / 2
        cell = side / 3
        paper = theme.c("panel")
        ink = theme.c("line")
        for num, (col, row, angle) in CELLS.items():
            cx = x0 + cell * (col + 0.5)
            cy = y0 + cell * (row + 0.5)
            seed = theme.seed_of("numpad", num)
            if angle is None:
                # 中間的 5：紙色圓圈，裡面是墨色數字
                r = QRectF(cx - cell * 0.36, cy - cell * 0.36, cell * 0.72, cell * 0.72)
                theme.draw_ink_ellipse(p, r, theme.c("ochre"), 2.6, seed, (2, 2))
            else:
                path = _arrow_path(cx, cy, cell * 0.92, angle, seed)
                # 麥克筆色錯開一點點，再描兩層墨線
                p.setPen(Qt.NoPen)
                p.setBrush(paper)
                p.drawPath(path.translated(2, 2))
                for w, alpha in ((2.6, 255), (1.1, 130)):
                    col = QColor(ink)
                    col.setAlpha(alpha)
                    p.setPen(QPen(col, w, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
                    p.setBrush(Qt.NoBrush)
                    p.drawPath(path)
            # 數字（放在箭身靠中心的位置）
            if angle is None:
                tx, ty = cx, cy
            else:
                a = math.radians(angle)
                tx = cx - math.cos(a) * cell * 0.19      # 箭身中間
                ty = cy + math.sin(a) * cell * 0.19
            p.setFont(theme.font(max(9.0, cell / 4.2), True))
            p.setPen(ink)
            p.drawText(QRectF(tx - cell / 2, ty - cell / 2, cell, cell), Qt.AlignCenter, str(num))
        p.end()
