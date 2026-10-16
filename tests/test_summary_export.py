from ai_memory.summary_export import export_summary, summary_path
from ai_memory.store import MemoryStore, MemoryEntry


def test_export_summary_writes_md(tmp_path):
    store = MemoryStore(tmp_path / "m.db")
    store.append(MemoryEntry("1", "session_summary", "proj", "s1",
                             "上次会话摘要内容", "2026-09-03T10:00:00"))
    out = export_summary(store, "proj", tmp_path)
    assert out.exists()
    text = out.read_text(encoding="utf-8")
    assert "上次会话摘要内容" in text


def test_export_summary_no_entry_returns_none(tmp_path):
    store = MemoryStore(tmp_path / "m.db")
    assert export_summary(store, "proj", tmp_path) is None


def test_summary_path(tmp_path):
    p = summary_path(tmp_path)
    assert p == tmp_path / "summary.md"
