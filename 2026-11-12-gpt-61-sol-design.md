# AI-m-OK GPT 版本设计

## 目标

新增一个独立的 GPT 运行入口，沿用现有 `AI-m-OK.py` 的抓取、筛选、审核页面、HTML 发布、飞书推送和历史去重规则。原 Ollama 版本继续由原任务计划使用。

## 方案

新增 `AI-m-OK-GPT.py` 作为薄启动器。它通过 `importlib` 加载现有主脚本，在内存中替换摘要生成函数和脚本备份函数，然后调用原主流程。这样不会修改原脚本，也不会维护一份容易漂移的完整副本。

GPT 请求使用 OpenAI 兼容的 Responses API：

- `OPENAI_API_KEY`：必填，运行时读取。
- `OPENAI_MODEL`：默认 `gpt-6.1-sol`，可通过环境变量改成账户实际可用的模型。
- `OPENAI_BASE_URL`：默认 `https://api.openai.com/v1`，支持兼容接口。
- 请求失败或返回无法解析的 JSON 时，保留原始标题和摘要，继续完成任务。

摘要缓存使用独立版本号和模型名，避免 GPT 与 Ollama 互相复用不兼容的结果。GPT 版本的脚本备份提交到同一个备份仓库，但目标文件为 `AI-m-OK-GPT.py`，提交说明包含日期和 GPT 版本。

## 约束

- 不启动、不依赖 Ollama，也不需要打开 ChatGPT 或 Codex 软件。
- 需要联网、有效的 OpenAI API Key 和可用 API 额度。
- 不自动把 API Key 写入文件、Git 或日志。
- 新版本只处理公开抓取到的内容，沿用原脚本现有的网络和平台规则。

## 验证

离线测试验证 Responses API 请求体、文本提取、JSON 提取和缺少 API Key 时的明确失败行为；语法检查验证启动器和原主脚本均可编译。实际 API 调用留给用户在配置 API Key 后运行。
