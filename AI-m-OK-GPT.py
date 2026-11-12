"""
AI'm OK GPT 版本启动器。

本文件不复制主脚本，而是加载 AI-m-OK.py，并只替换摘要模型调用与脚本备份目标。
运行前设置 OPENAI_API_KEY；默认模型为 gpt-6.1-sol，可用 OPENAI_MODEL 覆盖。
不需要打开 ChatGPT、Codex 或其他 GPT 软件。
"""

import importlib.util
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import requests as _real_requests


SCRIPT_DIR = Path(__file__).resolve().parent
SOURCE_SCRIPT = SCRIPT_DIR / "AI-m-OK.py"
OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY", "").strip()
OPENAI_MODEL = os.environ.get("OPENAI_MODEL", "gpt-6.1-sol").strip() or "gpt-6.1-sol"
OPENAI_BASE_URL = os.environ.get("OPENAI_BASE_URL", "https://api.openai.com/v1").strip().rstrip("/")
OPENAI_TIMEOUT = float(os.environ.get("OPENAI_TIMEOUT", "120"))
GPT_SUMMARY_CACHE_VERSION = os.environ.get(
    "GPT_SUMMARY_CACHE_VERSION", "summary-gpt-v1"
)
AUTO_GITHUB_BACKUP = os.environ.get("AUTO_GITHUB_BACKUP", "1").strip().lower() not in {
    "0",
    "false",
    "no",
}
SCRIPT_BACKUP_BRANCH = os.environ.get("SCRIPT_BACKUP_BRANCH", "main").strip() or "main"
SCRIPT_BACKUP_REPO_DIR = os.environ.get(
    "SCRIPT_BACKUP_REPO_DIR", r"F:\jiangxy2\AI-m-OK-code"
).strip()


def build_responses_payload(model, prompt):
    return {"model": str(model), "input": prompt if isinstance(prompt, list) else str(prompt)}


def extract_response_text(data):
    if not isinstance(data, dict):
        return ""
    direct = data.get("output_text")
    if isinstance(direct, str) and direct.strip():
        return direct.strip()
    chunks = []
    for output in data.get("output", []) or []:
        if not isinstance(output, dict):
            continue
        for content in output.get("content", []) or []:
            if not isinstance(content, dict):
                continue
            for key in ("text", "output_text"):
                value = content.get(key)
                if isinstance(value, str) and value.strip():
                    chunks.append(value.strip())
                    break
    return "\n".join(chunks).strip()


def extract_json_object(text):
    raw = str(text or "").strip()
    decoder = json.JSONDecoder()
    for match in re.finditer(r"\{", raw):
        try:
            value, _ = decoder.raw_decode(raw[match.start():])
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict):
            return value
    raise ValueError("GPT 返回内容中没有可解析的 JSON 对象")


class _Response:
    def __init__(self, response):
        self._response = response
        self.status_code = response.status_code
        self.ok = response.ok
        self.text = response.text

    def json(self):
        data = self._response.json()
        if not self.ok:
            error = data.get("error") if isinstance(data, dict) else None
            message = error.get("message") if isinstance(error, dict) else None
            raise RuntimeError(message or f"OpenAI API HTTP {self.status_code}")
        return data


class _RequestsProxy:
    """只替换主脚本的 post，其他网络请求仍使用 requests 原实现。"""

    def __init__(self, original):
        self._original = original

    def __getattr__(self, name):
        return getattr(self._original, name)

    def post(self, url, json=None, timeout=None, **kwargs):
        if url != "http://localhost:11434/api/chat":
            return self._original.post(url, json=json, timeout=timeout, **kwargs)

        messages = (json or {}).get("messages", [])
        headers = {
            "Authorization": f"Bearer {OPENAI_API_KEY}",
            "Content-Type": "application/json",
        }
        response = self._original.post(
            f"{OPENAI_BASE_URL}/responses",
            headers=headers,
            json=build_responses_payload(OPENAI_MODEL, messages),
            timeout=OPENAI_TIMEOUT,
            **kwargs,
        )
        if not response.ok:
            return _Response(response)
        data = response.json()
        if data.get("status") not in (None, "completed"):
            raise RuntimeError(f"OpenAI API 未完成生成: {data.get('status')}")
        output_text = extract_response_text(data)
        if not output_text:
            raise RuntimeError("OpenAI API 返回为空，未找到 output_text")
        result = extract_json_object(output_text)
        return _Response(_SyntheticResponse(json_module_dumps(result)))


class _SyntheticResponse:
    def __init__(self, output_text):
        self.status_code = 200
        self.ok = True
        self.text = output_text

    def json(self):
        return {"message": {"content": self.text}}


