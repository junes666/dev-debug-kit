@echo off
REM 在 Windows 上打包成单文件 exe（产物：dist\开发调试.exe）
chcp 65001 >nul
cd /d "%~dp0"

echo [开发调试] 安装依赖与打包工具...
python -m pip install -r requirements.txt pyinstaller
if errorlevel 1 goto err

echo [开发调试] 开始打包（首次较慢，请耐心等待）...
python -m PyInstaller --noconfirm --clean devdebug.spec
if errorlevel 1 goto err

echo.
echo ============================================
echo  打包完成！exe 位于：dist\开发调试.exe
echo ============================================
pause
exit /b 0

:err
echo.
echo 打包失败，请检查上方错误信息。
pause
exit /b 1
