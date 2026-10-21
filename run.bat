@echo off
cd /d "%~dp0"
python jarvis.py
if errorlevel 1 pause
