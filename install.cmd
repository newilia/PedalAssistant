@echo off
title PedalAssistant - Install

echo ========================================
echo    PedalAssistant - Install
echo ========================================
echo.

cd /d "%~dp0"

:: Check Python
python --version >nul 2>&1
if errorlevel 1 (
    echo [ERROR] Python not found!
    echo Please install Python 3.8+ from https://python.org
    echo.
    pause
    exit /b 1
)

echo [*] Upgrading pip...
python -m pip install --upgrade pip

echo.
echo [*] Installing dependencies...
echo.
python -m pip install -r requirements.txt

if errorlevel 1 (
    echo.
    echo [ERROR] Failed to install dependencies!
    pause
    exit /b 1
)

echo.
echo ========================================
echo [OK] Installation complete!
echo ========================================
echo.
echo You can now run the program using run.cmd
echo.
pause
