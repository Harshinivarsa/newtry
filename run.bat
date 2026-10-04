@echo off
title ESP32 Function Generator + Oscilloscope System
cd /d "%~dp0"

IF NOT EXIST ".venv" (
    echo Creating Python virtual environment .venv...
    python -m venv .venv
    IF ERRORLEVEL 1 (
        echo Failed to create virtual environment!
        pause
        exit /b 1
    )
)

echo Activating virtual environment...
call .venv\Scripts\activate.bat

echo Installing dependencies from requirements.txt...
pip install -r requirements.txt
IF ERRORLEVEL 1 (
    echo Failed to install requirements!
    pause
    exit /b 1
)

echo Starting Application...
python main.py
pause
