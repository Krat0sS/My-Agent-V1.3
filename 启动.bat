@echo off
chcp 65001 >nul 2>&1
cd /d "%~dp0"
set HTTP_PROXY=
set HTTPS_PROXY=
set http_proxy=
set https_proxy=
set ALL_PROXY=
set all_proxy=

echo.
echo   ========================================
echo          My Agent v1.3.5
echo   ========================================
echo.

REM === Step 1: Find Python (优先 3.12 > 3.13 > 3.14 > 默认) ===
set "PY_CMD="

py -3.12 --version >nul 2>&1
if %errorlevel% equ 0 (
    set "PY_CMD=py -3.12"
    goto :python_found
)

py -3.13 --version >nul 2>&1
if %errorlevel% equ 0 (
    set "PY_CMD=py -3.13"
    goto :python_found
)

py -3.14 --version >nul 2>&1
if %errorlevel% equ 0 (
    set "PY_CMD=py -3.14"
    goto :python_found
)

py -3 --version >nul 2>&1
if %errorlevel% equ 0 (
    set "PY_CMD=py -3"
    goto :python_found
)

python --version >nul 2>&1
if %errorlevel% equ 0 (
    set "PY_CMD=python"
    goto :python_found
)

python3 --version >nul 2>&1
if %errorlevel% equ 0 (
    set "PY_CMD=python3"
    goto :python_found
)

echo   [ERROR] Python not found!
echo.
echo   Please install Python 3.12 from:
echo   https://www.python.org/downloads/
echo.
echo   IMPORTANT: Check "Add Python to PATH" during install!
echo.
pause
exit /b 1

:python_found
echo   [OK] Python found: %PY_CMD%
%PY_CMD% --version
echo.

REM === Step 2: Create venv if needed ===
if exist "venv\Scripts\activate.bat" goto :venv_ready

echo   [1/3] Creating virtual environment ...
%PY_CMD% -m venv venv
if %errorlevel% neq 0 (
    echo.
    echo   [ERROR] Failed to create venv!
    echo   Try running manually: %PY_CMD% -m venv venv
    pause
    exit /b 1
)
echo   [OK] venv created.
echo.

:venv_ready
REM === Step 3: Activate venv ===
call venv\Scripts\activate.bat
if %errorlevel% neq 0 (
    echo   [ERROR] Failed to activate venv!
    pause
    exit /b 1
)

REM === Step 4: Install dependencies if needed ===
python -c "import flask" >nul 2>&1
if %errorlevel% equ 0 goto :skip_install

echo   [2/3] Installing dependencies (first time only) ...
echo   This may take 1-2 minutes ...
echo.

pip install -r requirements.txt --no-cache-dir -i https://mirrors.aliyun.com/pypi/simple/ --trusted-host mirrors.aliyun.com
if %errorlevel% neq 0 (
    echo.
    echo   [WARN] Aliyun mirror failed, trying Tsinghua mirror ...
    pip install -r requirements.txt --no-cache-dir -i https://pypi.tuna.tsinghua.edu.cn/simple/ --trusted-host pypi.tuna.tsinghua.edu.cn
)
if %errorlevel% neq 0 (
    echo.
    echo   [ERROR] pip install failed! Check your network.
    pause
    exit /b 1
)
echo.
echo   [OK] Dependencies installed.
goto :after_install

:skip_install
echo   [2/3] Dependencies already installed, skipping.

:after_install
REM === Step 5: Create .env if needed ===
if not exist ".env" (
    if exist ".env.example" (
        copy .env.example .env >nul
        echo   [OK] .env created.
    )
)

REM === Step 6: Launch ===
echo.
echo   [3/3] Starting My Agent ...
echo.
echo   ========================================
echo   My Agent is running!
echo   Open: http://localhost:8080
echo   Press Ctrl+C to stop.
echo   ========================================
echo.

start "" "http://localhost:8080"
python server.py --port 8080

if %errorlevel% neq 0 (
    echo.
    echo   [ERROR] Server crashed. See error above.
    echo.
    pause
)
