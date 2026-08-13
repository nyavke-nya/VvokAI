@echo off
title PylaAILauncher
color 0B


set "PYTHON_CMD=python"
python --version >nul 2>&1
if %errorlevel% equ 0 goto :PYTHON_FOUND

echo [INFO] Python not found in PATH. Checking default install locations...

if exist "%LocalAppData%\Programs\Python\Python311\python.exe" (
    set "PYTHON_CMD=%LocalAppData%\Programs\Python\Python311\python.exe"
    echo [INFO] Found Python 3.11 in LocalAppData.
    goto :PYTHON_FOUND
)
if exist "%ProgramFiles%\Python311\python.exe" (
    set "PYTHON_CMD=%ProgramFiles%\Python311\python.exe"
    echo [INFO] Found Python 3.11 in Program Files.
    goto :PYTHON_FOUND
)
if exist "%LocalAppData%\Programs\Python\Python312\python.exe" (
    set "PYTHON_CMD=%LocalAppData%\Programs\Python\Python312\python.exe"
    echo [INFO] Found Python 3.12 in LocalAppData.
    goto :PYTHON_FOUND
)
if exist "%LocalAppData%\Programs\Python\Python310\python.exe" (
    set "PYTHON_CMD=%LocalAppData%\Programs\Python\Python310\python.exe"
    echo [INFO] Found Python 3.10 in LocalAppData.
    goto :PYTHON_FOUND
)

echo [WARNING] Python is not installed on this system. 
echo [INFO] Downloading Python 3.11.9 installer...
curl -L -o python-installer.exe https://www.python.org/ftp/python/3.11.9/python-3.11.9-amd64.exe
if not exist python-installer.exe (
    echo [ERROR] Failed to download Python installer.
    pause
    exit /b
)

echo [INFO] Installing Python silently... This may take a few minutes.
start /wait python-installer.exe /quiet PrependPath=1 Include_test=0
del python-installer.exe

if exist "%LocalAppData%\Programs\Python\Python311\python.exe" (
    set "PYTHON_CMD=%LocalAppData%\Programs\Python\Python311\python.exe"
    echo [SUCCESS] Python installed successfully.
    goto :PYTHON_FOUND
)
if exist "%ProgramFiles%\Python311\python.exe" (
    set "PYTHON_CMD=%ProgramFiles%\Python311\python.exe"
    echo [SUCCESS] Python installed successfully.
    goto :PYTHON_FOUND
)

echo [ERROR] Automatic Python installation failed. Please install manually.
pause
exit /b

:PYTHON_FOUND
"%PYTHON_CMD%" --version >nul 2>&1
if %errorlevel% neq 0 (
    echo [ERROR] Cannot execute Python.
    pause
    exit /b
)

:: Create Virtual Environment
if not exist "venv\Scripts\activate.bat" (
    echo [INFO] Creating virtual environment...
    "%PYTHON_CMD%" -m venv venv
    if %errorlevel% neq 0 (
        echo [ERROR] Failed to create virtual environment.
        pause
        exit /b
    )
)

:: Activate Virtual Environment
echo [INFO] Activating virtual environment...
call venv\Scripts\activate.bat

:: Update pip
python -m pip install --upgrade pip setuptools wheel >nul 2>&1

:: Install Requirements and Analyze Hardware
if not exist "venv\.setup_complete" (
    echo [INFO] Analyzing hardware and installing optimized AI frameworks...
    echo [INFO] This will download large AI models and configure CUDA/DirectML. Please wait...
    
    :: Pipe "y" to automatically answer "yes" to any PyTorch / CUDA prompts
    echo y | python setup.py install
    
    if %errorlevel% neq 0 (
        echo [ERROR] Setup failed during dependency installation.
        pause
        exit /b
    )
    echo. > venv\.setup_complete
    echo [SUCCESS] Hardware setup complete!
)

:: Run Application
echo.
echo [INFO] Launching PylaAI...
python main.py

if %errorlevel% neq 0 (
    echo.
    echo [ERROR] PylaAI crashed or exited with an error.
    pause
)

exit /b
