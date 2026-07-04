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

REM build.bat may end with pause/nonzero code even on success -
REM so we check the actual result file instead of errorlevel.
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
powershell -NoProfile -Command "Compress-Archive -Path 'dist\L2Watcher\*' -DestinationPath '%ZIPNAME%' -Force"
if errorlevel 1 (
    echo Zip packing failed
    pause
    exit /b 1
)

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
