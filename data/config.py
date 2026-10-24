"""讀寫 config.json：缺少的欄位自動補預設值，舊版自動轉換，壞檔先備份再用預設值啟動。"""
from __future__ import annotations

import copy
import json
import logging
import time
import uuid
from pathlib import Path

from app_info import data_dir

log = logging.getLogger(__name__)

CONFIG_VERSION = 3

# 遊戲動作：基本 8 個固定，使用者可再新增自訂動作
BASE_ACTIONS = [
    {"code": "LP", "name": "輕拳"}, {"code": "MP", "name": "中拳"}, {"code": "HP", "name": "重拳"},
    {"code": "LK", "name": "輕腳"}, {"code": "MK", "name": "中腳"}, {"code": "HK", "name": "重腳"},
    {"code": "DP", "name": "DP（Parry）"}, {"code": "DI", "name": "DI（動力衝擊）"},
]

# 經典操作 Xbox 預設按鍵
DEFAULT_BUTTON_MAP = {"X": "LP", "Y": "MP", "RB": "HP", "A": "LK",
                      "B": "MK", "RT": "HK", "LB": "DP", "LT": "DI"}

# 範本：(訊號, 欄 x, 列 y, 形狀)。單位是一格按鍵寬
TEMPLATES = {
    # 方向鍵放左邊（Hitbox 排法），其他按鍵往右
    "xbox": [
        ("LEFT", 0, 0.5, "round"), ("DOWN", 1, 0.2, "round"), ("RIGHT", 2, 0.5, "round"),
        ("UP", 2.4, 2.3, "round"),
        ("LT", 3.6, 0, "rect"), ("LB", 4.6, 0, "rect"), ("RB", 7.6, 0, "rect"), ("RT", 8.6, 0, "rect"),
        ("Y", 7.6, 1, "round"), ("X", 6.6, 2, "round"), ("B", 8.6, 2, "round"), ("A", 7.6, 3, "round"),
        ("BACK", 3.6, 2, "rect"), ("START", 4.6, 2, "rect"), ("LS", 3.6, 3, "rect"), ("RS", 4.6, 3, "rect"),
    ],
    # 常見 Hitbox 排法：左手 左下右、拇指 上、右手兩排各四顆
    "hitbox": [
        ("LEFT", 0, 0.5, "round"), ("DOWN", 1, 0.2, "round"), ("RIGHT", 2, 0.5, "round"),
        ("UP", 3.3, 2.3, "round"),
        ("X", 3.6, 0.3, "round"), ("Y", 4.6, 0, "round"), ("RB", 5.6, 0, "round"), ("LB", 6.6, 0.2, "round"),
        ("A", 3.4, 1.3, "round"), ("B", 4.4, 1.0, "round"), ("RT", 5.4, 1.0, "round"), ("LT", 6.4, 1.2, "round"),
        ("BACK", 0, 2.4, "rect"), ("START", 1, 2.4, "rect"),
    ],
}
TEMPLATE_NAMES = {"xbox": "Xbox 手把", "hitbox": "Hitbox"}
DIR_INPUTS = ("LEFT", "DOWN", "RIGHT", "UP")
DIR_SPOTS = {"LEFT": (0, 0.5), "DOWN": (1, 0.2), "RIGHT": (2, 0.5), "UP": (2.4, 2.3)}
DIR_SHIFT = 3.6        # 補方向鍵時，原本的按鍵往右移多少
LAYOUT_COLS = 14       # 版面寬（單位），要跟 ui/key_canvas.py 的 EDIT_COLS 一樣


def add_direction_keys(layout: list[dict]) -> list[dict]:
    """版面裡缺少的方向鍵補上（放左邊）；左邊被佔用時，把原本的按鍵往右移。"""
    have = {k["inputs"][0] for k in layout if len(k["inputs"]) == 1}
    missing = [d for d in DIR_INPUTS if d not in have]
    if not missing:
        return layout
    if layout and min(k["x"] for k in layout) < DIR_SHIFT - 0.05:
        for k in layout:
            k["x"] = round(min(k["x"] + DIR_SHIFT, LAYOUT_COLS - 1), 2)
    for d in missing:
        x, y = DIR_SPOTS[d]
        layout.insert(0, new_key([d], x, y, "round"))
    return layout


