from ai_memory.config import DEFAULT_CONFIG, load_config
from ai_memory.providers import (
    AnthropicProvider,
    FakeProvider,
    NoneProvider,
    OpenAICompatProvider,
    extract_paths_and_commands,
    provider_from_config,
)


def _cfg(section: dict) -> dict:
    cfg = {s: dict(v) for s, v in DEFAULT_CONFIG.items()}
    cfg["provider"].update(section)
    return cfg


# ---------- FakeProvider ----------

def test_fake_provider_returns_fixed_text():
    p = FakeProvider("固定回复")
    assert p.complete("任意 prompt") == "固定回复"


# ---------- 工厂 ----------

def test_default_is_auto_and_falls_back_to_none(tmp_path, monkeypatch):
    """默认 auto：本机没有任何可用模型时，静默回退到规则提炼。"""
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.setattr("ai_memory.providers.detect_local_endpoint",
                        lambda timeout=0.6: None)
    cfg = load_config(tmp_path / "none.toml")
    assert cfg["provider"]["name"] == "auto"
    assert isinstance(provider_from_config(cfg), NoneProvider)


def test_anthropic_from_config():
    assert isinstance(provider_from_config(_cfg({"name": "anthropic"})), AnthropicProvider)


def test_openai_with_base_url():
    p = provider_from_config(_cfg({"name": "openai", "base_url": "http://x:1/v1", "model": "m"}))
    assert isinstance(p, OpenAICompatProvider)
    assert p.base_url == "http://x:1/v1" and p.model == "m"


def test_openai_no_endpoint_no_detect_falls_back_to_none():
    p = provider_from_config(_cfg({"name": "openai", "auto_detect": False}))
    assert isinstance(p, NoneProvider)  # 静默降级


def test_openai_auto_detect_used(monkeypatch):
    monkeypatch.setattr("ai_memory.providers.detect_local_endpoint",
                        lambda timeout=0.6: ("http://localhost:1234/v1", "qwen"))
    p = provider_from_config(_cfg({"name": "openai"}))
    assert isinstance(p, OpenAICompatProvider)
    assert p.base_url == "http://localhost:1234/v1" and p.model == "qwen"


def test_unknown_name_falls_back_to_none(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.setattr("ai_memory.providers.detect_local_endpoint",
                        lambda timeout=0.6: None)
    assert isinstance(provider_from_config(_cfg({"name": "ollama"})), NoneProvider)


# ---------- auto：自动接入可用模型 ----------

def test_auto_uses_explicit_base_url():
    p = provider_from_config(_cfg({"name": "auto", "base_url": "http://x:1/v1", "model": "m"}))
    assert isinstance(p, OpenAICompatProvider)
    assert p.base_url == "http://x:1/v1" and p.model == "m"


def test_auto_prefers_anthropic_key(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-openai")
    assert isinstance(provider_from_config(_cfg({"name": "auto"})), AnthropicProvider)


def test_auto_uses_openai_key_and_base_url(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.setenv("OPENAI_API_KEY", "sk-openai")
    monkeypatch.setenv("OPENAI_BASE_URL", "https://api.deepseek.com/v1")
    p = provider_from_config(_cfg({"name": "auto"}))
    assert isinstance(p, OpenAICompatProvider)
    assert p.base_url == "https://api.deepseek.com/v1"
    assert p.api_key == "sk-openai"


def test_auto_detects_local_endpoint(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.setattr("ai_memory.providers.detect_local_endpoint",
                        lambda timeout=0.6: ("http://localhost:11434/v1", "llama3"))
    p = provider_from_config(_cfg({"name": "auto"}))
    assert isinstance(p, OpenAICompatProvider)
    assert p.base_url == "http://localhost:11434/v1" and p.model == "llama3"


def test_auto_falls_back_to_none(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.setattr("ai_memory.providers.detect_local_endpoint",
                        lambda timeout=0.6: None)
    assert isinstance(provider_from_config(_cfg({"name": "auto"})), NoneProvider)


# ---------- 本地探测 ----------

def test_detect_local_endpoint_none_when_all_down(monkeypatch):
    import httpx

    def _boom(*a, **k):
        raise httpx.ConnectError("down")

    monkeypatch.setattr(httpx, "get", _boom)
    from ai_memory.providers import detect_local_endpoint
    assert detect_local_endpoint() is None


# ---------- 规则提炼 ----------

_CONV = """[user] 你好
[assistant] 你好，有什么可以帮你？
[user] 帮我重构 src/ai_memory/store.py 的搜索功能
[assistant] 好的，我会用 FTS5 实现。先运行 pytest 测试一下。
[user] 嗯
[user] 测试跑不通，报错了：sqlite3.OperationalError: no such table
[assistant] 我来修复这个问题
"""


def test_none_provider_filters_noise():
    out = NoneProvider().complete(_CONV)
    assert "已去噪" in out
    assert "你好" not in out.split("目标：")[1].splitlines()[0]


def test_none_provider_goal_and_todo():
    out = NoneProvider().complete(_CONV)
    assert "- 目标：帮我重构 src/ai_memory/store.py 的搜索功能" in out
    assert "测试跑不通" in out.split("- 待办：")[1].splitlines()[0]


def test_none_provider_extracts_context():
    out = NoneProvider().complete(_CONV)
    ctx_line = out.split("- 关键上下文：")[1]
    assert "src/ai_memory/store.py" in ctx_line


def test_none_provider_detects_blocker():
    out = NoneProvider().complete(_CONV)
    assert "OperationalError" in out.split("- 卡点：")[1].splitlines()[0]


def test_none_provider_honest_about_conclusion():
    out = NoneProvider().complete(_CONV)
    assert "未做语义归纳" in out  # 诚实降级，不编造结论


def test_none_provider_empty_conversation():
    out = NoneProvider().complete("")
    assert "- 目标：无" in out


def test_extract_paths_and_commands():
    found = extract_paths_and_commands("修改了 a/b.py，然后跑 pytest tests/ -v")
    assert "a/b.py" in found
    assert any(x.startswith("pytest") for x in found)


# ---------- summarizer 的 raw_input 分支 ----------

def test_summarize_raw_input_skips_template():
    from ai_memory.summarizer import summarize

    class Recording(NoneProvider):
        def complete(self, text):
            self.seen = text
            return "ok"

    p = Recording()
    assert summarize("[user] 原始对话", p) == "ok"
    assert p.seen == "[user] 原始对话"  # 未被套进 LLM 模板
