@echo off
chcp 65001 >nul
echo ========================================
echo TTS MultiModel Voice Studio
echo ========================================
echo.

cd /d "%~dp0"

echo Checking Python installation...
python --version
if %errorlevel% neq 0 (
    echo ERROR: Python not found. Please install Python 3.8+
    pause
    exit /b 1
)

echo.
echo Starting application server...
echo.

set "PATH=%~dp0ffmpeg;%PATH%"

python -c "from integrated_app.app_server import run_server; run_server()"

if %errorlevel% neq 0 (
    echo.
    echo ERROR: Application failed to start. Check the log for details.
    pause
    exit /b 1
)
