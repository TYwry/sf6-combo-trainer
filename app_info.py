"""程式名稱、版本與路徑等共用資訊。"""
import os
import sys
from pathlib import Path

APP_NAME = "SF6ComboTrainer"
APP_DISPLAY_NAME = "SF6 連段練習器"
APP_VERSION = "1.31.1"
APP_USER_MODEL_ID = "sf6-combo-trainer"

# 分享版（PyInstaller 打包成 exe）時 sys.frozen 為 True：
# 程式內附的檔案（字型、指令表、遊戲內腳本）在 sys._MEIPASS，exe 本身放在 APP_DIR。
FROZEN = bool(getattr(sys, "frozen", False))
PROJECT_DIR = Path(getattr(sys, "_MEIPASS", "")) if FROZEN else Path(__file__).resolve().parent
APP_DIR = Path(sys.executable).resolve().parent if FROZEN else PROJECT_DIR
ASSETS_DIR = PROJECT_DIR / "assets"


def data_dir() -> Path:
    """使用者的設定與紀錄放在 %APPDATA%\\SF6ComboTrainer\\。"""
    base = os.environ.get("APPDATA")
    root = Path(base) if base else Path.home() / ".config"
    path = root / APP_NAME
    path.mkdir(parents=True, exist_ok=True)
    return path


def logs_dir() -> Path:
    path = data_dir() / "logs"
    path.mkdir(parents=True, exist_ok=True)
    return path
