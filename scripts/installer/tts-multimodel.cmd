@echo off
REM ============================================================================
REM  tts-multimodel.cmd - CLI launcher for the TTSMultiModel desktop installer
REM ============================================================================
REM  Installed to <InstallDir>\tts-multimodel.cmd by setup.nsi and registered
REM  on the current user's PATH, so `tts-multimodel ...` works in any terminal
REM  (and for AI agents that shell out). It runs the bundled cli.py with the
REM  in-app bootstrap interpreter (same Python that carries the downloaded
REM  torch/runtime), NOT the system Python.
REM
REM  Layout after install + in-app "download manager" setup:
REM    <InstallDir>\app\bootstrap\python.exe        (bootstrap interpreter)
REM    <InstallDir>\app\app\integrated_app\cli.py   (CLI source, app/app two-level)
REM    <InstallDir>\app\model\VoxCPM2\              (default local weights)
REM
REM  Keep this file ASCII-only: it is a .cmd parsed under the active code page.
REM ============================================================================
setlocal EnableExtensions

set "APPROOT=%~dp0app"
set "PYEXE=%APPROOT%\bootstrap\python.exe"
set "CLIPY=%APPROOT%\app\integrated_app\cli.py"

if not exist "%PYEXE%" (
    echo [tts-multimodel] ERROR: bootstrap interpreter not found: %PYEXE% >&2
    echo [tts-multimodel] Open the desktop app and finish "Download Manager" -^> runtime install first. >&2
    exit /b 1
)
if not exist "%CLIPY%" (
    echo [tts-multimodel] ERROR: CLI entry not found: %CLIPY% >&2
    exit /b 1
)

REM --- Environment: align with start_portable.py (offline + local caches) ---
set "TRANSFORMERS_OFFLINE=1"
set "HF_HUB_OFFLINE=1"
set "MODELSCOPE_OFFLINE=1"
set "KMP_DUPLICATE_LIB_OK=True"
set "HUGGINGFACE_HUB_CACHE=%APPROOT%\cache\huggingface"
set "MODELSCOPE_CACHE=%APPROOT%\cache\modelscope"
set "TORCH_HOME=%APPROOT%\cache\torch"
set "XDG_CACHE_HOME=%APPROOT%\cache"

REM --- Default to the bundled local VoxCPM2 weights unless the caller has
REM     explicitly chosen a model source (--model-path / --hf-model-id). ---
set "DEFAULT_MODEL=%APPROOT%\model\VoxCPM2"
set "HAS_MODEL_OPT="
for %%A in (%*) do (
    if /I "%%~A"=="--model-path" set "HAS_MODEL_OPT=1"
    if /I "%%~A"=="--hf-model-id" set "HAS_MODEL_OPT=1"
)
if not defined HAS_MODEL_OPT (
    if exist "%DEFAULT_MODEL%\config.json" (
        set "ARGS=--model-path "%DEFAULT_MODEL%" %*"
    ) else (
        set "ARGS=%*"
    )
) else (
    set "ARGS=%*"
)

"%PYEXE%" "%CLIPY%" %ARGS%
exit /b %ERRORLEVEL%
