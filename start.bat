@echo off
setlocal EnableExtensions
cd /d "%~dp0"
title LlamaHelper

set "VENV_DIR=%~dp0.venv"
set "VENV_PYTHON=%VENV_DIR%\Scripts\python.exe"

echo [LlamaHelper] Checking runtime...

if exist "%VENV_PYTHON%" goto check_dependencies

echo [LlamaHelper] Creating project virtual environment...
call :find_python_312
if not defined BOOTSTRAP_PYTHON call :find_compatible_python

if not defined BOOTSTRAP_PYTHON (
    echo.
    echo [ERROR] Python 3.10 - 3.13 was not found.
    echo Install Python 3.12 from https://www.python.org/downloads/windows/
    echo and enable "Add python.exe to PATH", then run this file again.
    goto failed
)

if defined USE_PY_LAUNCHER (
    py -3.12 -m venv "%VENV_DIR%"
) else (
    "%BOOTSTRAP_PYTHON%" -m venv "%VENV_DIR%"
)

if errorlevel 1 (
    echo [ERROR] Failed to create .venv.
    goto failed
)

:check_dependencies
echo [LlamaHelper] Checking dependencies...
"%VENV_PYTHON%" -c "import importlib.metadata as m; assert tuple(map(int, m.version('PySide6').split('.')[:2])) >= (6, 8); assert int(m.version('psutil').split('.')[0]) >= 6; m.version('pyinstaller')" >nul 2>nul
if not errorlevel 1 goto launch

echo [LlamaHelper] Installing dependencies (first run may take a few minutes)...
"%VENV_PYTHON%" -m pip install -r "%~dp0requirements.txt"
if errorlevel 1 (
    echo [ERROR] Dependency installation failed. Check the network and messages above.
    goto failed
)

:launch
echo [LlamaHelper] Starting...
"%VENV_PYTHON%" "%~dp0main.py"
if errorlevel 1 (
    echo.
    echo [ERROR] LlamaHelper exited with an error.
    goto failed
)

endlocal
exit /b 0

:find_python_312
where py >nul 2>nul
if not errorlevel 1 (
    py -3.12 -c "import sys; raise SystemExit(sys.version_info[:2] != (3, 12))" >nul 2>nul
    if not errorlevel 1 (
        set "USE_PY_LAUNCHER=1"
        exit /b 0
    )
)

for /f "delims=" %%P in ('where python 2^>nul') do call :try_python_312 "%%P"
call :try_python_312 "%LocalAppData%\Programs\Python\Python312\python.exe"
call :try_python_312 "%UserProfile%\miniconda3\python.exe"
call :try_python_312 "%UserProfile%\anaconda3\python.exe"
call :try_python_312 "C:\ProgramData\miniconda3\python.exe"
call :try_python_312 "C:\ProgramData\anaconda3\python.exe"
call :try_python_312 "D:\miniconda3\python.exe"
for /d %%D in ("%UserProfile%\miniconda3\envs\*" "%UserProfile%\anaconda3\envs\*" "D:\miniconda3\envs\*") do call :try_python_312 "%%~fD\python.exe"
exit /b 0

:find_compatible_python
for /f "delims=" %%P in ('where python 2^>nul') do call :try_compatible_python "%%P"
call :try_compatible_python "%LocalAppData%\Programs\Python\Python313\python.exe"
call :try_compatible_python "%LocalAppData%\Programs\Python\Python311\python.exe"
call :try_compatible_python "%LocalAppData%\Programs\Python\Python310\python.exe"
call :try_compatible_python "%UserProfile%\miniconda3\python.exe"
call :try_compatible_python "%UserProfile%\anaconda3\python.exe"
call :try_compatible_python "C:\ProgramData\miniconda3\python.exe"
call :try_compatible_python "C:\ProgramData\anaconda3\python.exe"
call :try_compatible_python "D:\miniconda3\python.exe"
for /d %%D in ("%UserProfile%\miniconda3\envs\*" "%UserProfile%\anaconda3\envs\*" "D:\miniconda3\envs\*") do call :try_compatible_python "%%~fD\python.exe"
exit /b 0

:try_python_312
if defined BOOTSTRAP_PYTHON exit /b 0
if not exist "%~1" exit /b 0
"%~1" -c "import sys; raise SystemExit(sys.version_info[:2] != (3, 12))" >nul 2>nul
if not errorlevel 1 set "BOOTSTRAP_PYTHON=%~1"
exit /b 0

:try_compatible_python
if defined BOOTSTRAP_PYTHON exit /b 0
if not exist "%~1" exit /b 0
"%~1" -c "import sys; raise SystemExit(not ((3, 10) <= sys.version_info[:2] < (3, 14)))" >nul 2>nul
if not errorlevel 1 set "BOOTSTRAP_PYTHON=%~1"
exit /b 0

:failed
echo.
pause
endlocal
exit /b 1
