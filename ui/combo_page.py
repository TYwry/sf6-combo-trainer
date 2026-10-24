"""連段庫分頁：左邊是這個角色的連段清單，右邊編輯記法並即時顯示成訓練場樣式。"""
from __future__ import annotations

import logging
import time

from PySide6.QtCore import (QEasingCurve, QEvent, QPoint, QPropertyAnimation, Qt, QTimer, QUrl, QVariantAnimation,
                            Signal)
from PySide6.QtGui import QDesktopServices, QPainter
from PySide6.QtWidgets import (QCheckBox, QComboBox, QDialog, QDoubleSpinBox, QFrame, QGridLayout,
                               QHBoxLayout, QLabel, QLineEdit, QListWidget, QListWidgetItem,
                               QInputDialog, QMenu, QPlainTextEdit, QPushButton, QScrollArea, QSpinBox,
                               QStyle, QStyleOptionViewItem, QVBoxLayout, QWidget)

from core import key_sender, video
from core.game_bridge import GameFeed, GameJudge
from core.key_resolver import KeyResolver
from core.matcher import Matcher
from core.notation import _split_groups, compute_cost, group_move_name, normalize_simul, parse
from core.recorder import Recorder
from data.storage import ComboStore
from ui import theme
from ui.controller_setup import ConfirmButton, confirm
from ui.numpad_chart import NumpadChart
from ui.row_editor import FlowHolder, RowEditor
from ui.trial_view import TrialView

log = logging.getLogger(__name__)


class ReorderList(QListWidget):
    """連段清單：只上下捲；按住一項上下拖可以排順序，其他項目即時讓位。"""
    reordered = Signal(list)          # 放開時，畫面上所有連段 id 的新順序
    check_clicked = Signal(object)    # 選取模式：點了哪一項（勾／取消勾）
    DRAG_START = 8                    # 按住移動超過幾像素才算開始拖（輕點還是選取）

    def __init__(self, parent=None):
        super().__init__(parent)
        self.select_mode = False
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.setVerticalScrollMode(QListWidget.ScrollPerPixel)
        self.setTextElideMode(Qt.ElideRight)
        self.setWordWrap(False)
        self._press_y = None
        self._drag_item = None
        self._moved = False
        self._last_y = 0
        self._scroll = QTimer(self)          # 拖到上下邊緣時自動捲動，也順便讓其他項目抖動
        self._scroll.setInterval(30)
        self._scroll.timeout.connect(self._auto_scroll)
        self._lift = QVariantAnimation(self)  # 浮起來／放下去的動畫
        self._lift.setDuration(140)
        self._lift.setEasingCurve(QEasingCurve.OutBack)
        self._lift.valueChanged.connect(self._set_lift)
        self._lift.finished.connect(self._lift_done)

    def set_select_mode(self, on: bool):
        self.select_mode = on
        self.setProperty("select_mode", on)
        self.viewport().update()

    def mousePressEvent(self, e):
        if self.select_mode:
            # 選取模式：點一下勾／取消勾，不會換選連段，也不能拖
            item = self.itemAt(e.position().toPoint())
            if item and e.button() == Qt.LeftButton:
                self.check_clicked.emit(item)
                self.viewport().update()
            return
        super().mousePressEvent(e)
        if e.button() == Qt.LeftButton and self.itemAt(e.position().toPoint()):
            self._press_y = e.position().y()

    def mouseMoveEvent(self, e):
        y = e.position().y()
        if self._press_y is not None and self._drag_item is None and abs(y - self._press_y) >= self.DRAG_START:
            self._drag_item = self.currentItem()
            self._moved = False
            self._scroll.start()
            self._lift.stop()
            self._lift.setEasingCurve(QEasingCurve.OutBack)
            self._lift.setStartValue(float(self.property("drag_lift") or 0.0))
            self._lift.setEndValue(1.0)
            self._lift.start()
        if self._drag_item is None:
            return super().mouseMoveEvent(e)
        self._last_y = y
        self._follow(y)

    def _follow(self, y: float):
        item = self._drag_item
        src = self.row(item)
        target = self.indexAt(QPoint(10, int(y))).row()
        if target < 0:
            target = 0 if y < 0 else self.count() - 1
        if target != src:
            self.blockSignals(True)          # 搬動時不要觸發「換選連段」
            self.takeItem(src)
            self.insertItem(target, item)
            self.setCurrentItem(item)
            self.blockSignals(False)
            self._moved = True
        self.setProperty("drag_row", self.row(item))
        self.viewport().update()

    def _set_lift(self, v):
        self.setProperty("drag_lift", float(v))
        self.viewport().update()

    def _lift_done(self):
        if self._drag_item is None and float(self.property("drag_lift") or 0.0) <= 0.01:
            self.setProperty("drag_row", None)
            self.viewport().update()

    def paintEvent(self, e):
        super().paintEvent(e)
        row = self.property("drag_row")
        if row is None or not (0 <= row < self.count()):
            return
        # 浮起來的那一項最後畫，蓋在其他項目上面
        idx = self.model().index(row, 0)
        opt = QStyleOptionViewItem()
        self.initViewItemOption(opt)
        opt.rect = self.visualRect(idx)
        if self.currentRow() == row:
            opt.state |= QStyle.State_Selected
        opt.widget = self
        p = QPainter(self.viewport())
        self.setProperty("lift_pass", True)
        self.itemDelegate().paint(p, opt, idx)
        self.setProperty("lift_pass", False)
        p.end()

    def _auto_scroll(self):
        self.setProperty("wiggle", float(self.property("wiggle") or 0.0) + 0.5)
        self.viewport().update()
        bar = self.verticalScrollBar()
        h = self.viewport().height()
        edge = 36
        if self._last_y < edge:
            bar.setValue(bar.value() - 8)
        elif self._last_y > h - edge:
            bar.setValue(bar.value() + 8)
        else:
            return
        self._follow(self._last_y)

    def mouseDoubleClickEvent(self, e):
        if not self.select_mode:
            super().mouseDoubleClickEvent(e)

    def mouseReleaseEvent(self, e):
        if self.select_mode:
            return
        dragged, moved = self._drag_item, self._moved
        self._press_y = None
        self._drag_item = None
        self._moved = False
        self._scroll.stop()
        if dragged is not None:
            # 放下去：縮回原本大小，動畫結束才拿掉浮起狀態
            self._lift.stop()
            self._lift.setEasingCurve(QEasingCurve.InOutQuad)
            self._lift.setStartValue(float(self.property("drag_lift") or 1.0))
            self._lift.setEndValue(0.0)
            self._lift.start()
        if dragged is None:
            return super().mouseReleaseEvent(e)
        if moved:
            self.reordered.emit([self.item(i).data(Qt.UserRole) for i in range(self.count())])


HELP_TEXT = """連段記法

・一列一組：用 >（或 , 或 【】）分開，練習畫面上一組是一列。
・組裡的 . 只是分隔，例如 HP.HK 是「重拳接重腳」。
・方向用數字（角色面向右時，可以對照左邊的方向數字圖）：
    7＝↖　8＝↑　9＝↗
    4＝←　5＝中立　6＝→
    1＝↙　2＝↓　3＝↘
  例如 236 是 ↓↘→，623 是 →↓↘。蓄力寫成 [4]6P。
・按鍵：LP MP HP LK MK HK；P／K＝任一拳／腳；PP＝任兩顆拳（OD）。
  同時按寫 LP+LK 或 LPLK（連著寫就是同時按）；要分開按請用 . 隔開，例如 HP.HK。
  HP+HK（HPHK）＝DI（動力衝擊）、MP+MK＝DP（Parry），會顯示你設定的那顆鍵。
・系統：DR 取消前衝、PDR 格擋前衝、DI（動力衝擊）、DP（Parry）、SA1／SA2／SA3／CA。
・j. 表示跳躍中，例如 j.HP。
・括號裡是備註，例如 2MK(確認) > DR。

例：2MK > DR > HP.HK > 22P > 236236K"""

