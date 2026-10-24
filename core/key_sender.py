"""替使用者按一下鍵盤按鍵（連段斷了自動按遊戲的重置鍵）。

只在 Street Fighter 6 是最前面的視窗時才按，不會打字到別的程式。
用 Windows 內建的 SendInput 送掃描碼（遊戲讀的是實體按鍵位置），按住一小段時間再放開，遊戲才收得到。
"""
from __future__ import annotations

import ctypes
import logging
import sys
import threading
import time

from core import hotkeys

log = logging.getLogger(__name__)

HOLD_S = 0.06                      # 按住約 4 幀，太短遊戲可能讀不到
KEYEVENTF_EXTENDEDKEY, KEYEVENTF_KEYUP, KEYEVENTF_SCANCODE = 0x1, 0x2, 0x8
INPUT_KEYBOARD = 1
# 方向鍵、Home／End 這些要加「延伸鍵」旗標，不然會被當成數字鍵盤的鍵
_EXTENDED_VK = {0x21, 0x22, 0x23, 0x24, 0x25, 0x26, 0x27, 0x28, 0x2D, 0x2E}
_MOD_VK = ((hotkeys.MOD_CONTROL, 0x11), (hotkeys.MOD_SHIFT, 0x10), (hotkeys.MOD_ALT, 0x12))


def _structs():
    from ctypes import wintypes

    class KEYBDINPUT(ctypes.Structure):
        _fields_ = [("wVk", wintypes.WORD), ("wScan", wintypes.WORD), ("dwFlags", wintypes.DWORD),
                    ("time", wintypes.DWORD), ("dwExtraInfo", ctypes.c_size_t)]

    class MOUSEINPUT(ctypes.Structure):          # 只是讓 INPUT 的大小正確
        _fields_ = [("dx", wintypes.LONG), ("dy", wintypes.LONG), ("mouseData", wintypes.DWORD),
                    ("dwFlags", wintypes.DWORD), ("time", wintypes.DWORD), ("dwExtraInfo", ctypes.c_size_t)]

    class _U(ctypes.Union):
        _fields_ = [("ki", KEYBDINPUT), ("mi", MOUSEINPUT)]

    class INPUT(ctypes.Structure):
        _fields_ = [("type", wintypes.DWORD), ("u", _U)]

    return INPUT


def game_in_front(exe_name: str = "StreetFighter6.exe") -> bool:
    """最前面的視窗是不是遊戲。"""
    if sys.platform != "win32":
        return False
    from ctypes import wintypes
    user32, k32 = ctypes.windll.user32, ctypes.windll.kernel32
    hwnd = user32.GetForegroundWindow()
    if not hwnd:
        return False
    pid = wintypes.DWORD()
    user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    k32.OpenProcess.restype = wintypes.HANDLE
    h = k32.OpenProcess(0x1000, False, pid.value)          # PROCESS_QUERY_LIMITED_INFORMATION
    if not h:
        return False
    try:
        buf = ctypes.create_unicode_buffer(1024)
        size = wintypes.DWORD(len(buf))
        if not k32.QueryFullProcessImageNameW(h, 0, buf, ctypes.byref(size)):
            return False
        return buf.value.replace("/", "\\").rsplit("\\", 1)[-1].lower() == exe_name.lower()
    finally:
        k32.CloseHandle(h)


def _events(flags: int, vk: int, up: bool) -> list[tuple[int, int]]:
    """(掃描碼, 旗標) 的清單：先按修飾鍵再按主鍵，放開時順序相反。"""
    user32 = ctypes.windll.user32
    keys = [mvk for mod, mvk in _MOD_VK if flags & mod] + [vk]
    if up:
        keys.reverse()
    out = []
    for k in keys:
        f = KEYEVENTF_SCANCODE | (KEYEVENTF_KEYUP if up else 0) | (KEYEVENTF_EXTENDEDKEY if k in _EXTENDED_VK else 0)
        out.append((user32.MapVirtualKeyW(k, 0), f))
    return out


def _send(events: list[tuple[int, int]]):
    INPUT = _structs()
    arr = (INPUT * len(events))()
    for i, (scan, f) in enumerate(events):
        arr[i].type = INPUT_KEYBOARD
        arr[i].u.ki.wScan = scan
        arr[i].u.ki.dwFlags = f
    ctypes.windll.user32.SendInput(len(events), arr, ctypes.sizeof(INPUT))


def press(text: str) -> str | None:
    """按一下 text（Qt 的按鍵寫法，例如 "F5"、"Backspace"）。回傳沒按的原因，按了回傳 None。"""
    parsed = hotkeys.parse(text)
    if not parsed:
        return "沒有設定按鍵，或這個按鍵不支援"
    if not game_in_front():
        return "遊戲不在最前面"
    flags, vk = parsed

    def run():
        try:
            _send(_events(flags, vk, up=False))
            time.sleep(HOLD_S)
            _send(_events(flags, vk, up=True))
        except Exception:                      # 送按鍵失敗不能讓練習器當掉
            log.exception("自動按重置鍵失敗")

    threading.Thread(target=run, daemon=True).start()
    log.info("連段斷了，自動按 %s", text)
    return None
