del /f /s /q dist 1>nul
rmdir /s /q dist
mkdir dist

python -m nuitka videer.py

robocopy videer.dist dist /MOVE /E

pause