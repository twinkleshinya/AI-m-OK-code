@echo off
setlocal

chcp 65001 >nul
set PYTHONIOENCODING=utf-8
set PYTHONUTF8=1
set PYTHONDONTWRITEBYTECODE=1

set "WORKDIR=F:\jiangxy2\AI"
set "SCRIPT=%WORKDIR%\AI-m-OK.py"

cd /d "%WORKDIR%"

echo ============================================================
echo AI'm OK manual run
echo Workdir: %WORKDIR%
echo ============================================================
echo.

if not exist "%SCRIPT%" (
    echo [ERROR] Missing script: %SCRIPT%
    echo.
    pause
    exit /b 1
)

python -B "%SCRIPT%"
set "EXITCODE=%ERRORLEVEL%"

echo.
echo ============================================================
echo AI'm OK exited with code %EXITCODE%
echo ============================================================
echo.
pause
exit /b %EXITCODE%
