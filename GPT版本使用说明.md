# AI-m-OK GPT 版本

运行文件：`F:\jiangxy2\AI\AI-m-OK-GPT.py`

这个版本会复用 `AI-m-OK.py` 的抓取、筛选、审核、HTML 发布和飞书推送流程，只把摘要模型改成 GPT。原来的 Ollama 版本保持原样。

## 配置

在 Windows PowerShell 中设置 API Key：

```powershell
$env:OPENAI_API_KEY = "你的 API Key"
$env:OPENAI_MODEL = "gpt-6.1-sol"
python -B F:\jiangxy2\AI\AI-m-OK-GPT.py
```

也可以直接运行：

```text
F:\jiangxy2\AI\run-ai-m-ok-gpt.bat
```

可选环境变量：

- `OPENAI_MODEL`：默认 `gpt-6.1-sol`，如果 API 账户返回模型不可用，可改为账户支持的模型。
- `OPENAI_BASE_URL`：默认 `https://api.openai.com/v1`，兼容 OpenAI API 的服务可以改这个地址。
- `OPENAI_TIMEOUT`：单次请求超时时间，默认 120 秒。

## 是否需要打开 GPT 软件

不需要打开 ChatGPT、Codex 或其他 GPT 软件。脚本直接通过 HTTPS 调用 API，因此只需要网络、有效的 `OPENAI_API_KEY` 和 API 额度。

API Key 只从环境变量读取，不写入脚本、Git 或日志。GPT 摘要缓存使用独立版本，避免和 Ollama 的缓存相互混用。
