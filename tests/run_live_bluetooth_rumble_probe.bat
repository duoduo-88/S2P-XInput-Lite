@echo off
setlocal
set "PROJECT_ROOT=%~dp0.."
set "PYTHON_EXE=%PROJECT_ROOT%\runtime\python.exe"
set "PYTHONDONTWRITEBYTECODE=1"
title S2P-XInput-Lite - Native BLE Rumble Probe
cd /d "%PROJECT_ROOT%"
echo Windows native BLE latest-only rumble stress test - 10 seconds
echo Close the main program, enable Bluetooth, and wake the controller.
echo Hold SYNC if pairing is required. The test stops vibration automatically.
echo.
pause
"%PYTHON_EXE%" -B tests\live_transport_probe.py --mode bluetooth --inline --seconds 10 --rumble-stress
set "TEST_EXIT=%ERRORLEVEL%"
echo.
echo Exit code: %TEST_EXIT%
pause
exit /b %TEST_EXIT%
