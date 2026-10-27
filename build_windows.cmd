@echo off
setlocal
cd /d "%~dp0"
echo Building SSH Workbench for Windows x64...
echo Requires 64-bit Python 3.12 and internet access for build dependencies.
py -3.12 build_windows.py %*
if errorlevel 1 (
  echo.
  echo Build failed. Copy the error above for troubleshooting.
  pause
  exit /b 1
)
echo.
echo Build complete. The release folder contains the shareable ZIP.
pause
