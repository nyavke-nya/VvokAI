@echo off
setlocal enabledelayedexpansion
title VvokAI - repair install
color 0B

echo ============================================================
echo   VvokAI - repairing the Python environment
echo ============================================================
echo.
echo   Put this file in the SAME folder as main.py and run it.
echo   It does not touch your settings, queue or match history.
echo.

if not exist "main.py" (
    echo [ERROR] main.py is not next to this file.
    echo         Move fix_install.bat into the VvokAI folder and run it there.
    echo.
    pause
    exit /b 1
)

:: ---------------------------------------------------------------- Python
set "PYTHON_CMD=python"
python --version >nul 2>&1
if not errorlevel 1 goto :PYTHON_FOUND

echo [INFO] Python is not on PATH. Looking in the usual places...
if exist "%LocalAppData%\Programs\Python\Python311\python.exe" (
    set "PYTHON_CMD=%LocalAppData%\Programs\Python\Python311\python.exe"
    goto :PYTHON_FOUND
)
if exist "%ProgramFiles%\Python311\python.exe" (
    set "PYTHON_CMD=%ProgramFiles%\Python311\python.exe"
    goto :PYTHON_FOUND
)
if exist "%LocalAppData%\Programs\Python\Python312\python.exe" (
    set "PYTHON_CMD=%LocalAppData%\Programs\Python\Python312\python.exe"
    goto :PYTHON_FOUND
)
if exist "%LocalAppData%\Programs\Python\Python310\python.exe" (
    set "PYTHON_CMD=%LocalAppData%\Programs\Python\Python310\python.exe"
    goto :PYTHON_FOUND
)

echo [ERROR] No Python found. Install Python 3.11 from
echo         https://www.python.org/downloads/release/python-3119/
echo         and tick "Add python.exe to PATH" during setup.
echo.
pause
exit /b 1

:PYTHON_FOUND
for /f "tokens=*" %%v in ('"%PYTHON_CMD%" --version 2^>^&1') do echo [INFO] Using %%v

:: ---------------------------------------------------------------- venv
if not exist "venv\Scripts\python.exe" (
    echo [INFO] Creating the virtual environment...
    "%PYTHON_CMD%" -m venv venv
    if errorlevel 1 (
        echo [ERROR] Could not create the virtual environment.
        pause
        exit /b 1
    )
)

set "VPY=venv\Scripts\python.exe"
echo [INFO] Updating pip...
"%VPY%" -m pip install --upgrade pip setuptools wheel >nul 2>&1

:: ---------------------------------------------------------------- packages
::
:: The list is written out here rather than read from requirements.txt on
:: purpose: a broken install usually came WITH a stale requirements.txt, and
:: reading it would reproduce exactly the failure this file exists to repair.
echo.
echo [1/4] Installing what the bot needs. This downloads a few hundred MB.
echo.
"%VPY%" -m pip install aiohttp numpy requests toml pillow "discord.py" packaging pywin32 easyocr "adbutils==2.12.0" "av==12.3.0" Flask pycryptodome
if errorlevel 1 (
    echo.
    echo [ERROR] Package installation failed. Scroll up for the reason.
    pause
    exit /b 1
)

:: pandas is no longer required by current builds, but an older copy of
:: trophy_observer.py still imports it, and that import is what crashes the
:: launcher. Installed unpinned so pip picks a version that exists for this
:: Python - the pinned "pandas~=3.0" is what tends to fail.
echo.
echo [2/4] Installing pandas (only needed by older builds)...
"%VPY%" -m pip install pandas
if errorlevel 1 (
    echo [WARN] pandas would not install. That is fine on a current build.
)

