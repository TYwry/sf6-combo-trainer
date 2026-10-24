"""按鍵設定：自己排按鍵版面（可新增、拖曳、組合按鍵），並把每顆鍵對應到遊戲動作。"""
from __future__ import annotations

import copy

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtWidgets import (QComboBox, QDialog, QFrame, QGridLayout, QHBoxLayout, QLabel, QLineEdit,
                               QListWidget, QListWidgetItem, QMessageBox, QPushButton,
                               QVBoxLayout)

from data.config import BASE_ACTIONS, TEMPLATE_NAMES, action_title, make_layout, new_key
from core.key_resolver import KeyResolver
from core.xinput_reader import held_inputs
from ui import theme
from ui.key_canvas import KeyCanvas, key_title

RECORD_TIMEOUT_MS = 8000


class ConfirmButton(QPushButton):
    """刪除用按鈕：按下後跳出確認視窗，「取消」是預設按鈕，按 Enter 或 Esc 都不會刪除。

    message 可以是文字或回傳文字的函式（例如要寫出目前選到的名稱）。"""
    confirmed = Signal()

    def __init__(self, text: str, confirm_text: str = "", parent=None, message=None):
        super().__init__(text, parent)
        self._text = text
        self._action = confirm_text if confirm_text and len(confirm_text) <= 6 else text
        self.message = message
        self.clicked.connect(self._on_click)

    def _on_click(self):
        msg = self.message() if callable(self.message) else self.message
        if confirm(self.window(), self._text, msg or f"確定要{self._text}嗎？", self._action):
            self.confirmed.emit()


def confirm(parent, title: str, message: str, action: str = "刪除") -> bool:
    """二次確認視窗：按鈕寫「取消」和動作名稱（紅色），預設是取消。"""
    box = QMessageBox(parent)
    box.setIcon(QMessageBox.Warning)
    box.setWindowTitle(title)
    box.setText(message)
    ok = box.addButton(action, QMessageBox.DestructiveRole)
    ok.setProperty("danger", True)
    cancel = box.addButton("取消", QMessageBox.RejectRole)
    box.setDefaultButton(cancel)
    box.setEscapeButton(cancel)
    box.exec()
    return box.clickedButton() is ok


def button_qss() -> str:
    """外觀已改由 theme.SFStyle 與全程式樣式表處理，這裡保留給舊呼叫用。"""
    return ""


