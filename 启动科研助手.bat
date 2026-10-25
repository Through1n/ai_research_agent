@echo off
chcp 65001 >nul
REM ============================================================
REM  AI4S Research Agent - quick launcher (double-click to run)
REM
REM  Division of labour with start.bat: this file is the lightweight
REM  shortcut. It does NOT install dependencies and does NOT check the
REM  API key. It locates the conda env "ai_research" (PATH probe plus
REM  the usual install locations) and runs streamlit via "-m", so it
REM  never needs "conda activate". For a full one-click setup use start.bat.
REM
REM  Why this file is ASCII-only:
REM  a UTF-8 batch file that contains Chinese text together with chcp 65001
REM  makes cmd.exe mis-split lines and execute the tail of a comment as a
REM  command (observed: "'X' is not recognized as an internal or external
REM  command"). A pure-ASCII batch has no byte/char ambiguity, so it is
REM  immune on any console code page. Chinese copy lives in the app itself;
REM  the build script build_desktop.bat follows the same rule.
REM
REM  Failure handling: every exit path pauses, so double-clicking never
REM  flashes a window shut before the reason can be read.
REM ============================================================
cd /d "%~dp0"

if not exist "app.py" (
    echo [FAIL] app.py not found - put this file in the project root.
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
    echo [FAIL] Conda env "ai_research" not found and python is not on PATH.
    echo        Install Anaconda and create that env, or run start.bat to set it up.
    pause
    exit /b 1
)

echo Python: %PYEXE%
echo Starting AI4S Research Agent ... the browser will open http://127.0.0.1:8501
echo (close this window or press Ctrl+C to stop the server)
echo.

"%PYEXE%" -m streamlit run app.py --server.address=127.0.0.1

echo.
echo Server stopped. Press any key to close this window.
pause >nul
