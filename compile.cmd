del /f /s /q dist 1>nul
rmdir /s /q dist

python -m nuitka --follow-imports videer.py

pause