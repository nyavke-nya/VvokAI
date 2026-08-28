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
::
:: The interpreter and its arguments are kept apart on purpose. "py -3.11" is
:: two tokens and C:\Program Files\Python311\python.exe is one token with a
:: space in it, and a single variable holding either cannot be quoted in a way
:: that works for both - which is exactly how a perfectly good Python in
:: Program Files came back as "not found" over and over.
set "PYTHON_EXE="
set "PYTHON_ARGS="

call :TRY "py" "-3.11"
call :TRY "py" "-3.12"
call :TRY "py" "-3.10"
call :TRY "python"
if defined PYTHON_EXE goto :HAVE_PYTHON

call :SEARCH_DISK
if defined PYTHON_EXE goto :HAVE_PYTHON

call :SEARCH_REGISTRY
if defined PYTHON_EXE goto :HAVE_PYTHON

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

:: TargetDir is passed explicitly so there is one known place to look
:: afterwards, instead of guessing where the installer decided to put it.
set "PYDIR=%LocalAppData%\Programs\Python\Python311"
echo [INFO] Installing to %PYDIR%. A UAC prompt may appear - accept it.
start /wait "" "%TEMP%\vvok-python-3.11.9.exe" /quiet InstallAllUsers=0 PrependPath=1 Include_test=0 Include_launcher=1 TargetDir="%PYDIR%"
del /q "%TEMP%\vvok-python-3.11.9.exe" >nul 2>&1

:: A fresh install is not on this console's PATH - the console read PATH when
:: it opened. That is why the old code said "reopen the window", and why
:: reopening did not always help either. The full path is known, so use it.
call :TRY "%PYDIR%\python.exe"
if defined PYTHON_EXE goto :HAVE_PYTHON

call :SEARCH_DISK
if defined PYTHON_EXE goto :HAVE_PYTHON

call :SEARCH_REGISTRY
if defined PYTHON_EXE goto :HAVE_PYTHON

echo.
echo [ERROR] Python 3.11 was installed but this script still cannot run it.
echo         That usually means the install was cancelled at the UAC prompt.
echo.
echo         Install it by hand from
echo           https://www.python.org/downloads/release/python-3119/
echo         tick "Add python.exe to PATH" on the first screen, then run
echo         start_pyla.bat again.
echo.
pause
exit /b 1

:HAVE_PYTHON
:: ------------------------------------------------------------------ update
:: Before anything is installed or launched, because an update can change the
:: dependency list and this batch file itself. Never fatal: no network, GitHub
:: down or rate limited all come back as "carry on".
if not "%VVOK_RESTARTED%"=="" goto :SKIP_UPDATE

:: If this is a git clone, use git to update instead of the python updater
if exist ".git" (
    echo [INFO] Git repository detected. Updating via git pull...
    git pull origin main
    :: We do not run updater.py because it skips .git folders anyway
    goto :SKIP_UPDATE
)

if not exist "tools\updater.py" goto :SKIP_UPDATE

"%PYTHON_EXE%" %PYTHON_ARGS% tools\updater.py
if errorlevel 10 (
    echo.
    echo [INFO] Restarting to pick up the update...
    echo.
    :: A running batch file is read from disk as it executes, so one that has
    :: just been rewritten cannot simply continue - it has to be started again.
    :: The variable stops the new copy from checking a second time.
    set "VVOK_RESTARTED=1"
    cmd /c ""%~f0""
    exit /b %errorlevel%
)

:SKIP_UPDATE
:: ------------------------------------------------------------------- set up
"%PYTHON_EXE%" %PYTHON_ARGS% tools\installer.py
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
:: Exit code 10 means "an update was installed, start me again". The bot picks
:: updates up while it runs now, and a running Python process cannot reload the
:: modules it is already executing - so it asks to be relaunched instead. Only
:: ever after an update that actually installed; a check that finds nothing
:: leaves the bot alone entirely.
:RUN_VVOK
venv\Scripts\python.exe main.py
set "RUN_CODE=%errorlevel%"

if "%RUN_CODE%"=="10" (
    echo.
    echo [INFO] Update installed. Restarting VvokAI...
    echo.
    goto :RUN_VVOK
)

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
:TRY
if defined PYTHON_EXE exit /b 0
if "%~1"=="" exit /b 0
"%~1" %~2 -c "import sys; sys.exit(0 if (3,10) <= sys.version_info[:2] <= (3,12) else 1)" >nul 2>&1
if errorlevel 1 exit /b 0
set "PYTHON_EXE=%~1"
set "PYTHON_ARGS=%~2"
exit /b 0

:: --------------------------------------------------------------------------
:: The usual install locations. Program Files entries are the ones the old
:: unquoted version could never accept.
:: --------------------------------------------------------------------------
:SEARCH_DISK
for %%V in (311 312 310) do (
    call :TRY "%LocalAppData%\Programs\Python\Python%%V\python.exe"
    call :TRY "%ProgramFiles%\Python%%V\python.exe"
    call :TRY "%SystemDrive%\Python%%V\python.exe"
)
exit /b 0

:: --------------------------------------------------------------------------
:: Whatever the installer actually registered, which is the only way to find a
:: Python somebody put somewhere else entirely.
:: --------------------------------------------------------------------------
:SEARCH_REGISTRY
for %%V in (3.11 3.12 3.10) do (
    for %%H in (HKCU HKLM) do (
        for /f "tokens=2,*" %%A in ('reg query "%%H\Software\Python\PythonCore\%%V\InstallPath" /ve 2^>nul ^| findstr /i "REG_SZ"') do (
            call :TRY "%%~B\python.exe"
        )
    )
)
exit /b 0
