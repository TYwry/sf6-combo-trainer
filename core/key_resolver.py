"""把控制器的原始按鍵事件，對應到使用者版面上的「按鍵」。

Hitbox 的巨集鍵會讓電腦在同一瞬間收到好幾個訊號（例如 X 和 A）。
這裡把很短時間內（預設 12 毫秒）一起按下的訊號收成一組，
如果版面上有一顆組合按鍵剛好是這組訊號，就算成按下那一顆，只記一次。
程式分不出「巨集鍵」和「真的同時按兩顆」，兩者會被當成同一件事。
"""
from __future__ import annotations

from dataclasses import dataclass

from core.xinput_reader import InputEvent


@dataclass
class KeyEvent:
    t: float
    kind: str           # press / release
    key: dict
    background: bool = False


class KeyResolver:
    def __init__(self, layout: list[dict], window_ms: float = 12):
        self.window = window_ms / 1000
        self.set_layout(layout)
        self._pending: list[InputEvent] = []
        self._active: dict[str, dict] = {}   # key id → key（目前按著的版面按鍵）

    def set_layout(self, layout: list[dict]):
        self.layout = layout
        self.combos = sorted([k for k in layout if len(k["inputs"]) > 1],
                             key=lambda k: -len(k["inputs"]))
        self.singles = {k["inputs"][0]: k for k in layout if len(k["inputs"]) == 1}

    def feed(self, ev: InputEvent) -> list[KeyEvent]:
        """餵入一個原始事件；回傳已經能確定的版面事件。"""
        out = []
        if ev.kind == "press":
            self._pending.append(ev)
        elif ev.kind == "release":
            out += self.flush(ev.t, force=True)
            for kid, key in list(self._active.items()):
                if ev.button in key["inputs"]:
                    del self._active[kid]
                    out.append(KeyEvent(ev.t, "release", key, ev.background))
        return out

    def flush(self, now: float, force: bool = False) -> list[KeyEvent]:
        """把等待夠久的一組按下事件結算出來。"""
        if not self._pending or (not force and now - self._pending[0].t < self.window):
            return []
        first = self._pending[0]
        group = [e for e in self._pending if e.t - first.t <= self.window]
        self._pending = self._pending[len(group):]
        remaining = {e.button for e in group}
        out = []
        for combo in self.combos:
            needed = set(combo["inputs"])
            if needed <= remaining:
                remaining -= needed
                self._active[combo["id"]] = combo
                out.append(KeyEvent(first.t, "press", combo, first.background))
        for e in group:
            if e.button in remaining:
                key = self.singles.get(e.button) or {"id": "raw-" + e.button, "inputs": [e.button],
                                                     "label": "", "action": ""}
                self._active[key["id"]] = key
                out.append(KeyEvent(e.t, "press", key, e.background))
                remaining.discard(e.button)
        return out + self.flush(now, force)

    def lit_ids(self, held: frozenset) -> set[str]:
        """畫面上要亮的按鍵：組合按鍵的訊號都按著就亮組合鍵，裡面的單顆不重複亮。"""
        lit, used = set(), set()
        for combo in self.combos:
            needed = set(combo["inputs"])
            if needed <= held and not (needed & used):
                lit.add(combo["id"])
                used |= needed
        for btn, key in self.singles.items():
            if btn in held and btn not in used:
                lit.add(key["id"])
        return lit
