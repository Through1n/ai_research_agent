@echo off
chcp 65001 >nul
setlocal enabledelayedexpansion
title AI4S 科研助手 Agent

rem ===============================================================
rem  AI4S 科研助手 Agent —— Windows 一键启动脚本
rem
rem  按顺序自动完成：
rem    1/5 定位项目根目录
rem    2/5 检测 conda，没有 ai_research 环境就自动创建（Python 3.11）
rem    3/5 安装 / 刷新 requirements.txt 依赖
rem    4/5 检查大模型 API Key（只看有没有，绝不打印内容）
rem    5/5 启动 streamlit run app.py 并自动打开浏览器
rem
rem  安全说明：本脚本不含任何真实 API Key，运行时也只判断「有没有」，
rem          不读取、不回显、不外传 Key 的任何内容。
rem ===============================================================

set "ENV_NAME=ai_research"
set "CONDA_ROOT="
set "CONDA_EXE="
set "CONDA_SCRIPTS="
set "PYEXE="
set "PORT=8501"
set "KEY_OK=0"
set "GO="

rem ---------- 1/5 切换到脚本所在目录（支持双击运行）----------
cd /d "%~dp0"
echo.
echo ===============================================================
echo   AI4S 科研助手 Agent · Windows 一键启动
echo ===============================================================
echo.
echo [1/5] 项目目录：%CD%

if not exist "app.py" (
    echo.
    echo ✗ 没找到 app.py —— 请把本脚本放在项目根目录再运行。
    echo.
    pause
    exit /b 1
)
if not exist "requirements.txt" (
    echo.
    echo ✗ 没找到 requirements.txt —— 当前目录不是完整的项目副本。
    echo.
    pause
    exit /b 1
)

rem ---------- 2/5 检测 conda ----------
echo [2/5] 正在检测 conda ...

where conda >nul 2>nul
if not errorlevel 1 (
    for /f "delims=" %%i in ('where conda') do (
        if not defined CONDA_EXE (
            set "CONDA_EXE=%%i"
            set "CONDA_SCRIPTS=%%~dpi"
        )
    )
)

if defined CONDA_EXE (
    rem CONDA_EXE 形如 ...\Scripts\conda.exe，往上退一层就是 conda 根目录
    for %%d in ("%CONDA_SCRIPTS%..") do set "CONDA_ROOT=%%~fd"
)

rem PATH 里找不到时，再按常见安装路径兜底
if not defined CONDA_ROOT (
    for %%d in (
        "%USERPROFILE%\anaconda3"
        "%USERPROFILE%\miniconda3"
        "%USERPROFILE%\Miniconda3"
        "C:\ProgramData\anaconda3"
        "C:\ProgramData\miniconda3"
        "C:\tools\Anaconda3"
        "D:\ProgramData\anaconda3"
    ) do (
        if not defined CONDA_ROOT if exist "%%~d\Scripts\activate.bat" set "CONDA_ROOT=%%~d"
    )
)

if not defined CONDA_ROOT (
    echo.
    echo ✗ 没有检测到 conda，常见安装路径下也没找到。
    echo.
    echo   🛠️ 三条路任选一条：
    echo     ① 装 Miniconda（Python 3.11 版）后重新双击本脚本
    echo        下载地址：https://docs.conda.io/projects/miniconda/
    echo     ② 完全不想装东西：直接用在线演示地址，浏览器打开即可
    echo        见 资料\5分钟快速体验指南.md
    echo     ③ 装了 Docker：在项目根目录执行
    echo        docker build -f Dockerfile.quick -t ai4s-quick .
    echo        然后 docker run -p 8501:8501 ai4s-quick
    echo.
    pause
    exit /b 1
)

echo ✓ 找到 conda：%CONDA_ROOT%

set "ACT=%CONDA_ROOT%\Scripts\activate.bat"
set "PYEXE=%CONDA_ROOT%\envs\%ENV_NAME%\python.exe"

rem 环境不存在 → 先创建，再直接进入该环境
if exist "%PYEXE%" (
    call "%ACT%" "%CONDA_ROOT%\envs\%ENV_NAME%"
) else (
    echo   未找到环境 %ENV_NAME%，正在自动创建（Python 3.11，约 1-3 分钟）...
    call "%ACT%" "%CONDA_ROOT%"
    conda create -y -n %ENV_NAME% python=3.11
    if errorlevel 1 (
        echo.
        echo ✗ 环境创建失败 —— 多半是网络问题，可换国内 conda 源后重试。
        echo.
        pause
        exit /b 1
    )
    if not exist "%PYEXE%" (
        echo.
        echo ✗ 环境创建命令执行完了，但没有找到 %PYEXE%
        echo   请手动执行 conda create -n %ENV_NAME% python=3.11 看具体报错。
        echo.
        pause
        exit /b 1
    )
    call "%ACT%" "%CONDA_ROOT%\envs\%ENV_NAME%"
)

