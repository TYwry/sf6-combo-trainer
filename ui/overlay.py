"""浮動視窗：永遠在最上層、不搶遊戲焦點的小視窗，顯示目前練習的連段。

解鎖時可以拖曳移動、拉四邊或四個角調整大小、按右鍵調外觀；
鎖定時滑鼠會直接穿過它點到遊戲。
"""
from __future__ import annotations

from PySide6.QtCore import QPoint, QRectF, Qt, Signal
from PySide6.QtGui import QAction, QActionGroup, QColor, QPainter
from PySide6.QtWidgets import QMenu, QVBoxLayout, QWidget

from ui import theme
from ui.trial_view import TrialView

OPACITY_CHOICES = [1.0, 0.85, 0.7, 0.55, 0.4, 0.3]
ZOOM_CHOICES = [("小", 0.75), ("中", 1.0), ("大", 1.25), ("特大", 1.5)]
ROW_CHOICES = [0, 3, 5, 7]


class OverlayWindow(QWidget):
    settings_changed = Signal()      # 位置、外觀、鎖定改了，要存檔
    visibility_changed = Signal(bool)
    practice_requested = Signal(bool)   # 右鍵選單的開始／關閉練習

    def __init__(self, cfg: dict):
        super().__init__(None)
        self.cfg = cfg
        self.setWindowTitle("SF6 連段練習器－浮動視窗")
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setAttribute(Qt.WA_ShowWithoutActivating)
        self.setMinimumSize(260, 120)

        box = self.box = QVBoxLayout(self)
        box.setContentsMargins(10, 8, 10, 8)
        box.setSpacing(0)
        self.view = TrialView()
        self.view.setAttribute(Qt.WA_TransparentForMouseEvents)
        box.addWidget(self.view, 1)

        self._drag: QPoint | None = None
        self._resize: tuple | None = None     # (邊, 按下時的滑鼠位置, 按下時的視窗範圍)
        self.setMouseTracking(True)
        self._apply_flags()
        self.apply_look()
        self._restore_geometry()

    # ---- 視窗屬性 ----
    @property
    def locked(self) -> bool:
        return bool(self.cfg.get("overlay_locked"))

    def _apply_flags(self):
        flags = (Qt.Tool | Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint
                 | Qt.WindowDoesNotAcceptFocus | Qt.NoDropShadowWindowHint)
        if self.locked:
            flags |= Qt.WindowTransparentForInput
        visible = self.isVisible()
        self.setWindowFlags(flags)
        # 解鎖時下面留一行放「拖曳移動・右鍵設定」
        self.box.setContentsMargins(10, 8, 10, 8 if self.locked else 24)
        if visible:
            self.show()

    def apply_look(self):
        self.setWindowOpacity(float(self.cfg.get("overlay_opacity", 0.85)))
        look = (float(self.cfg.get("overlay_zoom", 1.0)), int(self.cfg.get("overlay_rows_shown", 0)))
        self.view.set_compact(*look)
        if getattr(self, "_look", None) not in (None, look):
            self.fit_to_content()        # 換了字的大小或列數：視窗跟著調整成剛好放得下
        self._look = look
        self.update()

    def fit_to_content(self):
        hint = self.view.sizeHint()
        m = self.box.contentsMargins()
        w = hint.width() + m.left() + m.right()
        h = hint.height() + m.top() + m.bottom()
        avail = self.screen().availableGeometry()
        self.resize(max(self.minimumWidth(), min(w, avail.width())),
                    max(self.minimumHeight(), min(h, avail.height())))
        self._keep_on_screen()
        self._remember()

    def set_locked(self, on: bool):
        self.cfg["overlay_locked"] = on
        self._apply_flags()
        self.update()
        self.settings_changed.emit()

    def toggle_locked(self):
        self.set_locked(not self.locked)

    def set_shown(self, on: bool):
        if on == self.isVisible():
            return
        self.setVisible(on)
        if on:
            self._keep_on_screen()
        self.cfg["overlay_visible"] = on
        self.visibility_changed.emit(on)
        self.settings_changed.emit()

    def toggle_shown(self):
        self.set_shown(not self.isVisible())

    # ---- 位置 ----
    def _restore_geometry(self):
        g = self.cfg.get("overlay_geometry")
        if isinstance(g, list) and len(g) == 4:
            self.setGeometry(*[int(v) for v in g])
        else:
            avail = self.screen().availableGeometry()
            self.setGeometry(avail.left() + 40, avail.top() + 180, 760, 330)
        self._keep_on_screen()

    def _keep_on_screen(self):
        """跑到螢幕外（例如拔掉第二個螢幕）就移回主螢幕。"""
        from PySide6.QtGui import QGuiApplication
        center = self.geometry().center()
        if any(s.availableGeometry().contains(center) for s in QGuiApplication.screens()):
            return
        avail = QGuiApplication.primaryScreen().availableGeometry()
        self.move(avail.left() + 40, avail.top() + 180)

    def _remember(self):
        g = self.geometry()
        self.cfg["overlay_geometry"] = [g.x(), g.y(), g.width(), g.height()]
        self.settings_changed.emit()

    # ---- 繪圖 ----
    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        r = QRectF(self.rect()).adjusted(3, 3, -3, -3)
        bg = QColor(theme.c("panel"))
        bg.setAlpha(238)
        theme.draw_ink_rect(p, r, 14, bg, 2.6, theme.seed_of("overlay", int(r.width()), int(r.height())))
        if not self.locked:
            p.setPen(QColor(theme.c("muted")))
            p.setFont(theme.font(8.5))
            p.drawText(r.adjusted(0, 0, -24, -4), Qt.AlignRight | Qt.AlignBottom, "拖曳移動・拉邊框調大小・滾輪捲動・右鍵設定")
        p.end()

    def resizeEvent(self, e):
        super().resizeEvent(e)

    # ---- 滑鼠：中間拖曳移動，四邊和四個角拖曳調整大小 ----
    EDGE = 10

    def _edge_at(self, pos) -> str:
        if self.locked:
            return ""
        e = self.EDGE
        x, y, w, h = pos.x(), pos.y(), self.width(), self.height()
        edge = ("t" if y < e else "b" if y > h - e else "") + ("l" if x < e else "r" if x > w - e else "")
        return edge

    _CURSORS = {"t": Qt.SizeVerCursor, "b": Qt.SizeVerCursor, "l": Qt.SizeHorCursor, "r": Qt.SizeHorCursor,
                "tl": Qt.SizeFDiagCursor, "br": Qt.SizeFDiagCursor, "tr": Qt.SizeBDiagCursor,
                "bl": Qt.SizeBDiagCursor}

    def mousePressEvent(self, e):
        if e.button() == Qt.LeftButton and not self.locked:
            edge = self._edge_at(e.position())
            if edge:
                self._resize = (edge, e.globalPosition().toPoint(), self.geometry())
            else:
                self._drag = e.globalPosition().toPoint() - self.frameGeometry().topLeft()
        super().mousePressEvent(e)

    def mouseMoveEvent(self, e):
        gp = e.globalPosition().toPoint()
        if self._resize and e.buttons() & Qt.LeftButton:
            edge, start, g = self._resize
            d = gp - start
            left, top, right, bottom = g.left(), g.top(), g.right(), g.bottom()
            mw, mh = self.minimumWidth(), self.minimumHeight()
            if "l" in edge:
                left = min(left + d.x(), right - mw)
            if "r" in edge:
                right = max(right + d.x(), left + mw)
            if "t" in edge:
                top = min(top + d.y(), bottom - mh)
            if "b" in edge:
                bottom = max(bottom + d.y(), top + mh)
            self.setGeometry(left, top, right - left + 1, bottom - top + 1)
            return
        if self._drag is not None and e.buttons() & Qt.LeftButton:
            self.move(gp - self._drag)
            return
        edge = self._edge_at(e.position())
        self.setCursor(self._CURSORS.get(edge, Qt.SizeAllCursor if not self.locked else Qt.ArrowCursor))

    def mouseReleaseEvent(self, e):
        if self._drag is not None or self._resize is not None:
            self._drag = None
            self._resize = None
            self._remember()
        super().mouseReleaseEvent(e)

    def contextMenuEvent(self, e):
        menu = QMenu(self)
        if not self.view.practice:
            menu.addAction("開始練習").triggered.connect(lambda: self.practice_requested.emit(True))
            menu.addSeparator()
        op = menu.addMenu("透明度")
        group = QActionGroup(op)
        cur = float(self.cfg.get("overlay_opacity", 0.85))
        for v in OPACITY_CHOICES:
            a = QAction(f"{round(v * 100)}%", op, checkable=True, checked=abs(v - cur) < 0.01)
            a.triggered.connect(lambda _=False, v=v: self._set("overlay_opacity", v))
            group.addAction(a)
            op.addAction(a)
        zm = menu.addMenu("字的大小")
        group2 = QActionGroup(zm)
        curz = float(self.cfg.get("overlay_zoom", 1.0))
        for name, v in ZOOM_CHOICES:
            a = QAction(name, zm, checkable=True, checked=abs(v - curz) < 0.01)
            a.triggered.connect(lambda _=False, v=v: self._set("overlay_zoom", v))
            group2.addAction(a)
            zm.addAction(a)
        rm = menu.addMenu("顯示幾列")
        group3 = QActionGroup(rm)
        curr = int(self.cfg.get("overlay_rows_shown", 0))
        for v in ROW_CHOICES:
            a = QAction("全部（放不下就捲動）" if v == 0 else f"{v} 列", rm, checkable=True, checked=v == curr)
            a.triggered.connect(lambda _=False, v=v: self._set("overlay_rows_shown", v))
            group3.addAction(a)
            rm.addAction(a)
        menu.addSeparator()
        lock = menu.addAction("鎖定（滑鼠穿過；用快捷鍵或系統匣解鎖）")
        lock.triggered.connect(lambda: self.set_locked(True))
        close = menu.addAction("關閉練習（關閉浮動視窗）")
        close.triggered.connect(self._close_all)
        menu.exec(e.globalPos())

    def _close_all(self):
        """練習和浮動視窗一起關。"""
        self.practice_requested.emit(False)
        self.set_shown(False)

    def wheelEvent(self, e):
        # 滑鼠滾輪往下看；下一列亮起時會自動捲回目前這列
        self.view.scroll_by(-e.angleDelta().y() / 120 * 54)

    def _set(self, key: str, value):
        self.cfg[key] = value
        self.apply_look()
        self.settings_changed.emit()

    def hideEvent(self, e):
        self._remember()
        super().hideEvent(e)
