@echo off
chcp 65001 >nul 2>&1
cd /d "%~dp0"
set HTTP_PROXY=
set HTTPS_PROXY=
set http_proxy=
set https_proxy=
set ALL_PROXY=
set all_proxy=
set PYTHONUTF8=1
set PYTHONIOENCODING=utf-8

echo.
echo   ========================================
echo          My Agent v1.3.5
echo   ========================================
echo.

REM ═══ Step 1: Find best Python (3.12 > 3.13 > 3.14 > default) ═══
set "PY_CMD="
set "PY_VER="

py -3.12 --version >nul 2>&1
if %errorlevel% equ 0 ( set "PY_CMD=py -3.12" & set "PY_VER=3.12" & goto :python_found )

py -3.13 --version >nul 2>&1
if %errorlevel% equ 0 ( set "PY_CMD=py -3.13" & set "PY_VER=3.13" & goto :python_found )

py -3.14 --version >nul 2>&1
if %errorlevel% equ 0 ( set "PY_CMD=py -3.14" & set "PY_VER=3.14" & goto :python_found )

py -3 --version >nul 2>&1
if %errorlevel% equ 0 (
    for /f "tokens=2" %%v in ('py -3 --version 2^>^&1') do set "PY_VER=%%v"
    set "PY_CMD=py -3"
    goto :python_found
)

python --version >nul 2>&1
if %errorlevel% equ 0 (
    for /f "tokens=2" %%v in ('python --version 2^>^&1') do set "PY_VER=%%v"
    set "PY_CMD=python"
    goto :python_found
)

python3 --version >nul 2>&1
if %errorlevel% equ 0 (
    for /f "tokens=2" %%v in ('python3 --version 2^>^&1') do set "PY_VER=%%v"
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
echo   [OK] Python: %PY_CMD% (%PY_VER%)
echo.

REM ═══ Step 2: Check venv Python version ═══
if not exist "venv\Scripts\activate.bat" goto :create_venv

REM venv exists — check if it matches our Python version
for /f "tokens=2" %%v in ('venv\Scripts\python.exe --version 2^>^&1') do set "VENV_VER=%%v"
echo   [INFO] Existing venv: Python %VENV_VER%

REM Extract major.minor from both versions
set "NEED_VER=%PY_VER:~0,4%"
set "HAVE_VER=%VENV_VER:~0,4%"

if "%NEED_VER%"=="%HAVE_VER%" goto :venv_ready

echo   [WARN] venv is Python %VENV_VER%, but we need %PY_VER%. Recreating ...
rmdir /s /q venv >nul 2>&1

:create_venv
echo   [1/4] Creating venv with Python %PY_VER% ...
%PY_CMD% -m venv venv
if %errorlevel% neq 0 (
    echo.
    echo   [ERROR] Failed to create venv!
    pause
    exit /b 1
)
echo   [OK] venv created.
echo.

:venv_ready
REM ═══ Step 3: Activate venv ═══
call venv\Scripts\activate.bat
if %errorlevel% neq 0 (
    echo   [ERROR] Failed to activate venv!
    pause
    exit /b 1
)

REM ═══ Step 4: Upgrade pip ═══
python -m pip install --upgrade pip --quiet --no-cache-dir 2>nul

REM ═══ Step 5: Install dependencies if needed ═══
python -c "import flask" >nul 2>&1
if %errorlevel% equ 0 goto :skip_install

echo   [2/4] Installing dependencies (first time only) ...
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
echo   [2/4] Dependencies already installed, skipping.

:after_install
REM ═══ Step 6: Create .env if needed ═══
if not exist ".env" (
    if exist ".env.example" (
        copy .env.example .env >nul
        echo   [OK] .env created.
    )
)

REM ═══ Step 7: Pre-flight checks ═══
echo.
echo   [3/4] Pre-flight checks ...

REM Check server.py exists
if not exist "server.py" (
    echo   [ERROR] server.py not found! Are you in the right directory?
    pause
    exit /b 1
)

REM Check Ollama (optional, don't fail if missing)
curl -s http://localhost:11434/api/tags >nul 2>&1
if %errorlevel% equ 0 (
    echo   [OK] Ollama detected — local model available
) else (
    echo   [INFO] Ollama not running — will use DeepSeek cloud
)

REM ═══ Step 8: Launch ═══
echo.
echo   [4/4] Starting My Agent ...
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
