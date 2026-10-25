@echo off
REM ============================================================
REM  AI4S Research Agent - desktop build script (Windows)
REM  Usage: double-click this file, or run build_desktop.bat in the project root.
REM  Output: dist\AI4SResearchAgent\AI4SResearchAgent.exe + its dependencies
REM
REM  NOTE: this file is intentionally ASCII-only (no Chinese, no chcp).
REM  A UTF-8 batch file with non-ASCII text plus "chcp 65001" makes cmd.exe
REM  mis-split lines and execute the tail of a comment as a command
REM  (reproduced: 10KB file, 120 Chinese REM lines -> 7 stray commands).
REM  Chinese UI text belongs in start.bat / the Streamlit app, not here.
REM ============================================================
setlocal
set ROOT=%~dp0
cd /d "%ROOT%"

REM Auto-detect Python: conda env ai_research first, then PATH.
REM No hard-coded user paths, so the repo stays safe to publish.
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
    echo [FAIL] Python not found. Install Anaconda/Miniconda, or put python on PATH.
    pause
    exit /b 1
)
echo Python: %PY%

REM ------------------------------------------------------------
REM [1/4] Dependency preflight.
REM Packaging deps are NOT in requirements.txt (that file is the runtime
REM dependency set). Without pywebview / PyInstaller / pythonnet the build
REM cannot work, so fail here with the exact command instead of halfway in.
REM ------------------------------------------------------------
echo [1/4] Checking build dependencies...
"%PY%" -c "import webview, PyInstaller, clr" 2>nul
if errorlevel 1 (
    echo [FAIL] Missing build dependency: pywebview / PyInstaller / pythonnet.
    echo        Run:
    echo            "%PY%" -m pip install -r requirements.txt -r requirements-desktop.txt
    pause
    exit /b 1
)
echo       dependencies OK.

REM ------------------------------------------------------------
REM [2/4] Clean old output.
REM If the delete fails (running exe / antivirus lock), PyInstaller cannot
REM remove the old directory during COLLECT and SILENTLY skips generating:
REM no "Build complete!", yet the exit code may still be 0. So only warn
REM here and let step 4 decide with the exe timestamp.
REM ------------------------------------------------------------
echo [2/4] Cleaning previous output...
if exist build rmdir /s /q build 2>nul
if exist dist rmdir /s /q dist 2>nul
if exist dist echo       [WARN] dist could not be removed; step 4 will verify the artifact.

echo [3/4] Running PyInstaller...
REM Record build start time; step 4 compares the exe mtime against it.
REM If the exe is older than this, nothing was actually produced.
powershell -NoProfile -Command "Set-Content -Path (Join-Path $env:TEMP 'ai4s_build_start.txt') -Value ((Get-Date).ToString('o'))" >nul
"%PY%" -m PyInstaller AI4SResearchAgent.spec --clean --noconfirm
if errorlevel 1 (
    echo [FAIL] PyInstaller failed.
    pause
    exit /b 1
)

echo [4/4] Size and artifact check...
if exist dist\AI4SResearchAgent (
    powershell -NoProfile -Command "$s=(Get-ChildItem 'dist\AI4SResearchAgent' -Recurse -File | Measure-Object Length -Sum).Sum/1MB; Write-Host ('dist\AI4SResearchAgent total: {0:N1} MB' -f $s)"
    if exist dist\AI4SResearchAgent\AI4SResearchAgent.exe (
        echo exe: dist\AI4SResearchAgent\AI4SResearchAgent.exe
    )
)
REM Trust the exe mtime, never the PyInstaller log wording.
if exist dist\AI4SResearchAgent\AI4SResearchAgent.exe (
    powershell -NoProfile -Command "$p=Join-Path $env:TEMP 'ai4s_build_start.txt'; $start=if(Test-Path $p){[datetime]::Parse((Get-Content -Raw $p).Trim())}else{Get-Date}; $f=Get-Item 'dist\AI4SResearchAgent\AI4SResearchAgent.exe'; Write-Host ('       exe mtime = ' + $f.LastWriteTime.ToString('yyyy-MM-dd HH:mm:ss') + '  build start = ' + $start.ToString('yyyy-MM-dd HH:mm:ss')); if($f.LastWriteTime -lt $start){exit 1}"
    if errorlevel 1 (
        echo [FAIL] exe timestamp is older than the build start: nothing new was produced.
        echo        Close any running desktop app, delete dist, then run again.
        echo        If dist still cannot be deleted, build into a fresh directory:
        echo          python -m PyInstaller AI4SResearchAgent.spec --clean --noconfirm --distpath dist_new --workpath build_new
        pause
        exit /b 1
    )
    echo       artifact freshness OK.
)
echo DONE.
REM Contract section 7 requires a trailing pause: keep the window open on double-click.
pause
endlocal
