@echo off
net session >nul 2>&1
if not %errorlevel% == 0 (
    powershell -NoProfile -Command "Start-Process '%~f0' -ArgumentList '%*' -Verb RunAs"
    exit /b
)
setlocal
cd /d "%~dp0"
set "PYTHON_EXE="
where py.exe >nul 2>&1
if not errorlevel 1 set "PYTHON_EXE=py.exe"
if not defined PYTHON_EXE if exist "%LocalAppData%\Programs\Python\Python313\python.exe" set "PYTHON_EXE=%LocalAppData%\Programs\Python\Python313\python.exe"
if not defined PYTHON_EXE where python.exe >nul 2>&1
if not defined PYTHON_EXE if not errorlevel 1 set "PYTHON_EXE=python.exe"
if not defined PYTHON_EXE (
    echo Python 3.13 не найден. Установите Python с https://www.python.org/downloads/windows/
    pause
    exit /b 1
)
"%PYTHON_EXE%" "%~dp0ru_helper.py" --console %*
if errorlevel 1 pause
endlocal
