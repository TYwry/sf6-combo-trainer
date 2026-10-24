"""從連段分享網站 sf6cm.sctrip.asia 讀連段清單，轉成這個程式的記法。

網站的連段庫是從公開網址載入的（不用登入）：
  /api/v1/library?controlType=classic&page=1&pageSize=50
每一筆有 title、character、author、tags、summaryMetadata（damage、driveCost、superCost、starter…）、
displaySteps（[{order, notation, note}]，notation 像 `2 1 4 + MK`、`2 3 6 + K K`、`DR（绿冲）`）。
抓下來的原始資料存在 %APPDATA%\\SF6ComboTrainer\\site_cache.json，下次打開不用重抓。
"""
from __future__ import annotations

import json
import logging
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path

from app_info import APP_VERSION, data_dir
from core.notation import normalize_simul, parse

log = logging.getLogger(__name__)

SITE = "https://sf6cm.sctrip.asia"
LIBRARY = "/api/v1/library"
PAGE_SIZE = 50                 # 網站一次最多給 50 筆
PAGE_DELAY = 1.0               # 每頁之間停一下，不要一直打網站
TIMEOUT = 20
MAX_PAGES = 200                # 保險：最多讀這麼多頁
MAX_BYTES = 8 * 1024 * 1024    # 一頁回應的大小上限

# 網站的角色代號 → 這個程式的角色 id
SITE_TO_LOCAL = {"chun-li": "chunli", "dee-jay": "deejay", "e-honda": "ehonda", "c-viper": "cviper",
                 "akuma": "gouki_akuma", "vega": "vega_mbison"}
# 網站自己也接受的別名（從網站程式碼抄來的）
ALIASES = {"chunli": "chun-li", "chun li": "chun-li", "cviper": "c-viper", "deejay": "dee-jay",
           "dee jay": "dee-jay", "ehonda": "e-honda", "e. honda": "e-honda", "e honda": "e-honda",
           "gouki": "akuma", "mbison": "vega", "m bison": "vega", "m. bison": "vega"}


def cache_path() -> Path:
    return data_dir() / "site_cache.json"


def site_slug(name: str) -> str:
    n = (name or "").strip().lower()
    return ALIASES.get(n) or re.sub(r"[_.\s]+", "-", n)


def local_id(site_character: str) -> str:
    slug = site_slug(site_character)
    return SITE_TO_LOCAL.get(slug, slug.replace("-", ""))


# ---- 簡體轉繁體（有裝 opencc 才轉，沒裝就保留原文） ----
_cc = None


def to_trad(text: str) -> str:
    global _cc
    if not text:
        return text
    if _cc is None:
        try:
            from opencc import OpenCC
            _cc = OpenCC("s2tw")
        except Exception:
            log.info("沒有簡繁轉換套件，網站文字保留原樣")
            _cc = False
    if not _cc:
        return text
    try:
        return _cc.convert(text)
    except Exception:
        return text


# ---- 讀網站 ----
class FetchError(Exception):
    """給使用者看的白話錯誤。"""


def _get_json(url: str) -> dict:
    req = urllib.request.Request(url, headers={
        "Accept": "application/json",
        "User-Agent": f"SF6ComboTrainer/{APP_VERSION} (personal practice tool)"})
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
            raw = resp.read(MAX_BYTES + 1)
    except urllib.error.HTTPError as e:
        if e.code == 429:
            raise FetchError("網站說讀太快了，請過幾分鐘再按「更新網站資料」。") from e
        raise FetchError(f"網站回應錯誤（代碼 {e.code}），可能是網站改版或暫時維修。") from e
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        raise FetchError("連不上網站，請確認網路，或用瀏覽器打開 sf6cm.sctrip.asia 看看網站是否正常。") from e
    if len(raw) > MAX_BYTES:
        raise FetchError("網站回傳的資料太大，已停止讀取。")
    try:
        data = json.loads(raw.decode("utf-8"))
    except ValueError as e:
        raise FetchError("網站回傳的不是連段資料，可能是網站改版了。") from e
    if not isinstance(data, dict) or not isinstance(data.get("items"), list):
        raise FetchError("網站回傳的資料格式看不懂，可能是網站改版了。")
    return data


