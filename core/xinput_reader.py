"""在背景讀取 Xbox 控制器（Windows 內建的 XInput），把狀態變化轉成事件。

讀取執行緒每秒約 250 次查詢控制器，只有「按下」「放開」「方向改變」才產生事件，
所以按住不放不會被當成連按。事件放進佇列，由介面定時取出。
"""
from __future__ import annotations

import ctypes
import logging
import math
import queue
import sys
import threading
import time
from dataclasses import dataclass, field

log = logging.getLogger(__name__)

# XInput 按鈕位元
BUTTON_BITS = {
    "UP": 0x0001,
    "DOWN": 0x0002,
    "LEFT": 0x0004,
    "RIGHT": 0x0008,
    "START": 0x0010,
    "BACK": 0x0020,
    "LS": 0x0040,
    "RS": 0x0080,
    "LB": 0x0100,
    "RB": 0x0200,
    "A": 0x1000,
    "B": 0x2000,
    "X": 0x4000,
    "Y": 0x8000,
}
# 會被當成按鍵事件的按鈕（十字鍵另外算方向）
FACE_BUTTONS = ["A", "B", "X", "Y", "LB", "RB", "LT", "RT", "LS", "RS", "BACK", "START"]

ERROR_SUCCESS = 0
ERROR_DEVICE_NOT_CONNECTED = 1167


class XINPUT_GAMEPAD(ctypes.Structure):
    _fields_ = [
        ("wButtons", ctypes.c_ushort),
        ("bLeftTrigger", ctypes.c_ubyte),
        ("bRightTrigger", ctypes.c_ubyte),
        ("sThumbLX", ctypes.c_short),
        ("sThumbLY", ctypes.c_short),
        ("sThumbRX", ctypes.c_short),
        ("sThumbRY", ctypes.c_short),
    ]


class XINPUT_STATE(ctypes.Structure):
    _fields_ = [("dwPacketNumber", ctypes.c_ulong), ("Gamepad", XINPUT_GAMEPAD)]


@dataclass
class PadState:
    """某一瞬間的控制器狀態。"""
    connected: bool = False
    buttons: frozenset = frozenset()
    dpad_dir: int = 5
    stick_dir: int = 5
    direction: int = 5
    lx: float = 0.0
    ly: float = 0.0
    lt: int = 0
    rt: int = 0


@dataclass
class InputEvent:
    """kind：press 按下、release 放開、dir 方向改變、connect 連線、disconnect 斷線。"""
    t: float
    kind: str
    button: str = ""
    direction: int = 5
    prev_direction: int = 5
    background: bool = False
    extra: dict = field(default_factory=dict)


def _load_xinput():
    if sys.platform != "win32":
        return None
    for name in ("xinput1_4", "xinput1_3", "xinput9_1_0"):
        try:
            dll = getattr(ctypes.windll, name)
            dll.XInputGetState.argtypes = [ctypes.c_uint, ctypes.POINTER(XINPUT_STATE)]
            dll.XInputGetState.restype = ctypes.c_uint
            log.info("使用 %s 讀取控制器", name)
            return dll
        except (OSError, AttributeError):
            continue
    return None


def numpad_from_xy(x: int, y: int) -> int:
    """x、y 為 -1/0/1（y 正為上），轉成九宮格數字（以面向右為準）。"""
    return {(-1, 1): 7, (0, 1): 8, (1, 1): 9,
            (-1, 0): 4, (0, 0): 5, (1, 0): 6,
            (-1, -1): 1, (0, -1): 2, (1, -1): 3}[(x, y)]


def dpad_direction(buttons: frozenset) -> int:
    x = (1 if "RIGHT" in buttons else 0) - (1 if "LEFT" in buttons else 0)
    y = (1 if "UP" in buttons else 0) - (1 if "DOWN" in buttons else 0)
    return numpad_from_xy(x, y)


DIR_NAMES = {1: {"DOWN", "LEFT"}, 2: {"DOWN"}, 3: {"DOWN", "RIGHT"}, 4: {"LEFT"}, 5: set(),
             6: {"RIGHT"}, 7: {"UP", "LEFT"}, 8: {"UP"}, 9: {"UP", "RIGHT"}}


def held_inputs(state: "PadState") -> frozenset:
    """按著的訊號，加上目前方向換算的 UP／DOWN／LEFT／RIGHT。

    Hitbox 設成搖桿模式時，方向不是十字鍵訊號，這樣方向鍵在畫面上也能亮。"""
    return frozenset(state.buttons) | DIR_NAMES.get(state.direction, set())


