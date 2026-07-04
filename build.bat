@echo off
setlocal enabledelayedexpansion
title L2 Watcher - Build EXE

REM ============================================================
REM   L2 Watcher - automatic EXE builder
REM   Just double-click this file. It will:
REM   1) check Python
REM   2) install required libraries
REM   3) build L2Watcher.exe
REM   4) open the folder with the result
REM   Put this .bat in the same folder as main.py
REM   All output is also written to build_log.txt
REM ============================================================

cd /d "%~dp0"

echo L2 Watcher build log > build_log.txt
echo Started: %date% %time% >> build_log.txt
echo. >> build_log.txt

echo.
echo ========================================
echo   L2 Watcher - building EXE
echo ========================================
echo.

echo [1/5] Checking Python...
python --version >> build_log.txt 2>&1
if errorlevel 1 (
    echo.
    echo [ERROR] Python not found. Install it from https://python.org
    echo [ERROR] Python not found >> build_log.txt
    echo.
    pause
    exit /b 1
)
python --version
echo     Python OK.
echo.

echo [2/5] Checking project files...
if not exist "main.py" (
    echo.
    echo [ERROR] main.py not found next to this .bat
    echo [ERROR] main.py not found >> build_log.txt
    echo.
    pause
    exit /b 1
)
echo     Files OK.
echo.

echo [3/5] Installing libraries (may take a few minutes)...
python -m pip install --upgrade pip >> build_log.txt 2>&1
python -m pip install -r requirements.txt >> build_log.txt 2>&1
if errorlevel 1 (
    echo.
    echo [ERROR] Failed to install libraries. Check internet, see build_log.txt
    echo.
    pause
    exit /b 1
)
echo     Libraries OK.
echo.

echo [4/5] Cleaning previous build...
if exist "build" rmdir /s /q "build"
if exist "dist" rmdir /s /q "dist"
if exist "L2Watcher.spec" del /q "L2Watcher.spec"
echo     Done.
echo.

echo     Preparing icon...
python -c "from PIL import Image; Image.open('tray_icon.png').save('app_icon.ico', sizes=[(16,16),(32,32),(48,48),(256,256)])" >> build_log.txt 2>&1
if exist "app_icon.ico" (
    set ICON_FLAG=--icon app_icon.ico
    echo     Icon ready.
) else (
    set ICON_FLAG=
    echo     Icon skipped ^(not critical^).
)
echo.

echo [5/5] Building L2Watcher.exe...
echo     ^(longest step, please wait^)
echo.

python -m PyInstaller ^
    --noconfirm ^
    --onedir ^
    --windowed ^
    --name "L2Watcher" ^
    %ICON_FLAG% ^
    --add-data "tray_icon.png;." ^
    --hidden-import "pystray._win32" ^
    --hidden-import "sv_ttk" ^
    --hidden-import "PIL._tkinter_finder" ^
    --hidden-import "win32gui" ^
    --hidden-import "win32ui" ^
    --hidden-import "win32con" ^
    --hidden-import "aiogram" ^
    --hidden-import "aiohttp" ^
    --collect-all "sv_ttk" ^
    --collect-submodules "aiogram" ^
    main.py >> build_log.txt 2>&1

if errorlevel 1 (
    echo.
    echo [ERROR] Build failed. Open build_log.txt and send it to the developer.
    echo.
    pause
    exit /b 1
)

echo.
echo ========================================
echo   DONE!
echo ========================================
echo.
echo   Result:  dist\L2Watcher\  (whole folder)
echo.
echo   Run dist\L2Watcher\L2Watcher.exe . Share the WHOLE folder (zip it).
echo.
echo Build finished OK >> build_log.txt

REM -- dev mode: copy feedback receiver files into dist if present --
if exist "feedback_config.json" (
    copy /y "feedback_config.json" "dist\L2Watcher\" >nul 2>&1
    copy /y "feedback_receiver.py" "dist\L2Watcher\" >nul 2>&1
    echo [dev] feedback receiver files copied to dist
)

if exist "dist\L2Watcher\L2Watcher.exe" (
    explorer "dist\L2Watcher"
) else (
    echo [WARNING] dist\L2Watcher\L2Watcher.exe not found though no error reported.
)

echo.
pause