:: ---------------------------------------------------------------- OpenCV
::
:: easyocr depends on opencv-python-headless, and both packages unpack into the
:: same cv2 folder, so whichever pip installs last is the one that runs. When
:: the headless build wins, every OpenCV window call fails with "the function
:: is not implemented" and the debug view can never open - with nothing obvious
:: in the log. Removing both and reinstalling the GUI build settles it.
echo.
echo [3/4] Making sure OpenCV has a working GUI build...
"%VPY%" -m pip uninstall -y opencv-python-headless opencv-python >nul 2>&1
"%VPY%" -m pip install "opencv-python~=4.11"
if errorlevel 1 (
    echo [ERROR] OpenCV would not install.
    pause
    exit /b 1
)

:: onnxruntime has the same problem: the DirectML and GPU builds share a
:: directory. Keep exactly one.
::
:: DirectML is chosen because it works on any GPU with no extra downloads. On
:: an NVIDIA card the CUDA build is roughly 3.7x faster, but it needs matching
:: CUDA and cuDNN wheels and clashes with the CUDA build of torch, so it is not
:: something to attempt unattended. See the README if you want it.
echo.
echo [INFO] Installing the DirectML runtime. On an NVIDIA card CUDA is faster;
echo [INFO] the README explains how to switch once the bot runs at all.
"%VPY%" -m pip uninstall -y onnxruntime onnxruntime-gpu onnxruntime-directml >nul 2>&1
"%VPY%" -m pip install "onnxruntime-directml~=1.24"

:: torch is only needed for a single line in the bot, so the CPU build is
:: enough and avoids the cuDNN clash the CUDA build causes with onnxruntime.
"%VPY%" -m pip install torch torchvision --index-url https://download.pytorch.org/whl/cpu

:: ---------------------------------------------------------------- verify
echo.
echo [4/4] Checking that everything actually imports...
echo.
set "CHECK=%TEMP%\vvok_check.py"
echo import importlib, sys> "%CHECK%"
echo wanted = "cv2:opencv-python,numpy:numpy,requests:requests,toml:toml,PIL:pillow,discord:discord.py,win32api:pywin32,easyocr:easyocr,adbutils:adbutils,av:av,flask:Flask,Crypto:pycryptodome,onnxruntime:onnxruntime,torch:torch,aiohttp:aiohttp">> "%CHECK%"
echo missing = []>> "%CHECK%"
echo for item in wanted.split(","):>> "%CHECK%"
echo     module, package = item.split(":")>> "%CHECK%"
echo     try:>> "%CHECK%"
echo         importlib.import_module(module)>> "%CHECK%"
echo         print("  OK       " + package)>> "%CHECK%"
echo     except Exception as error:>> "%CHECK%"
echo         missing.append(package)>> "%CHECK%"
echo         print("  MISSING  " + package + "   " + type(error).__name__)>> "%CHECK%"
echo try:>> "%CHECK%"
echo     import cv2>> "%CHECK%"
echo     info = cv2.getBuildInformation()>> "%CHECK%"
echo     part = info.split("GUI:", 1)[-1][:400].upper() if "GUI:" in info else "">> "%CHECK%"
echo     lit = any(name in part for name in ["WIN32UI", "GTK", "COCOA", "QT"])>> "%CHECK%"
echo     print("")>> "%CHECK%"
echo     print("  debug window support: " + ("yes" if lit else "NO - headless OpenCV is in the way"))>> "%CHECK%"
echo except Exception:>> "%CHECK%"
echo     pass>> "%CHECK%"
echo sys.exit(1 if missing else 0)>> "%CHECK%"

"%VPY%" "%CHECK%"
set "IMPORTS_OK=%errorlevel%"
del /q "%CHECK%" >nul 2>&1

:: Force the launcher to re-verify next time rather than trusting a stale marker.
if exist "venv\.setup_complete" del /q "venv\.setup_complete" >nul 2>&1

echo.
echo ============================================================
if "%IMPORTS_OK%"=="0" (
    echo   DONE. Start the bot with start_pyla.bat
) else (
    echo   SOME PACKAGES ARE STILL MISSING - see the list above.
    echo   Send that list back and it can be sorted out.
)
echo ============================================================
echo.
pause
exit /b 0
