"""連段記法：把打字的連段（例如 `MP > 236PP.6P`）解析成「組 → 步 → 記號」。

規則
- 組：用 `>`、`,`、`，` 或 【】 分開，練習畫面上一組是一列。
- 步：組裡用 `.` 或 `。` 分開，只是分隔，不用輸入。
- 方向：數字 1～9（相對角色，6＝前）。`[4]` 表示蓄力。
- 按鍵：LP MP HP LK MK HK；P／K＝任一拳／腳；PP／KK＝任兩顆（OD）；`LP+LK` 或 `LPLK` 同時按。
  `HP+HK`＝DI（動力衝擊）、`MP+MK`＝DP（Parry）。
- 系統：DI（動力衝擊）、DP（Parry）、DR 取消前衝、PDR 格擋前衝、SA1／SA2／SA3／CA。
- `j.` 跳躍中；括號（…）是備註，不參與比對。
- 也看得懂箭頭：↓↘→ 等於 236（從圖片讀進來的連段常用箭頭）。
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

GROUP_SEPS = ">,，＞"
STEP_SEPS = ".。"
BUTTON_WORDS = ["PDR", "SA1", "SA2", "SA3", "CA", "DR", "DI", "DP",
                "LP", "MP", "HP", "LK", "MK", "HK", "PPP", "KKK", "PP", "KK", "P", "K"]
SYSTEM_WORDS = {"PDR", "SA1", "SA2", "SA3", "CA", "DR", "DI", "DP"}
PUNCHES = {"LP", "MP", "HP"}
KICKS = {"LK", "MK", "HK"}
ARROWS = {1: "↙", 2: "↓", 3: "↘", 4: "←", 5: "N", 6: "→", 7: "↖", 8: "↑", 9: "↗"}
MIRROR = {1: 3, 3: 1, 4: 6, 6: 4, 7: 9, 9: 7, 2: 2, 5: 5, 8: 8}
# 同時按兩顆就是系統動作：重拳＋重腳＝DI（動力衝擊）、中拳＋中腳＝DP（Parry）
SIMUL_SYSTEM = {frozenset({"HP", "HK"}): "DI", frozenset({"MP", "MK"}): "DP"}
SA_LEVEL = {"SA1": 1, "SA2": 2, "SA3": 3, "CA": 3}
# 箭頭（含各種寫法）→ 方向數字
ARROW_IN = {}
for _d, _chars in {1: "↙⬋⇙", 2: "↓⬇⇓", 3: "↘⬊⇘", 4: "←⬅⇐", 6: "→➡⇒➔➜", 7: "↖⬉⇖",
                   8: "↑⬆⇑", 9: "↗⬈⇗"}.items():
    for _c in _chars:
        ARROW_IN[_c] = _d


@dataclass
class Token:
    kind: str            # dir / charge / button / system / jump / note
    value: object        # 方向數字、按鍵 tuple、系統代號或備註文字


@dataclass
class Step:
    tokens: list[Token] = field(default_factory=list)

    def text(self) -> str:
        return tokens_text(self.tokens)


@dataclass
class Group:
    steps: list[Step] = field(default_factory=list)

    def text(self) -> str:
        return ".".join(s.text() for s in self.steps)


@dataclass
class Combo:
    groups: list[Group] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)

    def text(self) -> str:
        return " > ".join(g.text() for g in self.groups)

    def step_count(self) -> int:
        return sum(len(g.steps) for g in self.groups)


def tokens_text(tokens: list[Token]) -> str:
    out = ""
    for t in tokens:
        if t.kind == "dir":
            out += str(t.value)
        elif t.kind == "charge":
            out += f"[{t.value}]"
        elif t.kind == "button":
            out += "+".join(t.value)
        elif t.kind == "system":
            out += t.value
        elif t.kind == "jump":
            out += "j."
        elif t.kind == "note":
            out += f"({t.value})"
    return out


def _split_groups(text: str) -> list[str]:
    text = text.replace("【", "\n").replace("】", "\n")
    parts = re.split("[" + re.escape(GROUP_SEPS) + r"\n]", text)
    return [p for p in (x.strip() for x in parts) if p]


def _tokenize(src: str, errors: list[str]) -> list[Token | str]:
    """回傳記號，步驟分隔用字串 '.' 表示。"""
    out: list[Token | str] = []
    i, n = 0, len(src)
    while i < n:
        ch = src[i]
        up = src[i:].upper()
        if ch.isspace():
            i += 1
        elif ch in "(（":
            close = min([j for j in (src.find(")", i), src.find("）", i)) if j != -1] or [n])
            out.append(Token("note", src[i + 1:close].strip()))
            i = close + 1
        elif ch == "[":
            m = re.match(r"\[([1-9])\]", src[i:])
            if m:
                out.append(Token("charge", int(m.group(1))))
                i += 3
            else:
                errors.append(f"看不懂「{src[i:i + 3]}」，蓄力請寫成 [4] 這樣")
                i += 1
        elif up.startswith(("NJ.", "BJ.", "SJ.")) or up.startswith("J."):
            out.append(Token("jump", "j"))
            i += up.index(".") + 1
        elif ch in STEP_SEPS:
            out.append(".")
            i += 1
        elif ch.isdigit() and ch != "0":
            out.append(Token("dir", int(ch)))
            i += 1
        elif ch in ARROW_IN:
            out.append(Token("dir", ARROW_IN[ch]))
            i += 1
        elif ch == "\ufe0f":       # 箭頭 emoji 後面的變體符號
            i += 1
        elif ch == "+":
            i += 1    # 方向後面的 + 不影響；按鍵之間的 + 在下面處理
        else:
            word = next((w for w in BUTTON_WORDS if up.startswith(w)), None)
            if not word:
                errors.append(f"看不懂「{ch}」")
                i += 1
                continue
            i += len(word)
            if word in SYSTEM_WORDS:
                out.append(Token("system", word))
                continue
            buttons = [word]
            # LP+LK 這種同時按；HPHK 這樣連著寫（中間沒有分隔）也算同時按
            while i < n:
                skip = 1 if src[i] == "+" else 0
                nxt = next((w for w in BUTTON_WORDS if src[i + skip:].upper().startswith(w)
                            and w not in SYSTEM_WORDS), None)
                if not nxt:
                    break
                buttons.append(nxt)
                i += skip + len(nxt)
            system = SIMUL_SYSTEM.get(frozenset(buttons)) if len(buttons) == 2 else None
            out.append(Token("system", system) if system else Token("button", tuple(buttons)))
    return out


_SIMUL_TEXT = [(re.compile(r"HP\+?HK|HK\+?HP", re.I), "DI"), (re.compile(r"MP\+?MK|MK\+?MP", re.I), "DP")]


def normalize_simul(text: str) -> str:
    """把記法文字裡的 HPHK／HP+HK 改寫成 DI、MPMK／MP+MK 改寫成 DP（括號裡的備註不動）。"""
    out, depth = [], 0
    for part in re.split(r"([()（）])", text):
        if part in ("(", "（"):
            depth += 1
        elif part in (")", "）"):
            depth = max(0, depth - 1)
        elif depth == 0:
            for pat, word in _SIMUL_TEXT:
                part = pat.sub(word, part)
        out.append(part)
    return "".join(out)


def parse(text: str) -> Combo:
    combo = Combo()
    for raw in _split_groups(text or ""):
        tokens = _tokenize(raw, combo.errors)
        group, step = Group(), Step()
        for t in tokens:
            if t == ".":
                if step.tokens:
                    group.steps.append(step)
                step = Step()
            else:
                step.tokens.append(t)
        if step.tokens:
            group.steps.append(step)
        if group.steps:
            combo.groups.append(group)
    return combo


# ---- 比對指令表 ----
def generalize(text: str) -> str:
    """把指定的拳腳換成 P／K，用來跟指令表比對。"""
    t = re.sub(r"\(.*?\)", "", text.upper())
    t = re.sub(r"[LMH]P", "P", t)
    t = re.sub(r"[LMH]K", "K", t)
    t = re.sub(r"P{2,}", "P", t)      # OD（PP）也算同一招
    t = re.sub(r"K{2,}", "K", t)
    return t.replace(" ", "")


def iter_moves(movelist: dict | None):
    if not movelist:
        return
    for cat in movelist.get("categories", []):
        for mv in cat.get("moves", []):
            yield cat, mv, None
            for child in mv.get("children", []):
                yield cat, child, mv


def find_move(text: str, movelist: dict | None, specific_first: bool = True):
    """找出這段輸入對應的招式（回傳 (分類, 招式) 或 None）。追加技不參與。"""
    if not movelist or not text:
        return None
    exact, general = text.upper(), generalize(text)
    candidates = [(c, m) for c, m, parent in iter_moves(movelist)
                  if parent is None and c["key"] in ("special", "super", "unique")]
    for cat, mv in candidates:
        if mv["input"].upper() == exact:
            return cat, mv
    for cat, mv in candidates:
        if generalize(mv["input"]) == general and re.search(r"\d", mv["input"]):
            # 只用有方向的招式做寬鬆比對，避免「P」這種單鍵亂配
            if len(re.sub(r"[^1-9]", "", mv["input"])) >= 2 or "[" in mv["input"]:
                return cat, mv
    return None


def _find_child(text: str, parent: dict | None):
    if not parent:
        return None
    exact, general = text.upper(), generalize(text)
    children = parent.get("children", [])
    return (next((c for c in children if c["input"].upper() == exact), None)
            or next((c for c in children if generalize(c["input"]) == general), None))


def group_move_name(group: Group, movelist: dict | None) -> str:
    names = []
    parent = None       # 上一步是有派生的招式時，下一步先找它的派生
    for step in group.steps:
        child = _find_child(step.text(), parent)
        if child:
            names.append(child["name"])
            parent = None
            continue
        hit = find_move(step.text(), movelist)
        parent = hit[1] if hit and hit[1].get("children") else None
        if hit:
            od = _is_motion(step.tokens) and _is_od_button(step.tokens) and hit[0]["key"] != "super"
            names.append(("OD " if od else "") + hit[1]["name"])
    whole = find_move(group.text(), movelist)
    if whole and not names:
        names.append(whole[1]["name"])
    return "・".join(names)


# ---- 槽消耗 ----
@dataclass
class Cost:
    drive: float = 0
    sa: int = 0
    details: list[str] = field(default_factory=list)


def _is_motion(tokens: list[Token]) -> bool:
    dirs = [t.value for t in tokens if t.kind == "dir"]
    return any(t.kind == "charge" for t in tokens) or len(dirs) >= 2


def _is_od_button(tokens: list[Token]) -> bool:
    for t in tokens:
        if t.kind == "button":
            b = set(t.value)
            if b & {"PP", "KK", "PPP", "KKK"}:
                return True
            if len(b & PUNCHES) >= 2 or len(b & KICKS) >= 2:
                return True
    return False


def compute_cost(combo: Combo, movelist: dict | None = None) -> Cost:
    """依連段內容估算動力槽（格）與氣槽（格）。數值是遊戲的通則，特殊招式可能不同。"""
    cost = Cost()
    for group in combo.groups:
        for step in group.steps:
            toks = step.tokens
            for t in toks:
                if t.kind == "system":
                    if t.value == "DR":
                        cost.drive += 3
                        cost.details.append("取消前衝 3 格")
                    elif t.value == "PDR":
                        cost.drive += 1
                        cost.details.append("格擋前衝 1 格")
                    elif t.value == "DI":
                        cost.drive += 1
                        cost.details.append("DI（動力衝擊）1 格")
                    elif t.value in SA_LEVEL:
                        cost.sa += SA_LEVEL[t.value]
                        cost.details.append(f"{t.value} {SA_LEVEL[t.value]} 格氣")
            hit = find_move(step.text(), movelist)
            if hit and hit[0]["key"] == "super":
                level = hit[1].get("level", 1)
                cost.sa += level
                cost.details.append(f"{hit[1]['name']} {level} 格氣")
            elif _is_motion(toks) and _is_od_button(toks):
                cost.drive += 2
                name = hit[1]["name"] if hit else step.text()
                cost.details.append(f"OD {name} 2 格")
    return cost


def display_dir(n: int, facing_right: bool) -> int:
    return n if facing_right else MIRROR[n]


def pretty(text: str, facing_right: bool = True) -> str:
    """把記法轉成箭頭文字，例如 `236PP` → `↓↘→ + PP`，給指令表顯示用。"""
    combo = parse(text)
    groups = []
    for g in combo.groups:
        steps = []
        for st in g.steps:
            out, prev_dir = "", False
            for t in st.tokens:
                if t.kind == "dir":
                    out += ARROWS[display_dir(t.value, facing_right)]
                    prev_dir = True
                    continue
                if t.kind == "charge":
                    out += f"蓄{ARROWS[display_dir(t.value, facing_right)]}"
                    prev_dir = True
                    continue
                if prev_dir and t.kind in ("button", "system"):
                    out += " + "
                prev_dir = False
                if t.kind == "button":
                    out += "".join(t.value)      # 同時按的鍵連著寫：MKHK
                elif t.kind == "system":
                    out += t.value
                elif t.kind == "jump":
                    out += "跳 "
                elif t.kind == "note":
                    out += f"（{t.value}）"
            steps.append(out.strip())
        groups.append(" · ".join(steps))
    return " ＞ ".join(groups)
