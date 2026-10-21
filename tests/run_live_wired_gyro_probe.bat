@echo off
setlocal

set "PROJECT_ROOT=%~dp0.."
set "PYTHON_EXE=%PROJECT_ROOT%\runtime\python.exe"
set "PYTHONDONTWRITEBYTECODE=1"

title S2P-XInput-Lite - Wired Gyro Probe
cd /d "%PROJECT_ROOT%"

if not exist "%PYTHON_EXE%" (
    echo ERROR: Bundled Python was not found:
    echo %PYTHON_EXE%
    echo.
    pause
    exit /b 1
)

echo Wired gyro test - four 15-second phases
echo.
echo This does NOT modify config.ini.
echo Close S2P-XInput-Lite, Steam, reWASD, and other controller programs.
echo Connect the controller directly by USB.
echo Continuously rotate it left/right and up/down during ALL phases.
echo.
pause

"%PYTHON_EXE%" -B "%PROJECT_ROOT%\tests\live_wired_gyro_probe.py"
set "TEST_EXIT=%ERRORLEVEL%"

echo.
if "%TEST_EXIT%"=="0" (
    echo RESULT: Wired gyro test completed successfully.
) else (
    echo RESULT: Wired gyro test failed. Exit code: %TEST_EXIT%
)
echo.
pause
exit /b %TEST_EXIT%
