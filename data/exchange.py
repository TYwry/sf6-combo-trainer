"""匯出、匯入連段（.json 檔）。

檔案格式：
{"format": "sf6-combo-trainer", "version": 1, "app_version": "...", "exported": "2026-10-16 20:00",
 "combos": [{"character", "name", "notation", "notes", "damage", "drive_override", "sa_override",
             "video_url", "video_t", "tags",
             "practice": [{"notation", "started", "finished", "total_steps", "completed_steps", "gaps", "strict"}]}],
 "snippets": [{"name", "notation"}]}
"""
from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from pathlib import Path

from app_info import APP_VERSION, data_dir
from core import video
from core.notation import normalize_simul
from data import snippets
from data.storage import ComboStore, clean_tags

FORMAT = "sf6-combo-trainer"
VERSION = 1
MAX_BYTES = 20 * 1024 * 1024       # 檔案太大就不讀，避免卡住
MAX_NAME = 80
MAX_NOTATION = 2000
MAX_NOTES = 4000


def backup_dir() -> Path:
    return data_dir() / "backups"


# ---- 匯出 ----
def build_export(store: ComboStore, combo_ids: list[int], with_practice: bool, with_snippets: bool) -> dict:
    combos = []
    for cid in combo_ids:
        row = store.get(cid)
        if not row:
            continue
        item = {k: row[k] for k in ("character", "name", "notation", "notes", "damage",
                                    "drive_override", "sa_override", "video_url", "video_t", "tags")}
        if with_practice:
            item["practice"] = [{
                "notation": p["notation"], "started": p["started"], "finished": p["finished"],
                "total_steps": p["total_steps"], "completed_steps": p["completed_steps"],
                "gaps": _json_list(p["gaps"]), "strict": bool(p["strict"])} for p in store.practice_for(cid)]
        combos.append(item)
    data = {"format": FORMAT, "version": VERSION, "app_version": APP_VERSION,
            "exported": time.strftime("%Y-%m-%d %H:%M"), "combos": combos}
    if with_snippets:
        data["snippets"] = snippets.load()
    return data


def write_file(path: Path, data: dict):
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)


def _json_list(text) -> list:
    if isinstance(text, list):
        return text
    try:
        v = json.loads(text or "[]")
        return v if isinstance(v, list) else []
    except ValueError:
        return []


# ---- 匯入：讀檔與檢查 ----
class ImportError_(Exception):
    """檔案不能用；訊息是給使用者看的白話。"""


@dataclass
class Parsed:
    combos: list[dict] = field(default_factory=list)
    snippets: list[dict] = field(default_factory=list)
    problems: list[str] = field(default_factory=list)   # 略過的項目和原因
    exported: str = ""


def read_file(path: Path, known_characters: dict[str, str]) -> Parsed:
    """known_characters：角色代號 → 顯示名稱。程式裡沒有的角色會略過並說明。"""
    try:
        if path.stat().st_size > MAX_BYTES:
            raise ImportError_("這個檔案太大了（超過 20 MB），看起來不是連段檔。")
        raw = json.loads(path.read_text(encoding="utf-8-sig"))
    except ImportError_:
        raise
    except UnicodeDecodeError:
        raise ImportError_("這個檔案不是文字檔，沒辦法讀。請選這個程式匯出的 .json 檔。")
    except ValueError:
        raise ImportError_("這個檔案的內容壞掉了，或不是 JSON 格式，沒辦法讀。")
    except OSError as e:
        raise ImportError_(f"打不開這個檔案：{e.strerror or e}")
    if not isinstance(raw, dict) or raw.get("format") != FORMAT:
        raise ImportError_("這個檔案不是「SF6 連段練習器」匯出的連段檔。")
    if not isinstance(raw.get("version"), int) or raw["version"] > VERSION:
        raise ImportError_("這個檔案是用比較新版的程式匯出的，請先更新程式再匯入。")

    out = Parsed(exported=str(raw.get("exported", ""))[:40])
    unknown: dict[str, int] = {}
    bad = 0
    for item in raw.get("combos") or []:
        c = _clean_combo(item)
        if c is None:
            bad += 1
            continue
        if c["character"] not in known_characters:
            unknown[c["character"]] = unknown.get(c["character"], 0) + 1
            continue
        out.combos.append(c)
    for ch, n in unknown.items():
        out.problems.append(f"程式裡沒有「{ch[:20]}」這個角色，它的 {n} 個連段先略過。")
    if bad:
        out.problems.append(f"有 {bad} 個連段的資料不完整，已略過。")
    for s in raw.get("snippets") or []:
        if isinstance(s, dict) and str(s.get("notation", "")).strip():
            out.snippets.append({"name": str(s.get("name", ""))[:MAX_NAME],
                                 "notation": normalize_simul(str(s["notation"])[:MAX_NOTATION])})
    return out