class ActionPicker(QDialog):
    """按鈕面板：用滑鼠點一下，選這顆鍵在遊戲裡的用途。"""

    def __init__(self, title: str, actions: list[dict], current: str = "", parent=None):
        super().__init__(parent)
        self.setWindowTitle("選擇這顆鍵的用途")
        self.chosen: str | None = None
        box = QVBoxLayout(self)
        box.setContentsMargins(20, 16, 20, 16)
        box.setSpacing(10)
        head = QLabel(f"「{title}」在遊戲裡是做什麼用的？點一下選擇。")
        head.setFont(theme.font(11, True))
        head.setWordWrap(True)
        box.addWidget(head)
        grid = QGridLayout()
        grid.setSpacing(8)
        items = [{"code": "", "name": "未設定"}] + actions
        for i, a in enumerate(items):
            b = QPushButton(action_title(a) if a["code"] else a["name"])
            b.setMinimumHeight(44)
            b.setFont(theme.font(10.5, True))
            if a["code"] == current:
                b.setText("✔ " + b.text())
            b.clicked.connect(lambda _=False, c=a["code"]: self._pick(c))
            grid.addWidget(b, i // 3, i % 3)
        box.addLayout(grid)
        bar = QHBoxLayout()
        bar.addStretch(1)
        later = QPushButton("先不設定")
        later.clicked.connect(self.reject)
        bar.addWidget(later)
        box.addLayout(bar)
        self.setMinimumWidth(560)

    def _pick(self, code: str):
        self.chosen = code
        self.accept()


class CustomActionsDialog(QDialog):
    """管理自訂動作（例如投擲、OD 拳）。"""

    def __init__(self, actions: list[dict], parent=None):
        super().__init__(parent)
        self.setWindowTitle("自訂動作")
        self.setMinimumWidth(380)
        self.actions = copy.deepcopy(actions)
        t = theme.colors()
        box = QVBoxLayout(self)
        box.setContentsMargins(20, 16, 20, 16)
        info = QLabel("基本的 8 個動作（輕拳～DI 動力衝擊）固定存在。這裡可以再加你在遊戲裡設定的快捷動作。")
        info.setWordWrap(True)
        info.setStyleSheet(f"color: {t['muted']};")
        box.addWidget(info)
        self.list = QListWidget()
        box.addWidget(self.list)
        box.addWidget(QLabel("動作名稱"))
        self.name = QLineEdit()
        self.name.setAccessibleName("動作名稱")
        box.addWidget(self.name)
        row = QHBoxLayout()
        add = QPushButton("新增")
        add.clicked.connect(self._add)
        rename = QPushButton("改名")
        rename.clicked.connect(self._rename)
        self.remove = ConfirmButton("移除", "移除",
                                    message="要移除這個自訂動作嗎？用到它的按鍵會變成沒有動作。")
        self.remove.confirmed.connect(self._remove)
        row.addWidget(add)
        row.addWidget(rename)
        row.addWidget(self.remove)
        row.addStretch(1)
        box.addLayout(row)
        self.note = QLabel()
        self.note.setStyleSheet(f"color: {t['muted']};")
        box.addWidget(self.note)
        bar = QHBoxLayout()
        bar.addStretch(1)
        cancel = QPushButton("取消")
        cancel.clicked.connect(self.reject)
        ok = QPushButton("完成")
        ok.setDefault(True)
        ok.clicked.connect(self.accept)
        bar.addWidget(cancel)
        bar.addWidget(ok)
        box.addLayout(bar)
        self.setStyleSheet(button_qss())
        self.list.currentRowChanged.connect(self._on_select)
        self._fill()

    def _fill(self):
        self.list.clear()
        for a in self.actions:
            self.list.addItem(QListWidgetItem(a["name"]))
        if not self.actions:
            self.note.setText("還沒有自訂動作。在上面打名稱後按「新增」。")

    def _on_select(self, row):
        if 0 <= row < len(self.actions):
            self.name.setText(self.actions[row]["name"])

    def _next_code(self) -> str:
        used = {a["code"] for a in self.actions}
        n = 1
        while f"C{n}" in used:
            n += 1
        return f"C{n}"

    def _add(self):
        name = self.name.text().strip()
        if not name:
            self.note.setText("請先在「動作名稱」打字。")
            return
        self.actions.append({"code": self._next_code(), "name": name})
        self.name.clear()
        self.note.setText(f"已新增「{name}」。")
        self._fill()

    def _rename(self):
        row = self.list.currentRow()
        name = self.name.text().strip()
        if row < 0 or not name:
            self.note.setText("請先在清單選一個動作，並在「動作名稱」打新名字。")
            return
        self.actions[row]["name"] = name
        self._fill()
        self.list.setCurrentRow(row)

    def _remove(self):
        row = self.list.currentRow()
        if row < 0:
            self.note.setText("請先在清單選一個動作。")
            return
        name = self.actions.pop(row)["name"]
        self.note.setText(f"已移除「{name}」，用到它的按鍵會改成未設定。")
        self._fill()


class KeySetupDialog(QDialog):
    def __init__(self, cfg: dict, reader, parent=None):
        super().__init__(parent)
        self.setWindowTitle("按鍵設定")
        self.reader = reader
        self.keys: list[dict] = copy.deepcopy(cfg.get("layout") or [])
        self.custom_actions: list[dict] = copy.deepcopy(cfg.get("custom_actions", []))
        self.template = cfg.get("layout_template", "xbox")
        self.recording: str | None = None     # "new" 或按鍵 id
        self.captured: set[str] = set()
        self.started = False
        self._build()
        self._refresh_actions()
        self._select("")
        self.record_timer = QTimer(self, interval=10)
        self.record_timer.timeout.connect(self._poll_record)
        self.record_deadline = QTimer(self, singleShot=True, interval=RECORD_TIMEOUT_MS)
        self.record_deadline.timeout.connect(lambda: self._stop_record("太久沒有按鍵，已取消錄製。"))
        # 設定期間按下控制器的按鍵，畫面上對應的鍵會亮
        self.lit_timer = QTimer(self, interval=16)
        self.lit_timer.timeout.connect(self._update_lit)
        self.lit_timer.start()

    def done(self, result):
        self.lit_timer.stop()
        self.record_timer.stop()
        super().done(result)

    def _update_lit(self):
        st = self.reader.state if self.reader else None
        if not st or not st.connected:
            self.canvas.set_lit(set())
            return
        self.canvas.set_lit(KeyResolver(self.keys).lit_ids(held_inputs(st)))

    # ---- 介面 ----
    def _build(self):
        t = theme.colors()
        outer = QVBoxLayout(self)
        outer.setContentsMargins(22, 18, 22, 18)
        outer.setSpacing(12)
        outer.addWidget(theme.GlowTitle("按鍵設定", 15))
        hint = QLabel("拖曳按鍵排成跟你的控制器一樣；點一下按鍵可以改名稱和對應的遊戲動作。"
                      "虛線框的是組合按鍵（同一瞬間收到好幾個訊號）。")
        hint.setWordWrap(True)
        hint.setStyleSheet(f"color: {t['muted']};")
        outer.addWidget(hint)

        tools = QHBoxLayout()
        self.add_btn = QPushButton("新增按鍵")
        self.add_btn.clicked.connect(lambda: self._start_record("new"))
        tools.addWidget(self.add_btn)
        tools.addSpacing(12)
        tools.addWidget(QLabel("範本"))
        self.template_box = QComboBox()
        self.template_box.setAccessibleName("範本")
        for key, name in TEMPLATE_NAMES.items():
            self.template_box.addItem(name, key)
        self.template_box.setCurrentIndex(max(0, list(TEMPLATE_NAMES).index(self.template)
                                              if self.template in TEMPLATE_NAMES else 0))
        tools.addWidget(self.template_box)
        apply_btn = QPushButton("套用範本")
        apply_btn.clicked.connect(self._apply_template)
        tools.addWidget(apply_btn)
        tools.addStretch(1)
        actions_btn = QPushButton("自訂動作")
        actions_btn.clicked.connect(self._edit_actions)
        tools.addWidget(actions_btn)
        outer.addLayout(tools)

        body = QHBoxLayout()
        body.setSpacing(14)
        self.canvas = KeyCanvas(editable=True)
        self.canvas.set_layout(self.keys)
        self.canvas.selected.connect(self._clicked_key)
        self._asked: set[str] = set()
        body.addWidget(self.canvas, 0, Qt.AlignTop)

        side = theme.GlassPanel()
        side.setFixedWidth(320)
        sb = QVBoxLayout(side)
        sb.setContentsMargins(16, 14, 16, 14)
        sb.setSpacing(6)
        self.side_title = QLabel()
        self.side_title.setFont(theme.font(11, True))
        self.side_title.setWordWrap(True)
        sb.addWidget(self.side_title)
        self.fields = QFrame()
        fb = QVBoxLayout(self.fields)
        fb.setContentsMargins(0, 4, 0, 0)
        fb.setSpacing(6)
        fb.addWidget(QLabel("顯示名稱"))
        self.label_edit = QLineEdit()
        self.label_edit.setAccessibleName("顯示名稱")
        self.label_edit.textEdited.connect(self._label_changed)
        fb.addWidget(self.label_edit)
        small = QLabel("留空就顯示訊號名稱")
        small.setFont(theme.font(8.5))
        small.setStyleSheet(f"color: {t['muted']};")
        fb.addWidget(small)
        fb.addSpacing(6)
        fb.addWidget(QLabel("對應的控制器訊號"))
        sig = QHBoxLayout()
        self.inputs_value = QLabel()
        self.inputs_value.setFont(theme.font(10.5, True))
        sig.addWidget(self.inputs_value, 1)
        self.rerecord = QPushButton("重新錄製")
        self.rerecord.clicked.connect(lambda: self._start_record(self.canvas.selected_id))
        sig.addWidget(self.rerecord)
        fb.addLayout(sig)
        fb.addSpacing(6)
        fb.addWidget(QLabel("遊戲動作"))
        self.action_box = QComboBox()
        self.action_box.setAccessibleName("遊戲動作")
        self.action_box.currentIndexChanged.connect(self._action_changed)
        fb.addWidget(self.action_box)
        pick = QPushButton("用按鈕面板選")
        pick.clicked.connect(self._pick_action)
        fb.addWidget(pick)
        fb.addSpacing(6)
        fb.addWidget(QLabel("形狀"))
        self.shape_box = QComboBox()
        self.shape_box.setAccessibleName("形狀")
        self.shape_box.addItem("圓形", "round")
        self.shape_box.addItem("方形", "rect")
        self.shape_box.currentIndexChanged.connect(self._shape_changed)
        fb.addWidget(self.shape_box)
        fb.addSpacing(10)
        self.remove_btn = ConfirmButton("移除這顆鍵", "移除", message="要從版面移除這顆鍵嗎？")
        self.remove_btn.confirmed.connect(self._remove_key)
        fb.addWidget(self.remove_btn)
        sb.addWidget(self.fields)
        sb.addStretch(1)
        body.addWidget(side)
        outer.addLayout(body)

        self.prompt = QLabel()
        self.prompt.setWordWrap(True)
        self.prompt.setMinimumHeight(36)
        self.prompt.setFont(theme.font(10.5, True))
        self.prompt.setStyleSheet(f"color: {t['accent']};")
        outer.addWidget(self.prompt)

        bar = QHBoxLayout()
        bar.addStretch(1)
        cancel = QPushButton("取消")
        cancel.clicked.connect(self.reject)
        save = QPushButton("儲存")
        save.setDefault(True)
        save.clicked.connect(self.accept)
        bar.addWidget(cancel)
        bar.addWidget(save)
        outer.addLayout(bar)
        self.setStyleSheet(button_qss())

    def _action_label(self, code: str) -> str:
        for a in BASE_ACTIONS:
            if a["code"] == code:
                return code
        for a in self.custom_actions:
            if a["code"] == code:
                return a["name"]
        return ""

    def _refresh_actions(self):
        self.canvas.action_label = self._action_label
        self.action_box.blockSignals(True)
        self.action_box.clear()
        self.action_box.addItem("未設定", "")
        for a in BASE_ACTIONS:
            self.action_box.addItem(action_title(a), a["code"])
        for a in self.custom_actions:
            self.action_box.addItem(a["name"], a["code"])
        self.action_box.blockSignals(False)

    def _current(self) -> dict | None:
        return next((k for k in self.keys if k["id"] == self.canvas.selected_id), None)

    def _select(self, key_id: str):
        self.canvas.selected_id = key_id
        self.canvas._recenter()
        key = self._current()
        self.fields.setVisible(key is not None)
        if key is None:
            self.side_title.setText("點一下左邊的按鍵來編輯")
            self.canvas.update()
            return
        self.side_title.setText(f"編輯：{key_title(key)}")
        self.label_edit.setText(key.get("label", ""))
        self.inputs_value.setText(" + ".join(key["inputs"]))
        self.action_box.blockSignals(True)
        idx = self.action_box.findData(key.get("action", ""))
        self.action_box.setCurrentIndex(max(0, idx))
        self.action_box.blockSignals(False)
        self.shape_box.blockSignals(True)
        self.shape_box.setCurrentIndex(0 if key.get("shape", "round") == "round" else 1)
        self.shape_box.blockSignals(False)
        self.canvas.update()

    # ---- 編輯 ----
    def _label_changed(self, text):
        key = self._current()
        if key:
            key["label"] = text.strip()
            self.side_title.setText(f"編輯：{key_title(key)}")
            self.canvas.update()

    def _action_changed(self):
        key = self._current()
        if not key:
            return
        code = self.action_box.currentData()
        note = ""
        if code:
            for other in self.keys:
                if other is not key and other.get("action") == code:
                    other["action"] = ""
                    note = f"原本設成這個動作的「{key_title(other)}」已改成未設定。"
        key["action"] = code
        self.prompt.setText(note)
        self.canvas.update()

    def _clicked_key(self, key_id: str):
        self._select(key_id)
        key = self._current()
        # 還沒設用途的組合按鍵（快捷鍵）：點到時跳出按鈕面板，每顆只問一次
        if key and len(key["inputs"]) > 1 and not key.get("action") and key_id not in self._asked:
            self._asked.add(key_id)
            QTimer.singleShot(0, self._pick_action)

    def _pick_action(self):
        key = self._current()
        if not key:
            return
        dlg = ActionPicker(key_title(key), BASE_ACTIONS + self.custom_actions, key.get("action", ""), self)
        if dlg.exec() == QDialog.Accepted and dlg.chosen is not None:
            idx = self.action_box.findData(dlg.chosen)
            self.action_box.setCurrentIndex(max(0, idx))   # 會觸發 _action_changed

    def _shape_changed(self):
        key = self._current()
        if key:
            key["shape"] = self.shape_box.currentData()
            self.canvas.update()

    def _remove_key(self):
        key = self._current()
        if key:
            self.keys.remove(key)
            self.prompt.setText(f"已移除「{key_title(key)}」，按「取消」可以放棄這次的修改。")
            self._select("")

    def _apply_template(self):
        tpl = self.template_box.currentData()
        box = QMessageBox(self)
        box.setWindowTitle("套用範本")
        box.setIcon(QMessageBox.Warning)
        box.setText(f"要用「{TEMPLATE_NAMES[tpl]}」範本取代目前的 {len(self.keys)} 顆按鍵嗎？\n"
                    "目前的位置、名稱、組合按鍵和對應都會被換掉。\n"
                    "還沒按「儲存」之前，按設定視窗的「取消」可以放棄。")
        go = box.addButton("取代", QMessageBox.DestructiveRole)
        cancel = box.addButton("取消", QMessageBox.RejectRole)
        box.setDefaultButton(cancel)
        box.setEscapeButton(cancel)
        box.exec()
        if box.clickedButton() is go:
            self.template = tpl
            self.keys[:] = make_layout(tpl)
            self.prompt.setText(f"已套用「{TEMPLATE_NAMES[tpl]}」範本，對應是預設值，請再檢查一次。")
            self._select("")

    def _edit_actions(self):
        dlg = CustomActionsDialog(self.custom_actions, self)
        if dlg.exec() == QDialog.Accepted:
            self.custom_actions = dlg.actions
            valid = {a["code"] for a in BASE_ACTIONS + self.custom_actions}
            for k in self.keys:
                if k.get("action") and k["action"] not in valid:
                    k["action"] = ""
            self._refresh_actions()
            self._select(self.canvas.selected_id)

    # ---- 錄製控制器訊號 ----
    def _start_record(self, target: str):
        if not self.reader.state.connected:
            self.prompt.setText("沒有偵測到控制器，接上後再試一次。")
            return
        self.recording = target
        self.captured = set()
        self.started = False
        self.add_btn.setEnabled(False)
        self.rerecord.setEnabled(False)
        self.prompt.setText("請按下控制器上的那顆鍵（巨集鍵直接按那一顆就好），放開後完成。按 Esc 取消。")
        self.record_timer.start()
        self.record_deadline.start()

    def _poll_record(self):
        held = held_inputs(self.reader.state)
        if held:
            self.started = True
            self.captured |= set(held)
        elif self.started:
            self._finish_record()

    def _stop_record(self, message: str):
        self.record_timer.stop()
        self.record_deadline.stop()
        self.recording = None
        self.add_btn.setEnabled(True)
        self.rerecord.setEnabled(True)
        self.prompt.setText(message)

    def _finish_record(self):
        inputs = sorted(self.captured)
        target = self.recording
        same = next((k for k in self.keys if sorted(k["inputs"]) == inputs and k["id"] != target), None)
        if same:
            self._stop_record(f"已經有一顆按鍵「{key_title(same)}」用這組訊號（{' + '.join(inputs)}），已幫你選取它。")
            self._select(same["id"])
            return
        if target == "new":
            x, y = self.canvas.free_spot()
            key = new_key(inputs, x, y)
            self.keys.append(key)
            kind = "組合按鍵" if len(inputs) > 1 else "按鍵"
            self._stop_record(f"已新增{kind}：{' + '.join(inputs)}。可以拖曳到正確位置，再選遊戲動作。")
            self._select(key["id"])
            if len(inputs) > 1:
                QTimer.singleShot(0, self._pick_action)     # 組合按鍵（快捷鍵）：直接跳出按鈕面板選用途
        else:
            key = next((k for k in self.keys if k["id"] == target), None)
            if key:
                key["inputs"] = inputs
                self._stop_record(f"已改成：{' + '.join(inputs)}。")
                self._select(key["id"])
                if len(inputs) > 1:
                    QTimer.singleShot(0, self._pick_action)
            else:
                self._stop_record("")

    def keyPressEvent(self, event):
        if event.key() == Qt.Key_Escape and self.recording:
            self._stop_record("已取消錄製。")
            return
        super().keyPressEvent(event)

    def values(self) -> dict:
        return {"layout": self.keys, "custom_actions": self.custom_actions,
                "layout_template": self.template}
