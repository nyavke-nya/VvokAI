@echo off
setlocal enabledelayedexpansion
title VvokAI
color 0B

:: Work from this file's own folder, whatever directory the shell is in. Being
:: launched by full path from elsewhere - a console sitting in System32, say -
:: would otherwise look for the project in that other place.
cd /d "%~dp0"

:: Everything past finding a Python lives in tools\installer.py. Batch can run
:: a program and read an exit code; it cannot retry a flaky download, work out
:: why a build failed, or explain it - and explaining it is the part that
:: actually saves anyone time. A missing C++ compiler that really means
:: "Python too new", a headless OpenCV that silently kills the debug window, a
:: setup marker written after a half-failed install: all of them looked like
:: something other than what they were.

if not exist "tools\installer.py" (
    echo [ERROR] tools\installer.py is missing - the download is incomplete.
    echo         Unzip the whole archive again, keeping the folder structure.
    echo.
    pause
    exit /b 1
)

:: --------------------------------------------------------------- find Python
::
:: The VERSION matters, not merely that python exists. Several dependencies
:: publish Windows builds only up to 3.12; on 3.13 pip falls back to compiling
:: them and stops at "Microsoft Visual C++ 14.0 or greater is required", which
:: sounds like a missing compiler and is really a Python that is too new.


:: ------------------------------------------------------------ install Python
echo [INFO] No suitable Python found. This project needs 3.10, 3.11 or 3.12.
echo [INFO] Downloading Python 3.11.9. This takes a couple of minutes.
echo.
curl -L --fail -o "%TEMP%\vvok-python-3.11.9.exe" https://www.python.org/ftp/python/3.11.9/python-3.11.9-amd64.exe
if errorlevel 1 (
    echo.
    echo [ERROR] The download failed. Install Python 3.11.9 by hand from
    echo         https://www.python.org/downloads/release/python-3119/
    echo         and tick "Add python.exe to PATH".
    echo.
    pause
    exit /b 1
)

echo [INFO] Installing. A UAC prompt may appear - accept it.
start /wait "" "%TEMP%\vvok-python-3.11.9.exe" /quiet InstallAllUsers=0 PrependPath=1 Include_test=0 Include_launcher=1
del /q "%TEMP%\vvok-python-3.11.9.exe" >nul 2>&1

call :TRY_PYTHON "py" "-3.11"
call :TRY_PYTHON "%LocalAppData%\Programs\Python\Python311\python.exe"
call :TRY_PYTHON "%ProgramFiles%\Python311\python.exe"
if defined PYTHON_CMD goto :HAVE_PYTHON

echo.
echo [ERROR] Python was installed but cannot be found yet.
echo         Close this window, open a new one and run start_pyla.bat again -
echo         a fresh console is needed to pick up the changed PATH.
echo.
pause
exit /b 1

:HAVE_PYTHON
:: ------------------------------------------------------------------- set up
%PYTHON_CMD% tools\installer.py
set "SETUP_CODE=%errorlevel%"

if not "%SETUP_CODE%"=="0" (
    echo.
    echo ============================================================
    echo   Setup did not finish. The reason is above, and the full
    echo   output is in install_log.txt - send that file if you are
    echo   asking for help.
    echo ============================================================
    echo.
    pause
    exit /b %SETUP_CODE%
)

:: ------------------------------------------------------------------- launch
echo.
echo [INFO] Launching VvokAI...
echo.
venv\Scripts\python.exe main.py
set "RUN_CODE=%errorlevel%"

if not "%RUN_CODE%"=="0" (
    echo.
    echo [ERROR] VvokAI stopped with an error. The last lines above say why.
    echo.
    pause
)

exit /b %RUN_CODE%


:: --------------------------------------------------------------------------
:: Accept a candidate interpreter only if it runs AND is a version the
:: dependencies publish builds for. First match wins, so the caller lists the
:: preferred versions first.
:: --------------------------------------------------------------------------
:TRY_PYTHON
if defined PYTHON_CMD exit /b 0
%~1 %~2 -c "import sys; sys.exit(0 if (3,10) <= sys.version_info[:2] <= (3,12) else 1)" >nul 2>&1
if errorlevel 1 exit /b 0
if "%~2"=="" (set "PYTHON_CMD=%~1") else (set "PYTHON_CMD=%~1 %~2")
exit /b 0
