"""角色指令表：程式內建一份（data/movelists），使用者修改過的另存在 %APPDATA%，更新程式不會蓋掉。"""
from __future__ import annotations

import copy
import json
import logging
from pathlib import Path

from app_info import PROJECT_DIR, data_dir

log = logging.getLogger(__name__)
BUILTIN_DIR = PROJECT_DIR / "data" / "movelists"
# 角色順序照官網
ORDER = ["ryu", "luke", "jamie", "chunli", "guile", "kimberly", "juri", "ken", "blanka", "dhalsim",
         "ehonda", "deejay", "manon", "marisa", "jp", "zangief", "lily", "cammy", "rashid", "aki",
         "ed", "gouki_akuma", "vega_mbison", "terry", "mai", "elena", "sagat", "cviper", "alex",
         "ingrid", "yasmine", "arjun"]


def user_dir() -> Path:
    path = data_dir() / "movelists"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _read(path: Path) -> dict | None:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        log.exception("指令表讀取失敗：%s", path)
        return None


def load_all() -> dict[str, dict]:
    """回傳 {角色 id: 指令表}；使用者版本優先。"""
    result: dict[str, dict] = {}
    names: dict[str, str] = {}
    for folder in (BUILTIN_DIR, user_dir()):
        if not folder.exists():
            continue
        for path in sorted(folder.glob("*.json")):
            data = _read(path)
            if data and data.get("id"):
                if folder == BUILTIN_DIR and data.get("name"):
                    names[data["id"]] = data["name"]
                result[data["id"]] = data
    # 角色名字一律用內建的，程式改了名字，改過的指令表也會跟著換
    for cid, name in names.items():
        result[cid]["name"] = name
    ordered = {cid: result[cid] for cid in ORDER if cid in result}
    ordered.update({cid: d for cid, d in result.items() if cid not in ordered})
    return ordered


def save_user(movelist: dict):
    path = user_dir() / f"{movelist['id']}.json"
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(movelist, ensure_ascii=False, indent=1), encoding="utf-8")
    tmp.replace(path)


def builtin(cid: str) -> dict | None:
    path = BUILTIN_DIR / f"{cid}.json"
    return copy.deepcopy(_read(path)) if path.exists() else None
