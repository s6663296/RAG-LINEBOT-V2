@echo off
SETLOCAL EnableDelayedExpansion

:: Set Window Title
TITLE RAG LINEBOT Launcher

:: Set console to UTF-8 for better character support
chcp 65001 >nul

echo ========================================
echo   Control Panel - Quick Start
echo ========================================

:: Navigate to the backend directory
:: %~dp0 ensures it finds the folder relative to the script location
cd /d "%~dp0backend"

:: Check if virtual environment exists
if not exist venv (
    echo [INFO] Virtual environment not found. Creating venv...
    python -m venv venv
    if !errorlevel! neq 0 (
        echo [ERROR] Failed to create virtual environment. 
        echo [ERROR] Please ensure Python is installed and added to PATH.
        goto :error
    )
)

:: Activate the virtual environment
echo [INFO] Activating virtual environment...
call venv\Scripts\activate
if !errorlevel! neq 0 (
    echo [ERROR] Failed to activate virtual environment.
    goto :error
)

:: Install dependencies
echo [INFO] Checking and installing dependencies...
pip install -r requirements.txt
if !errorlevel! neq 0 (
    echo [ERROR] Failed to install requirements.
    goto :error
)

:: Run the application
echo [INFO] Starting FastAPI server...
echo [HINT] Once running, visit: http://localhost:8080
python main.py

if !errorlevel! neq 0 (
    echo [CRASH] Application exited with code !errorlevel!
    goto :error
)

goto :end

:error
echo ========================================
echo   An error occurred during execution.
echo ========================================
pause
exit /b !errorlevel!

:end
echo Application closed normally.
pause