import importlib.util
import json
from pathlib import Path

import pytest


SCRIPT = Path(__file__).with_name("AI-m-OK-GPT.py")


def load_adapter():
    spec = importlib.util.spec_from_file_location("aimok_gpt_test", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_build_responses_payload_uses_selected_model_and_input():
    module = load_adapter()
    payload = module.build_responses_payload("gpt-6.1-sol", "请输出JSON")

    assert payload == {
        "model": "gpt-6.1-sol",
        "input": "请输出JSON",
    }


def test_extract_response_text_reads_output_text():
    module = load_adapter()
    data = {"output_text": "{\"ai_related\":true}"}

    assert module.extract_response_text(data) == '{"ai_related":true}'


def test_extract_json_object_ignores_surrounding_text():
    module = load_adapter()

    assert module.extract_json_object("结果如下：{\"category\":\"开源\"}谢谢") == {
        "category": "开源"
    }


def test_extract_json_object_keeps_nested_data_and_braces_in_strings():
    module = load_adapter()
    value = {"title_zh": "使用 {模板}", "metadata": {"category": "开源"}}
    assert module.extract_json_object("```json\n" + json.dumps(value) + "\n```") == value


def test_payload_preserves_correction_conversation_roles():
    module = load_adapter()
    conversation = [
        {"role": "user", "content": "请生成摘要"},
        {"role": "assistant", "content": "旧摘要"},
        {"role": "user", "content": "请纠正事实"},
    ]
    assert module.build_responses_payload("gpt-6.1-sol", conversation)["input"] == conversation


class FakeResponse:
    def __init__(self, data, status=200):
        self.data = data
        self.status_code = status
        self.ok = status < 400
        self.text = json.dumps(data, ensure_ascii=False)

    def json(self):
        return self.data


class FakeTransport:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def post(self, url, **kwargs):
        self.calls.append((url, kwargs))
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response


def make_api_response(**changes):
    summary = dict(
        ai_related=True, practical_reusable=True, emoji="🤖",
        title_zh="开源智能体教程演示自动整理资料",
        summary_zh="教程提供源码与具体操作步骤，介绍如何用智能体整理资料，可作为工作流实践参考。",
        category="开源",
    )
    summary.update(changes)
    return FakeResponse({
        "status": "completed",
        "output": [{"type": "message", "content": [
            {"type": "output_text", "text": json.dumps(summary, ensure_ascii=False)}
        ]}],
    })


def prepare_summary_flow(monkeypatch, tmp_path, responses):
    module = load_adapter()
    monkeypatch.setattr(module, "OPENAI_API_KEY", "offline-test-key")
    monkeypatch.setattr(module, "OPENAI_TIMEOUT", 17)
    source = module._load_source_module()
    transport = FakeTransport(responses)
    source.requests = module._RequestsProxy(transport)
    source.ensure_ollama_ready = module.ensure_gpt_ready
    source.OLLAMA_MODEL = module.OPENAI_MODEL
    source.SUMMARY_CACHE_FILE = tmp_path / "summaries.json"
    source._SUMMARY_CACHE = None
    monkeypatch.setattr(source, "fetch_content_context", lambda item: "教程公开源代码和操作步骤。")
    item = {"source": "Hacker News", "title": "Open source agent tutorial",
            "summary": "A practical tutorial with source code.", "url": "https://example.org/tutorial"}
    return module, source, transport, item


def test_real_summary_flow_uses_gpt_and_caches_result(monkeypatch, tmp_path):
    module, source, transport, item = prepare_summary_flow(monkeypatch, tmp_path, [make_api_response()])
    result = source.generate_chinese_summaries([item])
    assert result[0]["title_zh"] == "开源智能体教程演示自动整理资料"
    assert result[0]["category"] == "开源"
    assert not result[0].get("_summary_generation_failed")
    assert source.SUMMARY_CACHE_FILE.exists()
    url, request = transport.calls[0]
    assert url == module.OPENAI_BASE_URL + "/responses"
    assert request["timeout"] == 17
    assert request["json"]["model"] == module.OPENAI_MODEL
    assert "反幻觉" in request["json"]["input"][0]["content"]
    source.generate_chinese_summaries([dict(item)])
    assert len(transport.calls) == 1


def test_real_summary_flow_keeps_filter_rules(monkeypatch, tmp_path):
    _, source, _, item = prepare_summary_flow(monkeypatch, tmp_path, [make_api_response(practical_reusable=False)])
    assert source.generate_chinese_summaries([item]) == []


def test_real_summary_flow_preserves_correction_retry(monkeypatch, tmp_path):
    _, source, transport, item = prepare_summary_flow(monkeypatch, tmp_path, [make_api_response(), make_api_response()])
    validations = iter([(False, "需要纠正"), (True, "")])
    monkeypatch.setattr(source, "validate_summary_facts", lambda *args: next(validations))
    source.generate_chinese_summaries([item])
    assert len(transport.calls) == 2
    messages = transport.calls[1][1]["json"]["input"]
    assert [message["role"] for message in messages] == ["user", "assistant", "user"]
    assert "需要纠正" in messages[-1]["content"]


@pytest.mark.parametrize("response", [
    FakeResponse({"error": {"message": "model unavailable"}}, 404),
    FakeResponse({"status": "incomplete", "output_text": '{"title_zh":"部分输出"}'}),
    FakeResponse({"output_text": "not JSON"}),
    TimeoutError("offline timeout"),
])
def test_failed_requests_keep_originals_and_do_not_cache(monkeypatch, tmp_path, response):
    _, source, _, item = prepare_summary_flow(monkeypatch, tmp_path, [response])
    result = source.generate_chinese_summaries([item])
    assert result[0]["title_zh"] == item["title"]
    assert result[0]["summary_zh"] == item["summary"]
    assert result[0]["_summary_generation_failed"] is True
    assert not source._SUMMARY_CACHE


def test_main_stops_before_crawling_when_key_missing(monkeypatch):
    module = load_adapter()
    monkeypatch.setattr(module, "OPENAI_API_KEY", "")
    monkeypatch.setattr(module, "_load_source_module", lambda: pytest.fail("不应开始加载和抓取"))
    monkeypatch.setattr(module.sys, "argv", [str(SCRIPT)])
    assert module.main() == 2
