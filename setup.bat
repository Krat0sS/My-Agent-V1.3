@echo off
chcp 65001 >nul 2>&1
cd /d "%~dp0"

echo.
echo   My Agent - Environment Setup
echo   ============================
echo.

REM === Find Python ===
set "PY_CMD="

py -3 --version >nul 2>&1
if %errorlevel% equ 0 ( set "PY_CMD=py -3" & goto :found )

python --version >nul 2>&1
if %errorlevel% equ 0 (
    python -c "import sys" >nul 2>&1
    if %errorlevel% equ 0 ( set "PY_CMD=python" & goto :found )
)

python3 --version >nul 2>&1
if %errorlevel% equ 0 ( set "PY_CMD=python3" & goto :found )

echo   [ERROR] Python not found!
echo   Install Python 3.10+ from https://www.python.org/downloads/
echo   Make sure to check "Add Python to PATH".
pause
exit /b 1

:found
echo   Python: %PY_CMD%
%PY_CMD% --version
echo.

REM === Create venv ===
if not exist "venv\Scripts\activate.bat" (
    echo   Creating venv ...
    %PY_CMD% -m venv venv
    if %errorlevel% neq 0 (
        echo   [ERROR] Failed to create venv!
        pause
        exit /b 1
    )
    echo   [OK] venv created.
) else (
    echo   venv already exists.
)

REM === Activate and Install ===
call venv\Scripts\activate.bat

echo   Installing dependencies ...
pip install -r requirements.txt -i https://mirrors.aliyun.com/pypi/simple/ --trusted-host mirrors.aliyun.com
if %errorlevel% neq 0 (
    echo   [WARN] Trying Tsinghua mirror ...
    pip install -r requirements.txt -i https://pypi.tuna.tsinghua.edu.cn/simple/ --trusted-host pypi.tuna.tsinghua.edu.cn
)
if %errorlevel% neq 0 (
    echo   [ERROR] pip install failed! Check network.
    pause
    exit /b 1
)

REM === Create .env ===
if not exist ".env" (
    if exist ".env.example" copy .env.example .env >nul
)

echo.
echo   ============================
echo   Setup complete!
echo   Run 启动.bat to start My Agent.
echo   ============================
pause
