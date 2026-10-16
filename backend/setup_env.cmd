@echo off
rem ----------------------------------------------------------------------
rem  ASCII ONLY. DO NOT PUT NON-ASCII BYTES IN THIS FILE, INCLUDING COMMENTS.
rem
rem  cmd.exe parses .cmd files with the system ANSI code page. This file used
rem  to be UTF-8 with Chinese messages; cmd read the mis-decoded bytes as
rem  commands, the script died right after "chcp", and "uv sync" never ran.
rem  Symptom seen on a packaged first run: "python env preparation failed",
rem  no venv created, exit code 1. uv prints English progress anyway, so keep
rem  every byte here 7-bit and it cannot happen again on any locale.
rem ----------------------------------------------------------------------
chcp 65001 >nul
cd /d "%~dp0"

echo ============================================
echo   Soul Mate - first run: install Python deps
echo ============================================
echo.
echo This runs only once. It needs an internet connection and may take a
echo few minutes. The app window keeps waiting and continues by itself.
echo.

"%~dp0uv.exe" sync --frozen --no-dev
if errorlevel 1 goto failed

echo.
echo Done. This window closes in a few seconds.
timeout /t 3 >nul
exit /b 0

:failed
echo.
echo ********************************************
echo   Setup FAILED.
echo   Please copy the messages above to the developer.
echo ********************************************
echo (this window closes in 2 minutes)
timeout /t 120 >nul
exit /b 1