def new_key(inputs: list[str], x: float, y: float, shape: str = "round",
            action: str = "", label: str = "") -> dict:
    return {"id": uuid.uuid4().hex[:8], "inputs": list(inputs), "x": x, "y": y,
            "shape": shape, "action": action, "label": label}


def make_layout(template: str, button_map: dict | None = None) -> list[dict]:
    button_map = DEFAULT_BUTTON_MAP if button_map is None else button_map
    return [new_key([btn], x, y, shape, button_map.get(btn, ""))
            for btn, x, y, shape in TEMPLATES[template]]


# 快捷鍵動作：(代號, 名稱)
HOTKEY_ACTIONS = [("overlay", "顯示／隱藏浮動視窗"), ("practice", "開始／停止練習"),
                  ("restart", "重來"), ("next", "下一個連段"), ("prev", "上一個連段"),
                  ("lock", "鎖定／解鎖浮動視窗")]
DEFAULT_HOTKEYS = {"overlay": "Ctrl+Alt+O", "practice": "Ctrl+Alt+P", "restart": "Ctrl+Alt+R",
                   "next": "Ctrl+Alt+Right", "prev": "Ctrl+Alt+Left", "lock": "Ctrl+Alt+L"}
# 控制器快捷鍵：按住 BACK 時再按的訊號（BACK 單按本來就是重來）
DEFAULT_PAD_HOTKEYS = {"overlay": "UP", "practice": "START", "restart": "",
                       "next": "RIGHT", "prev": "LEFT", "lock": "DOWN"}

DEFAULTS = {
    "config_version": CONFIG_VERSION,
    "controller_index": -1,          # -1 = 自動找第一個連上的控制器
    "direction_source": "both",      # both / dpad / stick
    "stick_deadzone": 0.5,           # 0～1，搖桿推超過這個比例才算有方向
    "trigger_threshold": 30,         # 0～255，板機按超過才算按下
    "combo_window_ms": 12,           # 這段時間內一起按下的訊號，可組成一顆組合按鍵
    "layout_template": "xbox",
    "layout": None,                  # 按鍵版面，None 表示用範本產生
    "custom_actions": [],            # [{"code": "C1", "name": "投擲"}]
    "window_geometry": None,         # [x, y, w, h]
    "window_maximized": False,       # 上次關閉時是不是放到最大
    "window_size": "1920",           # 開啟時的大小：1920＝1920×1080、full＝佔滿整個螢幕、last＝上次關閉時
    "window_screen": "",             # 在哪個螢幕開（螢幕名稱；空白＝主螢幕）
    "last_character": "ryu",
    "last_tab": 0,
    "trial_button_style": "pad",     # pad＝顯示我的控制器按鍵；text＝顯示 LP／MP 文字
    "trial_facing_right": True,      # 練習顯示的角色方向（False＝在右邊面向左，方向左右對調）
    "practice_strict": False,        # 嚴格模式：按錯的那一列閃紅
    "practice_reset_s": 3.0,         # 練到一半超過幾秒沒輸入就自動重來
    "practice_simul_frames": 3,      # 同時按容差（幀）
    "practice_charge_frames": 40,    # 蓄力至少按住幾幀
    "asked_desktop_shortcut": False,
    # v1.8.0 浮動視窗、快捷鍵、系統匣
    "hotkeys_enabled": True,                   # 鍵盤快捷鍵總開關
    "practice_autonext_s": 1.2,                # 練完換下一個之前等幾秒
    "hotkeys": dict(DEFAULT_HOTKEYS),          # 鍵盤全域快捷鍵，""＝不用
    "pad_hotkeys_enabled": False,              # 按住 BACK 再按另一顆鍵觸發動作
    "pad_hotkeys": dict(DEFAULT_PAD_HOTKEYS),
    "close_to_tray": True,                     # 按 ✕ 時縮到系統匣
    "tray_hint_shown": False,
    "overlay_visible": False,
    "overlay_locked": False,
    "overlay_geometry": None,                  # [x, y, w, h]
    "overlay_opacity": 0.85,
    "overlay_zoom": 1.0,
    "ui_font": "hand",                         # hand＝手寫風、jhenghei＝微軟正黑體
    "ui_theme": "light",                       # light＝淺色、dark＝深色、system＝跟著 Windows
    "show_numpad": True,                       # 連段清單下方顯示方向數字圖
    "exchange_dir": "",                        # 上次匯出／匯入用的資料夾
    "overlay_rows_shown": 0,                   # 浮動視窗顯示幾列（0＝全部，放不下就捲動）
    # v1.28.0：用遊戲裡的連擊數確認連段成功（REFramework 遊戲內腳本）
    "game_check_enabled": True,                # 輸入比對完成後，還要遊戲的連擊數沒斷才算成功
    "sf6_game_dir": "",                        # 遊戲資料夾（空白＝自動從 Steam 找）
    "mod_start_notice": True,                  # 遊戲啟動時 mod 開著，跳系統通知提醒
    # v1.29.0：連段斷了（或 Hit 數不夠）就替使用者按一下遊戲的重置鍵
    "auto_reset_enabled": False,
    "auto_reset_mode": "game",                 # game＝遊戲直接重置；state＝播放儲存狀態；key＝按鍵盤按鍵
    "auto_reset_delay_s": 0.0,                 # 失敗後等幾秒才重置（0～10）
    "auto_reset_key": "",                      # Qt 按鍵寫法，例如 "F5"；空白＝沒設定
}


