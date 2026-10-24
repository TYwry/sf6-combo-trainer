"""遊戲 mod 連線：找遊戲資料夾、安裝遊戲內腳本、mod 開關、讀遊戲寫出來的連擊數、判定連段有沒有斷。

遊戲內腳本（mod/sf6_combo_trainer.lua）在訓練模式中把雙方的連擊數寫到
<遊戲資料夾>\\reframework\\data\\SF6ComboTrainer_state.json，這裡每一幀檢查一次有沒有更新。

mod 開關＝把遊戲資料夾裡的 dinput8.dll（REFramework 本體）改名成 dinput8.dll.off，檔案不刪；
第一次關閉前先複製一份 dinput8.dll.bak 當備份。遊戲開著時不能切換，切換後下次開遊戲才生效。
"""
from __future__ import annotations

import json
import logging
import os
import re
import shutil
import sys
import time
from pathlib import Path

from app_info import PROJECT_DIR

log = logging.getLogger(__name__)

EXE_NAME = "StreetFighter6.exe"
DLL_ON = "dinput8.dll"
DLL_OFF = "dinput8.dll.off"
DLL_BAK = "dinput8.dll.bak"
SCRIPT_NAME = "sf6_combo_trainer.lua"
STATE_NAME = "SF6ComboTrainer_state.json"
CMD_NAME = "SF6ComboTrainer_cmd.json"          # 練習器 → 遊戲內腳本的要求（目前只有重置）
SCRIPT_SRC = PROJECT_DIR / "mod" / SCRIPT_NAME
FONT_NAME = "LXGWWenKaiTC-Bold.ttf"                   # 遊戲裡提醒紙卡用的手寫字（跟練習器同一套）
FONT_SRC = PROJECT_DIR / "assets" / "fonts" / FONT_NAME
GAME_SUBDIR = Path("steamapps") / "common" / "Street Fighter 6"
STALE_S = 2.0          # 超過這麼久沒收到遊戲資料，就當作遊戲沒開或 mod 沒載入


# ---- 遊戲資料夾 ----
def is_game_dir(path: str | Path | None) -> bool:
    return bool(path) and (Path(path) / EXE_NAME).is_file()


