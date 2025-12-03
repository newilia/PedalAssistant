@echo off
chcp 65001 >nul
title PedalAssistant - Установка

echo ========================================
echo    PedalAssistant - Установка
echo ========================================
echo.

cd /d "%~dp0"

:: Проверка наличия Python
python --version >nul 2>&1
if errorlevel 1 (
    echo [ОШИБКА] Python не найден!
    echo Пожалуйста, установите Python 3.8+ с https://python.org
    echo.
    pause
    exit /b 1
)

echo [*] Обновление pip...
python -m pip install --upgrade pip

echo.
echo [*] Установка зависимостей...
echo.
python -m pip install -r requirements.txt

if errorlevel 1 (
    echo.
    echo [ОШИБКА] Не удалось установить зависимости!
    pause
    exit /b 1
)

echo.
echo ========================================
echo [OK] Установка завершена успешно!
echo ========================================
echo.
echo Теперь можно запустить программу через run.cmd
echo.
pause

