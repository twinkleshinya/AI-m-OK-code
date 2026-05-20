@echo off
setlocal

set "WERSS_DIR=E:\jiangxy2\werss"
set "WERSS_PY=E:\jiangxy2\werss\.venv\Scripts\python.exe"
set "WERSS_MAIN=E:\jiangxy2\werss\main.py"
set "WERSS_URL=http://127.0.0.1:8001/login"

echo ============================================================
echo WeRSS relogin helper
echo ============================================================
echo.

if not exist "%WERSS_PY%" (
  echo [ERROR] Missing Python: %WERSS_PY%
  pause
  exit /b 1
)

if not exist "%WERSS_MAIN%" (
  echo [ERROR] Missing main.py: %WERSS_MAIN%
  pause
  exit /b 1
)

powershell.exe -NoProfile -ExecutionPolicy Bypass -Command "$portOpen = Get-NetTCPConnection -LocalPort 8001 -State Listen -ErrorAction SilentlyContinue; if (-not $portOpen) { Write-Host '[INFO] Starting WeRSS service...'; Start-Process -FilePath '%WERSS_PY%' -ArgumentList 'main.py' -WorkingDirectory '%WERSS_DIR%' -WindowStyle Hidden; Start-Sleep -Seconds 6 } else { Write-Host '[OK] WeRSS service is already running.' }; Write-Host '[INFO] Opening login page...'; Start-Process '%WERSS_URL%'"

echo.
echo [OK] Login page should be open:
echo %WERSS_URL%
echo.
pause
