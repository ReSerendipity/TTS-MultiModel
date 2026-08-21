@echo off
chcp 65001 >nul
title TTS MultiModel

echo ============================================================
echo   TTS MultiModel - AI Voice Workshop Pro
echo ============================================================
echo.

:: Detect Python interpreter (prefer system Python, fallback to bundled WinPython)
set "PYTHON_CMD="

:: ============================================================
:: 0. First, prefer project-local .venv (isolated model env)
:: ============================================================
if exist "%~dp0.venv\Scripts\python.exe" (
    set "PYTHON_CMD=%~dp0.venv\Scripts\python.exe"
    echo [OK] Found project venv: %~dp0.venv\Scripts\python.exe
    goto :python_found
)

:: ============================================================
:: 1. Fallback: try system Python (shared, may be polluted)
:: ============================================================

:: 1a. Check common system Python installation paths
if exist "C:\Python312\python.exe" (
    set "PYTHON_CMD=C:\Python312\python.exe"
    echo [OK] Found system Python: C:\Python312\python.exe
    goto :python_found
)

if exist "C:\Python311\python.exe" (
    set "PYTHON_CMD=C:\Python311\python.exe"
    echo [OK] Found system Python: C:\Python311\python.exe
    goto :python_found
)

if exist "C:\Python310\python.exe" (
    set "PYTHON_CMD=C:\Python310\python.exe"
    echo [OK] Found system Python: C:\Python310\python.exe
    goto :python_found
)

if exist "C:\Program Files\Python312\python.exe" (
    set "PYTHON_CMD=C:\Program Files\Python312\python.exe"
    echo [OK] Found system Python: C:\Program Files\Python312\python.exe
    goto :python_found
)

if exist "C:\Users\%USERNAME%\AppData\Local\Programs\Python\Python312\python.exe" (
    set "PYTHON_CMD=C:\Users\%USERNAME%\AppData\Local\Programs\Python\Python312\python.exe"
    echo [OK] Found system Python (user-level)
    goto :python_found
)

:: 1b. Try PATH via `where python` - get the first one that's NOT in TRAE/IDE directories
for /f "delims=" %%i in ('where python 2^>nul') do (
    echo %%i | findstr /i "TRAE" >nul
    if errorlevel 1 (
        echo %%i | findstr /i "IDE" >nul
        if errorlevel 1 (
            set "PYTHON_CMD=%%i"
            echo [OK] Found system Python in PATH: %%i
            goto :python_found
        )
    )
)

:: ============================================================
:: 2. Fallback to bundled WinPython (legacy isolated mode)
:: ============================================================

:: 2a. Check WPy64-312101 (primary WinPython)
set "WP_DIR=%~dp0WPy64-312101"
if exist "%WP_DIR%\python\python.exe" (
    set "PYTHON_CMD=%WP_DIR%\python\python.exe"
    echo [OK] Found bundled WinPython 3.12.10
    goto :python_found
)

:: 2b. Search for any WPy64-* directory
for /d %%i in ("%~dp0WPy64-*") do (
    if exist "%%i\python\python.exe" (
        set "PYTHON_CMD=%%i\python\python.exe"
        echo [OK] Found bundled WinPython
        goto :python_found
    )
)

:: 2c. Search for WinPython64-* directory
for /d %%i in ("%~dp0WinPython64-*") do (
    for /d %%j in ("%%i\python-*.amd64") do (
        if exist "%%j\python.exe" (
            set "PYTHON_CMD=%%j\python.exe"
            echo [OK] Found bundled WinPython
            goto :python_found
        )
    )
)

:: 2d. Search for legacy WinPython directory
set "WP_LEGACY=%~dp0WinPython"
if exist "%WP_LEGACY%\python\python.exe" (
    set "PYTHON_CMD=%WP_LEGACY%\python\python.exe"
    echo [OK] Found bundled WinPython (legacy)
    goto :python_found
)

:: ============================================================
:: 3. No Python found at all
:: ============================================================
echo [ERROR] Python interpreter not found!
echo.
echo ============================================================
echo   You have two options:
echo ============================================================
echo.
echo   Option A (Recommended) - Use system Python:
echo     1. Install Python 3.10+ from https://www.python.org/downloads/
echo        Make sure to check "Add Python to PATH" during installation.
echo     2. Verify: open Command Prompt and run: python --version
echo     3. Run install.bat to install dependencies
echo     4. Then re-run start.bat
echo.
echo   Option B - Use bundled WinPython (isolated):
echo     1. Download WinPython from:
echo        https://github.com/winpython/winpython/releases
echo     2. Extract to project directory so this exists:
echo        %~dp0WPy64-312101\python\python.exe
echo     3. Then re-run start.bat
echo.
echo ============================================================
pause
exit /b 1

:python_found
echo Using Python: %PYTHON_CMD%
echo.

if not exist "%~dp0app\clean_launch.py" (
    echo Error: Launch script not found at app\clean_launch.py
    pause
    exit /b 1
)

echo Starting TTS MultiModel...
echo.

cd /d "%~dp0"
"%PYTHON_CMD%" app\clean_launch.py

if errorlevel 1 (
    echo.
    echo Application exited with error.
    pause
)
