"""練習紀錄分頁：完成率、最常停在哪一列、最近 14 天、每一次的紀錄和每一步的幀數。"""
from __future__ import annotations

from datetime import datetime, timedelta

from PySide6.QtCore import QRectF, QSize, Qt, Signal
from PySide6.QtGui import QColor, QPainter
from PySide6.QtWidgets import (QAbstractItemView, QComboBox, QHBoxLayout, QHeaderView, QLabel, QPushButton,
                               QSizePolicy, QTreeWidget, QTreeWidgetItem, QVBoxLayout, QWidget)

from core.notation import parse
from data.storage import ComboStore
from ui import theme
from ui.controller_setup import confirm

PAGE = 500
FRAME_S = 1 / 60
RANGES = [("全部時間", 0), ("最近 7 天", 7), ("今天", 1)]


def step_rows(notation: str) -> list[tuple[int, str]]:
    """記法的每一步 → (第幾列, 這一步的文字)。"""
    out = []
    for gi, g in enumerate(parse(notation).groups):
        for s in g.steps:
            out.append((gi, s.text()))
    return out


def frames_used(gaps: list) -> float:
    return sum(g for g in gaps if g)


class StatCard(QWidget):
    """手繪小卡片：上面一行小字標題，下面大字數值，再下面一行補充。"""

    def __init__(self, title: str, parent=None):
        super().__init__(parent)
        self.title, self.value, self.sub = title, "－", ""
        self.setMinimumSize(170, 96)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)

    def set(self, value: str, sub: str = ""):
        self.value, self.sub = value, sub
        self.setToolTip(sub)
        self.update()

    def sizeHint(self) -> QSize:
        return QSize(200, 100)

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        r = QRectF(self.rect()).adjusted(3, 3, -6, -6)
        theme.draw_ink_rect(p, r, 14, theme.PALETTE["panel"], 2.2, theme.seed_of("card", self.title))
        inner = r.adjusted(14, 8, -12, -8)
        p.setPen(QColor(theme.PALETTE["muted"]))
        p.setFont(theme.font(9.5))
        p.drawText(inner, Qt.AlignTop | Qt.AlignLeft, self.title)
        p.setPen(QColor(theme.PALETTE["text"]))
        f = theme.font(17, True)
        p.setFont(f)
        p.drawText(inner.adjusted(0, 20, 0, 0), Qt.AlignTop | Qt.AlignLeft,
                   p.fontMetrics().elidedText(self.value, Qt.ElideRight, int(inner.width())))
        p.setPen(QColor(theme.PALETTE["muted"]))
        p.setFont(theme.font(9))
        p.drawText(inner, Qt.AlignBottom | Qt.AlignLeft,
                   p.fontMetrics().elidedText(self.sub, Qt.ElideRight, int(inner.width())))
        p.end()


