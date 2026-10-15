@echo off
setlocal

title LLM Controller CE

cd /d "%~dp0"

echo ==================================================
echo   LLM Controller CE v1.5
echo ==================================================
echo.
echo Starting the application from:
echo   %CD%
echo.
echo Keep this window open while LLM Controller CE is running.
echo.
echo Recommended Command Prompt Window Settings
echo Disable - QuickEdit Mode
echo         - Insert Mode
echo         - Enable line wrapping selection
echo         - Extended text selection keys
echo.

if exist "%~dp0venv\Scripts\python.exe" (
    set "CE_PYTHON=%~dp0venv\Scripts\python.exe"
) else (
    where python >nul 2>&1
    if errorlevel 1 (
        echo ERROR: Python was not found on PATH.
        echo Install Python and make sure the ^'python^' command is available, then try again.
        echo.
        pause
        exit /b 1
    )
    set "CE_PYTHON=python"
)

if not exist "app.py" (
    echo ERROR: app.py was not found in:
    echo   %CD%
    echo Run this launcher from the LLM Controller CE project root.
    echo.
    pause
    exit /b 1
)

echo Launching: app.py
echo.
"%CE_PYTHON%" app.py
set "APP_EXIT_CODE=%ERRORLEVEL%"

if not "%APP_EXIT_CODE%"=="0" (
    echo.
    echo ERROR: LLM Controller CE stopped with exit code %APP_EXIT_CODE%.
    echo Review the app output above for the startup or runtime failure details.
    echo.
    pause
    exit /b %APP_EXIT_CODE%
)

echo.
echo LLM Controller CE exited normally.
echo.
pause
exit /b 0