def _num(v, kind=float):
    if v is None or isinstance(v, bool):
        return None
    try:
        return kind(v)
    except (TypeError, ValueError):
        return None


def _clean_combo(item) -> dict | None:
    if not isinstance(item, dict):
        return None
    ch = item.get("character")
    if not isinstance(ch, str) or not ch.strip():
        return None
    damage = _num(item.get("damage"), int)
    drive = _num(item.get("drive_override"))
    sa = _num(item.get("sa_override"), int)
    url = str(item.get("video_url") or "").strip()[:500]
    vt = _num(item.get("video_t"), int)
    c = {"character": ch.strip()[:40],
         "name": str(item.get("name") or "").strip()[:MAX_NAME] or "（沒有名稱）",
         "notation": normalize_simul(str(item.get("notation") or "")[:MAX_NOTATION]),
         "notes": str(item.get("notes") or "")[:MAX_NOTES],
         "damage": damage if damage and 0 < damage <= 20000 else None,
         "drive_override": drive if drive is not None and 0 <= drive <= 12 else None,
         "sa_override": sa if sa is not None and 0 <= sa <= 6 else None,
         "video_url": url if video.is_web_url(url) else "",
         "video_t": vt if vt is not None and 0 <= vt <= video.MAX_T else 0,
         "tags": clean_tags(item.get("tags") if isinstance(item.get("tags"), list) else []),
         "practice": []}
    for p in item.get("practice") or []:
        if not isinstance(p, dict):
            continue
        started, finished = _num(p.get("started")), _num(p.get("finished"))
        total, done = _num(p.get("total_steps"), int), _num(p.get("completed_steps"), int)
        if None in (started, finished, total, done) or total <= 0 or not 0 <= done <= total:
            continue
        gaps = [None if g is None else _num(g) for g in _json_list(p.get("gaps"))][:500]
        c["practice"].append({"notation": normalize_simul(str(p.get("notation") or "")[:MAX_NOTATION]),
                              "started": started, "finished": max(finished, started),
                              "total_steps": total, "completed_steps": done,
                              "gaps": gaps, "strict": bool(p.get("strict"))})
    return c


# ---- 匯入：寫進去 ----
@dataclass
class Result:
    added: int = 0
    renamed: int = 0
    skipped: int = 0
    practice: int = 0
    snippets_added: int = 0
    snippets_skipped: int = 0
    backup: Path | None = None
    new_ids: list[int] = field(default_factory=list)


def apply_import(store: ComboStore, combos: list[dict], snippet_items: list[dict], replace: bool) -> Result:
    """replace=True：檔案裡有的角色，原本的連段換成檔案裡的（先自動備份）。"""
    res = Result()
    replace_chars = sorted({c["character"] for c in combos}) if replace else []
    if replace_chars:
        res.backup = store.backup(backup_dir())
    to_add = []
    existing: dict[str, dict[str, set]] = {}     # 角色 → 名稱 → 記法們
    for c in combos:
        ch = c["character"]
        if ch not in existing:
            existing[ch] = {}
            if ch not in replace_chars:
                for row in store.list(ch):
                    existing[ch].setdefault(row["name"], set()).add(row["notation"].strip())
        names = existing[ch]
        item = dict(c)
        if item["notation"].strip() in names.get(item["name"], set()):
            res.skipped += 1
            continue
        if item["name"] in names:
            base = item["name"] + "（匯入）"
            item["name"], n = base, 2
            while item["name"] in names:
                item["name"] = f"{base}{n}"
                n += 1
            res.renamed += 1
        names.setdefault(item["name"], set()).add(item["notation"].strip())
        to_add.append(item)
    res.new_ids = store.import_combos(to_add, replace_chars)
    res.added = len(to_add)
    res.practice = sum(len(c.get("practice", [])) for c in to_add)

    if snippet_items:
        mine = snippets.load()
        names = {s["name"]: s["notation"] for s in mine}
        for s in snippet_items:
            if names.get(s["name"]) == s["notation"] or any(m["notation"] == s["notation"] and m["name"] == s["name"]
                                                             for m in mine):
                res.snippets_skipped += 1
                continue
            name = s["name"]
            if name in names:
                name, n = name + "（匯入）", 2
                while name in names:
                    name = f"{s['name']}（匯入）{n}"
                    n += 1
            mine.append({"name": name, "notation": s["notation"]})
            names[name] = s["notation"]
            res.snippets_added += 1
        if res.snippets_added:
            snippets.save(mine)
    return res
