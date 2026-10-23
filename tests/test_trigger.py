import json
import os
from pathlib import Path
from ai_memory.providers import FakeProvider
from ai_memory.trigger import classify_session, run_trigger
from ai_memory.store import MemoryStore


# ---------- v3：会话分类 ----------

def test_classify_scratch_for_short_session():
    assert classify_session("[user] 帮我写个脚本\n[assistant] 好的，用 requests") == "scratch"


def test_classify_archive_when_artifact_present():
    text = "[user] 实现登录\n[user] 再改一下\n[assistant] 已写入 auth.py\n```python\npass\n```"
    assert classify_session(text) == "archive"


def test_classify_session_summary_for_regular_session():
    text = "".join(f"[user] 第{i}个问题\n" for i in range(5))
    assert classify_session(text) == "session_summary"


def _write_session(root: Path, name: str, text: str, mtime=None):
    d = root / "proj"
    d.mkdir(parents=True, exist_ok=True)
    p = d / name
    p.write_text(json.dumps({"type": "user", "message": {"content": text}}) + "\n")
    if mtime is not None:
        os.utime(p, (mtime, mtime))
    return p


def test_run_trigger_writes_summary(tmp_path, monkeypatch):
    monkeypatch.setenv("AI_MEMORY_CLAUDE_PROJECTS", str(tmp_path / "projects"))
    monkeypatch.setenv("AI_MEMORY_DB", str(tmp_path / "m.db"))
    _write_session(tmp_path / "projects", "old.jsonl", "上次的对话")
    store = MemoryStore(Path(tmp_path / "m.db"))

    result = run_trigger(store, "proj", tmp_path, FakeProvider("提炼摘要"))

    assert result is not None
    assert store.latest_any("proj").content == "提炼摘要"
    assert (tmp_path / "summary.md").read_text(encoding="utf-8").find("提炼摘要") != -1


def test_run_trigger_no_session_returns_none(tmp_path, monkeypatch):
    monkeypatch.setenv("AI_MEMORY_CLAUDE_PROJECTS", str(tmp_path / "nonexistent"))
    monkeypatch.setenv("AI_MEMORY_DB", str(tmp_path / "m.db"))
    store = MemoryStore(Path(tmp_path / "m.db"))
    assert run_trigger(store, "proj", tmp_path, FakeProvider("x")) is None


def test_run_trigger_dedup_same_session(tmp_path, monkeypatch):
    """同一 session 二次触发只入库一条，且不重复调用 provider。"""
    monkeypatch.setenv("AI_MEMORY_CLAUDE_PROJECTS", str(tmp_path / "projects"))
    monkeypatch.setenv("AI_MEMORY_DB", str(tmp_path / "m.db"))
    _write_session(tmp_path / "projects", "s1.jsonl", "对话内容")
    store = MemoryStore(Path(tmp_path / "m.db"))

    assert run_trigger(store, "proj", tmp_path, FakeProvider("摘要")) is not None
    assert run_trigger(store, "proj", tmp_path, FakeProvider("不应出现")) is not None

    entries = store.all("proj")
    assert len(entries) == 1
    assert entries[0].content == "摘要"
    # 二次触发仍导出已有摘要，summary.md 保持可用
    assert "摘要" in (tmp_path / "summary.md").read_text(encoding="utf-8")


def test_run_trigger_ignores_other_projects(tmp_path, monkeypatch):
    """默认项目内扫描：其他项目的 session 不被本项目提炼。"""
    monkeypatch.setenv("AI_MEMORY_CLAUDE_PROJECTS", str(tmp_path / "projects"))
    monkeypatch.setenv("AI_MEMORY_DB", str(tmp_path / "m.db"))
    proj_root = tmp_path / "projects"
    other = proj_root / "other_proj"
    other.mkdir(parents=True)
    (other / "s1.jsonl").write_text(
        json.dumps({"type": "user", "message": {"content": "别人的对话"}}) + "\n"
    )
    store = MemoryStore(Path(tmp_path / "m.db"))

    assert run_trigger(store, "proj", tmp_path, FakeProvider("x")) is None
    assert store.all("proj") == []


def test_run_trigger_catch_up_archives_all_but_injects_latest(tmp_path, monkeypatch):
    """catch-up：多个未入库 session 全部入库，summary.md 只注入最新一条 + 索引段。"""
    monkeypatch.setenv("AI_MEMORY_CLAUDE_PROJECTS", str(tmp_path / "projects"))
    monkeypatch.setenv("AI_MEMORY_DB", str(tmp_path / "m.db"))
    proj_root = tmp_path / "projects"
    _write_session(proj_root, "s1.jsonl", "第一次对话", mtime=1000)
    _write_session(proj_root, "s2.jsonl", "第二次对话", mtime=2000)
    _write_session(proj_root, "s3.jsonl", "第三次对话", mtime=3000)
    store = MemoryStore(Path(tmp_path / "m.db"))

    result = run_trigger(store, "proj", tmp_path, FakeProvider("摘要"), catch_up=True)

    assert result is not None
    assert len(store.all("proj")) == 3
    text = (tmp_path / "summary.md").read_text(encoding="utf-8")
    assert "其他近期会话" in text
    assert "s1" in text and "s2" in text


def test_run_trigger_catch_up_respects_max(tmp_path, monkeypatch):
    """catch-up 上限：只补最近 max_catchup 个。"""
    monkeypatch.setenv("AI_MEMORY_CLAUDE_PROJECTS", str(tmp_path / "projects"))
    monkeypatch.setenv("AI_MEMORY_DB", str(tmp_path / "m.db"))
    proj_root = tmp_path / "projects"
    for i in range(5):
        _write_session(proj_root, f"s{i}.jsonl", f"对话{i}", mtime=1000 + i)
    store = MemoryStore(Path(tmp_path / "m.db"))

    run_trigger(store, "proj", tmp_path, FakeProvider("摘要"),
                catch_up=True, max_catchup=2)

    entries = store.all("proj")
    assert len(entries) == 2
    assert {e.session_id for e in entries} == {"s3", "s4"}


def test_run_trigger_catch_up_skips_already_archived(tmp_path, monkeypatch):
    """catch-up 幂等：已入库的 session 不重复提炼。"""
    monkeypatch.setenv("AI_MEMORY_CLAUDE_PROJECTS", str(tmp_path / "projects"))
    monkeypatch.setenv("AI_MEMORY_DB", str(tmp_path / "m.db"))
    proj_root = tmp_path / "projects"
    _write_session(proj_root, "s1.jsonl", "第一次对话", mtime=1000)
    store = MemoryStore(Path(tmp_path / "m.db"))
    run_trigger(store, "proj", tmp_path, FakeProvider("旧摘要"), catch_up=True)

    _write_session(proj_root, "s2.jsonl", "第二次对话", mtime=2000)
    run_trigger(store, "proj", tmp_path, FakeProvider("新摘要"), catch_up=True)

    entries = store.all("proj")
    assert len(entries) == 2
    assert entries[0].content == "旧摘要"  # s1 未被重新提炼
    assert entries[1].content == "新摘要"
