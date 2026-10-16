"""导出最近一次摘要到 summary.md，供 CLAUDE.md @引用自动加载。"""
from pathlib import Path
from typing import Optional

from ai_memory.store import MemoryStore

_HEADER = "<!-- 本文件由 ai-memory-tool 自动生成，勿手动编辑 -->\n# 上次会话摘要\n\n"


def summary_path(directory: Path) -> Path:
    return directory / "summary.md"


def export_summary(store: MemoryStore, project: str, directory: Path,
                   extra_sessions: Optional[list] = None) -> Optional[Path]:
    """把 project 的最近一次摘要写到 directory/summary.md。无摘要返回 None。

    extra_sessions：catch-up 时同批入库的其他会话 [(session_id, summary), ...]，
    以索引段形式附在摘要末尾，提示用 search_memory 检索（摘要注入永远只有最新一条）。
    """
    entry = store.latest(project)
    if entry is None:
        return None
    body = entry.content
    if extra_sessions:
        lines = ["", "---", "", "## 其他近期会话（已入库，用 search_memory 检索）", ""]
        for session_id, summary in extra_sessions:
            first = summary.strip().splitlines()[0] if summary.strip() else ""
            lines.append(f"- `{session_id}` {first}")
        body += "\n".join(lines)
    directory.mkdir(parents=True, exist_ok=True)
    out = summary_path(directory)
    out.write_text(_HEADER + body + "\n", encoding="utf-8")
    return out
