"""外觀：水墨手繪風——米白紙張、抖動的黑色毛筆外框、麥克筆上色（顏色故意跟外框錯開一點）。

中文用霞鶩文楷 TC，英數用 Patrick Hand／Gochi Hand（assets/fonts，SIL OFL 授權）。
抖動線條用固定的亂數種子，同一個東西每次重畫形狀都一樣，畫面不會閃。
"""
from __future__ import annotations

import logging
import math
import random
from functools import lru_cache

from PySide6.QtCore import QPointF, QRectF, QSize, Qt
from PySide6.QtGui import (QColor, QFont, QFontDatabase, QFontMetrics, QImage, QPainter,
                           QPainterPath, QPalette, QPen, QPixmap, QPolygonF)
from PySide6.QtWidgets import (QApplication, QFrame, QProxyStyle, QStyle, QStyledItemDelegate,
                               QWidget)

from app_info import ASSETS_DIR

log = logging.getLogger(__name__)

LIGHT = {
    "bg": "#F1F0EC",          # 紙張
    "panel": "#FAFAF7",       # 白紙面板
    "panel_alt": "#E6E4DD",
    "line": "#1E1E1E",        # 墨線
    "text": "#1E1E1E",
    "muted": "#6F6D67",
    "accent": "#1E1E1E",      # 按下、選取：整塊墨色
    "accent2": "#A27AD9",     # 紫色（完成）
    "accent_text": "#F7F6F2",
    "lit": "#1E1E1E",          # 方向亮起：深墨色
    "lit_text": "#F7F6F2",
    "ok": "#2F7D6D",
    "warn": "#C4472F",
    "ochre": "#EBC56E",       # 黃土色：選取、拳
    "teal": "#93D5C9",        # 青綠色：分頁、腳
    "purple": "#A27AD9",
    "lavender": "#D7C7F2",
    "red": "#E07060",
    "grey": "#D6D4CC",
    "dark_item": "#3A3A38",
    # 以下是原本寫死在各處的顏色，集中到這裡才能換深色主題
    "disabled_text": "#A9A79F",
    "disabled_ink": "#B9B7B0",
    "input_bg": "rgba(255, 255, 255, 150)",
    "input_focus_bg": "#FFFFFF",
    "card_hover": "#FFFFFF",
    "hover_fill": "#F5E4B8",          # 勾選框滑過
    "ink_panel": "#1E1E1E",           # 整塊黑墨面板
    "ink_panel_text": "#F7F6F2",
    "ink_panel_muted": "#B9B7B0",
    "ink_panel_btn": "#C9C7C0",       # 黑墨面板裡的按鈕
    "ink_panel_ink": "#0E0E0E",
    "ink_panel_hover": "#4A4A47",
    "alt_row": "rgba(30, 30, 30, 10)",
    "sel_row": "rgba(235, 197, 110, 210)",
    "scroll": "rgba(30, 30, 30, 120)",
    "gap_text": "#4E2F8A",            # 連段畫面的幀數字
    "paper_dot": "#000000",           # 紙紋雜點
}
DARK = {
    "bg": "#1A1917",          # 深墨色的紙
    "panel": "#252420",
    "panel_alt": "#312F2B",
    "line": "#E6E2D9",        # 米白色的線
    "text": "#E6E2D9",
    "muted": "#9C988F",
    "accent": "#E6E2D9",
    "accent2": "#8E6BC9",
    "accent_text": "#1A1917",
    "lit": "#E6E2D9",
    "lit_text": "#1A1917",
    "ok": "#5FB8A3",
    "warn": "#E57A62",
    "ochre": "#8C6C2A",       # 麥克筆色調暗，上面的米白字才看得清楚
    "teal": "#2F6A61",
    "purple": "#7653B3",
    "lavender": "#4A3C66",
    "red": "#A34A3B",
    "grey": "#3A3834",
    "dark_item": "#2C2B28",
    "disabled_text": "#6A675F",
    "disabled_ink": "#57544D",
    "input_bg": "rgba(255, 255, 255, 14)",
    "input_focus_bg": "#2B2A26",
    "card_hover": "#2E2D29",
    "hover_fill": "#4A3F26",
    "ink_panel": "#0F0E0D",
    "ink_panel_text": "#E6E2D9",
    "ink_panel_muted": "#8E8A82",
    "ink_panel_btn": "#3A3834",
    "ink_panel_ink": "#8E8A82",
    "ink_panel_hover": "#3A3935",
    "alt_row": "rgba(255, 255, 255, 10)",
    "sel_row": "rgba(140, 108, 42, 220)",
    "scroll": "rgba(230, 226, 217, 110)",
    "gap_text": "#C9B3F0",
    "paper_dot": "#FFFFFF",
}
PALETTE = dict(LIGHT)     # 目前用的配色；換主題時整份換掉，其他檔案照樣讀 PALETTE
_dark = False

