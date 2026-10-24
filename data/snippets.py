"""自訂庫：使用者自己的常用記法按鈕，存在 %APPDATA%\\SF6ComboTrainer\\snippets.json，所有角色共用。"""
from __future__ import annotations

import json
import logging
import time

from app_info import data_dir

log = logging.getLogger(__name__)


def path():
    return data_dir() / "snippets.json"


def load() -> list[dict]:
    p = path()
    if not p.exists():
        return []
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
        items = data.get("items", []) if isinstance(data, dict) else []
        return [{"name": str(i.get("name", "")), "notation": str(i.get("notation", ""))}
                for i in items if isinstance(i, dict) and i.get("notation")]
    except Exception:
        log.exception("自訂庫讀取失敗")
        # 壞檔先備份，不覆蓋
        backup = p.with_name(f"snippets.broken-{time.strftime('%Y%m%d-%H%M%S')}.json")
        try:
            p.rename(backup)
        except OSError:
            pass
        return []


def save(items: list[dict]):
    p = path()
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps({"version": 1, "items": items}, ensure_ascii=False, indent=2),
                   encoding="utf-8")
    tmp.replace(p)