def _steam_roots() -> list[Path]:
    roots: list[Path] = []
    if sys.platform == "win32":
        import winreg
        for hive, key in ((winreg.HKEY_CURRENT_USER, r"Software\Valve\Steam"),
                          (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\WOW6432Node\Valve\Steam")):
            try:
                with winreg.OpenKey(hive, key) as k:
                    for name in ("SteamPath", "InstallPath"):
                        try:
                            roots.append(Path(winreg.QueryValueEx(k, name)[0]))
                        except OSError:
                            pass
            except OSError:
                pass
    roots.append(Path(r"C:\Program Files (x86)\Steam"))
    return roots


def find_game_dir() -> Path | None:
    """從 Steam 的遊戲庫清單找 Street Fighter 6；找不到時再試各磁碟的 SteamLibrary。"""
    libraries: list[Path] = []
    for root in _steam_roots():
        libraries.append(root)
        vdf = root / "steamapps" / "libraryfolders.vdf"
        try:
            text = vdf.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        for m in re.finditer(r'"path"\s+"([^"]+)"', text):
            libraries.append(Path(m.group(1).replace("\\\\", "\\")))
    for letter in "CDEFGHIJ":
        libraries.append(Path(f"{letter}:\\SteamLibrary"))
    seen = set()
    for lib in libraries:
        key = str(lib).lower()
        if key in seen:
            continue
        seen.add(key)
        if is_game_dir(lib / GAME_SUBDIR):
            return lib / GAME_SUBDIR
    return None


# ---- 遊戲有沒有開著 ----
def game_running() -> bool:
    if sys.platform != "win32":
        return False
    import ctypes
    from ctypes import wintypes

    class PROCESSENTRY32W(ctypes.Structure):
        _fields_ = [("dwSize", wintypes.DWORD), ("cntUsage", wintypes.DWORD),
                    ("th32ProcessID", wintypes.DWORD), ("th32DefaultHeapID", ctypes.c_void_p),
                    ("th32ModuleID", wintypes.DWORD), ("cntThreads", wintypes.DWORD),
                    ("th32ParentProcessID", wintypes.DWORD), ("pcPriClassBase", ctypes.c_long),
                    ("dwFlags", wintypes.DWORD), ("szExeFile", ctypes.c_wchar * 260)]

    k32 = ctypes.windll.kernel32
    k32.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
    snap = k32.CreateToolhelp32Snapshot(0x2, 0)       # TH32CS_SNAPPROCESS
    if not snap or snap == wintypes.HANDLE(-1).value:
        return False
    try:
        entry = PROCESSENTRY32W()
        entry.dwSize = ctypes.sizeof(entry)
        ok = k32.Process32FirstW(snap, ctypes.byref(entry))
        while ok:
            if entry.szExeFile.lower() == EXE_NAME.lower():
                return True
            ok = k32.Process32NextW(snap, ctypes.byref(entry))
    finally:
        k32.CloseHandle(snap)
    return False


# ---- mod 開關 ----
def mod_state(game_dir: str | Path) -> str:
    """on＝REFramework 會載入；off＝已改名關閉；none＝遊戲資料夾裡沒有 REFramework。"""
    d = Path(game_dir)
    if (d / DLL_ON).is_file():
        return "on"
    if (d / DLL_OFF).is_file():
        return "off"
    return "none"


def set_mod(game_dir: str | Path, on: bool) -> str | None:
    """切換 mod；回傳錯誤訊息，成功回傳 None。

    開啟隨時都可以（.off 檔沒被遊戲載入，改名不影響正在玩的遊戲）；
    關閉要等遊戲關掉，因為 dinput8.dll 正被遊戲使用中。"""
    d = Path(game_dir)
    if not on and game_running():
        return "遊戲還開著。請先關掉遊戲再關閉 mod（關閉後下次開遊戲才會生效）。"
    state = mod_state(d)
    try:
        if on:
            if state == "on":
                return None
            if state == "none":
                return "遊戲資料夾裡找不到 REFramework（dinput8.dll 或 dinput8.dll.off）。"
            (d / DLL_OFF).rename(d / DLL_ON)
        else:
            if state != "on":
                return None
            if not (d / DLL_BAK).exists():
                shutil.copy2(d / DLL_ON, d / DLL_BAK)     # 第一次關閉前先備份一份
            if (d / DLL_OFF).exists():
                return f"遊戲資料夾裡已經有 {DLL_OFF}，為了不蓋掉它，這次沒有切換。請檢查遊戲資料夾。"
            (d / DLL_ON).rename(d / DLL_OFF)
    except OSError as exc:
        log.exception("切換 mod 失敗")
        return f"切換失敗：{exc}"
    log.info("mod 已%s", "開啟" if on else "關閉")
    return None


# ---- 遊戲內腳本 ----
def script_path(game_dir: str | Path) -> Path:
    return Path(game_dir) / "reframework" / "autorun" / SCRIPT_NAME


def font_path(game_dir: str | Path) -> Path:
    return Path(game_dir) / "reframework" / "fonts" / FONT_NAME


def script_state(game_dir: str | Path) -> str:
    """ok＝已安裝最新版；old＝已安裝但跟練習器附的版本不同；none＝還沒安裝。"""
    target = script_path(game_dir)
    if not target.is_file():
        return "none"
    if not font_path(game_dir).is_file():
        return "old"
    try:
        return "ok" if target.read_bytes() == SCRIPT_SRC.read_bytes() else "old"
    except OSError:
        return "old"


def install_script(game_dir: str | Path) -> str | None:
    """把遊戲內腳本複製到 reframework\\autorun\\（只會覆蓋這個腳本自己），手寫字型沒有就補一份到 fonts\\。"""
    autorun = script_path(game_dir).parent
    if not autorun.parent.is_dir():
        return "遊戲資料夾裡沒有 reframework 資料夾，請先確認 REFramework 有裝好。"
    try:
        autorun.mkdir(exist_ok=True)
        shutil.copyfile(SCRIPT_SRC, script_path(game_dir))
        font = font_path(game_dir)
        if not font.is_file() and FONT_SRC.is_file():
            font.parent.mkdir(exist_ok=True)
            shutil.copyfile(FONT_SRC, font)
    except OSError as exc:
        log.exception("安裝遊戲內腳本失敗")
        return f"安裝失敗：{exc}"
    log.info("遊戲內腳本已安裝到 %s", script_path(game_dir))
    return None


# ---- 讀遊戲資料 ----
class GameFeed:
    """讀遊戲內腳本寫出來的狀態檔；檔案有更新才重新讀。"""

    def __init__(self):
        self.path: Path | None = None
        self._mtime = 0
        self._seq = None
        self.state: dict = {}
        self.recv_t = -1e9

    def set_game_dir(self, game_dir: str | Path | None):
        path = Path(game_dir) / "reframework" / "data" / STATE_NAME if is_game_dir(game_dir) else None
        if path != self.path:
            self.path = path
            self._mtime = 0
            self._seq = None
            self.state = {}
            self.recv_t = -1e9

    def poll(self, now: float) -> bool:
        """回傳 True＝這次讀到新資料。"""
        if not self.path:
            return False
        try:
            mtime = os.stat(self.path).st_mtime_ns
        except OSError:
            return False
        if mtime == self._mtime:
            return False
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return False          # 遊戲正在寫入，下一幀再讀
        self._mtime = mtime
        if not isinstance(data, dict) or data.get("seq") == self._seq:
            return False
        self._seq = data.get("seq")
        self.state = data
        self.recv_t = now
        return True

    def request_reset(self, mode: str = "refresh") -> bool:
        """請遊戲內腳本在訓練模式重置一次。mode：refresh＝一般重置；state＝播放儲存狀態。回傳有沒有寫成功。"""
        if not self.path:
            return False
        cmd = self.path.with_name(CMD_NAME)
        tmp = cmd.with_suffix(".tmp")
        try:
            tmp.write_text(json.dumps({"reset": time.time_ns(), "mode": mode}), encoding="utf-8")
            os.replace(tmp, cmd)               # 整個換掉，遊戲不會讀到寫一半的檔案
        except OSError:
            log.exception("寫入重置要求失敗")
            return False
        log.info("連段斷了，請遊戲重置")
        return True

    def alive(self, now: float) -> bool:
        return now - self.recv_t < STALE_S

    def training(self, now: float) -> bool:
        return self.alive(now) and bool(self.state.get("training")) and bool(self.state.get("counts_ok"))

    def hits(self) -> int:
        try:
            return max(int(self.state.get("p1") or 0), int(self.state.get("p2") or 0))
        except (TypeError, ValueError):
            return 0


class GameJudge:
    """一次練習的遊戲判定：記錄連擊數有沒有中途歸零、最多打到幾 Hit。

    練習開始時如果前一段的連擊還沒結束，先等它歸零，才開始算這一次。
    """
    SETTLE_S = 1.5        # 輸入完成後，連擊數這麼久沒再增加就當作連段結束

    def __init__(self, hits_now: int, now: float):
        self.waiting_prev = hits_now > 0
        self.cur = hits_now
        self.max_hits = 0
        self.rose = False            # 這一次有沒有打中過
        self.dropped = False         # 打中之後連擊數歸零了（連段結束或斷了）
        self.last_change = now

    def feed(self, hits: int, now: float):
        if self.waiting_prev:
            if hits == 0:
                self.waiting_prev = False
            self.cur = hits
            return
        if hits > self.cur:
            self.rose = True
            self.last_change = now
        elif self.rose and hits == 0 and self.cur > 0:
            self.dropped = True
        self.max_hits = max(self.max_hits, hits)
        self.cur = hits

    def settled(self, finish_t: float, now: float) -> bool:
        """輸入完成後，可以下判定了沒。"""
        return self.dropped or now - max(finish_t, self.last_change) > self.SETTLE_S

    def verdict(self, expected: int | None) -> tuple[bool, str]:
        """(成功與否, 失敗時顯示的字)。"""
        if not self.rose:
            return False, "沒打中"
        if expected and self.max_hits < expected:
            return False, f"只打到 {self.max_hits} / {expected} Hit"
        return True, ""
