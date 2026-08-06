@echo off
echo ========================================
echo  Cleanup Cache and Temporary Files
echo ========================================
echo.
echo This script will delete:
echo   1. temp_indextts\       (35MB - IndexTTS2 source clone)
echo   2. .mypy_cache\         (46MB - type checking cache)
echo   3. torch_compile_cache\ (empty directory)
echo   4. .uploads\            (empty directory)
echo.
echo NOTE: reference_repos\ will NOT be deleted
echo.
echo ========================================
echo.
pause

echo Deleting temp_indextts...
if exist "temp_indextts" (
    rmdir /s /q "temp_indextts"
    if not exist "temp_indextts" (
        echo   [OK] Deleted temp_indextts
    ) else (
        echo   [FAIL] Could not delete temp_indextts (permission denied?)
    )
) else (
    echo   [SKIP] temp_indextts not found
)

echo Deleting .mypy_cache...
if exist ".mypy_cache" (
    rmdir /s /q ".mypy_cache"
    if not exist ".mypy_cache" (
        echo   [OK] Deleted .mypy_cache
    ) else (
        echo   [FAIL] Could not delete .mypy_cache (permission denied?)
    )
) else (
    echo   [SKIP] .mypy_cache not found
)

echo Deleting torch_compile_cache...
if exist "torch_compile_cache" (
    rmdir /s /q "torch_compile_cache"
    if not exist "torch_compile_cache" (
        echo   [OK] Deleted torch_compile_cache
    ) else (
        echo   [FAIL] Could not delete torch_compile_cache (permission denied?)
    )
) else (
    echo   [SKIP] torch_compile_cache not found
)

echo Deleting .uploads...
if exist ".uploads" (
    rmdir /s /q ".uploads"
    if not exist ".uploads" (
        echo   [OK] Deleted .uploads
    ) else (
        echo   [FAIL] Could not delete .uploads (permission denied?)
    )
) else (
    echo   [SKIP] .uploads not found
)

echo.
echo ========================================
echo  Cleanup Complete!
echo ========================================
echo.
pause
