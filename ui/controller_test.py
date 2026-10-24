"""控制器分頁：即時顯示方向與按鍵，確認控制器讀得到。"""
from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (QComboBox, QHBoxLayout, QLabel, QPushButton, QSlider,
                               QVBoxLayout, QWidget)

from core.xinput_reader import InputEvent, XInputReader, held_inputs
from core.key_resolver import KeyResolver
from data.config import BASE_ACTIONS, action_name
from ui import theme
from ui.controller_setup import KeySetupDialog
from ui.key_canvas import KeyCanvas
from ui.pad_widgets import DirectionWidget

SOURCE_OPTIONS = [("both", "十字鍵＋左搖桿"), ("dpad", "只用十字鍵"), ("stick", "只用左搖桿")]


class Panel(theme.GlassPanel):
    def __init__(self, title: str, parent=None):
        super().__init__(parent)
        self.box = QVBoxLayout(self)
        self.box.setContentsMargins(16, 10, 16, 14)
        self.box.setSpacing(8)
        self.box.addWidget(theme.GlowTitle(title, 11.5))


class ControllerPage(QWidget):
    """讀取器由主視窗擁有；主視窗收到事件後交給這一頁。"""
    config_changed = Signal()     # 設定改了，請主視窗存檔
    layout_changed = Signal()     # 按鍵版面改了，其他分頁要跟著更新

    def __init__(self, cfg: dict, reader: XInputReader, parent=None):
        super().__init__(parent)
        self.cfg = cfg
        self.reader = reader
        self.resolver = KeyResolver(cfg["layout"], cfg.get("combo_window_ms", 12))

        self._build()
        self.apply_theme()
        if not self.reader.available:
            self._set_status("這台電腦找不到 Windows 的控制器元件（XInput），無法讀取控制器。", "warn")

    # ---- 介面 ----
    def _build(self):
        outer = QVBoxLayout(self)
        outer.setContentsMargins(20, 16, 20, 16)
        outer.setSpacing(12)

        top = QHBoxLayout()
        title = theme.GlowTitle("控制器測試", 14)
        title.setFixedWidth(150)
        top.addWidget(title)
        top.addSpacing(12)
        self.status = QLabel()
        self.status.setFont(theme.font(10, True))
        top.addWidget(self.status)
        top.addStretch(1)
        self.rate = QLabel()
        self.rate.setFont(theme.font(9))
        top.addWidget(self.rate)
        outer.addLayout(top)

        row = QVBoxLayout()
        row.setSpacing(12)
        dir_panel = Panel("方向")
        self.dir_widget = DirectionWidget()
        self.dir_widget.setFixedWidth(420)
        top_row = QHBoxLayout()
        top_row.addWidget(self.dir_widget)
        top_row.addSpacing(24)
        side = QVBoxLayout()
        side.setSpacing(10)
        side.addStretch(1)
        opts = QHBoxLayout()
        lbl = QLabel("方向來源")
        opts.addWidget(lbl)
        self.source = QComboBox()
        self.source.setAccessibleName("方向來源")
        for key, text in SOURCE_OPTIONS:
            self.source.addItem(text, key)
        self.source.setCurrentIndex(max(0, [k for k, _ in SOURCE_OPTIONS].index(
            self.cfg.get("direction_source", "both"))))
        self.source.currentIndexChanged.connect(self._settings_changed)
        opts.addWidget(self.source)
        opts.addStretch(1)
        side.addLayout(opts)
        opts = QHBoxLayout()
        opts.addWidget(QLabel("搖桿死區"))
        self.deadzone = QSlider(Qt.Horizontal)
        self.deadzone.setAccessibleName("搖桿死區")
        self.deadzone.setRange(10, 90)
        self.deadzone.setValue(int(self.cfg.get("stick_deadzone", 0.5) * 100))
        self.deadzone.valueChanged.connect(self._settings_changed)
        self.deadzone.setMaximumWidth(320)
        opts.addWidget(self.deadzone, 1)
        self.deadzone_value = QLabel()
        opts.addWidget(self.deadzone_value)
        side.addLayout(opts)
        side.addStretch(1)
        top_row.addLayout(side, 1)
        dir_panel.box.addLayout(top_row)
        row.addWidget(dir_panel)

        btn_panel = Panel("按鍵（下方小字是對應的遊戲動作）")
        self.map_btn = QPushButton("按鍵設定")
        self.map_btn.clicked.connect(self._open_button_map)
        btn_panel.box.itemAt(0).widget().setParent(None)
        head = QHBoxLayout()
        head.addWidget(theme.GlowTitle("按鍵", 11.5), 1)
        head_note = QLabel("下方小字是對應的遊戲動作")
        head_note.setObjectName("muted")
        head.addWidget(head_note)
        head.addSpacing(8)
        head.addStretch(1)
        head.addWidget(self.map_btn)
        btn_panel.box.insertLayout(0, head)
        self.key_canvas = KeyCanvas()
        self.key_canvas.action_label = self._action_short
        self.key_canvas.setMinimumHeight(240)
        self.key_canvas.set_layout(self.cfg["layout"])
        btn_panel.box.addWidget(self.key_canvas, 1)
        self.trigger_info = QLabel()
        self.trigger_info.setFont(theme.font(8.5))
        self.trigger_info.setObjectName("muted")
        btn_panel.box.addWidget(self.trigger_info)
        row.addWidget(btn_panel, 1)
        outer.addLayout(row, 1)

        self._update_deadzone_label()

    def apply_theme(self):
        t = theme.colors()
        self.rate.setStyleSheet(f"color: {t['muted']};")
        self._refresh_status()
        self.update()

    def _set_status(self, text: str, tone: str):
        self.status.setText(text)
        self.status.setStyleSheet(f"color: {theme.colors()[tone]};")

    def _refresh_status(self):
        if not self.reader.available:
            return
        if self.reader.state.connected:
            self._set_status(f"● 已連接：控制器 {self.reader.controller_index + 1}", "ok")
        else:
            self._set_status("○ 沒有偵測到控制器，請接上 Xbox 控制器", "warn")

    def _update_deadzone_label(self):
        self.deadzone_value.setText(f"{self.deadzone.value()}%")

    # ---- 動作 ----
    def _settings_changed(self):
        self.cfg["direction_source"] = self.source.currentData()
        self.cfg["stick_deadzone"] = self.deadzone.value() / 100
        self.reader.apply_settings(self.cfg)
        self._update_deadzone_label()
        self.config_changed.emit()

    def _open_button_map(self):
        dialog = KeySetupDialog(self.cfg, self.reader, self)
        if dialog.exec() == KeySetupDialog.Accepted:
            self.cfg.update(dialog.values())
            self.resolver.set_layout(self.cfg["layout"])
            self.key_canvas.set_layout(self.cfg["layout"])
            self.config_changed.emit()
            self.layout_changed.emit()

    def _action_short(self, code: str) -> str:
        if any(a["code"] == code for a in BASE_ACTIONS):
            return code
        return action_name(self.cfg, code)

    def tick(self, now: float):
        """主視窗每 16 毫秒呼叫一次（事件已先用 handle_event 送進來）。"""
        self.resolver.flush(now)
        self._refresh_status()
        if not self.isVisible():
            return
        st = self.reader.state
        self.dir_widget.update_state(st, self.reader.deadzone)
        lit = set()
        if st.connected:
            lit = self.resolver.lit_ids(held_inputs(st))
        self.key_canvas.set_lit(lit)
        self.trigger_info.setText(f"LT {st.lt:>3}／255　RT {st.rt:>3}／255" if st.connected else "")
        hz = self.reader.measured_hz
        self.rate.setText(f"每秒讀取 {hz:.0f} 次" if hz else "")

    def handle_event(self, ev: InputEvent):
        """主視窗送來的事件；這一頁只即時顯示狀態，不需要逐筆處理。"""
        if ev.kind in ("press", "release"):
            self.resolver.feed(ev)
