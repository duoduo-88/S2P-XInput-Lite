@echo off
setlocal

set "PROJECT_ROOT=%~dp0.."
set "PYTHON_EXE=%PROJECT_ROOT%\runtime\python.exe"
set "PYTHONDONTWRITEBYTECODE=1"

title S2P-XInput-Lite - Native Bluetooth Probe
cd /d "%PROJECT_ROOT%"

if not exist "%PYTHON_EXE%" (
    echo ERROR: Bundled Python was not found:
    echo %PYTHON_EXE%
    echo.
    pause
    exit /b 1
)

echo Native Bluetooth input test - 30 seconds
echo.
echo Before continuing:
echo   1. Close S2P-XInput-Lite and other controller programs.
echo   2. Disconnect the ESP32-S3 and USB controller cable.
echo   3. Turn on Windows Bluetooth.
echo   4. Wake the controller. Hold SYNC if native pairing is required.
echo   5. Move sticks and press buttons during the test.
echo.
pause

"%PYTHON_EXE%" -B "%PROJECT_ROOT%\tests\live_transport_probe.py" --mode bluetooth --inline
set "TEST_EXIT=%ERRORLEVEL%"

echo.
if "%TEST_EXIT%"=="0" (
    echo RESULT: Native Bluetooth test completed successfully.
) else (
    echo RESULT: Native Bluetooth test failed. Exit code: %TEST_EXIT%
)
echo.
pause
exit /b %TEST_EXIT%
