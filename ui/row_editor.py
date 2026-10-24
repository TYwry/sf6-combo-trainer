"""單一列的編輯區：文字框、可以用滑鼠點的按鈕面板（動作、方向、常用搓招、自訂庫）、控制器錄製。"""
from __future__ import annotations

from PySide6.QtCore import QPoint, QRect, QSize, Qt, QTimer, Signal
from PySide6.QtWidgets import (QComboBox, QPlainTextEdit, QDialog, QDialogButtonBox, QFormLayout, QFrame, QHBoxLayout, QLabel,
                               QLayout, QLineEdit, QMenu, QMessageBox, QPushButton, QScrollArea,
                               QSizePolicy, QVBoxLayout, QWidget)

from core.notation import normalize_simul, parse
from data import snippets
from data.config import action_title, all_actions
from ui import theme
from ui.controller_setup import ConfirmButton, confirm

DIRECTIONS = [("7", "↖"), ("8", "↑"), ("9", "↗"), ("4", "←"), ("6", "→"),
              ("1", "↙"), ("2", "↓"), ("3", "↘")]
MOTIONS = [("236", "↓↘→"), ("214", "↓↙←"), ("623", "→↓↘"), ("421", "←↓↙"),
           ("41236", "←↙↓↘→"), ("63214", "→↘↓↙←"), ("22", "↓↓"), ("66", "→→"), ("44", "←←"),
           ("236236", "↓↘→↓↘→"), ("214214", "↓↙←↓↙←"), ("[4]6", "蓄←→"), ("[2]8", "蓄↓↑")]
EXTRA_BUTTONS = ["P", "K", "PP", "KK", "DR"]
MARKS = [("+", "＋ 同時按"), ("j.", "跳躍 j."), (".", "分隔 .")]
UNDO_MAX = 3                 # 「上一步」最多退回幾次


class FlowLayout(QLayout):
    """一排放不下就自動換行的排版。"""

    def __init__(self, parent=None, spacing: int = 6):
        super().__init__(parent)
        self._items = []
        self._spacing = spacing
        self.setContentsMargins(0, 0, 0, 0)

    def addItem(self, item):
        self._items.append(item)

    def count(self):
        return len(self._items)

    def itemAt(self, i):
        return self._items[i] if 0 <= i < len(self._items) else None

    def takeAt(self, i):
        return self._items.pop(i) if 0 <= i < len(self._items) else None

    def expandingDirections(self):
        return Qt.Orientations(0)

    def hasHeightForWidth(self):
        return True

    def heightForWidth(self, width):
        return self._do(QRect(0, 0, width, 0), True)

    def setGeometry(self, rect):
        super().setGeometry(rect)
        self._do(rect, False)

    def sizeHint(self):
        return self.minimumSize()

    def minimumSize(self):
        size = QSize()
        for it in self._items:
            size = size.expandedTo(it.minimumSize())
        return size

    def _do(self, rect, test):
        x, y, line_h = rect.x(), rect.y(), 0
        for it in self._items:
            hint = it.sizeHint()
            if x + hint.width() > rect.right() and line_h:
                x, y, line_h = rect.x(), y + line_h + self._spacing, 0
            if not test:
                it.setGeometry(QRect(QPoint(x, y), hint))
            x += hint.width() + self._spacing
            line_h = max(line_h, hint.height())
        return y + line_h - rect.y()


