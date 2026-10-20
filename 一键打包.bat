@echo off
chcp 65001 >nul
title 产教融合项目管理系统 - 便携包生成

cd /d "%~dp0"

echo ================================================================
echo   产教融合项目管理系统 - Windows 便携包生成
echo ================================================================
echo.
echo   将生成：pm-windows-portable.zip
echo   目标机解压双击 start.bat 即可运行，无需安装 Python
echo.
echo ================================================================
echo.

REM ---------------------------------------------------------------- 检查 PowerShell
where powershell >nul 2>&1
if errorlevel 1 (
    echo [错误] 找不到 PowerShell
    echo.
    pause
    exit /b 1
)

REM ---------------------------------------------------------------- 执行打包
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0pack-portable.ps1"

set EXITCODE=%errorlevel%

echo.
echo ================================================================
if %EXITCODE%==0 (
    echo   打包完成
    if exist "pm-windows-portable.zip" (
        for %%F in ("pm-windows-portable.zip") do set SIZE=%%~zF
        set /a SIZE_MB=%SIZE%/1048576
        echo.
        echo   文件：pm-windows-portable.zip  ^(约 %SIZE_MB% MB^)
        echo   位置：%~dp0
        echo.
        echo   下一步：
        echo     1. 把这个 zip 拷到目标服务器
        echo     2. 解压到任意目录，例如 D:\pm
        echo     3. 双击 start.bat
    )
) else (
    echo   打包失败，请查看上方错误信息
)
echo ================================================================
echo.

pause
