@echo off
setlocal
cd /d F:\jiangxy2\AI

if "%OPENAI_API_KEY%"=="" (
    echo [ERROR] Set OPENAI_API_KEY before running this script.
    echo See GPT version instructions for setup.
    exit /b 1
)

if "%OPENAI_MODEL%"=="" set "OPENAI_MODEL=gpt-6.1-sol"
python -B "F:\jiangxy2\AI\AI-m-OK-GPT.py" %*
set "GPT_EXIT_CODE=%ERRORLEVEL%"
endlocal & exit /b %GPT_EXIT_CODE%