"%PYEXE%" -c "print(1)" >nul 2>nul
if errorlevel 1 (
    echo.
    echo ✗ 解释器不可用：%PYEXE%
    echo.
    pause
    exit /b 1
)
for /f "delims=" %%v in ('"%PYEXE%" -c "import sys;print('%d.%d.%d'%sys.version_info[:3])"') do set "PYVER=%%v"
echo ✓ 使用 Python %PYVER%

rem ---------- 3/5 依赖检查与安装 ----------
echo [3/5] 正在检查依赖 ...
"%PYEXE%" -c "import streamlit, fitz, chromadb, pandas, numpy, plotly, networkx, dotenv, rank_bm25, psutil" >nul 2>nul
if errorlevel 1 (
    echo   依赖不完整，正在按 requirements.txt 安装（首次约 2-5 分钟）...
    "%PYEXE%" -m pip install -r requirements.txt --disable-pip-version-check
    if errorlevel 1 (
        echo.
        echo ✗ 依赖安装失败。可先换国内 pip 源再重试：
        echo   pip install -r requirements.txt -i https://pypi.tuna.tsinghua.edu.cn/simple
        echo.
        pause
        exit /b 1
    )
) else (
    echo ✓ 依赖已就绪
)

rem 想强制刷新依赖时，在本脚本前设置环境变量 UPDATE_DEPS=1
if /i "%UPDATE_DEPS%"=="1" (
    echo   检测到 UPDATE_DEPS=1，强制按 requirements.txt 刷新依赖 ...
    "%PYEXE%" -m pip install -r requirements.txt --disable-pip-version-check
)

rem ---------- 4/5 API Key 检查（只判断是否存在）----------
echo [4/5] 检查大模型 API Key ...
if defined ZHIPU_API_KEY set "KEY_OK=1"
if defined LLM_API_KEY set "KEY_OK=1"
if exist ".env" (
    findstr /r /c:"ZHIPU_API_KEY=." ".env" >nul 2>nul
    if not errorlevel 1 set "KEY_OK=1"
    findstr /r /c:"LLM_API_KEY=." ".env" >nul 2>nul
    if not errorlevel 1 set "KEY_OK=1"
)

if "%KEY_OK%"=="1" (
    echo ✓ 已检测到 API Key（为安全起见，此处不显示任何内容）
    echo   提示：也可以在网页侧边栏临时填入自己的 Key，仅存于会话内存，不落盘。
) else (
    echo.
    echo ⚠ 没有检测到 API Key（ZHIPU_API_KEY）
    echo   🛠️ 想用问答 / 翻译 / 写作这些大模型功能：
    echo      复制 .env.example 为 .env，在 ZHIPU_API_KEY= 后面填你的 Key
    echo      （智谱开放平台免费注册：https://open.bigmodel.cn/ ）
    echo   💡 没有 Key 也完全能演示：网页打开后，点侧边栏底部的
    echo      「🚀 一键加载演示数据（v1/v7/BERT）」
    echo      本地解析 / 混合检索 / 引用图谱 / 版本管理全部离线可用。
    echo.
    set /p GO=    直接回车 = 继续以离线演示模式启动，输入 q 回车 = 退出：
    if /i "!GO!"=="q" exit /b 0
)

rem ---------- 5/5 端口、浏览器、启动 ----------
echo [5/5] 启动服务 ...
for /l %%p in (8501,1,8510) do (
    netstat -ano | findstr /r /c:":%%p .*LISTENING" >nul 2>nul
    if errorlevel 1 (
        set "PORT=%%p"
        goto port_ok
    )
)
:port_ok
set "URL=http://localhost:%PORT%"

if not "%PORT%"=="8501" (
    echo ⚠ 8501 已被占用，自动改用端口 %PORT%
)

echo.
echo 🚀 正在启动 AI4S 科研助手：%URL%
echo   浏览器会自动打开。用完直接关闭本窗口，或按 Ctrl+C 停止服务。
echo   首次打开需要几秒预热，请稍等。
echo.

rem 等 6 秒让 streamlit 起来，再自动打开默认浏览器
start /min cmd /c "ping -n 7 127.0.0.1>nul & start %URL%"

"%PYEXE%" -m streamlit run app.py --server.port=%PORT% --server.headless=true --browser.gatherUsageStats=false

echo.
echo ℹ 服务已停止。
pause
