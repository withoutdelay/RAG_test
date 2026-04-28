@echo off
setlocal

cd /d "%~dp0"

net session >nul 2>&1
if not "%ERRORLEVEL%"=="0" (
  echo [RAG_test] Requesting administrator permission for dependency installation...
  powershell.exe -NoProfile -ExecutionPolicy Bypass -Command "Start-Process -FilePath '%~f0' -WorkingDirectory '%~dp0' -Verb RunAs"
  exit /b 0
)

echo [RAG_test] Starting Windows one-click installer...
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\windows-install.ps1" -Action install
set EXIT_CODE=%ERRORLEVEL%

echo.
if not "%EXIT_CODE%"=="0" (
  echo [RAG_test] Installer failed with exit code %EXIT_CODE%.
  echo [RAG_test] Check the message above, then rerun this file after fixing the issue.
) else (
  echo [RAG_test] Installer finished successfully.
)

echo.
pause
exit /b %EXIT_CODE%
