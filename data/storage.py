"""連段庫：存在 %APPDATA%\\SF6ComboTrainer-mod\\combos.db（SQLite）。"""
from __future__ import annotations

import json
import logging
import sqlite3
import time
from pathlib import Path

from app_info import data_dir

SCHEMA_VERSION = 7
log = logging.getLogger(__name__)


MAX_TAGS = 30
MAX_TAG_LEN = 30


def clean_tags(tags) -> list[str]:
    """標籤整理：去空白、去重複（保留順序）、限制數量與長度。"""
    out: list[str] = []
    for t in tags or []:
        t = str(t).strip()[:MAX_TAG_LEN]
        if t and t not in out:
            out.append(t)
    return out[:MAX_TAGS]


def _row(r) -> dict:
    d = dict(r)
    try:
        d["tags"] = clean_tags(json.loads(d.get("tags") or "[]"))
    except (TypeError, ValueError):
        d["tags"] = []
    return d


def db_path() -> Path:
    return data_dir() / "combos.db"


class ComboStore:
    def __init__(self, path: Path | None = None):
        self.path = path or db_path()
        self.broken_backup: Path | None = None     # 連段庫壞掉時，原檔改名後的位置（給主視窗提示用）
        try:
            self._open()
        except sqlite3.DatabaseError:
            log.exception("連段庫讀取失敗")
            try:
                self.conn.close()
            except Exception:
                pass
            # 壞檔不刪，改名留著，再建立一個新的連段庫
            backup = self.path.with_name(f"combos.broken-{time.strftime('%Y%m%d-%H%M%S')}.db")
            self.path.rename(backup)
            self.broken_backup = backup
            self._open()

    def _open(self):
        self.conn = sqlite3.connect(str(self.path))
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA quick_check").fetchone()
        self._migrate()

    def _migrate(self):
        version = self.conn.execute("PRAGMA user_version").fetchone()[0]
        if version < 1:
            self.conn.executescript("""
                CREATE TABLE IF NOT EXISTS combos (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    character TEXT NOT NULL,
                    name TEXT NOT NULL DEFAULT '',
                    notation TEXT NOT NULL DEFAULT '',
                    notes TEXT NOT NULL DEFAULT '',
                    damage INTEGER,                -- 手動填的傷害，NULL＝還沒填
                    drive_override REAL,           -- 手動改的動力槽，NULL＝自動計算
                    sa_override INTEGER,           -- 手動改的氣槽，NULL＝自動計算
                    sort INTEGER NOT NULL DEFAULT 0,
                    created REAL NOT NULL,
                    updated REAL NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_combos_character ON combos(character, sort);
            """)
            self.conn.execute("PRAGMA user_version = 1")
            self.conn.commit()
        if version < 2:
            # 練習紀錄：每一次練習（完成或中途重來）存一筆
            self.conn.executescript("""
                CREATE TABLE IF NOT EXISTS practice (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    combo_id INTEGER NOT NULL,
                    notation TEXT NOT NULL DEFAULT '',
                    started REAL NOT NULL,
                    finished REAL NOT NULL,
                    total_steps INTEGER NOT NULL,
                    completed_steps INTEGER NOT NULL,
                    gaps TEXT NOT NULL DEFAULT '[]',    -- 每一步距上一步幾幀（JSON）
                    strict INTEGER NOT NULL DEFAULT 0
                );
                CREATE INDEX IF NOT EXISTS idx_practice_combo ON practice(combo_id, started);
            """)
            self.conn.execute("PRAGMA user_version = 2")
            self.conn.commit()
        if version < 3:
            # 從連段分享網站加入的連段：記住網站上的代號，避免重複加入
            self.conn.executescript("""
                CREATE TABLE IF NOT EXISTS site_imports (
                    source_key TEXT PRIMARY KEY,
                    combo_id INTEGER NOT NULL,
                    imported REAL NOT NULL
                );
            """)
            self.conn.execute("PRAGMA user_version = 3")
            self.conn.commit()
        if version < 4:
            # 參考影片：網址＋從第幾秒開始播（原本的連段不受影響，預設空白）
            self.conn.executescript("""
                ALTER TABLE combos ADD COLUMN video_url TEXT NOT NULL DEFAULT '';
                ALTER TABLE combos ADD COLUMN video_t INTEGER NOT NULL DEFAULT 0;
            """)
            self.conn.execute("PRAGMA user_version = 4")
            self.conn.commit()
        if version < 5:
            # 標籤（也當作自己設的清單）：JSON 陣列
            self.conn.execute("ALTER TABLE combos ADD COLUMN tags TEXT NOT NULL DEFAULT '[]'")
            self.conn.execute("PRAGMA user_version = 5")
            self.conn.commit()
            self._backfill_site_tags()
        if version < 6:
            # 角色改名：阿爾俊的連段名稱、說明、標籤裡的舊名「阿朱恩」一起換掉
            for col in ("name", "notes", "tags"):
                self.conn.execute(f"UPDATE combos SET {col} = REPLACE({col}, '阿朱恩', '阿爾俊') "
                                  f"WHERE character = 'arjun' AND {col} LIKE '%阿朱恩%'")
            self.conn.execute("PRAGMA user_version = 6")
            self.conn.commit()
        if version < 7:
            # 遊戲判定：這個連段在遊戲裡打過最多幾 Hit（NULL＝還沒記過）
            self.conn.execute("ALTER TABLE combos ADD COLUMN game_hits INTEGER")
            self.conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
            self.conn.commit()

    def list(self, character: str) -> list[dict]:
        rows = self.conn.execute(
            "SELECT * FROM combos WHERE character=? ORDER BY sort, id", (character,)).fetchall()
        return [_row(r) for r in rows]

    def tags(self, character: str) -> list[str]:
        """這個角色用到的所有標籤（依名稱排序，起手類放最後）。"""
        found: set[str] = set()
        for r in self.list(character):
            found.update(r["tags"])
        return sorted(found, key=lambda t: (t.endswith("起手"), t))     # 起手類的排在後面

    def _backfill_site_tags(self):
        """升級時：之前從網站加入的連段，用電腦上存的網站清單補上標籤（只補還沒有標籤的）。"""
        try:
            from core import site_library as site
            rows = self.conn.execute(
                "SELECT s.source_key, c.id FROM site_imports s JOIN combos c ON c.id = s.combo_id "
                "WHERE c.tags = '[]'").fetchall()
            if not rows:
                return
            items, _ = site.load_cache()
            by_key = {}
            for it in items:
                c = site.to_combo(it)
                if c:
                    by_key[c.key] = c
            with self.conn:
                for key, cid in rows:
                    if key in by_key:
                        self.conn.execute("UPDATE combos SET tags=? WHERE id=?",
                                          (json.dumps(site.combo_tags(by_key[key]), ensure_ascii=False), cid))
        except Exception:
            log.exception("補網站連段標籤失敗（不影響其他功能）")

    def get(self, combo_id: int) -> dict | None:
        row = self.conn.execute("SELECT * FROM combos WHERE id=?", (combo_id,)).fetchone()
        return _row(row) if row else None

    def create(self, character: str, name: str, notation: str = "", notes: str = "") -> int:
        now = time.time()
        sort = self.conn.execute("SELECT COALESCE(MAX(sort), 0) + 1 FROM combos WHERE character=?",
                                 (character,)).fetchone()[0]
        cur = self.conn.execute(
            "INSERT INTO combos(character, name, notation, notes, sort, created, updated) "
            "VALUES (?,?,?,?,?,?,?)", (character, name, notation, notes, sort, now, now))
        self.conn.commit()
        return cur.lastrowid

    def reorder(self, character: str, ids: list[int]):
        """拖動排序：ids 是畫面上（可能是篩選後的一部分）新的順序。
        只在這些連段原本佔的位置之間重新排，其他連段的位置不動。"""
        rows = [r["id"] for r in self.conn.execute(
            "SELECT id FROM combos WHERE character=? ORDER BY sort, id", (character,))]
        moving = set(ids)
        queue = [i for i in ids if i in rows]
        new_order = [queue.pop(0) if rid in moving and queue else rid for rid in rows]
        with self.conn:
            for n, rid in enumerate(new_order, 1):
                self.conn.execute("UPDATE combos SET sort=? WHERE id=?", (n, rid))

    def update(self, combo_id: int, **fields):
        allowed = {"name", "notation", "notes", "damage", "drive_override", "sa_override", "sort", "video_url", "video_t",
                   "tags", "game_hits"}
        fields = {k: v for k, v in fields.items() if k in allowed}
        if not fields:
            return
        if "notation" in fields and "game_hits" not in fields:
            # 連段內容改了，之前記下的 Hit 數就不準了
            row = self.conn.execute("SELECT notation FROM combos WHERE id=?", (combo_id,)).fetchone()
            if row and row[0] != fields["notation"]:
                fields["game_hits"] = None
        if "tags" in fields:
            fields["tags"] = json.dumps(clean_tags(fields["tags"]), ensure_ascii=False)
        fields["updated"] = time.time()
        cols = ", ".join(f"{k}=?" for k in fields)
        self.conn.execute(f"UPDATE combos SET {cols} WHERE id=?", (*fields.values(), combo_id))
        self.conn.commit()

    def duplicate(self, combo_id: int) -> int | None:
        src = self.get(combo_id)
        if not src:
            return None
        new_id = self.create(src["character"], src["name"] + "（複製）", src["notation"], src["notes"])
        self.update(new_id, damage=src["damage"], drive_override=src["drive_override"],
                    sa_override=src["sa_override"], video_url=src["video_url"], video_t=src["video_t"],
                    tags=src["tags"])
        return new_id

    def delete(self, combo_id: int):
        """只在使用者於程式裡二次確認後呼叫。"""
        self.conn.execute("DELETE FROM combos WHERE id=?", (combo_id,))
        self.conn.commit()

    def add_practice(self, combo_id: int, notation: str, started: float, total: int,
                     completed: int, gaps: list, strict: bool) -> int:
        cur = self.conn.execute(
            "INSERT INTO practice(combo_id, notation, started, finished, total_steps, completed_steps, gaps, strict) "
            "VALUES (?,?,?,?,?,?,?,?)",
            (combo_id, notation, started, time.time(), total, completed,
             json.dumps([None if g is None else round(g, 1) for g in gaps]), int(strict)))
        self.conn.commit()
        return cur.lastrowid

    def practice_stats(self, combo_id: int) -> dict:
        row = self.conn.execute(
            "SELECT COUNT(*), SUM(completed_steps = total_steps) FROM practice WHERE combo_id=?",
            (combo_id,)).fetchone()
        return {"tries": row[0] or 0, "clears": row[1] or 0}

    # ---- 練習紀錄 ----
    def list_practice(self, character: str, combo_id: int = 0, since: float = 0,
                      limit: int = 500, offset: int = 0) -> list[dict]:
        """紀錄清單（新的在前）。combo_id=0 是這個角色全部；-1 是已刪除連段留下的紀錄。"""
        sql, args = self._practice_filter(character, combo_id, since)
        rows = self.conn.execute(
            f"SELECT p.*, c.name AS combo_name FROM practice p LEFT JOIN combos c ON c.id = p.combo_id "
            f"WHERE {sql} ORDER BY p.started DESC, p.id DESC LIMIT ? OFFSET ?",
            (*args, limit, offset)).fetchall()
        out = []
        for r in rows:
            d = dict(r)
            try:
                d["gaps"] = json.loads(d["gaps"] or "[]")
            except ValueError:
                d["gaps"] = []
            out.append(d)
        return out

    def count_practice(self, character: str, combo_id: int = 0, since: float = 0) -> int:
        sql, args = self._practice_filter(character, combo_id, since)
        return self.conn.execute(
            f"SELECT COUNT(*) FROM practice p LEFT JOIN combos c ON c.id = p.combo_id WHERE {sql}",
            args).fetchone()[0]

    def has_orphan_practice(self) -> bool:
        return bool(self.conn.execute(
            "SELECT 1 FROM practice WHERE combo_id NOT IN (SELECT id FROM combos) LIMIT 1").fetchone())

    @staticmethod
    def _practice_filter(character: str, combo_id: int, since: float) -> tuple[str, list]:
        if combo_id == -1:
            sql, args = "c.id IS NULL", []
        elif combo_id:
            sql, args = "p.combo_id = ?", [combo_id]
        else:
            sql, args = "c.character = ?", [character]
        if since:
            sql += " AND p.started >= ?"
            args.append(since)
        return sql, args

    def delete_practice(self, ids: list[int]) -> int:
        """只在使用者於程式裡確認後呼叫。"""
        if not ids:
            return 0
        with self.conn:
            cur = self.conn.executemany("DELETE FROM practice WHERE id=?", [(i,) for i in ids])
        return cur.rowcount

    def practice_ids(self, character: str, combo_id: int = 0, since: float = 0) -> list[int]:
        sql, args = self._practice_filter(character, combo_id, since)
        return [r[0] for r in self.conn.execute(
            f"SELECT p.id FROM practice p LEFT JOIN combos c ON c.id = p.combo_id WHERE {sql}", args)]

    # ---- 匯出入 ----
    def characters(self) -> list[str]:
        return [r[0] for r in self.conn.execute("SELECT DISTINCT character FROM combos ORDER BY character")]

    def practice_for(self, combo_id: int) -> list[dict]:
        return [dict(r) for r in self.conn.execute(
            "SELECT * FROM practice WHERE combo_id=? ORDER BY started", (combo_id,))]

    def count_for_characters(self, characters: list[str]) -> tuple[int, int]:
        """這些角色現在有幾個連段、幾筆練習紀錄（取代前給確認視窗用）。"""
        if not characters:
            return 0, 0
        q = ",".join("?" * len(characters))
        combos = self.conn.execute(f"SELECT COUNT(*) FROM combos WHERE character IN ({q})",
                                   characters).fetchone()[0]
        practice = self.conn.execute(
            f"SELECT COUNT(*) FROM practice WHERE combo_id IN (SELECT id FROM combos WHERE character IN ({q}))",
            characters).fetchone()[0]
        return combos, practice

    def import_combos(self, items: list[dict], replace_characters: list[str] = ()) -> list[int]:
        """整批寫入：中途出錯就全部不算。replace_characters 裡的角色先清掉原本的連段和紀錄。
        items：{character, name, notation, notes, damage, drive_override, sa_override, practice:[...]}"""
        new_ids = []
        now = time.time()
        with self.conn:
            for ch in replace_characters:
                self.conn.execute("DELETE FROM practice WHERE combo_id IN (SELECT id FROM combos WHERE character=?)",
                                  (ch,))
                self.conn.execute("DELETE FROM combos WHERE character=?", (ch,))
            for it in items:
                sort = self.conn.execute("SELECT COALESCE(MAX(sort), 0) + 1 FROM combos WHERE character=?",
                                         (it["character"],)).fetchone()[0]
                cur = self.conn.execute(
                    "INSERT INTO combos(character, name, notation, notes, damage, drive_override, sa_override, "
                    "video_url, video_t, tags, sort, created, updated) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (it["character"], it["name"], it["notation"], it.get("notes", ""), it.get("damage"),
                     it.get("drive_override"), it.get("sa_override"), it.get("video_url") or "",
                     int(it.get("video_t") or 0), json.dumps(clean_tags(it.get("tags")), ensure_ascii=False),
                     sort, now, now))
                cid = cur.lastrowid
                new_ids.append(cid)
                for p in it.get("practice", []):
                    self.conn.execute(
                        "INSERT INTO practice(combo_id, notation, started, finished, total_steps, completed_steps, "
                        "gaps, strict) VALUES (?,?,?,?,?,?,?,?)",
                        (cid, p["notation"], p["started"], p["finished"], p["total_steps"],
                         p["completed_steps"], json.dumps(p["gaps"]), int(p["strict"])))
        return new_ids

    # ---- 連段分享網站 ----
    def site_imported_keys(self) -> set[str]:
        """已經加進連段庫、而且連段還在的網站代號。"""
        rows = self.conn.execute(
            "SELECT s.source_key FROM site_imports s JOIN combos c ON c.id = s.combo_id").fetchall()
        return {r[0] for r in rows}

    def add_site_combos(self, pairs: list[tuple[str, dict]]) -> list[int]:
        """整批加入網站連段：[(網站代號, 連段資料)]。中途出錯就全部不算。"""
        new_ids = []
        now = time.time()
        with self.conn:
            for key, it in pairs:
                sort = self.conn.execute("SELECT COALESCE(MAX(sort), 0) + 1 FROM combos WHERE character=?",
                                         (it["character"],)).fetchone()[0]
                cur = self.conn.execute(
                    "INSERT INTO combos(character, name, notation, notes, damage, drive_override, sa_override, "
                    "video_url, video_t, tags, sort, created, updated) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (it["character"], it["name"], it["notation"], it.get("notes", ""), it.get("damage"),
                     it.get("drive_override"), it.get("sa_override"), it.get("video_url") or "",
                     int(it.get("video_t") or 0), json.dumps(clean_tags(it.get("tags")), ensure_ascii=False),
                     sort, now, now))
                new_ids.append(cur.lastrowid)
                self.conn.execute("INSERT OR REPLACE INTO site_imports(source_key, combo_id, imported) "
                                  "VALUES (?,?,?)", (key, cur.lastrowid, now))
        return new_ids

    def backup(self, folder: Path) -> Path:
        """把整個連段庫複製一份（SQLite 內建的備份，寫入中也安全）。"""
        folder.mkdir(parents=True, exist_ok=True)
        target = folder / f"combos-{time.strftime('%Y%m%d-%H%M%S')}.db"
        n = 2
        while target.exists():
            target = folder / f"combos-{time.strftime('%Y%m%d-%H%M%S')}-{n}.db"
            n += 1
        dst = sqlite3.connect(str(target))
        try:
            self.conn.backup(dst)
        finally:
            dst.close()
        return target

    def close(self):
        self.conn.close()
