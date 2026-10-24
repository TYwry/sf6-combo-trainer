"""主視窗：上方選角色，下面分頁「連段庫／指令表／控制器」。控制器讀取器由這裡統一管理。"""
from __future__ import annotations

import queue
import time

from PySide6.QtCore import QRect, Qt, QTimer
from PySide6.QtGui import QGuiApplication, QIcon
from PySide6.QtWidgets import (QApplication, QComboBox, QHBoxLayout, QLabel, QMainWindow, QMessageBox,
                               QPushButton, QStackedWidget, QVBoxLayout)

from app_info import APP_DISPLAY_NAME, APP_VERSION, ASSETS_DIR
from core.xinput_reader import XInputReader
from data import movelist as ml
from data.config import save_config
from core import autostart, hotkeys, shortcut
from data.config import DEFAULTS, HOTKEY_ACTIONS
from data.storage import ComboStore
from ui import theme
from ui.combo_page import ComboPage
from ui.controller_test import ControllerPage
from ui.movelist_page import MovelistPage
from ui.nav_bar import NavBar
from ui.overlay import OverlayWindow
from ui.tray import Tray

PAD_DIRS = {8: "UP", 2: "DOWN", 4: "LEFT", 6: "RIGHT"}


# 改了要重開程式才會套用的設定：(設定名稱, 給使用者看的說法)
RESTART_KEYS = (("ui_font", "字體"), ("ui_theme", "主題"))


