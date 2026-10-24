@echo off
chcp 65001 >nul
cd /d "%~dp0"
title 存到 GitHub
set "PATH=%PATH%;%ProgramFiles%\Git\cmd;%ProgramFiles%\GitHub CLI"

if not exist ".git" goto nosetup

git add -A
call :safety
if errorlevel 1 exit /b 1

git diff --cached --quiet
if not errorlevel 1 goto nochange

if exist ".commit-message.txt" goto usefile
echo 這次改了什麼？（輸入一句話後按 Enter）
set "MSG="
set /p "MSG=說明："
if not defined MSG set "MSG=更新"
git commit -q -m "%MSG%"
if errorlevel 1 goto commitfail
goto push

:usefile
echo 使用 .commit-message.txt 的更新日誌：
echo --------------------------------------------
type ".commit-message.txt"
echo --------------------------------------------
git commit -q -F ".commit-message.txt"
if errorlevel 1 goto commitfail
del ".commit-message.txt"

:push
git push -u origin main
if errorlevel 1 goto pushfail
echo.
echo 存好了，已經上傳到 GitHub。
pause
exit /b 0

:nochange
echo 沒有新的修改，不用存。
pause
exit /b 0

rem ================= 安全檢查 =================
:safety
set "BAD="
for /f "delims=" %%f in ('git diff --cached --name-only') do call :checkone "%%f"
if not defined BAD exit /b 0
echo.
echo 發現不該上傳的檔案（上面列出的），已經取消這次的存檔，檔案本身都還在。
echo 請把它們加進 .gitignore 後再執行一次。
git reset -q >nul 2>nul
pause
exit /b 1

:checkone
echo %~1| findstr /i /r "\.env$ \.env\. config\.json$ history\.json$ \.log$ \.db$ \.sqlite$ \.key$ \.pem$ secret password credential apikey site_cache\.json$ ^\.venv/ __pycache__/ ^圖片/ ^imports/ ^official-pages/ ^\.git-old" >nul
if errorlevel 1 exit /b 0
echo   不該上傳：%~1
set "BAD=1"
exit /b 0

:nosetup
echo 還沒設定過 GitHub，請先雙擊 setup-github.bat。
pause
exit /b 1

:commitfail
echo.
echo 建立存檔紀錄失敗，請把這個視窗的畫面截圖給協助你的人看。
pause
exit /b 1

:pushfail
echo.
echo 上傳失敗，請確認網路連線後再雙擊 save.bat 試一次（剛剛的存檔紀錄還在，不會不見）。
pause
exit /b 1
