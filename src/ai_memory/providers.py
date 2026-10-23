"""LLM Provider 抽象。

三种实现：
- NoneProvider（兜底）：纯本地规则提炼，零依赖零成本可离线。诚实降级——
  不做语义归纳，「结论」明确标注未归纳，不编造。
- OpenAICompatProvider：任意 OpenAI 兼容端点。本机已运行的 Ollama(11434)/
  LM Studio(1234)/llama.cpp(8080)/vLLM(8000) 自动探测即用；也覆盖
  DeepSeek/Kimi/OpenRouter 等云服务。无需安装 Ollama。
- AnthropicProvider：Anthropic API（可选高质量路径）。

provider 的 raw_input=True 表示 complete() 直接接收原始会话文本
（不经 summarizer 的 LLM 模板），规则提炼用。
"""
import os
import re
from typing import Optional, Protocol


class LLMProvider(Protocol):
    raw_input: bool

    def complete(self, prompt: str) -> str: ...


class FakeProvider:
    """测试用：返回固定文本，不发起真实网络调用。"""

    raw_input = False

    def __init__(self, fixed: str = ""):
        self.fixed = fixed

    def complete(self, prompt: str) -> str:
        return self.fixed


# ---------- 规则提炼（默认，无模型） ----------

_NOISE_RE = re.compile(
    r"^(你好|您好|嗨|哈喽|hi+|hello|hey|早上好|晚上好|下午好|谢谢|多谢|thanks?|thank you|"
    r"好的?|好嘞|嗯+|哦+|ok|okay|是的?|对的?|行|可以|明白|收到|继续|continue|go on|请继续|"
    r"再见|拜拜|bye)[!！。~…，,.\s]*$",
    re.IGNORECASE,
)

_ERROR_RE = re.compile(
    r"(error|exception|traceback|failed|失败|报错|错误|卡住|不行|跑不通|崩溃|异常)",
    re.IGNORECASE,
)

_MSG_RE = re.compile(r"^\[(user|assistant)\] (.*?)(?=^\[(?:user|assistant)\] |\Z)",
                     re.S | re.M)

_PATH_CMD_PATTERNS = [
    r"[\w.\-/\\:]+\.(?:py|md|json|toml|js|ts|tsx|jsx|sql|yaml|yml|html|css|txt)",
    r"\b(?:python|pytest|pip|git|npm|npx|node|uv|curl)\s+[\w.\-]+[^\s]*",
]


def extract_paths_and_commands(text: str, max_items: int = 10) -> list[str]:
    """从文本中提取文件路径与命令行。"""
    found: list[str] = []
    for pat in _PATH_CMD_PATTERNS:
        for m in re.findall(pat, text):
            if m not in found:
                found.append(m)
            if len(found) >= max_items:
                return found
    return found


def _is_noise(text: str) -> bool:
    t = text.strip()
    if not t:
        return True
    if _NOISE_RE.match(t):
        return True
    # 极短且不含路径/命令特征的消息视为噪音（「嗯嗯好的」「ok」）
    if len(t) <= 3 and not re.search(r"[./\\:]", t):
        return True
    return False


def _first_line(text: str, limit: int = 100) -> str:
    line = text.strip().splitlines()[0] if text.strip() else ""
    return line[: limit - 1] + "…" if len(line) > limit else line


class NoneProvider:
    """规则提炼：去噪 + 摘录关键消息 + 提取路径/命令。确定性、离线、零成本。"""

    raw_input = True

    def complete(self, conversation: str) -> str:
        messages = [(m.group(1), m.group(2).strip())
                    for m in _MSG_RE.finditer(conversation)]
        total = len(messages)
        substantive = [(role, text) for role, text in messages if not _is_noise(text)]
        users = [text for role, text in substantive if role == "user"]
        assistants = [text for role, text in substantive if role == "assistant"]

        goal = _first_line(users[0]) if users else "无"
        todo = _first_line(users[-1]) if len(users) > 1 else "见对话末尾原文"
        last_reply = _first_line(assistants[-1]) if assistants else "无"
        progress = (f"有效对话 {len(substantive)} 条（原始 {total} 条，已去噪）；"
                    f"最近一条 AI 回复：{last_reply}")

        blockers = []
        for _, text in substantive:
            for line in text.splitlines():
                if _ERROR_RE.search(line) and line.strip() not in blockers:
                    blockers.append(line.strip())
                if len(blockers) >= 3:
                    break
            if len(blockers) >= 3:
                break
        blocker = "；".join(_first_line(b, 80) for b in blockers) if blockers else "无"

        ctx = extract_paths_and_commands(conversation)
        context = "、".join(ctx) if ctx else "无"

        return (
            f"- 目标：{goal}\n"
            f"- 进展：{progress}\n"
            f"- 结论：（规则提炼，未做语义归纳；细节请用 search_memory 查库或回溯会话引用）\n"
            f"- 待办：{todo}\n"
            f"- 卡点：{blocker}\n"
            f"- 关键上下文：{context}"
        )