def config_path() -> Path:
    return data_dir() / "config.json"


def _merge(defaults: dict, loaded: dict) -> dict:
    result = copy.deepcopy(defaults)
    for key, value in loaded.items():
        if key in result and isinstance(result[key], dict) and isinstance(value, dict):
            result[key] = {**result[key], **value}
        else:
            result[key] = value
    return result


def _migrate(data: dict) -> dict:
    version = data.get("config_version", 1)
    if version < 2:
        # v1 只有「實體按鍵 → 動作」，轉成 Xbox 版面並保留原本的對應
        old_map = data.get("button_map") or DEFAULT_BUTTON_MAP
        data["layout_template"] = "xbox"
        data["layout"] = make_layout("xbox", old_map)
        data["button_map_v1"] = data.pop("button_map", None)
    if version < 3 and isinstance(data.get("layout"), list):
        # v1.7.2：方向鍵也變成版面上的按鍵，可以拖曳調整位置
        data["layout"] = add_direction_keys(data["layout"])
    data["config_version"] = CONFIG_VERSION
    return data


# 預設是 None 的欄位，讀到時應該是什麼型別
_NULLABLE = {"layout": list, "window_geometry": list, "overlay_geometry": list}


# 數字設定的合理範圍（超出就改回預設值）
_RANGES = [("overlay_opacity", 0.2, 1.0), ("overlay_zoom", 0.5, 2.0), ("overlay_rows_shown", 0, 30),
           ("stick_deadzone", 0.05, 0.95), ("combo_window_ms", 0, 100), ("trigger_threshold", 1, 254),
           ("practice_reset_s", 1, 10), ("practice_simul_frames", 1, 10), ("practice_charge_frames", 10, 90),
           ("last_tab", 0, 2)]