class FlowHolder(QWidget):
    """放 FlowLayout 的容器：寬度改變時，高度跟著換行後的行數調整。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.flow = FlowLayout(self)
        sp = QSizePolicy(QSizePolicy.Preferred, QSizePolicy.Preferred)
        sp.setHeightForWidth(True)
        self.setSizePolicy(sp)

    def hasHeightForWidth(self):
        return True

    def heightForWidth(self, w):
        return self.flow.heightForWidth(w)

    def sizeHint(self):
        return QSize(300, self.flow.heightForWidth(300))

    def resizeEvent(self, e):
        super().resizeEvent(e)
        h = self.flow.heightForWidth(self.width())
        if h != self.minimumHeight():
            self.setMinimumHeight(h)


class SnippetDialog(QDialog):
    """新增或修改一顆自訂按鈕。"""

    def __init__(self, name: str = "", notation: str = "", parent=None, host=None):
        super().__init__(parent)
        self.setWindowTitle("自訂按鈕")
        self.host = host
        form = QFormLayout(self)
        self.name = QLineEdit(name)
        self.name.setAccessibleName("按鈕名稱")
        self.notation = QLineEdit(notation)
        self.notation.setAccessibleName("記法")
        form.addRow("按鈕名稱", self.name)
        form.addRow("記法", self.notation)
        rec = QHBoxLayout()
        self.record_btn = QPushButton("用控制器錄")
        self.record_btn.setCheckable(True)
        self.record_btn.setEnabled(host is not None)
        self.record_btn.toggled.connect(self._record_toggled)
        rec.addWidget(self.record_btn)
        self.record_status = QLabel()
        self.record_status.setWordWrap(True)
        rec.addWidget(self.record_status, 1)
        form.addRow(rec)
        hint = QLabel("記法的寫法跟連段一樣，例如 236PP.6P。名稱是顯示在按鈕上的字。")
        hint.setObjectName("muted")
        hint.setWordWrap(True)
        form.addRow(hint)
        btns = QDialogButtonBox()
        btns.addButton("儲存", QDialogButtonBox.AcceptRole)
        btns.addButton("取消", QDialogButtonBox.RejectRole)
        btns.accepted.connect(self._ok)
        btns.rejected.connect(self.reject)
        form.addRow(btns)
        self.resize(460, 0)

    def _record_toggled(self, on: bool):
        if not self.host:
            return
        if on:
            self.record_btn.setText("停止錄製")
            self.record_status.setText("錄製中：照著打一次，停手 2 秒會自動結束。")
            self.host.start_recording(self._recorded)
        else:
            self.host.stop_recording()

    def _recorded(self, text: str):
        self.record_btn.blockSignals(True)
        self.record_btn.setChecked(False)
        self.record_btn.setText("用控制器錄")
        self.record_btn.blockSignals(False)
        if text:
            self.notation.setText(text)
            self.record_status.setText("已錄好，可以再修改文字。")
        else:
            self.record_status.setText("沒有錄到按鍵。請確認控制器有連上，按鍵也有設定動作。")

    def done(self, result):
        if self.host and self.record_btn.isChecked():
            self.host.cancel_recording()
        super().done(result)

    def _ok(self):
        if not self.notation.text().strip():
            QMessageBox.information(self, "自訂按鈕", "記法不能是空的。")
            return
        self.accept()

    def values(self) -> dict:
        n = self.notation.text().strip()
        return {"name": self.name.text().strip() or n, "notation": n}


class RowEditor(theme.GlassPanel):
    text_changed = Signal(str)          # 這一列的記法改了
    insert_below = Signal()
    delete_row = Signal()
    closed = Signal()
    record_toggled = Signal(bool)       # 開始／停止用控制器錄
    saved = Signal(dict)                # 招式模式：按「儲存」

    def __init__(self, cfg: dict, parent=None, mode: str = "row"):
        super().__init__(parent)
        self.cfg = cfg
        self.mode = mode                 # row＝連段的一列；move＝指令表的一招
        self.row = -1
        self._recorded = ""
        self._history: list[str] = []      # 「上一步」可以退回的內容（最多 UNDO_MAX 個）
        self._last_text = ""
        self._typing = QTimer(self, singleShot=True, interval=1000)   # 連續打字算同一步
        self.record_host = None            # 連段頁：負責把控制器輸入轉給錄製
        outer = QVBoxLayout(self)
        outer.setContentsMargins(6, 6, 6, 6)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        scroll.setStyleSheet("QScrollArea, QScrollArea > QWidget > QWidget { background: transparent; }")
        inner = QWidget()
        scroll.setWidget(inner)
        outer.addWidget(scroll)
        box = QVBoxLayout(inner)
        box.setContentsMargins(10, 4, 10, 8)
        box.setSpacing(6)

        top = QHBoxLayout()
        self.title = theme.GlowTitle("編輯第 1 列", 11)
        top.addWidget(self.title)
        top.addStretch(1)
        close = QPushButton("收起")
        close.clicked.connect(self.closed.emit)
        top.addWidget(close)
        box.addLayout(top)
        if mode == "move":
            self._build_move_fields(box)
        head = QHBoxLayout()
        self.insert_btn = QPushButton("在下面插入一列")
        self.insert_btn.clicked.connect(self.insert_below.emit)
        head.addWidget(self.insert_btn)
        self.delete_btn = ConfirmButton("刪除這一列", "刪除",
                                        message=lambda: f"要刪除第 {self.row + 1} 列嗎？\n刪除後沒辦法救回。")
        self.delete_btn.confirmed.connect(self.delete_row.emit)
        head.addWidget(self.delete_btn)
        head.addStretch(1)

        what = "指令（記法）" if mode == "move" else "這一列的記法"
        box.addWidget(QLabel(what))
        self.edit = QLineEdit()
        self.edit.setAccessibleName(what)
        self.edit.setFont(theme.font(12))
        self.edit.textEdited.connect(self._typed)
        self.edit.returnPressed.connect(self._enter)      # 按 Enter 就收起
        box.addWidget(self.edit)
        undo_row = QHBoxLayout()
        self.undo_btn = QPushButton("上一步")
        self.undo_btn.setToolTip(f"回到前一個樣子（最多退回 {UNDO_MAX} 次）")
        self.undo_btn.setFocusPolicy(Qt.NoFocus)
        self.undo_btn.clicked.connect(self.undo)
        self.undo_btn.setEnabled(False)
        undo_row.addWidget(self.undo_btn)
        undo_row.addStretch(1)
        box.addLayout(undo_row)
        box.addLayout(head)
        if mode == "move":
            self.insert_btn.hide()
            self.delete_btn.hide()

        rec = QHBoxLayout()
        self.record_btn = QPushButton(self._record_text())
        self.record_btn.setCheckable(True)
        self.record_btn.toggled.connect(self._record_toggled)
        rec.addWidget(self.record_btn)
        self.apply_btn = QPushButton("套用錄到的記法")
        self.apply_btn.clicked.connect(self._apply_recorded)
        self.apply_btn.setVisible(False)
        rec.addWidget(self.apply_btn)
        rec.addStretch(1)
        box.addLayout(rec)
        self.record_status = QLabel()
        self.record_status.setWordWrap(True)
        box.addWidget(self.record_status)

        self.palette_box = QVBoxLayout()
        self.palette_box.setSpacing(4)
        box.addLayout(self.palette_box)
        box.addStretch(1)
        self.rebuild_palette()
        if mode == "move":
            row = QHBoxLayout()
            row.addStretch(1)
            cancel = QPushButton("取消")
            cancel.clicked.connect(self.closed.emit)
            self.save_btn = QPushButton("儲存")
            self.save_btn.clicked.connect(self._save_move)
            row.addWidget(cancel)
            row.addWidget(self.save_btn)
            outer.addLayout(row)

    def _enter(self):
        """編輯時按 Enter：連段的一列直接收起；指令表的招式先儲存再收起。"""
        if self.mode == "move":
            self._save_move()
        else:
            self.closed.emit()

    def _record_text(self) -> str:
        return "用控制器錄指令" if self.mode == "move" else "用控制器錄這一列"

    # ---- 招式模式 ----
    def _build_move_fields(self, box):
        from ui.move_card import GAUGE_CODES, gauge_label
        self._gauge_label = gauge_label
        self._gauge_codes = GAUGE_CODES
        box.addWidget(QLabel("名稱"))
        self.f_name = QLineEdit()
        self.f_name.setAccessibleName("招式名稱")
        self.f_name.returnPressed.connect(self._enter)
        box.addWidget(self.f_name)
        box.addWidget(QLabel("備註"))
        self.f_note = QLineEdit()
        self.f_note.setAccessibleName("備註")
        self.f_note.returnPressed.connect(self._enter)
        box.addWidget(self.f_note)
        box.addWidget(QLabel("說明（留空就不顯示）"))
        self.f_desc = QPlainTextEdit()
        self.f_desc.setAccessibleName("說明")
        self.f_desc.setFixedHeight(64)
        box.addWidget(self.f_desc)
        box.addWidget(QLabel("槽消耗"))
        self.f_gauge = QComboBox()
        self.f_gauge.setAccessibleName("槽消耗")
        box.addWidget(self.f_gauge)

    def open_move(self, move: dict, cat_key: str, title: str):
        self.stop_recording()
        self._move = dict(move)
        self.title.setText(title)
        self.f_name.setText(move.get("name", ""))
        self.f_note.setText(move.get("note", ""))
        self.f_desc.setPlainText(move.get("desc", ""))
        self.f_gauge.clear()
        for code in self._gauge_codes:
            self.f_gauge.addItem(self._gauge_label(code, cat_key) or "沒有", code)
        self.f_gauge.setCurrentIndex(max(0, self.f_gauge.findData(move.get("gauge", ""))))
        self.edit.setText(move.get("input", ""))
        self._reset_history(self.edit.text())
        self.record_status.setText("")
        self.apply_btn.setVisible(False)
        self.show()
        self.f_name.setFocus()

    def _save_move(self):
        text = self.edit.text().strip()
        errors = parse(text).errors if text else []     # 自動發動的招式可以沒有指令
        if not text and not self.f_name.text().strip():
            errors = ["名稱和指令至少要填一個"]
        if errors:
            self.record_status.setText("指令看不懂：" + "　".join(errors))
            return
        out = {k: v for k, v in self._move.items() if k in ("children",)}
        out["name"] = self.f_name.text().strip() or text
        out["input"] = normalize_simul(text)
        if self.f_note.text().strip():
            out["note"] = self.f_note.text().strip()
        if self.f_desc.toPlainText().strip():
            out["desc"] = self.f_desc.toPlainText().strip()
        gauge = self.f_gauge.currentData() or ""
        if gauge:
            out["gauge"] = gauge
            if gauge.startswith("s"):
                out["level"] = int(gauge[1:])
        self.stop_recording()
        self.saved.emit(out)

    # ---- 按鈕面板 ----
    def _section(self, title: str) -> FlowLayout:
        label = QLabel(title)
        label.setFont(theme.font(9.5, True))
        self.palette_box.addWidget(label)
        holder = FlowHolder()
        self.palette_box.addWidget(holder)
        return holder.flow

    def _chip(self, flow: FlowLayout, text: str, notation: str, tip: str = "") -> QPushButton:
        b = QPushButton(text)
        b.setFont(theme.font(10, True))
        b.setFixedSize(b.fontMetrics().horizontalAdvance(text) + 30, 34)
        b.setToolTip(tip or f"加入 {notation}")
        b.setFocusPolicy(Qt.NoFocus)          # 點按鈕時游標留在文字框
        b.clicked.connect(lambda _=False, n=notation: self.insert(n))
        flow.addWidget(b)
        return b

    def rebuild_palette(self):
        while self.palette_box.count():
            it = self.palette_box.takeAt(0)
            if it.widget():
                it.widget().deleteLater()
        flow = self._section("動作")
        for a in all_actions(self.cfg):
            self._chip(flow, a["code"], a["code"], action_title(a))
        for code in EXTRA_BUTTONS:
            self._chip(flow, code, code)
        for n, label in MARKS:
            self._chip(flow, label, n)
        flow = self._section("方向")
        for n, arrow in DIRECTIONS:
            self._chip(flow, arrow, n, f"加入 {arrow}（{n}）")
        flow = self._section("常用搓招")
        for n, arrows in MOTIONS:
            self._chip(flow, arrows, n, f"加入 {arrows}（{n}）")
        flow = self._section("自訂庫（在自訂按鈕上按右鍵可以修改或刪除）")
        for i, item in enumerate(snippets.load()):
            b = self._chip(flow, item["name"], item["notation"], f"加入 {item['notation']}")
            b.setContextMenuPolicy(Qt.CustomContextMenu)
            b.customContextMenuRequested.connect(lambda _p, i=i, b=b: self._snippet_menu(i, b))
        add = QPushButton("＋ 加到自訂庫")
        add.setFixedSize(add.fontMetrics().horizontalAdvance(add.text()) + 36, 34)
        add.setFocusPolicy(Qt.NoFocus)
        add.clicked.connect(self._add_snippet)
        flow.addWidget(add)

    def insert(self, notation: str):
        self._snapshot(self.edit.text())
        self.edit.insert(notation)
        self.edit.setFocus()
        self._last_text = self.edit.text()
        self.text_changed.emit(self.edit.text())

    # ---- 上一步 ----
    def _snapshot(self, text: str):
        """改之前先記住原本的內容，「上一步」就能退回去。"""
        if self._history and self._history[-1] == text:
            return
        self._history.append(text)
        del self._history[:-UNDO_MAX]
        self.undo_btn.setEnabled(True)

    def _typed(self, text: str):
        if not self._typing.isActive():
            self._snapshot(self._last_text)
        self._typing.start()
        self._last_text = text
        self.text_changed.emit(text)

    def _set_text(self, text: str):
        """整個換掉（錄到的內容、上一步）並更新畫面。"""
        self.edit.setText(text)
        self._last_text = text
        self.text_changed.emit(text)

    def undo(self):
        if not self._history:
            return
        self._typing.stop()
        self._set_text(self._history.pop())
        self.undo_btn.setEnabled(bool(self._history))
        self.edit.setFocus()

    def _reset_history(self, text: str):
        self._history.clear()
        self._last_text = text
        self._typing.stop()
        self.undo_btn.setEnabled(False)

    # ---- 自訂庫 ----
    def _add_snippet(self):
        dlg = SnippetDialog("", self.edit.selectedText() or "", self, self.record_host)
        if dlg.exec() == QDialog.Accepted:
            items = snippets.load()
            items.append(dlg.values())
            snippets.save(items)
            self.rebuild_palette()

    def _snippet_menu(self, index: int, button: QPushButton):
        menu = QMenu(self)
        edit = menu.addAction("修改")
        remove = menu.addAction("刪除")
        chosen = menu.exec(button.mapToGlobal(button.rect().bottomLeft()))
        items = snippets.load()
        if not (0 <= index < len(items)):
            return
        if chosen is edit:
            dlg = SnippetDialog(items[index]["name"], items[index]["notation"], self, self.record_host)
            if dlg.exec() == QDialog.Accepted:
                items[index] = dlg.values()
                snippets.save(items)
                self.rebuild_palette()
        elif chosen is remove:
            if confirm(self.window(), "刪除自訂按鈕",
                       f"要從自訂庫刪除「{items[index]['name']}」嗎？\n刪除後沒辦法救回。"):
                items.pop(index)
                snippets.save(items)
                self.rebuild_palette()

    # ---- 列 ----
    def open_row(self, row: int, text: str):
        self.stop_recording()
        self.row = row
        self.title.setText(f"編輯第 {row + 1} 列")
        self.edit.setText(text)
        self._reset_history(text)
        self.record_status.setText("")
        self.apply_btn.setVisible(False)
        self.show()
        self.edit.setFocus()

    # ---- 控制器錄製 ----
    def _record_toggled(self, on: bool):
        self.record_btn.setText("停止錄製" if on else self._record_text())
        if on:
            self.apply_btn.setVisible(False)
            what = "這一招" if self.mode == "move" else "這一列"
            self.record_status.setText(f"錄製中：照著打一次{what}，停手 2 秒會自動結束。")
        self.record_toggled.emit(on)

    def stop_recording(self):
        if self.record_btn.isChecked():
            self.record_btn.setChecked(False)

    def set_recorded(self, text: str):
        """錄製結束時由連段頁呼叫。"""
        self._recorded = text
        self.record_btn.blockSignals(True)
        self.record_btn.setChecked(False)
        self.record_btn.setText(self._record_text())
        self.record_btn.blockSignals(False)
        if text:
            # 錄到的直接填進去，畫面馬上更新；不對就按「上一步」
            self._snapshot(self.edit.text())
            self._set_text(text)
            self.record_status.setText("")
        else:
            self.record_status.setText("沒有錄到按鍵。請確認控制器有連上，按鍵也有設定動作。")

    def _apply_recorded(self):
        self.edit.setText(self._recorded)
        self.apply_btn.setVisible(False)
        self.record_status.setText("已套用。")
        self.text_changed.emit(self._recorded)
