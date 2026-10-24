@echo off
chcp 65001 >nul
cd /d "%~dp0"
title 打包並發佈下載版到 GitHub
set "PATH=%PATH%;%ProgramFiles%\Git\cmd;%ProgramFiles%\GitHub CLI"
set "ZIPNAME=sf6-combo-trainer.zip"
set "EXENAME=SF6ComboTrainer"
set "PY=.venv\Scripts\python.exe"

if not exist ".git" goto nosetup
if not exist "%PY%" goto novenv

rem ---- 從 app_info.py 讀版本號 ----
set "VER="
for /f "delims=" %%v in ('powershell -NoProfile -Command "$m = Select-String -Path app_info.py -Pattern 'APP_VERSION\s*=\s*.([0-9.]+)'; if ($m) { $m.Matches[0].Groups[1].Value }"') do set "VER=%%v"
if not defined VER goto nover
set "TAG=v%VER%"
echo 這次要發佈的版本：%TAG%

rem ---- 還有沒存的修改就先請使用者存檔 ----
set "DIRTY="
for /f "delims=" %%s in ('git status --porcelain') do set "DIRTY=1"
if defined DIRTY goto dirty

rem ---- 同一版已經發佈過就停下來 ----
gh release view %TAG% >nul 2>nul
if not errorlevel 1 goto already

rem ---- 同一版已經打包過（上次上傳失敗）就直接上傳 ----
set "OUT=dist\%TAG%"
if exist "%OUT%\%ZIPNAME%" goto upload

rem ---- 打包成 exe（別人不用裝 Python）----
echo.
echo 正在準備打包工具 PyInstaller（約 10 MB，只有第一次需要下載）...
"%PY%" -m pip install --quiet "pyinstaller>=6.6"
if errorlevel 1 goto buildfail

echo 正在打包，大約需要 1～3 分鐘...
set "ROOT=%CD%"
"%PY%" -m PyInstaller --noconfirm --clean --windowed --name "%EXENAME%" ^
  --icon "%ROOT%\assets\app_ink.ico" ^
  --add-data "%ROOT%\assets;assets" ^
  --add-data "%ROOT%\data\movelists;data\movelists" ^
  --add-data "%ROOT%\mod;mod" ^
  --collect-all opencc --collect-all winrt ^
  --distpath "%ROOT%\%OUT%\app" --workpath "%ROOT%\build\pyinstaller" --specpath "%ROOT%\build" ^
  "%ROOT%\main.py"
if errorlevel 1 goto buildfail
if not exist "%OUT%\app\%EXENAME%\%EXENAME%.exe" goto buildfail

echo 正在壓縮成 %ZIPNAME% ...
powershell -NoProfile -Command "Compress-Archive -Path '%OUT%\app\%EXENAME%' -DestinationPath '%OUT%\%ZIPNAME%'"
if errorlevel 1 goto zipfail
if not exist "%OUT%\%ZIPNAME%" goto zipfail

:upload
git log -1 --format=%%B > "%OUT%\release-notes.txt"
git push -u origin main
if errorlevel 1 goto pushfail
gh release create %TAG% "%OUT%\%ZIPNAME%" --title "%TAG%" --notes-file "%OUT%\release-notes.txt"
if errorlevel 1 goto releasefail

echo.
echo 發佈好了！README 最上面的下載連結現在會下載 %TAG%。
pause
exit /b 0

:nosetup
echo 還沒設定過 GitHub，請先雙擊 setup-github.bat。
pause
exit /b 1

:novenv
echo 找不到練習器的 Python 環境，請先雙擊 start.bat 開一次練習器，再執行這個檔案。
pause
exit /b 1

:nover
echo 讀不到 app_info.py 裡的版本號（APP_VERSION），沒有發佈。
pause
exit /b 1

:dirty
echo 還有修改沒存到 GitHub，請先雙擊 save.bat 存檔，再執行這個檔案。
pause
exit /b 1

:already
echo %TAG% 已經發佈過了。程式更新版本號後再發佈。
pause
exit /b 1

:buildfail
echo.
echo 打包失敗，沒有發佈。請把這個視窗的畫面截圖給協助你的人看。
pause
exit /b 1

:zipfail
echo 壓縮失敗，沒有發佈。
pause
exit /b 1

:pushfail
echo 上傳失敗，請確認網路連線後再執行一次（已經打包好的檔案會直接沿用）。
pause
exit /b 1

:releasefail
echo 在 GitHub 建立發佈失敗，請確認網路連線後再執行一次（已經打包好的檔案會直接沿用）。
pause
exit /b 1