class DailyChart(QWidget):
    """最近 14 天：紙色長條是練習次數，紫色是完成次數。"""

    DAYS = 14

    def __init__(self, parent=None):
        super().__init__(parent)
        self.days: list[tuple[str, int, int]] = []
        self.setMinimumHeight(150)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.setAccessibleName("最近 14 天練習次數圖")

    def sizeHint(self) -> QSize:
        return QSize(600, 160)

    def set_records(self, records: list[dict]):
        today = datetime.now().date()
        keys = [today - timedelta(days=i) for i in range(self.DAYS - 1, -1, -1)]
        tries = {k: 0 for k in keys}
        clears = {k: 0 for k in keys}
        for r in records:
            d = datetime.fromtimestamp(r["started"]).date()
            if d in tries:
                tries[d] += 1
                clears[d] += r["completed_steps"] >= r["total_steps"]
        self.days = [(f"{k.month}/{k.day}", tries[k], clears[k]) for k in keys]
        self.setToolTip("\n".join(f"{d}：練 {t} 次、完成 {c} 次" for d, t, c in self.days if t))
        self.update()

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        r = QRectF(self.rect()).adjusted(3, 3, -6, -6)
        theme.draw_ink_rect(p, r, 14, theme.PALETTE["panel"], 2.2, theme.seed_of("chart", int(r.width())))
        inner = r.adjusted(16, 30, -16, -26)
        p.setFont(theme.font(9.5))
        p.setPen(QColor(theme.PALETTE["muted"]))
        p.drawText(r.adjusted(16, 8, -16, 0), Qt.AlignTop | Qt.AlignLeft, "最近 14 天")
        legend = r.adjusted(16, 8, -16, 0)
        p.drawText(legend, Qt.AlignTop | Qt.AlignRight, "練習次數　　完成次數")
        fm = p.fontMetrics()
        lx = legend.right() - fm.horizontalAdvance("練習次數　　完成次數")
        theme.draw_ink_rect(p, QRectF(lx - 18, legend.top() + 3, 12, 12), 3, theme.PALETTE["grey"], 1.2, 3, (1, 1))
        lx2 = legend.right() - fm.horizontalAdvance("完成次數")
        p.save()
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(theme.PALETTE["accent2"]))
        p.drawRoundedRect(QRectF(lx2 - 16, legend.top() + 4, 11, 11), 3, 3)
        p.restore()
        if not self.days:
            p.end()
            return
        top = max(1, max(t for _, t, _ in self.days))
        slot = inner.width() / len(self.days)
        bw = min(26.0, slot * 0.6)
        for i, (label, t, c) in enumerate(self.days):
            cx = inner.left() + slot * (i + 0.5)
            if t:
                h = max(6.0, inner.height() * t / top)
                bar = QRectF(cx - bw / 2, inner.bottom() - h, bw, h)
                theme.draw_ink_rect(p, bar, 4, theme.PALETTE["grey"], 1.4, theme.seed_of("bar", i), (1.5, 1))
                if c:
                    ch = max(4.0, inner.height() * c / top)
                    p.save()
                    p.setPen(Qt.NoPen)
                    p.setBrush(QColor(theme.PALETTE["accent2"]))
                    p.drawRoundedRect(QRectF(cx - bw / 2 + 4, inner.bottom() - ch + 2, bw - 8, ch - 4), 3, 3)
                    p.restore()
                p.setPen(QColor(theme.PALETTE["text"]))
                p.setFont(theme.font(8.5))
                p.drawText(QRectF(cx - slot / 2, inner.bottom() - h - 18, slot, 16), Qt.AlignCenter, str(t))
            p.setPen(QColor(theme.PALETTE["muted"]))
            p.setFont(theme.font(8.5))
            p.drawText(QRectF(cx - slot / 2, inner.bottom() + 4, slot, 18), Qt.AlignCenter, label)
        theme.draw_ink_line(p, inner.bottomLeft(), inner.bottomRight(), 1.6, 5)
        p.end()


