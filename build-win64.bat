@echo off
setlocal

echo [1/3] Installing dependencies...
python -m pip install -r requirements.txt
if errorlevel 1 goto :fail

echo [2/3] Building Win64 package...
python scripts\build_win64.py
if errorlevel 1 goto :fail

echo [3/3] Done.
echo Output: dist\BiliDownloader
exit /b 0

:fail
echo Build failed.
exit /b 1

