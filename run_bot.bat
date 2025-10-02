@echo off
setlocal
REM === change if your folder is different ===
set PROJECT_DIR=C:\Users\sperm\Desktop\gzsbot
set PYTHON=%PROJECT_DIR%\.venv\Scripts\python.exe

cd /d "%PROJECT_DIR%"

:loop
"%PYTHON%" bot.py >> "%PROJECT_DIR%\bot_out.log" 2>> "%PROJECT_DIR%\bot_err.log"
REM wait 5s before restarting if it exits/crashes
timeout /t 5 /nobreak >nul
goto loop