class RecordsPage(QWidget):
    open_combo = Signal(int)

    def __init__(self, store: ComboStore, parent=None):
        super().__init__(parent)
        self.store = store
        self.character = ""
        self.char_name = ""
        self._shown = 0
        self._build()

    def _build(self):
        outer = QVBoxLayout(self)
        outer.setContentsMargins(20, 12, 20, 16)
        outer.setSpacing(10)

        top = QHBoxLayout()
        top.addWidget(QLabel("連段"))
        self.combo_box = QComboBox()
        self.combo_box.setAccessibleName("要看哪個連段的紀錄")
        self.combo_box.setMinimumWidth(260)
        self.combo_box.currentIndexChanged.connect(lambda _i: self.refresh())
        top.addWidget(self.combo_box)
        top.addSpacing(12)
        top.addWidget(QLabel("時間"))
        self.range_box = QComboBox()
        self.range_box.setAccessibleName("時間範圍")
        for name, days in RANGES:
            self.range_box.addItem(name, days)
        self.range_box.currentIndexChanged.connect(lambda _i: self.refresh())
        top.addWidget(self.range_box)
        top.addStretch(1)
        outer.addLayout(top)

        cards = QHBoxLayout()
        cards.setSpacing(10)
        self.c_tries = StatCard("練習次數")
        self.c_rate = StatCard("完成率")
        self.c_stop = StatCard("最常停在")
        self.c_speed = StatCard("完成時平均每一步")
        for c in (self.c_tries, self.c_rate, self.c_stop, self.c_speed):
            cards.addWidget(c)
        outer.addLayout(cards)

        self.chart = DailyChart()
        outer.addWidget(self.chart)

        body = QHBoxLayout()
        body.setSpacing(12)
        left = QVBoxLayout()
        self.tree = QTreeWidget()
        self.tree.setAccessibleName("練習紀錄清單")
        self.tree.setHeaderLabels(["時間", "連段", "結果", "嚴格", "用時"])
        self.tree.setRootIsDecorated(False)
        self.tree.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.tree.setUniformRowHeights(True)
        header = self.tree.header()
        header.setSectionResizeMode(0, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(1, QHeaderView.Stretch)
        for i in (2, 3, 4):
            header.setSectionResizeMode(i, QHeaderView.ResizeToContents)
        self.tree.itemSelectionChanged.connect(self._selection_changed)
        self.tree.itemDoubleClicked.connect(self._open_item)
        left.addWidget(self.tree, 1)
        self.more_btn = QPushButton("載入更早的紀錄")
        self.more_btn.clicked.connect(self._load_more)
        left.addWidget(self.more_btn)
        btns = QHBoxLayout()
        self.del_btn = QPushButton("刪除選取的紀錄")
        self.del_btn.clicked.connect(self._delete_selected)
        self.clear_btn = QPushButton("清空列出的紀錄")
        self.clear_btn.clicked.connect(self._clear_all)
        btns.addWidget(self.del_btn)
        btns.addWidget(self.clear_btn)
        btns.addStretch(1)
        hint = QLabel("按住 Ctrl 或 Shift 可以選好幾筆；點兩下跳到那個連段")
        hint.setObjectName("muted")
        btns.addWidget(hint)
        left.addLayout(btns)
        body.addLayout(left, 3)

        right = theme.GlassPanel()
        right.setMinimumWidth(340)
        rbox = QVBoxLayout(right)
        rbox.setContentsMargins(16, 12, 18, 16)
        self.detail_title = theme.GlowTitle("這一次的每一步", 11)
        rbox.addWidget(self.detail_title)
        self.detail_info = QLabel("點左邊一筆紀錄，這裡會列出每一步距離上一步幾幀。")
        self.detail_info.setObjectName("muted")
        self.detail_info.setWordWrap(True)
        rbox.addWidget(self.detail_info)
        self.steps = QTreeWidget()
        self.steps.setAccessibleName("每一步的幀數")
        self.steps.setHeaderLabels(["列", "動作", "距上一步"])
        self.steps.setRootIsDecorated(False)
        self.steps.setSelectionMode(QAbstractItemView.NoSelection)
        sh = self.steps.header()
        sh.setSectionResizeMode(0, QHeaderView.ResizeToContents)
        sh.setSectionResizeMode(1, QHeaderView.Stretch)
        sh.setSectionResizeMode(2, QHeaderView.ResizeToContents)
        rbox.addWidget(self.steps, 1)
        body.addWidget(right, 2)
        outer.addLayout(body, 1)
        self._selection_changed()

    # ---- 外部呼叫 ----
    def set_character(self, cid: str, name: str):
        self.character, self.char_name = cid, name
        self._fill_combos()
        self.refresh()

    def show_combo(self, combo_id: int):
        self._fill_combos()
        idx = self.combo_box.findData(combo_id)
        self.combo_box.setCurrentIndex(max(0, idx))
        self.refresh()

    def _fill_combos(self):
        keep = self.combo_box.currentData()
        self.combo_box.blockSignals(True)
        self.combo_box.clear()
        self.combo_box.addItem(f"{self.char_name}的全部連段", 0)
        for row in self.store.list(self.character):
            self.combo_box.addItem(row["name"] or "（沒有名稱）", row["id"])
        if self.store.has_orphan_practice():
            self.combo_box.addItem("（已刪除的連段）", -1)
        idx = self.combo_box.findData(keep)
        self.combo_box.setCurrentIndex(max(0, idx))
        self.combo_box.blockSignals(False)

    # ---- 讀取與統計 ----
    def _filter(self) -> tuple[int, float]:
        days = self.range_box.currentData() or 0
        since = 0.0
        if days:
            start = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0) - timedelta(days=days - 1)
            since = start.timestamp()
        return int(self.combo_box.currentData() or 0), since

    def refresh(self):
        if not self.character:
            return
        combo_id, since = self._filter()
        records = self.store.list_practice(self.character, combo_id, since, limit=100000)
        self._stats(records, combo_id)
        start14 = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0) - timedelta(days=13)
        self.chart.set_records(self.store.list_practice(self.character, combo_id, start14.timestamp(),
                                                        limit=100000))
        self.tree.clear()
        self._shown = 0
        self._total = len(records)
        self._load_more()

    def _load_more(self):
        combo_id, since = self._filter()
        rows = self.store.list_practice(self.character, combo_id, since, limit=PAGE, offset=self._shown)
        for r in rows:
            done = r["completed_steps"] >= r["total_steps"]
            stop_row = ""
            if not done:
                mapping = step_rows(r["notation"])
                k = r["completed_steps"]
                n_rows = (mapping[-1][0] + 1) if mapping else 0
                stop_row = f"停在第 {mapping[k][0] + 1}／{n_rows} 列" if k < len(mapping) else \
                    f"停在第 {k + 1}／{r['total_steps']} 步"
            item = QTreeWidgetItem([
                datetime.fromtimestamp(r["started"]).strftime("%m/%d %H:%M"),
                r["combo_name"] or "（已刪除的連段）",
                "完成" if done else stop_row,
                "是" if r["strict"] else "",
                f"{frames_used(r['gaps']) * FRAME_S:.1f} 秒"])
            item.setData(0, Qt.UserRole, r)
            if done:
                item.setForeground(2, QColor(theme.PALETTE["ok"]))
            self.tree.addTopLevelItem(item)
        self._shown += len(rows)
        self.more_btn.setVisible(self._shown < self._total)
        self.clear_btn.setEnabled(self._total > 0)
        if not self._total:
            self.detail_info.setText("這裡還沒有練習紀錄。到「連段庫」按「開始練習」，每練一次就會記一筆。")

    def _stats(self, records: list[dict], combo_id: int):
        n = len(records)
        clears = [r for r in records if r["completed_steps"] >= r["total_steps"]]
        self.c_tries.set(f"{n} 次", f"完成 {len(clears)} 次" if n else "還沒有紀錄")
        self.c_rate.set(f"{round(len(clears) * 100 / n)}%" if n else "－",
                        f"{len(clears)}／{n}" if n else "")

        if combo_id and combo_id != -1:
            self.c_stop.title = "最常停在"
            stops: dict[int, int] = {}
            texts: dict[int, str] = {}
            for r in records:
                if r["completed_steps"] >= r["total_steps"]:
                    continue
                mapping = step_rows(r["notation"])
                k = r["completed_steps"]
                if k < len(mapping):
                    row = mapping[k][0]
                    stops[row] = stops.get(row, 0) + 1
                    groups = parse(r["notation"]).groups
                    texts[row] = groups[row].text() if row < len(groups) else ""
            if stops:
                row, cnt = max(stops.items(), key=lambda kv: (kv[1], -kv[0]))
                self.c_stop.set(f"第 {row + 1} 列：{cnt} 次", texts.get(row, ""))
            else:
                self.c_stop.set("－", "沒有中途停下的紀錄" if n else "")
        else:
            # 全部連段時，列號沒有意義，改顯示完成率最低的連段
            self.c_stop.title = "最難的連段（練 3 次以上）"
            per: dict[str, list[int]] = {}
            for r in records:
                key = r["combo_name"] or "（已刪除的連段）"
                s = per.setdefault(key, [0, 0])
                s[0] += 1
                s[1] += r["completed_steps"] >= r["total_steps"]
            cands = [(c / t, -t, name, t, c) for name, (t, c) in per.items() if t >= 3]
            if cands:
                _rate, _t, name, t, c = min(cands)
                self.c_stop.set(name, f"完成 {c}／{t} 次（{round(c * 100 / t)}%）")
            else:
                self.c_stop.set("－", "練 3 次以上才會比較")

        gaps = [g for r in clears for g in r["gaps"][1:] if g is not None]
        if gaps:
            best = min(clears, key=lambda r: frames_used(r["gaps"]))
            bf = frames_used(best["gaps"])
            self.c_speed.set(f"{sum(gaps) / len(gaps):.1f} 幀",
                             f"最快一次：{bf:.0f} 幀（{bf * FRAME_S:.1f} 秒）")
        else:
            self.c_speed.set("－", "完成過才會計算")

    # ---- 選取與細節 ----
    def _selected(self) -> list[dict]:
        return [it.data(0, Qt.UserRole) for it in self.tree.selectedItems()]

    def _selection_changed(self):
        sel = self._selected()
        self.del_btn.setEnabled(bool(sel))
        self.steps.clear()
        if len(sel) != 1:
            if len(sel) > 1:
                self.detail_info.setText(f"選了 {len(sel)} 筆；只選一筆時會列出每一步。")
            elif self.tree.topLevelItemCount():
                self.detail_info.setText("點左邊一筆紀錄，這裡會列出每一步距離上一步幾幀。")
            return
        r = sel[0]
        mapping = step_rows(r["notation"])
        done = r["completed_steps"] >= r["total_steps"]
        gaps = r["gaps"]
        real = [(i, g) for i, g in enumerate(gaps) if i > 0 and g]
        slow = max(real, key=lambda x: x[1])[0] if real else -1
        when = datetime.fromtimestamp(r["started"]).strftime("%Y/%m/%d %H:%M")
        self.detail_info.setText(f"{when}　{r['combo_name'] or '（已刪除的連段）'}　"
                                 f"{'完成' if done else '沒有完成'}"
                                 + ("　最慢的一步標成紅色" if slow >= 0 else ""))
        last_row = -1
        for i, (row, text) in enumerate(mapping):
            if i > r["completed_steps"]:
                break
            if i == r["completed_steps"]:
                if done:
                    break
                item = QTreeWidgetItem([str(row + 1), text, "← 停在這裡"])
                item.setForeground(2, QColor(theme.PALETTE["warn"]))
                self.steps.addTopLevelItem(item)
                break
            g = gaps[i] if i < len(gaps) else None
            item = QTreeWidgetItem([str(row + 1) if row != last_row else "", text,
                                    "第一步" if i == 0 else ("同時" if not g else f"+{g:.0f} 幀")])
            if i == slow:
                for col in range(3):
                    item.setForeground(col, QColor(theme.PALETTE["warn"]))
            self.steps.addTopLevelItem(item)
            last_row = row

    def _open_item(self, item, _col):
        r = item.data(0, Qt.UserRole)
        if r and r["combo_name"]:
            self.open_combo.emit(r["combo_id"])

    # ---- 刪除 ----
    def _delete_selected(self):
        sel = self._selected()
        if not sel:
            return
        if not confirm(self, "刪除練習紀錄", f"要刪除選取的 {len(sel)} 筆練習紀錄嗎？\n刪除後沒辦法救回。"):
            return
        self.store.delete_practice([r["id"] for r in sel])
        self._fill_combos()
        self.refresh()

    def _clear_all(self):
        combo_id, since = self._filter()
        ids = self.store.practice_ids(self.character, combo_id, since)
        if not ids:
            return
        scope = self.combo_box.currentText()
        when = self.range_box.currentText()
        msg = f"要刪除「{scope}」在「{when}」的全部 {len(ids)} 筆練習紀錄嗎？\n連段本身不會刪。刪除後沒辦法救回。"
        if not confirm(self, "清空練習紀錄", msg):
            return
        self.store.delete_practice(ids)
        self._fill_combos()
        self.refresh()
