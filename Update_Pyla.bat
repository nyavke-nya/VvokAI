@echo off
echo.
echo Changing directory to the project folder...
cd /d "%~dp0"
if not exist ".git" (
    echo [ERROR] This script was not run inside a git repository!
    echo Please move this file (Update_Pyla.bat^) INSIDE your project folder and run it there.
    pause
    exit /b
)

echo.
echo [1/4] Backing up configuration files (cfg folder)...
xcopy "cfg" "cfg_backup" /E /I /H /Y >nul

echo.
echo [2/4] Fetching latest updates from GitHub...
git fetch --all

echo.
echo [3/4] Resetting code files to match the original repository...
git reset --hard origin/main

echo.
echo [4/4] Restoring your configuration files from backup...
xcopy "cfg_backup" "cfg" /E /I /H /Y >nul
rmdir /s /q "cfg_backup"

echo.
pause
