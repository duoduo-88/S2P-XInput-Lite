@echo off
setlocal

set "PROJECT_ROOT=%~dp0.."
set "PYTHON_EXE=%PROJECT_ROOT%\runtime\python.exe"

if not exist "%PYTHON_EXE%" (
    echo ERROR: Bundled Python was not found:
    echo %PYTHON_EXE%
    pause
    exit /b 1
)

echo Conservative Switch 2 Pro Controller rumble sweep
echo Modes: wired, bluetooth, esp32
set /p "SWEEP_MODE=Mode: "
echo Channels: lf, hf
set /p "SWEEP_CHANNEL=Channel: "

cd /d "%PROJECT_ROOT%"
"%PYTHON_EXE%" -B tests\live_rumble_sweep.py --mode "%SWEEP_MODE%" --channel "%SWEEP_CHANNEL%"
set "TEST_EXIT=%ERRORLEVEL%"

echo.
pause
exit /b %TEST_EXIT%
