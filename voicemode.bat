@echo off
setlocal EnableExtensions

REM Change to this folder, run, then restore directory
pushd "%~dp0"
python -m voiceapp %*
set "EC=%ERRORLEVEL%"
popd

REM Restore echo state for the caller to avoid prompt weirdness
endlocal & echo on & exit /b %EC%
