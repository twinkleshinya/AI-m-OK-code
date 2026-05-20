@echo off
setlocal

chcp 65001 >nul
set "WERSS_DIR=E:\jiangxy2\werss"
set "WERSS_PY=E:\jiangxy2\werss\.venv\Scripts\python.exe"
set "WERSS_MAIN=E:\jiangxy2\werss\main.py"
set "WERSS_URL=http://127.0.0.1:8001/login"

echo ============================================================
echo WeRSS 重新登录助手
echo ============================================================
echo.

if not exist "%WERSS_PY%" (
  echo [ERROR] 找不到 WeRSS Python: %WERSS_PY%
  pause
  exit /b 1
)

if not exist "%WERSS_MAIN%" (
  echo [ERROR] 找不到 WeRSS main.py: %WERSS_MAIN%
  pause
  exit /b 1
)

powershell -NoProfile -ExecutionPolicy Bypass -Command ^
  "$portOpen = Get-NetTCPConnection -LocalPort 8001 -State Listen -ErrorAction SilentlyContinue; ^
   if (-not $portOpen) { ^
     Write-Host '[INFO] WeRSS 服务未运行，正在启动...'; ^
     Start-Process -FilePath '%WERSS_PY%' -ArgumentList 'main.py' -WorkingDirectory '%WERSS_DIR%' -WindowStyle Hidden; ^
     Start-Sleep -Seconds 5; ^
   } else { ^
     Write-Host '[OK] WeRSS 服务已在运行。'; ^
   }; ^
   Start-Process '%WERSS_URL%'"

echo.
echo [OK] 已打开 WeRSS 登录页：%WERSS_URL%
echo 如果页面提示微信授权过期，请在页面里重新扫码登录微信账号。
echo.
pause
