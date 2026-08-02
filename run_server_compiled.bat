@echo off
setlocal
chcp 65001 >nul
title Uroboros Server (Compiled)
cd /d "%~dp0"

set "AUTOSTART="
if /i "%~1"=="--autostart" set "AUTOSTART=1"

set "EXE="
for %%C in ("dist\UroborosServer.exe" "dist\UroborosServer.dist\UroborosServer.exe" "UroborosServer.exe") do (
    if exist "%%~C" if not defined EXE set "EXE=%%~C"
)
if not defined EXE (
    echo [ERROR] Compiled server not found.
    echo Run run_server.bat and press [B] to build it.
    pause
    exit /b 1
)

if defined AUTOSTART (
    call :run
    exit /b 0
)

call :get_version

:menu
cls
echo ^>^> Uroboros Server ^(compiled^) - v%UROBOROS_VERSION% ^<^<
echo.
echo [1] Run full server (auth + admin dashboard)
echo [2] Start Minecraft server only
echo [3] Stop Minecraft server
echo [4] Show server status
echo [S] Full shutdown (panel + Minecraft)
echo [A] Add to autostart
echo [R] Remove from autostart
echo [Q] Quit
echo.
choice /c 1234SARQ /n /m "Select action: "
if errorlevel 8 exit /b 0
if errorlevel 7 goto remove_autostart
if errorlevel 6 goto add_autostart
if errorlevel 5 goto stop_all
if errorlevel 4 goto status
if errorlevel 3 goto stop
if errorlevel 2 goto start
if errorlevel 1 goto run

:run
set PORT=25581
call :killport
call :lanip
echo.
echo Starting server at http://%LANIP%:%PORT% ...
echo Admin dashboard: http://%LANIP%:%PORT%/admin/
echo.
"%EXE%" run
call :hold
if defined AUTOSTART exit /b 0
goto menu

:start
echo.
echo Starting Minecraft server ...
echo.
"%EXE%" start
call :hold
goto menu

:stop
echo.
"%EXE%" stop
call :hold
goto menu

:status
echo.
"%EXE%" status
call :hold
goto menu

:stop_all
echo.
echo Full shutdown ...
set PORT=25581
call :killport
echo.
echo Stopping Minecraft servers ...
"%EXE%" stop
echo.
echo [OK] Everything stopped.
call :hold
goto menu

:add_autostart
> "%TEMP%\uroboros_autostart_compiled.vbs" echo Set sh = CreateObject("WScript.Shell")
>> "%TEMP%\uroboros_autostart_compiled.vbs" echo sh.Run """%~f0"" --autostart", 7, False
copy /y "%TEMP%\uroboros_autostart_compiled.vbs" "%APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup\Uroboros Server (Compiled).vbs" >nul
echo Autostart enabled.
call :hold
goto menu

:remove_autostart
if exist "%APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup\Uroboros Server (Compiled).vbs" del "%APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup\Uroboros Server (Compiled).vbs" >nul
echo Autostart disabled.
call :hold
goto menu

:killport
echo.
echo Killing existing process on port %PORT% ...
for /f "tokens=5" %%a in ('netstat -ano ^| findstr ":%PORT%" ^| findstr LISTENING') do (
    taskkill /F /PID %%a >nul 2>&1 && echo Killed PID %%a on port %PORT%
)
exit /b 0

:lanip
set "LANIP=127.0.0.1"
for /f "delims=" %%i in ('powershell -NoProfile -Command "(Get-NetIPAddress -AddressFamily IPv4 -ErrorAction SilentlyContinue | Where-Object { $_.IPAddress -notlike '127.*' -and $_.IPAddress -notlike '169.254.*' } | Sort-Object InterfaceMetric | Select-Object -First 1).IPAddress"') do if not "%%i"=="" set "LANIP=%%i"
exit /b 0

:hold
if not defined AUTOSTART pause
exit /b 0

:get_version
set "UROBOROS_VERSION="
for /f "delims=" %%i in ('"%EXE%" version 2^>nul') do set "UROBOROS_VERSION=%%i"
exit /b 0

:done
exit /b 0
