@echo off
chcp 65001 >nul
title PedalAssistant

echo ========================================
echo       PedalAssistant - Запуск
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

:: Проверка и установка зависимостей
echo [*] Проверка зависимостей...

python -c "import pygame, customtkinter, numpy, sounddevice" >nul 2>&1
if errorlevel 1 (
    echo [*] Установка необходимых пакетов...
    echo.
    python -m pip install --upgrade pip >nul 2>&1
    python -m pip install -r requirements.txt
    if errorlevel 1 (
        echo.
        echo [ОШИБКА] Не удалось установить зависимости!
        pause
        exit /b 1
    )
    echo.
    echo [OK] Зависимости установлены!
) else (
    echo [OK] Все зависимости уже установлены
)

echo.
echo [*] Запуск PedalAssistant...
echo.

:: Запуск программы
python pedal_assistant.py

if errorlevel 1 (
    echo.
    echo [ОШИБКА] Программа завершилась с ошибкой
    pause
)
