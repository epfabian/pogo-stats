@echo off
setlocal
cd /d "%~dp0"

echo Stopping PoGo Bot and Backend ...

taskkill /FI "WINDOWTITLE eq PoGo Bot*" /T /F >nul 2>&1
taskkill /FI "WINDOWTITLE eq PoGo Backend*" /T /F >nul 2>&1

echo Done.
pause
