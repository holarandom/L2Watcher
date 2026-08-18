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
echo   - tray_icon.png, template_*.png, l2watcher_*_256.png
echo   - config.json, feedback_config.json, *.log
echo   - docs\, .git\
echo.
echo After this it will ASK SEPARATELY about heavy leftovers
echo (old release zips, unpacked builds, dist\, UI mockups).
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
if exist "file_version_info.txt" (
    del /q "file_version_info.txt"
    echo   removed file_version_info.txt
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
echo   Basic junk cleaned.
echo ========================================
echo.

REM ============================================================
REM   PART 2 - heavy leftovers. Asked separately because these
REM   are big but not obviously junk: you may still want them.
REM   Current version's zip is ALWAYS kept.
REM ============================================================

REM -- read current version from version.py --
set CURVER=
for /f "tokens=2 delims== " %%a in ('findstr /c:"APP_VERSION" version.py') do set CURVER=%%a
set CURVER=%CURVER:"=%

echo ========================================
echo   Heavy leftovers
echo ========================================
echo.
echo These take the most space. Old release zips are ~78 MB EACH
echo and are already published on GitHub Releases - you can always
echo download them back from there.
echo.
echo   L2Watcher_v*.zip     old release archives
echo                        (KEEPING current: L2Watcher_v%CURVER%.zip)
echo   L2Watcher_v*\         unpacked old builds
echo   dist\                 last build output (rebuild anytime)
echo   L2Watcher UI mockups.zip   design mockups (already applied)
echo.
echo NOT touched here either: docs, .git
echo.
set /p CONFIRM2="Type Y and press Enter to delete these too (anything else skips): "
if /i not "%CONFIRM2%"=="Y" (
    echo.
    echo Skipped. Heavy files left in place.
    echo.
    pause
    exit /b 0
)

echo.
echo Cleaning heavy leftovers...

REM ---- old release zips, keeping the current version ----
for %%F in ("L2Watcher_v*.zip") do (
    if /i not "%%~nxF"=="L2Watcher_v%CURVER%.zip" (
        del /q "%%~F"
        echo   removed %%~nxF
    )
)

REM ---- unpacked old build folders ----
for /d %%D in ("L2Watcher_v*") do (
    rmdir /s /q "%%~D"
    echo   removed %%~nxD\
)

REM ---- last build output ----
if exist "dist" (
    rmdir /s /q "dist"
    echo   removed dist\
)

REM ---- design mockups ----
if exist "L2Watcher UI mockups.zip" (
    del /q "L2Watcher UI mockups.zip"
    echo   removed L2Watcher UI mockups.zip
)

echo.
echo ========================================
echo   Done. Folder cleaned.
echo ========================================
echo.
pause
