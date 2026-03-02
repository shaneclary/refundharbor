@echo off
REM Quick launcher for DenseWealth paper trader (Windows)

echo.
echo ========================================
echo   DENSEWEALTH - Polymarket Paper Trader
echo ========================================
echo.

REM Check if Python is available
python --version >nul 2>&1
if errorlevel 1 (
    echo ERROR: Python not found
    echo Please install Python 3.10+ from python.org
    pause
    exit /b 1
)

REM Check if dependencies are installed
python -c "import httpx" >nul 2>&1
if errorlevel 1 (
    echo Installing dependencies...
    python -m pip install -r requirements.txt
    if errorlevel 1 (
        echo ERROR: Failed to install dependencies
        pause
        exit /b 1
    )
)

REM Run health check
echo Running health check...
echo.
python healthcheck.py
if errorlevel 1 (
    echo.
    echo Fix the issues above before starting.
    pause
    exit /b 1
)

REM Start the bot
echo.
echo Starting paper trader...
echo Press Ctrl+C to stop
echo.
python main.py

pause
