"""練習時的輸入比對：把控制器事件跟連段一步一步對照。

程式只看得到輸入，看不到遊戲裡有沒有打中，所以結果叫「輸入比對完成」。
- 搓招：需要的方向依序出現就算，中間多出別的方向（例如 2→1→2→3→6）也可以。
- 搓招（兩個方向以上）很寬鬆：上一步完成後，方向有依序輸入過就算，轉完放開搖桿再按鍵也可以。
- 簡易指令：昇龍拳系 623 也接受 323、232；反方向 421 也接受 121、212。
- 蓄力：[4]6P 要先按住後方（1／4／7 都算）夠久，接著在緩衝時間內推前並按鍵。
- 同時按：PP、LP+LK 等在「同時按容差」內按齊就算；DI（動力衝擊）／DP（Parry）也接受快捷鍵。
- 取消前衝 DR（拳腳後取消）：按一下 DP（Parry），或推兩次前，就算。
- 格擋前衝 PDR（網站寫 RAW DR）：按 DP（Parry）後推兩次前，或「前、DP（Parry）、前」。
"""
from __future__ import annotations

from dataclasses import dataclass, field

from core.notation import Combo, MIRROR, Step

FRAME = 1 / 60
PUNCHES = frozenset({"LP", "MP", "HP"})
KICKS = frozenset({"LK", "MK", "HK"})
LAST_DIR_TOL = 10 * FRAME      # 最後一個方向到按鍵之間最多幾幀
MOTION_PER_DIR = 12 * FRAME    # 搓招每個方向平均可用的時間
CHARGE_BUFFER = 10 * FRAME     # 放開蓄力方向到推前之間的緩衝
DR_WINDOW = 0.5                # DP（Parry）前後多久內要推前（秒）
# 簡易指令：記法寫左邊的方向，練習時右邊的打法也算（以面向右為準，面向左自動翻轉）
SHORTCUTS = {
    (6, 2, 3): [[3, 2, 3], [2, 3, 2]],      # 昇龍拳系
    (4, 2, 1): [[1, 2, 1], [2, 1, 2]],      # 反方向昇龍拳系
}

DEFAULT_SETTINGS = {
    "practice_strict": False,
    "practice_reset_s": 3.0,
    "practice_simul_frames": 3,
    "practice_charge_frames": 40,
}


def _acc(d: int) -> frozenset:
    """單一方向的接受範圍：下（2）包含斜下，上（8）包含斜上。"""
    if d == 2:
        return frozenset({1, 2, 3})
    if d == 8:
        return frozenset({7, 8, 9})
    return frozenset({d})


def _charge_set(d: int) -> frozenset:
    return {4: frozenset({1, 4, 7}), 6: frozenset({3, 6, 9}),
            2: frozenset({1, 2, 3}), 8: frozenset({7, 8, 9})}.get(d, frozenset({d}))


@dataclass
class StepReq:
    kind: str = "skip"                                  # buttons / dirs / dr / sa / skip
    dirs: list[int] = field(default_factory=list)       # 已依面向翻轉
    charge: int | None = None
    slots: list[frozenset] = field(default_factory=list)
    alt: frozenset = frozenset()                        # 單一按鍵就能滿足（例：DI 快捷鍵）
    alt_dirs: list[list[int]] = field(default_factory=list)  # 簡易指令（已依面向翻轉）

    def visual_dirs(self) -> int:
        return len(self.dirs) + (1 if self.charge else 0)


def build_step(step: Step, facing_right: bool) -> StepReq:
    req = StepReq()
    flip = (lambda d: d) if facing_right else (lambda d: MIRROR[d])
    raw_dirs = [t.value for t in step.tokens if t.kind == "dir"]
    req.alt_dirs = [[flip(d) for d in alt] for alt in SHORTCUTS.get(tuple(raw_dirs), [])]
    for t in step.tokens:
        if t.kind == "dir":
            req.dirs.append(flip(t.value))
        elif t.kind == "charge":
            req.charge = flip(t.value)
        elif t.kind == "button":
            for code in t.value:
                if code in ("PP", "PPP"):
                    req.slots += [PUNCHES] * len(code)
                elif code in ("KK", "KKK"):
                    req.slots += [KICKS] * len(code)
                elif code == "P":
                    req.slots.append(PUNCHES)
                elif code == "K":
                    req.slots.append(KICKS)
                else:
                    req.slots.append(frozenset({code}))
        elif t.kind == "system":
            if t.value == "DI":
                req.slots += [frozenset({"HP"}), frozenset({"HK"})]
                req.alt = frozenset({"DI"})
            elif t.value == "DP":
                req.slots += [frozenset({"MP"}), frozenset({"MK"})]
                req.alt = frozenset({"DP"})
            elif t.value == "DR":
                req.kind = "drc"
            elif t.value == "PDR":
                req.kind = "dr"
            else:
                req.kind = "sa"
    if req.kind in ("dr", "drc", "sa"):
        return req
    if req.slots:
        req.kind = "buttons"
    elif req.dirs or req.charge:
        req.kind = "dirs"
    return req