def ensure_gpt_ready():
    if not OPENAI_API_KEY:
        print("  [WARN] 未设置 OPENAI_API_KEY，GPT 摘要将回退为原标题/原摘要。")
        return False
    print(f"      GPT API 已配置: {OPENAI_MODEL} ({OPENAI_BASE_URL})")
    return True


def json_module_dumps(value):
    return json.dumps(value, ensure_ascii=False)


def _load_source_module():
    if not SOURCE_SCRIPT.exists():
        raise FileNotFoundError(f"找不到原始主脚本: {SOURCE_SCRIPT}")
    spec = importlib.util.spec_from_file_location("aimok_source_for_gpt", SOURCE_SCRIPT)
    if spec is None or spec.loader is None:
        raise ImportError(f"无法加载原始主脚本: {SOURCE_SCRIPT}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def backup_gpt_script_to_github(date_str):
    if not AUTO_GITHUB_BACKUP:
        return
    repo_dir = Path(SCRIPT_BACKUP_REPO_DIR).expanduser().resolve()
    if not (repo_dir / ".git").exists():
        print(f"[WARN] GPT 脚本备份跳过：目录不是 git 仓库 -> {repo_dir}")
        return
    target = repo_dir / "AI-m-OK-GPT.py"
    try:
        if Path(__file__).resolve() != target.resolve():
            shutil.copyfile(Path(__file__).resolve(), target)
        subprocess.run(
            ["git", "add", "--", target.name],
            cwd=str(repo_dir),
            check=True,
            capture_output=True,
        )
        diff_proc = subprocess.run(
            ["git", "diff", "--cached", "--quiet", "--", target.name],
            cwd=str(repo_dir),
            capture_output=True,
        )
        if diff_proc.returncode == 0:
            print("      GPT 脚本备份: 无新增变更")
            return
        subprocess.run(
            ["git", "commit", "-m", f"backup: AI-m-OK GPT {OPENAI_MODEL} {date_str}", "--", target.name],
            cwd=str(repo_dir),
            check=True,
            capture_output=True,
        )
        try:
            subprocess.run(
                ["git", "pull", "--rebase", "--autostash", "origin", SCRIPT_BACKUP_BRANCH],
                cwd=str(repo_dir),
                check=True,
                capture_output=True,
                timeout=60,
            )
        except Exception as pull_error:
            print(f"[WARN] GPT 脚本备份 pull --rebase 失败: {pull_error}")
        subprocess.run(
            ["git", "push", "origin", SCRIPT_BACKUP_BRANCH],
            cwd=str(repo_dir),
            check=True,
            capture_output=True,
            timeout=60,
        )
        print(f"      GPT 脚本已备份到 GitHub ({SCRIPT_BACKUP_BRANCH}) ✅")
    except Exception as error:
        print(f"[WARN] GPT 脚本自动备份失败: {error}")


def main():
    if "--help" in sys.argv or "-h" in sys.argv:
        print("AI-m-OK GPT：默认模型 gpt-6.1-sol；先设置 OPENAI_API_KEY，再运行此脚本。")
        print("不需要打开 ChatGPT/Codex。参数：--check-config（离线检查），--learn-samples。")
        return 0
    if "--learn-samples" not in sys.argv and not ensure_gpt_ready():
        return 2
    if "--check-config" in sys.argv:
        print("配置检查通过；此检查未请求 API，尚未验证密钥、模型权限及余额。")
        return 0
    source = _load_source_module()
    source.requests = _RequestsProxy(_real_requests)
    source.OLLAMA_MODEL = OPENAI_MODEL
    source.OLLAMA_URL = "http://localhost:11434/api/chat"
    source.SUMMARY_CACHE_VERSION = GPT_SUMMARY_CACHE_VERSION
    source.SUMMARY_CACHE_FILE = Path(os.environ.get(
        "GPT_SUMMARY_CACHE_FILE", str(source.STATE_DIR / "summary_cache_gpt.json")
    ))
    source._SUMMARY_CACHE = None
    original_print = source.print
    def gpt_print(*args, **kwargs):
        original_print(*(arg.replace("Ollama", "GPT API") if isinstance(arg, str) else arg for arg in args), **kwargs)
    source.print = gpt_print
    source.ensure_ollama_ready = ensure_gpt_ready
    source.backup_script_to_github = backup_gpt_script_to_github
    if "--learn-samples" in sys.argv:
        source.learn_positive_samples_only()
    else:
        source.main()
    return 0


if __name__ == "__main__":
    sys.exit(main())
