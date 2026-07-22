@echo off
setlocal
set "PROJECT_ROOT=%~dp0.."
set "PYTHON_EXE=%PROJECT_ROOT%\runtime\python.exe"
set "PYTHONDONTWRITEBYTECODE=1"
title S2P-XInput-Lite - ESP32 Rumble Probe
cd /d "%PROJECT_ROOT%"
echo ESP32 latest-only rumble stress test - 10 seconds
echo Close the main program, connect the ESP32-S3, and wake the controller.
echo The test stops vibration automatically.
echo.
pause
"%PYTHON_EXE%" -B tests\live_input_probe.py --seconds 10 --rumble-stress
set "TEST_EXIT=%ERRORLEVEL%"
echo.
echo Exit code: %TEST_EXIT%
pause
exit /b %TEST_EXIT%