CHECK_ROLE = Qt.UserRole + 1   # 清單項目：選取模式下有沒有勾起來
SUCCESS_S = 1.5          # 「Success」顯示多久（秒）
FAIL_S = 1.5             # 遊戲判定沒過時，「連段斷了」顯示多久（秒）
PENDING_SHOW_S = 0.3     # 等遊戲判定超過這麼久才顯示「判定中…」
SUCCESS_GRACE_S = 1.0    # Success 後這麼久內，除了 Back 以外的輸入都不會讓連段重來


class ComboPage(QWidget):
    combo_selected = Signal(int)      # 目前選的連段 id（0＝沒選）
    settings_changed = Signal()
    export_requested = Signal()
    import_requested = Signal()
    site_requested = Signal()

    def __init__(self, cfg: dict, store: ComboStore, parent=None):
        super().__init__(parent)
        self.cfg = cfg
        self.store = store
        self.character = ""
        self.movelist: dict | None = None
        self.current_id = 0
        self._loading = False
        self._save_timer = QTimer(self, singleShot=True, interval=400)
        self._save_timer.timeout.connect(self._save_now)
        self._checked: set[int] = set()        # 選取模式下勾起來的連段 id
        self.mirrors: list = []          # 其他顯示同一個連段的畫面（浮動視窗）
        self._build()

    # ---- 介面 ----
    def _build(self):
        outer = QHBoxLayout(self)
        outer.setContentsMargins(20, 16, 20, 16)
        outer.setSpacing(12)

        # 左：清單
        left = theme.GlassPanel(dark=True)
        left.setFixedWidth(270)
        lbox = QVBoxLayout(left)
        lbox.setContentsMargins(16, 14, 18, 18)
        lbox.addWidget(theme.GlowTitle("連段清單", 11.5))
        frow = QHBoxLayout()
        frow.addWidget(QLabel("清單"))
        self.list_filter = QComboBox()
        self.list_filter.setAccessibleName("只列出這個清單（標籤）的連段")
        self.list_filter.currentIndexChanged.connect(self._filter_changed)
        frow.addWidget(self.list_filter, 1)
        lbox.addLayout(frow)
        self.list = ReorderList()
        self.list.reordered.connect(self._list_reordered)
        self.list.check_clicked.connect(self._toggle_checked)
        self.list.setContextMenuPolicy(Qt.CustomContextMenu)
        self.list.customContextMenuRequested.connect(self._list_menu)
        self.list.setAccessibleName("連段清單")
        self.list.setItemDelegate(theme.InkListDelegate(self.list))
        self.list.setMouseTracking(True)
        self.list.currentItemChanged.connect(self._on_select)
        lbox.addWidget(self.list, 1)
        btns = QHBoxLayout()
        self.add_btn = QPushButton("新增")
        self.add_btn.clicked.connect(lambda: self.new_combo())
        self.select_btn = QPushButton("選取")
        self.select_btn.setCheckable(True)
        self.select_btn.setAccessibleName("選取多個連段來整理")
        self.select_btn.toggled.connect(self._select_mode)
        btns.addWidget(self.add_btn, 1)
        btns.addWidget(self.select_btn, 1)
        lbox.addLayout(btns)
        # 選取模式才出現：對選到的連段做整理
        self.sel_bar = QWidget()
        sbox = QVBoxLayout(self.sel_bar)
        sbox.setContentsMargins(0, 0, 0, 0)
        sbox.setSpacing(6)
        self.sel_count = QLabel()
        self.sel_count.setObjectName("muted")
        sbox.addWidget(self.sel_count)
        srow = QHBoxLayout()
        self.sel_add = QPushButton("加到清單")
        self.sel_add.clicked.connect(self._sel_add_menu)
        self.sel_remove = QPushButton("從清單拿掉")
        self.sel_remove.clicked.connect(self._sel_remove_menu)
        srow.addWidget(self.sel_add, 1)
        srow.addWidget(self.sel_remove, 1)
        sbox.addLayout(srow)
        self.sel_del = QPushButton("刪除選到的連段")
        self.sel_del.clicked.connect(self._sel_delete)
        sbox.addWidget(self.sel_del)
        self.sel_bar.hide()
        lbox.addWidget(self.sel_bar)
        self.site_btn = QPushButton("從網站抓連段")
        self.site_btn.clicked.connect(self.site_requested.emit)
        lbox.addWidget(self.site_btn)
        self.image_btn = QPushButton("從圖片加入")
        self.image_btn.clicked.connect(self._from_image)
        lbox.addWidget(self.image_btn)
        self.numpad = NumpadChart()
        self.numpad.setFixedHeight(220)
        self.numpad.setVisible(bool(self.cfg.get("show_numpad", True)))
        lbox.addWidget(self.numpad)
        outer.addWidget(left)

        # 右：編輯
        right = theme.GlassPanel()
        rbox = QVBoxLayout(right)
        rbox.setContentsMargins(16, 12, 16, 14)
        rbox.setSpacing(8)

        self.empty_hint = QLabel("左邊按「新增」建立第一個連段，或到「指令表」分頁點招式加入。")
        self.empty_hint.setObjectName("muted")
        self.empty_hint.setWordWrap(True)
        rbox.addWidget(self.empty_hint)

        self.editor = QWidget()
        ebox = QVBoxLayout(self.editor)
        ebox.setContentsMargins(0, 0, 0, 0)
        ebox.setSpacing(8)

        name_row = QHBoxLayout()
        name_row.addWidget(QLabel("名稱"))
        self.name = QLineEdit()
        self.name.setAccessibleName("連段名稱")
        self.name.textChanged.connect(self._changed)
        name_row.addWidget(self.name, 1)
        self._edit_only = [self._wrap(ebox, name_row)]

        # 標籤（也是清單）：點標籤可以拿掉，右邊打字按 Enter 新增
        tag_row = QHBoxLayout()
        tag_row.addWidget(QLabel("標籤"))
        self.tag_holder = FlowHolder()
        tag_row.addWidget(self.tag_holder, 1)
        self.tag_input = QLineEdit()
        self.tag_input.setAccessibleName("新增標籤或清單名稱")
        self.tag_input.setFixedWidth(150)
        self.tag_input.returnPressed.connect(self._add_tag_from_input)
        tag_row.addWidget(self.tag_input)
        add_tag = QPushButton("加標籤")
        add_tag.clicked.connect(self._add_tag_from_input)
        tag_row.addWidget(add_tag)
        self._edit_only.append(self._wrap(ebox, tag_row))
        self._tags: list[str] = []

        note_row = QHBoxLayout()
        # 整段記法很少用，預設收起來；點一下展開
        self.notation_toggle = QPushButton("▸ 整段記法（點一下展開）")
        self.notation_toggle.setCheckable(True)
        self.notation_toggle.toggled.connect(self._notation_toggled)
        note_row.addWidget(self.notation_toggle)
        note_row.addStretch(1)
        help_btn = QPushButton("記法說明")
        help_btn.clicked.connect(self._show_help)
        note_row.addWidget(help_btn)
        self._edit_only.append(self._wrap(ebox, note_row))
        self.notation = QPlainTextEdit()
        self.notation.setAccessibleName("連段記法")
        self.notation.setFont(theme.font(12))
        self.notation.setFixedHeight(64)
        self.notation.textChanged.connect(self._changed)
        self.notation.installEventFilter(self)     # 按 Enter 收起；Shift＋Enter 才換行
        ebox.addWidget(self.notation)
        self.notation.setVisible(False)
        self.errors = QLabel()
        self.errors.setWordWrap(True)
        ebox.addWidget(self.errors)

        # 參考影片（YouTube 等）：可以指定從幾分幾秒開始播
        vrow = QHBoxLayout()
        vrow.addWidget(QLabel("影片連結"))
        self.video_url = QLineEdit()
        self.video_url.setAccessibleName("影片連結")
        self.video_url.textChanged.connect(self._video_url_changed)
        vrow.addWidget(self.video_url, 1)
        vrow.addWidget(QLabel("開始時間"))
        self.video_t = QLineEdit()
        self.video_t.setAccessibleName("影片開始時間（例如 1:23）")
        self.video_t.setFixedWidth(80)
        self.video_t.setAlignment(Qt.AlignCenter)
        self.video_t.textChanged.connect(self._video_changed)
        vrow.addWidget(self.video_t)
        self.video_btn = QPushButton("開啟")
        self.video_btn.clicked.connect(self._open_video)
        vrow.addWidget(self.video_btn)
        ebox.addLayout(vrow)
        self.video_hint = QLabel("貼上影片網址；開始時間寫成 1:23 或 83（秒）。"
                                 "用 YouTube「分享 → 開始時間」複製的網址，時間會自動填好。")
        self.video_hint.setObjectName("muted")
        self.video_hint.setWordWrap(True)
        ebox.addWidget(self.video_hint)

        opts = QHBoxLayout()
        self.facing = QCheckBox("角色在右邊（方向左右對調）")
        self.facing.setChecked(not self.cfg.get("trial_facing_right", True))
        self.facing.toggled.connect(self._display_changed)
        opts.addWidget(self.facing)
        opts.addSpacing(16)
        opts.addWidget(QLabel("按鍵顯示"))
        self.style = QComboBox()
        self.style.setAccessibleName("按鍵顯示方式")
        self.style.addItem("我的控制器按鍵", "pad")
        self.style.addItem("英文方向鍵", "text")
        self.style.setCurrentIndex(0 if self.cfg.get("trial_button_style", "pad") == "pad" else 1)
        self.style.currentIndexChanged.connect(self._display_changed)
        opts.addWidget(self.style)
        opts.addStretch(1)
        ebox.addLayout(opts)

        self._build_practice(ebox)
        self.view = TrialView()
        self.view.setCursor(Qt.PointingHandCursor)
        self.view.editable = True
        self.view.row_clicked.connect(self._edit_row)
        self.view.add_clicked.connect(self._add_action)
        self.view.row_moved.connect(self._row_moved)
        self._place_practice_btn()
        scroll = self.scroll = QScrollArea()
        scroll.setWidget(self.view)
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        scroll.setMinimumHeight(200)
        self._scroll_anim = QPropertyAnimation(scroll.verticalScrollBar(), b"value", self)
        self._scroll_anim.setDuration(380)
        self._scroll_anim.setEasingCurve(QEasingCurve.OutCubic)
        view_row = QHBoxLayout()
        view_row.setSpacing(10)
        view_row.addWidget(scroll, 1)
        ebox.addLayout(view_row, 1)
        self.row_hint = QLabel("點連段裡任何一列，可以單獨編輯那一列（打字、點按鈕或用控制器錄）。")
        self.row_hint.setObjectName("muted")
        ebox.addWidget(self.row_hint)
        self._edit_only.append(self.row_hint)
        self.row_editor = RowEditor(self.cfg)
        self.row_editor.hide()
        self.row_editor.text_changed.connect(self._row_text_changed)
        self.row_editor.insert_below.connect(self._row_insert)
        self.row_editor.delete_row.connect(self._row_delete)
        self.row_editor.closed.connect(self._close_row_editor)
        self.row_editor.record_toggled.connect(self._record_toggled)
        self.recorder: Recorder | None = None
        self._record_done = None          # 錄完要把記法交給誰（列編輯區或自訂按鈕視窗）
        self.row_editor.record_host = self
        self._row_editing = False
        self.row_editor.setFixedWidth(500)
        view_row.addWidget(self.row_editor)

        stats = QGridLayout()
        stats.setHorizontalSpacing(10)
        stats.addWidget(QLabel("傷害"), 0, 0)
        self.damage = QSpinBox()
        self.damage.setAccessibleName("傷害")
        self.damage.setRange(0, 20000)
        self.damage.setSingleStep(100)
        self.damage.setSpecialValueText("未填")
        self.damage.valueChanged.connect(self._changed)
        stats.addWidget(self.damage, 0, 1)

        stats.addWidget(QLabel("動力槽（格）"), 0, 2)
        self.drive_auto = QLabel()
        stats.addWidget(self.drive_auto, 0, 3)
        self.drive_manual = QCheckBox("手動改")
        self.drive_manual.toggled.connect(self._override_toggled)
        stats.addWidget(self.drive_manual, 0, 4)
        self.drive = QDoubleSpinBox()
        self.drive.setAccessibleName("動力槽手動數值")
        self.drive.setRange(0, 12)
        self.drive.setSingleStep(0.5)
        self.drive.setDecimals(1)
        self.drive.valueChanged.connect(self._changed)
        stats.addWidget(self.drive, 0, 5)

        stats.addWidget(QLabel("SA 槽（格）"), 1, 2)
        self.sa_auto = QLabel()
        stats.addWidget(self.sa_auto, 1, 3)
        self.sa_manual = QCheckBox("手動改")
        self.sa_manual.toggled.connect(self._override_toggled)
        stats.addWidget(self.sa_manual, 1, 4)
        self.sa = QSpinBox()
        self.sa.setAccessibleName("SA 槽手動數值")
        self.sa.setRange(0, 6)
        self.sa.valueChanged.connect(self._changed)
        stats.addWidget(self.sa, 1, 5)
        stats.setColumnStretch(6, 1)
        self._edit_only.append(self._wrap(ebox, stats))
        self.cost_detail = QLabel()
        self.cost_detail.setObjectName("muted")
        self.cost_detail.setWordWrap(True)
        self.cost_detail.setFont(theme.font(9))
        ebox.addWidget(self.cost_detail)
        self._edit_only.append(self.cost_detail)

        notes_label = QLabel("筆記")
        ebox.addWidget(notes_label)
        self._edit_only.append(notes_label)
        self.notes = QPlainTextEdit()
        self.notes.setAccessibleName("筆記")
        self.notes.setFixedHeight(70)
        self.notes.textChanged.connect(self._changed)
        ebox.addWidget(self.notes)
        self._edit_only.append(self.notes)
        self._lower = self._edit_only[-4:]     # 傷害、槽、筆記：編輯一列時先藏起來，讓出空間

        rbox.addWidget(self.editor, 1)
        outer.addWidget(right, 1)
        self._show_editor(False)

    @staticmethod
    def _wrap(box, layout) -> QWidget:
        w = QWidget()
        layout.setContentsMargins(0, 0, 0, 0)
        w.setLayout(layout)
        box.addWidget(w)
        return w

    def apply_theme(self):
        t = theme.colors()
        self.errors.setStyleSheet(f"color: {t['warn']};")
        self.view.update()

    # ---- 角色與清單 ----
    def set_character(self, cid: str, movelist: dict | None):
        self._save_now()
        self.character, self.movelist = cid, movelist
        self._reload_list()

    def set_movelist(self, movelist: dict | None):
        self.movelist = movelist
        self._refresh_preview()

    def _views(self) -> list:
        return [self.view] + self.mirrors

    def add_mirror(self, view):
        """浮動視窗的連段顯示：跟主畫面同步內容與練習進度。"""
        self.mirrors.append(view)
        view.set_facing(self.view.facing_right)
        view.set_button_style(self.view.style, self.view.layout_keys)
        view.set_combo(self.view.combo, self.view.names, self.view.title)
        view.set_stats(*self.view.stats)
        view.set_practice(self.view.practice)

    # ---- 給快捷鍵用 ----
    def toggle_practice(self):
        if self.current_id:
            self.practice_btn.toggle()

    def restart_practice(self):
        self._restart(save=True)

    def select_offset(self, delta: int) -> str:
        """換到上／下一個連段（頭尾相接）；回傳新連段的名稱。"""
        n = self.list.count()
        if not n:
            return ""
        row = (self.list.currentRow() + delta) % n
        self.list.setCurrentRow(row)
        return self.list.item(row).text()

    def set_layout_keys(self):
        for v in self._views():
            v.set_button_style(self.style.currentData(), self.cfg["layout"])
        self.resolver.set_layout(self.cfg["layout"])

    # ---- 練習 ----
    def _build_practice(self, ebox):
        self.matcher: Matcher | None = None
        self.finish_t = 0.0
        # 遊戲判定：主視窗把 GameFeed 交給這裡
        self.game_feed: GameFeed | None = None
        self.judge: GameJudge | None = None
        self._pending_t = 0.0             # 輸入比對完成、正在等遊戲判定（0＝沒有在等）
        self._reset_due = 0.0             # 連段斷了：到這個時間自動重置（0＝沒有要重置）
        self._banner: tuple[str, str, float] | None = None   # (字, 顏色, 顯示到什麼時候)
        self._game_key = None
        self._last_row = -1
        self.resolver = KeyResolver(self.cfg["layout"], self.cfg.get("combo_window_ms", 12))

        row = QHBoxLayout()
        self.practice_btn = QPushButton("開始練習")
        self.practice_btn.setCheckable(True)
        self.practice_btn.toggled.connect(self._toggle_practice)
        self.autonext = QCheckBox("練完換下一個")
        self.autonext.setToolTip("輸入比對完成後，自動換到目前清單的下一個連段接著練；最後一個練完回到第一個。")
        self.autonext.setChecked(bool(self.cfg.get("practice_autonext", False)))
        self.autonext.toggled.connect(self._autonext_toggled)
        row.addWidget(self.autonext)
        self.autonext_s = QDoubleSpinBox()
        self.autonext_s.setAccessibleName("練完後等幾秒換下一個")
        self.autonext_s.setRange(0, 10)
        self.autonext_s.setDecimals(1)
        self.autonext_s.setSingleStep(0.5)
        self.autonext_s.setPrefix("等 ")
        self.autonext_s.setSuffix(" 秒")
        self.autonext_s.setValue(float(self.cfg.get("practice_autonext_s", 1.2)))
        self.autonext_s.setEnabled(self.autonext.isChecked())
        self.autonext_s.valueChanged.connect(self._autonext_s_changed)
        row.addWidget(self.autonext_s)
        row.addSpacing(10)
        self.rehit_btn = ConfirmButton("重記 Hit 數", "重記", message=self._rehit_message)
        self.rehit_btn.setToolTip("清掉這個連段記下的 Hit 數，下一次遊戲判定成功時重新記。")
        self.rehit_btn.confirmed.connect(self._reset_hits)
        row.addWidget(self.rehit_btn)
        row.addSpacing(10)
        self.practice_status = QLabel()
        self.practice_status.setWordWrap(True)
        row.addWidget(self.practice_status, 1)
        ebox.addLayout(row)

        opts = QHBoxLayout()

        def spin(label, lo, hi, value, suffix, step=1.0, decimals=0):
            opts.addWidget(QLabel(label))
            box = QDoubleSpinBox()
            box.setAccessibleName(label)
            box.setRange(lo, hi)
            box.setDecimals(decimals)
            box.setSingleStep(step)
            box.setSuffix(suffix)
            box.setValue(value)
            box.valueChanged.connect(self._practice_settings_changed)
            opts.addWidget(box)
            opts.addSpacing(10)
            return box
        self.reset_s = spin("自動重來", 1, 10, float(self.cfg.get("practice_reset_s", 3.0)), " 秒", 0.5, 1)
        self.simul = spin("同時按容差", 1, 10, float(self.cfg.get("practice_simul_frames", 3)), " 幀")
        self.charge = spin("蓄力", 10, 90, float(self.cfg.get("practice_charge_frames", 40)), " 幀")
        opts.addStretch(1)
        ebox.addLayout(opts)
        self._update_practice_status()

    def _practice_settings(self) -> dict:
        return {"practice_strict": False,          # 嚴格模式已拿掉
                "practice_reset_s": self.reset_s.value(),
                "practice_simul_frames": int(self.simul.value()),
                "practice_charge_frames": int(self.charge.value())}

    def _practice_settings_changed(self):
        self.cfg.update(self._practice_settings())
        if self.matcher:
            self.matcher.settings.update(self._practice_settings())
        self.settings_changed.emit()

    def _toggle_practice(self, on: bool):
        self.practice_btn.setText("停止練習" if on else "開始練習")
        self._place_practice_btn()
        # 練習中也可以編輯連段庫的所有東西，所以編輯區不再藏起來
        self._last_row = -1
        if on:
            self._new_matcher()
        else:
            self._refresh_preview()
            self._save_attempt()
            self.matcher = None
            for v in self._views():
                v.set_practice(None)
        self._update_practice_status()

    def _place_practice_btn(self):
        """開始／停止練習按鈕放在連段顯示的標題前面。"""
        b = self.practice_btn
        if b.parent() is not self.view:
            b.setParent(self.view)
            b.show()
        b.adjustSize()
        b.move(12, max(0, (46 - b.height()) // 2))
        self.view.title_x = 12 + b.width() + 14
        self.view.update()

    def _new_matcher(self):
        history = self.matcher.history if self.matcher else None
        combo = parse(self.notation.toPlainText())
        self._matcher_text = self.notation.toPlainText()
        self.matcher = Matcher(combo, self._practice_settings(), self.cfg.get("trial_facing_right", True))
        if history:
            self.matcher.history = history
        self.finish_t = 0.0
        self._clear_judge()

    def _restart(self, save: bool):
        if not self.matcher:
            return
        if save:
            self._save_attempt()
        self.matcher.reset()
        self.finish_t = 0.0
        self._clear_judge()
        self._last_row = -1
        self._update_practice_status()

    def _clear_judge(self):
        self.judge = None
        self._pending_t = 0.0
        self._banner = None

    def _save_attempt(self):
        m = self.matcher
        if not m or not self.current_id or m.done == 0 or self.finish_t:
            return
        self._store_attempt(m)

    def _store_attempt(self, m: Matcher, done: int | None = None):
        started = time.time() - (time.perf_counter() - m.started_t)
        self.store.add_practice(self.current_id, self.notation.toPlainText(), started, m.total,
                                m.done if done is None else done, m.gaps(), False)

    # ---- 遊戲判定 ----
    def _game_on(self, now: float) -> bool:
        return bool(self.cfg.get("game_check_enabled", True)) and bool(self.game_feed) \
            and self.game_feed.training(now)

    def _expected_hits(self) -> int | None:
        """這個連段記下的 Hit 數；連段內容還沒存檔（跟記錄時不同）就當作還沒記。"""
        row = self.store.get(self.current_id) if self.current_id else None
        if not row or row["notation"] != self.notation.toPlainText():
            return None
        return row.get("game_hits")

    def _rehit_message(self) -> str:
        n = self._expected_hits()
        return (f"這個連段目前記的是 {n} Hit。\n\n要清掉重記嗎？下一次遊戲判定成功時會重新記下 Hit 數。"
                if n else "這個連段還沒記過 Hit 數。")

    def _reset_hits(self):
        if self.current_id:
            self.store.update(self.current_id, game_hits=None)
        self._update_practice_status()

    def _game_status(self, now: float) -> str:
        if not self.cfg.get("game_check_enabled", True):
            return ""
        feed = self.game_feed
        if not feed or not feed.path:
            return "遊戲判定：找不到遊戲資料夾，先只比對輸入（到「設定 → 遊戲 mod」設定）。"
        if not feed.alive(now):
            return "遊戲判定：沒收到遊戲資料（遊戲沒開，或遊戲內腳本沒裝好），先只比對輸入。"
        if not feed.training(now):
            return "遊戲判定：遊戲不在訓練模式，先只比對輸入。"
        n = self._expected_hits()
        return (f"遊戲判定：開著，這段要打到 {n} Hit。" if n
                else "遊戲判定：開著，這段還沒記過 Hit 數，下一次成功會記下來。")

    def _complete(self, t: float):
        """這一次練習成功（輸入比對完成；有遊戲判定時也通過了）。"""
        m = self.matcher
        self._pending_t = 0.0
        self.finish_t = t
        if self.current_id:
            self._store_attempt(m)
        self._update_practice_status()
        if self.autonext.isChecked():
            QTimer.singleShot(int(self.autonext_s.value() * 1000),
                              lambda cid=self.current_id: self._auto_next(cid))

    def _fail(self, text: str, now: float, reset: bool = False):
        """遊戲判定沒過：記一筆沒完成的練習，顯示原因，直接開始下一次。

        reset＝連段斷了：設定有開的話，替使用者按一下遊戲的重置鍵。"""
        if reset and self.cfg.get("auto_reset_enabled"):
            try:
                delay = max(0.0, min(10.0, float(self.cfg.get("auto_reset_delay_s", 0.0))))
            except (TypeError, ValueError):
                delay = 0.0
            self._reset_due = now + delay       # 到時間才重置（_game_tick 檢查）
        m = self.matcher
        if self.current_id and m.done:
            self._store_attempt(m, min(m.done, m.total - 1))
        m.reset()
        self.finish_t = 0.0
        self.judge = None
        self._pending_t = 0.0
        self._banner = (text, "warn", now + FAIL_S)
        self._last_row = -1
        self._update_practice_status()

    def _do_reset(self, now: float):
        """連段斷了之後的自動重置：遊戲直接重置，或替使用者按鍵盤按鍵。只在訓練模式。"""
        self._reset_due = 0.0
        if not (self.game_feed and self.game_feed.training(now)):
            return
        mode = self.cfg.get("auto_reset_mode", "game")
        if mode == "key":
            why = key_sender.press(self.cfg.get("auto_reset_key", ""))
            if why:
                log.info("沒有自動按重置鍵：%s", why)
        else:
            self.game_feed.request_reset("state" if mode == "state" else "refresh")

    def _game_tick(self, now: float):
        if self._reset_due and now >= self._reset_due:
            self._do_reset(now)
        m = self.matcher
        on = self._game_on(now)
        key = (bool(self.cfg.get("game_check_enabled", True)), bool(self.game_feed and self.game_feed.path),
               bool(self.game_feed and self.game_feed.alive(now)), on)
        if key != self._game_key:
            self._game_key = key
            self._update_practice_status()
        if self._banner and now > self._banner[2]:
            self._banner = None
        if not on:
            self.judge = None
            if self._pending_t:
                self._complete(now)          # 等判定時遊戲資料斷了：只看輸入
            return
        hits = self.game_feed.hits()
        if m.done > 0 and self.judge is None and not self.finish_t and not self._pending_t:
            self.judge = GameJudge(hits, now)
        if not self.judge:
            if self._pending_t:
                self._complete(now)          # 這次開始時還沒有遊戲資料：只看輸入
            return
        self.judge.feed(hits, now)
        if not m.finished:
            if self.judge.dropped:
                self._fail("連段斷了", now, reset=True)
            return
        if not self._pending_t:
            return
        expected = self._expected_hits()
        if expected and self.judge.rose and self.judge.max_hits >= expected:
            self._complete(now)              # 已經打到記下的 Hit 數：馬上算成功，不用等連段結束
            return
        if self.judge.settled(self._pending_t, now):
            ok, text = self.judge.verdict(expected)
            if ok:
                if self.current_id and (not expected or self.judge.max_hits > expected):
                    self.store.update(self.current_id, game_hits=self.judge.max_hits)
                self._complete(now)
            else:
                self._fail(text, now, reset=self.judge.rose)   # 有打中但 Hit 數不夠也算斷了；完全沒打中不按

    def _update_practice_status(self):
        if not self.current_id:
            return
        stats = self.store.practice_stats(self.current_id)
        record = f"這個連段練過 {stats['tries']} 次，完成 {stats['clears']} 次。"
        if not self.matcher:
            text = "按「開始練習」後直接用控制器輸入，切到遊戲裡也會繼續讀取。" + record
        elif self.matcher.total == 0:
            text = "先在上面輸入連段。"
        elif self.matcher.finished and self._pending_t:
            text = "輸入對了，等遊戲確認連擊數…" + record
        elif self.matcher.finished:
            text = "完成！再按任何鍵重新開始。" + record
        else:
            text = "練習中：照著亮起的列輸入；控制器的 BACK 鍵（沒設定動作時）可以重來。" + record
        game = self._game_status(time.perf_counter())
        self.rehit_btn.setVisible(bool(game))
        self.practice_status.setText(text + ("\n" + game if game else ""))

    def practice_event(self, ev):
        """主視窗把控制器事件送進來（不管這個分頁有沒有顯示）。"""
        if self.recorder:
            self.recorder.feed(ev)
            return
        m = self.matcher
        if not m:
            return
        if ev.kind == "dir":
            if m.on_dir(ev.t, ev.direction) == "advance":
                self._after_advance(ev.t)
        elif ev.kind in ("press", "release"):
            for kev in self.resolver.feed(ev):
                self._practice_key(kev)

    def _practice_key(self, kev):
        m = self.matcher
        if not m or kev.kind != "press":
            return
        key = kev.key
        if key.get("inputs") == ["BACK"] and not key.get("action"):
            self._restart(save=True)
            return
        if m.finished:
            if self._pending_t or kev.t - self.finish_t < SUCCESS_GRACE_S:
                return                       # 正在等遊戲判定，或剛 Success（Back 在上面已處理）
            self._restart(save=False)
        if m.on_press(kev.t, key.get("action", "")) == "advance":
            self._after_advance(kev.t)

    def _after_advance(self, t: float):
        m = self.matcher
        if m.finished and not self.finish_t and not self._pending_t:
            if self.judge and self._game_on(t):
                self._pending_t = t          # 輸入對了，再等遊戲的連擊數確認
                self._update_practice_status()
            else:
                self._complete(t)

    def _autonext_toggled(self, on: bool):
        self.cfg["practice_autonext"] = on
        self.autonext_s.setEnabled(on)
        self.settings_changed.emit()

    def _autonext_s_changed(self, v: float):
        self.cfg["practice_autonext_s"] = v
        self.settings_changed.emit()

    def _auto_next(self, combo_id: int):
        """播放清單：練完換到目前清單的下一個（頭尾相接）。"""
        m = self.matcher
        if not m or not m.finished or self.current_id != combo_id or not self.autonext.isChecked():
            return
        if self.list.count() <= 1:
            self._restart(save=False)
            return
        self.select_offset(1)

    def practice_tick(self, now: float):
        if self.recorder:
            self.recorder.tick(now)
            if self.recorder.finished:
                text = self.recorder.text()
                self.recorder = None
                done, self._record_done = self._record_done, None
                if done:
                    done(text)
                return
                self.row_editor.set_recorded(text)
            return
        m = self.matcher
        if not m:
            return
        for kev in self.resolver.flush(now):
            self._practice_key(kev)
        self._game_tick(now)
        if 0 < m.done < m.total and now - m.last_input_t > self.reset_s.value():
            self._restart(save=True)
        banner = None
        if self._banner:
            banner = {"text": self._banner[0], "color": self._banner[1],
                      "left": round(self._banner[2] - now, 2)}
        elif self._pending_t and now - self._pending_t > PENDING_SHOW_S:
            banner = {"text": "判定中…", "color": "muted", "left": 1.0}
        state = {
            "done": m.done, "total": m.total, "partial": m.partial(now),
            "gaps": m.gaps(), "finished": m.finished,
            # 連段完成後畫面中間顯示「Success」1.5 秒（大於 0 時是還剩幾秒）
            "success": round(max(0.0, SUCCESS_S - (now - self.finish_t)), 2) if m.finished and self.finish_t else 0.0,
            "error": m.settings["practice_strict"] and now - m.error_t < 0.35,
            "banner": banner,                # 遊戲判定：連段斷了／判定中…
        }
        # 主視窗縮到系統匣時，浮動視窗一樣要更新
        for v in self.mirrors:
            if v.isVisible():
                v.set_practice(state)
        if not self.isVisible():
            return
        self.view.set_practice(state)
        row = self.view.practice_row(state)
        if row != self._last_row:
            # 像歌詞字幕一樣：目前這一列平順地捲到正中間
            self._last_row = row
            top, bottom = self.view.row_span(row)
            bar = self.scroll.verticalScrollBar()
            target = int((top + bottom) / 2 - self.scroll.viewport().height() / 2)
            target = max(bar.minimum(), min(target, bar.maximum()))
            self._scroll_anim.stop()
            self._scroll_anim.setStartValue(bar.value())
            self._scroll_anim.setEndValue(target)
            self._scroll_anim.start()

    def _refill_filter(self):
        keep = self.list_filter.currentData() or ""
        tags = self.store.tags(self.character) if self.character else []
        self.list_filter.blockSignals(True)
        self.list_filter.clear()
        self.list_filter.addItem("全部連段", "")
        for t in tags:
            self.list_filter.addItem(t, t)
        self.list_filter.setCurrentIndex(max(0, self.list_filter.findData(keep)))
        self.list_filter.blockSignals(False)

    def _filter_changed(self):
        self._save_now()
        self._reload_list(self.current_id)

    def _reload_list(self, select_id: int = 0):
        self._refill_filter()
        only = self.list_filter.currentData() or ""
        self._loading = True
        bar = self.list.verticalScrollBar()
        keep = bar.value()                     # 重新整理後捲回原本的位置，不要跳回最上面
        self.list.clear()
        target = None
        for row in self.store.list(self.character):
            if only and only not in row["tags"]:
                continue
            item = QListWidgetItem(row["name"] or "（沒有名稱）")
            item.setData(Qt.UserRole, row["id"])
            item.setData(CHECK_ROLE, row["id"] in self._checked)
            self.list.addItem(item)
            if row["id"] == select_id:
                target = item
        self._loading = False
        if target is None and self.list.count():
            target = self.list.item(0)
        self.list.doItemsLayout()              # 先排好，捲軸範圍才對
        bar.setValue(keep)
        if target:
            self.list.setCurrentItem(target)
            self.list.scrollToItem(target, QListWidget.EnsureVisible)
        else:
            self._load(0)
        self._checked &= {self.list.item(i).data(Qt.UserRole) for i in range(self.list.count())}
        self._update_sel_bar()

    def _list_reordered(self, ids: list[int]):
        """拖動排完順序：存起來（有篩選時只調整看得到的這幾個）。"""
        self.store.reorder(self.character, ids)

    def reload(self, select_id: int = 0):
        """匯入後重新整理清單。"""
        self._save_now()
        self._reload_list(select_id or self.current_id)

    def select_combo(self, combo_id: int):
        for i in range(self.list.count()):
            if self.list.item(i).data(Qt.UserRole) == combo_id:
                self.list.setCurrentRow(i)
                return

    def _on_select(self, item, _prev=None):
        if self._loading:
            return
        self._save_now()
        self._save_attempt()
        self._load(item.data(Qt.UserRole) if item else 0)

    def _show_editor(self, show: bool):
        self.editor.setVisible(show)
        self.empty_hint.setVisible(not show)

    def _load(self, combo_id: int):
        self.current_id = combo_id
        row = self.store.get(combo_id) if combo_id else None
        self._show_editor(bool(row))
        self.combo_selected.emit(combo_id if row else 0)
        if not row:
            self.current_id = 0
            for v in self.mirrors:
                v.set_combo(parse(""), [], "還沒有選連段")
                v.set_stats("", "", "")
            return
        self._loading = True
        self.name.setText(row["name"])
        self.notation.setPlainText(row["notation"])
        self.notes.setPlainText(row["notes"])
        self.damage.setValue(row["damage"] or 0)
        self.drive_manual.setChecked(row["drive_override"] is not None)
        self.drive.setValue(row["drive_override"] or 0)
        self.sa_manual.setChecked(row["sa_override"] is not None)
        self.sa.setValue(row["sa_override"] or 0)
        self._tags = list(row["tags"])
        self._render_tags()
        self.video_url.setText(row.get("video_url") or "")
        self.video_t.setText(video.format_time(row.get("video_t") or 0))
        self._update_video_state()
        self._loading = False
        self._close_row_editor()
        self._override_toggled()
        if normalize_simul(self.notation.toPlainText()) != self.notation.toPlainText():
            self._normalize_notation()        # 舊連段裡的 HPHK 也改成 DI，並存檔
        self._refresh_preview()
        self._update_practice_status()

    # ---- 動作 ----
    def new_combo(self, notation: str = "") -> int:
        self._save_now()
        count = len(self.store.list(self.character)) + 1
        new_id = self.store.create(self.character, f"連段 {count}", notation)
        only = self.list_filter.currentData() or ""
        if only:                               # 正在看某個清單：新連段也放進這個清單
            self.store.update(new_id, tags=[only])
        self._reload_list(new_id)
        self.name.setFocus()
        self.name.selectAll()
        return new_id

    def _duplicate(self, combo_id: int = 0):
        combo_id = combo_id or self.current_id
        if not combo_id:
            return
        self._save_now()
        new_id = self.store.duplicate(combo_id)
        if new_id:
            self._reload_list(new_id)

    def _delete_ask(self, combo_id: int):
        row = self.store.get(combo_id)
        if not row:
            return
        if confirm(self.window(), "刪除連段", f"要刪除連段「{row['name'] or '沒有名稱'}」嗎？\n刪除後沒辦法救回。",
                   "刪除"):
            self._delete_ids([combo_id])

    def _delete_ids(self, ids: list[int]):
        if not ids:
            return
        row = self.list.currentRow()
        self._save_now()
        self._save_timer.stop()
        for cid in ids:
            self.store.delete(cid)
            self._checked.discard(cid)
        if self.current_id in ids:
            self.current_id = 0
        self._reload_list(self.current_id)
        if not self.current_id and self.list.count():
            self.list.setCurrentRow(min(max(row, 0), self.list.count() - 1))

    # ---- 選取多個 ----
    def _select_mode(self, on: bool):
        self._save_now()
        self._checked.clear()
        self.select_btn.setText("完成" if on else "選取")
        self.add_btn.setEnabled(not on)
        self.list.set_select_mode(on)
        for i in range(self.list.count()):
            self.list.item(i).setData(CHECK_ROLE, False)
        self.sel_bar.setVisible(on)
        self._update_sel_bar()

    def _toggle_checked(self, item):
        cid = item.data(Qt.UserRole)
        on = cid not in self._checked
        (self._checked.add if on else self._checked.discard)(cid)
        item.setData(CHECK_ROLE, on)
        self._update_sel_bar()

    def _update_sel_bar(self):
        n = len(self._checked)
        self.sel_count.setText(f"已選 {n} 個連段" if n else "點連段來選取，可以選很多個")
        for b in (self.sel_add, self.sel_remove, self.sel_del):
            b.setEnabled(bool(n))

    def _checked_rows(self) -> list[dict]:
        rows = [self.store.get(cid) for cid in self._checked]
        return [r for r in rows if r]

    def _sel_add_menu(self):
        menu = QMenu(self)
        for t in self.store.tags(self.character):
            menu.addAction(t, lambda t=t: self._sel_add(t))
        if not menu.isEmpty():
            menu.addSeparator()
        menu.addAction("新清單…", self._sel_add_new)
        menu.exec(self.sel_add.mapToGlobal(QPoint(0, self.sel_add.height())))

    def _sel_add_new(self):
        name, ok = QInputDialog.getText(self, "新清單", "清單名稱：")
        name = (name or "").strip()
        if ok and name:
            self._sel_add(name)

    def _sel_add(self, tag: str):
        self._save_now()
        for row in self._checked_rows():
            if tag not in row["tags"]:
                self.store.update(row["id"], tags=list(row["tags"]) + [tag])
        self._after_tags()

    def _sel_remove_menu(self):
        tags = sorted({t for r in self._checked_rows() for t in r["tags"]})
        menu = QMenu(self)
        for t in tags:
            menu.addAction(t, lambda t=t: self._sel_remove(t))
        if menu.isEmpty():
            menu.addAction("選到的連段都不在任何清單裡").setEnabled(False)
        menu.exec(self.sel_remove.mapToGlobal(QPoint(0, self.sel_remove.height())))

    def _sel_remove(self, tag: str):
        rows = [r for r in self._checked_rows() if tag in r["tags"]]
        if not rows:
            return
        if not confirm(self.window(), "從清單拿掉", f"要把 {len(rows)} 個連段從清單「{tag}」拿掉嗎？\n連段本身不會被刪除，之後也可以再加回來。",
                       "拿掉"):
            return
        self._save_now()
        for row in rows:
            self.store.update(row["id"], tags=[t for t in row["tags"] if t != tag])
        self._after_tags()

    def _after_tags(self):
        if self.current_id:
            row = self.store.get(self.current_id)
            if row:
                self._tags = list(row["tags"])
                self._render_tags()
        self._reload_list(self.current_id)

    def _sel_delete(self):
        ids = list(self._checked)
        if not ids:
            return
        if confirm(self.window(), "刪除連段", f"要刪除選到的 {len(ids)} 個連段嗎？\n刪除後沒辦法救回。", "刪除"):
            self._delete_ids(ids)

    def append_notation(self, text: str) -> str:
        """指令表點「加入目前連段」時呼叫。沒有選連段就新增一個。回傳連段名稱。"""
        if not self.current_id:
            self.new_combo(text)
            return self.name.text()
        current = self.notation.toPlainText().rstrip()
        self.notation.setPlainText(f"{current} > {text}" if current else text)
        self._save_now()
        return self.name.text()

    def _from_image(self):
        from ui.image_dialog import ImageDialog
        self._save_now()
        dlg = ImageDialog(self.cfg, self.movelist, self.style.currentData(), bool(self.current_id), self)
        accepted = dlg.exec() == ImageDialog.Accepted
        self.settings_changed.emit()          # 記住讀圖方式
        if not accepted or not dlg.result_text:
            return
        if dlg.mode == "append" and self.current_id:
            self.append_notation(dlg.result_text)
        else:
            self.new_combo(dlg.result_text)

    def _show_help(self):
        """用程式自己的視窗顯示記法說明（程式的字體、沒有 Windows 提示音）。"""
        dlg = QDialog(self)
        dlg.setWindowTitle("記法說明")
        box = QVBoxLayout(dlg)
        box.setContentsMargins(22, 16, 22, 16)
        box.addWidget(theme.GlowTitle("連段記法", 13))
        text = QLabel(HELP_TEXT.split("\n", 2)[-1].strip())
        text.setFont(theme.font(10.5))
        text.setTextInteractionFlags(Qt.TextSelectableByMouse)
        box.addWidget(text)
        row = QHBoxLayout()
        row.addStretch(1)
        ok = QPushButton("知道了")
        ok.setDefault(True)
        ok.clicked.connect(dlg.accept)
        row.addWidget(ok)
        box.addLayout(row)
        dlg.exec()

    def set_numpad_visible(self, on: bool):
        self.numpad.setVisible(on)

    # ---- 單列編輯 ----
    def _raw_rows(self) -> tuple[list[str], list[int]]:
        """原始文字的每一組，以及畫面上第 i 列對應到第幾組（看不懂的組不會變成一列）。"""
        raw = _split_groups(self.notation.toPlainText())
        index = [k for k, r in enumerate(raw) if parse(r).groups]
        return raw, index

    def _set_rows(self, raw: list[str]):
        self._row_editing = True
        self.notation.setPlainText(" > ".join(r for r in raw if r.strip()))
        self._row_editing = False

    def _set_pending(self, on: bool):
        if self.view.pending_new != on:
            self.view.pending_new = on
            self.view.setMinimumHeight(self.view.sizeHint().height())
            self.view.updateGeometry()
            self.view.update()

    def _add_action(self):
        """按了「＋ 新增動作」：最後面多一格虛線，右邊開編輯區，直接開始錄。"""
        _raw, index = self._raw_rows()
        row = len(index)
        self._set_pending(True)
        self.view.set_selected(-1)
        self.row_editor.open_row(row, "")
        self.row_editor.title.setText(f"新增第 {row + 1} 列")
        self.row_editor.setVisible(True)
        for wdg in self._lower:
            wdg.setVisible(False)
        top, bottom = self.view.row_span(row)
        self.scroll.ensureVisible(0, bottom + 60, 0, 60)
        if self.row_editor.record_btn.isEnabled():
            self.row_editor.record_btn.setChecked(True)

    def _row_moved(self, src: int, dst: int):
        """拖動排完：第 src 列搬到第 dst 列。"""
        raw, index = self._raw_rows()
        if not (0 <= src < len(index) and 0 <= dst < len(index)):
            return
        rows = [raw[k] for k in index]
        rows.insert(dst, rows.pop(src))
        for k, r in zip(index, rows):
            raw[k] = r
        self._set_rows(raw)
        if self.row_editor.isVisible() and self.row_editor.mode == "row":
            cur = self.row_editor.row
            if cur == src:
                cur = dst
            elif src < cur <= dst:
                cur -= 1
            elif dst <= cur < src:
                cur += 1
            self.row_editor.row = cur
            self.row_editor.title.setText(f"編輯第 {cur + 1} 列")
            self.view.set_selected(cur)

    def _edit_row(self, row: int):
        raw, index = self._raw_rows()
        if not (0 <= row < len(index)):
            return
        self._set_pending(False)
        self.view.set_selected(row)
        self.row_editor.open_row(row, raw[index[row]])
        self.row_editor.setVisible(True)
        for wdg in self._lower:
            wdg.setVisible(False)
        top, bottom = self.view.row_span(row)
        self.scroll.ensureVisible(0, (top + bottom) // 2, 0, 60)

    def _row_text_changed(self, text: str):
        row = self.row_editor.row
        raw, index = self._raw_rows()
        new = self.view.pending_new and row == len(index)
        if not (0 <= row < len(index)) and not new:
            return
        if not text.strip() or not parse(text).groups:
            self.errors.setText("這一列目前看不懂，打完整後畫面才會更新。")
            self.errors.setVisible(bool(text.strip()))
            return
        # 一列裡不可以再分組，> 和 , 改成步驟分隔
        for sep in ">,，＞【】":
            text = text.replace(sep, ".")
        if new:
            raw.append(text.strip())
            self._set_pending(False)
            self.row_editor.title.setText(f"編輯第 {row + 1} 列")
        else:
            raw[index[row]] = text.strip()
        self._set_rows(raw)
        self.view.set_selected(row)

    def _row_insert(self):
        row = self.row_editor.row
        raw, index = self._raw_rows()
        if self.view.pending_new:
            return
        pos = index[row] + 1 if 0 <= row < len(index) else len(raw)
        raw.insert(pos, "LP")
        self._set_rows(raw)
        self._edit_row(row + 1)

    def _row_delete(self):
        row = self.row_editor.row
        raw, index = self._raw_rows()
        if not (0 <= row < len(index)):
            self._close_row_editor()          # 還沒輸入的新動作：直接收掉
            return
        raw.pop(index[row])
        self._set_rows(raw)
        self._close_row_editor()

    def _close_row_editor(self):
        self.row_editor.stop_recording()
        self.cancel_recording()
        self.row_editor.hide()
        self._set_pending(False)
        self.view.set_selected(-1)
        for wdg in self._lower:
            wdg.setVisible(True)

    # ---- 用控制器錄（列編輯區、自訂按鈕共用） ----
    def start_recording(self, on_done):
        """開始錄；停手 2 秒自動結束，結果用 on_done(記法) 交回。"""
        self.recorder = Recorder(self.cfg["layout"], self.cfg.get("combo_window_ms", 12),
                                 self.cfg.get("trial_facing_right", True))
        self._record_done = on_done

    def stop_recording(self):
        """提早結束：一樣把目前錄到的交回。"""
        if not self.recorder:
            return
        self.recorder.stop()
        text = self.recorder.text()
        self.recorder = None
        done, self._record_done = self._record_done, None
        if done:
            done(text)

    def cancel_recording(self):
        self.recorder = None
        self._record_done = None

    def _record_toggled(self, on: bool):
        if on:
            self.start_recording(self.row_editor.set_recorded)
        else:
            self.stop_recording()

    def eventFilter(self, obj, e):
        if (obj is self.notation and e.type() == QEvent.KeyPress
                and e.key() in (Qt.Key_Return, Qt.Key_Enter) and not e.modifiers() & Qt.ShiftModifier):
            self.notation_toggle.setChecked(False)
            return True
        return super().eventFilter(obj, e)

    def _notation_toggled(self, on: bool):
        self.notation_toggle.setText("▾ 整段記法（點一下收起）" if on else "▸ 整段記法（點一下展開）")
        self._update_notation_visible()

    def _update_notation_visible(self):
        self.notation.setVisible(self.notation_toggle.isChecked())

    def _normalize_notation(self) -> bool:
        """HPHK → DI、MPMK → DP：直接改寫在記法裡。有改寫就回傳 True。"""
        text = self.notation.toPlainText()
        new = normalize_simul(text)
        if new == text:
            return False
        cur = self.notation.textCursor()
        pos = cur.position() - (len(text) - len(new))
        self.notation.setPlainText(new)
        cur = self.notation.textCursor()
        cur.setPosition(max(0, min(pos, len(new))))
        self.notation.setTextCursor(cur)
        return True

    def _override_toggled(self):
        self.drive.setEnabled(self.drive_manual.isChecked())
        self.sa.setEnabled(self.sa_manual.isChecked())
        self._changed()

    def _display_changed(self):
        self.cfg["trial_facing_right"] = not self.facing.isChecked()
        self.cfg["trial_button_style"] = self.style.currentData()
        for v in self._views():
            v.set_facing(self.cfg["trial_facing_right"])
        self.set_layout_keys()
        self.settings_changed.emit()
        if self.matcher:
            self._new_matcher()

    def _changed(self):
        if self._loading or not self.current_id:
            return
        if self.sender() is self.notation and self._normalize_notation():
            return                            # 改寫後會再觸發一次 _changed
        if not self._row_editing and self.row_editor.isVisible() and self.sender() is self.notation:
            self._close_row_editor()          # 在上面的大框改了，列號可能已經不對
        self._refresh_preview()
        self._save_timer.start()

    def _refresh_preview(self):
        if not self.current_id:
            return
        if self.matcher and self.notation.toPlainText() != self._matcher_text:
            self._new_matcher()           # 練習中改了連段內容才重來；改名稱、筆記不影響練習
        combo = parse(self.notation.toPlainText())
        names = [group_move_name(g, self.movelist) for g in combo.groups]
        self.errors.setText("　".join(dict.fromkeys(combo.errors)))
        self.errors.setVisible(bool(combo.errors))
        cost = compute_cost(combo, self.movelist)
        self.drive_auto.setText(f"自動 {cost.drive:g}")
        self.sa_auto.setText(f"自動 {cost.sa}")
        # 一樣的項目合併成「×次數」，很長的連段才不會塞滿畫面
        counts: dict[str, int] = {}
        for d in cost.details:
            counts[d] = counts.get(d, 0) + 1
        parts = [d if n == 1 else f"{d} ×{n}" for d, n in counts.items()]
        text = "計算方式：" + "、".join(parts) if parts else "這個連段沒有用到動力槽或 SA 槽。"
        if cost.drive > 6 or cost.sa > 3:
            text += "（超過一條槽的上限：動力槽 6 格、SA 槽 3 格，請確認記法）"
        self.cost_detail.setText(text)
        drive = self.drive.value() if self.drive_manual.isChecked() else cost.drive
        sa = self.sa.value() if self.sa_manual.isChecked() else cost.sa
        damage = f"{self.damage.value():,}" if self.damage.value() else ""
        for v in self._views():
            v.set_facing(self.cfg.get("trial_facing_right", True))
            v.set_button_style(self.style.currentData(), self.cfg["layout"])
            v.set_combo(combo, names, self.name.text())
            v.set_stats(damage, f"{drive:g}", str(sa))

    # ---- 標籤／清單 ----
    def _render_tags(self):
        flow = self.tag_holder.flow
        while flow.count():
            it = flow.takeAt(0)
            if it.widget():
                it.widget().hide()            # 先藏起來，不然刪掉前還會畫一次
                it.widget().deleteLater()
        if not self._tags:
            hint = QLabel("還沒有標籤")
            hint.setObjectName("muted")
            flow.addWidget(hint)
        for t in self._tags:
            b = QPushButton(f"{t}  ✕")
            b.setToolTip(f"把「{t}」從這個連段拿掉")
            b.setFocusPolicy(Qt.NoFocus)
            b.clicked.connect(lambda _=False, t=t: self._remove_tag(t))
            flow.addWidget(b)
        self.tag_holder.updateGeometry()

    def _set_combo_tags(self, combo_id: int, tags: list[str]):
        self.store.update(combo_id, tags=tags)
        if combo_id == self.current_id:
            self._tags = self.store.get(combo_id)["tags"]
            self._render_tags()
        self._reload_list(self.current_id)

    def _add_tag_from_input(self):
        t = self.tag_input.text().strip()
        if not t or not self.current_id:
            return
        self.tag_input.clear()
        if t not in self._tags:
            self._save_now()
            self._set_combo_tags(self.current_id, self._tags + [t])

    def _remove_tag(self, tag: str):
        if not self.current_id:
            return
        if confirm(self.window(), "拿掉標籤", f"要把「{tag}」從這個連段拿掉嗎？\n連段本身不會被刪除，之後也可以再加回來。",
                   "拿掉"):
            self._save_now()
            self._set_combo_tags(self.current_id, [x for x in self._tags if x != tag])

    def _list_menu(self, pos):
        item = self.list.itemAt(pos)
        if not item or self.list.select_mode:
            return
        cid = item.data(Qt.UserRole)
        row = self.store.get(cid)
        if not row:
            return
        self._save_now()
        menu = QMenu(self)
        menu.addAction("複製", lambda: self._duplicate(cid))
        menu.addAction("刪除…", lambda: self._delete_ask(cid))
        menu.addSeparator()
        sub = menu.addMenu("加到清單")
        for t in self.store.tags(self.character):
            act = sub.addAction(t)
            act.setCheckable(True)
            act.setChecked(t in row["tags"])
            act.toggled.connect(lambda on, t=t: self._toggle_list(cid, t, on))
        if not sub.isEmpty():
            sub.addSeparator()
        sub.addAction("新清單…", lambda: self._new_list(cid))
        menu.exec(self.list.viewport().mapToGlobal(pos))

    def _toggle_list(self, combo_id: int, tag: str, on: bool):
        row = self.store.get(combo_id)
        if not row:
            return
        tags = list(row["tags"])
        if on and tag not in tags:
            tags.append(tag)
        elif not on and tag in tags:
            if not confirm(self.window(), "移出清單", f"要把「{row['name']}」移出清單「{tag}」嗎？\n連段本身不會被刪除。",
                           "移出"):
                return
            tags.remove(tag)
        self._set_combo_tags(combo_id, tags)

    def _new_list(self, combo_id: int):
        name, ok = QInputDialog.getText(self, "新清單", "清單名稱：")
        name = (name or "").strip()
        if ok and name:
            self._toggle_list(combo_id, name, True)

    # ---- 參考影片 ----
    def _video_full_url(self) -> str:
        url = self.video_url.text().strip()
        if url and "://" not in url and "." in url.split("/")[0]:
            url = "https://" + url            # 少打 https:// 也可以
        return url

    def _video_url_changed(self):
        if not self._loading:
            secs = video.time_in_url(self.video_url.text().strip())
            if secs:                           # 網址裡有時間：填進開始時間
                self.video_t.setText(video.format_time(secs))
        self._video_changed()

    def _video_changed(self):
        self._update_video_state()
        if not self._loading and self.current_id:
            self._save_timer.start()

    def _update_video_state(self):
        url = self._video_full_url()
        secs = video.parse_time(self.video_t.text())
        self.video_btn.setEnabled(video.is_web_url(url) and secs is not None)
        if url and not video.is_web_url(url):
            self.video_hint.setText("網址看不懂，請貼完整的網址（https:// 開頭）。")
        elif secs is None:
            self.video_hint.setText("開始時間看不懂，請寫成 1:23 或 83（秒）。")
        else:
            self.video_hint.setText("貼上影片網址；開始時間寫成 1:23 或 83（秒）。"
                                    "用 YouTube「分享 → 開始時間」複製的網址，時間會自動填好。")

    def _open_video(self):
        url = self._video_full_url()
        secs = video.parse_time(self.video_t.text())
        if not video.is_web_url(url) or secs is None:
            return
        QDesktopServices.openUrl(QUrl(video.with_time(url, secs)))

    def _save_now(self):
        self._save_timer.stop()
        if not self.current_id or self._loading:
            return
        self.store.update(
            self.current_id,
            name=self.name.text().strip(),
            notation=self.notation.toPlainText(),
            notes=self.notes.toPlainText(),
            damage=self.damage.value() or None,
            drive_override=self.drive.value() if self.drive_manual.isChecked() else None,
            sa_override=self.sa.value() if self.sa_manual.isChecked() else None,
            video_url=self.video_url.text().strip(),
            video_t=video.parse_time(self.video_t.text()) or 0,
        )
        item = self.list.currentItem()
        if item and item.data(Qt.UserRole) == self.current_id:
            item.setText(self.name.text().strip() or "（沒有名稱）")
