"""按鍵版面：照使用者的版面畫出每顆按鍵；編輯模式可以點選、拖曳移動。"""
from __future__ import annotations

from PySide6.QtCore import QPointF, QRectF, Qt, Signal
from PySide6.QtGui import QPainter, QPen
from PySide6.QtWidgets import QSizePolicy, QWidget

from ui import theme

KEY = 1.0          # 一格按鍵的寬（單位）
KEY_H = 0.8        # 方形鍵的高
EDIT_UNIT = 88     # 編輯模式每單位幾像素
EDIT_COLS, EDIT_ROWS = 14, 5
SNAP = 0.1

INPUT_NAMES = {"UP": "↑", "DOWN": "↓", "LEFT": "←", "RIGHT": "→"}


def key_title(key: dict) -> str:
    if key.get("label"):
        return key["label"]
    return "+".join(INPUT_NAMES.get(i, i) for i in key["inputs"])


class KeyCanvas(QWidget):
    selected = Signal(str)     # 點選的按鍵 id（空字串＝沒選）
    moved = Signal()

    def __init__(self, editable: bool = False, parent=None):
        super().__init__(parent)
        self.editable = editable
        self.layout_keys: list[dict] = []
        self.lit: set[str] = set()
        self.action_label = lambda code: code
        self.selected_id = ""
        self._drag = None
        if editable:
            self.setFixedSize(int(EDIT_COLS * EDIT_UNIT) + 8, int(EDIT_ROWS * EDIT_UNIT) + 8)
        else:
            self.setMinimumSize(360, 220)
            self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)

    def set_layout(self, keys: list[dict]):
        self.layout_keys = keys
        self._recenter()
        self.update()

    def _bbox(self):
        keys = self._keys()
        min_x = min(k["x"] for k in keys)
        min_y = min(k["y"] for k in keys)
        max_x = max(k["x"] for k in keys) + KEY
        max_y = max(k["y"] for k in keys) + KEY
        return min_x, min_y, max_x, max_y

    def _recenter(self):
        """編輯模式：整組按鍵放在畫布正中間（拖曳中不動，放開後才重新置中）。"""
        self._edit_off = QPointF(4, 4)
        if not self.layout_keys:
            return
        min_x, min_y, max_x, max_y = self._bbox()
        self._edit_off = QPointF(4 + ((EDIT_COLS - (max_x - min_x)) / 2 - min_x) * EDIT_UNIT,
                                 4 + ((EDIT_ROWS - (max_y - min_y)) / 2 - min_y) * EDIT_UNIT)

    def set_lit(self, lit: set[str]):
        if lit != self.lit:
            self.lit = lit
            self.update()

    def _keys(self) -> list[dict]:
        return self.layout_keys

    # ---- 座標 ----
    def _scale_offset(self):
        keys = self._keys()
        if self.editable:
            return EDIT_UNIT, getattr(self, "_edit_off", QPointF(4, 4))
        if not keys:
            return EDIT_UNIT, QPointF(4, 4)
        min_x, min_y, max_x, max_y = self._bbox()
        unit = min((self.width() - 8) / max(1, max_x - min_x),
                   (self.height() - 8) / max(1, max_y - min_y), 72)
        # 放在正中間
        ox = (self.width() - (max_x - min_x) * unit) / 2
        oy = (self.height() - (max_y - min_y) * unit) / 2
        return unit, QPointF(ox - min_x * unit, oy - min_y * unit)

    def _rect(self, key, unit, off) -> QRectF:
        h = KEY if key.get("shape") == "round" else KEY_H
        size = unit * 0.9
        return QRectF(off.x() + key["x"] * unit, off.y() + key["y"] * unit, size, size * h)

    # ---- 繪圖 ----
    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        unit, off = self._scale_offset()
        if self.editable:
            theme.draw_ink_rect(p, QRectF(2, 2, self.width() - 6, self.height() - 6), 14,
                                theme.c("panel_alt"), 1.6, 51, (2, 2))
        for key in self._keys():
            rect = self._rect(key, unit, off)
            pressed = key["id"] in self.lit
            sel = self.editable and key["id"] == self.selected_id
            combo = len(key["inputs"]) > 1
            radius = rect.height() / 2 if key.get("shape") == "round" else 10
            fill = theme.c("lit") if pressed else (theme.c("ochre") if sel else theme.c("panel"))
            seed = theme.seed_of(key["id"])
            if combo and not (pressed or sel):
                # 組合鍵：虛線外框
                p.setPen(QPen(theme.c("line"), 1.6, Qt.DashLine))
                p.setBrush(fill)
                p.drawRoundedRect(rect, radius, radius)
            else:
                theme.draw_ink_rect(p, rect, radius, fill, 3 if sel or pressed else 2.0, seed,
                                    (0, 0) if pressed else (2, 2))
            title = key_title(key)
            sub = self.action_label(key.get("action", "")) if key.get("action") else ""
            p.setPen(theme.c("accent_text") if pressed else theme.c("text"))
            arrow = title in INPUT_NAMES.values()
            p.setFont(theme.font(16 if arrow else (10.5 if len(title) <= 3 else 8), True))
            if sub:
                p.drawText(rect.adjusted(2, 0, -2, -rect.height() * 0.42), Qt.AlignHCenter | Qt.AlignBottom, title)
                p.setFont(theme.font(8))
                p.setPen(theme.c("accent_text") if pressed else theme.c("muted"))
                p.drawText(rect.adjusted(2, rect.height() * 0.55, -2, 0), Qt.AlignHCenter | Qt.AlignTop, sub)
            else:
                p.drawText(rect, Qt.AlignCenter | Qt.TextWordWrap, title)
        if not self.layout_keys:
            p.setPen(theme.c("muted"))
            p.drawText(self.rect(), Qt.AlignCenter, "還沒有按鍵，按「新增按鍵」加入")
        p.end()

    # ---- 編輯 ----
    def _key_at(self, pos):
        unit, off = self._scale_offset()
        for key in reversed(self.layout_keys):
            if self._rect(key, unit, off).contains(pos):
                return key
        return None

    def mousePressEvent(self, e):
        if not self.editable:
            return
        key = self._key_at(e.position())
        self.selected_id = key["id"] if key else ""
        self.selected.emit(self.selected_id)
        if key:
            self._drag = (key, e.position(), key["x"], key["y"])
        self.update()

    def mouseMoveEvent(self, e):
        if not self._drag:
            return
        key, start, x0, y0 = self._drag
        d = e.position() - start
        nx = round((x0 + d.x() / EDIT_UNIT) / SNAP) * SNAP
        ny = round((y0 + d.y() / EDIT_UNIT) / SNAP) * SNAP
        key["x"] = round(min(max(0, nx), EDIT_COLS - KEY), 2)
        key["y"] = round(min(max(0, ny), EDIT_ROWS - KEY), 2)
        self.update()

    def mouseReleaseEvent(self, _):
        if self._drag:
            self._drag = None
            self._recenter()
            self.update()
            self.moved.emit()

    def free_spot(self) -> tuple[float, float]:
        """找一個沒被佔用的位置放新按鍵。"""
        y = 0.0
        while y <= EDIT_ROWS - KEY:
            x = 0.0
            while x <= EDIT_COLS - KEY:
                if all(abs(k["x"] - x) >= KEY or abs(k["y"] - y) >= KEY for k in self.layout_keys):
                    return x, y
                x += 0.5
            y += 0.5
        return 0.0, 0.0
