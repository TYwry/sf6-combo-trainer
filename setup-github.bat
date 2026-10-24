@echo off
chcp 65001 >nul
cd /d "%~dp0"
title 第一次上傳到 GitHub（只要跑一次）
set "REPO=sf6-combo-trainer"
set "PATH=%PATH%;%ProgramFiles%\Git\cmd;%ProgramFiles%\GitHub CLI"

echo ============================================
echo   第一次上傳到 GitHub（只要跑一次）
echo   會建立一個「公開」的 GitHub 儲存庫：%REPO%
echo   公開＝任何人都看得到程式碼、可以下載（你的連段和設定不會上傳）
echo ============================================
echo.

rem ---- 1. 缺 Git 就安裝 ----
where git >nul 2>nul
if not errorlevel 1 goto hasgit
echo 正在安裝 Git（約 60 MB）...
winget install --id Git.Git -e --source winget --accept-package-agreements --accept-source-agreements
where git >nul 2>nul
if errorlevel 1 goto needrestart
:hasgit

rem ---- 2. 缺 GitHub CLI 就安裝 ----
where gh >nul 2>nul
if not errorlevel 1 goto hasgh
echo 正在安裝 GitHub CLI（約 15 MB）...
winget install --id GitHub.cli -e --source winget --accept-package-agreements --accept-source-agreements
where gh >nul 2>nul
if errorlevel 1 goto needrestart
:hasgh

rem ---- 3. 登入 GitHub（會開瀏覽器）----
gh auth status >nul 2>nul
if not errorlevel 1 goto loggedin
echo.
echo 等一下會開瀏覽器請你登入 GitHub：
echo   畫面上會出現一組 8 個字的代碼，按 Enter 開瀏覽器，把代碼貼上並按授權。
echo.
gh auth login --web --git-protocol https --hostname github.com
if errorlevel 1 goto loginfail
:loggedin
gh auth setup-git

rem ---- 4. 建立本機儲存庫 ----
if exist ".git" (
    echo 這個資料夾已經有 Git 紀錄了，直接沿用。
) else (
    git init -b main
)
git config core.quotepath false
rem 存檔紀錄一律用 GitHub 帳號名稱＋GitHub 提供的匿名信箱，不會露出電腦名稱和真實信箱
set "GHID="
set "GHLOGIN="
for /f "delims=" %%i in ('gh api user --jq .id') do set "GHID=%%i"
for /f "delims=" %%i in ('gh api user --jq .login') do set "GHLOGIN=%%i"
if not defined GHLOGIN goto loginfail
git config user.name "%GHLOGIN%"
git config user.email "%GHID%+%GHLOGIN%@users.noreply.github.com"

rem ---- 5. 加入檔案＋安全檢查 ----
git add -A
call :safety
if errorlevel 1 goto end

git diff --cached --quiet
if not errorlevel 1 goto nochange
git commit -q -m "第一次發佈"
if errorlevel 1 goto commitfail
:nochange

rem ---- 6. 在 GitHub 建立公開的儲存庫並上傳 ----
git remote get-url origin >nul 2>nul
if not errorlevel 1 goto pushonly
gh repo create %REPO% --public --source . --push
if errorlevel 1 goto createfail
goto done

:pushonly
gh repo edit --visibility public --accept-visibility-change-consequences
git push -u origin main
if errorlevel 1 goto pushfail

:done
echo.
echo 完成！之後改了程式，雙擊 save.bat 就會存到 GitHub。
pause
exit /b 0

rem ================= 安全檢查 =================
:safety
set "BAD="
for /f "delims=" %%f in ('git diff --cached --name-only') do call :checkone "%%f"
if not defined BAD exit /b 0
echo.
echo 發現不該上傳的檔案（上面列出的），已經取消這次的上傳，檔案本身都還在。
echo 請把它們加進 .gitignore 後再執行一次。
git reset -q >nul 2>nul || git rm -r -q --cached . >nul 2>nul
pause
exit /b 1

:checkone
echo %~1| findstr /i /r "\.env$ \.env\. config\.json$ history\.json$ \.log$ \.db$ \.sqlite$ \.key$ \.pem$ secret password credential apikey site_cache\.json$ ^\.venv/ __pycache__/ ^圖片/ ^imports/ ^official-pages/ ^\.git-old" >nul
if errorlevel 1 exit /b 0
echo   不該上傳：%~1
set "BAD=1"
exit /b 0

rem ================= 錯誤訊息 =================
:needrestart
echo.
echo 安裝好了，但這個視窗還找不到它。請關掉這個視窗，再雙擊 setup-github.bat 一次。
pause
exit /b 1

:loginfail
echo.
echo 登入 GitHub 沒有成功，請再雙擊 setup-github.bat 試一次。
pause
exit /b 1

:commitfail
echo.
echo 建立存檔紀錄失敗，請把這個視窗的畫面截圖給協助你的人看。
pause
exit /b 1

:createfail
echo.
echo 在 GitHub 建立儲存庫失敗。可能是 GitHub 上已經有同名的 %REPO%。
echo 如果是這樣，請到 GitHub 網站確認後再試一次。
pause
exit /b 1

:pushfail
echo.
echo 上傳失敗，請確認網路連線後，雙擊 save.bat 再試一次。
pause
exit /b 1

:end
exit /b 1