# ---------- OpenAI 兼容端点 ----------

LOCAL_CANDIDATES = (
    "http://localhost:11434/v1",  # Ollama
    "http://localhost:1234/v1",   # LM Studio
    "http://localhost:8080/v1",   # llama.cpp server
    "http://localhost:8000/v1",   # vLLM
)


def detect_local_endpoint(timeout: float = 0.6) -> Optional[tuple[str, str]]:
    """探测本机常见端口的 OpenAI 兼容服务，返回 (base_url, 第一个模型名)。"""
    import httpx

    for base in LOCAL_CANDIDATES:
        try:
            r = httpx.get(f"{base}/models", timeout=timeout)
            if r.status_code == 200:
                data = r.json().get("data") or []
                model = data[0].get("id", "") if data and isinstance(data[0], dict) else ""
                return base, model
        except Exception:
            continue
    return None


class OpenAICompatProvider:
    raw_input = False

    def __init__(self, base_url: str, model: str,
                 api_key: str = "", timeout: int = 30):
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.api_key = api_key
        self.timeout = timeout

    def complete(self, prompt: str) -> str:
        import httpx

        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        resp = httpx.post(
            f"{self.base_url}/chat/completions",
            json={
                "model": self.model,
                "messages": [{"role": "user", "content": prompt}],
                "temperature": 0.2,
            },
            headers=headers,
            timeout=self.timeout,
        )
        resp.raise_for_status()
        return resp.json()["choices"][0]["message"]["content"]


# ---------- Anthropic ----------

class AnthropicProvider:
    raw_input = False

    def __init__(self, timeout: int = 30):
        self.timeout = timeout

    def complete(self, prompt: str) -> str:
        import anthropic

        client = anthropic.Anthropic(timeout=self.timeout, max_retries=1)
        resp = client.messages.create(
            model=os.environ.get("AI_MEMORY_ANTHROPIC_MODEL", "claude-sonnet-4-6"),
            max_tokens=1024,
            messages=[{"role": "user", "content": prompt}],
        )
        return "".join(
            block.text for block in resp.content if getattr(block, "type", "") == "text"
        )


# ---------- 工厂 ----------

def provider_from_config(cfg: dict) -> LLMProvider:
    """按配置构造 provider。

    name=auto（默认）时的探测优先级：
      1) 显式 base_url（配置里写了就直接用，不探测）
      2) 环境变量密钥：ANTHROPIC_API_KEY → Anthropic；OPENAI_API_KEY → OpenAI 兼容
      3) 本机已在运行的服务：Ollama(11434)/LM Studio(1234)/llama.cpp(8080)/vLLM(8000)
      4) 以上都没有 → NoneProvider 规则提炼
    任何一步失败都静默降级，绝不抛错、不阻塞宿主启动。
    """
    p = cfg.get("provider", {})
    name = str(p.get("name") or "auto").lower()
    timeout = int(p.get("timeout_seconds", 30))
    base = str(p.get("base_url") or "")
    model = str(p.get("model") or "")
    api_key = str(p.get("api_key") or "")

    if name == "none":
        return NoneProvider()

    if name == "anthropic":
        return AnthropicProvider(timeout=timeout)

    if name in ("openai", "openai_compat", "local"):
        if not base and p.get("auto_detect", True):
            found = detect_local_endpoint()
            if found:
                base, detected_model = found
                model = model or detected_model
        if not base:
            return NoneProvider()  # 配置了但没服务可用 → 静默降级
        return OpenAICompatProvider(base, model or "default",
                                    api_key=api_key, timeout=timeout)

    # ---------- auto：自动接入任何可用模型 ----------
    if base:
        return OpenAICompatProvider(base, model or "default",
                                    api_key=api_key, timeout=timeout)
    if os.environ.get("ANTHROPIC_API_KEY"):
        return AnthropicProvider(timeout=timeout)
    if os.environ.get("OPENAI_API_KEY"):
        return OpenAICompatProvider(
            os.environ.get("OPENAI_BASE_URL") or "https://api.openai.com/v1",
            model or "gpt-4o-mini",
            api_key=os.environ["OPENAI_API_KEY"], timeout=timeout,
        )
    if p.get("auto_detect", True):
        found = detect_local_endpoint()
        if found:
            detected_base, detected_model = found
            return OpenAICompatProvider(detected_base, model or detected_model or "default",
                                        timeout=timeout)
    return NoneProvider()
