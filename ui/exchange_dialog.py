"""匯出、匯入連段的視窗。"""
from __future__ import annotations

import time
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QButtonGroup, QCheckBox, QDialog, QFileDialog, QHBoxLayout, QLabel,
                               QListWidget, QListWidgetItem, QPushButton, QRadioButton, QVBoxLayout)

from data import exchange
from data.storage import ComboStore
from ui import theme
from ui.controller_setup import confirm


def notice(parent, title: str, text: str):
    """用程式自己的視窗顯示訊息（程式的字體、沒有 Windows 提示音）。"""
    dlg = QDialog(parent)
    dlg.setWindowTitle(title)
    box = QVBoxLayout(dlg)
    box.setContentsMargins(22, 16, 22, 16)
    box.setSpacing(10)
    box.addWidget(theme.GlowTitle(title, 13))
    label = QLabel(text)
    label.setWordWrap(True)
    label.setMinimumWidth(380)
    label.setTextInteractionFlags(Qt.TextSelectableByMouse)
    box.addWidget(label)
    row = QHBoxLayout()
    row.addStretch(1)
    ok = QPushButton("知道了")
    ok.setDefault(True)
    ok.clicked.connect(dlg.accept)
    row.addWidget(ok)
    box.addLayout(row)
    dlg.exec()


def _start_dir(cfg: dict) -> str:
    d = cfg.get("exchange_dir") or ""
    if d and Path(d).is_dir():
        return d
    docs = Path.home() / "Documents"
    return str(docs if docs.is_dir() else Path.home())


def _safe_name(text: str) -> str:
    return "".join("_" if ch in '\\/:*?"<>|' else ch for ch in text).strip() or "連段"


class ExportDialog(QDialog):
    def __init__(self, cfg: dict, store: ComboStore, character: str, current_id: int,
                 char_names: dict[str, str], parent=None):
        super().__init__(parent)
        self.cfg, self.store = cfg, store
        self.character, self.current_id, self.char_names = character, current_id, char_names
        self.setWindowTitle("匯出連段")
        box = QVBoxLayout(self)
        box.setContentsMargins(22, 16, 22, 16)
        box.setSpacing(8)
        box.addWidget(theme.GlowTitle("匯出連段", 13))
        box.addWidget(QLabel("要匯出哪些連段？"))

        self.cur_name = (store.get(current_id) or {}).get("name", "") if current_id else ""
        n_char = len(store.list(character))
        n_all = sum(len(store.list(c)) for c in store.characters())
        self.scope_one = QRadioButton(f"目前這個連段（{self.cur_name}）" if current_id else "目前這個連段（沒有選）")
        self.scope_char = QRadioButton(f"{char_names.get(character, character)}的全部連段（{n_char} 個）")
        self.scope_all = QRadioButton(f"所有角色的全部連段（{n_all} 個）")
        self.scope_one.setEnabled(bool(current_id))
        self.scope_char.setEnabled(n_char > 0)
        self.scope_all.setEnabled(n_all > 0)
        group = QButtonGroup(self)
        for b in (self.scope_one, self.scope_char, self.scope_all):
            group.addButton(b)
            box.addWidget(b)
        (self.scope_char if n_char else self.scope_all).setChecked(True)

        box.addSpacing(6)
        self.with_practice = QCheckBox("包含練習紀錄（自己備份時再勾）")
        self.with_snippets = QCheckBox("包含自訂庫（編輯一列時的自訂按鈕）")
        box.addWidget(self.with_practice)
        box.addWidget(self.with_snippets)
        hint = QLabel("不會匯出你的按鍵設定、快捷鍵和視窗位置。")
        hint.setObjectName("muted")
        box.addWidget(hint)

        row = QHBoxLayout()
        row.addStretch(1)
        cancel = QPushButton("取消")
        cancel.clicked.connect(self.reject)
        self.ok = QPushButton("匯出…")
        self.ok.setDefault(True)
        self.ok.setEnabled(n_all > 0)
        self.ok.clicked.connect(self._export)
        row.addWidget(cancel)
        row.addWidget(self.ok)
        box.addLayout(row)
        self.saved_path: Path | None = None

    def _ids_and_label(self) -> tuple[list[int], str]:
        if self.scope_one.isChecked():
            return [self.current_id], f"{self.char_names.get(self.character, self.character)}-{self.cur_name}"
        if self.scope_char.isChecked():
            return ([r["id"] for r in self.store.list(self.character)],
                    self.char_names.get(self.character, self.character))
        ids = [r["id"] for c in self.store.characters() for r in self.store.list(c)]
        return ids, "全部角色"

    def _export(self):
        ids, label = self._ids_and_label()
        name = f"SF6連段-{_safe_name(label)}-{time.strftime('%Y-%m-%d')}.json"
        path, _ = QFileDialog.getSaveFileName(self, "匯出連段", str(Path(_start_dir(self.cfg)) / name),
                                              "連段檔 (*.json)")
        if not path:
            return
        path = Path(path)
        if path.suffix.lower() != ".json":
            path = path.with_name(path.name + ".json")
        try:
            data = exchange.build_export(self.store, ids, self.with_practice.isChecked(),
                                         self.with_snippets.isChecked())
            exchange.write_file(path, data)
        except OSError as e:
            notice(self, "匯出失敗", f"沒辦法存到這個位置：{e.strerror or e}\n請換一個資料夾再試一次。")
            return
        self.cfg["exchange_dir"] = str(path.parent)
        self.saved_path = path
        parts = [f"{len(data['combos'])} 個連段"]
        if self.with_practice.isChecked():
            parts.append(f"{sum(len(c.get('practice', [])) for c in data['combos'])} 筆練習紀錄")
        if self.with_snippets.isChecked():
            parts.append(f"{len(data.get('snippets', []))} 個自訂按鈕")
        notice(self, "匯出完成", f"已匯出{'、'.join(parts)}。\n\n存在：{path}")
        self.accept()


