@echo off
cd /d "%~dp0"
if exist "AT Commander.exe" (
  start "" "AT Commander.exe"
  exit /b
)
if exist ".venv\Scripts\python.exe" (
  ".venv\Scripts\python.exe" app.py
) else (
  py -3 bootstrap.pyw
)
pause
