"""訓練場「連段挑戰」樣式的連段顯示（手繪版）：完成的列塗紫色麥克筆，目前這列畫毛筆框。"""
from __future__ import annotations

import math

from PySide6.QtCore import QEasingCurve, QPointF, QRectF, QSize, Qt, QTimer, QVariantAnimation, Signal
from PySide6.QtGui import QColor, QFontMetrics, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import QSizePolicy, QWidget

from core.notation import ARROWS, Combo, display_dir
from ui import theme

ROW_H, ROW_GAP, ICON = 48, 6, 30
HEAD_H = 46
FOOT_H = 40          # 浮動視窗：進度、傷害、槽固定在最下面這一條
SUCCESS_SPACE = ROW_H + ROW_GAP     # 最後一列下面留給「Success」紙卡的空間
PAD_X = 14

BTN_TEXT = QColor("#1E1E1E")     # 按鍵圖示是淺色底，字固定用深色（深色主題也一樣）

XBOX_COLORS = {"A": "#9CCB8C", "B": "#E78C7E", "X": "#93B8E6", "Y": "#EBC56E"}
PUNCH_COLOR, KICK_COLOR, OTHER_COLOR = "#EBC56E", "#93D5C9", "#D6D4CC"
SYSTEM_NAMES = {"DR": "DR", "PDR": "PDR", "DI": "DI", "DP": "DP",
                "SA1": "SA1", "SA2": "SA2", "SA3": "SA3", "CA": "CA"}


def _button_color(code: str) -> str:
    if code.endswith("P"):
        return PUNCH_COLOR
    if code.endswith("K"):
        return KICK_COLOR
    return OTHER_COLOR


