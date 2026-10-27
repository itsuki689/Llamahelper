@echo off
rem LlamaHelper PyInstaller 打包脚本（Windows 11）
setlocal
cd /d %~dp0

if not exist .venv (
    echo [build] 创建虚拟环境 .venv ...
    py -3.12 -m venv .venv || python -m venv .venv
)
call .venv\Scripts\activate.bat

rem A venv created from Conda keeps native dependencies in base_prefix\Library\bin.
rem Add those locations so PyInstaller can bundle OpenSSL, lzma, bz2, expat and ffi.
for /f "usebackq delims=" %%I in (`python -c "import sys; print(sys.base_prefix)"`) do set "PY_BASE=%%I"
if exist "%PY_BASE%\Library\bin" set "PATH=%PY_BASE%\Library\bin;%PY_BASE%\DLLs;%PATH%"
rem Prefer Windows system DLLs over same-named DLLs from unrelated developer tools.
rem Qt uses the Windows ICU API; a third-party icuuc.dll has incompatible exports.
set "PATH=%SystemRoot%\System32;%SystemRoot%;%PATH%"
set "EXTRA_BINARIES="
if exist "%PY_BASE%\Library\bin\libcrypto-3-x64.dll" set EXTRA_BINARIES=--add-binary "%PY_BASE%\Library\bin\libcrypto-3-x64.dll;." --add-binary "%PY_BASE%\Library\bin\libssl-3-x64.dll;." --add-binary "%PY_BASE%\Library\bin\liblzma.dll;." --add-binary "%PY_BASE%\Library\bin\libbz2.dll;." --add-binary "%PY_BASE%\Library\bin\libexpat.dll;." --add-binary "%PY_BASE%\Library\bin\ffi.dll;."

echo [build] 安装依赖 ...
python -m pip install --upgrade pip
pip install -r requirements.txt

echo [build] PyInstaller 打包中 ...
pyinstaller --noconfirm --clean ^
    --onefile --windowed ^
    --name LlamaHelper ^
    --icon assets/icon.ico ^
    --add-data "assets;assets" ^
    --add-data "LICENSE;." ^
    %EXTRA_BINARIES% ^
    main.py

if errorlevel 1 (
    echo [build] 打包失败
    exit /b 1
)
echo [build] 完成: dist\LlamaHelper.exe
endlocal
