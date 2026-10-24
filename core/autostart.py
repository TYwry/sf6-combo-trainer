"""開機自動啟動：寫在目前使用者的「啟動」設定（登錄檔 HKCU\\...\\Run），不需要系統管理員權限。

自動啟動時會加上 --tray，程式直接縮在系統匣，不跳出主視窗。
"""
from __future__ import annotations

import logging
import sys

from core.shortcut import _target

log = logging.getLogger(__name__)

RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
VALUE_NAME = "SF6ComboTrainer"


def supported() -> bool:
    return sys.platform == "win32"


def command() -> str:
    target, args = _target()
    return f'"{target}" {args} --tray' if args else f'"{target}" --tray'


def is_enabled() -> bool:
    if not supported():
        return False
    import winreg
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as key:
            winreg.QueryValueEx(key, VALUE_NAME)
            return True
    except OSError:
        return False


def set_enabled(on: bool) -> str | None:
    """開啟或關閉；成功回傳 None，失敗回傳白話原因。"""
    if not supported():
        return "只有 Windows 可以設定開機自動啟動。"
    import winreg
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0,
                            winreg.KEY_SET_VALUE | winreg.KEY_QUERY_VALUE) as key:
            if on:
                winreg.SetValueEx(key, VALUE_NAME, 0, winreg.REG_SZ, command())
            else:
                try:
                    winreg.DeleteValue(key, VALUE_NAME)
                except FileNotFoundError:
                    pass
        log.info("開機自動啟動：%s", on)
        return None
    except OSError as exc:
        log.exception("設定開機自動啟動失敗")
        return f"Windows 不讓程式修改啟動設定（{exc}）。可能是防毒軟體擋住了。"
