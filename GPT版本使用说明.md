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
- `GPT_SUMMARY_CACHE_FILE`：GPT 摘要缓存路径，默认使用状态目录下的 `summary_cache_gpt.json`。

执行 `python -B F:\jiangxy2\AI\AI-m-OK-GPT.py --check-config` 可以离线检查配置；这不会请求 API，也不能验证密钥、模型权限和余额。缺少密钥时，普通启动会提前退出。配置密钥后，单条摘要请求失败会沿用原标题和摘要。

PowerShell 中的 `$env:` 只对当前窗口和从它启动的程序生效。直接在同一个 PowerShell 窗口运行批处理也可以；若另开窗口或设置计划任务，需要让那个运行环境也能读取密钥。

GPT 版沿用原脚本的审核页面、发布目录、历史去重及飞书接收配置。仍需按原流程人工审核。未改动原计划任务，也未创建 GPT 定时任务。

## 是否需要打开 GPT 软件

不需要打开 ChatGPT、Codex 或其他 GPT 软件。脚本直接通过 HTTPS 调用 API，因此只需要网络、有效的 `OPENAI_API_KEY` 和 API 额度。

ChatGPT 订阅与 API 分开计费。本次仅完成离线验证，本机尚未配置 API Key，因此未进行真实模型请求。

API Key 只从环境变量读取，不写入脚本、Git 或日志。GPT 摘要缓存使用独立版本，避免和 Ollama 的缓存相互混用。
