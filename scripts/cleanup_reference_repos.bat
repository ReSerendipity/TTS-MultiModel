@echo off
echo ========================================
echo  Cleanup Reference Repositories
echo ========================================
echo.
echo This script will delete the following folders:
echo   1. chatterbox
echo   2. ChatTTS
echo   3. CosyVoice
echo   4. fish-speech
echo   5. GPT-SoVITS
echo.
echo The following folder will be KEPT:
echo   - OpenVoice
echo   - VoxCPM
echo.
echo ========================================
echo.
pause

echo.
echo Deleting chatterbox...
if exist "reference_repos\chatterbox" (
    rmdir /s /q "reference_repos\chatterbox"
    echo   [OK] Deleted chatterbox
) else (
    echo   [SKIP] chatterbox not found
)

echo Deleting ChatTTS...
if exist "reference_repos\ChatTTS" (
    rmdir /s /q "reference_repos\ChatTTS"
    echo   [OK] Deleted ChatTTS
) else (
    echo   [SKIP] ChatTTS not found
)

echo Deleting CosyVoice...
if exist "reference_repos\CosyVoice" (
    rmdir /s /q "reference_repos\CosyVoice"
    echo   [OK] Deleted CosyVoice
) else (
    echo   [SKIP] CosyVoice not found
)

echo Deleting fish-speech...
if exist "reference_repos\fish-speech" (
    rmdir /s /q "reference_repos\fish-speech"
    echo   [OK] Deleted fish-speech
) else (
    echo   [SKIP] fish-speech not found
)

echo Deleting GPT-SoVITS...
if exist "reference_repos\GPT-SoVITS" (
    rmdir /s /q "reference_repos\GPT-SoVITS"
    echo   [OK] Deleted GPT-SoVITS
) else (
    echo   [SKIP] GPT-SoVITS not found
)

echo.
echo ========================================
echo  Cleanup Complete!
echo ========================================
echo.
echo Remaining folders in reference_repos:
dir /b "reference_repos" 2>nul
echo.
pause
