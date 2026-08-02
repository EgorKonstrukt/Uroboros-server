@echo off
setlocal
chcp 65001 >nul
title Uroboros Server
cd /d "%~dp0"

set "AUTOSTART="
if /i "%~1"=="--autostart" set "AUTOSTART=1"

call :ensure_python
if errorlevel 1 goto done
call :ensure_venv
if errorlevel 1 goto done

if defined AUTOSTART (
    call :run
    exit /b 0
)

:menu
cls
echo ================================================
echo              UROBOROS SERVER
echo ================================================
echo.
echo  [1] Run admin web panel
echo  [2] Start Minecraft server
echo  [3] Stop Minecraft server
echo  [4] Show server status
echo  [S] Full shutdown (panel + Minecraft)
echo  [B] Build compiled version (Nuitka)
echo  [A] Enable autostart
echo  [R] Disable autostart
echo  [Q] Quit
echo.
choice /c 1234SBARQ /n /m "Press a key to select: "
if errorlevel 9 exit /b 0
if errorlevel 8 goto remove_autostart
if errorlevel 7 goto add_autostart
if errorlevel 6 goto build
if errorlevel 5 goto stop_all
if errorlevel 4 goto status
if errorlevel 3 goto stop
if errorlevel 2 goto start
if errorlevel 1 goto run

:ensure_python
set "PYTHON="
where py >nul 2>nul
if not errorlevel 1 (
    for /f "delims=" %%i in ('py -3.13 -c "import sys; print(sys.executable)" 2^>nul') do set "PYTHON=%%i"
    if not defined PYTHON (
        for /f "delims=" %%i in ('py -3 -c "import sys; print(sys.executable)" 2^>nul') do set "PYTHON=%%i"
    )
)
if not defined PYTHON (
    where python >nul 2>nul
    if not errorlevel 1 (
        for /f "delims=" %%i in ('python -c "import sys; print(sys.executable)" 2^>nul') do set "PYTHON=%%i"
    )
)
if defined PYTHON exit /b 0
echo.
echo [INFO] Python not found. Installing Python 3.13.14 ...
where winget >nul 2>nul
if not errorlevel 1 (
    winget install -e --id Python.Python.3.13 --silent --accept-package-agreements --accept-source-agreements >nul 2>nul
    if exist "%LOCALAPPDATA%\Programs\Python\Python313\python.exe" set "PYTHON=%LOCALAPPDATA%\Programs\Python\Python313\python.exe"
)
if defined PYTHON exit /b 0
echo [INFO] Downloading Python 3.13.14 installer ...
powershell -NoProfile -ExecutionPolicy Bypass -Command "$ProgressPreference='SilentlyContinue'; Invoke-WebRequest -Uri 'https://www.python.org/ftp/python/3.13.14/python-3.13.14-amd64.exe' -OutFile '%TEMP%\uroboros_python_setup.exe'"
if errorlevel 1 (
    echo [ERROR] Failed to download Python. Install it manually: https://www.python.org/downloads/
    exit /b 1
)
echo [INFO] Installing Python 3.13.14 (per user) ...
"%TEMP%\uroboros_python_setup.exe" /quiet InstallAllUsers=0 PrependPath=1 Include_launcher=1 Include_test=0
if exist "%LOCALAPPDATA%\Programs\Python\Python313\python.exe" set "PYTHON=%LOCALAPPDATA%\Programs\Python\Python313\python.exe"
if defined PYTHON exit /b 0
echo [ERROR] Python installation failed. Install it manually: https://www.python.org/downloads/
exit /b 1

