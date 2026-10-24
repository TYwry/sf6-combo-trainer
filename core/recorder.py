"""用控制器錄一列：把按下的方向和按鍵轉成記法。

- 方向：從上一次按鍵之後的方向變化依序記下（中立不記、重複的合併），例如 ↓↘→ 記成 236。
- 按鍵：照版面上設定的動作（LP、DI…）；幾乎同時按下的寫成 LP+LK。
- 每按一次鍵是一步，步與步之間用 . 分開。
- 第一次按鍵後，停手超過 STOP_AFTER 秒（2 秒）就自動結束。
"""
from __future__ import annotations

from core.key_resolver import KeyResolver
from core.notation import MIRROR, normalize_simul

STOP_AFTER = 2.0
SIMUL = 4 / 60           # 幾乎同時按下（4 幀內）算同時按
MOTION_KEEP = 0.6        # 按鍵前多久內的方向才算這一步的搓招


class Recorder:
    def __init__(self, layout: list[dict], window_ms: float, facing_right: bool = True):
        self.resolver = KeyResolver(layout, window_ms)
        self.facing_right = facing_right
        self.steps: list[str] = []
        self.dirs: list[tuple[float, int]] = []
        self._pending: list[tuple[float, str]] = []
        self.last_t = 0.0
        self.cur = 5             # 目前按著的方向
        self.started = False
        self.finished = False

    # ---- 輸入 ----
    def feed(self, ev):
        if self.finished:
            return
        if ev.kind == "dir":
            d = ev.direction if self.facing_right else MIRROR[ev.direction]
            self.cur = d
            if d != 5:
                self.dirs.append((ev.t, d))
            self.last_t = ev.t
        elif ev.kind in ("press", "release"):
            if ev.button == "BACK":
                return
            for kev in self.resolver.feed(ev):
                self._key(kev)

    def tick(self, now: float):
        if self.finished:
            return
        for kev in self.resolver.flush(now):
            self._key(kev)
        if self._pending and now - self._pending[-1][0] > SIMUL:
            self._commit()
        if self.started and not self._pending and now - self.last_t > STOP_AFTER:
            self.finished = True

    def stop(self):
        self._commit()
        self.finished = True

    # ---- 組成記法 ----
    def _key(self, kev):
        if kev.kind != "press":
            return
        action = kev.key.get("action", "")
        if action:
            actions = [action]
        else:
            # 組合鍵沒設動作時，改用裡面每顆鍵各自的動作
            actions = [self.resolver.singles.get(i, {}).get("action", "") for i in kev.key.get("inputs", [])]
            actions = [a for a in actions if a]
        if not actions:
            return
        if self._pending and kev.t - self._pending[0][0] > SIMUL:
            self._commit()
        self._pending += [(kev.t, a) for a in actions]
        self.started = True
        self.last_t = kev.t

    def _commit(self):
        if not self._pending:
            return
        t0 = self._pending[0][0]
        motion = []
        for t, d in self.dirs:
            if t0 - t <= MOTION_KEEP and t <= t0 + SIMUL and (not motion or motion[-1] != d):
                motion.append(d)
        if not motion and self.cur != 5:
            motion = [self.cur]          # 一直按著的方向（例如蹲著按）
        codes = list(dict.fromkeys(c for _, c in self._pending))
        self.steps.append("".join(str(d) for d in motion) + "+".join(codes))
        self._pending = []
        self.dirs = []

    def text(self) -> str:
        return normalize_simul(".".join(self.steps))     # 重拳＋重腳記成 DI、中拳＋中腳記成 DP