def fetch_all(control: str = "classic", progress=None, cancelled=lambda: False) -> list[dict]:
    """讀整個連段庫（一頁一頁）。progress(第幾頁, 總頁數)。"""
    items: list[dict] = []
    page, pages = 1, 1
    while page <= pages and page <= MAX_PAGES:
        if cancelled():
            raise FetchError("已取消。")
        query = urllib.parse.urlencode({"controlType": control, "sort": "updatedAt", "direction": "desc",
                                        "page": page, "pageSize": PAGE_SIZE})
        data = _get_json(f"{SITE}{LIBRARY}?{query}")
        total = int(data.get("total") or 0)
        pages = max(1, -(-total // PAGE_SIZE))
        items += [it for it in data["items"] if isinstance(it, dict)]
        if progress:
            progress(page, pages)
        if not data["items"]:
            break
        page += 1
        if page <= pages:
            time.sleep(PAGE_DELAY)
    return items


def save_cache(items: list[dict], control: str = "classic"):
    path = cache_path()
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps({"fetched": time.time(), "control": control, "items": items},
                              ensure_ascii=False), encoding="utf-8")
    tmp.replace(path)


def load_cache() -> tuple[list[dict], float]:
    try:
        data = json.loads(cache_path().read_text(encoding="utf-8"))
        return [it for it in data.get("items", []) if isinstance(it, dict)], float(data.get("fetched", 0))
    except FileNotFoundError:
        return [], 0.0
    except Exception:
        log.exception("網站快取讀取失敗")
        return [], 0.0


# ---- 記法轉換 ----
# 網站上常見的寫法：214+PP、[4]6+HP、j.HK、>j.K、RAW DR、drive rush、2+KKK、j.214+MP/HP、LP!
SYSTEM = {"DI": "DI", "DR": "DR", "DRC": "DR", "DP": "DP", "PARRY": "DP", "REV": "6DI", "THROW": "LP+LK"}
MOTIONS = {"360": "63214789", "HCB": "63214", "HCF": "41236"}
SEP_WORDS = {">", "FOLLOWUP", "VALIDFOLLOWUP"}
# 括號裡的說明，有些其實就是招式（綠衝＝DR）
DR_WORDS = ("绿冲", "綠衝", "裸绿冲", "取消绿冲")
NOTE_WORDS = {"DO NOTHING": "什麼都不做", "LVL": "Lv", "ENHANCED": "強化版"}
_TOKEN = re.compile(r"\[[1-9]\]|\d+_HOLD|\d+|J\.|DRIVE\s*RUSH|(?:[LMH]?[PK])+(?![A-Z])|[A-Z]+"
                    r"|[\u4e00-\u9fff]+|>|\+|\S")


def _flush(buttons: list[str]) -> str:
    if not buttons:
        return ""
    if all(b == "P" for b in buttons) or all(b == "K" for b in buttons):
        return "".join(buttons)
    return "+".join(buttons)


def _note(text: str) -> str:
    up = text.upper()
    for k, v in NOTE_WORDS.items():
        if k in up:
            up = up.replace(k, v)
            text = up
    return text.strip()


def convert_step(notation: str) -> tuple[str, list[str], list[str]]:
    """一步的網站記法 → (我們的記法, 括號備註, 看不懂的字)。"""
    text = (notation or "").replace("（", "(").replace("）", ")").strip()
    notes = [_note(n) for n in re.findall(r"\(([^)]*)\)", text) if n.strip()]
    text = re.sub(r"\([^)]*\)", " ", text)
    for w in sorted(DR_WORDS, key=len, reverse=True):
        text = text.replace(w, " DR ")
    # 「MP/HP」這種二選一：用前一個，另一個寫進備註
    def alt(m):
        notes.append(f"{m.group(1)} 或 {m.group(2)} 都可以")
        return m.group(1)
    text = re.sub(r"([LMH]?[PK]+)\s*/\s*([LMH]?[PK]+)", alt, text, flags=re.I)
    if "!" in text:
        notes.append("網站標了「!」")
        text = text.replace("!", " ")
    out, buttons, unknown, raw = "", [], [], False
    for t in _TOKEN.findall(text.upper()):
        if t == "+":
            continue
        if re.fullmatch(r"(?:[LMH]?[PK])+", t):
            buttons += re.findall(r"[LMH]?[PK]", t)
            continue
        out += _flush(buttons)
        buttons = []
        if t.startswith("[") and t.endswith("]"):
            out += t
        elif t.endswith("_HOLD") and t[0] in "123456789":
            out += f"[{t[0]}]"
        elif t in MOTIONS:
            out += MOTIONS[t]
            if t == "360":
                notes.append("轉一圈")
        elif t.isdigit():
            out += "".join(ch for ch in t if ch != "0")
        elif t in SYSTEM:
            out += SYSTEM[t]
        elif t.replace(" ", "").startswith("DRIVERUSH"):
            out += "DR"
        elif t == "RAW":
            raw = True                   # RAW DR＝格擋前衝 PDR（按住 Parry 前前）
        elif t in SEP_WORDS:
            out += "."
        elif t == "HOLD":
            notes.append("長按")
        elif t in ("J.", "J", "JUMP"):
            out += "j."
        else:
            unknown.append(t)
    out += _flush(buttons)
    out = out.strip(".")
    if not out and any(w in (notation or "") for w in DR_WORDS):
        out = "DR"
    # 括號裡只是重複說明綠衝的就不留
    notes = [n for n in notes if n and not (out == "DR" and n in DR_WORDS)]
    if raw and out.endswith("DR"):
        out = out[:-2] + "PDR"
        notes.insert(0, "綠衝")
    if out and parse(out).errors:
        unknown.append(out)
    if not out and not unknown and notes:
        return out, notes, unknown      # 這一步只有說明（例如「什麼都不做」）
    return out, notes, unknown


@dataclass
class SiteCombo:
    key: str                     # 網站上的唯一代號（避免重複加入）
    character: str               # 這個程式的角色 id
    title: str
    author: str
    tags: list[str]
    difficulty: int              # 星數，0＝不知道
    damage: int | None
    drive: float | None
    sa: int | None
    steps: list[str]             # 每一步轉好的記法（含括號備註）
    raw_steps: list[str]         # 網站原本的記法（給看不懂時對照）
    problems: list[str] = field(default_factory=list)
    starter: str = ""
    notes: str = ""

    @property
    def notation(self) -> str:
        return " > ".join(s for s in self.steps if s)

    def combo_notes(self) -> str:
        lines = [f"來源：{SITE}（作者：{self.author}）"]
        if self.tags:
            lines.append("標籤：" + "、".join(self.tags))
        if self.starter:
            lines.append("起手：" + self.starter)
        if self.notes:
            lines.append(self.notes)
        if self.problems:
            lines.append("網站原本的寫法：" + " ＞ ".join(self.raw_steps))
        return "\n".join(lines)


def _num(v):
    return v if isinstance(v, (int, float)) and not isinstance(v, bool) and v >= 0 else None


def to_combo(item: dict) -> SiteCombo | None:
    if not isinstance(item, dict) or not item.get("id"):
        return None
    meta = item.get("summaryMetadata") if isinstance(item.get("summaryMetadata"), dict) else {}
    author = item.get("author") if isinstance(item.get("author"), dict) else {}
    steps, raw, problems = [], [], []
    for i, st in enumerate(item.get("displaySteps") or [], 1):
        if not isinstance(st, dict):
            continue
        n = str(st.get("notation") or "")[:200]
        raw.append(n)
        conv, notes, unknown = convert_step(n)
        note = str(st.get("note") or "").strip()[:200]
        if note:
            notes.append(note)
        if unknown or not (conv or notes):
            problems.append(f"第 {i} 步「{to_trad(n)}」看不懂")
            notes.insert(0, to_trad(n))      # 看不懂就把原文放進備註，至少看得到
        step = conv
        if notes:
            step += "".join(f"（{to_trad(x)}）" for x in notes if x)
        steps.append(step.strip())
    drive = _num(meta.get("driveCost"))
    sa = _num(meta.get("superCost"))
    return SiteCombo(
        key=str(item.get("canonicalKey") or item["id"])[:120],
        character=local_id(str(item.get("character") or "")),
        title=to_trad(str(item.get("title") or "（沒有名稱）"))[:80],
        author=to_trad(str(author.get("name") or "")),
        tags=[to_trad(str(t)) for t in (item.get("tags") or []) if t][:12],
        difficulty=int(meta.get("difficultyStars") or 0) if isinstance(meta.get("difficultyStars"), int) else 0,
        damage=int(meta["damage"]) if isinstance(meta.get("damage"), int) and meta["damage"] > 0 else None,
        drive=float(drive) if drive is not None else None,
        sa=int(sa) if sa is not None else None,
        steps=steps, raw_steps=raw, problems=problems,
        starter=to_trad(str(meta.get("starter") or "")),
        notes=to_trad(str(meta.get("notes") or item.get("description") or ""))[:1000],
    )


CORNER_WORDS = ("板邊", "版邊", "壁邊", "版邊", "板边", "版边")
JUMP_START = {"7", "8", "9"}
STARTER_NAMES = {"DI": "DI（動力衝擊）", "DP": "DP（Parry）", "6DI": "DI（動力衝擊）", "PDR": "綠衝"}


def starter_tag(starter: str) -> str:
    """網站的起手寫法 → 標籤，例如「HP 起手」「DP（Parry）起手」「跳入起手」。"""
    conv, _notes, unknown = convert_step(starter)
    if not conv or unknown:
        return ""
    if conv in JUMP_START or conv.startswith("j."):
        return "跳入起手"
    if conv in STARTER_NAMES:
        return f"{STARTER_NAMES[conv]}起手"
    return f"{conv} 起手"


def combo_tags(c: SiteCombo) -> list[str]:
    """加入連段庫時自動帶的標籤：網站標籤、起手、板邊限定。"""
    tags = [t for t in c.tags if t not in CORNER_WORDS]
    corner = any(w in c.title for w in CORNER_WORDS) or any(t in CORNER_WORDS for t in c.tags)
    if corner:
        tags.append("板邊限定")
    st = starter_tag(c.starter)
    if st:
        tags.append(st)
    return list(dict.fromkeys(tags))


def to_store_item(c: SiteCombo) -> dict:
    """給 ComboStore.import_combos 用的格式。"""
    return {"character": c.character, "name": c.title, "notation": normalize_simul(c.notation),
            "notes": c.combo_notes(), "damage": c.damage, "drive_override": c.drive, "sa_override": c.sa,
            "tags": combo_tags(c)}