:ensure_venv
set "RUNNER="
if exist ".venv\Scripts\python.exe" set "RUNNER=.venv\Scripts\python.exe"
if not defined RUNNER (
    echo.
    echo [INFO] Creating virtual environment ...
    "%PYTHON%" -m venv .venv >nul 2>nul
    if exist ".venv\Scripts\python.exe" set "RUNNER=.venv\Scripts\python.exe"
)
set "USER_MODE="
if not defined RUNNER (
    echo [WARN] Could not create venv. Using system Python.
    echo        Packages will be installed for the current user only.
    set "RUNNER=%PYTHON%"
    set "USER_MODE=1"
)
"%RUNNER%" -c "import fastapi,uvicorn,sqlalchemy,aiosqlite,pydantic,aiohttp,requests,psutil,multipart" >nul 2>nul
if not errorlevel 1 exit /b 0
echo.
echo [INFO] Installing dependencies (first run) ...
if defined USER_MODE (
    "%PYTHON%" -m pip install --user --upgrade pip >nul 2>nul
    "%PYTHON%" -m pip install --user -r requirements.txt
    if errorlevel 1 (
        echo [ERROR] Failed to install dependencies.
        exit /b 1
    )
    exit /b 0
)
"%RUNNER%" -m pip install --upgrade pip >nul 2>nul
"%RUNNER%" -m pip install -r requirements.txt
if not errorlevel 1 exit /b 0
echo [WARN] Install into venv failed. Trying --user ...
set "RUNNER=%PYTHON%"
set "USER_MODE=1"
"%PYTHON%" -m pip install --user -r requirements.txt
if errorlevel 1 (
    echo [ERROR] Failed to install dependencies.
    exit /b 1
)
exit /b 0

:run
set PORT=25581
call :killport
echo.
echo Starting server at: http://127.0.0.1:%PORT%
echo Admin panel at: http://127.0.0.1:%PORT%/admin/
echo.
"%RUNNER%" -m server run
call :hold
if defined AUTOSTART exit /b 0
goto menu

:start
echo.
echo Starting Minecraft server ...
echo.
"%RUNNER%" -m server start
call :hold
goto menu

:stop
echo.
echo Stopping Minecraft server ...
echo.
"%RUNNER%" -m server stop
call :hold
goto menu

:status
echo.
echo Server status:
echo.
"%RUNNER%" -m server status
call :hold
goto menu

:stop_all
echo.
echo Full shutdown ...
set PORT=25581
call :killport
echo.
echo Stopping Minecraft servers ...
"%RUNNER%" -m server stop
echo.
echo [OK] Everything stopped.
call :hold
goto menu

:build
echo.
echo Building compiled version with Nuitka ...
echo This runs build.py using the virtual environment.
echo This can take several minutes.
echo.
"%RUNNER%" -c "import nuitka" >nul 2>nul
if errorlevel 1 (
    echo [INFO] Installing Nuitka into the virtual environment ...
    if defined USER_MODE (
        "%RUNNER%" -m pip install --user nuitka
    ) else (
        "%RUNNER%" -m pip install nuitka
    )
    if errorlevel 1 (
        echo.
        echo [ERROR] Failed to install Nuitka.
        call :hold
        goto menu
    )
)
"%RUNNER%" build.py
if errorlevel 1 (
    echo.
    echo [ERROR] Build failed. Fix the errors above and try again.
    call :hold
    goto menu
)
echo.
echo [OK] Build finished. Use run_server_compiled.bat to run the compiled version.
call :hold
goto menu

:add_autostart
> "%TEMP%\uroboros_autostart.vbs" echo Set sh = CreateObject("WScript.Shell")
>> "%TEMP%\uroboros_autostart.vbs" echo sh.Run """%~f0"" --autostart", 7, False
copy /y "%TEMP%\uroboros_autostart.vbs" "%APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup\Uroboros Server.vbs" >nul
echo.
echo [OK] Autostart enabled.
call :hold
goto menu

:remove_autostart
if exist "%APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup\Uroboros Server.vbs" del "%APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup\Uroboros Server.vbs" >nul
echo.
echo [OK] Autostart disabled.
call :hold
goto menu

:killport
echo Freeing port %PORT% ...
for /f "tokens=5" %%a in ('netstat -ano ^| findstr ":%PORT%" ^| findstr LISTENING') do (
    taskkill /F /PID %%a >nul 2>&1 && echo Killed process PID %%a
)
exit /b 0

:hold
if not defined AUTOSTART pause
exit /b 0

:done
exit /b 0
