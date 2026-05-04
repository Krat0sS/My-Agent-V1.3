@echo off
chcp 65001 >nul 2>&1
cd /d "%~dp0"

if not exist "venv\Scripts\activate.bat" (
    echo   [ERROR] venv not found! Run 启动.bat first.
    pause
    exit /b 1
)

call venv\Scripts\activate.bat

echo.
echo   My Agent starting at http://localhost:8080
echo   Press Ctrl+C to stop.
echo.

start "" "http://localhost:8080"
python server.py --port 8080

if %errorlevel% neq 0 (
    echo.
    echo   [ERROR] Server crashed. See error above.
    pause
)
