@echo off
cd /d "%~dp0"
set HTTP_PROXY=
set HTTPS_PROXY=
set http_proxy=
set https_proxy=
set ALL_PROXY=
set all_proxy=

if exist "venv\Scripts\activate.bat" goto :start

echo [1/3] First run - setting up environment ...
set PYTHON_CMD=
py -3 --version >nul 2>&1
if %errorlevel% equ 0 ( set PYTHON_CMD=py -3 & goto :found )
python --version >nul 2>&1
if %errorlevel% equ 0 ( set PYTHON_CMD=python & goto :found )
python3 --version >nul 2>&1
if %errorlevel% equ 0 ( set PYTHON_CMD=python3 & goto :found )

echo [ERROR] Python not found. Install from https://python.org
pause
exit /b 1

:found
%PYTHON_CMD% -m venv venv
call venv\Scripts\activate.bat
echo [2/3] Installing dependencies ...
pip install -r requirements.txt -i https://mirrors.aliyun.com/pypi/simple/ --trusted-host mirrors.aliyun.com -q
if %errorlevel% neq 0 (
    pip install -r requirements.txt -i https://pypi.tuna.tsinghua.edu.cn/simple/ --trusted-host pypi.tuna.tsinghua.edu.cn -q
)
if not exist ".env" if exist ".env.example" copy .env.example .env >nul
echo [3/3] Setup complete!

:start
call venv\Scripts\activate.bat
echo.
echo   My Agent starting at http://localhost:8080
echo   Press Ctrl+C to stop.
echo.
python server.py --port 8080
if %errorlevel% neq 0 (
    echo.
    echo [ERROR] Server crashed. See error above.
)
pause
