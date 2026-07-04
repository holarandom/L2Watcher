@echo off
setlocal enabledelayedexpansion
title L2 Watcher - Clean junk

REM ============================================================
REM   Cleans build junk and temporary files from the folder.
REM   SAFE: does NOT touch source code, templates, tray_icon.png,
REM   config, or the dist folder with your built exe.
REM   Put this .bat in the project folder and run it.
REM ============================================================

cd /d "%~dp0"

echo.
echo ========================================
echo   L2 Watcher - folder cleanup
echo ========================================
echo.
echo This will DELETE the following junk (if present):
echo.
echo   [folders]
echo     __pycache__\      (Python cache)
echo     build\            (PyInstaller temp)
echo.
echo   [files]
echo     *.spec            (PyInstaller specs, incl. old L2Monitor.spec)
echo     build_log.txt     (build log)
echo     app_icon.ico      (generated from png each build)
echo     6_*.png 7_*.png 8_*.png  (demo screenshots)
echo.
echo It will NOT touch:
echo   - any .py source files
echo   - tray_icon.png, template_*.png  (needed files)
echo   - config.json, *.log
echo   - dist\  (your built exe)
echo.
set /p CONFIRM="Type Y and press Enter to clean (anything else cancels): "
if /i not "%CONFIRM%"=="Y" (
    echo.
    echo Cancelled. Nothing deleted.
    echo.
    pause
    exit /b 0
)

echo.
echo Cleaning...

REM ---- Folders ----
if exist "__pycache__" (
    rmdir /s /q "__pycache__"
    echo   removed __pycache__\
)
if exist "build" (
    rmdir /s /q "build"
    echo   removed build\
)

REM ---- Files ----
if exist "*.spec" (
    del /q "*.spec"
    echo   removed *.spec
)
if exist "build_log.txt" (
    del /q "build_log.txt"
    echo   removed build_log.txt
)
if exist "app_icon.ico" (
    del /q "app_icon.ico"
    echo   removed app_icon.ico
)

REM ---- Demo screenshots (exact prefixes only, never tray/template png) ----
for %%F in ("6_*.png" "7_*.png" "8_*.png") do (
    if exist "%%~F" (
        del /q "%%~F"
        echo   removed %%~F
    )
)

echo.
echo ========================================
echo   Done. Folder cleaned.
echo ========================================
echo.
pause
