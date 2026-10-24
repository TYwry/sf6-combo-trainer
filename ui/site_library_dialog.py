"""從連段分享網站抓連段：讀清單、篩選、預覽、勾選後加入連段庫。"""
from __future__ import annotations

import time

from PySide6.QtCore import QEvent, QObject, Qt, QThread, Signal
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (QCheckBox, QComboBox, QDialog, QHBoxLayout, QHeaderView, QLabel,
                               QAbstractItemView, QApplication, QLineEdit, QPushButton, QScrollArea,
                               QSplitter, QTreeWidget, QTreeWidgetItem, QVBoxLayout, QWidget)

from core import site_library as site
from core.notation import parse
from data.storage import ComboStore
from ui import theme
from ui.exchange_dialog import notice
from ui.trial_view import TrialView

COLS = ["名稱", "作者", "難度", "傷害", "動力槽", "SA", "步數"]


class _Fetcher(QObject):
    progress = Signal(int, int)
    finished = Signal(list, str)          # (資料, 錯誤訊息；空字串＝成功)

    def __init__(self):
        super().__init__()
        self.stop = False

    def run(self):
        try:
            items = site.fetch_all("classic", lambda p, n: self.progress.emit(p, n), lambda: self.stop)
            site.save_cache(items)
            self.finished.emit(items, "")
        except site.FetchError as e:
            self.finished.emit([], str(e))
        except Exception as e:           # 不讓背景錯誤把程式關掉
            site.log.exception("抓網站連段失敗")
            self.finished.emit([], f"抓取失敗：{e}")


class _NumItem(QTreeWidgetItem):
    """數字欄位照數字大小排序。"""

    def __lt__(self, other):
        col = self.treeWidget().sortColumn() if self.treeWidget() else 0
        a, b = self.data(col, Qt.UserRole + 1), other.data(col, Qt.UserRole + 1)
        if isinstance(a, (int, float)) and isinstance(b, (int, float)):
            return a < b
        return self.text(col) < other.text(col)


