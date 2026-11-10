@echo off
chcp 65001 >nul
cd /d %~dp0

echo [1/2] 安装打包工具 PyInstaller ...
python -m pip install --upgrade pyinstaller
if errorlevel 1 goto error

echo [2/2] 打包为单文件 exe ...
python -m PyInstaller --noconfirm --clean -F -w -n KeySim keyboard_api.py
if errorlevel 1 goto error

echo.
echo 打包完成：dist\KeySim.exe
echo 双击该 exe 即可打开窗口，默认监听 8765 端口。
pause
exit /b 0

:error
echo.
echo 打包失败，请确认已安装 Python 3 并加入 PATH。
pause
exit /b 1