def _fix_types(cfg: dict) -> dict:
    """設定檔被手動改壞（例如數字變成文字）時，那一欄改回預設值，其他設定照常保留。"""
    for key, default in DEFAULTS.items():
        value = cfg.get(key)
        if default is None:
            want = _NULLABLE.get(key)
            ok = value is None or want is None or isinstance(value, want)
        elif isinstance(default, bool):
            ok = isinstance(value, bool)
        elif isinstance(default, (int, float)):
            ok = isinstance(value, (int, float)) and not isinstance(value, bool)
        else:
            ok = isinstance(value, type(default))
        if not ok:
            log.warning("設定「%s」的內容不對，改回預設值", key)
            cfg[key] = copy.deepcopy(default)
    for key, lo, hi in _RANGES:
        if not lo <= cfg[key] <= hi:
            log.warning("設定「%s」超出範圍，改回預設值", key)
            cfg[key] = DEFAULTS[key]
    if cfg["window_size"] not in ("1920", "full", "last"):
        cfg["window_size"] = "1920"
    if cfg["ui_theme"] not in ("light", "dark", "system"):
        cfg["ui_theme"] = "light"
    for key in ("window_geometry", "overlay_geometry"):
        g = cfg.get(key)
        if g is not None and not (len(g) == 4 and all(isinstance(v, (int, float)) for v in g)):
            cfg[key] = None
    if isinstance(cfg.get("layout"), list):
        keys = []
        for k in cfg["layout"]:
            ok = (isinstance(k, dict) and isinstance(k.get("inputs"), list) and k["inputs"]
                  and all(isinstance(i, str) for i in k["inputs"])
                  and all(isinstance(k.get(c), (int, float)) for c in ("x", "y")))
            if not ok:
                log.warning("按鍵版面裡有一顆鍵的資料不對，略過")
                continue
            for field_, default in (("id", ""), ("shape", "round"), ("action", ""), ("label", "")):
                if not isinstance(k.get(field_), str):
                    k[field_] = default
            if not k["id"]:
                k["id"] = uuid.uuid4().hex[:8]
            keys.append(k)
        cfg["layout"] = keys or None
    cfg["custom_actions"] = [a for a in cfg["custom_actions"]
                             if isinstance(a, dict) and isinstance(a.get("code"), str) and isinstance(a.get("name"), str)]
    return cfg


def finalize(cfg: dict) -> dict:
    _fix_types(cfg)
    if not isinstance(cfg.get("layout"), list):
        cfg["layout"] = make_layout(cfg.get("layout_template", "xbox"))
    return cfg


def all_actions(cfg: dict) -> list[dict]:
    return BASE_ACTIONS + list(cfg.get("custom_actions", []))


def action_title(a: dict) -> str:
    """選單上顯示的動作名稱，例如「輕拳（LP）」；名稱已含代號時（DP（Parry））不重複。"""
    return a["name"] if a["code"] in a["name"] else f"{a['name']}（{a['code']}）"


def action_name(cfg: dict, code: str) -> str:
    for a in all_actions(cfg):
        if a["code"] == code:
            return a["name"]
    return ""


def load_config() -> tuple[dict, str | None]:
    """回傳（設定, 要告訴使用者的訊息或 None）。"""
    path = config_path()
    if not path.exists():
        return finalize(copy.deepcopy(DEFAULTS)), None
    try:
        loaded = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(loaded, dict):
            raise ValueError("設定檔內容不是物件")
        old_version = loaded.get("config_version", 1)
        if old_version < CONFIG_VERSION:
            # 轉換前先留一份原本的設定
            keep = path.with_name(f"config.v{old_version}-backup.json")
            if not keep.exists():
                try:
                    keep.write_text(path.read_text(encoding="utf-8"), encoding="utf-8")
                except OSError:
                    log.warning("舊設定備份失敗：%s", keep)
        return finalize(_merge(DEFAULTS, _migrate(loaded))), None
    except Exception:
        log.exception("設定檔讀取失敗")
        backup = path.with_name(f"config.broken-{time.strftime('%Y%m%d-%H%M%S')}.json")
        try:
            path.rename(backup)
        except OSError:
            backup = None
        msg = "設定檔損壞，已改用預設值啟動。"
        if backup:
            msg += f"\n原本的檔案備份在：{backup}"
        return finalize(copy.deepcopy(DEFAULTS)), msg


def save_config(cfg: dict):
    path = config_path()
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)
