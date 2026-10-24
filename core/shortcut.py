"""建立 Windows 捷徑（桌面＋開始功能表），用 Windows 內建的 PowerShell，不用另外安裝東西。

已經有同名捷徑就不動它（不覆蓋）。
"""
from __future__ import annotations

import logging
import os
import subprocess
import sys
from pathlib import Path

from app_info import APP_DIR, APP_DISPLAY_NAME, ASSETS_DIR, FROZEN

log = logging.getLogger(__name__)

PS_SCRIPT = r"""
$ErrorActionPreference = 'Stop'
$shell = New-Object -ComObject WScript.Shell
foreach ($folder in @([Environment]::GetFolderPath('Desktop'), [Environment]::GetFolderPath('Programs'))) {
    $path = Join-Path $folder ($env:SF6_NAME + '.lnk')
    if (Test-Path -LiteralPath $path) { Write-Output ('SKIP|' + $path); continue }
    try {
        $s = $shell.CreateShortcut($path)
        $s.TargetPath = $env:SF6_TARGET
        $s.Arguments = $env:SF6_ARGS
        $s.WorkingDirectory = $env:SF6_DIR
        $s.IconLocation = $env:SF6_ICON + ',0'
        $s.Description = $env:SF6_NAME
        $s.Save()
        Write-Output ('MADE|' + $path)
    } catch {
        Write-Output ('FAIL|' + $path + '：' + $_.Exception.Message)
    }
}
"""


def supported() -> bool:
    return sys.platform == "win32"


def _target() -> tuple[str, str]:
    """分享版直接指向 exe；自用版用 pythonw.exe（不會跳出黑色視窗）執行 main.py。"""
    if FROZEN:
        return sys.executable, ""
    exe = Path(sys.executable)
    pyw = exe.with_name("pythonw.exe")
    return str(pyw if pyw.exists() else exe), f'"{APP_DIR / "main.py"}"'


def create_all() -> tuple[list[str], list[str], list[str]]:
    """回傳 (已建立, 已存在略過, 錯誤訊息)。"""
    target, args = _target()
    env = dict(os.environ, SF6_NAME=APP_DISPLAY_NAME, SF6_TARGET=target, SF6_ARGS=args,
               SF6_DIR=str(APP_DIR), SF6_ICON=str(ASSETS_DIR / "app_ink.ico"))
    try:
        out = subprocess.run(
            ["powershell.exe", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
             "-Command", "[Console]::OutputEncoding = [Text.Encoding]::UTF8;" + PS_SCRIPT],
            env=env, capture_output=True, timeout=30,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    except (OSError, subprocess.SubprocessError) as exc:
        log.exception("建立捷徑失敗")
        return [], [], [str(exc)]
    made, skipped, errors = [], [], []
    for line in out.stdout.decode("utf-8", "replace").splitlines():
        kind, _, rest = line.strip().partition("|")
        {"MADE": made, "SKIP": skipped, "FAIL": errors}.get(kind, []).append(rest)
    if out.returncode and not (made or skipped or errors):
        errors.append(out.stderr.decode("utf-8", "replace").strip()[:300] or "PowerShell 執行失敗")
    log.info("捷徑：建立 %s，略過 %s，失敗 %s", made, skipped, errors)
    return made, skipped, errors