class TrialView(QWidget):
    row_clicked = Signal(int)        # 點了第幾列（編輯用）
    add_clicked = Signal()           # 按了「＋ 新增動作」
    row_moved = Signal(int, int)     # 拖動排序：第幾列搬到第幾列
    DRAG_START = 8

    def __init__(self, parent=None):
        super().__init__(parent)
        self.selected_row = -1
        self.combo = Combo()
        self.names: list[str] = []
        self.facing_right = True
        self.style = "pad"
        self.layout_keys: list[dict] = []
        self.title = ""
        self.title_x = 16          # 標題左邊可以讓位給按鈕（連段頁的開始／停止練習）
        self.stats = ("", "", "")      # 傷害、動力槽、氣槽（已轉成文字）
        self.practice: dict | None = None   # 練習中：done, partial, gaps, error, finished
        self._rows: list[list[tuple]] = []
        self._content_w = 400
        # 精簡模式（浮動視窗用）：只畫目前這列附近幾列，整體縮放到放得下
        self.compact = False
        self.zoom = 1.0
        self.rows_shown = 0          # 0＝全部
        self._offset = 0.0           # 精簡模式往上捲了多少（像歌詞字幕，目前這列保持在中間）
        self._follow_row = -1
        self._anim = QVariantAnimation(self)
        self._anim.setDuration(380)
        self._anim.setEasingCurve(QEasingCurve.OutCubic)
        self._anim.valueChanged.connect(self._set_offset)
        # 連段頁可以編輯：最後有「＋ 新增動作」、列可以拖動排序（其他地方的預覽不行）
        self.editable = False
        self.pending_new = False      # 正在新增一列（還沒輸入內容）：畫一格虛線
        self._press = None            # (列, 按下的 y)
        self._drag_row = -1           # 正在拖的列（原本的編號）
        self._drag_y = 0.0
        self._drag_grab = 0.0
        self._order: list[int] = []   # 拖動中畫面上的順序
        self._lift = 0.0
        self._wiggle = 0.0
        self._lift_anim = QVariantAnimation(self)
        self._lift_anim.setDuration(140)
        self._lift_anim.valueChanged.connect(self._set_lift)
        self._wiggle_timer = QTimer(self)
        self._wiggle_timer.setInterval(30)
        self._wiggle_timer.timeout.connect(self._tick_wiggle)
        self.setMouseTracking(True)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.setAccessibleName("連段顯示")

    # ---- 設定 ----
    def set_combo(self, combo: Combo, names: list[str], title: str = ""):
        self.combo, self.names, self.title = combo, names, title
        self._relayout()
        self.follow(self.row_of_step(self.practice["done"]) if self.practice else 0, animate=False)

    def set_stats(self, damage: str, drive: str, sa: str):
        self.stats = (damage, drive, sa)
        self.update()

    def set_facing(self, facing_right: bool):
        self.facing_right = facing_right
        self._relayout()

    def set_button_style(self, style: str, layout_keys: list[dict]):
        self.style, self.layout_keys = style, layout_keys
        self._relayout()

    def set_compact(self, zoom: float, rows: int):
        self.compact, self.zoom, self.rows_shown = True, zoom, rows
        self._relayout()

    def _scale(self) -> float:
        if not self.compact:
            return 1.0
        # 字的大小就照選的來；拉視窗大小不會改變字的大小，只會看到比較多或比較少
        return max(0.3, self.zoom)

    def _title_rect_h(self, width: float) -> float:
        """浮動視窗：標題太長就換行，算出要幾行高。"""
        fm = QFontMetrics(theme.font(13, True))
        room = max(40, int(width - self.title_x - 20))
        h = fm.boundingRect(0, 0, room, 100000, Qt.TextWordWrap,
                            self.title or "連段").height()
        return h

    def _head_h(self, width: float | None = None) -> float:
        """標題區高度；浮動視窗的標題會換行，所以跟著變高。"""
        if not self.compact:
            return HEAD_H
        if width is None:
            width = self.width() / self._scale()
        return max(HEAD_H, self._title_rect_h(width) + 20)

    def _foot_h(self) -> float:
        return FOOT_H if self.compact else 0

    # ---- 置中捲動（精簡模式） ----
    def _set_offset(self, v):
        self._offset = float(v)
        self.update()

    def _target_offset(self, row: int) -> float:
        area = self.height() / self._scale() - self._head_h() - self._foot_h()
        total = len(self._rows) * (ROW_H + ROW_GAP) + (SUCCESS_SPACE if self.practice else 0)
        center = row * (ROW_H + ROW_GAP) + ROW_H / 2
        return max(0.0, min(center - area / 2, total - area))

    def follow(self, row: int, animate: bool = True):
        """讓第 row 列捲到中間。"""
        self._follow_row = row
        if not self.compact:
            return
        target = max(0.0, self._target_offset(row))
        self._anim.stop()
        if animate and self.isVisible() and abs(target - self._offset) > 0.5:
            self._anim.setStartValue(self._offset)
            self._anim.setEndValue(target)
            self._anim.start()
        else:
            self._set_offset(target)

    def scroll_by(self, dy: float):
        """手動捲動（滾輪）。"""
        if not self.compact:
            return
        area = self.height() / self._scale() - self._head_h() - self._foot_h()
        total = len(self._rows) * (ROW_H + ROW_GAP)
        self._anim.stop()
        self._set_offset(max(0.0, min(self._offset + dy, max(0.0, total - area))))

    def resizeEvent(self, e):
        super().resizeEvent(e)
        if self.compact:
            self.follow(max(0, self._follow_row), animate=False)

    def set_selected(self, row: int):
        self.selected_row = row
        self.update()

    def _slot_at(self, y: float) -> int:
        """畫面 y 落在第幾格（-1＝標題區）。"""
        y -= HEAD_H
        return int(y // (ROW_H + ROW_GAP)) if y >= 0 else -1

    def add_slot(self) -> int:
        """「＋ 新增動作」在第幾格：最後一列下面；練習時讓出 Success 的位置。"""
        return len(self._rows) + (1 if self.pending_new else 0) + (1 if self.practice else 0)

    def mousePressEvent(self, e):
        if self.compact or e.button() != Qt.LeftButton:
            return super().mousePressEvent(e)
        y = e.position().y()
        slot = self._slot_at(y)
        inside = slot >= 0 and (y - HEAD_H) - slot * (ROW_H + ROW_GAP) <= ROW_H
        if self.editable and inside and slot == self.add_slot():
            self.add_clicked.emit()
            return
        if 0 <= slot < len(self._rows) and inside:
            self._press = (slot, y)

    def mouseMoveEvent(self, e):
        y = e.position().y()
        if self._press and self._drag_row < 0 and self.editable and len(self._rows) > 1 \
                and abs(y - self._press[1]) >= self.DRAG_START:
            row = self._press[0]
            self._drag_row = row
            self._drag_grab = self._press[1] - (HEAD_H + row * (ROW_H + ROW_GAP))
            self._order = list(range(len(self._rows)))
            self._wiggle_timer.start()
            self._animate_lift(1.0, QEasingCurve.OutBack)
        if self._drag_row < 0:
            return super().mouseMoveEvent(e)
        self._drag_y = y
        # 被拖的那一列跟著滑鼠；中心經過哪一格就換到哪一格，其他列讓位
        center = y - self._drag_grab + ROW_H / 2
        target = max(0, min(len(self._rows) - 1, self._slot_at(center)))
        cur = self._order.index(self._drag_row)
        if target != cur:
            self._order.pop(cur)
            self._order.insert(target, self._drag_row)
        self.update()

    def mouseReleaseEvent(self, e):
        press, self._press = self._press, None
        if self._drag_row >= 0:
            src, dst = self._drag_row, self._order.index(self._drag_row)
            self._drag_row = -1
            self._wiggle_timer.stop()
            self._lift = 0.0
            self._order = []
            self.update()
            if src != dst:
                self.row_moved.emit(src, dst)
            return
        if press and e.button() == Qt.LeftButton:
            self.row_clicked.emit(press[0])

    def _animate_lift(self, end: float, curve):
        self._lift_anim.stop()
        self._lift_anim.setEasingCurve(curve)
        self._lift_anim.setStartValue(self._lift)
        self._lift_anim.setEndValue(end)
        self._lift_anim.start()

    def _set_lift(self, v):
        self._lift = float(v)
        self.update()

    def _tick_wiggle(self):
        self._wiggle += 0.5
        self.update()

    def set_practice(self, state: dict | None):
        """練習狀態；None＝一般預覽。"""
        if state != self.practice:
            self.practice = state
            row = self.practice_row(state) if state else 0
            if row != self._follow_row:
                self.follow(row)
            self.update()

    # ---- 按鍵圖示 ----
    def _pad_glyph(self, code: str) -> tuple[str, str] | None:
        """找使用者版面上對應這個動作的按鍵，回傳 (顯示文字, 顏色)。單一訊號的鍵優先。"""
        keys = [k for k in self.layout_keys if k.get("action") == code]
        if not keys:
            return None
        keys.sort(key=lambda k: len(k["inputs"]))
        key = keys[0]
        if key.get("label"):
            return key["label"], _button_color(code)
        name = "+".join(key["inputs"])
        return name, XBOX_COLORS.get(name, OTHER_COLOR)

    def _button_items(self, buttons: tuple) -> list[tuple]:
        """同時按的幾顆鍵畫成一顆，字連著寫（例：MKHK），不拆成 MK + HK。"""
        texts, colors = [], []
        for code in buttons:
            # PP／KK＝任兩顆，畫成一顆寫著 PP／KK 的鍵
            glyph = None
            if code not in ("PP", "KK", "PPP", "KKK") and self.style == "pad" and len(code) == 2:
                glyph = self._pad_glyph(code)
            if glyph:
                texts.append(glyph[0])
                colors.append(glyph[1])
            else:
                texts.append(code)
                colors.append(_button_color(code[0] if code in ("PP", "KK", "PPP", "KKK") else code))
        color = colors[0] if len(set(colors)) == 1 else OTHER_COLOR
        text = "".join(texts)
        return [("btn", text, color, len(texts) == 1 and text in XBOX_COLORS)]

    def group_items(self, group, first_step: int = 0, fm_icon=None, fm_name=None) -> list[tuple]:
        """一列（一組）要畫的圖示：[(圖示, 寬度, 第幾步)]。指令表的招式卡片也用這個畫指令。"""
        fm_icon = fm_icon or QFontMetrics(theme.font(10.5, True))
        fm_name = fm_name or QFontMetrics(theme.font(10))
        step_no = first_step
        items: list[tuple] = []
        tags: list[int] = []
        for si, step in enumerate(group.steps):
            if si:
                items.append(("sep", "▶"))
                tags.append(step_no)
            prev_dir = False
            start = len(items)
            for t in step.tokens:
                if t.kind in ("dir", "charge"):
                    items.append(("dir", display_dir(t.value, self.facing_right), t.kind == "charge"))
                    prev_dir = True
                    continue
                if prev_dir and t.kind in ("button", "system"):
                    items.append(("plus", "+"))
                prev_dir = False
                if t.kind == "button":
                    items += self._button_items(t.value)
                elif t.kind == "system":
                    # DI／DP 有設定專用鍵時，顯示那顆鍵（例：RB）
                    glyph = self._pad_glyph(t.value) if self.style == "pad" and t.value in ("DI", "DP") else None
                    if glyph:
                        items.append(("btn", glyph[0], glyph[1], glyph[0] in XBOX_COLORS))
                    else:
                        items.append(("sys", SYSTEM_NAMES.get(t.value, t.value)))
                elif t.kind == "jump":
                    items.append(("sys", "跳"))
                elif t.kind == "note":
                    items.append(("note", t.value))
            tags += [step_no] * (len(items) - start)
            step_no += 1
        sized = []
        for it, tag in zip(items, tags):
            kind = it[0]
            if kind == "dir":
                w = ICON
            elif kind == "btn":
                w = max(ICON, fm_icon.horizontalAdvance(it[1]) + 16)
            elif kind == "sys":
                w = fm_icon.horizontalAdvance(it[1]) + 18
            elif kind == "note":
                w = fm_name.horizontalAdvance(f"（{it[1]}）")
            else:
                w = 16
            sized.append((it, w, tag))
        return sized

    # ---- 排版 ----
    def _relayout(self):
        fm_icon = QFontMetrics(theme.font(10.5, True))
        fm_name = QFontMetrics(theme.font(10))
        self._rows = []
        widest = 360
        step_no = 0
        for gi, group in enumerate(self.combo.groups):
            first_step = step_no
            sized = self.group_items(group, first_step, fm_icon, fm_name)
            step_no += len(group.steps)
            name = self.names[gi] if gi < len(self.names) else ""
            icons_w = sum(w for _, w, _t in sized) + 4 * max(0, len(sized) - 1)
            gaps_w = 70 + 46 * (step_no - first_step)      # 練習時顯示幀數的位置
            row_w = PAD_X + 30 + 12 + icons_w + gaps_w + (24 + fm_name.horizontalAdvance(name) if name else 0) + PAD_X
            widest = max(widest, row_w)
            self._rows.append((sized, name, first_step, step_no))
        if self.compact:
            # 浮動視窗：寬度也要放得下標題和右上角的傷害、槽，不然換字的大小時會疊在一起
            fm_t, fm_c = QFontMetrics(theme.font(13, True)), QFontMetrics(theme.font(10, True))
            damage, drive, sa = self.stats
            chips = ["進度 99 / 99", f"傷害 {damage or '未填'}", f"動力槽 {drive or '0'}", f"SA {sa or '0'}"]
            # 標題會換行，所以寬度只要放得下下面那一排資訊
            foot_w = 16 + sum(fm_c.horizontalAdvance(t) + 34 for t in chips) + 20
            widest = max(widest, foot_w, min(fm_t.horizontalAdvance(self.title or "連段") + self.title_x + 20, 560))
        self._content_w = widest + 16
        if not self.compact:
            self.setMinimumWidth(self._content_w)
            self.setMinimumHeight(self.sizeHint().height())
        self.updateGeometry()
        self.update()

    def row_of_step(self, step: int) -> int:
        for i, (_s, _n, first, end) in enumerate(self._rows):
            if first <= step < end:
                return i
        return max(0, len(self._rows) - 1)

    def practice_row(self, state: dict) -> int:
        """練習中要捲到哪一列；練完了就捲到最後一列下面的「Success」。"""
        return len(self._rows) if state["finished"] else self.row_of_step(state["done"])

    def row_span(self, row: int) -> tuple[int, int]:
        y = HEAD_H + row * (ROW_H + ROW_GAP)
        return y, y + ROW_H

    def sizeHint(self) -> QSize:
        rows = max(1, min(len(self._rows), self.rows_shown or len(self._rows)) if self.compact else len(self._rows))
        z = self.zoom if self.compact else 1.0
        extra = FOOT_H if self.compact else SUCCESS_SPACE
        if self.editable and not self.compact:
            extra += (ROW_H + ROW_GAP) * (1 + (1 if self.pending_new else 0))
        head = self._head_h(self._content_w) if self.compact else HEAD_H
        return QSize(int(self._content_w * z), int((head + rows * (ROW_H + ROW_GAP) + extra + 16) * z))

    # ---- 繪圖 ----
    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        z = self._scale()
        p.scale(z, z)
        width = self.width() / z
        hh = self._head_h(width)
        self._paint_head(p, width)
        if self.compact:
            self._paint_foot(p, width)

        if self.editable and not self.compact:
            self._paint_add(p, width)
        if not self._rows:
            if self.editable:
                p.end()
                return
            p.setPen(theme.c("muted"))
            p.setFont(theme.font(10.5))
            p.drawText(QRectF(0, hh, width, ROW_H), Qt.AlignCenter,
                       "在上面輸入連段，這裡會顯示成訓練場的樣子")
            p.end()
            return

        pr = self.practice
        done_n = pr["done"] if pr else -1
        if self.compact:
            # 精簡模式：標題下面的區域整個往上捲，只畫看得到的列
            p.setClipRect(QRectF(0, hh - 2, width, self.height() / z - hh - FOOT_H + 2))
        offset = self._offset if self.compact else 0.0
        order = self._order if self._drag_row >= 0 else range(len(self._rows))
        lifted = None
        for slot, i in enumerate(order):
            y = hh + slot * (ROW_H + ROW_GAP) - offset
            if i == self._drag_row:
                lifted = i
                continue
            if y + ROW_H < hh - 4 or y > self.height() / z:
                continue
            if self._drag_row >= 0:
                # 拖動中：其他列輕輕抖動
                p.save()
                c = QPointF(width / 2, y + ROW_H / 2)
                p.translate(c)
                p.rotate(0.5 * self._lift * math.sin(self._wiggle + i * 1.7))
                p.translate(-c)
                self._paint_row(p, i, y, width, pr, done_n)
                p.restore()
            else:
                self._paint_row(p, i, y, width, pr, done_n)
        if self.pending_new and not self.compact:
            self._paint_dashed(p, HEAD_H + len(self._rows) * (ROW_H + ROW_GAP), width,
                               "新動作：用控制器打一次，或在右邊輸入")
        if lifted is not None:
            # 被拖的那一列：放大、浮起來、底下有影子，畫在最上面
            k = self._lift
            y = self._drag_y - self._drag_grab
            rect = QRectF(8, y, width - 22, ROW_H)
            p.save()
            p.setPen(Qt.NoPen)
            p.setBrush(QColor(0, 0, 0, int(70 * k)))
            p.drawPath(theme.wobbly_path(rect.translated(6 * k, 10 * k), 16, 1.4, theme.seed_of("lift")))
            theme.draw_ink_rect(p, rect, 16, theme.c("panel"), 3.0, theme.seed_of("liftcard"), (0, 0))
            c = rect.center()
            p.translate(c.x(), c.y())
            p.scale(1 + 0.04 * k, 1 + 0.04 * k)
            p.translate(-c.x(), -c.y())
            self._paint_row(p, lifted, y, width, pr, done_n)
            p.restore()
        if pr and pr.get("success", 0) > 0:
            self._paint_success(p, width, pr["success"], hh)
        elif pr and pr.get("banner"):
            bn = pr["banner"]
            self._paint_success(p, width, bn["left"], hh, bn["text"], bn["color"])
        p.end()

    def _paint_dashed(self, p: QPainter, y: float, width: float, text: str):
        r = QRectF(8, y, width - 22, ROW_H)
        p.save()
        pen = QPen(theme.c("muted"), 2, Qt.DashLine)
        p.setPen(pen)
        p.setBrush(Qt.NoBrush)
        p.drawRoundedRect(r.adjusted(2, 2, -2, -2), 14, 14)
        p.setFont(theme.font(10.5, True))
        p.drawText(r, Qt.AlignCenter, text)
        p.restore()

    def _paint_add(self, p: QPainter, width: float):
        """「＋ 新增動作」：沒有列時在第一列的位置，有列時跟在最後一列下面。"""
        y = HEAD_H + self.add_slot() * (ROW_H + ROW_GAP)
        f = theme.font(10.5, True)
        w = QFontMetrics(f).horizontalAdvance("＋ 新增動作") + 48
        r = QRectF(8 + PAD_X - 4, y + 6, w, ROW_H - 12)
        p.save()
        theme.draw_ink_rect(p, r, r.height() / 2, theme.c("grey"), 2.3, theme.seed_of("add"), (2.5, 2))
        p.setFont(f)
        p.setPen(theme.c("text"))
        p.drawText(r, Qt.AlignCenter, "＋ 新增動作")
        p.restore()

    def _paint_row(self, p: QPainter, i: int, y: float, width: float, pr, done_n: int):
        sized, name, first_step, step_end = self._rows[i]
        if True:
            rect = QRectF(8, y, width - 22, ROW_H)
            done = bool(pr) and done_n >= step_end
            current = bool(pr) and not pr["finished"] and first_step <= done_n < step_end
            if i == self.selected_row and not self.compact:
                # 正在編輯的列：青綠色麥克筆（練習中也亮，完成的紫色會蓋在上面）
                theme.draw_marker(p, rect.adjusted(-2, 6, 0, -6), theme.c("teal"),
                                  theme.seed_of("sel", i, int(rect.width())), alpha=120, strokes=3)
            if done:
                # 完成的列：紫色麥克筆塗過去
                theme.draw_marker(p, rect.adjusted(-2, 4, 0, -4), theme.c("purple"),
                                  theme.seed_of("row", i, int(rect.width())), alpha=150, strokes=4)
            if current:
                bad = pr.get("error")
                theme.draw_ink_rect(p, rect.adjusted(0, 1, 0, -1), 16, None, 2.8 if bad else 2.4,
                                    theme.seed_of("cur", i, int(rect.width())), (0, 0),
                                    theme.c("warn") if bad else None)

            # 編號
            x = rect.left() + PAD_X
            cy = rect.center().y()
            theme.draw_ink_ellipse(p, QRectF(x + 1, cy - 13, 26, 26), None, 2.2, theme.seed_of("n", i))
            p.setFont(theme.font(10.5, True))
            p.setPen(theme.c("text"))
            p.drawText(QRectF(x, cy - 13, 28, 26), Qt.AlignCenter, str(i + 1))
            x += 30 + 12

            dir_seen: dict[int, int] = {}
            for it, w, tag in sized:
                lit = False
                if pr:
                    if tag == done_n and it[0] == "dir":
                        k = dir_seen.get(tag, 0)
                        lit = k < pr.get("partial", 0)
                    if it[0] == "dir":
                        dir_seen[tag] = dir_seen.get(tag, 0) + 1
                self._paint_item(p, it, QRectF(x, cy - ICON / 2, w, ICON), lit)
                x += w + 4

            if pr:
                parts = []
                for s in range(first_step, min(step_end, done_n)):
                    g = pr["gaps"][s] if s < len(pr["gaps"]) else None
                    parts.append("起手" if g is None else f"{g:.0f}F")
                if parts:
                    p.setFont(theme.font(10.5, True))
                    p.setPen(theme.c("gap_text"))
                    gap_rect = QRectF(x + 14, y, 46 * len(parts) + 40, ROW_H)
                    p.drawText(gap_rect, Qt.AlignLeft | Qt.AlignVCenter, " · ".join(parts))

            if name:
                p.setFont(theme.font(10.5))
                p.setPen(theme.c("text"))
                p.drawText(QRectF(x + 16, y, rect.right() - x - 16 - PAD_X, ROW_H),
                           Qt.AlignRight | Qt.AlignVCenter, name)

    def _paint_success(self, p: QPainter, width: float, left: float, hh: float = HEAD_H,
                       text: str = "Success", color: str = "purple"):
        """連段完成：最後一列下方跳出「Success」紙卡，最後 0.25 秒淡出。
        遊戲判定沒過時，同一個位置改顯示「連段斷了」之類的字。"""
        p.save()
        offset = self._offset if self.compact else 0.0
        y = hh + len(self._rows) * (ROW_H + ROW_GAP) - offset
        f = theme.display_font(20)
        w = QFontMetrics(f).horizontalAdvance(text) + 64
        card = QRectF((width - 14) / 2 - w / 2, y, w, ROW_H)
        p.setOpacity(min(1.0, left / 0.25))
        # 墊一張紙卡，字才不會跟紫色的完成列混在一起
        theme.draw_ink_rect(p, card, ROW_H / 3, theme.c("panel"), 2.6, theme.seed_of("success"), (4, 4))
        theme.draw_glow_text(p, card, Qt.AlignCenter, text, f, theme.c(color))
        p.restore()

    def _chips(self) -> list[tuple]:
        damage, drive, sa = self.stats
        chips = [("傷害", damage or "未填", "ochre"), ("動力槽", drive or "0", "teal"),
                 ("SA", sa or "0", "lavender")]
        if self.practice:
            pr = self.practice
            chips.insert(0, ("進度", f"{pr['done']} / {pr['total']}", "grey"))
        return chips

    def _paint_foot(self, p: QPainter, width: float):
        """浮動視窗：進度、傷害、槽固定在最下面一排（右下角說明小字的上方）。"""
        p.save()
        p.setFont(theme.font(10, True))
        fm = QFontMetrics(p.font())
        top = self.height() / self._scale() - FOOT_H
        x = width - 20
        for label, value, color in reversed(self._chips()):
            text = f"{label} {value}"
            w = fm.horizontalAdvance(text) + 24
            x -= w
            r = QRectF(x, top + 6, w, FOOT_H - 12)
            theme.draw_ink_rect(p, r, r.height() / 2, theme.c(color), 2.0, theme.seed_of(label), (2, 2))
            p.setPen(theme.c("text"))
            p.drawText(r, Qt.AlignCenter, text)
            x -= 10
        p.restore()

    def _paint_head(self, p: QPainter, width: float):
        pr = self.practice
        if self.compact:
            # 浮動視窗：標題固定在最上面，太長就換行，一定完整顯示
            room = max(40, width - self.title_x - 20)
            p.setFont(theme.font(13, True))
            p.setPen(theme.c("text"))
            p.drawText(QRectF(self.title_x, 10, room, self._head_h(width) - 20),
                       Qt.AlignLeft | Qt.AlignVCenter | Qt.TextWordWrap,
                       self.title or "連段")
            return
        # 右上角的傷害、槽先畫，標題放剩下的空間，太長就用「…」，不會疊在一起
        damage, drive, sa = self.stats
        chips = [("傷害", damage or "未填", "ochre"), ("動力槽", drive or "0", "teal"),
                 ("SA", sa or "0", "lavender")]
        if pr:
            chips.insert(0, ("進度", f"{pr['done']} / {pr['total']}", "grey"))
        p.setFont(theme.font(10, True))
        fm = QFontMetrics(p.font())
        x = width - 20
        for label, value, color in reversed(chips):
            text = f"{label} {value}"
            w = fm.horizontalAdvance(text) + 24
            x -= w
            r = QRectF(x, 8, w, HEAD_H - 18)
            theme.draw_ink_rect(p, r, r.height() / 2, theme.c(color), 2.0, theme.seed_of(label), (2, 2))
            p.setPen(theme.c("text"))
            p.drawText(r, Qt.AlignCenter, text)
            x -= 10
        room = x - 6 - self.title_x
        p.setFont(theme.font(13, True))
        p.setPen(theme.c("text"))
        fm = QFontMetrics(p.font())
        title = fm.elidedText(self.title or "連段", Qt.ElideRight, int(max(0, room)))
        p.drawText(QRectF(self.title_x, 0, max(0, room), HEAD_H), Qt.AlignLeft | Qt.AlignVCenter, title)
        if pr and pr["finished"]:
            tx = self.title_x + fm.horizontalAdvance(title) + 28
            done_w = QFontMetrics(theme.display_font(13)).horizontalAdvance("輸入比對完成")
            if tx + done_w <= x - 6:
                theme.draw_glow_text(p, QRectF(tx, 0, done_w + 4, HEAD_H), Qt.AlignLeft | Qt.AlignVCenter,
                                     "輸入比對完成", theme.display_font(13), theme.c("purple"))

    def _paint_item(self, p: QPainter, it: tuple, r: QRectF, lit: bool = False):
        kind = it[0]
        seed = theme.seed_of(kind, int(r.x()), int(r.y()))
        if kind == "dir":
            num, charge = it[1], it[2]
            if lit:
                # 正在輸入的方向：後面塗一團紫色
                theme.draw_ink_ellipse(p, r.adjusted(-3, -3, 3, 3), theme.c("purple"), 0, seed, (0, 0))
            if charge:
                theme.draw_ink_ellipse(p, r.adjusted(1, 1, -1, -1), None, 1.8, seed)
            # 墨線描邊的手繪箭頭，中間留白
            f = theme.font(17, True)
            text = ARROWS[num] if num != 5 else "N"
            fm = QFontMetrics(f)
            path = QPainterPath()
            path.addText(QPointF(r.center().x() - fm.horizontalAdvance(text) / 2,
                                 r.center().y() + (fm.ascent() - fm.descent()) / 2), f, text)
            p.setPen(QPen(theme.c("text"), 3.4, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
            p.setBrush(Qt.NoBrush)
            p.drawPath(path)
            p.setPen(Qt.NoPen)
            p.setBrush(theme.c("text"))
            p.drawPath(path)
        elif kind == "btn":
            text, color, round_ = it[1], it[2], it[3]
            if round_ and r.width() <= ICON + 0.5:
                theme.draw_ink_ellipse(p, r, QColor(color), 2.2, seed, (2, 2))
            else:
                theme.draw_ink_rect(p, r, 9, QColor(color), 2.2, seed, (2, 2))
            p.setPen(BTN_TEXT)
            p.setFont(theme.font(10.5, True))
            p.drawText(r, Qt.AlignCenter, text)
        elif kind == "sys":
            theme.draw_ink_rect(p, r, 7, None, 2.2, seed)
            p.setPen(theme.c("text"))
            p.setFont(theme.font(10.5, True))
            p.drawText(r, Qt.AlignCenter, it[1])
        elif kind == "note":
            p.setPen(theme.c("muted"))
            p.setFont(theme.font(10))
            p.drawText(r, Qt.AlignVCenter | Qt.AlignLeft, f"（{it[1]}）")
        else:
            p.setPen(theme.c("muted") if kind == "sep" else theme.c("text"))
            p.setFont(theme.font(9 if kind == "sep" else 14, True))
            p.drawText(r, Qt.AlignCenter, "›" if kind == "sep" else it[1])
