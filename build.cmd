@echo off
rem Build a standalone videer.exe distribution (no Python needed on the target PC)
cd /d "%~dp0"
python -m pip install -r requirements.txt nuitka
python build.py
pause
