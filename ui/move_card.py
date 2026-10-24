"""指令表的招式卡片：一招一格，派生招式內縮排在原招式下面（跟官網一樣）。"""
from __future__ import annotations

from PySide6.QtCore import QPointF, QRectF, QSize, Qt, Signal
from PySide6.QtGui import QColor, QFontMetrics, QPainter
from PySide6.QtWidgets import QPushButton, QSizePolicy, QWidget

from core.notation import parse
from ui import theme
from ui.trial_view import ICON

# 官網右下角的槽消耗圖示 → 卡片上的標籤
GAUGE_CODES = ["", "d05", "d1", "d2", "d3", "dd", "s1", "s2", "s3"]
INDENT = 44


def gauge_label(code: str, cat_key: str = "") -> str:
    if not code or code == "0":
        return ""
    if code.startswith("s"):
        return f"SA {code[1:]} 格"
    if code == "dd":
        return "動力槽持續消耗"
    amount = "0.5" if code == "d05" else code[1:]
    # 必殺技的槽消耗是 OD 版本用的
    return f"{'OD ' if cat_key == 'special' else ''}動力槽 {amount} 格"


class MoveCard(QWidget):
    clicked = Signal(object)            # path
    double_clicked = Signal(object)
    menu_requested = Signal(object, object)   # path, 全域座標
    add_clicked = Signal(object)

    PAD = 14

    def __init__(self, move: dict, path: tuple, cat_key: str, renderer, child: bool = False,
                 last: bool = True, parent=None):
        super().__init__(parent)
        self.move, self.path, self.cat_key = move, path, cat_key
        self.renderer = renderer        # TrialView：借用它畫指令圖示（跟著按鍵顯示設定）
        self.child = child
        self.last = last                # 最後一個派生招式：連接線畫到一半就停
        self.selected = False
        self._hover = False
        self.setMouseTracking(True)
        self.setCursor(Qt.PointingHandCursor)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.setAccessibleName(f"招式：{move.get('name', '')}")
        self.setToolTip(f"記法：{move.get('input', '')}　點兩下加入目前連段，右鍵可以修改")
        self.add_btn = QPushButton("加入連段", self)
        self.add_btn.setFocusPolicy(Qt.NoFocus)
        self.add_btn.clicked.connect(lambda: self.add_clicked.emit(self.path))
        self._items = []
        self.refresh()

    def refresh(self):
        combo = parse(self.move.get("input", ""))
        self._items = []
        self.add_btn.setVisible(bool(self.move.get("input")))    # 自動發動的招式沒有指令，不能加入連段
        for gi, g in enumerate(combo.groups):
            if gi:
                self._items.append((("sep", "▶"), 16, 0))
            self._items += self.renderer.group_items(g)
        self.updateGeometry()
        self.update()

    def set_selected(self, on: bool):
        self.selected = on
        self.update()

    # ---- 大小 ----
    def _text_lines(self) -> list[tuple[str, object, str]]:
        out = []
        if self.move.get("note"):
            out.append((self.move["note"], theme.font(9.5), "muted"))
        if self.move.get("desc"):
            out.append((self.move["desc"], theme.font(10), "text"))
        return out

    def _inner_w(self, width: int) -> float:
        return width - (INDENT if self.child else 0) - 2 * self.PAD - 8

    def _btn_w(self) -> int:
        if not self.add_btn.isVisible() and self.add_btn.isHidden():
            return 0
        return self.add_btn.sizeHint().width() + 10

    def _text_h(self, width: float) -> int:
        h = 0
        for text, f, _ in self._text_lines():
            fm = QFontMetrics(f)
            h += fm.boundingRect(0, 0, int(width), 2000, Qt.TextWordWrap, text).height() + 4
        return h

    def heightForWidth(self, w: int) -> int:
        # 名稱 → 指令（整行寬）→ 備註（右邊留給「加入連段」按鈕）
        h = 10 + 34 + ICON + 8
        bottom = max(self._text_h(self._inner_w(w) - self._btn_w()),
                     self.add_btn.sizeHint().height() + 4 if self._btn_w() else 0)
        return int(h + bottom + self.PAD)

    def hasHeightForWidth(self) -> bool:
        return True

    def sizeHint(self) -> QSize:
        return QSize(420, self.heightForWidth(420))

    def resizeEvent(self, e):
        super().resizeEvent(e)
        self.setFixedHeight(self.heightForWidth(self.width()))
        b = self.add_btn
        b.adjustSize()
        b.move(self.width() - b.width() - self.PAD - 6, self.height() - b.height() - 12)

    # ---- 繪圖 ----
    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        left = INDENT if self.child else 0
        r = QRectF(left + 3, 3, self.width() - left - 9, self.height() - 9)
        fill = theme.PALETTE["ochre"] if self.selected else (theme.PALETTE["card_hover"] if self._hover else theme.PALETTE["panel"])
        theme.draw_ink_rect(p, r, 14, fill, 2.4 if not self.child else 2.0,
                            theme.seed_of("move", self.path, int(r.width())))
        if self.child:
            # 派生招式：左邊畫一條墨線接到上面的原招式
            bottom = self.height() / 2 if self.last else self.height() + 4
            theme.draw_ink_line(p, QPointF(18, -4), QPointF(18, bottom), 2.0, 3)
            theme.draw_ink_line(p, QPointF(18, self.height() / 2), QPointF(left + 2, self.height() / 2), 2.0, 4)
        x0, y = r.left() + self.PAD, r.top() + 10
        inner_w = r.width() - 2 * self.PAD

        # 名稱與槽消耗標籤
        label = gauge_label(self.move.get("gauge", ""), self.cat_key)
        badge_w = 0
        if label:
            fb = theme.font(9, True)
            badge_w = QFontMetrics(fb).horizontalAdvance(label) + 20
            br = QRectF(r.right() - self.PAD - badge_w, y + 2, badge_w, 24)
            color = theme.PALETTE["lavender"] if label.startswith("SA") else theme.PALETTE["teal"]
            theme.draw_ink_rect(p, br, 9, color, 1.8, theme.seed_of("badge", label), (1.5, 1.5))
            p.setFont(fb)
            p.setPen(QColor(theme.PALETTE["text"]))
            p.drawText(br, Qt.AlignCenter, label)
        fn = theme.font(13 if not self.child else 12, True)
        p.setFont(fn)
        p.setPen(QColor(theme.PALETTE["text"]))
        name = QFontMetrics(fn).elidedText(self.move.get("name", ""), Qt.ElideRight, int(inner_w - badge_w - 10))
        p.drawText(QRectF(x0, y, inner_w - badge_w, 28), Qt.AlignVCenter | Qt.AlignLeft, name)
        y += 34

        # 指令
        x = x0
        limit = r.right() - self.PAD
        for it, w, _tag in self._items:
            if x + w > limit and it[0] != "note":
                p.setPen(QColor(theme.PALETTE["muted"]))
                p.setFont(theme.font(12, True))
                p.drawText(QRectF(x, y, 20, ICON), Qt.AlignCenter, "…")
                break
            self.renderer._paint_item(p, it, QRectF(x, y, w, ICON))
            x += w + 4
        y += ICON + 8

        # 備註、說明（右下角是「加入連段」按鈕）
        inner_w -= self._btn_w()
        for text, f, color in self._text_lines():
            p.setFont(f)
            p.setPen(QColor(theme.PALETTE[color]))
            fm = QFontMetrics(f)
            box = fm.boundingRect(0, 0, int(inner_w), 2000, Qt.TextWordWrap, text)
            p.drawText(QRectF(x0, y, inner_w, box.height() + 2), Qt.TextWordWrap | Qt.AlignLeft, text)
            y += box.height() + 4
        p.end()

    # ---- 滑鼠 ----
    def enterEvent(self, _):
        self._hover = True
        self.update()

    def leaveEvent(self, _):
        self._hover = False
        self.update()

    def mousePressEvent(self, e):
        if e.button() == Qt.LeftButton:
            self.clicked.emit(self.path)
        super().mousePressEvent(e)

    def mouseDoubleClickEvent(self, e):
        if e.button() == Qt.LeftButton:
            self.double_clicked.emit(self.path)

    def contextMenuEvent(self, e):
        self.clicked.emit(self.path)
        self.menu_requested.emit(self.path, e.globalPos())
