@echo off
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
  echo AT Commander's Python environment is not installed yet.
  echo Launch AT Commander once first, then run this file again.
  pause
  exit /b 1
)
".venv\Scripts\python.exe" -c "import discord" >nul 2>&1
if errorlevel 1 (
  echo Installing the optional Discord bot dependency...
  ".venv\Scripts\python.exe" -m pip install -q -r requirements-discord.txt
  if errorlevel 1 (
    echo Failed to install Discord bot dependency.
    pause
    exit /b 1
  )
)
".venv\Scripts\python.exe" -m atcommander.cm_discord_bot
pause
