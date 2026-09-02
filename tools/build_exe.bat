@echo off
setlocal enabledelayedexpansion
title VvokAI - build VvokAI.exe
color 0B

:: Build VvokAI.exe from launcher.py.
::
:: What comes out is around fifteen megabytes and contains no part of the bot.
:: The dependencies are three gigabytes - torch, opencv, the CUDA runtime - and
:: an executable that size would unpack itself into the temp folder on every
:: launch, take a minute to start, and be exactly the shape antivirus software
:: treats as suspicious. The exe fetches those on first run instead, the same
:: way start_pyla.bat always has.
::
:: This script is the whole build. Anybody can run it, read launcher.py, and
:: see that the two match - which is worth more against "is this a virus" than
:: any promise, because an unsigned executable downloaded from a stranger is a
:: thing to be suspicious of and building it yourself is the answer.

cd /d "%~dp0.."

echo ============================================================
echo   Building %~n0
echo ============================================================
echo.

if not exist "launcher.py" (
    echo [ERROR] launcher.py is missing - the download is incomplete.
    echo         Unzip the whole archive again, keeping the folder structure.
    echo.
    pause
    exit /b 1
)

:: A Python to build with. The venv is the obvious one, and it already exists
:: for anybody who has run the bot; otherwise fall back to whatever is on PATH,
:: because the launcher itself only needs the standard library.
set "BUILD_PY="
if exist "venv\Scripts\python.exe" set "BUILD_PY=venv\Scripts\python.exe"
if not defined BUILD_PY (
    for %%P in ("py -3.11" "py -3.12" "py -3.10" "python") do (
        if not defined BUILD_PY (
            %%~P -c "import sys; sys.exit(0 if (3,9) <= sys.version_info[:2] else 1)" >nul 2>&1
            if not errorlevel 1 set "BUILD_PY=%%~P"
        )
    )
)

if not defined BUILD_PY (
    echo [ERROR] No Python found to build with.
    echo         Run start_pyla.bat once, or install Python 3.11 from
    echo         https://www.python.org/downloads/release/python-3119/
    echo.
    pause
    exit /b 1
)

echo [INFO] Building with: %BUILD_PY%
echo.

echo [INFO] Making sure PyInstaller is available...
%BUILD_PY% -m pip install --quiet --upgrade pyinstaller
if errorlevel 1 (
    echo.
    echo [ERROR] PyInstaller could not be installed. Check the connection.
    echo.
    pause
    exit /b 1
)

:: Leftovers from an earlier build confuse PyInstaller more often than they
:: help, and this build takes under a minute anyway.
if exist "build\launcher" rmdir /s /q "build\launcher"
mkdir "build\launcher" >nul 2>&1

set "VERSIONARG="

:: A version resource: company, description, product name, version. An exe
:: with none of that is one of the things Defender's machine-learning models
:: weigh, and an unsigned onefile that downloads Python and starts other
:: processes is already most of the rest of that shape. It does not make the
:: file trusted - only a signature does that - it removes a free reason to
:: distrust it, and tells the properties dialog what the file is.
%BUILD_PY% tools\make_version_file.py "%~dp0..\build\launcher\version_info.txt"
if exist "%~dp0..\build\launcher\version_info.txt" (
    set "VERSIONARG=--version-file=%~dp0..\build\launcher\version_info.txt"
) else (
    echo [WARN] The version resource could not be written; building without it.
)

:: The mark from the corner of the panel, as a Windows icon. It is a styled

:: letter in CSS there and no use to Windows, so tools/make_icon.py redraws it

:: from the same numbers. The file is in the repository; this only rebuilds it

:: if it has gone missing. --icon resolves relative to --specpath, hence the

:: absolute path.

set "ICONARG="

if not exist "assets\images\vvokai.ico" %BUILD_PY% tools\make_icon.py >nul 2>&1

if exist "%~dp0..\assets\images\vvokai.ico" (

    set "ICONARG=--icon=%~dp0..\assets\images\vvokai.ico"

    echo [INFO] Icon: assets\images\vvokai.ico

) else (

    echo [WARN] assets\images\vvokai.ico is missing; building without an icon.

)

if exist "VvokAI.spec" del /q "VvokAI.spec"

echo.
echo [INFO] Compiling. This takes under a minute.
echo.

:: --console on purpose. The launcher downloads Python, the project and the
:: dependencies, and somebody running an unsigned exe for the first time
:: should be able to watch it say so. The window it finally opens is a proper
:: application window; this console belongs to the setup that precedes it.
::
:: --exclude-module keeps the size down: launcher.py is standard library only,
:: but PyInstaller will happily notice numpy sitting in the same venv and pull
:: in three hundred megabytes of it.
%BUILD_PY% -m PyInstaller ^
    --onefile ^
    --console ^
    --name VvokAI ^
    --distpath . ^
    --workpath build\launcher ^
    --specpath build\launcher ^
    !ICONARG! ^
    !VERSIONARG! ^
    --exclude-module numpy ^
    --exclude-module torch ^
    --exclude-module cv2 ^
    --exclude-module PySide6 ^
    --exclude-module PIL ^
    --exclude-module scipy ^
    --exclude-module tkinter ^
    --exclude-module onnxruntime ^
    launcher.py

if errorlevel 1 (
    echo.
    echo [ERROR] The build failed. The reason is above.
    echo.
    pause
    exit /b 1
)

if not exist "VvokAI.exe" (
    echo.
    echo [ERROR] The build reported success but VvokAI.exe is not here.
    echo.
    pause
    exit /b 1
)

for %%F in ("VvokAI.exe") do set "SIZE=%%~zF"
set /a SIZE_MB=!SIZE! / 1048576

echo.
echo ============================================================
echo   Done. VvokAI.exe  (!SIZE_MB! MB)
echo ============================================================
echo.
echo   Put it in an empty folder and run it. It will fetch Python,
echo   the project and the dependencies on first start, then open
echo   the application window. Later starts go straight to the
echo   window, checking for updates on the way.
echo.
echo   If Defender quarantines it: that detection is Bearfoos.B^!ml or
echo   one like it, an ML guess rather than a match on anything known,
echo   and an unsigned onefile that downloads Python and starts other
echo   processes is the exact shape it guesses on. Send the file to
echo   Microsoft as a false positive - it is free, takes a day or two,
echo   and clears it for everyone rather than one machine:
echo     https://www.microsoft.com/en-us/wdsi/filesubmission
echo   An exclusion only ever fixes the machine it is added on, and a
echo   signing certificate is the only thing that stops it recurring.
echo.
pause
exit /b 0
