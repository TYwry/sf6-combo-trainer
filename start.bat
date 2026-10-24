@echo off
chcp 65001 >nul
cd /d "%~dp0"
title SF6 連段練習器

if exist ".venv\Scripts\python.exe" goto checkver
echo 第一次執行：正在建立虛擬環境...
where py >nul 2>nul
if not errorlevel 1 (
    py -3 -m venv .venv
) else (
    python -m venv .venv
)
if not exist ".venv\Scripts\python.exe" goto nopython

:checkver
".venv\Scripts\python.exe" -c "import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)"
if errorlevel 1 goto oldpython

".venv\Scripts\python.exe" -c "import PySide6" >nul 2>nul
if not errorlevel 1 goto ocr
echo 正在安裝介面套件 PySide6（約 80 MB，只有第一次需要）...
".venv\Scripts\python.exe" -m pip install --upgrade pip >nul
".venv\Scripts\python.exe" -m pip install -r requirements.txt
if errorlevel 1 goto pipfail

:ocr
".venv\Scripts\python.exe" -c "import opencc" >nul 2>nul
if not errorlevel 1 goto ocr2
echo 正在安裝簡繁轉換套件（不到 1 MB，從網站抓連段時把簡體轉成繁體，只有第一次需要）...
".venv\Scripts\python.exe" -m pip install "opencc-python-reimplemented>=0.1.7"
if errorlevel 1 (
    echo 簡繁轉換套件安裝失敗，從網站抓的連段會保留簡體字；其他功能不受影響。
    timeout /t 5 >nul
)

:ocr2
".venv\Scripts\python.exe" -c "import winrt.windows.media.ocr, winrt.windows.graphics.imaging, winrt.windows.storage.streams, winrt.windows.globalization" >nul 2>nul
if not errorlevel 1 goto run
echo 正在安裝 Windows 文字辨識套件（約 5 MB，讀圖功能用，只有第一次需要）...
".venv\Scripts\python.exe" -m pip install -r requirements-ocr.txt
if errorlevel 1 (
    echo 文字辨識套件安裝失敗，「從圖片加入」不能用；其他功能不受影響。
    timeout /t 5 >nul
)

:run
start "" ".venv\Scripts\pythonw.exe" main.py
exit /b 0

:nopython
echo.
echo 找不到 Python。請到 https://www.python.org/downloads/windows/ 安裝 Python 3.11 以上，
echo 安裝時記得勾選「Add python.exe to PATH」，裝好後再雙擊 start.bat。
pause
exit /b 1

:oldpython
echo.
echo 目前的 Python 版本太舊，需要 3.11 以上。
echo 請安裝新版 Python 後，把專案裡的 .venv 資料夾改名（例如 .venv-old），再雙擊 start.bat 重建。
pause
exit /b 1

:pipfail
echo.
echo 套件安裝失敗，請確認網路連線後再雙擊 start.bat 試一次。
pause
exit /b 1
