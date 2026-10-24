"""指令表分頁：一招一張卡片，派生招式內縮排在原招式下面；可以加入連段、修改、新增、刪除。"""
from __future__ import annotations

import copy

from PySide6.QtCore import QEvent, Qt, QTimer, Signal
from PySide6.QtWidgets import (QFrame, QHBoxLayout, QLabel, QMenu, QPushButton, QScrollArea,
                               QVBoxLayout, QWidget)

from data import movelist as ml
from ui import theme
from ui.controller_setup import ConfirmButton, confirm
from ui.move_card import MoveCard
from ui.row_editor import RowEditor
from ui.trial_view import TrialView

CARD_MIN_W = 430


class MovelistPage(QWidget):
    add_requested = Signal(str, str)          # (記法, 招式名稱)
    movelist_changed = Signal(str, dict)      # (角色 id, 新的指令表)

    def __init__(self, cfg: dict, host=None, parent=None):
        super().__init__(parent)
        self.cfg = cfg
        self.host = host                      # 連段頁：借用它的按鍵顯示設定和控制器錄製
        self.cid = ""
        self.data: dict | None = None
        self.selected: tuple | None = None    # (分類, 招式, 派生；-1＝不是派生)
        self.cards: dict[tuple, MoveCard] = {}
        self._target = None                   # 編輯區正在編輯什麼
        self._cols = 0
        self.renderer = TrialView()           # 不顯示，只用來畫指令圖示
        self.renderer.hide()
        self._build()

    def _build(self):
        outer = QHBoxLayout(self)
        outer.setContentsMargins(20, 16, 20, 16)
        outer.setSpacing(12)
        panel = theme.GlassPanel()
        box = QVBoxLayout(panel)
        box.setContentsMargins(16, 12, 16, 14)
        box.setSpacing(8)

        top = QHBoxLayout()
        self.title = theme.GlowTitle("", 13)
        top.addWidget(self.title, 1)
        self.source = QLabel()
        self.source.setObjectName("muted")
        self.source.setFont(theme.font(9))
        top.addWidget(self.source)
        box.addLayout(top)

        edit = QHBoxLayout()
        self.new_move_btn = QPushButton("新增招式")
        self.new_move_btn.clicked.connect(self._new_move)
        self.new_child_btn = QPushButton("新增派生")
        self.new_child_btn.clicked.connect(self._new_child)
        self.edit_btn = QPushButton("修改")
        self.edit_btn.clicked.connect(self._edit)
        self.del_btn = ConfirmButton("刪除招式", "刪除", message=self._delete_message)
        self.del_btn.confirmed.connect(self._delete)
        for b in (self.new_move_btn, self.new_child_btn, self.edit_btn, self.del_btn):
            edit.addWidget(b)
        edit.addStretch(1)
        self.reset_btn = ConfirmButton("還原成內建版本", "還原",
                                       message="要還原成內建版本嗎？你對這個角色指令表的修改會被取代，沒辦法救回。")
        self.reset_btn.confirmed.connect(self._reset)
        edit.addWidget(self.reset_btn)
        box.addLayout(edit)

        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QFrame.NoFrame)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.scroll.setStyleSheet("QScrollArea, QScrollArea > QWidget > QWidget { background: transparent; }")
        self.scroll.setAccessibleName("指令表")
        self.scroll.viewport().installEventFilter(self)   # 編輯區打開／收起時，卡片重新分欄
        box.addWidget(self.scroll, 1)

        hint = QLabel("點兩下卡片或按「加入連段」，就會加進「連段庫」目前選的連段（沒有選就新增一個）。"
                      "派生招式會連同原招式一起加入。在卡片上按右鍵可以修改。")
        hint.setObjectName("muted")
        hint.setWordWrap(True)
        hint.setFont(theme.font(9))
        box.addWidget(hint)
        outer.addWidget(panel, 1)

        self.editor = RowEditor(self.cfg, mode="move")
        self.editor.record_host = self.host
        self.editor.setFixedWidth(400)
        self.editor.hide()
        self.editor.closed.connect(self._close_editor)
        self.editor.saved.connect(self._saved)
        self.editor.record_toggled.connect(self._record_toggled)
        outer.addWidget(self.editor)

        self._relayout_timer = QTimer(self)
        self._relayout_timer.setSingleShot(True)
        self._relayout_timer.setInterval(60)
        self._relayout_timer.timeout.connect(self._maybe_relayout)

    # ---- 外觀 ----
    def apply_theme(self):
        self._fill(self.selected)

    def _sync_style(self):
        """指令圖示跟著連段頁的「按鍵顯示」設定（控制器按鍵或英文、面向）。"""
        v = getattr(self.host, "view", None)
        if v is None:
            return
        self.renderer.facing_right = v.facing_right
        self.renderer.style, self.renderer.layout_keys = v.style, v.layout_keys

    def showEvent(self, e):
        super().showEvent(e)
        self._sync_style()
        for card in self.cards.values():
            card.refresh()

    def resizeEvent(self, e):
        super().resizeEvent(e)
        self._relayout_timer.start()

    def eventFilter(self, obj, e):
        if obj is self.scroll.viewport() and e.type() == QEvent.Resize:
            self._relayout_timer.start()
        return super().eventFilter(obj, e)

    def _col_count(self) -> int:
        w = self.scroll.viewport().width() or self.scroll.width()
        return max(1, min(3, w // CARD_MIN_W))

    def _maybe_relayout(self):
        if self._col_count() != self._cols:
            self._fill(self.selected)

    # ---- 資料 ----
    def set_character(self, cid: str, data: dict | None):
        self._close_editor()
        self.cid, self.data = cid, data
        self.selected = None
        self._fill()

    def _modified(self) -> bool:
        return (ml.user_dir() / f"{self.cid}.json").exists()

    def _fill(self, select: tuple | None = None):
        self._sync_style()
        self.cards = {}
        content = QWidget()
        lay = QVBoxLayout(content)
        lay.setContentsMargins(0, 0, 6, 0)
        lay.setSpacing(6)
        self._cols = self._col_count()
        if not self.data:
            self.title.setText("沒有這個角色的指令表")
            self.source.setText("")
        else:
            self.title.setText(self.data.get("name", ""))
            parts = [self.data.get("source", "")]
            if self._modified():
                parts.append("（這份有你修改過的內容）")
            self.source.setText("　".join(p for p in parts if p))
            for ci, cat in enumerate(self.data.get("categories", [])):
                lay.addWidget(theme.GlowTitle(cat["name"], 12, line=True))
                row = QHBoxLayout()
                row.setSpacing(10)
                cols, heights = [], []
                for _ in range(self._cols):
                    col = QVBoxLayout()
                    col.setSpacing(8)
                    row.addLayout(col, 1)
                    cols.append(col)
                    heights.append(0)
                for mi, mv in enumerate(cat.get("moves", [])):
                    group = self._group(ci, mi, mv, cat["key"])
                    k = heights.index(min(heights))       # 放到目前最短的那一欄
                    cols[k].addWidget(group)
                    heights[k] += 3 + 2 * len(mv.get("children", []))
                for col in cols:
                    col.addStretch(1)
                lay.addLayout(row)
                if not cat.get("moves"):
                    empty = QLabel("這一類還沒有招式，選一張卡片後按「新增招式」可以加進來。")
                    empty.setObjectName("muted")
                    lay.addWidget(empty)
        lay.addStretch(1)
        old = self.scroll.verticalScrollBar().value()
        self.scroll.setWidget(content)
        self.scroll.verticalScrollBar().setValue(old)
        self.selected = select if select in self.cards else None
        if self.selected:
            self.cards[self.selected].set_selected(True)
        self._update_buttons()

    def _group(self, ci: int, mi: int, mv: dict, cat_key: str) -> QWidget:
        """原招式一張卡片，派生招式內縮排在下面。"""
        w = QWidget()
        v = QVBoxLayout(w)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(4)
        v.addWidget(self._card(mv, (ci, mi, -1), cat_key, False))
        children = mv.get("children", [])
        for ki, child in enumerate(children):
            v.addWidget(self._card(child, (ci, mi, ki), cat_key, True, ki == len(children) - 1))
        return w

    def _card(self, mv: dict, path: tuple, cat_key: str, child: bool, last: bool = True) -> MoveCard:
        card = MoveCard(mv, path, cat_key, self.renderer, child, last)
        card.clicked.connect(self._select)
        card.double_clicked.connect(self._add_to_combo)
        card.add_clicked.connect(self._add_to_combo)
        card.menu_requested.connect(self._menu)
        self.cards[path] = card
        return card

    def _select(self, path: tuple):
        if self.selected in self.cards:
            self.cards[self.selected].set_selected(False)
        self.selected = path
        if path in self.cards:
            self.cards[path].set_selected(True)
        self._update_buttons()

    def _cat(self, ci) -> dict:
        return self.data["categories"][ci]

    def _move_at(self, path: tuple) -> dict:
        ci, mi, ki = path
        mv = self._cat(ci)["moves"][mi]
        return mv["children"][ki] if ki >= 0 else mv

    def _update_buttons(self):
        has = bool(self.data)
        sel = self.selected is not None
        self.new_move_btn.setEnabled(has)
        self.edit_btn.setEnabled(sel)
        self.del_btn.setEnabled(sel)
        self.new_child_btn.setEnabled(sel and self.selected[2] < 0)
        self.reset_btn.setEnabled(has and ml.builtin(self.cid) is not None and self._modified())

    # ---- 加入連段 ----
    def _add_to_combo(self, path: tuple):
        self._select(path)
        ci, mi, ki = path
        mv = self._cat(ci)["moves"][mi]
        if ki >= 0:
            child = mv["children"][ki]
            parts = [p for p in (mv.get("input", ""), child.get("input", "")) if p]
            self.add_requested.emit(".".join(parts), child["name"])
        elif mv.get("input"):
            self.add_requested.emit(mv["input"], mv["name"])

    def _menu(self, path: tuple, pos):
        menu = QMenu(self)
        add = menu.addAction("加入目前連段")
        edit = menu.addAction("修改")
        child = menu.addAction("新增派生") if path[2] < 0 else None
        menu.addSeparator()
        remove = menu.addAction("刪除")
        chosen = menu.exec(pos)
        if chosen is add:
            self._add_to_combo(path)
        elif chosen is edit:
            self._edit()
        elif child is not None and chosen is child:
            self._new_child()
        elif chosen is remove:
            if confirm(self.window(), "刪除招式", self._delete_message()):
                self._delete()

    # ---- 編輯區 ----
    def _open_editor(self, target, move: dict, cat_key: str, title: str):
        self._target = target
        self.editor.open_move(move, cat_key, title)

    def _close_editor(self):
        self.editor.stop_recording()
        if self.host:
            self.host.cancel_recording()
        self.editor.hide()
        self._target = None

    def _record_toggled(self, on: bool):
        if not self.host:
            return
        if on:
            self.host.start_recording(self.editor.set_recorded)
        else:
            self.host.stop_recording()

    def _new_move(self):
        if not self.data:
            return
        cats = self.data.get("categories", [])
        if self.selected:
            ci = self.selected[0]
        else:
            ci = next((i for i, c in enumerate(cats) if c["key"] == "special"), 0)
        cat = cats[ci]
        self._open_editor(("new", ci), {}, cat["key"], f"新增招式到「{cat['name']}」")

    def _new_child(self):
        if not self.selected or self.selected[2] >= 0:
            return
        ci, mi, _ = self.selected
        mv = self._cat(ci)["moves"][mi]
        self._open_editor(("child", ci, mi), {}, self._cat(ci)["key"], f"新增「{mv['name']}」的派生招式")

    def _edit(self):
        if not self.selected:
            return
        mv = self._move_at(self.selected)
        self._open_editor(("edit", self.selected), mv, self._cat(self.selected[0])["key"], f"修改「{mv['name']}」")

    def _saved(self, values: dict):
        target = self._target
        if not target or not self.data:
            return
        if target[0] == "new":
            moves = self._cat(target[1]).setdefault("moves", [])
            moves.append(values)
            select = (target[1], len(moves) - 1, -1)
        elif target[0] == "child":
            _, ci, mi = target
            children = self._cat(ci)["moves"][mi].setdefault("children", [])
            children.append(values)
            select = (ci, mi, len(children) - 1)
        else:
            select = target[1]
            mv = self._move_at(select)
            mv.clear()
            mv.update(values)
        self._close_editor()
        self._commit(select)

    def _commit(self, select: tuple | None):
        ml.save_user(self.data)
        self._fill(select)
        self.movelist_changed.emit(self.cid, self.data)

    # ---- 刪除、還原 ----
    def _delete_message(self) -> str:
        if not self.selected or not self.data:
            return ""
        mv = self._move_at(self.selected)
        extra = ""
        if self.selected[2] < 0 and mv.get("children"):
            extra = f"\n它下面的 {len(mv['children'])} 個派生招式也會一起刪除。"
        return (f"要刪除「{mv.get('name', '')}」嗎？{extra}\n"
                "刪除後要按「還原成內建版本」才找得回內建招式。")

    def _delete(self):
        if not self.selected:
            return
        ci, mi, ki = self.selected
        mv = self._cat(ci)["moves"][mi]
        self._close_editor()
        if ki >= 0:
            mv["children"].pop(ki)
            if not mv["children"]:
                mv.pop("children")
            self._commit((ci, mi, -1))
        else:
            self._cat(ci)["moves"].pop(mi)
            self._commit(None)

    def _reset(self):
        original = ml.builtin(self.cid)
        if original is None:
            return
        self._close_editor()
        self.data = copy.deepcopy(original)
        self._commit(None)

    def rebuild_palette(self):
        self.editor.rebuild_palette()
