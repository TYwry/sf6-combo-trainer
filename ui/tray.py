"""系統匣（螢幕右下角）的小圖示與右鍵選單。"""
from __future__ import annotations

from PySide6.QtCore import QObject, Signal
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QMenu, QSystemTrayIcon

from app_info import APP_DISPLAY_NAME, ASSETS_DIR


class Tray(QObject):
    open_requested = Signal()
    overlay_toggled = Signal()
    lock_toggled = Signal()
    practice_toggled = Signal()
    settings_requested = Signal()
    quit_requested = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.icon = QSystemTrayIcon(QIcon(str(ASSETS_DIR / "app_ink.ico")), self)
        self.icon.setToolTip(APP_DISPLAY_NAME)
        self.menu = QMenu()
        self.menu.addAction("開啟主視窗").triggered.connect(self.open_requested.emit)
        self.menu.addSeparator()
        self.overlay_action = self.menu.addAction("浮動視窗")
        self.overlay_action.setCheckable(True)
        self.overlay_action.triggered.connect(lambda _=False: self.overlay_toggled.emit())
        self.lock_action = self.menu.addAction("鎖定浮動視窗（滑鼠穿過）")
        self.lock_action.setCheckable(True)
        self.lock_action.triggered.connect(lambda _=False: self.lock_toggled.emit())
        self.practice_action = self.menu.addAction("開始練習")
        self.practice_action.triggered.connect(self.practice_toggled.emit)
        self.menu.addSeparator()
        self.menu.addAction("設定").triggered.connect(self.settings_requested.emit)
        self.menu.addAction("結束").triggered.connect(self.quit_requested.emit)
        self.icon.setContextMenu(self.menu)
        self.icon.activated.connect(self._activated)

    @staticmethod
    def available() -> bool:
        return QSystemTrayIcon.isSystemTrayAvailable()

    def show(self):
        self.icon.show()

    def hide(self):
        self.icon.hide()

    def sync(self, overlay_on: bool, locked: bool, practicing: bool, has_combo: bool):
        self.overlay_action.setChecked(overlay_on)
        self.lock_action.setChecked(locked)
        self.practice_action.setText("停止練習" if practicing else "開始練習")
        self.practice_action.setEnabled(has_combo or practicing)

    def notify(self, text: str):
        self.icon.showMessage(APP_DISPLAY_NAME, text, QSystemTrayIcon.Information, 5000)

    def _activated(self, reason):
        if reason == QSystemTrayIcon.DoubleClick:
            self.open_requested.emit()
