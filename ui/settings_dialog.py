"""設定視窗：遊戲 mod、快捷鍵、控制器快捷鍵、按 ✕ 的行為、開機自動啟動、浮動視窗外觀、桌面捷徑。"""
from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QEvent, Qt, Signal
from PySide6.QtGui import QGuiApplication, QKeySequence
from PySide6.QtWidgets import (QAbstractSpinBox, QApplication, QCheckBox, QComboBox, QDialog, QDoubleSpinBox, QFileDialog, QGridLayout, QHBoxLayout, QKeySequenceEdit, QLineEdit,
                               QLabel, QPushButton, QRadioButton, QScrollArea, QSlider, QVBoxLayout,
                               QButtonGroup, QFrame, QMessageBox)

from core import autostart, game_bridge, hotkeys
from data.config import DEFAULT_HOTKEYS, DEFAULT_PAD_HOTKEYS, HOTKEY_ACTIONS
from ui import theme
from ui.controller_setup import ConfirmButton
from ui.overlay import ROW_CHOICES, ZOOM_CHOICES

PAD_CHOICES = [("", "（不用）"), ("UP", "↑ 上"), ("DOWN", "↓ 下"), ("LEFT", "← 左"), ("RIGHT", "→ 右"),
               ("START", "START"), ("A", "A"), ("B", "B"), ("X", "X"), ("Y", "Y"),
               ("LB", "LB"), ("RB", "RB"), ("LT", "LT"), ("RT", "RT"), ("LS", "LS"), ("RS", "RS")]


