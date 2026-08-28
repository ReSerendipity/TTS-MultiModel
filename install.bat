@echo off
chcp 65001 >nul
title TTS MultiModel - Installer

echo ============================================================
echo   TTS MultiModel - Installation Script
echo ============================================================
echo.
echo This script will:
echo   1. Check Python environment (system Python preferred)
echo   2. Install required dependencies
echo   3. Create required directories
echo   4. Guide you to download required models
echo.
echo ============================================================
echo.

cd /d "%~dp0"

:: Detect Python interpreter (prefer system Python, fallback to bundled WinPython)
set "PYTHON_CMD="

:: ============================================================
:: 1. First, try system Python (preferred)
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
if exist "WPy64-312101\python\python.exe" (
    set "PYTHON_CMD=%~dp0WPy64-312101\python\python.exe"
    echo [OK] Found bundled Python: WPy64-312101
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
echo     3. Then re-run install.bat
echo.
echo   Option B - Use bundled WinPython (isolated):
echo     1. Download WinPython from:
echo        https://github.com/winpython/winpython/releases
echo     2. Extract to project directory so this exists:
echo        %~dp0WPy64-312101\python\python.exe
echo     3. Then re-run install.bat
echo.
echo ============================================================
pause
exit /b 1

:python_found
echo.
echo ============================================================
echo   Step 1: Installing Python Dependencies
echo ============================================================
echo.
echo Using Python: %PYTHON_CMD%
"%PYTHON_CMD%" --version
echo.

if exist "requirements.txt" (
    echo Upgrading pip first...
    "%PYTHON_CMD%" -m pip install --upgrade pip
    echo.
    echo Installing PyTorch with CUDA 13.2 support (recommended)...
    echo   If download is too slow, download the .whl files manually:
    echo   torch-2.13.0+cu132: https://download-r2.pytorch.org/whl/cu132/torch-2.13.0%%2Bcu132-cp312-cp312-win_amd64.whl
    echo   torchvision-0.28.0+cu132: https://download-r2.pytorch.org/whl/cu132/torchvision-0.28.0%%2Bcu132-cp312-cp312-win_amd64.whl
    echo   Then install locally: pip install torch-*.whl torchvision-*.whl torchaudio
    echo   NOTE: torchaudio displays "+cpu" tag - this is NORMAL. Official cu132
    echo   index has no Windows cp312 torchaudio build. GPU support comes from
    echo   the underlying torch+cu132 and has been verified working.
    echo.
    "%PYTHON_CMD%" -m pip install torch torchvision torchaudio --index-url https://download.pytorch.org/whl/cu132 --timeout 1200 --retries 10
    echo.
    echo Installing dependencies from requirements.txt...
    "%PYTHON_CMD%" -m pip install -r requirements.txt --timeout 300 --retries 3
    if errorlevel 1 (
        echo.
        echo [ERROR] Failed to install dependencies
        pause
        exit /b 1
    )
    echo [OK] Dependencies installed successfully
) else (
    echo [WARNING] requirements.txt not found, skipping dependency installation
)

echo.
echo ============================================================
echo   Step 2: Creating Required Directories
echo ============================================================
echo.

if not exist "model" mkdir "model"
if not exist "personas" mkdir "personas"
if not exist "outputs" mkdir "outputs"
if not exist "cache" mkdir "cache"
if not exist "lora" mkdir "lora"
if not exist "logs" mkdir "logs"

echo [OK] Required directories created

echo.
echo ============================================================
echo   Step 3: Model Download Guide
echo ============================================================
echo.
echo IMPORTANT: You need to download the following models before using this app.
echo.
echo Models should be placed in the model folder:
echo.
echo   1. VoxCPM2 (Main TTS model)
echo      - Place in: model\VoxCPM2\
echo.
echo   2. SenseVoiceSmall (ASR model)
echo      - Place in: model\SenseVoiceSmall\
echo.
echo   3. speech_zipenhancer (Audio denoiser)
echo      - Place in: model\speech_zipenhancer\
echo.
echo Download links:
echo   - HuggingFace: https://huggingface.co
echo   - ModelScope: https://modelscope.cn
echo.
echo Or use the model download script (if available):
if exist "scripts\download_indextts2.py" (
    echo.
    echo Running download scripts...
    "%PYTHON_CMD%" scripts\download_indextts2.py
) else (
    echo [INFO] Download script available: scripts\download_indextts2.py
)

echo.
echo ============================================================
echo   Step 4: Verification
echo ============================================================
echo.

set "ALL_MODELS_OK=1"

if exist "model\VoxCPM2" (
    echo [OK] VoxCPM2 model found
) else (
    echo [MISSING] VoxCPM2 model not found
    set "ALL_MODELS_OK=0"
)

if exist "model\SenseVoiceSmall" (
    echo [OK] SenseVoiceSmall model found
) else (
    echo [MISSING] SenseVoiceSmall model not found
    set "ALL_MODELS_OK=0"
)

if exist "model\speech_zipenhancer" (
    echo [OK] speech_zipenhancer model found
) else (
    echo [MISSING] speech_zipenhancer model not found
    set "ALL_MODELS_OK=0"
)

echo.

if "%ALL_MODELS_OK%"=="1" (
    echo ============================================================
    echo   Installation Complete!
    echo ============================================================
    echo.
    echo You can now start the application by running:
    echo   start.bat
    echo.
) else (
    echo ============================================================
    echo   Installation Partially Complete
    echo ============================================================
    echo.
    echo Some models are missing. Please download them before starting.
    echo.
    echo You can still try starting by running:
    echo   start.bat
    echo.
)

echo ============================================================
echo.
pause
