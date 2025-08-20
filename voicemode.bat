@echo off
setlocal EnableExtensions

REM Change to this folder without using the directory stack
REM Using pushd/popd can leave an extra "+" in some cmd prompts if Ctrl+C aborts
REM before popd runs. cd /d avoids that artifact.
set "STARTDIR=%CD%"
cd /d "%~dp0"

python -m voiceapp %*
set "EC=%ERRORLEVEL%"

REM Restore starting directory (best-effort; if batch was aborted with Ctrl+C, this
REM may not run, but at least we haven't modified the pushd stack)
cd /d "%STARTDIR%"

endlocal & exit /b %EC%
