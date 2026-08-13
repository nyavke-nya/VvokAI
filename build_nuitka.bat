@echo off
title PylaAI Builder (Nuitka)
color 0B

echo ========================================================
echo               PylaAI Nuitka Compiler
echo ========================================================
echo.

:: Check if Virtual Environment exists
if not exist "venv\Scripts\activate.bat" (
    echo [ERROR] Virtual environment not found. 
    echo Please run start_pyla.bat first to set up the environment and install dependencies.
    pause
    exit /b
)

:: Activate Virtual Environment
echo [INFO] Activating virtual environment...
call venv\Scripts\activate.bat

:: Install Nuitka if not installed
echo [INFO] Checking for Nuitka...
python -m pip install nuitka

:: Build the project
echo [INFO] Starting compilation with Nuitka...
echo [INFO] This may take a while. Please be patient.
python -m nuitka --standalone --onefile --enable-plugin=numpy --enable-plugin=torch --include-data-dir=cfg=cfg --include-data-dir=models=models --include-data-dir=static=static --include-data-dir=templates=templates main.py

if %errorlevel% neq 0 (
    echo [ERROR] Compilation failed!
    pause
    exit /b
)

echo.
echo [SUCCESS] Compilation completed successfully!
echo You can now run main.exe!
pause
