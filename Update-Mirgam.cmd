@echo off
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\install_desktop_update.ps1" -Launch
if errorlevel 1 pause
