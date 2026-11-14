@echo off
rem Checks your Gemini key and runs the writing chain on a sample CV and job.
rem Double-click this file. Results are saved to gemini-check-log.txt next to it.
rem Your key is read from .env and is never printed.
setlocal
cd /d "%~dp0"
set "LOG=%~dp0gemini-check-log.txt"
echo Gemini check started %DATE% %TIME% > "%LOG%"

set "PY="
py -3 -c "import sys; sys.exit(0 if sys.version_info >= (3,12) else 1)" >nul 2>&1 && set "PY=py -3"
if not defined PY (
  python -c "import sys; sys.exit(0 if sys.version_info >= (3,12) else 1)" >nul 2>&1 && set "PY=python"
)
if not defined PY (
  echo Python 3.12 or newer was not found. >> "%LOG%"
  py -0 >> "%LOG%" 2>&1
  python --version >> "%LOG%" 2>&1
  echo Python 3.12 or newer was not found. Install it from https://www.python.org/downloads/
  echo ^(tick "Add python.exe to PATH"^), then double-click this file again.
  pause
  exit /b 1
)

if not exist "backend\.venv\Scripts\python.exe" (
  echo Setting up, this takes a few minutes the first time...
  %PY% -m venv "backend\.venv" >> "%LOG%" 2>&1
)
set "VPY=%~dp0backend\.venv\Scripts\python.exe"
"%VPY%" -m pip install --quiet --disable-pip-version-check -e "backend" >> "%LOG%" 2>&1
if errorlevel 1 (
  echo Installing the packages failed. See gemini-check-log.txt >> "%LOG%"
  echo Installing the packages failed. Details are in gemini-check-log.txt
  pause
  exit /b 1
)

set "LLM_PROVIDER=gemini"
set "PYTHONIOENCODING=utf-8"
cd backend
echo. >> "%LOG%"
echo ===== 1. Connection check ===== >> "%LOG%"
echo Checking the connection...
"%VPY%" ..\scripts\check_gemini.py >> "%LOG%" 2>&1
echo exit code %ERRORLEVEL% >> "%LOG%"
echo. >> "%LOG%"
echo ===== 2. Writing chain on a sample CV and job ===== >> "%LOG%"
echo Writing a sample application...
"%VPY%" ..\scripts\try_gemini.py >> "%LOG%" 2>&1
echo exit code %ERRORLEVEL% >> "%LOG%"
echo Finished %DATE% %TIME% >> "%LOG%"
cd ..
echo.
type "%LOG%"
echo.
echo Done. Results are saved in gemini-check-log.txt
pause