CJK_FAMILY = "LXGW WenKai TC"
FALLBACK_CJK = "Microsoft JhengHei UI"
_fam = {"hand": "", "bold": "", "cjk": ""}
FONT_SCALE = 1.12      # 手寫字型字面比較小，整體放大一點
FONT_MODE = "hand"     # hand＝手寫風；jhenghei＝全部用微軟正黑體（設定裡切換，重開程式後套用）
JHENGHEI = ["Microsoft JhengHei UI", "Microsoft JhengHei"]


def set_font_mode(mode: str):
    global FONT_MODE
    FONT_MODE = "jhenghei" if mode == "jhenghei" else "hand"


def load_fonts():
    """載入程式附的手寫字型；載入失敗就退回正黑體。"""
    for key, name in (("hand", "PatrickHand-Regular.ttf"), ("bold", "GochiHand-Regular.ttf"),
                      ("cjk", "LXGWWenKaiTC-Regular.ttf"), ("cjk", "LXGWWenKaiTC-Bold.ttf")):
        fid = QFontDatabase.addApplicationFont(str(ASSETS_DIR / "fonts" / name))
        fams = QFontDatabase.applicationFontFamilies(fid) if fid >= 0 else []
        if fams:
            _fam[key] = fams[0]
        else:
            log.warning("字型載入失敗：%s", name)


def system_is_dark() -> bool:
    """Windows 的「應用程式模式」是不是深色。"""
    try:
        from PySide6.QtGui import QGuiApplication
        return QGuiApplication.styleHints().colorScheme() == Qt.ColorScheme.Dark
    except Exception:
        pass
    try:
        import winreg
        key = winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                             r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize")
        return winreg.QueryValueEx(key, "AppsUseLightTheme")[0] == 0
    except Exception:
        return False


def set_theme_mode(mode: str):
    """light＝淺色、dark＝深色、system＝跟著 Windows；要在 apply_palette 之前呼叫。"""
    global _dark, _paper
    _dark = mode == "dark" or (mode == "system" and system_is_dark())
    PALETTE.clear()
    PALETTE.update(DARK if _dark else LIGHT)
    _paper = None


def is_dark() -> bool:
    return _dark


def colors() -> dict:
    return PALETTE


def c(name: str) -> QColor:
    return QColor(PALETTE[name])


def font(size: float = 10, bold: bool = False) -> QFont:
    """一般文字：英數用手寫字，中文用文楷。"""
    f = QFont()
    if FONT_MODE == "jhenghei":
        f.setFamilies(JHENGHEI)
        f.setPointSizeF(size)
        f.setBold(bold)
        return f
    latin = _fam["bold"] if bold else _fam["hand"]
    f.setFamilies([x for x in (latin, _fam["cjk"] or CJK_FAMILY, FALLBACK_CJK) if x])
    f.setPointSizeF(size * FONT_SCALE)
    f.setBold(bold)
    return f


def display_font(size: float) -> QFont:
    return font(size, True)


# ---- 手繪線條 ----
@lru_cache(maxsize=4096)
def _wobbly(x: float, y: float, w: float, h: float, rad: float, amp: float, seed: int) -> QPainterPath:
    R = random.Random(seed)
    rad = max(0.0, min(rad, w / 2, h / 2))
    amp = min(amp, max(0.3, min(w, h) / 18))
    x0, y0, x1, y1 = x, y, x + w, y + h
    corners = [QPointF(x0 + rad, y0), QPointF(x1 - rad, y0), QPointF(x1, y0 + rad), QPointF(x1, y1 - rad),
               QPointF(x1 - rad, y1), QPointF(x0 + rad, y1), QPointF(x0, y1 - rad), QPointF(x0, y0 + rad)]
    ctrl = [QPointF(x1, y0), QPointF(x1, y1), QPointF(x0, y1), QPointF(x0, y0)]
    start = QPointF(corners[0].x() + R.uniform(-amp, amp), corners[0].y() + R.uniform(-amp, amp))
    path = QPainterPath(start)
    for k in range(4):
        a, b = corners[2 * k], corners[2 * k + 1]
        n = max(2, int(math.hypot(b.x() - a.x(), b.y() - a.y()) / 38))
        horizontal = abs(a.y() - b.y()) < 0.01
        for i in range(1, n + 1):
            t = i / n
            j = R.uniform(-amp, amp) if i < n else 0.0
            path.lineTo(a.x() + (b.x() - a.x()) * t + (0 if horizontal else j),
                        a.y() + (b.y() - a.y()) * t + (j if horizontal else 0))
        nxt = corners[2 * k + 2] if k < 3 else start
        path.quadTo(ctrl[k], nxt)
    return path


