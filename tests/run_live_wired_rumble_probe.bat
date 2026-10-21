@echo off
setlocal
set "PROJECT_ROOT=%~dp0.."
set "PYTHON_EXE=%PROJECT_ROOT%\runtime\python.exe"
set "PYTHONDONTWRITEBYTECODE=1"
title S2P-XInput-Lite - Wired Rumble Probe
cd /d "%PROJECT_ROOT%"
echo Wired priority rumble stress test - 10 seconds
echo Close the main program and connect the controller by USB.
echo The test stops vibration automatically.
echo.
pause
"%PYTHON_EXE%" -B tests\live_transport_probe.py --mode wired --inline --seconds 10 --rumble-stress --rumble-profile priority
set "TEST_EXIT=%ERRORLEVEL%"
echo.
echo Exit code: %TEST_EXIT%
pause
exit /b %TEST_EXIT%
