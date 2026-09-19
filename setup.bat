@echo off
title YouTube Downloader — Setup
echo.
echo ============================================
echo   YouTube Playlist Downloader — Setup
echo ============================================
echo.

:: Check Python
python --version >nul 2>&1
if errorlevel 1 (
    echo [ERROR] Python not found. Download from https://python.org
    pause
    exit /b 1
)
echo [OK] Python found.

:: Install Python dependencies
echo.
echo Installing Python packages...
pip install "yt-dlp[default]" rich click --quiet --upgrade
if errorlevel 1 (
    echo [ERROR] pip install failed. Check your internet connection.
    pause
    exit /b 1
)
echo [OK] Python packages installed.

:: Check ffmpeg
ffmpeg -version >nul 2>&1
if errorlevel 1 (
    echo.
    echo [WARNING] ffmpeg not found on PATH.
    echo   yt-dlp needs ffmpeg to merge video and audio streams.
    echo.
    echo   Install options:
    echo   1. winget install Gyan.FFmpeg          (recommended)
    echo   2. choco install ffmpeg                (if you have Chocolatey)
    echo   3. Manual: https://ffmpeg.org/download.html
    echo.
    echo   After installing, restart this window and re-run setup.bat
) else (
    echo [OK] ffmpeg found.
)

:: Create a handy launcher shortcut in the same folder
echo.
echo Creating launcher scripts...

(
echo @echo off
echo python "%~dp0yt_downloader.py" %%*
) > "%~dp0yt.bat"

echo [OK] Launcher created: yt.bat
echo.
echo ============================================
echo   Setup complete!
echo.
echo   Usage examples:
echo     yt download "PLAYLIST_URL"
echo     yt download "PLAYLIST_URL" -q 1080p
echo     yt download "PLAYLIST_URL" -o "D:\Videos"
echo     yt channel "CHANNEL_URL"
echo     yt info    "PLAYLIST_URL"
echo     yt history
echo     yt config  --output "D:\Videos" --quality 720p
echo ============================================
echo.
pause
