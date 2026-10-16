import json
import os
from ai_memory.session_reader import (
    find_latest_session,
    find_sessions_since,
    read_conversation,
)


def _write_session(root, name, lines, mtime=None, project="proj"):
    d = root / project
    d.mkdir(parents=True, exist_ok=True)
    p = d / name
    p.write_text("".join(json.dumps(x) + "\n" for x in lines))
    if mtime is not None:
        os.utime(p, (mtime, mtime))
    return p


def test_read_conversation_extracts_user_assistant(tmp_path):
    p = _write_session(tmp_path, "s1.jsonl", [
        {"type": "user", "message": {"content": "你好"}},
        {"type": "assistant", "message": {"content": "你好，有什么可以帮你？"}},
        {"type": "user", "message": {"content": "写个函数"}},
    ])
    text = read_conversation(p)
    assert "你好" in text
    assert "写个函数" in text


def test_read_conversation_skips_tool_results(tmp_path):
    p = _write_session(tmp_path, "s1.jsonl", [
        {"type": "user", "message": {"content": "跑一下"}},
        {"type": "assistant", "message": {"content": [{"type": "tool_use", "name": "Bash"}]}},
    ])
    text = read_conversation(p)
    assert "跑一下" in text


def test_find_latest_session_returns_newest(tmp_path, monkeypatch):
    monkeypatch.setenv("AI_MEMORY_CLAUDE_PROJECTS", str(tmp_path))
    _write_session(tmp_path, "old.jsonl", [{"type": "user", "message": {"content": "x"}}], mtime=1000)
    _write_session(tmp_path, "new.jsonl", [{"type": "user", "message": {"content": "y"}}], mtime=2000)
    found = find_latest_session()
    assert found is not None and found.name == "new.jsonl"


def test_find_latest_session_excludes_current(tmp_path, monkeypatch):
    monkeypatch.setenv("AI_MEMORY_CLAUDE_PROJECTS", str(tmp_path))
    _write_session(tmp_path, "old.jsonl", [{"type": "user", "message": {"content": "x"}}], mtime=1000)
    _write_session(tmp_path, "current.jsonl", [{"type": "user", "message": {"content": "y"}}], mtime=2000)
    found = find_latest_session(exclude_session_id="current")
    assert found is not None and found.name == "old.jsonl"


def test_find_latest_session_no_files_returns_none(tmp_path, monkeypatch):
    monkeypatch.setenv("AI_MEMORY_CLAUDE_PROJECTS", str(tmp_path / "empty"))
    assert find_latest_session() is None


def test_find_latest_session_project_filter(tmp_path, monkeypatch):
    """限定项目时，其他项目更新的 session 不应被选中。"""
    monkeypatch.setenv("AI_MEMORY_CLAUDE_PROJECTS", str(tmp_path))
    _write_session(tmp_path, "a.jsonl", [{"type": "user", "message": {"content": "x"}}],
                   mtime=1000, project="proj_a")
    _write_session(tmp_path, "b.jsonl", [{"type": "user", "message": {"content": "y"}}],
                   mtime=2000, project="proj_b")
    found = find_latest_session(project="proj_a")
    assert found is not None and found.name == "a.jsonl"


def test_find_latest_session_missing_project_returns_none(tmp_path, monkeypatch):
    monkeypatch.setenv("AI_MEMORY_CLAUDE_PROJECTS", str(tmp_path))
    _write_session(tmp_path, "a.jsonl", [{"type": "user", "message": {"content": "x"}}],
                   mtime=1000, project="proj_a")
    assert find_latest_session(project="proj_b") is None


def test_find_sessions_since_filters_and_sorts_ascending(tmp_path, monkeypatch):
    """since_mtime 水位线过滤 + 按 mtime 升序返回。"""
    monkeypatch.setenv("AI_MEMORY_CLAUDE_PROJECTS", str(tmp_path))
    _write_session(tmp_path, "s1.jsonl", [{"type": "user", "message": {"content": "1"}}], mtime=1000)
    _write_session(tmp_path, "s2.jsonl", [{"type": "user", "message": {"content": "2"}}], mtime=2000)
    _write_session(tmp_path, "s3.jsonl", [{"type": "user", "message": {"content": "3"}}], mtime=3000)
    found = find_sessions_since(project="proj", since_mtime=1000)
    assert [p.name for p in found] == ["s2.jsonl", "s3.jsonl"]


def test_find_sessions_since_excludes_ids(tmp_path, monkeypatch):
    monkeypatch.setenv("AI_MEMORY_CLAUDE_PROJECTS", str(tmp_path))
    _write_session(tmp_path, "s1.jsonl", [{"type": "user", "message": {"content": "1"}}], mtime=1000)
    _write_session(tmp_path, "s2.jsonl", [{"type": "user", "message": {"content": "2"}}], mtime=2000)
    found = find_sessions_since(project="proj", exclude_ids=frozenset(["s2"]))
    assert [p.name for p in found] == ["s1.jsonl"]
