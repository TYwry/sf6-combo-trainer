"""圖片資料夾流程的自動匯入。

把連段截圖放進「圖片\\待處理」，讀完後把結果做成匯入檔放進「imports」。
練習器開著時每隔幾秒看一下 imports：有新檔案就自動「加入」（不取代、不問），
成功後把匯入檔搬到「imports\\已匯入」、把那批圖片從「待處理」搬到「已處理」。
只會搬檔案，不刪任何檔案；同名時在檔名後面加數字。

匯入檔就是一般的匯出格式，另外可以多一個 "images": ["1-23.png", ...]（待處理裡的檔名）。
"""
from __future__ import annotations

import json
import logging
import shutil
import time
from dataclasses import dataclass, field
from pathlib import Path

from app_info import APP_DIR
from data import exchange
from data.storage import ComboStore

log = logging.getLogger(__name__)

IMAGES_TODO = APP_DIR / "圖片" / "待處理"
IMAGES_DONE = APP_DIR / "圖片" / "已處理"
IMPORTS = APP_DIR / "imports"
IMPORTS_DONE = IMPORTS / "已匯入"
SETTLE_S = 2.0          # 檔案寫入後等這麼久才讀，避免讀到寫一半的檔案


def ensure_folders():
    for p in (IMAGES_TODO, IMAGES_DONE, IMPORTS, IMPORTS_DONE):
        p.mkdir(parents=True, exist_ok=True)


def _free_name(folder: Path, name: str) -> Path:
    target = folder / name
    stem, suffix, n = target.stem, target.suffix, 2
    while target.exists():
        target = folder / f"{stem}（{n}）{suffix}"
        n += 1
    return target


def _move(src: Path, folder: Path) -> Path:
    folder.mkdir(parents=True, exist_ok=True)
    target = _free_name(folder, src.name)
    shutil.move(str(src), str(target))
    return target


@dataclass
class Done:
    file: str
    added: int = 0
    skipped: int = 0
    per_character: dict = field(default_factory=dict)
    images_moved: int = 0
    new_ids: list = field(default_factory=list)
    error: str = ""


class AutoImporter:
    def __init__(self, store: ComboStore, known_characters: dict[str, str]):
        self.store = store
        self.known = known_characters
        self._failed: dict[str, float] = {}      # 讀不了的檔案 → 當時的修改時間（檔案沒變就不再報錯）

    def check(self) -> list[Done]:
        try:
            ensure_folders()
            files = sorted(IMPORTS.glob("*.json"))
        except OSError:
            log.exception("自動匯入：資料夾讀取失敗")
            return []
        out = []
        now = time.time()
        for path in files:
            try:
                mtime = path.stat().st_mtime
            except OSError:
                continue
            if now - mtime < SETTLE_S or self._failed.get(path.name) == mtime:
                continue
            out.append(self._one(path, mtime))
        return out

    def _one(self, path: Path, mtime: float) -> Done:
        done = Done(path.name)
        try:
            parsed = exchange.read_file(path, self.known)
        except exchange.ImportError_ as e:
            self._failed[path.name] = mtime
            done.error = str(e)
            return done
        try:
            res = exchange.apply_import(self.store, parsed.combos, [], replace=False)
        except Exception as e:
            log.exception("自動匯入失敗")
            self._failed[path.name] = mtime
            done.error = f"寫進連段庫時出錯，這個檔案沒有匯入：{e}"
            return done
        done.added, done.skipped, done.new_ids = res.added, res.skipped, res.new_ids
        for c in parsed.combos:
            done.per_character[c["character"]] = done.per_character.get(c["character"], 0) + 1
        # 圖片：從待處理搬到已處理
        try:
            raw = json.loads(path.read_text(encoding="utf-8-sig"))
            names = [str(n) for n in raw.get("images") or [] if isinstance(n, str)]
        except (OSError, ValueError):
            names = []
        for name in names:
            src = IMAGES_TODO / Path(name).name          # 只接受待處理資料夾裡的檔名
            if src.is_file():
                try:
                    _move(src, IMAGES_DONE)
                    done.images_moved += 1
                except OSError:
                    log.exception("搬圖片失敗：%s", src)
        try:
            _move(path, IMPORTS_DONE)
        except OSError:
            log.exception("搬匯入檔失敗：%s", path)
            self._failed[path.name] = mtime          # 搬不走就別一直重複匯入
        return done
