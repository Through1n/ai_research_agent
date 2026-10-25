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
    pause
    exit /b 1
)
echo 使用 Python: %PY%

REM ------------------------------------------------------------------
REM [1/4] 依赖自检：打包依赖不在 requirements.txt 里（那是业务运行时），
REM        缺 pywebview / PyInstaller / pythonnet 时不要等到 PyInstaller 跑到一半才报错。
REM ------------------------------------------------------------------
echo [1/4] 检查打包依赖...
"%PY%" -c "import webview, PyInstaller, clr" 2>nul
if errorlevel 1 (
    echo [FAIL] 缺少桌面版打包依赖（pywebview / PyInstaller / pythonnet 之一）。
    echo        请先执行：
    echo            "%PY%" -m pip install -r requirements.txt -r requirements-desktop.txt
    pause
    exit /b 1
)
echo       依赖齐备。

REM ------------------------------------------------------------------
REM [2/4] 清理旧产物。若清理失败（文件被占用 / 安全软件拦截），
REM        PyInstaller 会在 COLLECT 阶段删不掉旧目录而**静默跳过生成**，
REM        日志里照样没有 Build complete!，但退出码可能仍是 0。
REM        所以这里只告警不中断，并在第 4 步用时间戳兜底判定。
REM ------------------------------------------------------------------
echo [2/4] 清理旧产物...
if exist build rmdir /s /q build 2>nul
if exist dist rmdir /s /q dist 2>nul
if exist dist echo       [WARN] dist 未能删除，本次构建可能沿用旧目录，请留意第 4 步结论。

echo [3/4] PyInstaller 打包...
"%PY%" -m PyInstaller AI4SResearchAgent.spec --clean --noconfirm
if errorlevel 1 (
    echo [FAIL] PyInstaller 打包失败
    pause
    exit /b 1
)

echo [4/4] 体积统计与产物校验...
if exist dist\AI4SResearchAgent (
    powershell -NoProfile -Command "$s=(Get-ChildItem 'dist\AI4SResearchAgent' -Recurse -File | Measure-Object Length -Sum).Sum/1MB; Write-Host ('dist\AI4SResearchAgent 总体积: {0:N1} MB' -f $s)"
    if exist dist\AI4SResearchAgent\AI4SResearchAgent.exe (
        echo 生成的 exe: dist\AI4SResearchAgent\AI4SResearchAgent.exe
    )
)
REM 只认 exe 的修改时间，不认 PyInstaller 的日志措辞：
REM 正常构建约 3 分钟，若 exe 时间戳还在 60 分钟以前，说明本次根本没有产出新文件。
if exist dist\AI4SResearchAgent\AI4SResearchAgent.exe (
    powershell -NoProfile -Command "$f=Get-Item 'dist\AI4SResearchAgent\AI4SResearchAgent.exe'; if($f.LastWriteTime -lt (Get-Date).AddMinutes(-60)){Write-Host ('[FAIL] exe 时间戳为 ' + $f.LastWriteTime + '，本次未产出新文件。'); Write-Host '       处置：先关掉正在运行的桌面版，再手动删除 dist 后重跑；仍失败则改用'; Write-Host '       python -m PyInstaller AI4SResearchAgent.spec --clean --noconfirm --distpath dist_new --workpath build_new'; exit 1}"
    if errorlevel 1 (
        pause
        exit /b 1
    )
    echo       产物新鲜度校验通过。
)
echo DONE.
REM 契约 §7 要求「结束 pause」：双击运行时窗口不闪退，体积统计看得见
pause
endlocal
