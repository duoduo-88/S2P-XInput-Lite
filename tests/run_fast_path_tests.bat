@echo off
setlocal

set "PROJECT_ROOT=%~dp0.."
set "PYTHON_EXE=%PROJECT_ROOT%\runtime\python.exe"
set "PYTHONDONTWRITEBYTECODE=1"

title S2P-XInput-Lite - Fast Path Tests
cd /d "%PROJECT_ROOT%"

if not exist "%PYTHON_EXE%" (
    echo ERROR: Bundled Python was not found:
    echo %PYTHON_EXE%
    echo.
    pause
    exit /b 1
)

echo Running complete regression tests...
echo.
"%PYTHON_EXE%" -B tests\run_tests.py
set "TEST_EXIT=%ERRORLEVEL%"

echo.
if "%TEST_EXIT%"=="0" (
    echo RESULT: All tests passed.
) else (
    echo RESULT: Tests failed. Exit code: %TEST_EXIT%
)
echo.
pause
exit /b %TEST_EXIT%
