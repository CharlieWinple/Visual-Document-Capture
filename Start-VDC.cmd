@echo off
cd /d "%~dp0"
if not exist ".venv\Scripts\pythonw.exe" (
  echo Please install the project first. See README.md.
  pause
  exit /b 1
)
".venv\Scripts\python.exe" -c "import PySide6, mss, vdc" >nul 2>&1
if errorlevel 1 (
  echo Install desktop dependencies: .venv\Scripts\python.exe -m pip install -e ".[gui]"
  pause
  exit /b 1
)
start "" ".venv\Scripts\pythonw.exe" -m vdc.app.main
