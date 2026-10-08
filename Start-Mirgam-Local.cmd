@echo off
cd /d "%~dp0"
python scripts\run_local.py
if errorlevel 1 pause
