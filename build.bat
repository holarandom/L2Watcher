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

REM ============================================================
REM   Running app check. This is THE most common cause of a
REM   silently broken build: a running L2Watcher.exe locks files
REM   in dist\, so "rmdir dist" quietly fails, PyInstaller cannot
REM   overwrite the exe, tkinter data never gets written, and the
REM   zip step later chokes on a locked base_library.zip.
REM   The build "succeeds" and ships broken. Stop it right here.
REM ============================================================
echo     Checking that L2 Watcher is not running...
tasklist /FI "IMAGENAME eq L2Watcher.exe" /NH 2>nul | find /I "L2Watcher.exe" >nul
if not errorlevel 1 (
    echo.
    echo ============================================================
    echo   [ERROR] L2Watcher.exe is RUNNING. Cannot build.
    echo.
    echo   A running app locks files in dist\ and the build would
    echo   silently produce a BROKEN package.
    echo.
    echo   Close it properly: tray icon - right click - Exit.
    echo   Or force it from here:
    echo       taskkill /IM L2Watcher.exe /F
    echo ============================================================
    echo.
    pause
    exit /b 1
)
echo     Not running, OK.

REM -- Leftover feedback receivers (dev mode). They are separate python --
REM -- processes spawned by the app; taskkill on L2Watcher.exe does NOT --
REM -- take them down. An orphan holds dist\ locked and the build dies  --
REM -- with "file is used by another process".                          --
echo     Stopping leftover feedback receivers...
powershell -NoProfile -Command "$p=Get-CimInstance Win32_Process -Filter \"Name='pythonw.exe'\" | Where-Object { $_.CommandLine -like '*feedback_receiver*' }; if ($p) { $p | ForEach-Object { Stop-Process -Id $_.ProcessId -Force }; Write-Output ('     stopped: ' + $p.Count) } else { Write-Output '     none found' }"
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

echo     Preparing version metadata...
REM Пустые свойства файла (издатель/продукт/версия) - отдельный минус в
REM глазах ML-движка Defender. Генерируем метаданные из version.py.
python make_version_file.py >> build_log.txt 2>&1
if exist "file_version_info.txt" (
    set VERSION_FLAG=--version-file file_version_info.txt
    echo     Version metadata ready.
) else (
    set VERSION_FLAG=
    echo     [WARN] Version metadata skipped - exe will have empty properties.
)
echo.

echo     Preparing icon...
python -c "from PIL import Image; Image.open('tray_icon.png').convert('RGBA').save('app_icon.ico', sizes=[(16,16),(32,32),(48,48),(256,256)])" >> build_log.txt 2>&1
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
    %VERSION_FLAG% ^
    --noupx ^
    --add-data "tray_icon.png;." ^
    --add-data "app_icon.ico;." ^
    --hidden-import "pystray._win32" ^
    --hidden-import "sv_ttk" ^
    --hidden-import "PIL._tkinter_finder" ^
    --hidden-import "win32gui" ^
    --hidden-import "win32ui" ^
    --hidden-import "win32con" ^
    --hidden-import "win32crypt" ^
    --hidden-import "win32com.client" ^
    --hidden-import "pythoncom" ^
    --hidden-import "pywintypes" ^
    --hidden-import "aiogram" ^
    --hidden-import "aiohttp" ^
    --hidden-import "_tkinter" ^
    --hidden-import "tkinter" ^
    --hidden-import "tkinter.ttk" ^
    --hidden-import "tkinter.messagebox" ^
    --hidden-import "tkinter.filedialog" ^
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

REM ============================================================
REM   Post-build check: tkinter must be really bundled.
REM   Without this the build "succeeds" but the Settings window
REM   crashes with "Tcl data directory ... _tcl_data not found",
REM   and you only find out after shipping it to users.
REM ============================================================
echo.
echo     Verifying tkinter is bundled...
set TK_OK=1
if not exist "dist\L2Watcher\_internal\_tcl_data" set TK_OK=0
if not exist "dist\L2Watcher\_internal\_tk_data"  set TK_OK=0
REM ВНИМАНИЕ: внутри блока if (...) НЕЛЬЗЯ писать голую закрывающую скобку
REM в echo — cmd примет её за конец блока и свалится с "was unexpected at
REM this time", оборвав весь скрипт. Поэтому нумерация точками, а не "1)".
if "%TK_OK%"=="0" (
    echo.
    echo ============================================================
    echo   [ERROR] tkinter was NOT bundled into the build.
    echo.
    echo   The app will start, but the Settings window will crash
    echo   with: Tcl data directory ..._internal\_tcl_data not found
    echo.
    echo   Most common causes:
    echo     1. L2 Watcher was RUNNING during the build. Close it
    echo        completely via tray icon - Exit, then rebuild.
    echo     2. Antivirus quarantined tcl86t.dll / tk86t.dll.
    echo        Check the AV quarantine and whitelist this folder.
    echo     3. Leftovers in dist. Delete build\ and dist\, rebuild.
    echo ============================================================
    echo.
    pause
    exit /b 1
)
echo     tkinter OK.

REM -- Metadata check: empty file properties are one of the reasons --
REM -- Defender's ML engine flags the build as Wacatac.            --
echo.
echo     Verifying exe metadata...
powershell -NoProfile -Command "$v=(Get-Item 'dist\L2Watcher\L2Watcher.exe').VersionInfo; if ([string]::IsNullOrWhiteSpace($v.ProductName)) { Write-Output '     [WARN] exe properties are EMPTY - rebuild after checking make_version_file.py'; exit 1 } else { Write-Output ('     Product: ' + $v.ProductName + ' ' + $v.FileVersion + ' by ' + $v.CompanyName) }"

REM -- SHA256 of the exe: publish it next to the release so users --
REM -- can verify the download was not tampered with.             --
echo.
echo     SHA256 of L2Watcher.exe:
powershell -NoProfile -Command "(Get-FileHash 'dist\L2Watcher\L2Watcher.exe' -Algorithm SHA256).Hash"

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

REM ВАЖНО: явный успешный код возврата.
REM explorer.exe всегда завершается с кодом 1, даже когда открыл папку
REM нормально, а pause errorlevel не сбрасывает. Из-за этого build.bat
REM отдавал наверх "1", и build_release.bat считал успешную сборку
REM провалившейся и не доходил до упаковки архива.
exit /b 0