def stick_direction(lx: float, ly: float, deadzone: float) -> int:
    """左搖桿轉八方向：小於死區算中立，其餘每 45 度一個方向。"""
    if math.hypot(lx, ly) < deadzone:
        return 5
    angle = math.degrees(math.atan2(ly, lx)) % 360
    sector = int(((angle + 22.5) % 360) // 45)
    # 0=右 1=右上 2=上 3=左上 4=左 5=左下 6=下 7=右下
    return [6, 9, 8, 7, 4, 1, 2, 3][sector]


class XInputReader:
    def __init__(self, settings: dict, poll_hz: int = 1000):
        self._dll = _load_xinput()
        self.available = self._dll is not None
        self.poll_hz = poll_hz
        self.events: "queue.Queue[InputEvent]" = queue.Queue()
        self._lock = threading.Lock()
        self._state = PadState()
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self._is_foreground = lambda: True
        self.controller_index = -1
        self.measured_hz = 0.0
        self.apply_settings(settings)

    def apply_settings(self, settings: dict):
        self.deadzone = float(settings.get("stick_deadzone", 0.5))
        self.trigger_threshold = int(settings.get("trigger_threshold", 30))
        self.direction_source = settings.get("direction_source", "both")
        self.preferred_index = int(settings.get("controller_index", -1))

    def set_foreground_check(self, func):
        """傳入一個回答「本程式視窗是否在前景」的函式，用來標記背景事件。"""
        self._is_foreground = func

    @property
    def state(self) -> PadState:
        with self._lock:
            return self._state

    def start(self):
        if not self.available or self._thread:
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="xinput-reader", daemon=True)
        self._thread.start()

    def stop(self):
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=1)
            self._thread = None

    # ---- 讀取 ----
    def _read(self, index: int):
        st = XINPUT_STATE()
        result = self._dll.XInputGetState(index, ctypes.byref(st))
        return st if result == ERROR_SUCCESS else None

    def _find_controller(self):
        order = [self.preferred_index] if self.preferred_index in range(4) else []
        order += [i for i in range(4) if i not in order]
        for i in order:
            st = self._read(i)
            if st is not None:
                return i, st
        return -1, None

    def _to_state(self, st: XINPUT_STATE) -> PadState:
        gp = st.Gamepad
        pressed = {name for name, bit in BUTTON_BITS.items() if gp.wButtons & bit}
        if gp.bLeftTrigger > self.trigger_threshold:
            pressed.add("LT")
        if gp.bRightTrigger > self.trigger_threshold:
            pressed.add("RT")
        buttons = frozenset(pressed)
        lx = max(-1.0, gp.sThumbLX / 32767)
        ly = max(-1.0, gp.sThumbLY / 32767)
        dpad = dpad_direction(buttons)
        stick = stick_direction(lx, ly, self.deadzone)
        if self.direction_source == "dpad":
            direction = dpad
        elif self.direction_source == "stick":
            direction = stick
        else:
            direction = dpad if dpad != 5 else stick
        return PadState(True, buttons, dpad, stick, direction, lx, ly,
                        gp.bLeftTrigger, gp.bRightTrigger)

    def _emit(self, ev: InputEvent):
        try:
            ev.background = not self._is_foreground()
        except Exception:
            ev.background = False
        self.events.put(ev)

    def _run(self):
        winmm = None
        if sys.platform == "win32":
            try:
                # 讓 sleep 精準到 1 毫秒，否則 Windows 預設約 15 毫秒一次
                winmm = ctypes.windll.winmm
                winmm.timeBeginPeriod(1)
            except OSError:
                winmm = None
        interval = 1.0 / self.poll_hz
        prev = PadState()
        next_scan = 0.0
        count, count_start = 0, time.perf_counter()
        try:
            while not self._stop.is_set():
                loop_start = time.perf_counter()
                st = None
                if self.controller_index >= 0:
                    st = self._read(self.controller_index)
                    if st is None:
                        self.controller_index = -1
                        self._emit(InputEvent(loop_start, "disconnect"))
                elif loop_start >= next_scan:
                    # 沒連線時每秒找一次，避免一直查詢浪費資源
                    idx, st = self._find_controller()
                    next_scan = loop_start + 1.0
                    if st is not None:
                        self.controller_index = idx
                        self._emit(InputEvent(loop_start, "connect", extra={"index": idx}))

                cur = self._to_state(st) if st is not None else PadState()
                now = time.perf_counter()
                if cur.connected:
                    for b in FACE_BUTTONS:
                        if b in cur.buttons and b not in prev.buttons:
                            self._emit(InputEvent(now, "press", button=b, direction=cur.direction))
                        elif b not in cur.buttons and b in prev.buttons:
                            self._emit(InputEvent(now, "release", button=b, direction=cur.direction))
                    if cur.direction != prev.direction:
                        self._emit(InputEvent(now, "dir", direction=cur.direction,
                                              prev_direction=prev.direction))
                prev = cur
                with self._lock:
                    self._state = cur

                count += 1
                if now - count_start >= 1.0:
                    self.measured_hz = count / (now - count_start)
                    count, count_start = 0, now

                remain = interval - (time.perf_counter() - loop_start)
                if remain > 0:
                    time.sleep(remain)
        except Exception:
            log.exception("控制器讀取執行緒發生錯誤")
        finally:
            if winmm is not None:
                winmm.timeEndPeriod(1)
