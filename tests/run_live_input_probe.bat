@echo off
setlocal

set "PROJECT_ROOT=%~dp0.."
set "PYTHON_EXE=%PROJECT_ROOT%\runtime\python.exe"
set "PYTHONDONTWRITEBYTECODE=1"

title S2P-XInput-Lite - COM3 Live Input Probe
cd /d "%PROJECT_ROOT%"

if not exist "%PYTHON_EXE%" (
    echo ERROR: Bundled Python was not found:
    echo %PYTHON_EXE%
    echo.
    pause
    exit /b 1
)

echo COM3 live input test - 30 seconds
echo.
echo Before continuing:
echo   1. Connect the ESP32-S3.
echo   2. Close S2P-XInput-Lite and any program using COM3.
echo   3. Wake the controller and move sticks / press buttons during the test.
echo.
pause

"%PYTHON_EXE%" -B "%PROJECT_ROOT%\tests\live_input_probe.py"
set "TEST_EXIT=%ERRORLEVEL%"

echo.
if "%TEST_EXIT%"=="0" (
    echo RESULT: COM3 live input test completed successfully.
) else (
    echo RESULT: COM3 live input test failed. Exit code: %TEST_EXIT%
)
echo.
pause
exit /b %TEST_EXIT%
