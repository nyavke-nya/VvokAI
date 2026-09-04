@echo off
:: Compatibility shim. The launcher is start_vvok.bat now; this keeps old
:: desktop shortcuts and any muscle memory working after the rename. It only
:: forwards - all of the real work (finding Python, updating, launching) lives
:: in start_vvok.bat.
cd /d "%~dp0"
call "%~dp0start_vvok.bat" %*
exit /b %errorlevel%
