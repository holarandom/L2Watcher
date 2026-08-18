@echo off
setlocal enabledelayedexpansion
title L2 Watcher - RELEASE build

REM ============================================================
REM   L2 Watcher - RELEASE builder (for users / GitHub)
REM   1) asks for new version and writes it to version.py
REM   2) runs the normal build (build.bat)
REM   3) packs dist\L2Watcher into L2Watcher_vX.X.X.zip
REM   Result zip is what you upload to GitHub Releases.
REM   For your own testing use build.bat instead.
REM ============================================================

cd /d "%~dp0"

echo.
echo ========================================
echo   L2 Watcher - RELEASE build
echo ========================================
echo.

REM -- show current version --
for /f "tokens=2 delims== " %%a in ('findstr /c:"APP_VERSION" version.py') do set CURVER=%%a
set CURVER=%CURVER:"=%
echo Current version: %CURVER%
echo.
set /p NEWVER=Enter NEW version (e.g. 1.0.1): 
if "%NEWVER%"=="" (
    echo No version entered. Aborted.
    pause
    exit /b 1
)

REM -- write new version into version.py (via temp script, quotes-safe) --
echo import re > _setver.py
echo s = open('version.py', encoding='utf-8').read() >> _setver.py
echo s = re.sub(r'APP_VERSION = "[^"]+"', 'APP_VERSION = "%NEWVER%"', s) >> _setver.py
echo open('version.py', 'w', encoding='utf-8').write(s) >> _setver.py
echo print('version.py -^> %NEWVER%') >> _setver.py
python _setver.py
set SETVER_RC=%errorlevel%
del _setver.py
if not "%SETVER_RC%"=="0" (
    echo FAILED to update version.py
    pause
    exit /b 1
)

echo.
echo [release] Running normal build...
call build.bat
set BUILD_RC=%errorlevel%

REM ВАЖНО: проверяем именно КОД ВОЗВРАТА build.bat, а не наличие exe.
REM Раньше проверялось только "лежит ли L2Watcher.exe" — и когда сборка
REM падала (например приложение было запущено и файлы оказались заняты),
REM в dist оставался СТАРЫЙ exe, проверка его находила, и релиз спокойно
REM паковал в архив предыдущую сборку. Именно так уехал битый zip.
if not "%BUILD_RC%"=="0" (
    echo.
    echo ============================================================
    echo   [ERROR] Build failed with code %BUILD_RC%. Release aborted.
    echo   Scroll up for the reason, or open build_log.txt
    echo ============================================================
    echo.
    pause
    exit /b 1
)

if not exist "dist\L2Watcher\L2Watcher.exe" (
    echo Build failed - L2Watcher.exe not found. See build_log.txt
    pause
    exit /b 1
)

echo.
echo [release] Removing dev files from dist (feedback receiver)...
if exist "dist\L2Watcher\feedback_config.json" del "dist\L2Watcher\feedback_config.json"
if exist "dist\L2Watcher\feedback_receiver.py" del "dist\L2Watcher\feedback_receiver.py"

echo.
echo [release] Packing zip...
set ZIPNAME=L2Watcher_v%NEWVER%.zip
if exist "%ZIPNAME%" del "%ZIPNAME%"
REM -ErrorAction Stop + exit 1 обязательны: без них Compress-Archive пишет
REM ошибку красным, но возвращает КОД 0, и errorlevel её не ловит. Именно
REM так был собран неполный архив без base_library.zip — скрипт отрапортовал
REM об успехе.
powershell -NoProfile -Command "try { Compress-Archive -Path 'dist\L2Watcher\*' -DestinationPath '%ZIPNAME%' -Force -ErrorAction Stop } catch { Write-Output $_.Exception.Message; exit 1 }"
if errorlevel 1 (
    echo.
    echo ============================================================
    echo   [ERROR] Zip packing failed. Release aborted.
    echo   Usually means a file in dist\ is locked - is the app running?
    echo ============================================================
    echo.
    pause
    exit /b 1
)

REM -- Сверяем: в архиве должно быть столько же файлов, сколько в dist. --
REM -- Иначе архив неполный, а внешне выглядит нормальным.             --
echo     Verifying archive contents...
REM ВНИМАНИЕ: труба внутри кавычек пишется БЕЗ ^. Внутри "..." cmd её и так
REM не трогает, а "^|" уезжает в PowerShell литералом и роняет разбор строки
REM ("Unexpected token '^'") — из-за этого падала упаковка релиза.
powershell -NoProfile -Command "$d=(Get-ChildItem 'dist\L2Watcher' -Recurse -File).Count; Add-Type -A System.IO.Compression.FileSystem; $z=[IO.Compression.ZipFile]::OpenRead((Resolve-Path '%ZIPNAME%')); $c=($z.Entries | Where-Object { $_.Name -ne '' }).Count; $z.Dispose(); Write-Output ('     dist: ' + $d + ' files, zip: ' + $c + ' files'); if ($c -lt $d) { Write-Output '     MISMATCH - archive is incomplete'; exit 1 }"
if errorlevel 1 (
    echo.
    echo ============================================================
    echo   [ERROR] Archive is INCOMPLETE - do not publish it.
    echo   Close L2 Watcher completely and run the release again.
    echo ============================================================
    echo.
    pause
    exit /b 1
)

REM ============================================================
REM   SHA256 of the release zip.
REM   Publish it in the GitHub release notes: the exe is unsigned
REM   (a signing certificate costs 100+ USD/year and does not pay
REM   off here), so a published hash + VirusTotal link is what
REM   lets people check the download is genuine.
REM ============================================================
echo.
echo [release] SHA256 of %ZIPNAME%:
REM Без экранированных кавычек внутри -Command: cmd передаёт их в PowerShell
REM как попало, и строка ломается. Собираем текст конкатенацией.
powershell -NoProfile -Command "$h=(Get-FileHash '%ZIPNAME%' -Algorithm SHA256).Hash; Write-Output $h; $h + '  %ZIPNAME%' | Set-Content -Path '%ZIPNAME%.sha256.txt' -Encoding utf8"
echo     saved to %ZIPNAME%.sha256.txt
echo.
echo     Next: upload %ZIPNAME% to https://www.virustotal.com/gui/home/upload
echo     and put BOTH the hash and the VirusTotal link into the release notes.

echo.
echo [release] Restoring dev files into dist (feedback receiver)...
if exist "feedback_config.json" (
    copy /y "feedback_config.json" "dist\L2Watcher\" >nul 2>&1
    copy /y "feedback_receiver.py" "dist\L2Watcher\" >nul 2>&1
    echo [dev] feedback receiver files restored
)

echo.
echo ========================================
echo   DONE: %ZIPNAME%
echo   Upload this file to GitHub Releases.
echo   Your dist build stays ready to use.
echo ========================================
echo.
pause
