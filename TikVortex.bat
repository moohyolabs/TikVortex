@echo off
rem Starts TikVortex with no Command Prompt window.
cd /d "%~dp0"
where pyw >nul 2>nul && (start "" pyw -3 "%~dp0TikVortex.pyw" %*) || (start "" pythonw "%~dp0TikVortex.pyw" %*)
