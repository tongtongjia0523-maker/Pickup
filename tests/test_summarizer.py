from ai_memory.summarizer import build_prompt, summarize
from ai_memory.providers import FakeProvider


def test_build_prompt_contains_required_fields():
    p = build_prompt("一段对话")
    for field in ["目标", "进展", "结论", "待办", "卡点", "关键上下文"]:
        assert field in p
    assert "一段对话" in p


def test_build_prompt_asks_to_ignore_noise():
    assert "忽略" in build_prompt("x")


def test_summarize_uses_provider():
    p = FakeProvider("提炼结果")
    assert summarize("对话", p) == "提炼结果"


def test_summarize_passes_prompt_to_provider():
    seen = {}

    class Recording(FakeProvider):
        def complete(self, prompt):
            seen["prompt"] = prompt
            return "ok"

    summarize("对话内容", Recording())
    assert "对话内容" in seen["prompt"]