class SettingsDialog(QDialog):
    shortcut_requested = Signal()
    export_requested = Signal()
    import_requested = Signal()
    game_dir_changed = Signal(str)

    def __init__(self, cfg: dict, parent=None):
        super().__init__(parent)
        self.cfg = cfg
        self.setWindowTitle("設定")
        self.resize(760, 820)
        self._build()
        self._validate()

    # ---- 介面 ----
    def _section(self, box, title: str) -> QVBoxLayout:
        panel = theme.GlassPanel()
        inner = QVBoxLayout(panel)
        inner.setContentsMargins(18, 12, 18, 16)
        inner.setSpacing(8)
        inner.addWidget(theme.GlowTitle(title, 12))
        box.addWidget(panel)
        return inner

    def _build(self):
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 12)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        body = theme.Backdrop()
        box = QVBoxLayout(body)
        box.setContentsMargins(18, 14, 18, 14)
        box.setSpacing(12)
        scroll.setWidget(body)
        outer.addWidget(scroll, 1)
        self._scroll = scroll

        # 遊戲 mod
        self._build_game(self._section(box, "遊戲 mod"))

        # 鍵盤快捷鍵
        sec = self._section(box, "鍵盤快捷鍵（在遊戲裡也能用）")
        self.key_on = QCheckBox("使用鍵盤快捷鍵")
        self.key_on.setChecked(bool(self.cfg.get("hotkeys_enabled", True)))
        self.key_on.toggled.connect(self._key_toggled)
        sec.addWidget(self.key_on)
        grid = QGridLayout()
        grid.setHorizontalSpacing(10)
        self.key_edits: dict[str, QKeySequenceEdit] = {}
        self._key_widgets: list = []
        current = {**DEFAULT_HOTKEYS, **(self.cfg.get("hotkeys") or {})}
        for row, (code, name) in enumerate(HOTKEY_ACTIONS):
            grid.addWidget(QLabel(name), row, 0)
            edit = QKeySequenceEdit(QKeySequence(current.get(code, ""), QKeySequence.PortableText))
            edit.setAccessibleName(f"{name}的快捷鍵")
            for line in edit.findChildren(QLineEdit):   # Qt 內建的「Press shortcut」提示字拿掉
                line.setPlaceholderText("")
            if hasattr(edit, "setMaximumSequenceLength"):
                edit.setMaximumSequenceLength(1)
            edit.keySequenceChanged.connect(self._validate)
            grid.addWidget(edit, row, 1)
            clear = QPushButton("清除")
            clear.clicked.connect(lambda _=False, e=edit: e.clear())
            grid.addWidget(clear, row, 2)
            self.key_edits[code] = edit
            self._key_widgets += [edit, clear]
        grid.setColumnStretch(1, 1)
        sec.addLayout(grid)
        hint = QLabel("點一下格子，再按想要的按鍵組合。要有 Ctrl、Alt 或 Shift，或是用 F1～F12，"
                      "避免打字時誤觸。清除＝這個動作不用快捷鍵。")
        hint.setObjectName("muted")
        hint.setWordWrap(True)
        sec.addWidget(hint)
        self.key_warn = QLabel()
        self.key_warn.setWordWrap(True)
        self.key_warn.setStyleSheet(f"color: {theme.colors()['warn']};")
        sec.addWidget(self.key_warn)
        row = QHBoxLayout()
        row.addStretch(1)
        reset = ConfirmButton("恢復預設", "恢復預設", message="要把鍵盤快捷鍵恢復成預設嗎？按「儲存」後才會生效。")
        reset.confirmed.connect(self._reset_keys)
        row.addWidget(reset)
        self._key_widgets.append(reset)
        for wdg in self._key_widgets:
            wdg.setEnabled(self.key_on.isChecked())
        sec.addLayout(row)
        if not hotkeys.supported():
            sec.addWidget(QLabel("（只有 Windows 能用鍵盤快捷鍵）"))

        # 控制器快捷鍵
        sec = self._section(box, "控制器快捷鍵")
        self.pad_on = QCheckBox("按住 BACK，再按另一顆鍵來觸發動作")
        self.pad_on.setChecked(bool(self.cfg.get("pad_hotkeys_enabled")))
        self.pad_on.toggled.connect(self._pad_toggled)
        sec.addWidget(self.pad_on)
        grid = QGridLayout()
        grid.setHorizontalSpacing(10)
        self.pad_boxes: dict[str, QComboBox] = {}
        pads = {**DEFAULT_PAD_HOTKEYS, **(self.cfg.get("pad_hotkeys") or {})}
        for r, (code, name) in enumerate(HOTKEY_ACTIONS):
            grid.addWidget(QLabel(name), r, 0)
            combo = QComboBox()
            combo.setAccessibleName(f"{name}的控制器快捷鍵")
            for value, text in PAD_CHOICES:
                combo.addItem(f"BACK＋{text}" if value else text, value)
            combo.setCurrentIndex(max(0, combo.findData(pads.get(code, ""))))
            combo.currentIndexChanged.connect(self._validate)
            grid.addWidget(combo, r, 1)
            self.pad_boxes[code] = combo
        grid.setColumnStretch(2, 1)
        sec.addLayout(grid)
        hint = QLabel("單按 BACK 一樣是「重來」。按住 BACK 時按的那顆鍵不會算進連段輸入。"
                      "如果遊戲裡也用 BACK 組合鍵（例如訓練場的重置位置），先關掉這個功能或換一顆鍵。")
        hint.setObjectName("muted")
        hint.setWordWrap(True)
        sec.addWidget(hint)
        self._pad_toggled(self.pad_on.isChecked())

        # 視窗
        sec = self._section(box, "視窗")
        sec.addWidget(QLabel("按主視窗右上角的 ✕ 時"))
        self.close_tray = QRadioButton("縮到系統匣（右下角小圖示），練習和快捷鍵照常運作")
        self.close_quit = QRadioButton("直接結束程式")
        group = QButtonGroup(self)
        group.addButton(self.close_tray)
        group.addButton(self.close_quit)
        (self.close_tray if self.cfg.get("close_to_tray", True) else self.close_quit).setChecked(True)
        sec.addWidget(self.close_tray)
        sec.addWidget(self.close_quit)
        self.autostart = QCheckBox("開機時自動啟動（啟動後縮在系統匣）")
        self.autostart.setChecked(autostart.is_enabled())
        self.autostart.setEnabled(autostart.supported())
        sec.addWidget(self.autostart)
        sec.addWidget(QLabel("主視窗開啟時"))
        srow = QHBoxLayout()
        srow.addWidget(QLabel("在哪個螢幕"))
        self.screen_box = QComboBox()
        self.screen_box.setAccessibleName("主視窗在哪個螢幕開啟")
        self.screen_box.addItem("主螢幕", "")
        for i, sc in enumerate(QGuiApplication.screens(), 1):
            g = sc.geometry()
            self.screen_box.addItem(f"螢幕 {i}（{g.width()}×{g.height()}）", sc.name())
        k = self.screen_box.findData(self.cfg.get("window_screen", ""))
        self.screen_box.setCurrentIndex(max(0, k))
        srow.addWidget(self.screen_box, 1)
        sec.addLayout(srow)
        self.size_btns = {
            "1920": QRadioButton("1920×1080，放在螢幕中間（螢幕比較小就放到最大）"),
            "full": QRadioButton("全螢幕（放到最大）"),
            "last": QRadioButton("上次關閉程式之前的位置和大小"),
        }
        sgroup = QButtonGroup(self)
        for key, b in self.size_btns.items():
            sgroup.addButton(b)
            sec.addWidget(b)
            b.toggled.connect(self._size_toggled)
        self.size_btns.get(self.cfg.get("window_size", "1920"), self.size_btns["1920"]).setChecked(True)
        self._size_toggled()
        row = QHBoxLayout()
        shortcut = QPushButton("建立桌面捷徑")
        shortcut.clicked.connect(self.shortcut_requested.emit)
        row.addWidget(shortcut)
        row.addStretch(1)
        sec.addLayout(row)

        # 連段資料
        sec = self._section(box, "連段資料")
        hint = QLabel("把連段存成檔案分享或備份，或是讀進別人分享的連段檔。")
        hint.setObjectName("muted")
        hint.setWordWrap(True)
        sec.addWidget(hint)
        row = QHBoxLayout()
        export = QPushButton("匯出")
        export.clicked.connect(self.export_requested.emit)
        imp = QPushButton("匯入")
        imp.clicked.connect(self.import_requested.emit)
        row.addWidget(export, 1)
        row.addWidget(imp, 1)
        sec.addLayout(row)

        # 外觀
        sec = self._section(box, "外觀")
        sec.addWidget(QLabel("字體"))
        self.font_hand = QRadioButton("手寫風")
        self.font_jh = QRadioButton("微軟正黑體")
        fgroup = QButtonGroup(self)
        fgroup.addButton(self.font_hand)
        fgroup.addButton(self.font_jh)
        (self.font_jh if self.cfg.get("ui_font") == "jhenghei" else self.font_hand).setChecked(True)
        sec.addWidget(self.font_hand)
        sec.addWidget(self.font_jh)
        sec.addWidget(QLabel("主題"))
        self.theme_btns = {}
        tgroup = QButtonGroup(self)
        for key, label in (("light", "淺色"), ("dark", "深色"), ("system", "跟著 Windows")):
            btn = QRadioButton(label)
            tgroup.addButton(btn)
            sec.addWidget(btn)
            self.theme_btns[key] = btn
        self.theme_btns.get(self.cfg.get("ui_theme"), self.theme_btns["light"]).setChecked(True)
        note = QLabel("換字體或主題要重新開啟程式才會套用；按「儲存」後會問要不要馬上重開。")
        note.setObjectName("muted")
        sec.addWidget(note)
        self.show_numpad = QCheckBox("在連段清單下面顯示方向數字圖")
        self.show_numpad.setChecked(bool(self.cfg.get("show_numpad", True)))
        sec.addWidget(self.show_numpad)

        # 浮動視窗
        sec = self._section(box, "浮動視窗")
        grid = QGridLayout()
        grid.setHorizontalSpacing(10)
        grid.addWidget(QLabel("透明度"), 0, 0)
        self.opacity = QSlider(Qt.Horizontal)
        self.opacity.setAccessibleName("浮動視窗透明度")
        self.opacity.setRange(30, 100)
        self.opacity.setValue(round(float(self.cfg.get("overlay_opacity", 0.85)) * 100))
        grid.addWidget(self.opacity, 0, 1)
        self.opacity_label = QLabel()
        self.opacity.valueChanged.connect(lambda v: self.opacity_label.setText(f"{v}%"))
        self.opacity_label.setText(f"{self.opacity.value()}%")
        self.opacity_label.setMinimumWidth(50)
        grid.addWidget(self.opacity_label, 0, 2)
        grid.addWidget(QLabel("字的大小"), 1, 0)
        self.zoom = QComboBox()
        self.zoom.setAccessibleName("浮動視窗字的大小")
        for name, v in ZOOM_CHOICES:
            self.zoom.addItem(name, v)
        cz = float(self.cfg.get("overlay_zoom", 1.0))
        self.zoom.setCurrentIndex(min(range(len(ZOOM_CHOICES)), key=lambda i: abs(ZOOM_CHOICES[i][1] - cz)))
        grid.addWidget(self.zoom, 1, 1)
        grid.addWidget(QLabel("顯示幾列"), 2, 0)
        self.rows = QComboBox()
        self.rows.setAccessibleName("浮動視窗顯示幾列")
        for v in ROW_CHOICES:
            self.rows.addItem("全部" if v == 0 else f"{v} 列", v)
        self.rows.setCurrentIndex(max(0, self.rows.findData(int(self.cfg.get("overlay_rows_shown", 0)))))
        grid.addWidget(self.rows, 2, 1)
        grid.setColumnStretch(1, 1)
        sec.addLayout(grid)
        box.addStretch(1)

        # 按鈕
        btns = QHBoxLayout()
        btns.setContentsMargins(18, 0, 18, 0)
        btns.addStretch(1)
        cancel = QPushButton("取消")
        cancel.clicked.connect(self.reject)
        btns.addWidget(cancel)
        self.save_btn = QPushButton("儲存")
        self.save_btn.setDefault(True)
        self.save_btn.clicked.connect(self.accept)
        btns.addWidget(self.save_btn)
        outer.addLayout(btns)
        self._block_wheel(body)

    # ---- 滾輪：選單、滑桿不吃滾輪，捲動時只捲整個設定畫面，不會不小心改到值 ----
    def _block_wheel(self, root):
        for w in root.findChildren(QComboBox) + root.findChildren(QSlider) + root.findChildren(QAbstractSpinBox):
            w.setFocusPolicy(Qt.StrongFocus)
            w.installEventFilter(self)

    def eventFilter(self, obj, event):
        if event.type() == QEvent.Wheel and isinstance(obj, (QComboBox, QSlider, QAbstractSpinBox)):
            QApplication.sendEvent(self._scroll.verticalScrollBar(), event)
            return True
        return super().eventFilter(obj, event)

    # ---- 動作 ----
    def _build_game(self, sec):
        self.game_check = QCheckBox("用遊戲判定連段成功（輸入對了，遊戲的連擊數也沒斷才算）")
        self.game_check.setChecked(bool(self.cfg.get("game_check_enabled", True)))
        sec.addWidget(self.game_check)
        self.mod_notice = QCheckBox("遊戲啟動時 mod 是開著的，就跳通知提醒我")
        self.mod_notice.setChecked(bool(self.cfg.get("mod_start_notice", True)))
        sec.addWidget(self.mod_notice)

        # 連段斷了自動按重置鍵（要在遊戲的鍵盤設定把同一個鍵設成訓練模式的重置）
        row = QHBoxLayout()
        self.auto_reset = QCheckBox("連段斷了就自動重置")
        self.auto_reset.setChecked(bool(self.cfg.get("auto_reset_enabled", False)))
        self.auto_reset.setToolTip("只在訓練模式才會重置。")
        self.auto_reset.toggled.connect(self._validate)
        row.addWidget(self.auto_reset)
        self.reset_mode = QComboBox()
        self.reset_mode.addItem("遊戲直接重置（預設重置）", "game")
        self.reset_mode.addItem("播放儲存狀態", "state")
        self.reset_mode.addItem("按鍵盤按鍵", "key")
        self.reset_mode.setCurrentIndex(max(0, self.reset_mode.findData(self.cfg.get("auto_reset_mode", "game"))))
        self.reset_mode.currentIndexChanged.connect(self._validate)
        row.addWidget(self.reset_mode)
        self.reset_key = QKeySequenceEdit(QKeySequence(self.cfg.get("auto_reset_key", ""), QKeySequence.PortableText))
        self.reset_key.setAccessibleName("重置鍵")
        if hasattr(self.reset_key, "setMaximumSequenceLength"):
            self.reset_key.setMaximumSequenceLength(1)
        for line in self.reset_key.findChildren(QLineEdit):   # Qt 內建的「Press shortcut」提示字拿掉
            line.setPlaceholderText("")
        self.reset_key.keySequenceChanged.connect(self._validate)
        row.addWidget(self.reset_key, 1)
        self.reset_clear = QPushButton("清除")
        self.reset_clear.clicked.connect(self.reset_key.clear)
        row.addWidget(self.reset_clear)
        sec.addLayout(row)
        row = QHBoxLayout()
        row.addWidget(QLabel("失敗後等"))
        self.reset_delay = QDoubleSpinBox()
        self.reset_delay.setRange(0.0, 10.0)
        self.reset_delay.setSingleStep(0.1)
        self.reset_delay.setDecimals(1)
        self.reset_delay.setValue(float(self.cfg.get("auto_reset_delay_s", 0.0)))
        row.addWidget(self.reset_delay)
        row.addWidget(QLabel("秒再重置"))
        row.addStretch(1)
        sec.addLayout(row)
        self.reset_warn = QLabel()
        self.reset_warn.setStyleSheet(f"color: {theme.colors()['warn']};")
        self.reset_warn.setWordWrap(True)
        self.reset_warn.hide()
        sec.addWidget(self.reset_warn)

        sec.addWidget(QLabel("遊戲資料夾"))
        row = QHBoxLayout()
        self.game_dir_label = QLabel()
        self.game_dir_label.setWordWrap(True)
        self.game_dir_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        row.addWidget(self.game_dir_label, 1)
        pick = QPushButton("選擇資料夾")
        pick.clicked.connect(self._pick_game_dir)
        row.addWidget(pick)
        sec.addLayout(row)

        self.game_state = QLabel()
        self.game_state.setWordWrap(True)
        sec.addWidget(self.game_state)
        row = QHBoxLayout()
        self.install_btn = QPushButton("安裝遊戲內腳本")
        self.install_btn.clicked.connect(self._install_script)
        self.mod_btn = QPushButton()
        self.mod_btn.clicked.connect(self._toggle_mod)
        refresh = QPushButton("重新檢查")
        refresh.clicked.connect(self._refresh_game)
        row.addWidget(self.install_btn, 1)
        row.addWidget(self.mod_btn, 1)
        row.addWidget(refresh, 1)
        sec.addLayout(row)
        self._game_dir = self.cfg.get("sf6_game_dir", "")
        self._refresh_game()

    def _refresh_game(self):
        d = self._game_dir
        ok = game_bridge.is_game_dir(d)
        self.game_dir_label.setText(d if ok else "找不到遊戲資料夾，請按「選擇資料夾」選 Street Fighter 6 的安裝位置。")
        self.install_btn.setEnabled(ok)
        self.mod_btn.setEnabled(ok)
        if not ok:
            self.mod_btn.setText("關閉 mod")
            self.game_state.setText("")
            self.game_state.hide()                  # 沒找到遊戲就不留一塊空白
            return
        mod = game_bridge.mod_state(d)
        script = game_bridge.script_state(d)
        running = game_bridge.game_running()
        mod_text = {"on": "開著", "off": "關著", "none": "沒有安裝 REFramework"}[mod]
        script_text = {"ok": "已安裝", "old": "需要更新（請按安裝）", "none": "還沒安裝"}[script]
        self.game_state.show()
        self.game_state.setText(f"mod：{mod_text}　｜　遊戲內腳本：{script_text}　｜　遊戲：{'開著' if running else '沒開'}")
        self.mod_btn.setText("開啟 mod" if mod == "off" else "關閉 mod")
        self.mod_btn.setEnabled(mod != "none")
        self.install_btn.setText("更新遊戲內腳本" if script == "old" else "安裝遊戲內腳本")

    def _pick_game_dir(self):
        path = QFileDialog.getExistingDirectory(self, "選擇 Street Fighter 6 的遊戲資料夾", self._game_dir or "")
        if not path:
            return
        if not game_bridge.is_game_dir(path):
            box = QMessageBox(self)
            box.setWindowTitle("遊戲資料夾")
            box.setText(f"這個資料夾裡沒有 {game_bridge.EXE_NAME}，請選 Street Fighter 6 的安裝資料夾。")
            box.exec()
            return
        self._game_dir = str(Path(path))
        self.game_dir_changed.emit(self._game_dir)
        self._refresh_game()

    def _install_script(self):
        err = game_bridge.install_script(self._game_dir)
        box = QMessageBox(self)
        box.setWindowTitle("遊戲內腳本")
        box.setText(err or "安裝好了。遊戲開著的話，請重開遊戲才會載入。")
        box.exec()
        self._refresh_game()

    def _toggle_mod(self):
        on = game_bridge.mod_state(self._game_dir) == "off"
        running = game_bridge.game_running()
        err = game_bridge.set_mod(self._game_dir, on)
        if on and running:
            done = "mod 已開啟。\n遊戲現在開著，請重新開啟遊戲，mod 就會生效。"
        else:
            done = "mod 已開啟，下次開遊戲就會生效。"
        box = QMessageBox(self)
        box.setWindowTitle("mod 開關")
        box.setText(err or (done if on else
                            "mod 已關閉，下次開遊戲不會載入，可以放心線上對戰。\n要練習時再回來按「開啟 mod」。"))
        box.exec()
        self._refresh_game()

    def _size_toggled(self, *_):
        # 「上次的位置」本來就記得在哪個螢幕，不用另外選
        self.screen_box.setEnabled(not self.size_btns["last"].isChecked())

    def _key_toggled(self, on: bool):
        for wdg in self._key_widgets:
            wdg.setEnabled(on)
        self._validate()

    def _pad_toggled(self, on: bool):
        for b in self.pad_boxes.values():
            b.setEnabled(on)
        self._validate()

    def _reset_keys(self):
        for code, edit in self.key_edits.items():
            edit.setKeySequence(QKeySequence(DEFAULT_HOTKEYS.get(code, ""), QKeySequence.PortableText))

    def key_texts(self) -> dict[str, str]:
        out = {}
        for code, edit in self.key_edits.items():
            seq = edit.keySequence()
            out[code] = QKeySequence(seq[0]).toString(QKeySequence.PortableText) if not seq.isEmpty() else ""
        return out

    def _validate(self):
        problems = []
        names = dict(HOTKEY_ACTIONS)
        seen: dict[str, str] = {}
        for code, text in self.key_texts().items():
            if not text or not self.key_on.isChecked():
                continue
            if not hotkeys.usable(text):
                problems.append(f"「{names[code]}」的 {hotkeys.display(text)} 要加上 Ctrl、Alt 或 Shift，或改用 F1～F12。")
            elif text in seen:
                problems.append(f"{hotkeys.display(text)} 同時設給「{seen[text]}」和「{names[code]}」，請換一組。")
            seen.setdefault(text, names[code])
        if self.pad_on.isChecked():
            used: dict[str, str] = {}
            for code, box in self.pad_boxes.items():
                v = box.currentData()
                if v and v in used:
                    problems.append(f"控制器的 BACK＋{box.currentText().split('＋')[-1]} 同時設給「{used[v]}」和「{names[code]}」。")
                if v:
                    used.setdefault(v, names[code])
        self.key_warn.setText("\n".join(problems))
        self.key_warn.setVisible(bool(problems))
        reset_problem = self._reset_problem()
        if hasattr(self, "reset_warn"):
            self.reset_warn.setText(reset_problem)
            self.reset_warn.setVisible(bool(reset_problem))
        if hasattr(self, "save_btn"):
            self.save_btn.setEnabled(not problems and not reset_problem)

    def reset_key_text(self) -> str:
        seq = self.reset_key.keySequence() if hasattr(self, "reset_key") else QKeySequence()
        return QKeySequence(seq[0]).toString(QKeySequence.PortableText) if not seq.isEmpty() else ""

    def _reset_problem(self) -> str:
        if not hasattr(self, "auto_reset"):
            return ""
        by_key = self.reset_mode.currentData() == "key"
        self.reset_key.setVisible(by_key)
        self.reset_clear.setVisible(by_key)
        if not self.auto_reset.isChecked() or not by_key:
            return ""
        text = self.reset_key_text()
        if not text:
            return "請點右邊的格子，按一下遊戲裡用來重置的鍵盤按鍵。"
        if not hotkeys.parse(text):
            return f"{hotkeys.display(text)} 這個鍵不支援，請換一個（字母、數字、F1～F12、方向鍵等）。"
        if hasattr(self, "key_on") and self.key_on.isChecked() and text in self.key_texts().values():
            return f"{hotkeys.display(text)} 已經是練習器的快捷鍵，請換一個。"
        return ""

    def values(self) -> dict:
        return {
            "game_check_enabled": self.game_check.isChecked(),
            "mod_start_notice": self.mod_notice.isChecked(),
            "auto_reset_enabled": self.auto_reset.isChecked(),
            "auto_reset_key": self.reset_key_text(),
            "auto_reset_mode": self.reset_mode.currentData(),
            "auto_reset_delay_s": round(self.reset_delay.value(), 1),
            "hotkeys": self.key_texts(),
            "hotkeys_enabled": self.key_on.isChecked(),
            "window_size": next((k for k, b in self.size_btns.items() if b.isChecked()), "1920"),
            "window_screen": self.screen_box.currentData() or "",
            "pad_hotkeys_enabled": self.pad_on.isChecked(),
            "pad_hotkeys": {code: box.currentData() for code, box in self.pad_boxes.items()},
            "close_to_tray": self.close_tray.isChecked(),
            "ui_font": "jhenghei" if self.font_jh.isChecked() else "hand",
            "ui_theme": next((k for k, b in self.theme_btns.items() if b.isChecked()), "light"),
            "show_numpad": self.show_numpad.isChecked(),
            "overlay_opacity": self.opacity.value() / 100,
            "overlay_zoom": self.zoom.currentData(),
            "overlay_rows_shown": self.rows.currentData(),
        }

    def autostart_wanted(self) -> bool:
        return self.autostart.isChecked()