def wobbly_path(r: QRectF, rad: float = 10, amp: float = 1.4, seed: int = 0) -> QPainterPath:
    return _wobbly(round(r.x(), 1), round(r.y(), 1), round(r.width(), 1), round(r.height(), 1),
                   round(rad, 1), amp, seed)


@lru_cache(maxsize=1024)
def _wobbly_ellipse(cx: float, cy: float, rx: float, ry: float, seed: int) -> QPainterPath:
    R = random.Random(seed)
    n = 18
    pts = []
    for i in range(n):
        a = 2 * math.pi * i / n
        k = 1 + R.uniform(-0.018, 0.018)
        pts.append(QPointF(cx + math.cos(a) * rx * k, cy + math.sin(a) * ry * k))

    def mid(p, q):
        return QPointF((p.x() + q.x()) / 2, (p.y() + q.y()) / 2)
    path = QPainterPath(mid(pts[-1], pts[0]))
    for i in range(n):
        path.quadTo(pts[i], mid(pts[i], pts[(i + 1) % n]))
    return path


def seed_of(*parts) -> int:
    """用位置、大小、文字算出固定的種子。"""
    h = 7
    for part in parts:
        for ch in str(part):
            h = (h * 131 + ord(ch)) & 0xFFFFFF
    return h


def draw_ink_rect(p: QPainter, r: QRectF, rad: float = 10, fill=None, width: float = 2.4,
                  seed: int = 0, offset=(2.5, 2.0), ink=None, amp: float = 1.4):
    """毛筆框：先塗麥克筆色（稍微錯開），再畫兩層抖動的墨線。"""
    p.save()
    if fill is not None:
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(fill))
        p.drawPath(wobbly_path(r.translated(*offset), rad, amp * 1.4, seed + 99))
    ink = QColor(ink or PALETTE["line"])
    if width > 0:
        for k, (w, a) in enumerate(((width, 255), (width * 0.45, 130))):
            col = QColor(ink)
            col.setAlpha(a)
            p.setPen(QPen(col, w, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
            p.setBrush(Qt.NoBrush)
            p.drawPath(wobbly_path(r.adjusted(k * 0.8, k * 0.6, -k * 0.5, k * 0.4), rad, amp, seed + k))
    p.restore()


def draw_ink_ellipse(p: QPainter, r: QRectF, fill=None, width: float = 2.4, seed: int = 0,
                     offset=(2.5, 2.0), ink=None):
    p.save()
    if fill is not None:
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(fill))
        rr = r.translated(*offset)
        p.drawPath(_wobbly_ellipse(rr.center().x(), rr.center().y(), rr.width() / 2, rr.height() / 2, seed + 9))
    if width > 0:
        p.setPen(QPen(QColor(ink or PALETTE["line"]), width, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
        p.setBrush(Qt.NoBrush)
        p.drawPath(_wobbly_ellipse(r.center().x(), r.center().y(), r.width() / 2, r.height() / 2, seed))
    p.restore()


def draw_marker(p: QPainter, r: QRectF, color, seed: int = 0, fade: bool = True,
                alpha: int = 150, strokes: int | None = None):
    """麥克筆塗抹：幾道半透明粗線疊起來，右邊參差不齊。"""
    R = random.Random(seed)
    n = strokes or max(1, min(7, int(r.height() / 6)))
    col = QColor(color)
    col.setAlpha(alpha)
    p.save()
    p.setPen(QPen(col, max(3.0, r.height() / (n * 0.75)), Qt.SolidLine, Qt.RoundCap))
    for i in range(n):
        y = r.top() + r.height() * (i + 0.5) / n + R.uniform(-1.2, 1.2)
        end = r.right() - (R.uniform(0, min(40, r.width() * 0.08)) if fade else 0)
        p.drawLine(QPointF(r.left() + R.uniform(0, 6), y), QPointF(end, y + R.uniform(-1.5, 1.5)))
    p.restore()


def draw_ink_line(p: QPainter, a: QPointF, b: QPointF, width: float = 2.5, seed: int = 0, ink=None):
    R = random.Random(seed)
    path = QPainterPath(a)
    n = max(2, int(math.hypot(b.x() - a.x(), b.y() - a.y()) / 22))
    for i in range(1, n + 1):
        t = i / n
        j = R.uniform(-1, 1) if i < n else 0
        path.lineTo(a.x() + (b.x() - a.x()) * t + j * 0.6, a.y() + (b.y() - a.y()) * t + j)
    p.save()
    p.setPen(QPen(QColor(ink or PALETTE["line"]), width, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
    p.setBrush(Qt.NoBrush)
    p.drawPath(path)
    p.restore()


_paper: QPixmap | None = None


def paper_texture() -> QPixmap:
    """紙紋：一小塊固定的雜點，鋪滿背景。"""
    global _paper
    if _paper is None:
        size = 256
        img = QImage(size, size, QImage.Format_ARGB32_Premultiplied)
        img.fill(QColor(PALETTE["bg"]))
        p = QPainter(img)
        R = random.Random(11)
        for _ in range(2600):
            dot = QColor(PALETTE["paper_dot"])
            dot.setAlpha(R.randint(5, 16) if not _dark else R.randint(3, 10))
            p.setPen(QPen(dot, R.uniform(0.6, 1.4)))
            p.drawPoint(QPointF(R.random() * size, R.random() * size))
        for _ in range(18):       # 幾條很淡的纖維
            p.setPen(QPen(QColor(120, 110, 90, 14), 0.8))
            x, y = R.random() * size, R.random() * size
            p.drawLine(QPointF(x, y), QPointF(x + R.uniform(-14, 14), y + R.uniform(-6, 6)))
        p.end()
        _paper = QPixmap.fromImage(img)
    return _paper


def _in_dark_panel(w) -> bool:
    while isinstance(w, QWidget):
        if w.objectName() == "inkDark":
            return True
        w = w.parentWidget()
    return False


# ---- 全程式樣式 ----
def apply_palette(app: QApplication):
    if not _fam["hand"]:
        load_fonts()
    if not isinstance(app.style(), SFStyle):
        app.setStyle(SFStyle())
    pal = QPalette()
    for role, key in ((QPalette.Window, "bg"), (QPalette.WindowText, "text"),
                      (QPalette.Base, "panel"), (QPalette.AlternateBase, "panel_alt"),
                      (QPalette.Text, "text"), (QPalette.Button, "grey"),
                      (QPalette.ButtonText, "text"), (QPalette.Highlight, "ochre"),
                      (QPalette.HighlightedText, "text"), (QPalette.PlaceholderText, "muted"),
                      (QPalette.ToolTipBase, "panel"), (QPalette.ToolTipText, "text"),
                      (QPalette.Link, "purple"), (QPalette.Mid, "muted"), (QPalette.Dark, "line"),
                      (QPalette.Light, "panel"), (QPalette.Midlight, "panel_alt")):
        pal.setColor(role, QColor(PALETTE[key]))
    for role in (QPalette.ButtonText, QPalette.Text, QPalette.WindowText):
        pal.setColor(QPalette.Disabled, role, QColor(PALETTE["disabled_text"]))
    app.setPalette(pal)
    app.setFont(font(10))
    app.setStyleSheet(app_qss())


def app_qss() -> str:
    t = PALETTE
    ui = (ASSETS_DIR / "ui").as_posix()
    arrow = "ink_dark_arrow" if _dark else "ink_arrow"      # 深色主題用米白色的箭頭圖
    return f"""
        QLabel#muted {{ color: {t['muted']}; }}
        QLineEdit, QPlainTextEdit, QSpinBox, QDoubleSpinBox, QComboBox {{
            background: {t['input_bg']}; color: {t['text']};
            border: 2px solid {t['line']}; border-radius: 10px; padding: 4px 8px;
            selection-background-color: {t['ochre']}; selection-color: {t['text']};
        }}
        QSpinBox, QDoubleSpinBox {{ qproperty-alignment: AlignCenter; padding-right: 22px; }}
        QLineEdit:focus, QPlainTextEdit:focus, QSpinBox:focus, QDoubleSpinBox:focus, QComboBox:focus {{
            border: 3px solid {t['line']}; background: {t['input_focus_bg']};
        }}
        QLineEdit:disabled, QSpinBox:disabled, QDoubleSpinBox:disabled, QComboBox:disabled {{
            color: {t['disabled_text']}; border-color: {t['disabled_ink']}; background: transparent; }}
        QComboBox::drop-down {{ border: none; width: 26px; }}
        QComboBox::down-arrow {{ image: url({ui}/{arrow}_down.png); width: 14px; height: 10px; }}
        QSpinBox::up-button, QDoubleSpinBox::up-button {{ subcontrol-position: top right;
            border: none; width: 20px; background: transparent; }}
        QSpinBox::down-button, QDoubleSpinBox::down-button {{ subcontrol-position: bottom right;
            border: none; width: 20px; background: transparent; }}
        QSpinBox::up-arrow, QDoubleSpinBox::up-arrow {{ image: url({ui}/{arrow}_up.png); width: 11px; height: 8px; }}
        QSpinBox::down-arrow, QDoubleSpinBox::down-arrow {{ image: url({ui}/{arrow}_down.png); width: 11px; height: 8px; }}
        QSpinBox::up-arrow:disabled, QDoubleSpinBox::up-arrow:disabled {{ image: url({ui}/{arrow}_up_off.png); }}
        QSpinBox::down-arrow:disabled, QDoubleSpinBox::down-arrow:disabled,
        QComboBox::down-arrow:disabled {{ image: url({ui}/{arrow}_down_off.png); }}
        QComboBox QAbstractItemView {{ background: {t['panel']}; color: {t['text']};
            border: 2px solid {t['line']}; selection-background-color: {t['ochre']};
            selection-color: {t['text']}; outline: none; padding: 4px; }}
        QTreeWidget, QListWidget {{ background: transparent; border: none; outline: none;
            alternate-background-color: {t['alt_row']}; }}
        QTreeWidget::item {{ padding: 4px 2px; }}
        QListWidget::item {{ padding: 7px 8px; }}
        QTreeWidget::item:selected {{ background: {t['sel_row']}; color: {t['text']}; }}
        QListWidget::item:selected {{ background: {t['ochre']}; color: {t['text']}; border-radius: 8px; }}
        QHeaderView {{ background: transparent; }}
        QHeaderView::section {{ background: transparent; color: {t['muted']}; border: none;
            border-bottom: 2px solid {t['line']}; padding: 4px 6px; }}
        QScrollArea {{ background: transparent; border: none; }}
        QScrollArea > QWidget > QWidget {{ background: transparent; }}
        QScrollBar:vertical {{ background: transparent; width: 10px; margin: 2px; }}
        QScrollBar::handle:vertical {{ background: {t['scroll']}; min-height: 30px; border-radius: 3px; }}
        QScrollBar::handle:vertical:hover {{ background: {t['line']}; }}
        QScrollBar:horizontal {{ background: transparent; height: 10px; margin: 2px; }}
        QScrollBar::handle:horizontal {{ background: {t['scroll']}; min-width: 30px; border-radius: 3px; }}
        QScrollBar::add-line, QScrollBar::sub-line {{ width: 0; height: 0; }}
        QScrollBar::add-page, QScrollBar::sub-page {{ background: none; }}
        QStatusBar {{ background: transparent; color: {t['muted']}; }}
        QToolTip {{ background: {t['panel']}; color: {t['text']}; border: 2px solid {t['line']}; }}
        QDialog, QMessageBox {{ background: {t['bg']}; }}
        QProgressBar {{ border: 2px solid {t['line']}; border-radius: 4px; background: transparent; }}
        QProgressBar::chunk {{ background: {t['purple']}; border-radius: 2px; }}
        QSlider:horizontal {{ min-height: 28px; }}
        QSlider::groove:horizontal {{ height: 4px; background: {t['line']}; border-radius: 2px; }}
        QSlider::handle:horizontal {{ background: {t['ochre']}; border: 2px solid {t['line']};
            width: 16px; height: 16px; margin: -8px 0; border-radius: 10px; }}
        QFrame#inkDark QLabel, QFrame#inkDark QCheckBox, QFrame#inkDark QListWidget {{ color: {t['ink_panel_text']}; }}
        QFrame#inkDark QLabel#muted {{ color: {t['ink_panel_muted']}; }}
    """


def split_label(text: str) -> str:
    """按鈕、標籤文字太長時對半換行；奇數字時多的字放後行。"""
    if len(text) <= 6:
        return text
    half = len(text) // 2
    return text[:half] + "\n" + text[half:]


# ---- 按鈕、勾選框、箭頭 ----
class SFStyle(QProxyStyle):
    """以 Fusion 為底，按鈕畫成手繪的藥丸形：墨線外框＋錯開的麥克筆色。"""

    def __init__(self):
        super().__init__("Fusion")

    def drawPrimitive(self, element, option, painter, widget=None):
        if element in (QStyle.PE_PanelButtonCommand, QStyle.PE_PanelButtonBevel):
            self._draw_button(option, painter, widget)
            return
        if element in (QStyle.PE_IndicatorArrowDown, QStyle.PE_IndicatorArrowUp,
                       QStyle.PE_IndicatorSpinUp, QStyle.PE_IndicatorSpinDown,
                       QStyle.PE_IndicatorSpinPlus, QStyle.PE_IndicatorSpinMinus):
            up = element in (QStyle.PE_IndicatorArrowUp, QStyle.PE_IndicatorSpinUp, QStyle.PE_IndicatorSpinPlus)
            self._draw_arrow(option, painter, up)
            return
        if element == QStyle.PE_IndicatorCheckBox:
            self._draw_check(option, painter, widget)
            return
        if element == QStyle.PE_FrameFocusRect and isinstance(option.styleObject, QWidget) \
                and option.styleObject.inherits("QAbstractButton"):
            return          # 按鈕的焦點改用加粗外框表示
        super().drawPrimitive(element, option, painter, widget)

    def _draw_button(self, option, p: QPainter, widget):
        r = QRectF(option.rect).adjusted(3, 3, -5, -4)
        state = option.state
        enabled = bool(state & QStyle.State_Enabled)
        hover = enabled and bool(state & QStyle.State_MouseOver)
        down = bool(state & (QStyle.State_Sunken | QStyle.State_On))
        focus = bool(state & QStyle.State_HasFocus)
        danger = bool(widget is not None and widget.property("danger"))
        text = widget.text() if widget is not None and hasattr(widget, "text") else ""
        seed = seed_of(int(r.width()), int(r.height()), text)
        on_dark = _in_dark_panel(widget)

        if not enabled:
            fill, ink = None, PALETTE["disabled_ink"]
        elif danger:
            fill, ink = PALETTE["red"], PALETTE["line"]
        elif down:
            fill, ink = PALETTE["line"], PALETTE["line"]
        elif hover:
            fill, ink = PALETTE["ochre"], PALETTE["line"]
        else:
            fill, ink = (PALETTE["ink_panel_btn"] if on_dark else PALETTE["grey"]), PALETTE["line"]
        if on_dark and enabled:
            ink = PALETTE["ink_panel_ink"]
        p.save()
        p.setRenderHint(QPainter.Antialiasing)
        offset = (0, 0) if down else (2.5, 2.0)
        draw_ink_rect(p, r, r.height() / 2, fill, 3.2 if focus else 2.3, seed, offset, ink)
        p.restore()

    def _draw_arrow(self, option, p: QPainter, up: bool):
        r = QRectF(option.rect)
        cx, cy = r.center().x(), r.center().y()
        w, h = min(5.0, r.width() / 3), min(3.5, r.height() / 3)
        enabled = bool(option.state & QStyle.State_Enabled)
        pts = [QPointF(cx - w, cy + h / 2), QPointF(cx, cy - h / 2), QPointF(cx + w, cy + h / 2)] if up \
            else [QPointF(cx - w, cy - h / 2), QPointF(cx, cy + h / 2), QPointF(cx + w, cy - h / 2)]
        p.save()
        p.setRenderHint(QPainter.Antialiasing)
        p.setPen(QPen(QColor(PALETTE["line"] if enabled else PALETTE["disabled_ink"]), 2, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
        p.setBrush(Qt.NoBrush)
        p.drawPolyline(QPolygonF(pts))
        p.restore()

    def _draw_check(self, option, p: QPainter, widget=None):
        r = QRectF(option.rect).adjusted(1.5, 1.5, -1.5, -1.5)
        on = bool(option.state & QStyle.State_On)
        enabled = bool(option.state & QStyle.State_Enabled)
        hover = bool(option.state & QStyle.State_MouseOver)
        ink = PALETTE["disabled_ink"] if not enabled else (PALETTE["ink_panel_text"] if _in_dark_panel(widget) else PALETTE["line"])
        p.save()
        p.setRenderHint(QPainter.Antialiasing)
        fill = PALETTE["ochre"] if on and enabled else (PALETTE["hover_fill"] if hover and enabled else None)
        draw_ink_rect(p, r, 3, fill, 2.0, seed_of(int(r.width())), (1.5, 1.2), ink, amp=0.6)
        if on:
            p.setPen(QPen(QColor(ink), 2.4, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
            p.setBrush(Qt.NoBrush)
            p.drawPolyline(QPolygonF([QPointF(r.left() + r.width() * 0.18, r.center().y()),
                                      QPointF(r.left() + r.width() * 0.42, r.bottom() - r.height() * 0.18),
                                      QPointF(r.right() + r.width() * 0.08, r.top() - r.height() * 0.05)]))
        p.restore()

    def drawControl(self, element, option, painter, widget=None):
        if element == QStyle.CE_PushButtonLabel:
            enabled = bool(option.state & QStyle.State_Enabled)
            down = bool(option.state & (QStyle.State_Sunken | QStyle.State_On))
            col = PALETTE["accent_text"] if down and enabled else (PALETTE["text"] if enabled else PALETTE["disabled_text"])
            option.palette.setColor(QPalette.ButtonText, QColor(col))
            option.rect = option.rect.adjusted(0, 0, -1, -1)
        super().drawControl(element, option, painter, widget)

    def pixelMetric(self, metric, option=None, widget=None):
        if metric in (QStyle.PM_IndicatorWidth, QStyle.PM_IndicatorHeight):
            return 18
        return super().pixelMetric(metric, option, widget)

    def sizeFromContents(self, ct, option, size, widget=None):
        s = super().sizeFromContents(ct, option, size, widget)
        if ct == QStyle.CT_PushButton:
            return QSize(s.width() + 28, max(s.height() + 6, 40))
        return s


# ---- 面板 ----
class GlassPanel(QFrame):
    """手繪面板：白紙（預設）或整塊黑墨（dark=True）。名稱沿用舊版，其他檔案不用改。"""

    def __init__(self, parent=None, dark: bool = False):
        super().__init__(parent)
        self.dark = dark
        self.setObjectName("inkDark" if dark else "panel")

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        r = QRectF(self.rect()).adjusted(3, 3, -6, -6)
        seed = seed_of(self.width(), self.height(), "dark" if self.dark else "panel")
        if self.dark:
            draw_ink_rect(p, r, 22, PALETTE["ink_panel"], 3.6, seed, (0, 0))
        else:
            draw_ink_rect(p, r, 22, PALETTE["panel"], 3.2, seed, (3.5, 3))
        p.end()


class InkListDelegate(QStyledItemDelegate):
    """黑墨面板裡的清單：每一項是一塊深灰手繪卡片，選到的塗黃土色。"""

    def sizeHint(self, option, index):
        s = super().sizeHint(option, index)
        # 寬度不跟著字數變長：清單不會出現左右捲軸，太長的名稱用「…」
        return QSize(1, max(s.height() + 18, 52))

    def paint(self, p: QPainter, option, index):
        r = QRectF(option.rect).adjusted(4, 4, -8, -6)
        sel = bool(option.state & QStyle.State_Selected)
        hover = bool(option.state & QStyle.State_MouseOver)
        text = index.data() or ""
        w = option.widget
        picking = w is not None and bool(w.property("select_mode"))
        if picking:
            sel = bool(index.data(Qt.UserRole + 1))      # 選取模式：勾起來的才塗色
        p.save()
        p.setRenderHint(QPainter.Antialiasing)
        fill = PALETTE["ochre"] if sel else (PALETTE["ink_panel_hover"] if hover else PALETTE["dark_item"])
        drag_row = w.property("drag_row") if w is not None else None
        lifted = drag_row is not None and drag_row == index.row()
        if lifted and not w.property("lift_pass"):
            p.restore()
            return                       # 拖動中的那一項最後才畫，才會浮在其他項目上面
        if lifted:
            # 拖動中的那一項：放大、往上浮起，底下一大塊影子（像在排 iPhone 的 App）
            k = float(w.property("drag_lift") or 0.0)
            shadow = QColor(0, 0, 0, int(130 * k))
            p.setPen(Qt.NoPen)
            p.setBrush(shadow)
            p.drawPath(wobbly_path(r.translated(6 * k, 10 * k), 14, 1.4, seed_of("shadow", index.row())))
            c = r.center()
            p.translate(c.x(), c.y() - 6 * k)
            p.scale(1 + 0.08 * k, 1 + 0.08 * k)
            p.translate(-c.x(), -c.y())
        elif drag_row is not None:
            # 其他項目輕輕抖動，表示現在可以排順序
            phase = float(w.property("wiggle") or 0.0) + index.row() * 1.7
            k = min(1.0, float(w.property("drag_lift") or 0.0))
            c = r.center()
            p.translate(c.x(), c.y())
            p.rotate(1.3 * k * math.sin(phase))
            p.translate(-c.x(), -c.y())
        draw_ink_rect(p, r, 14, fill, 3.4 if lifted else 2.4, seed_of(index.row(), int(r.width())), (2, 2),
                      PALETTE["ink_panel_ink"])
        left = 16
        if picking:
            # 選取模式：左邊一個手繪圓圈，勾起來的打勾
            ink = QColor(PALETTE["text"] if sel else PALETTE["ink_panel_text"])
            box = QRectF(r.left() + 12, r.center().y() - 10, 20, 20)
            draw_ink_ellipse(p, box, None, 2.2, seed_of("chk", index.row()), (0, 0), ink)
            if sel:
                pen = QPen(ink, 3)
                pen.setCapStyle(Qt.RoundCap)
                pen.setJoinStyle(Qt.RoundJoin)
                p.setPen(pen)
                p.drawPolyline([QPointF(box.left() + 4, box.center().y()),
                                QPointF(box.left() + 8.5, box.bottom() - 4.5),
                                QPointF(box.right() - 3.5, box.top() + 4.5)])
            left = 42
        p.setFont(font(11, True))
        p.setPen(QColor(PALETTE["text"] if sel else PALETTE["ink_panel_text"]))
        p.drawText(r.adjusted(left, 0, -10, 0), Qt.AlignVCenter | Qt.AlignLeft,
                   p.fontMetrics().elidedText(text, Qt.ElideRight, int(r.width() - left - 10)))
        p.restore()


def draw_glow_text(p: QPainter, rect: QRectF, flags, text: str, f: QFont,
                   glow: QColor | None = None, radius: int = 3):
    """手寫標題字；給 glow 時，字後面塗一道該顏色的麥克筆。"""
    p.save()
    p.setFont(f)
    if glow is not None:
        br = QRectF(p.fontMetrics().boundingRect(rect.toRect(), int(flags), text))
        draw_marker(p, br.adjusted(-12, br.height() * 0.3, 12, br.height() * 0.05), glow,
                    seed_of(text), strokes=3)
    p.setPen(QColor(PALETTE["text"]))
    p.drawText(rect, flags, text)
    p.restore()


def draw_notch_line(p: QPainter, r: QRectF):
    """標題下的黃土色麥克筆線。"""
    draw_marker(p, QRectF(r.left() + 4, r.top() + 2, min(r.width() * 0.4, 220), 9),
                PALETTE["ochre"], 3, strokes=2)


class GlowTitle(QWidget):
    """手寫標題；line=True 時下面塗一道黃土色。在黑墨面板裡會自動改成白字。"""

    def __init__(self, text: str, size: float = 12, line: bool = False,
                 align=Qt.AlignLeft, parent=None):
        super().__init__(parent)
        self.text, self.size, self.line, self.align = text, size, line, align
        self.setFixedHeight(int(size * 2.5) + (10 if line else 0))
        self.setAccessibleName(text)

    def setText(self, text: str):
        self.text = text
        self.setAccessibleName(text)
        self.updateGeometry()
        self.update()

    def sizeHint(self) -> QSize:
        return QSize(QFontMetrics(display_font(self.size)).horizontalAdvance(self.text) + 16, self.height())

    def minimumSizeHint(self) -> QSize:
        return self.sizeHint()

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        text_h = self.height() - (10 if self.line else 0)
        rect = QRectF(4, 0, self.width() - 8, text_h)
        f = display_font(self.size)
        if self.line:
            tw = QFontMetrics(f).horizontalAdvance(self.text)
            x = rect.center().x() - tw / 2 if self.align & Qt.AlignHCenter else rect.left()
            draw_marker(p, QRectF(x - 4, text_h - 12, tw + 14, 14), PALETTE["ochre"],
                        seed_of(self.text), strokes=2, alpha=190)
        p.setFont(f)
        p.setPen(QColor(PALETTE["ink_panel_text"] if _in_dark_panel(self) else PALETTE["text"]))
        p.drawText(rect, self.align | Qt.AlignVCenter, self.text)
        p.end()


class Backdrop(QWidget):
    """主視窗背景：米白紙張加紙紋。"""

    def paintEvent(self, _):
        p = QPainter(self)
        p.drawTiledPixmap(self.rect(), paper_texture())
        p.end()
