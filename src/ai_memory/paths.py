"""路径与项目识别。"""
import os
from pathlib import Path


def project_key(project_dir: str) -> str:
    """把项目路径转为 Claude Code 的目录编码（`:`、`/`、`\\` 都替换为 `-`）。"""
    return project_dir.replace(":", "-").replace("/", "-").replace("\\", "-")


def default_db_path() -> Path:
    """记忆库 SQLite 文件路径。

    优先级：AI_MEMORY_DB 环境变量 > AI_MEMORY_HOME/memory.db > ~/.ai-memory/memory.db
    """
    if "AI_MEMORY_DB" in os.environ:
        return Path(os.environ["AI_MEMORY_DB"])
    base = Path(os.environ.get("AI_MEMORY_HOME", Path.home() / ".ai-memory"))
    return base / "memory.db"


def current_project() -> str:
    """当前工作目录对应的项目 key。"""
    return project_key(os.getcwd())
