"""鍵盤全域快捷鍵：用 Windows 內建的 RegisterHotKey，在遊戲裡按也收得到，不用另外安裝套件。

快捷鍵寫成 Qt 的文字格式，例如 "Ctrl+Alt+O"、"Ctrl+Alt+Right"。
"""
from __future__ import annotations

import ctypes
import logging
import sys

from PySide6.QtCore import QAbstractNativeEventFilter, QObject, Qt, Signal
from PySide6.QtGui import QKeySequence

log = logging.getLogger(__name__)

WM_HOTKEY = 0x0312
MOD_ALT, MOD_CONTROL, MOD_SHIFT, MOD_WIN, MOD_NOREPEAT = 0x1, 0x2, 0x4, 0x8, 0x4000

_SPECIAL_VK = {
    Qt.Key_Left: 0x25, Qt.Key_Up: 0x26, Qt.Key_Right: 0x27, Qt.Key_Down: 0x28,
    Qt.Key_Space: 0x20, Qt.Key_PageUp: 0x21, Qt.Key_PageDown: 0x22, Qt.Key_End: 0x23,
    Qt.Key_Home: 0x24, Qt.Key_Insert: 0x2D, Qt.Key_Delete: 0x2E, Qt.Key_Pause: 0x13,
    Qt.Key_Minus: 0xBD, Qt.Key_Equal: 0xBB, Qt.Key_Comma: 0xBC, Qt.Key_Period: 0xBE,
    Qt.Key_Slash: 0xBF, Qt.Key_Semicolon: 0xBA, Qt.Key_BracketLeft: 0xDB,
    Qt.Key_BracketRight: 0xDD, Qt.Key_Backslash: 0xDC, Qt.Key_Apostrophe: 0xDE,
    Qt.Key_QuoteLeft: 0xC0, Qt.Key_Backspace: 0x08, Qt.Key_Tab: 0x09, Qt.Key_Return: 0x0D,
    Qt.Key_Enter: 0x0D,
}


def _v(x) -> int:
    return int(x.value) if hasattr(x, "value") else int(x)


_SPECIAL_VK = {_v(k): vk for k, vk in _SPECIAL_VK.items()}


def supported() -> bool:
    return sys.platform == "win32"


def parse(text: str) -> tuple[int, int] | None:
    """把 "Ctrl+Alt+O" 轉成 (修飾鍵, 虛擬按鍵碼)；看不懂就回傳 None。"""
    if not text:
        return None
    seq = QKeySequence(text, QKeySequence.PortableText)
    if seq.isEmpty():
        return None
    combo = seq[0]
    key = combo.key()
    mods = combo.keyboardModifiers()
    flags = MOD_NOREPEAT
    if mods & Qt.ControlModifier:
        flags |= MOD_CONTROL
    if mods & Qt.AltModifier:
        flags |= MOD_ALT
    if mods & Qt.ShiftModifier:
        flags |= MOD_SHIFT
    if mods & Qt.MetaModifier:
        flags |= MOD_WIN
    k = _v(key)
    if _v(Qt.Key_A) <= k <= _v(Qt.Key_Z) or _v(Qt.Key_0) <= k <= _v(Qt.Key_9):
        vk = k                                  # 英文字母與數字的按鍵碼跟 ASCII 一樣
    elif _v(Qt.Key_F1) <= k <= _v(Qt.Key_F24):
        vk = 0x70 + (k - _v(Qt.Key_F1))
    else:
        vk = _SPECIAL_VK.get(k)
    if vk is None:
        return None
    return flags, vk


def usable(text: str) -> bool:
    """要有 Ctrl／Alt／Shift／Win，或是 F1～F24，避免打字時誤觸。"""
    parsed = parse(text)
    if not parsed:
        return False
    flags, vk = parsed
    return bool(flags & (MOD_ALT | MOD_CONTROL | MOD_SHIFT | MOD_WIN)) or 0x70 <= vk <= 0x87


def display(text: str) -> str:
    """給人看的寫法，例如 Ctrl＋Alt＋→。"""
    if not text:
        return "（不用）"
    seq = QKeySequence(text, QKeySequence.PortableText).toString(QKeySequence.NativeText)
    for a, b in (("Right", "→"), ("Left", "←"), ("Up", "↑"), ("Down", "↓")):
        seq = seq.replace(a, b)
    return seq.replace("+", "＋")


class _Filter(QAbstractNativeEventFilter):
    def __init__(self, owner: "GlobalHotkeys"):
        super().__init__()
        self.owner = owner

    def nativeEventFilter(self, event_type, message):
        if event_type in (b"windows_generic_MSG", "windows_generic_MSG"):
            try:
                msg = ctypes.wintypes.MSG.from_address(int(message))
            except (TypeError, ValueError):
                return False, 0
            if msg.message == WM_HOTKEY:
                action = self.owner._ids.get(int(msg.wParam))
                if action:
                    self.owner.triggered.emit(action)
                    return True, 0
        return False, 0


class GlobalHotkeys(QObject):
    """register(dict 動作→按鍵) 後，按下時送出 triggered(動作)。"""
    triggered = Signal(str)

    def __init__(self, app, parent=None):
        super().__init__(parent)
        self._ids: dict[int, str] = {}
        self._filter = None
        if supported():
            import ctypes.wintypes  # noqa: F401  讓 MSG 結構可用
            self._user32 = ctypes.windll.user32
            self._user32.RegisterHotKey.argtypes = [ctypes.c_void_p, ctypes.c_int,
                                                    ctypes.c_uint, ctypes.c_uint]
            self._user32.UnregisterHotKey.argtypes = [ctypes.c_void_p, ctypes.c_int]
            self._filter = _Filter(self)
            app.installNativeEventFilter(self._filter)

    def register(self, mapping: dict[str, str]) -> list[str]:
        """先清掉舊的再註冊；回傳註冊失敗的動作代號（被別的程式佔用或看不懂）。"""
        self.unregister_all()
        if not supported():
            return []
        failed = []
        for i, (action, text) in enumerate(mapping.items(), start=1):
            if not text:
                continue
            parsed = parse(text)
            if not parsed:
                failed.append(action)
                continue
            flags, vk = parsed
            if self._user32.RegisterHotKey(None, i, flags, vk):
                self._ids[i] = action
            else:
                log.warning("快捷鍵註冊失敗：%s %s", action, text)
                failed.append(action)
        return failed

    def unregister_all(self):
        if supported():
            for i in list(self._ids):
                self._user32.UnregisterHotKey(None, i)
        self._ids.clear()
