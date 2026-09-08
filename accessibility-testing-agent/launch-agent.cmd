@echo off
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
  echo Run the installation commands in README.md first.
  pause
  exit /b 1
)
if exist ".browsers" set "PLAYWRIGHT_BROWSERS_PATH=%CD%\.browsers"
".venv\Scripts\python.exe" -m accessibility_agent %*
pause
