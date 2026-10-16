"""读 Claude Code 的 session 文件（可替换层，防格式变动）。"""
import json
import os
from pathlib import Path
from typing import Optional


def projects_root() -> Path:
    base = os.environ.get("AI_MEMORY_CLAUDE_PROJECTS")
    if base:
        return Path(base)
    return Path.home() / ".claude" / "projects"


def _iter_session_files(root: Path, project: Optional[str]):
    """迭代 session 文件。指定 project 时只扫 root/project/*.jsonl，否则全量扫描。"""
    if project is not None:
        base = root / project
        if base.is_dir():
            yield from base.glob("*.jsonl")
        return
    yield from root.rglob("*.jsonl")


def find_sessions_since(project: Optional[str] = None, since_mtime: float = 0.0,
                        exclude_ids: frozenset = frozenset()) -> list[Path]:
    """返回 mtime > since_mtime 的 session 列表，按 mtime 升序（catch-up 补课用）。"""
    root = projects_root()
    if not root.exists():
        return []
    sessions = []
    for p in _iter_session_files(root, project):
        if p.stem in exclude_ids:
            continue
        try:
            mtime = p.stat().st_mtime
        except OSError:
            continue
        if mtime > since_mtime:
            sessions.append((mtime, p))
    sessions.sort(key=lambda t: t[0])
    return [p for _, p in sessions]


def find_latest_session(exclude_session_id: Optional[str] = None,
                        project: Optional[str] = None) -> Optional[Path]:
    """返回 mtime 最新的 session（可排除当前 session、限定项目目录）。"""
    exclude = frozenset([exclude_session_id]) if exclude_session_id else frozenset()
    sessions = find_sessions_since(project=project, exclude_ids=exclude)
    return sessions[-1] if sessions else None


def _extract_text(message) -> str:
    """从 Claude Code 消息对象里抽文本。content 可能是 str 或 list。"""
    content = message.get("content", "")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for item in content:
            if isinstance(item, dict) and item.get("type") == "text":
                parts.append(item.get("text", ""))
        return "\n".join(parts)
    return ""


def read_conversation(session_path: Path, limit: int = 100) -> str:
    """读 session jsonl，提取 user/assistant 文本消息（跳过 tool 结果），返回拼接文本。"""
    lines = []
    with open(session_path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except json.JSONDecodeError:
                continue
            t = obj.get("type")
            if t in ("user", "assistant"):
                msg = obj.get("message", {})
                text = _extract_text(msg).strip()
                if text:
                    lines.append(f"[{t}] {text}")
    return "\n".join(lines[-limit:])
