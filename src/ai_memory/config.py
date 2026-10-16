"""配置加载：~/.ai-memory/config.toml。

优先级：环境变量 > config.toml > 内置默认值。
所有段都有默认值，无配置文件也能开箱即用（默认 provider = none，零依赖）。
"""
import os
import tomllib
from pathlib import Path

from ai_memory.paths import default_db_path

DEFAULT_CONFIG: dict = {
    "provider": {
        "name": "none",          # none | anthropic | openai
        "base_url": "",          # openai 兼容端点；留空且 auto_detect=true 时自动探测本地服务
        "model": "",             # 留空时自动探测取第一个模型
        "api_key": "",
        "auto_detect": True,     # 探测 11434(Ollama)/1234(LM Studio)/8080(llama.cpp)/8000(vLLM)
        "timeout_seconds": 30,
    },
    "trigger": {
        "catch_up": False,       # 启动时是否补课处理所有未提炼会话
        "max_catchup": 10,       # 单次最多补课几个会话
    },
    "retention": {
        "enabled": False,        # 默认不自动回收
        "cold_days": 180,        # 超过且从未被访问 → 回收候选
        "delete_policy": "archive_only",  # archive_only=只标记不删 | hard=软删除
    },
}

_DEFAULT_TOML = """# ai-memory-tool 配置文件
# 全部项都有默认值，本文件可整段删除。修改后下次会话生效。

[provider]
# none     = 纯本地规则提炼，零依赖零成本（默认，开箱即用）
# openai   = 任意 OpenAI 兼容端点（本机已跑的 Ollama/LM Studio/llama.cpp/vLLM，或 DeepSeek/Kimi 等云服务）
# anthropic = Anthropic API（需 ANTHROPIC_API_KEY）
name = "none"
base_url = ""        # 例 "http://localhost:1234/v1"；留空时自动探测本地常见端口
model = ""           # 留空时取探测到的第一个模型
api_key = ""
auto_detect = true
timeout_seconds = 30

[trigger]
catch_up = false     # true 时启动会把所有未提炼的历史会话补入库（摘要只注入最新一条）
max_catchup = 10

[retention]
enabled = false      # 默认不自动回收；true 时 hook 启动顺带清理
cold_days = 180      # 超过此天数且从未被搜索命中 → 回收候选
delete_policy = "archive_only"  # archive_only：只报告不删除；hard：软删除（可用 forget 恢复前请先导出）
"""


def config_path() -> Path:
    if "AI_MEMORY_CONFIG" in os.environ:
        return Path(os.environ["AI_MEMORY_CONFIG"])
    return default_db_path().parent / "config.toml"


def load_config(path: Path | None = None) -> dict:
    """加载配置并与默认值合并（按段合并，未知键保留）。"""
    cfg = {section: dict(values) for section, values in DEFAULT_CONFIG.items()}
    path = path or config_path()
    if path.exists():
        with open(path, "rb") as f:
            user = tomllib.load(f)
        for section, values in user.items():
            if isinstance(values, dict) and section in cfg:
                cfg[section].update(values)
            else:
                cfg[section] = values
    # 环境变量覆盖（向后兼容）
    if os.environ.get("AI_MEMORY_PROVIDER"):
        cfg["provider"]["name"] = os.environ["AI_MEMORY_PROVIDER"]
    return cfg


def write_default_config(path: Path | None = None) -> Path:
    """写入带注释的默认配置（ai-memory init 用）。已存在则不覆盖。"""
    path = path or config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.exists():
        path.write_text(_DEFAULT_TOML, encoding="utf-8")
    return path
