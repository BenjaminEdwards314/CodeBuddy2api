@echo off
REM CodeBuddy2API Desktop Launcher (Windows)
REM Double-click this file to start the desktop application.

cd /d "%~dp0"

where python >nul 2>&1
if errorlevel 1 (
    echo Python is not installed or not in PATH.
    pause
    exit /b 1
)

if not exist "venv" (
    echo First run: creating virtual environment...
    python -m venv venv
)

call venv\Scripts\activate.bat

python -c "import webview" >nul 2>&1
if errorlevel 1 (
    echo Installing dependencies, this may take a few minutes...
    python -m pip install --quiet --upgrade pip
    python -m pip install --quiet -r requirements.txt
)

echo Starting CodeBuddy2API Desktop...
python desktop.py
if errorlevel 1 pause
