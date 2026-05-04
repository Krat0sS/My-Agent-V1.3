@echo off
cd /d "%~dp0"
set HTTP_PROXY=
set HTTPS_PROXY=
set http_proxy=
set https_proxy=
set ALL_PROXY=
set all_proxy=

set PYTHON_CMD=
py -3 --version >nul 2>&1
if %errorlevel% equ 0 (
    set PYTHON_CMD=py -3
    goto :found
)
python --version >nul 2>&1
if %errorlevel% equ 0 (
    set PYTHON_CMD=python
    goto :found
)
python3 --version >nul 2>&1
if %errorlevel% equ 0 (
    set PYTHON_CMD=python3
    goto :found
)

echo Python not found.
echo Install Python 3.10+ from https://www.python.org/downloads/
echo Make sure to check "Add Python to PATH".
pause
exit /b 1

:found
echo Found Python: %PYTHON_CMD%
%PYTHON_CMD% --version

if not exist "venv\Scripts\activate.bat" (
    echo Creating venv ...
    %PYTHON_CMD% -m venv venv
    if %errorlevel% neq 0 (
        echo Failed to create venv.
        pause
        exit /b 1
    )
)

call venv\Scripts\activate.bat
echo Installing dependencies ...
pip install -r requirements.txt -i https://mirrors.aliyun.com/pypi/simple/ --trusted-host mirrors.aliyun.com -q
if %errorlevel% neq 0 (
    pip install -r requirements.txt -i https://pypi.tuna.tsinghua.edu.cn/simple/ --trusted-host pypi.tuna.tsinghua.edu.cn -q
)
if not exist ".env" (
    if exist ".env.example" copy .env.example .env >nul
)
echo.
echo Done! Now run start.bat
pause
