"""「從圖片加入」視窗：選圖或貼上 → 讀出記法 → 確認或修改 → 加入連段。"""
from __future__ import annotations

import threading
import time
from pathlib import Path

from PySide6.QtCore import QObject, Qt, Signal
from PySide6.QtGui import QGuiApplication, QImage, QKeySequence, QPixmap, QShortcut
from PySide6.QtWidgets import (QDialog, QFileDialog, QFrame, QHBoxLayout, QLabel,
                               QPlainTextEdit, QProgressBar, QPushButton, QScrollArea,
                               QVBoxLayout)

from core import image_reader as ir
from core.notation import group_move_name, parse
from ui import theme
from ui.trial_view import TrialView

IMAGE_FILTER = "圖片 (*.png *.jpg *.jpeg *.bmp *.webp *.gif)"


class _Relay(QObject):
    """背景執行緒把結果送回畫面用。"""
    stage = Signal(str)
    done = Signal(str, str, object)       # 記法、方式、提醒
    failed = Signal(str)


class ImageDialog(QDialog):
    def __init__(self, cfg: dict, movelist: dict | None, button_style: str,
                 can_append: bool, parent=None):
        super().__init__(parent)
        self.cfg = cfg
        self.movelist = movelist
        self.button_style = button_style
        self.image: QImage | None = None
        self.result_text = ""
        self.mode = ""
        self._busy = False
        self._cancel_read = False
        self.relay = _Relay()
        self.relay.stage.connect(self._set_status)
        self.relay.done.connect(self._got_result)
        self.relay.failed.connect(self._got_error)

        self.setWindowTitle("從圖片加入連段")
        self.setAcceptDrops(True)
        self.resize(860, 760)
        self._build(can_append)
        if not ir.winocr_available():
            self._set_status("Windows 內建文字辨識還沒裝好，請關掉程式後用 start.bat 重新開啟，會自動安裝。", "warn")
        else:
            self._set_status("用 Windows 內建文字辨識讀圖（只看得懂文字，看不懂箭頭圖示）。"
                             "有箭頭的攻略圖，可以請 AI 聊天工具讀成文字再貼上，會比較準。")

    # ---- 介面 ----
    def _build(self, can_append: bool):
        box = QVBoxLayout(self)
        box.setContentsMargins(18, 14, 18, 14)
        box.setSpacing(8)

        row = QHBoxLayout()
        title = theme.GlowTitle("圖片", 11.5)
        title.setMinimumWidth(80)
        row.addWidget(title)
        row.addStretch(1)
        pick = QPushButton("選擇圖片")
        pick.clicked.connect(self._pick_file)
        paste = QPushButton("貼上圖片")
        paste.clicked.connect(self._paste)
        row.addWidget(pick)
        row.addWidget(paste)
        box.addLayout(row)
        hint = QLabel("可以選圖片檔、把圖片拖進來，或複製圖片後按 Ctrl+V。")
        hint.setObjectName("muted")
        box.addWidget(hint)

        self.preview = QLabel("還沒有圖片")
        self.preview.setObjectName("muted")
        self.preview.setAlignment(Qt.AlignCenter)
        self.preview.setMinimumHeight(170)
        self.preview.setFrameShape(QFrame.StyledPanel)
        box.addWidget(self.preview)

        how = QHBoxLayout()
        how.addStretch(1)
        self.read_btn = QPushButton("讀取")
        self.read_btn.clicked.connect(self._read)
        how.addWidget(self.read_btn)
        box.addLayout(how)

        self.bar = QProgressBar()
        self.bar.setTextVisible(False)
        self.bar.setFixedHeight(8)
        self.bar.hide()
        box.addWidget(self.bar)
        self.status = QLabel()
        self.status.setObjectName("muted")
        self.status.setWordWrap(True)
        box.addWidget(self.status)

        box.addWidget(theme.GlowTitle("讀到的記法（可以直接修改）", 11))
        self.notation = QPlainTextEdit()
        self.notation.setAccessibleName("讀到的連段記法")
        self.notation.setFont(theme.font(12))
        self.notation.setFixedHeight(60)
        self.notation.textChanged.connect(self._update_preview)
        box.addWidget(self.notation)
        self.errors = QLabel()
        self.errors.setWordWrap(True)
        self.errors.setStyleSheet(f"color: {theme.colors()['warn']};")
        self.errors.hide()
        box.addWidget(self.errors)

        self.view = TrialView()
        self.view.set_facing(self.cfg.get("trial_facing_right", True))
        self.view.set_button_style(self.button_style, self.cfg["layout"])
        scroll = QScrollArea()
        scroll.setWidget(self.view)
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        scroll.setMinimumHeight(170)
        box.addWidget(scroll, 1)

        bottom = QHBoxLayout()
        bottom.addStretch(1)
        cancel = QPushButton("取消")
        cancel.clicked.connect(self.reject)
        self.new_btn = QPushButton("新增成新連段")
        self.new_btn.clicked.connect(lambda: self._finish("new"))
        self.append_btn = QPushButton("加到目前連段")
        self.append_btn.clicked.connect(lambda: self._finish("append"))
        self.append_btn.setVisible(can_append)
        for b in (cancel, self.new_btn, self.append_btn):
            bottom.addWidget(b)
        box.addLayout(bottom)

        QShortcut(QKeySequence.Paste, self, activated=self._paste)
        self._update_preview()

    def _set_status(self, text: str, tone: str = "muted"):
        self.status.setText(text)
        self.status.setStyleSheet(f"color: {theme.colors()[tone]};")

    # ---- 圖片來源 ----
    def _pick_file(self):
        path, _ = QFileDialog.getOpenFileName(self, "選擇連段圖片", "", IMAGE_FILTER)
        if path:
            self._load_path(path)

    def _load_path(self, path: str):
        img = QImage(path)
        if img.isNull():
            self._set_status(f"打不開這個檔案：{Path(path).name}", "warn")
            return
        self.set_image(img)

    def _paste(self):
        data = QGuiApplication.clipboard().mimeData()
        if data is not None and data.hasImage():
            img = QImage(QGuiApplication.clipboard().image())
            if not img.isNull():
                self.set_image(img)
                return
        if data is not None and data.hasUrls():
            for url in data.urls():
                if url.isLocalFile():
                    self._load_path(url.toLocalFile())
                    return
        self._set_status("剪貼簿裡沒有圖片。請先在圖片上按右鍵「複製圖片」，或用截圖工具截圖。", "warn")

    def dragEnterEvent(self, event):
        if event.mimeData().hasImage() or event.mimeData().hasUrls():
            event.acceptProposedAction()

    def dropEvent(self, event):
        data = event.mimeData()
        if data.hasImage():
            self.set_image(QImage(data.imageData()))
        elif data.hasUrls() and data.urls()[0].isLocalFile():
            self._load_path(data.urls()[0].toLocalFile())

    def set_image(self, img: QImage):
        self.image = img
        pix = QPixmap.fromImage(img)
        self.preview.setText("")
        self.preview.setPixmap(pix.scaled(self.preview.width() - 12, 220, Qt.KeepAspectRatio,
                                          Qt.SmoothTransformation))
        self._read()

    # ---- 讀取 ----
    def _read(self):
        if self.image is None:
            self._set_status("先選一張圖片或貼上圖片。", "warn")
            return
        if self._busy:
            self._cancel_read = True          # 讀取中再按一次＝停止
            self.read_btn.setEnabled(False)
            return
        self._busy = True
        self._cancel_read = False
        self._read_start = time.monotonic()
        self.read_btn.setText("停止")
        self.bar.setRange(0, 0)
        self.bar.show()
        img = self.image.copy()

        def work():
            try:
                text, source, notes = ir.read_image(img, self.relay.stage.emit, lambda: self._cancel_read)
                self.relay.done.emit(text, source, notes)
            except ir.ReaderError as e:
                self.relay.failed.emit(str(e))
            except Exception as e:        # 不讓背景執行緒的意外讓程式當掉
                self.relay.failed.emit(f"讀圖時發生錯誤：{e}")
        threading.Thread(target=work, daemon=True).start()

    def _read_finished(self):
        self._busy = False
        self.read_btn.setText("讀取")
        self.read_btn.setEnabled(True)
        self.bar.hide()

    def _got_result(self, text: str, source: str, notes):
        self._read_finished()
        self.notation.setPlainText(text)
        secs = time.monotonic() - getattr(self, "_read_start", time.monotonic())
        msg = f"用{source}讀完了（{secs:.1f} 秒），請對照圖片確認，不對的地方可以直接改。"
        if notes:
            msg += "\n" + "\n".join(notes)
        self._set_status(msg, "ok" if not notes else "muted")

    def _got_error(self, text: str):
        self._read_finished()
        self._set_status(text, "warn")

    # ---- 預覽與結束 ----
    def _update_preview(self):
        combo = parse(self.notation.toPlainText())
        names = [group_move_name(g, self.movelist) for g in combo.groups]
        self.view.set_combo(combo, names, "預覽")
        errs = list(dict.fromkeys(combo.errors))
        self.errors.setText("　".join(errs))
        self.errors.setVisible(bool(errs))
        ok = combo.step_count() > 0
        self.new_btn.setEnabled(ok)
        self.append_btn.setEnabled(ok)

    def _finish(self, mode: str):
        self.result_text = self.notation.toPlainText().strip()
        self.mode = mode
        self.accept()

    def done(self, r):
        self._cancel_read = True
        super().done(r)
