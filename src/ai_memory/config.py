"""配置加载：~/.ai-memory/config.toml。

优先级：环境变量 > config.toml > 内置默认值。
所有段都有默认值，无配置文件也能开箱即用（默认 provider = auto：
自动接入任何可用模型，一个都探测不到时静默回退纯本地规则提炼）。
"""
import os
import re
import tomllib
from pathlib import Path

from ai_memory.paths import default_db_path

DEFAULT_CONFIG: dict = {
    "provider": {
        "name": "auto",          # auto(默认) | none | anthropic | openai
        "base_url": "",          # 指定端点；留空时 auto 模式自动探测本地服务
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
        "cold_days": 180,        # 超过且从未被搜索命中 → 回收候选
        "delete_policy": "archive_only",  # archive_only=只标记不删 | hard=软删除
    },
    "semantic": {
        "enabled": True,         # FTS 全空时是否尝试语义兜底（需要有可用的 /embeddings 端点）
        "threshold": 0.3,        # 余弦相似度阈值；低于此值的条目不返回
        "model": "",             # embedding 模型；留空则让端点用默认值
    },
}

_DEFAULT_TOML = """# ai-memory-tool 配置文件
# 全部项都有默认值，本文件可整段删除。修改后下次会话生效。

[provider]
# auto      = 默认：自动接入任何可用模型（先看环境变量密钥 → 再看本机已运行的服务 → 都没有则回退规则提炼）
# none      = 强制纯本地规则提炼，零依赖零成本、可离线
# openai    = 指定任意 OpenAI 兼容端点（本机 Ollama/LM Studio/llama.cpp/vLLM，或 DeepSeek/Kimi 等云服务）
# anthropic = Anthropic API（需 ANTHROPIC_API_KEY）
name = "auto"
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

[semantic]
enabled = true       # FTS 全空时是否尝试语义兜底（需要有可用的 /embeddings 端点，探测不到自动跳过）
threshold = 0.3      # 余弦相似度阈值，过低会带入噪声
model = ""           # embedding 模型；留空让端点用默认
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


def _fmt_toml_value(value) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int):
        return str(value)
    text = str(value).replace("\\", "\\\\").replace('"', '\\"')
    return f'"{text}"'


def save_config_values(updates: dict[str, dict], path: Path | None = None) -> Path:
    """把 {段: {键: 值}} 写回配置文件，尽量保留原有注释与未知键（web 配置页用）。

    逐行处理：命中 `key = ...` 的行只替换等号后的值，保留行尾注释；
    未命中的键追加到所在段末尾；段不存在则新建。
    """
    path = path or config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = (path.read_text(encoding="utf-8").splitlines()
             if path.exists() else _DEFAULT_TOML.splitlines())

    for section, kv in updates.items():
        header = f"[{section}]"
        start = next((i for i, line in enumerate(lines) if line.strip() == header), None)
        if start is None:
            lines.extend(["", header])
            start = len(lines) - 1
        end = len(lines)
        for i in range(start + 1, len(lines)):
            stripped = lines[i].strip()
            if stripped.startswith("[") and stripped.endswith("]"):
                end = i
                break
        for key, value in kv.items():
            pattern = re.compile(rf"^(\s*){re.escape(key)}(\s*=\s*)(.*?)(\s+#.*)?$")
            replaced = False
            for i in range(start + 1, end):
                if lines[i].lstrip().startswith("#"):
                    continue
                m = pattern.match(lines[i])
                if m:
                    lines[i] = (f"{m.group(1)}{key}{m.group(2)}"
                                f"{_fmt_toml_value(value)}{m.group(4) or ''}")
                    replaced = True
                    break
            if not replaced:
                lines.insert(end, f"{key} = {_fmt_toml_value(value)}")
                end += 1

    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path
