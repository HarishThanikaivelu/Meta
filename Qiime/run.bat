@echo off
title QIIME2 Lab Tool - Amplicon 2026.1
color 0A
cls

echo ============================================
echo   QIIME2 Lab Tool - Amplicon 2026.1
echo ============================================
echo.

REM Check if Python is available
python --version >nul 2>&1
if %errorlevel% neq 0 (
    echo [ERROR] Python was not found on your PATH.
    echo.
    echo Please do ONE of the following:
    echo.
    echo   Option A: Install Python from https://www.python.org/downloads/
    echo             IMPORTANT: tick "Add Python to PATH" during install.
    echo.
    echo   Option B: Open Anaconda Prompt, navigate here, and run:
    echo                 python qiime2_app.py
    echo.
    pause
    exit /b 1
)

echo [OK] Found:
python --version
echo.

REM Check if PyQt6 is installed, auto-install if missing
python -c "import PyQt6" >nul 2>&1
if %errorlevel% neq 0 (
    echo [INFO] PyQt6 not found. Installing now...
    echo.
    pip install PyQt6
    if %errorlevel% neq 0 (
        echo.
        echo [ERROR] Failed to install PyQt6.
        echo         Try manually:  pip install PyQt6
        echo.
        pause
        exit /b 1
    )
    echo.
    echo [OK] PyQt6 installed successfully.
    echo.
)

REM Check app file exists
if not exist "%~dp0qiime2_app.py" (
    echo [ERROR] qiime2_app.py not found in this folder.
    echo         Make sure run.bat and qiime2_app.py are in the same folder.
    echo.
    pause
    exit /b 1
)

REM Launch
echo Starting QIIME2 Lab Tool...
echo.
cd /d "%~dp0"
python qiime2_app.py

if %errorlevel% neq 0 (
    echo.
    echo [ERROR] The application exited with an error (code %errorlevel%).
    echo         Check the messages above for details.
    echo.
    pause
)
