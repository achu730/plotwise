@echo off
cd /d "%~dp0"
set PYTHONUTF8=1
if exist .venv\Scripts\python.exe goto run
set PY=
where py >nul 2>nul && set PY=py -3
if not defined PY (python --version >nul 2>nul && set PY=python)
if not defined PY (
  echo Python 3 is not installed. Install it with:  winget install -e --id Python.Python.3.12
  echo or from https://www.python.org/downloads/ ^(tick "Add python.exe to PATH"^), then open a new window.
  exit /b 1
)
if exist .venv rmdir /s /q .venv
%PY% -m venv .venv
:run
.venv\Scripts\python -m pip install -q --disable-pip-version-check -r requirements.txt
.venv\Scripts\python app.py