class MainWindow(QMainWindow):
    def __init__(self, cfg: dict):
        super().__init__()
        self.cfg = cfg
        self.setWindowTitle(f"{APP_DISPLAY_NAME}　v{APP_VERSION}")
        icon = ASSETS_DIR / "app_ink.ico"
        if icon.exists():
            self.setWindowIcon(QIcon(str(icon)))

        self.reader = XInputReader(cfg)
        self._foreground = True
        self.reader.set_foreground_check(lambda: self._foreground)
        self.store = ComboStore()
        self.movelists = ml.load_all()

        self._quitting = False
        self.restart_requested = False     # 結束後由 main.py 重新開啟程式（換字體、主題時）
        self._back_held = False
        self._build()
        self._restore_geometry()
        self.apply_theme()
        self._build_background()
        self._build_game()

        self.timer = QTimer(self)
        self.timer.timeout.connect(self._tick)
        self.timer.start(16)
        self.reader.start()

        # 圖片資料夾流程：放進 imports 的匯入檔自動加入連段庫
        from data.auto_import import AutoImporter
        self.auto_importer = AutoImporter(self.store, self._char_names())
        self.auto_timer = QTimer(self)
        self.auto_timer.timeout.connect(self._auto_import)
        self.auto_timer.start(5000)
        QTimer.singleShot(1500, self._auto_import)

    def _build(self):
        root = theme.Backdrop()
        outer = QVBoxLayout(root)
        outer.setContentsMargins(0, 10, 0, 0)
        outer.setSpacing(8)

        top = QHBoxLayout()
        top.setContentsMargins(22, 0, 22, 0)
        title = theme.GlowTitle(APP_DISPLAY_NAME, 18, line=True)
        title.setMinimumWidth(240)
        top.addWidget(title)
        top.addSpacing(16)
        role = QLabel("角色")
        role.setFont(theme.font(10.5, True))
        top.addWidget(role)
        self.character = QComboBox()
        self.character.setAccessibleName("角色")
        self.character.setMinimumWidth(160)
        for cid, data in self.movelists.items():
            self.character.addItem(f"{data.get('name', cid)}　{data.get('name_en', '')}", cid)
        top.addWidget(self.character)
        top.addStretch(1)
        self.overlay_btn = QPushButton("浮動視窗")
        self.overlay_btn.setCheckable(True)
        self.overlay_btn.clicked.connect(lambda: self.run_action("overlay"))
        top.addWidget(self.overlay_btn)
        self.settings_btn = QPushButton("設定")
        self.settings_btn.clicked.connect(self.open_settings)
        top.addWidget(self.settings_btn)
        outer.addLayout(top)

        self.nav = NavBar()
        self.pages = QStackedWidget()
        self.combo_page = ComboPage(self.cfg, self.store)
        self.move_page = MovelistPage(self.cfg, self.combo_page)
        self.controller_page = ControllerPage(self.cfg, self.reader)
        for page, text in ((self.combo_page, "連段庫"), (self.move_page, "指令表"),
                           (self.controller_page, "控制器")):
            self.pages.addWidget(page)
            self.nav.addTab(text)
        self.nav.currentChanged.connect(self.pages.setCurrentIndex)
        outer.addWidget(self.nav)
        outer.addWidget(self.pages, 1)
        self.setCentralWidget(root)
        self.tabs = self.nav

        self.combo_page.settings_changed.connect(self._save)
        self.controller_page.config_changed.connect(self._save)
        self.controller_page.layout_changed.connect(self.combo_page.set_layout_keys)
        self.move_page.add_requested.connect(self._add_move)
        self.combo_page.export_requested.connect(self._export)
        self.combo_page.import_requested.connect(self._import)
        self.combo_page.site_requested.connect(self._site_library)
        self.move_page.movelist_changed.connect(self._movelist_changed)
        self.tabs.currentChanged.connect(self._tab_changed)
        self.character.currentIndexChanged.connect(self._character_changed)

        idx = self.character.findData(self.cfg.get("last_character", "ryu"))
        self.character.setCurrentIndex(max(0, idx))
        self._character_changed()
        self.tabs.setCurrentIndex(min(max(0, int(self.cfg.get("last_tab", 0) or 0)), 2))

    # ---- 遊戲 mod ----
    def _build_game(self):
        from core import game_bridge
        self.game_feed = game_bridge.GameFeed()
        self.combo_page.game_feed = self.game_feed
        self.apply_game_dir()
        self._game_was_running = False
        self.game_timer = QTimer(self)
        self.game_timer.timeout.connect(self._watch_game)
        self.game_timer.start(3000)
        QTimer.singleShot(2500, self._watch_game)

    def apply_game_dir(self):
        """遊戲資料夾沒設定或不對時，自動從 Steam 找一次。"""
        from core import game_bridge
        d = self.cfg.get("sf6_game_dir", "")
        if not game_bridge.is_game_dir(d):
            found = game_bridge.find_game_dir()
            if found:
                self.cfg["sf6_game_dir"] = d = str(found)
                self._save()
        self.game_feed.set_game_dir(d if game_bridge.is_game_dir(d) else None)

    def _watch_game(self):
        """遊戲剛開起來、mod 又是開著的：跳通知提醒對戰前要關 mod。"""
        from core import game_bridge
        running = game_bridge.game_running()
        started = running and not self._game_was_running
        self._game_was_running = running
        d = self.cfg.get("sf6_game_dir", "")
        if not started or not game_bridge.is_game_dir(d):
            return
        if self.cfg.get("mod_start_notice", True) and game_bridge.mod_state(d) == "on":
            text = ("遊戲啟動了，mod 是開著的。\n要線上對戰前，請先關遊戲，"
                    "再到練習器「設定 → 遊戲 mod」把 mod 關掉。")
            if Tray.available():
                self.tray.notify(text)
            else:
                QMessageBox.information(self, APP_DISPLAY_NAME, text)
        if self.cfg.get("game_check_enabled", True) and game_bridge.script_state(d) != "ok":
            self.tray.notify("遊戲內腳本還沒安裝或需要更新，遊戲判定暫時用不到。\n"
                             "請到「設定 → 遊戲 mod」按「安裝遊戲內腳本」，再重開遊戲。")

    # ---- 浮動視窗、系統匣、快捷鍵 ----
    def _build_background(self):
        self.overlay = OverlayWindow(self.cfg)
        self.combo_page.add_mirror(self.overlay.view)
        self.overlay.settings_changed.connect(self._save)
        self.overlay.settings_changed.connect(self._sync_controls)
        self.overlay.visibility_changed.connect(self._overlay_shown)
        self.overlay.practice_requested.connect(self._set_practice)
        self.combo_page.practice_btn.toggled.connect(lambda _on: self._sync_controls())
        self.combo_page.combo_selected.connect(self._combo_selected)

        self.tray = Tray(self)
        self.tray.open_requested.connect(self.show_main)
        self.tray.overlay_toggled.connect(lambda: self.run_action("overlay"))
        self.tray.lock_toggled.connect(lambda: self.run_action("lock"))
        self.tray.practice_toggled.connect(lambda: self.run_action("practice"))
        self.tray.settings_requested.connect(self.open_settings)
        self.tray.quit_requested.connect(self.quit_app)
        if Tray.available():
            self.tray.show()

        self.hotkeys = hotkeys.GlobalHotkeys(QApplication.instance(), self)
        self.hotkeys.triggered.connect(self.run_action)
        self._sync_controls()

    def start_background(self, quiet: bool = False):
        """主視窗顯示後呼叫：註冊快捷鍵、恢復浮動視窗。quiet＝開機自動啟動，提示改用系統匣通知。"""
        if self.cfg.get("overlay_visible"):
            self.overlay.set_shown(True)
        self._register_hotkeys(quiet)
        if self.store.broken_backup:
            from ui.exchange_dialog import notice
            notice(self, "連段庫讀不到", "連段庫的檔案壞掉了，程式已建立一個新的空白連段庫。\n\n"
                   f"原本的檔案沒有刪，改名留在：\n{self.store.broken_backup}\n\n"
                   "如果你之前有匯出過連段，可以用「匯入」拿回來。")
            self.store.broken_backup = None

    def _register_hotkeys(self, quiet: bool = False):
        if not self.cfg.get("hotkeys_enabled", True):
            self.hotkeys.unregister_all()          # 關掉鍵盤快捷鍵：一個都不佔用
            return
        failed = self.hotkeys.register(self.cfg.get("hotkeys") or {})
        if not failed:
            return
        names = dict(HOTKEY_ACTIONS)
        lines = [f"{hotkeys.display(self.cfg['hotkeys'].get(code, ''))}（{names.get(code, code)}）"
                 for code in failed]
        text = ("這些快捷鍵被其他程式佔用了，沒辦法使用：\n" + "\n".join(lines)
                + "\n\n請到「設定」換一組按鍵。")
        if quiet and Tray.available():
            self.tray.notify(text)
        else:
            QMessageBox.warning(self, APP_DISPLAY_NAME, text)

    def _overlay_shown(self, on: bool):
        # 打開浮動視窗就直接進入練習模式
        if on:
            self._set_practice(True)
        self._sync_controls()

    def _combo_selected(self, combo_id: int):
        if combo_id and self.overlay.isVisible():
            QTimer.singleShot(0, lambda: self._set_practice(True))
        self._sync_controls()

    def _set_practice(self, on: bool):
        if self.combo_page.practice_btn.isChecked() != on and (self.combo_page.current_id or not on):
            self.combo_page.practice_btn.setChecked(on)
        self._sync_controls()

    def run_action(self, code: str):
        """快捷鍵、系統匣、按鈕共用的動作。"""
        if code == "overlay":
            self.overlay.toggle_shown()
        elif code == "lock":
            if not self.overlay.isVisible():
                self.overlay.set_shown(True)
            self.overlay.toggle_locked()
        elif code == "practice":
            self.combo_page.toggle_practice()
        elif code == "restart":
            self.combo_page.restart_practice()
        elif code in ("next", "prev"):
            name = self.combo_page.select_offset(1 if code == "next" else -1)
            if name:
                self.statusBar().showMessage(f"換到「{name}」", 3000)
        self._sync_controls()

    def _sync_controls(self):
        practicing = self.combo_page.matcher is not None
        self.overlay_btn.setChecked(self.overlay.isVisible())
        self.tray.sync(self.overlay.isVisible(), self.overlay.locked, practicing,
                       bool(self.combo_page.current_id))

    def show_main(self):
        if self.isMinimized():
            self.setWindowState(self.windowState() & ~Qt.WindowMinimized)
        self.show()
        self.raise_()
        self.activateWindow()

    def open_settings(self):
        from ui.settings_dialog import SettingsDialog
        self.hotkeys.unregister_all()          # 設定時先放開，才錄得到原本的快捷鍵
        dlg = SettingsDialog(self.cfg, self if self.isVisible() else None)
        dlg.shortcut_requested.connect(self._create_shortcuts)
        dlg.export_requested.connect(self._export)
        dlg.import_requested.connect(self._import)
        dlg.game_dir_changed.connect(self._game_dir_changed)
        place_before = (self.cfg.get("window_size", "1920"), self.cfg.get("window_screen", ""))
        accepted = dlg.exec() == SettingsDialog.Accepted
        restart = False
        if accepted:
            values = dlg.values()
            # 要重開程式才會套用的設定：先問要不要重開；取消就維持原本的設定
            need = [label for key, label in RESTART_KEYS
                    if values.get(key) != self.cfg.get(key, DEFAULTS.get(key))]
            if need:
                if self._ask_restart("、".join(need)):
                    restart = True
                else:
                    for key, _label in RESTART_KEYS:
                        values[key] = self.cfg.get(key, DEFAULTS.get(key))
            self.cfg.update(values)
            self.combo_page.set_numpad_visible(bool(self.cfg.get("show_numpad", True)))
            self.combo_page._update_practice_status()
            want = dlg.autostart_wanted()
            if want != autostart.is_enabled():
                err = autostart.set_enabled(want)
                if err:
                    QMessageBox.warning(self, APP_DISPLAY_NAME, err)
            self.overlay.apply_look()
            if (values.get("window_size"), values.get("window_screen")) != place_before:
                self.apply_window_place()
            self._save()
        if restart:
            self.restart_requested = True
            self.quit_app()
            return
        self._register_hotkeys()

    def _game_dir_changed(self, path: str):
        """設定視窗裡換了遊戲資料夾：馬上存起來並改讀新的位置（不用等按儲存）。"""
        self.cfg["sf6_game_dir"] = path
        self._save()
        self.game_feed.set_game_dir(path)

    def _ask_restart(self, what: str) -> bool:
        """詢問要不要立刻重開程式；按「確定」才回傳 True。"""
        box = QMessageBox(self if self.isVisible() else None)
        box.setIcon(QMessageBox.Question)
        box.setWindowTitle(APP_DISPLAY_NAME)
        box.setText(f"{what}要重新開啟程式才會套用。\n\n按「確定」會馬上重新開啟並套用；"
                    f"按「取消」就不改{what}，其他設定照常儲存。")
        ok = box.addButton("確定", QMessageBox.AcceptRole)
        cancel = box.addButton("取消", QMessageBox.RejectRole)
        box.setDefaultButton(ok)
        box.setEscapeButton(cancel)
        box.exec()
        return box.clickedButton() is ok

    def quit_app(self):
        self._quitting = True
        self.close()

    def apply_theme(self):
        self.controller_page.apply_theme()
        self.combo_page.apply_theme()
        self.move_page.apply_theme()

    # ---- 角色、指令表 ----
    def _cid(self) -> str:
        return self.character.currentData() or ""

    def _character_changed(self):
        cid = self._cid()
        self.cfg["last_character"] = cid
        data = self.movelists.get(cid)
        self.combo_page.set_character(cid, data)
        self.move_page.set_character(cid, data)
        self._save()

    def _movelist_changed(self, cid: str, data: dict):
        self.movelists[cid] = data
        if cid == self._cid():
            self.combo_page.set_movelist(data)

    def _add_move(self, text: str, move_name: str):
        combo_name = self.combo_page.append_notation(text)
        self.statusBar().showMessage(f"已把「{move_name}」加到「{combo_name}」", 4000)

    def _tab_changed(self, index: int):
        self.cfg["last_tab"] = index

    # ---- 匯出入、練習紀錄 ----
    def _char_name(self, cid: str) -> str:
        return (self.movelists.get(cid) or {}).get("name", cid)

    def _char_names(self) -> dict[str, str]:
        return {cid: self._char_name(cid) for cid in self.movelists}

    def _export(self):
        from ui.exchange_dialog import ExportDialog
        self.combo_page._save_now()
        ExportDialog(self.cfg, self.store, self._cid(), self.combo_page.current_id, self._char_names(), self).exec()
        self._save()

    def _import(self):
        from data import exchange
        from ui.exchange_dialog import ImportDialog, choose_import_file, notice
        self.combo_page._save_now()
        path = choose_import_file(self, self.cfg)
        self._save()
        if not path:
            return
        try:
            parsed = exchange.read_file(path, self._char_names())
        except exchange.ImportError_ as e:
            notice(self, "沒辦法匯入", str(e))
            return
        if not parsed.combos and not parsed.snippets:
            notice(self, "沒辦法匯入", "\n".join(parsed.problems) or "這個檔案裡沒有連段。")
            return
        if self.combo_page.matcher:
            # 練習中：先把這一次存起來、從頭開始，避免取代後把紀錄記到已經不在的連段上
            self.combo_page.restart_practice()
        dlg = ImportDialog(self.store, parsed, self._char_names(), path, self)
        if dlg.exec() != ImportDialog.Accepted:
            return
        res = dlg.result_info
        # 匯入的連段如果有目前這個角色的，選到第一個
        first = next((i for i in res.new_ids if (self.store.get(i) or {}).get("character") == self._cid()), 0)
        self.combo_page.reload(first)
        self.combo_page.row_editor.rebuild_palette()      # 自訂庫可能多了按鈕
        self.move_page.rebuild_palette()
        notice(self, "匯入完成", dlg.summary())

    def _auto_import(self):
        results = self.auto_importer.check()
        if not results:
            return
        names = self._char_names()
        lines, new_ids = [], []
        for r in results:
            if r.error:
                lines.append(f"{r.file} 沒有匯入：{r.error}")
                continue
            new_ids += r.new_ids
            chars = "、".join(f"{names.get(k, k)} {v} 個" for k, v in r.per_character.items())
            text = f"自動加入 {r.added} 個連段（{chars}）" if r.added else "沒有新的連段"
            if r.skipped:
                text += f"，{r.skipped} 個已經有了略過"
            if r.images_moved:
                text += f"；{r.images_moved} 張圖片已搬到「圖片\\已處理」"
            lines.append(text)
        if new_ids:
            self.combo_page._save_now()
            first = next((i for i in new_ids if (self.store.get(i) or {}).get("character") == self._cid()), 0)
            if self.combo_page.matcher:
                self.combo_page.reload(self.combo_page.current_id)    # 練習中不換連段
            else:
                self.combo_page.reload(first)
        text = "\n".join(lines)
        if Tray.available():
            self.tray.notify(text)
        else:
            from ui.exchange_dialog import notice
            notice(self, "自動匯入", text)

    def _site_library(self):
        from ui.site_library_dialog import SiteLibraryDialog
        self.combo_page._save_now()
        if self.combo_page.matcher:
            self.combo_page.restart_practice()
        dlg = SiteLibraryDialog(self.store, self._cid(), self._char_names(), self.combo_page.view, self)
        dlg.exec()
        if dlg.added_ids:
            first = next((i for i in dlg.added_ids if (self.store.get(i) or {}).get("character") == self._cid()), 0)
            self.combo_page.reload(first)

    # ---- 捷徑 ----
    def ask_shortcut_once(self):
        """第一次開啟時問一次要不要建立捷徑。"""
        if self.cfg.get("asked_desktop_shortcut") or not shortcut.supported():
            return
        self.cfg["asked_desktop_shortcut"] = True
        self._save()
        box = QMessageBox(self)
        box.setWindowTitle(APP_DISPLAY_NAME)
        box.setText("要在桌面和開始功能表建立「SF6 連段練習器」的捷徑嗎？\n之後也可以在「設定」裡建立。")
        yes = box.addButton("建立", QMessageBox.AcceptRole)
        no = box.addButton("先不用", QMessageBox.RejectRole)
        box.setDefaultButton(no)
        box.exec()
        if box.clickedButton() is yes:
            self._create_shortcuts()

    def _create_shortcuts(self):
        if not shortcut.supported():
            QMessageBox.information(self, APP_DISPLAY_NAME, "只有 Windows 可以建立捷徑。")
            return
        made, skipped, errors = shortcut.create_all()
        lines = []
        if made:
            lines.append("已建立：\n" + "\n".join(str(x) for x in made))
        if skipped:
            lines.append("已經有了，沒有更動：\n" + "\n".join(str(x) for x in skipped))
        if errors:
            lines.append("建立失敗：\n" + "\n".join(errors))
        QMessageBox.information(self, APP_DISPLAY_NAME, "\n\n".join(lines) or "沒有建立任何捷徑。")

    # ---- 控制器 ----
    def set_foreground(self, value: bool):
        self._foreground = value

    def _tick(self):
        drained = 0
        pad_on = bool(self.cfg.get("pad_hotkeys_enabled"))
        while drained < 500:
            try:
                ev = self.reader.events.get_nowait()
            except queue.Empty:
                break
            drained += 1
            self.controller_page.handle_event(ev)
            if ev.kind in ("press", "release") and ev.button == "BACK":
                self._back_held = ev.kind == "press"
            elif ev.kind == "disconnect":
                self._back_held = False
            elif pad_on and self._back_held and self._pad_hotkey(ev):
                continue                 # 按住 BACK 時按的鍵不算連段輸入
            self.combo_page.practice_event(ev)
        now = time.perf_counter()
        self.game_feed.poll(now)
        self.controller_page.tick(now)
        self.combo_page.practice_tick(now)

    def _pad_hotkey(self, ev) -> bool:
        """按住 BACK 時的控制器快捷鍵；回傳 True＝這個事件被快捷鍵用掉。"""
        if ev.kind == "press":
            signal = ev.button
        elif ev.kind == "dir":
            signal = PAD_DIRS.get(ev.direction)
            if not signal:
                return True              # 回中立或斜方向：不觸發，也不算輸入
        else:
            return False
        for code, value in (self.cfg.get("pad_hotkeys") or {}).items():
            if value and value == signal:
                self.run_action(code)
                break
        return True

    # ---- 視窗位置與存檔 ----
    def _pick_screen(self):
        """設定選的螢幕；找不到（例如拔掉了）就用主螢幕。"""
        name = self.cfg.get("window_screen") or ""
        for s in QGuiApplication.screens():
            if name and s.name() == name:
                return s
        return QGuiApplication.primaryScreen()

    def _restore_geometry(self):
        """照設定決定開在哪個螢幕、多大：1920×1080、佔滿整個螢幕，或上次關閉時的位置大小。"""
        mode = self.cfg.get("window_size", "1920")
        g = self.cfg.get("window_geometry")
        if mode == "last" and g:
            rect = QRect(*[int(v) for v in g])
            if any(s.availableGeometry().contains(rect.center()) for s in QGuiApplication.screens()):
                self.setGeometry(rect)
                self.start_maximized = bool(self.cfg.get("window_maximized"))
                return
        avail = self._pick_screen().availableGeometry()
        w, h = 1920, 1080
        self.start_maximized = mode == "full" or w > avail.width() or h > avail.height()
        self.resize(min(w, avail.width()), min(h, avail.height()))
        self.move(avail.center() - self.rect().center())

    def apply_window_place(self):
        """設定改了開啟位置：馬上照新設定擺一次。"""
        if self.cfg.get("window_size") == "last":
            return                       # 「上次的大小」＝維持現在的樣子
        self.showNormal()
        self._restore_geometry()
        if self.start_maximized:
            self.showMaximized()

    def _save(self):
        g = self.normalGeometry() if self.isMaximized() else self.geometry()
        self.cfg["window_geometry"] = [g.x(), g.y(), g.width(), g.height()]
        self.cfg["window_maximized"] = self.isMaximized()
        try:
            save_config(self.cfg)
        except OSError:
            pass

    def closeEvent(self, event):
        if not self._quitting and self.cfg.get("close_to_tray", True) and Tray.available():
            # 縮到系統匣：練習、浮動視窗、快捷鍵照常運作
            event.ignore()
            self._save()
            self.hide()
            if not self.cfg.get("tray_hint_shown"):
                self.cfg["tray_hint_shown"] = True
                self._save()
                self.tray.notify("程式還在右下角的系統匣執行。要結束請在小圖示按右鍵選「結束」。")
            return
        self.hotkeys.unregister_all()
        self.overlay.close()
        self.tray.hide()
        self.timer.stop()
        self.reader.stop()
        self.combo_page._save_attempt()
        self.combo_page._save_now()
        self._save()
        self.store.close()
        super().closeEvent(event)
        QApplication.instance().quit()
