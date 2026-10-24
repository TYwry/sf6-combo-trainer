"""控制器測試畫面上的方向九宮格加搖桿位置。按鍵版面在 key_canvas.py。"""
from __future__ import annotations

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QPainter, QPen
from PySide6.QtWidgets import QSizePolicy, QWidget

from core.xinput_reader import PadState
from ui import theme

ARROWS = {1: "↙", 2: "↓", 3: "↘", 4: "←", 5: "●", 6: "→", 7: "↖", 8: "↑", 9: "↗"}
GRID = [[7, 8, 9], [4, 5, 6], [1, 2, 3]]


class DirectionWidget(QWidget):
    """左邊九宮格顯示目前方向（含數字），右邊圓圈顯示左搖桿實際位置與死區。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.state = PadState()
        self.deadzone = 0.5
        self.setMinimumSize(380, 214)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)

    def update_state(self, state: PadState, deadzone: float):
        self.state, self.deadzone = state, deadzone
        self.update()

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        cell, gap = 54, 6
        x0, y0 = 4, 8
        for r, row in enumerate(GRID):
            for col, num in enumerate(row):
                rect = QRectF(x0 + col * (cell + gap), y0 + r * (cell + gap), cell, cell)
                active = num == self.state.direction and self.state.connected
                theme.draw_ink_rect(p, rect.adjusted(1, 1, -3, -3), 10,
                                    theme.c("lit") if active else theme.c("panel"),
                                    2.6 if active else 1.8, theme.seed_of("dir", num), (2, 2))
                p.setPen(theme.c("lit_text") if active else theme.c("text"))
                p.setFont(theme.font(17, True))
                p.drawText(rect.adjusted(0, -6, 0, -6), Qt.AlignCenter, ARROWS[num])
                p.setFont(theme.font(8))
                p.setPen(theme.c("lit_text") if active else theme.c("muted"))
                p.drawText(rect.adjusted(0, 0, -5, -3), Qt.AlignRight | Qt.AlignBottom, str(num))
                # 小點標示十字鍵（左下）與搖桿（右下）各自讀到的方向
                if self.state.connected and num != 5:
                    if num == self.state.dpad_dir:
                        p.setBrush(theme.c("lit_text") if active else theme.c("accent"))
                        p.setPen(Qt.NoPen)
                        p.drawEllipse(QPointF(rect.left() + 8, rect.bottom() - 8), 3.5, 3.5)
                    if num == self.state.stick_dir:
                        p.setBrush(Qt.NoBrush)
                        p.setPen(QPen(theme.c("lit_text") if active else theme.c("accent"), 1.6))
                        p.drawEllipse(QPointF(rect.left() + 18, rect.bottom() - 8), 3.5, 3.5)

        # 搖桿圓圈
        size = 3 * cell + 2 * gap
        radius = 74
        cx = x0 + size + 28 + radius
        cy = y0 + size / 2
        theme.draw_ink_ellipse(p, QRectF(cx - radius, cy - radius, radius * 2, radius * 2),
                               theme.c("panel"), 2.4, 41, (3, 2))
        p.setPen(QPen(theme.c("muted"), 1.2, Qt.DashLine))
        p.setBrush(Qt.NoBrush)
        p.drawEllipse(QPointF(cx, cy), radius * self.deadzone, radius * self.deadzone)
        theme.draw_ink_line(p, QPointF(cx - radius + 6, cy), QPointF(cx + radius - 6, cy), 1.2, 42)
        theme.draw_ink_line(p, QPointF(cx, cy - radius + 6), QPointF(cx, cy + radius - 6), 1.2, 43)
        if self.state.connected:
            px = cx + self.state.lx * radius
            py = cy - self.state.ly * radius
            theme.draw_ink_ellipse(p, QRectF(px - 9, py - 9, 18, 18), theme.c("ochre"), 2.2, 44, (1.5, 1.5))
        p.setPen(theme.c("muted"))
        p.setFont(theme.font(8.5))
        p.drawText(QRectF(cx - 100, y0 + size + 6, 200, 18), Qt.AlignCenter, "虛線內＝中立")
        p.drawText(QRectF(x0, y0 + size + 6, size, 18), Qt.AlignCenter, "● 十字鍵　○ 搖桿")
        p.end()