def choose_import_file(parent, cfg: dict) -> Path | None:
    path, _ = QFileDialog.getOpenFileName(parent, "匯入連段", _start_dir(cfg), "連段檔 (*.json)")
    if not path:
        return None
    cfg["exchange_dir"] = str(Path(path).parent)
    return Path(path)


class ImportDialog(QDialog):
    """匯入預覽：先看檔案裡有什麼、勾要哪些，再選加入或取代。"""

    def __init__(self, store: ComboStore, parsed: exchange.Parsed, char_names: dict[str, str],
                 source: Path, parent=None):
        super().__init__(parent)
        self.store, self.parsed, self.char_names = store, parsed, char_names
        self.result_info: exchange.Result | None = None
        self.setWindowTitle("匯入連段")
        self.resize(560, 620)
        box = QVBoxLayout(self)
        box.setContentsMargins(22, 16, 22, 16)
        box.setSpacing(8)
        box.addWidget(theme.GlowTitle("匯入連段", 13))

        src = QLabel(f"檔案：{source.name}" + (f"（{parsed.exported} 匯出）" if parsed.exported else ""))
        src.setObjectName("muted")
        src.setWordWrap(True)
        box.addWidget(src)

        per_char: dict[str, int] = {}
        for c in parsed.combos:
            per_char[c["character"]] = per_char.get(c["character"], 0) + 1
        n_practice = sum(len(c["practice"]) for c in parsed.combos)
        summary = "、".join(f"{char_names.get(k, k)} {v} 個" for k, v in per_char.items()) or "沒有可以匯入的連段"
        lines = [f"連段：{summary}"]
        if n_practice:
            lines.append(f"練習紀錄：{n_practice} 筆（跟著連段一起匯入）")
        if parsed.snippets:
            lines.append(f"自訂按鈕：{len(parsed.snippets)} 個")
        info = QLabel("\n".join(lines))
        info.setWordWrap(True)
        box.addWidget(info)
        if parsed.problems:
            warn = QLabel("\n".join(parsed.problems))
            warn.setWordWrap(True)
            warn.setStyleSheet(f"color: {theme.colors()['warn']};")
            box.addWidget(warn)

        head = QHBoxLayout()
        head.addWidget(QLabel("勾選要匯入的連段"))
        head.addStretch(1)
        all_btn = QPushButton("全選")
        all_btn.clicked.connect(lambda: self._check_all(True))
        none_btn = QPushButton("全不選")
        none_btn.clicked.connect(lambda: self._check_all(False))
        head.addWidget(all_btn)
        head.addWidget(none_btn)
        box.addLayout(head)
        self.list = QListWidget()
        self.list.setAccessibleName("要匯入的連段")
        for i, c in enumerate(parsed.combos):
            text = f"{char_names.get(c['character'], c['character'])}｜{c['name']}"
            if c["practice"]:
                text += f"（{len(c['practice'])} 筆紀錄）"
            item = QListWidgetItem(text)
            item.setFlags(item.flags() | Qt.ItemIsUserCheckable)
            item.setCheckState(Qt.Checked)
            item.setData(Qt.UserRole, i)
            item.setToolTip(c["notation"])
            self.list.addItem(item)
        self.list.itemChanged.connect(lambda _i: self._update_ok())
        box.addWidget(self.list, 1)

        self.with_snippets = QCheckBox(f"一起加入 {len(parsed.snippets)} 個自訂按鈕（一樣的會略過）")
        self.with_snippets.setChecked(bool(parsed.snippets))
        self.with_snippets.setVisible(bool(parsed.snippets))
        self.with_snippets.toggled.connect(lambda _on: self._update_ok())
        box.addWidget(self.with_snippets)

        box.addWidget(QLabel("匯入方式"))
        self.mode_add = QRadioButton("加入：放在現有連段後面，原本的不動；一模一樣的會略過")
        self.mode_replace = QRadioButton("取代：檔案裡有的角色，原本的連段換成檔案裡的（會先自動備份）")
        group = QButtonGroup(self)
        group.addButton(self.mode_add)
        group.addButton(self.mode_replace)
        self.mode_add.setChecked(True)
        box.addWidget(self.mode_add)
        box.addWidget(self.mode_replace)

        row = QHBoxLayout()
        row.addStretch(1)
        cancel = QPushButton("取消")
        cancel.clicked.connect(self.reject)
        self.ok = QPushButton("匯入")
        self.ok.setDefault(True)
        self.ok.clicked.connect(self._import)
        row.addWidget(cancel)
        row.addWidget(self.ok)
        box.addLayout(row)
        self._update_ok()

    def _check_all(self, on: bool):
        for i in range(self.list.count()):
            self.list.item(i).setCheckState(Qt.Checked if on else Qt.Unchecked)

    def _chosen(self) -> list[dict]:
        out = []
        for i in range(self.list.count()):
            item = self.list.item(i)
            if item.checkState() == Qt.Checked:
                out.append(self.parsed.combos[item.data(Qt.UserRole)])
        return out

    def _update_ok(self):
        self.ok.setEnabled(bool(self._chosen()) or (self.with_snippets.isChecked() and bool(self.parsed.snippets)))

    def _import(self):
        combos = self._chosen()
        replace = self.mode_replace.isChecked() and bool(combos)
        if replace:
            chars = sorted({c["character"] for c in combos})
            n_combo, n_practice = self.store.count_for_characters(chars)
            names = "、".join(self.char_names.get(c, c) for c in chars)
            msg = (f"要用檔案裡的 {len(combos)} 個連段，取代{names}原本的連段嗎？\n\n"
                   f"會刪掉：{n_combo} 個連段、{n_practice} 筆練習紀錄。\n"
                   f"取代前會自動備份整個連段庫到：\n{exchange.backup_dir()}\n"
                   "刪掉的可以從備份救回。")
            if not confirm(self, "取代連段", msg, "取代"):
                return
        snippet_items = self.parsed.snippets if self.with_snippets.isChecked() else []
        try:
            self.result_info = exchange.apply_import(self.store, combos, snippet_items, replace)
        except Exception as e:      # 整批寫入失敗時資料庫不會改到一半
            notice(self, "匯入失敗", f"匯入時發生問題，原本的連段沒有變動。\n\n原因：{e}")
            return
        self.accept()

    def summary(self) -> str:
        r = self.result_info
        if not r:
            return ""
        lines = [f"加入了 {r.added} 個連段" + (f"、{r.practice} 筆練習紀錄" if r.practice else "") + "。"]
        if r.renamed:
            lines.append(f"其中 {r.renamed} 個跟你原本的連段同名，名稱後面加上「（匯入）」。")
        if r.skipped:
            lines.append(f"有 {r.skipped} 個跟你原本的一模一樣，已略過。")
        if r.snippets_added or r.snippets_skipped:
            lines.append(f"自訂按鈕加入 {r.snippets_added} 個" +
                         (f"，{r.snippets_skipped} 個一樣的已略過" if r.snippets_skipped else "") + "。")
        if r.backup:
            lines.append(f"\n取代前的備份：{r.backup}")
        return "\n".join(lines)
