@echo off
cd /d %~dp0

if not exist "venv\Scripts\activate.bat" (
    echo [ERROR] venv not found. Run setup.bat first.
    pause
    exit /b 1
)

call venv\Scripts\activate.bat
echo Starting My Agent on http://localhost:8080 ...
echo Press Ctrl+C to stop.
echo.
python server.py --port 8080
if %errorlevel% neq 0 (
    echo.
    echo [ERROR] Server crashed. See error above.
)
pause
