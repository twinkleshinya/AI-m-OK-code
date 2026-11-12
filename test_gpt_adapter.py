import importlib.util
from pathlib import Path


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
