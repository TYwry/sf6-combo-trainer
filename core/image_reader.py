"""從圖片讀出連段記法。

用 Windows 內建文字辨識（只看得懂文字，不用顯示卡）。
這個檔案不碰介面；耗時的函式請放在背景執行緒呼叫。
"""
from __future__ import annotations

import asyncio
import re
import sys
import unicodedata

from PySide6.QtCore import QBuffer, QByteArray, QIODevice, Qt
from PySide6.QtGui import QImage

from core.notation import parse

WINOCR = "winocr"


class ReaderError(Exception):
    """讀圖失敗，訊息可以直接給使用者看。"""


# ---- 把讀到的文字整理成記法 ----
def to_notation(raw: str) -> str:
    t = re.sub(r"<think>.*?</think>", "", raw or "", flags=re.S)
    t = unicodedata.normalize("NFKC", t).replace("`", "")
    lines = []
    for line in t.splitlines():
        line = re.sub(r"^\s*(連段|記法|指令|combo|notation|answer|output)\s*[:：]\s*", "", line.strip(), flags=re.I)
        if line and re.search(r"[1-9↓↘→↙←↖↑↗]|[LMH][PK]|\b[PK]\b|DR|DI|SA", line, re.I):
            lines.append(line)
    t = " > ".join(lines)
    t = re.sub(r"\s+xx\s+", " > ", t, flags=re.I)            # 2MK xx 236HP（取消）
    # 按鍵後面、接著空白或下一招的 → 是「接」，不是方向（後面直接接箭頭時仍當方向）
    t = re.sub(r"(?<=[A-Za-z)])\s*→(?=\s|[0-9A-Za-z])\s*", " > ", t)
    t = re.sub(r"\b(cr|c)\.", "2", t, flags=re.I)              # cr.MK＝2MK
    t = re.sub(r"\b(st|s)\.", "", t, flags=re.I)
    t = t.replace("~", ".")
    # 括號外的中文說明包成備註
    parts = re.split(r"(\([^)]*\))", t)
    for i in range(0, len(parts), 2):
        parts[i] = re.sub(r"([㐀-鿿]+)", r"(\1)", parts[i])
    t = "".join(parts)
    # 按鍵後面直接接方向（HK 623HP）就是下一招
    t = re.sub(r"(?<=[PK])\s*(?=[1-9↓↘→↙←↖↑↗\[])", " > ", t, flags=re.I)
    t = re.sub(r"\s*>\s*", " > ", t)
    t = re.sub(r"(\s*>\s*){2,}", " > ", t).strip(" >")
    combo = parse(t)
    # 全部看得懂就改成標準寫法（箭頭換成數字）；有看不懂的地方保留原樣給使用者改
    return combo.text() if combo.groups and not combo.errors else t


def usable(notation: str) -> bool:
    combo = parse(notation)
    return combo.step_count() > 0 and len(combo.errors) <= combo.step_count()


# ---- 圖片前處理 ----
def to_png(img: QImage, max_side: int = 1400) -> bytes:
    if max(img.width(), img.height()) > max_side:
        img = img.scaled(max_side, max_side, Qt.KeepAspectRatio, Qt.SmoothTransformation)
    ba = QByteArray()
    buf = QBuffer(ba)
    buf.open(QIODevice.WriteOnly)
    img.save(buf, "PNG")
    return bytes(ba)


def prepare_for_ocr(img: QImage, max_side: int = 4000) -> QImage:
    """放大、轉灰階、拉高對比；深底淺字就反相成白底黑字。"""
    w, h = img.width(), img.height()
    scale = min(3.0, 600 / max(h, 1)) if h < 600 else 1.0
    scale = min(scale, max_side / max(w, h, 1))
    if abs(scale - 1.0) > 0.05:
        img = img.scaled(int(w * scale), int(h * scale), Qt.KeepAspectRatio, Qt.SmoothTransformation)
    g = img.convertToFormat(QImage.Format_Grayscale8)
    data = bytes(g.constBits())[:g.sizeInBytes()]
    hist = [data.count(bytes([v])) for v in range(256)]
    total = sum(hist) or 1
    acc, lo, hi = 0, 0, 255
    for v in range(256):          # 去掉最暗與最亮各 1% 再拉滿
        acc += hist[v]
        if acc >= total * 0.01:
            lo = v
            break
    acc = 0
    for v in range(255, -1, -1):
        acc += hist[v]
        if acc >= total * 0.01:
            hi = v
            break
    mean = sum(v * c for v, c in enumerate(hist)) / total
    invert = mean < 110
    span = max(hi - lo, 1)
    table = bytearray()
    for v in range(256):
        x = min(255, max(0, round((v - lo) * 255 / span)))
        table.append(255 - x if invert else x)
    out = data.translate(bytes(table))
    result = QImage(out, g.width(), g.height(), g.bytesPerLine(), QImage.Format_Grayscale8).copy()
    return result


def winocr_available() -> bool:
    if sys.platform != "win32":
        return False
    try:
        import winrt.windows.media.ocr  # noqa: F401
        import winrt.windows.graphics.imaging  # noqa: F401
        import winrt.windows.storage.streams  # noqa: F401
    except ImportError:
        return False
    return True


async def _winocr_async(data: bytes, w: int, h: int) -> list[str]:
    from winrt.windows.globalization import Language
    import winrt.windows.foundation  # noqa: F401  讓 await 可以用
    from winrt.windows.graphics.imaging import BitmapPixelFormat, SoftwareBitmap
    from winrt.windows.media.ocr import OcrEngine
    from winrt.windows.storage.streams import DataWriter

    writer = DataWriter()
    writer.write_bytes(data)
    bitmap = SoftwareBitmap.create_copy_from_buffer(
        writer.detach_buffer(), BitmapPixelFormat.BGRA8, w, h)
    engine = None
    try:
        engine = OcrEngine.try_create_from_language(Language("en-US"))
    except Exception:
        engine = None
    engine = engine or OcrEngine.try_create_from_user_profile_languages()
    if engine is None:
        raise ReaderError("Windows 沒有可用的文字辨識語言。")
    result = await engine.recognize_async(bitmap)
    return [line.text for line in result.lines]


def windows_ocr(img: QImage) -> str:
    if not winocr_available():
        raise ReaderError("Windows 內建文字辨識還沒裝好（重新用 start.bat 開啟程式會自動安裝）。")
    prepared = prepare_for_ocr(img).convertToFormat(QImage.Format_ARGB32)
    data = bytes(prepared.constBits())[:prepared.sizeInBytes()]
    try:
        lines = asyncio.run(_winocr_async(data, prepared.width(), prepared.height()))
    except ReaderError:
        raise
    except Exception as e:
        raise ReaderError(f"Windows 文字辨識失敗：{e}") from e
    return "\n".join(lines)


# ---- 整合 ----
def read_image(img: QImage, on_stage=lambda text: None,
               cancelled=lambda: False) -> tuple[str, str, list[str]]:
    """回傳 (記法, 用了什麼方式, 提醒訊息)。失敗時丟出 ReaderError。"""
    on_stage("Windows 內建文字辨識中…")
    text = to_notation(windows_ocr(img))
    if not text:
        raise ReaderError("圖片裡沒有讀到文字。內建辨識看不懂箭頭圖示，只適合用文字寫的連段；"
                          "有箭頭的圖可以請 AI 聊天工具讀成文字再貼上。")
    return text, "Windows 內建文字辨識", ["內建辨識看不懂箭頭圖示，請對照圖片檢查。"]
