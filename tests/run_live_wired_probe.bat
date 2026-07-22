@echo off
setlocal

set "PROJECT_ROOT=%~dp0.."
set "PYTHON_EXE=%PROJECT_ROOT%\runtime\python.exe"
set "PYTHONDONTWRITEBYTECODE=1"

title S2P-XInput-Lite - Wired USB Probe
cd /d "%PROJECT_ROOT%"

if not exist "%PYTHON_EXE%" (
    echo ERROR: Bundled Python was not found:
    echo %PYTHON_EXE%
    echo.
    pause
    exit /b 1
)

echo Wired USB input test - 30 seconds
echo.
echo Before continuing:
echo   1. Close S2P-XInput-Lite, Steam, reWASD, and other controller programs.
echo   2. Connect the controller directly by USB.
echo   3. Move sticks and press buttons during the test.
echo.
pause

"%PYTHON_EXE%" -B "%PROJECT_ROOT%\tests\live_transport_probe.py" --mode wired --inline
set "TEST_EXIT=%ERRORLEVEL%"

echo.
if "%TEST_EXIT%"=="0" (
    echo RESULT: Wired USB test completed successfully.
) else (
    echo RESULT: Wired USB test failed. Exit code: %TEST_EXIT%
)
echo.
pause
exit /b %TEST_EXIT%
