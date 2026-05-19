@echo off
setlocal

chcp 65001 >nul
set PYTHONIOENCODING=utf-8
set PYTHONUTF8=1

set "WORKDIR=F:\jiangxy2\AI"
set "SCRIPT=%WORKDIR%\AI-m-OK.py"
set "LOG=%WORKDIR%\aimok_daily.log"

cd /d "%WORKDIR%"

echo ============================================================ >> "%LOG%"
echo [%date% %time%] AI'm OK scheduled run started >> "%LOG%"

tasklist /FI "IMAGENAME eq ollama.exe" 2>NUL | find /I "ollama.exe" >NUL
if ERRORLEVEL 1 (
    echo [%date% %time%] Starting Ollama... >> "%LOG%"
    start "" /min "C:\Users\jiangxy2\AppData\Local\Programs\Ollama\ollama.exe" serve
    timeout /t 10 /nobreak >NUL
)

python -B "%SCRIPT%" >> "%LOG%" 2>&1
set "EXITCODE=%ERRORLEVEL%"

echo [%date% %time%] AI'm OK scheduled run finished with code %EXITCODE% >> "%LOG%"
exit /b %EXITCODE%