def _assign(slots: list[frozenset], codes: list[str]) -> bool:
    """每個槽要配到不同的一次按鍵。槽很少，直接回溯。"""
    order = sorted(range(len(slots)), key=lambda i: len(slots[i]))
    used = [False] * len(codes)

    def go(k):
        if k == len(order):
            return True
        slot = slots[order[k]]
        for i, code in enumerate(codes):
            if not used[i] and code in slot:
                used[i] = True
                if go(k + 1):
                    return True
                used[i] = False
        return False
    return go(0)


class Matcher:
    def __init__(self, combo: Combo, settings: dict, facing_right: bool = True):
        self.settings = {**DEFAULT_SETTINGS, **(settings or {})}
        self.steps = [build_step(s, facing_right) for g in combo.groups for s in g.steps]
        self.forward = 6 if facing_right else 4
        self.history: list[tuple[float, int]] = [(0.0, 5)]
        self.reset()

    # ---- 狀態 ----
    def reset(self):
        self.done = 0
        self.times: list[float] = []
        self.presses: list[tuple[float, str]] = []
        self.dr_t: float | None = None
        self.late_t: float | None = None     # 按鍵對了、方向晚一點才到：等「同時按容差」這麼久
        self.error_t = 0.0
        self.last_input_t = 0.0
        self.started_t = 0.0
        self._skip()

    @property
    def total(self) -> int:
        return len(self.steps)

    @property
    def finished(self) -> bool:
        return self.total > 0 and self.done >= self.total

    def gaps(self) -> list[float | None]:
        """每一步距離上一步幾幀；第一步是 None。"""
        return [None if i == 0 else (t - self.times[i - 1]) / FRAME for i, t in enumerate(self.times)]

    def _skip(self):
        while self.done < self.total and self.steps[self.done].kind == "skip":
            self.times.append(self.times[-1] if self.times else 0.0)
            self.done += 1

    def _advance(self, t: float):
        if self.done == 0:
            self.started_t = t
        self.times.append(t)
        self.done += 1
        self.presses.clear()
        self.dr_t = None
        self.late_t = None
        self._skip()

    # ---- 方向歷史 ----
    def dir_at(self, t: float) -> int:
        d = 5
        for ti, di in self.history:
            if ti > t:
                break
            d = di
        return d

    def _step_since(self, t: float) -> float:
        """這一步的方向從什麼時候開始算：上一步完成的時間；第一步往前看「多久沒輸入就重來」那麼久。"""
        if self.done > 0 and self.times:
            return self.times[-1]
        return t - self.settings["practice_reset_s"]

    def _motion_ok(self, dirs: list[int], t: float, since: float | None = None,
                   loose: bool = False) -> float | None:
        """dirs 依序出現且最後一個在 t 附近 → 回傳第一個方向的時間，否則 None。
        loose：從 since 開始依序出現過就算，最後一個方向不必還按著。"""
        if not dirs:
            return t
        if len(dirs) == 1 and since is None:
            if self.dir_at(t) in _acc(dirs[0]):
                return t
            return t if any(t - LAST_DIR_TOL <= ti <= t and di in _acc(dirs[0])
                            for ti, di in self.history) else None
        start = t - MOTION_PER_DIR * len(dirs) - LAST_DIR_TOL
        if loose and since is not None:
            start = since
        elif since is not None:
            start = max(start, since)
        entries = [(start, self.dir_at(start))] + [(ti, di) for ti, di in self.history if start < ti <= t]
        idx = len(dirs) - 1
        first_t = None
        for k in range(len(entries) - 1, -1, -1):
            ti, di = entries[k]
            if idx == len(dirs) - 1 and not loose:
                # 最後一個方向：現在還按著，或剛剛才經過
                end = entries[k + 1][0] if k + 1 < len(entries) else t
                if di == dirs[idx] and end >= t - LAST_DIR_TOL:
                    idx -= 1
                    first_t = ti
            elif di == dirs[idx]:
                idx -= 1
                first_t = ti
            if idx < 0:
                return first_t
        return None

    def _dr_ok(self, t: float) -> bool:
        """剛推了前：DP（Parry）後已推兩次前，或 DP（Parry）前剛推過前（前、Parry、前）。"""
        f = self.forward
        if self._motion_ok([f, f], t, since=self.dr_t) is not None:
            return True
        return self.dir_at(self.dr_t) == f or any(
            di == f for ti, di in self.history if self.dr_t - DR_WINDOW <= ti <= self.dr_t)

    def _charge_ok(self, d: int, before: float) -> bool:
        """在 before 之前（含緩衝）有一段連續按住蓄力方向夠久。"""
        need = self.settings["practice_charge_frames"] * FRAME
        cset = _charge_set(d)
        run_start = None
        for i, (ti, di) in enumerate(self.history):
            end = self.history[i + 1][0] if i + 1 < len(self.history) else before
            if di in cset:
                run_start = ti if run_start is None else run_start
                if end >= before - CHARGE_BUFFER and min(end, before) - run_start >= need:
                    return True
            else:
                run_start = None
            if ti > before:
                break
        return False

    def _dirs_ok(self, req: StepReq, t: float) -> bool:
        if req.charge:
            first = self._motion_ok(req.dirs, t) if req.dirs else t
            return first is not None and self._charge_ok(req.charge, first)
        return any(self._dir_seq_ok(dirs, t) for dirs in [req.dirs] + req.alt_dirs)

    def _dir_seq_ok(self, dirs: list[int], t: float) -> bool:
        if len(dirs) >= 2:
            # 只看上一步完成之後的方向，上一招的搓招不能重複拿來用
            return self._motion_ok(dirs, t, self._step_since(t), loose=True) is not None
        return self._motion_ok(dirs, t) is not None

    def partial(self, now: float) -> int:
        """目前這一步已經亮幾個方向（給畫面逐格點亮用）。"""
        if self.finished or self.done >= self.total:
            return 0                     # 空的連段（剛按新增）沒有東西要比對
        req = self.steps[self.done]
        n = 0
        if req.charge:
            if not self._charge_ok(req.charge, now):
                return 0
            n = 1
        best = 0
        for dirs in [req.dirs] + req.alt_dirs:
            for k in range(len(dirs), best, -1):
                if self._dir_seq_ok(dirs[:k], now):
                    best = k
                    break
        return n + best

    # ---- 輸入 ----
    def on_dir(self, t: float, d: int) -> str | None:
        self.history.append((t, d))
        if len(self.history) > 400:
            self.history = self.history[-300:]
        if self.finished or self.done >= self.total:
            return None
        req = self.steps[self.done]
        if req.kind == "buttons" and self.late_t is not None:
            # 按鍵比最後一個方向早一點點按下（手快時很常見），容差內方向到了也算
            if t - self.late_t <= self.settings["practice_simul_frames"] * FRAME and self._dirs_ok(req, t):
                self.last_input_t = t
                self._advance(t)
                return "advance"
            if t - self.late_t > self.settings["practice_simul_frames"] * FRAME:
                self.late_t = None
        if req.kind == "dirs" and self._dirs_ok(req, t):
            self.last_input_t = t
            self._advance(t)
            return "advance"
        if req.kind == "drc" and d == self.forward and self._motion_ok([d, d], t) is not None:
            self.last_input_t = t
            self._advance(t)
            return "advance"
        if req.kind == "dr" and self.dr_t is not None:
            if t - self.dr_t > DR_WINDOW:
                self.dr_t = None
            elif d == self.forward and self._dr_ok(t):
                self.last_input_t = t
                self._advance(t)
                return "advance"
        return None

    def on_press(self, t: float, code: str) -> str | None:
        """回傳 advance（前進一步）、error（嚴格模式按錯）或 None。"""
        self.last_input_t = t
        if self.finished or not code or self.done >= self.total:
            return None
        req = self.steps[self.done]
        window = self.settings["practice_simul_frames"] * FRAME
        self.presses = [(pt, c) for pt, c in self.presses if t - pt <= window] + [(t, code)]
        codes = [c for _, c in self.presses]

        if req.kind == "buttons":
            hit = code in req.alt or _assign(req.slots, codes)
            if hit and self._dirs_ok(req, t):
                self._advance(t)
                return "advance"
            if hit:
                # 按鍵會晚一點點才結算（要等同時按的其他鍵），這段時間到的方向已經在紀錄裡
                grace = self.settings["practice_simul_frames"] * FRAME
                for ti, _d in self.history:
                    if t < ti <= t + grace and self._dirs_ok(req, ti):
                        self._advance(ti)
                        return "advance"
                self.late_t = t              # 方向可能還沒到，等一下 on_dir 再判斷
            if any(code in s for s in req.slots) or code in req.alt:
                return None          # 可能是同時按的第一顆，或方向還沒對
        elif req.kind == "drc":
            if code == "DP" or _assign([frozenset({"MP"}), frozenset({"MK"})], codes):
                self._advance(t)
                return "advance"
            if code in ("MP", "MK"):
                return None          # 可能是 MP+MK 的第一顆
        elif req.kind == "dr":
            if code == "DP" or _assign([frozenset({"MP"}), frozenset({"MK"})], codes):
                self.dr_t = t
                return None
            if code in ("MP", "MK"):
                return None
        elif req.kind == "sa":
            if code in PUNCHES | KICKS:
                f = self.forward
                b = 10 - f if f in (4, 6) else f
                if (self._motion_ok([2, 3 if f == 6 else 1, f] * 2, t) is not None
                        or self._motion_ok([2, 1 if f == 6 else 3, b] * 2, t) is not None):
                    self._advance(t)
                    return "advance"
                return None
        if self.settings["practice_strict"]:
            self.error_t = t
            return "error"
        return None
