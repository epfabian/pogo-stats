@echo off
setlocal
cd /d "%~dp0"

if not exist ".venv\Scripts\python.exe" (
    echo Virtual environment not found ^(.venv^). Please set it up first:
    echo   python -m venv .venv
    echo   .venv\Scripts\activate
    echo   pip install -r requirements.txt
    pause
    exit /b 1
)

if not exist ".env" (
    echo No .env file found. Please set DISCORD_TOKEN and CHANNEL_ID first.
    pause
    exit /b 1
)

if not exist "logs" mkdir logs

echo.
echo   PoGo Stats - Start
echo.
choice /C YN /M "Should the Bot and Backend console windows be visible"
if errorlevel 2 goto minimized
if errorlevel 1 goto visible

:visible
echo Starting Bot and Backend in visible windows ...
start "PoGo Bot" cmd /k ".venv\Scripts\python.exe -m bot.bot"
start "PoGo Backend" cmd /k ".venv\Scripts\python.exe -m uvicorn backend.main:app --host 0.0.0.0 --port 8000"
goto browser

:minimized
echo Starting Bot and Backend minimized ...
echo Logs are also written to logs\bot.log and logs\backend.log
start "PoGo Bot" /min cmd /c ".venv\Scripts\python.exe -m bot.bot > logs\bot.log 2>&1"
start "PoGo Backend" /min cmd /c ".venv\Scripts\python.exe -m uvicorn backend.main:app --host 0.0.0.0 --port 8000 > logs\backend.log 2>&1"
echo.
echo Running minimized in the taskbar. Use stop.bat to stop it.
goto browser

:browser
echo Waiting a moment for the backend to be ready ...
timeout /t 3 /nobreak >nul
start "" http://localhost:8000
goto end

:end
echo.
echo Dashboard: http://localhost:8000
echo This window will close automatically. Bot and Backend keep running -
echo use stop.bat to stop them.
timeout /t 3 /nobreak >nul
exit /b 0