class SiteLibraryDialog(QDialog):
    def __init__(self, store: ComboStore, character: str, char_names: dict[str, str], view=None, parent=None):
        super().__init__(parent)
        self.store, self.char_names = store, char_names
        self.combos: list[site.SiteCombo] = []
        self.added_ids: list[int] = []
        self.imported = store.site_imported_keys()
        self._thread: QThread | None = None
        self._worker: _Fetcher | None = None
        self.setWindowTitle("從網站抓連段")
        self._fit_screen()
        box = QVBoxLayout(self)
        box.setContentsMargins(22, 16, 22, 16)
        box.setSpacing(8)

        top = QHBoxLayout()
        top.addWidget(theme.GlowTitle("從網站抓連段", 13))
        top.addStretch(1)
        self.status = QLabel()
        self.status.setObjectName("muted")
        top.addWidget(self.status)
        self.fetch_btn = QPushButton("更新網站資料")
        self.fetch_btn.clicked.connect(self._fetch)
        top.addWidget(self.fetch_btn)
        self.cancel_fetch_btn = QPushButton("停止")
        self.cancel_fetch_btn.clicked.connect(self._stop_fetch)
        self.cancel_fetch_btn.hide()
        top.addWidget(self.cancel_fetch_btn)
        box.addLayout(top)

        src = QLabel(f"資料來源：{site.SITE}（經典操作）。抓下來的清單會存在你的電腦，下次打開不用重抓。")
        src.setObjectName("muted")
        src.setWordWrap(True)
        box.addWidget(src)

        filters = QHBoxLayout()
        filters.addWidget(QLabel("角色"))
        self.char = QComboBox()
        self.char.setAccessibleName("角色")
        self.char.addItem("全部角色", "")
        for cid, name in char_names.items():
            self.char.addItem(name, cid)
        self.char.setCurrentIndex(max(0, self.char.findData(character)))
        filters.addWidget(self.char)
        filters.addWidget(QLabel("搜尋（名稱、作者、標籤）"))
        self.search = QLineEdit()
        self.search.setAccessibleName("搜尋名稱、作者、標籤")
        filters.addWidget(self.search, 1)
        filters.addWidget(QLabel("標籤"))
        self.tag = QComboBox()
        self.tag.setAccessibleName("標籤")
        filters.addWidget(self.tag)
        self.only_new = QCheckBox("只顯示還沒加入的")
        box.addLayout(filters)
        self.char.currentIndexChanged.connect(self._refill)
        self.search.textChanged.connect(self._refill)
        self.tag.currentIndexChanged.connect(self._refill)
        self.only_new.toggled.connect(self._refill)

        # 加入、關閉放在最前面，視窗再窄、右邊再長都看得到
        main = QHBoxLayout()
        self.add_btn = QPushButton("加入連段庫")
        self.add_btn.clicked.connect(self._add)
        close = QPushButton("關閉")
        close.clicked.connect(self.reject)
        main.addWidget(self.add_btn)
        main.addWidget(close)
        main.addStretch(1)
        main.addWidget(self.only_new)
        box.addLayout(main)
        actions = QHBoxLayout()
        actions.addWidget(QLabel("勾選"))
        for text, fn in (("全選", lambda: self._check_all(True)), ("全不選", lambda: self._check_all(False)),
                         ("反向勾選", self._invert), ("勾選反白", lambda: self._check_selected(True)),
                         ("取消反白", lambda: self._check_selected(False)),
                         ("勾選沒問題的", self._check_ok)):
            btn = QPushButton(text)
            btn.clicked.connect(fn)
            actions.addWidget(btn)
        actions.addStretch(1)
        box.addLayout(actions)
        hint = QLabel("按住 Shift 或 Ctrl 點清單可以一次反白很多列，再按「勾選反白」；也可以按空白鍵勾選或取消。"
                      "「全不選」會取消所有勾選（包括目前沒列出的），其他按鈕只對目前列出的連段有效。")
        hint.setObjectName("muted")
        hint.setWordWrap(True)
        box.addWidget(hint)

        split = QSplitter(Qt.Horizontal)
        self.tree = QTreeWidget()
        self.tree.setAccessibleName("網站上的連段")
        self.tree.setHeaderLabels(COLS)
        self.tree.setRootIsDecorated(False)
        self.tree.setUniformRowHeights(True)
        self.tree.setSortingEnabled(True)
        self.tree.sortByColumn(3, Qt.DescendingOrder)
        self.tree.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.tree.installEventFilter(self)
        header = self.tree.header()
        # 清單不左右捲動：名稱吃掉剩下的寬度，其他欄固定（可以拖拉調整）
        self.tree.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        header.setStretchLastSection(False)
        header.setSectionResizeMode(0, QHeaderView.Stretch)
        header.setMinimumSectionSize(36)
        for i, width in enumerate((96, 100, 58, 62, 36, 46), 1):
            header.setSectionResizeMode(i, QHeaderView.Interactive)
            self.tree.setColumnWidth(i, width)
        self.tree.currentItemChanged.connect(lambda *_: self._preview())
        self.tree.itemChanged.connect(lambda *_: self._update_count())
        split.addWidget(self.tree)

        right = QWidget()
        rbox = QVBoxLayout(right)
        rbox.setContentsMargins(8, 0, 0, 0)
        self.p_title = theme.GlowTitle("", 12)
        rbox.addWidget(self.p_title)
        self.p_info = QLabel()
        self.p_info.setWordWrap(True)
        self.p_info.setObjectName("muted")
        rbox.addWidget(self.p_info)
        self.view = TrialView()
        if view is not None:
            self.view.set_facing(view.facing_right)
            self.view.set_button_style(view.style, view.layout_keys)
        rbox.addWidget(self.view, 1)
        self.p_warn = QLabel()
        self.p_warn.setWordWrap(True)
        self.p_warn.setStyleSheet(f"color: {theme.colors()['warn']};")
        rbox.addWidget(self.p_warn)
        scroll = QScrollArea()              # 預覽太長時在右邊捲動，不把視窗撐高
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.NoFrame)
        # 預覽的捲軸加粗，比較好按
        scroll.setStyleSheet("QScrollBar:horizontal { height: 18px; } QScrollBar:vertical { width: 18px; }"
                             "QScrollBar::handle:horizontal { min-width: 60px; border-radius: 6px; }"
                             "QScrollBar::handle:vertical { min-height: 60px; border-radius: 6px; }")
        scroll.setWidget(right)
        split.addWidget(scroll)
        split.setSizes([self.width() * 62 // 100, self.width() * 38 // 100])
        box.addWidget(split, 1)

        self._checked: set[str] = set()
        self._load(*site.load_cache())

    def _fit_screen(self):
        """視窗大小配合螢幕可用範圍（扣掉工作列），不超出畫面。"""
        scr = (self.parent().screen() if self.parent() else None) or QApplication.primaryScreen()
        avail = scr.availableGeometry() if scr else None
        w, h = 1280, 820
        if avail is not None:
            w, h = min(w, int(avail.width() * 0.92)), min(h, int(avail.height() * 0.88))
        self.setMinimumSize(min(720, w), min(420, h))
        self.resize(w, h)
        if avail is not None:
            self.move(avail.center().x() - w // 2, avail.center().y() - h // 2)

    def eventFilter(self, obj, ev):
        if obj is self.tree and ev.type() == QEvent.KeyPress and ev.key() == Qt.Key_Space:
            rows = self._selected_rows()
            if rows:
                on = any(it.checkState(0) != Qt.Checked for it in rows)
                self._check_selected(on)
                return True
        return super().eventFilter(obj, ev)

    # ---- 資料 ----
    def _load(self, items: list[dict], fetched: float):
        self.combos = [c for c in (site.to_combo(it) for it in items) if c]
        if fetched:
            when = time.strftime("%Y-%m-%d %H:%M", time.localtime(fetched))
            self.status.setText(f"上次更新：{when}，共 {len(self.combos)} 筆")
        else:
            self.status.setText("還沒抓過網站資料，請按「更新網站資料」（大約 1 分鐘）")
        tags = sorted({t for c in self.combos for t in c.tags})
        self.tag.blockSignals(True)
        self.tag.clear()
        self.tag.addItem("全部標籤", "")
        for t in tags:
            self.tag.addItem(t, t)
        self.tag.blockSignals(False)
        self._refill()

    def _visible(self) -> list[site.SiteCombo]:
        cid = self.char.currentData() or ""
        words = site.to_trad(self.search.text()).strip().lower().split()   # 打簡體或繁體都找得到
        tag = self.tag.currentData() or ""
        out = []
        for c in self.combos:
            if c.character not in self.char_names:      # 練習器還沒有的角色先不列
                continue
            if cid and c.character != cid:
                continue
            if tag and tag not in c.tags:
                continue
            if self.only_new.isChecked() and c.key in self.imported:
                continue
            hay = " ".join([c.title, c.author, *c.tags]).lower()
            if any(w not in hay for w in words):
                continue
            out.append(c)
        return out

    def _refill(self):
        self.tree.blockSignals(True)
        self.tree.setSortingEnabled(False)
        self.tree.clear()
        self._by_key = {}
        for c in self._visible():
            done = c.key in self.imported
            vals = [c.title, c.author, "★" * c.difficulty if c.difficulty else "",
                    f"{c.damage:,}" if c.damage else "", f"{c.drive:g}" if c.drive is not None else "",
                    str(c.sa) if c.sa is not None else "", str(len(c.steps))]
            it = _NumItem(vals)
            for col, num in ((2, c.difficulty), (3, c.damage or 0), (4, c.drive or 0), (5, c.sa or 0),
                             (6, len(c.steps))):
                it.setData(col, Qt.UserRole + 1, num)
            it.setData(0, Qt.UserRole, c.key)
            if done:
                it.setFlags(it.flags() & ~Qt.ItemIsUserCheckable)
                for col in range(len(COLS)):
                    it.setForeground(col, QColor(theme.PALETTE["muted"]))
            else:
                it.setFlags(it.flags() | Qt.ItemIsUserCheckable)
                it.setCheckState(0, Qt.Checked if c.key in self._checked else Qt.Unchecked)
            it.setToolTip(0, ("（已加入）" if done else "") + c.notation)
            self.tree.addTopLevelItem(it)
            self._by_key[c.key] = c
        self.tree.setSortingEnabled(True)
        self.tree.blockSignals(False)
        self._update_count()
        if self.tree.topLevelItemCount():
            self.tree.setCurrentItem(self.tree.topLevelItem(0))
        else:
            self._preview()

    def _sync_checked(self):
        for i in range(self.tree.topLevelItemCount()):
            it = self.tree.topLevelItem(i)
            key = it.data(0, Qt.UserRole)
            if it.flags() & Qt.ItemIsUserCheckable:
                if it.checkState(0) == Qt.Checked:
                    self._checked.add(key)
                else:
                    self._checked.discard(key)

    def _update_count(self):
        self._sync_checked()
        n = len(self._checked - self.imported)
        self.add_btn.setText(f"加入連段庫（{n} 個）")
        self.add_btn.setEnabled(n > 0)

    def _check_all(self, on: bool):
        if not on:
            self._checked.clear()        # 換過角色或搜尋時勾的也一起取消
        self._set_checks(self._rows(), lambda it: on)

    def _rows(self):
        return [self.tree.topLevelItem(i) for i in range(self.tree.topLevelItemCount())
                if self.tree.topLevelItem(i).flags() & Qt.ItemIsUserCheckable]

    def _selected_rows(self):
        return [it for it in self.tree.selectedItems() if it.flags() & Qt.ItemIsUserCheckable]

    def _set_checks(self, rows, decide):
        self.tree.blockSignals(True)
        for it in rows:
            it.setCheckState(0, Qt.Checked if decide(it) else Qt.Unchecked)
        self.tree.blockSignals(False)
        self._update_count()

    def _invert(self):
        self._set_checks(self._rows(), lambda it: it.checkState(0) != Qt.Checked)

    def _check_selected(self, on: bool):
        self._set_checks(self._selected_rows(), lambda it: on)

    def _check_ok(self):
        """勾選目前列出、每一步都轉得過來的連段（其他的不動）。"""
        ok = [it for it in self._rows() if not self._by_key[it.data(0, Qt.UserRole)].problems]
        self._set_checks(ok, lambda it: True)

    def _preview(self):
        it = self.tree.currentItem()
        c = self._by_key.get(it.data(0, Qt.UserRole)) if it else None
        if not c:
            self.p_title.setText("")
            self.p_info.setText("")
            self.p_warn.setText("")
            self.view.set_combo(parse(""), [], "")
            return
        self.p_title.setText(c.title)
        info = [f"{self.char_names.get(c.character, c.character)}　作者：{c.author}"]
        if c.tags:
            info.append("標籤：" + "、".join(c.tags))
        self.p_info.setText("\n".join(info))
        combo = parse(c.notation)
        self.view.set_combo(combo, [""] * len(combo.groups), "")
        self.view.set_stats(f"{c.damage:,}" if c.damage else "", f"{c.drive:g}" if c.drive is not None else "",
                            str(c.sa) if c.sa is not None else "")
        warn = list(c.problems)
        self.p_warn.setText("\n".join(warn) + ("\n看不懂的步驟會把網站原文放在備註裡，可以加入後自己改。" if c.problems else ""))

    # ---- 抓取 ----
    def _fetch(self):
        if self._thread:
            return
        self.fetch_btn.setEnabled(False)
        self.cancel_fetch_btn.show()
        self.status.setText("正在連線到網站…")
        self._thread = QThread(self)
        self._worker = _Fetcher()
        self._worker.moveToThread(self._thread)
        self._thread.started.connect(self._worker.run)
        self._worker.progress.connect(self._progress)       # 背景執行緒 → 回到畫面執行緒更新文字
        self._worker.finished.connect(self._fetched)
        self._thread.start()

    def _progress(self, page: int, pages: int):
        self.status.setText(f"正在讀第 {page}／{pages} 頁…")

    def _stop_fetch(self):
        if self._worker:
            self._worker.stop = True
            self.status.setText("正在停止…")

    def _fetched(self, items: list, error: str):
        self._thread.quit()
        self._thread.wait(3000)
        self._thread = self._worker = None
        self.fetch_btn.setEnabled(True)
        self.cancel_fetch_btn.hide()
        if error:
            cached, when = site.load_cache()
            self._load(cached, when)
            notice(self, "沒有抓到", error + ("\n先顯示上次抓到的清單。" if cached else ""))
            return
        self._load(items, time.time())

    # ---- 加入 ----
    def _add(self):
        self._sync_checked()
        chosen = [c for c in self.combos if c.key in self._checked and c.key not in self.imported]
        if not chosen:
            return
        pairs = [(c.key, site.to_store_item(c)) for c in chosen]
        try:
            ids = self.store.add_site_combos(pairs)
        except Exception as e:
            site.log.exception("加入網站連段失敗")
            notice(self, "沒有加入", f"寫入連段庫時出錯，沒有任何連段被加入。\n{e}")
            return
        self.added_ids += ids
        self.imported |= {c.key for c in chosen}
        self._checked -= {c.key for c in chosen}
        per: dict[str, int] = {}
        for c in chosen:
            per[c.character] = per.get(c.character, 0) + 1
        lines = ["已加入：" + "、".join(f"{self.char_names.get(k, k)} {v} 個" for k, v in per.items())]
        bad = sum(1 for c in chosen if c.problems)
        if bad:
            lines.append(f"其中 {bad} 個有看不懂的步驟，網站原文放在連段的備註裡，可以自己修改。")
        self._refill()
        notice(self, "加入完成", "\n".join(lines))

    def done(self, result):
        if self._worker:
            self._worker.stop = True
        if self._thread:
            self._thread.quit()
            self._thread.wait(25000)
        super().done(result)
