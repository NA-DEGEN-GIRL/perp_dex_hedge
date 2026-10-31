@echo off
REM Run update-mpdex.ps1 without changing execution policy (Bypass for this run only)
cd /d "%~dp0..\.."
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0update-mpdex.ps1"
