"""連段的參考影片（YouTube 等）：讀網址裡的時間、組出從某個時間點開始播的網址。"""
from __future__ import annotations

import re
import urllib.parse

MAX_T = 24 * 3600


def parse_time(text: str) -> int | None:
    """「1:23」「83」「1:02:03」「1m23s」「1h2m3s」→ 秒數；空白回傳 0；看不懂回傳 None。"""
    t = (text or "").strip().lower()
    if not t:
        return 0
    if re.fullmatch(r"\d+", t):
        secs = int(t)
    elif re.fullmatch(r"\d+(:\d{1,2}){1,2}", t):
        secs = 0
        for part in t.split(":"):
            secs = secs * 60 + int(part)
    else:
        m = re.fullmatch(r"(?:(\d+)h)?(?:(\d+)m)?(?:(\d+)s?)?", t)
        if not m or not any(m.groups()):
            return None
        h, mi, s = (int(x or 0) for x in m.groups())
        secs = h * 3600 + mi * 60 + s
    return secs if 0 <= secs <= MAX_T else None


def format_time(secs: int) -> str:
    if not secs:
        return ""
    h, rest = divmod(int(secs), 3600)
    m, s = divmod(rest, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


def _split(url: str):
    return urllib.parse.urlsplit(url.strip())


def is_web_url(url: str) -> bool:
    try:
        p = _split(url)
    except ValueError:
        return False
    return p.scheme in ("http", "https") and bool(p.netloc)


def time_in_url(url: str) -> int | None:
    """網址裡帶的時間（YouTube 的 t=、start=，或 #t=）；沒有就回傳 None。"""
    try:
        p = _split(url)
    except ValueError:
        return None
    q = dict(urllib.parse.parse_qsl(p.query))
    frag = dict(urllib.parse.parse_qsl(p.fragment))
    for v in (q.get("t"), q.get("start"), frag.get("t")):
        if v:
            secs = parse_time(v)
            if secs is not None:
                return secs
    return None


def with_time(url: str, secs: int) -> str:
    """把網址原本的時間拿掉，換成 secs（0＝從頭播）。"""
    p = _split(url)
    query = [(k, v) for k, v in urllib.parse.parse_qsl(p.query, keep_blank_values=True) if k not in ("t", "start")]
    if secs:
        query.append(("t", f"{int(secs)}s"))
    frag = "" if p.fragment.startswith("t=") else p.fragment
    return urllib.parse.urlunsplit((p.scheme, p.netloc, p.path, urllib.parse.urlencode(query), frag))
