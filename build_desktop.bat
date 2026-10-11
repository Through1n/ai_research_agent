@echo off
REM ============================================================
REM  AI4S 科研助手 - 桌面版一键打包脚本（Windows）
REM  用法：双击本文件，或在项目根目录执行 build_desktop.bat
REM  产物：dist\AI4SResearchAgent\AI4SResearchAgent.exe 及其依赖
REM ============================================================
setlocal
set ROOT=%~dp0
cd /d "%ROOT%"

REM 自动探测 Python：优先 conda 环境 ai_research，其次当前 PATH。
REM 不硬编码任何用户目录，保证仓库公开时不泄露个人信息。
set PY=
for %%P in (
    "%USERPROFILE%\anaconda3\envs\ai_research\python.exe"
    "%USERPROFILE%\miniconda3\envs\ai_research\python.exe"
    "C:\ProgramData\anaconda3\envs\ai_research\python.exe"
    "%USERPROFILE%\anaconda3\python.exe"
    "%USERPROFILE%\miniconda3\python.exe"
    "C:\ProgramData\anaconda3\python.exe"
) do (
    if not defined PY if exist %%~P set PY=%%~P
)
if not defined PY (
    where python >nul 2>nul
    if not errorlevel 1 set PY=python
)
if not defined PY (
    echo [FAIL] 未找到 Python，请先安装 Anaconda/Miniconda，或把 python 加入 PATH
    exit /b 1
)
echo 使用 Python: %PY%

echo [1/3] 清理旧产物...
if exist build rmdir /s /q build
if exist dist rmdir /s /q dist

echo [2/3] PyInstaller 打包...
"%PY%" -m PyInstaller AI4SResearchAgent.spec --clean --noconfirm
if errorlevel 1 (
    echo [FAIL] PyInstaller 打包失败
    exit /b 1
)

echo [3/3] 体积统计...
if exist dist\AI4SResearchAgent (
    powershell -NoProfile -Command "$s=(Get-ChildItem 'dist\AI4SResearchAgent' -Recurse -File | Measure-Object Length -Sum).Sum/1MB; Write-Host ('dist\AI4SResearchAgent 总体积: {0:N1} MB' -f $s)"
    if exist dist\AI4SResearchAgent\AI4SResearchAgent.exe (
        echo 生成的 exe: dist\AI4SResearchAgent\AI4SResearchAgent.exe
    )
)
echo DONE.
endlocal
