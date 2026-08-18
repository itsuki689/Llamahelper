@echo off
rem LlamaHelper PyInstaller 打包脚本（Windows 11）
setlocal
cd /d %~dp0

if not exist .venv (
    echo [build] 创建虚拟环境 .venv ...
    py -3.12 -m venv .venv || python -m venv .venv
)
call .venv\Scripts\activate.bat

echo [build] 安装依赖 ...
python -m pip install --upgrade pip
pip install -r requirements.txt

echo [build] PyInstaller 打包中 ...
pyinstaller --noconfirm --clean ^
    --onefile --windowed ^
    --name LlamaHelper ^
    --icon assets/icon.ico ^
    --add-data "assets;assets" ^
    --collect-submodules psutil ^
    main.py

if errorlevel 1 (
    echo [build] 打包失败
    exit /b 1
)
echo [build] 完成: dist\LlamaHelper.exe
endlocal
