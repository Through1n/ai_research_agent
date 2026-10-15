@echo off
chcp 65001 >nul
REM ============================================================
REM  AI4S 科研助手 - 快捷启动（双击即用）
REM
REM  与 start.bat 的分工：本文件是轻量快捷方式——不装依赖、不检查 Key，
REM  直接用 conda 环境 ai_research 启动 streamlit；完整一键搭建请用 start.bat。
REM
REM  Round 16 修正（旧文件的两个实测痛点）：
REM   1) 旧实现裸调 `conda activate` + `streamlit`，双击时若 PATH 里没有
REM      conda（常见：非 Anaconda Prompt 环境启动），会报"不是内部或外部命令"
REM      并因缺少 pause 瞬间闪退，用户看不到任何原因。
REM   2) 旧文件第 2 行注释是乱码（UTF-8 被二次转码成 U+FFFD），已重写。
REM  现在：显式定位 ai_research 环境的 python.exe（PATH 探测 + 常见路径兜底），
REM  用 `-m streamlit` 启动，不依赖 activate；任一步失败都 pause 等按键。
REM ============================================================
cd /d "%~dp0"

if not exist "app.py" (
    echo [FAIL] 未找到 app.py —— 请把本文件放在项目根目录再双击。
    pause
    exit /b 1
)

set "PYEXE="
for %%P in (
    "%USERPROFILE%\anaconda3\envs\ai_research\python.exe"
    "%USERPROFILE%\miniconda3\envs\ai_research\python.exe"
    "C:\ProgramData\anaconda3\envs\ai_research\python.exe"
) do (
    if not defined PYEXE if exist %%P set "PYEXE=%%~P"
)
if not defined PYEXE (
    where python >nul 2>nul
    if not errorlevel 1 set "PYEXE=python"
)
if not defined PYEXE (
    echo [FAIL] 未找到 Python 环境 ai_research，PATH 里也没有 python。
    echo         请先安装 Anaconda 并创建 ai_research 环境，或改用 start.bat 自动搭建。
    pause
    exit /b 1
)

echo 使用 Python: %PYEXE%
echo 正在启动 AI4S 科研助手 ... 浏览器将打开 http://127.0.0.1:8501
echo （关闭本窗口或按 Ctrl+C 停止服务）
echo.

"%PYEXE%" -m streamlit run app.py --server.address=127.0.0.1

echo.
echo 服务已停止。按任意键关闭窗口。
pause >nul
