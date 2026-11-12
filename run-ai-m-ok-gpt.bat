@echo off
setlocal
cd /d F:\jiangxy2\AI

if "%OPENAI_API_KEY%"=="" (
    echo [ERROR] 请先设置 OPENAI_API_KEY，再运行此脚本。
    echo 示例：setx OPENAI_API_KEY "你的API Key"
    exit /b 1
)

if "%OPENAI_MODEL%"=="" set "OPENAI_MODEL=gpt-6.1-sol"
python -B "F:\jiangxy2\AI\AI-m-OK-GPT.py" %*
endlocal
