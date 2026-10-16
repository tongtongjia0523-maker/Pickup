from ai_memory.mcp_server import build_get_memory_response, build_search_response
from ai_memory.store import MemoryStore, MemoryEntry


def test_get_memory_returns_latest(tmp_path):
    store = MemoryStore(tmp_path / "m.db")
    store.append(MemoryEntry("1", "session_summary", "proj", "s1",
                             "摘要内容", "2026-09-03T10:00:00"))
    assert "摘要内容" in build_get_memory_response(store, "proj")


def test_get_memory_empty_returns_hint(tmp_path):
    store = MemoryStore(tmp_path / "m.db")
    assert "暂无" in build_get_memory_response(store, "proj")


def test_search_memory_returns_matches(tmp_path):
    store = MemoryStore(tmp_path / "m.db")
    store.append(MemoryEntry("1", "session_summary", "proj", "s1",
                             "- 目标：写爬虫抓取网页", "2026-09-03T10:00:00"))
    store.append(MemoryEntry("2", "session_summary", "proj", "s2",
                             "- 目标：重构数据库层", "2026-09-04T10:00:00"))
    resp = build_search_response(store, "proj", "爬虫")
    assert "写爬虫" in resp
    assert "重构数据库" not in resp


def test_search_memory_no_match_returns_hint(tmp_path):
    store = MemoryStore(tmp_path / "m.db")
    store.append(MemoryEntry("1", "session_summary", "proj", "s1",
                             "- 目标：写爬虫", "2026-09-03T10:00:00"))
    assert "未找到" in build_search_response(store, "proj", "不存在的关键词")


def test_search_memory_scoped_to_project(tmp_path):
    store = MemoryStore(tmp_path / "m.db")
    store.append(MemoryEntry("1", "session_summary", "other", "s1",
                             "- 目标：别人的爬虫", "2026-09-03T10:00:00"))
    assert "未找到" in build_search_response(store, "proj", "爬虫")
