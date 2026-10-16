from ai_memory.store import MemoryStore, MemoryEntry


def _make(id="1", type="session_summary", project="proj", session_id="s1",
          content="摘要", created_at="2026-09-03T10:00:00"):
    return MemoryEntry(id, type, project, session_id, content, created_at)


def test_append_and_latest(tmp_path):
    store = MemoryStore(tmp_path / "m.db")
    store.append(_make(id="1", session_id="s1", content="第一次摘要"))
    store.append(_make(id="2", session_id="s2", content="第二次摘要", created_at="2026-09-03T11:00:00"))
    latest = store.latest("proj")
    assert latest.id == "2"
    assert latest.content == "第二次摘要"


def test_append_is_cumulative_not_overwrite(tmp_path):
    store = MemoryStore(tmp_path / "m.db")
    store.append(_make(id="1", session_id="s1"))
    store.append(_make(id="2", session_id="s2", created_at="2026-09-03T11:00:00"))
    assert len(store.all("proj")) == 2  # 两条都在，未被覆盖


def test_latest_empty_returns_none(tmp_path):
    store = MemoryStore(tmp_path / "m.db")
    assert store.latest("proj") is None


def test_latest_scoped_by_project(tmp_path):
    store = MemoryStore(tmp_path / "m.db")
    store.append(_make(id="1", project="A"))
    store.append(_make(id="2", project="B"))
    assert store.latest("A").id == "1"
    assert store.latest("B").id == "2"


# ---------- v2：去重 ----------

def test_same_session_dedup(tmp_path):
    store = MemoryStore(tmp_path / "m.db")
    assert store.append(_make(id="1", session_id="s1")) is True
    assert store.append(_make(id="2", session_id="s1")) is False  # 同 session 跳过
    assert len(store.all("proj")) == 1


def test_different_sessions_both_kept(tmp_path):
    store = MemoryStore(tmp_path / "m.db")
    assert store.append(_make(id="1", session_id="s1")) is True
    assert store.append(_make(id="2", session_id="s2")) is True
    assert len(store.all("proj")) == 2


def test_get_by_session(tmp_path):
    store = MemoryStore(tmp_path / "m.db")
    store.append(_make(id="1", session_id="s1"))
    assert store.get_by_session("proj", "s1").id == "1"
    assert store.get_by_session("proj", "nope") is None


# ---------- v2：搜索 ----------

def test_search_chinese_substring(tmp_path):
    store = MemoryStore(tmp_path / "m.db")
    store.append(_make(id="1", session_id="s1", content="目标：写爬虫，用 requests 库"))
    store.append(_make(id="2", session_id="s2", content="目标：重构登录模块"))
    hits = store.search("爬虫")
    assert len(hits) == 1 and hits[0].id == "1"


def test_search_short_query_falls_back_to_like(tmp_path):
    store = MemoryStore(tmp_path / "m.db")
    store.append(_make(id="1", session_id="s1", content="卡点：DB 连接失败"))
    hits = store.search("DB")  # 不足 3 字符
    assert len(hits) == 1


def test_search_excludes_deleted(tmp_path):
    store = MemoryStore(tmp_path / "m.db")
    store.append(_make(id="1", session_id="s1", content="目标：写爬虫"))
    store.soft_delete("1")
    assert store.search("爬虫") == []


def test_search_scoped_by_project(tmp_path):
    store = MemoryStore(tmp_path / "m.db")
    store.append(_make(id="1", project="A", session_id="s1", content="目标：写爬虫"))
    store.append(_make(id="2", project="B", session_id="s2", content="目标：写爬虫"))
    hits = store.search("爬虫", project="A")
    assert len(hits) == 1 and hits[0].project == "A"


def test_search_touch_updates_last_accessed(tmp_path):
    store = MemoryStore(tmp_path / "m.db")
    store.append(_make(id="1", session_id="s1", content="目标：写爬虫"))
    store.search("爬虫")
    assert store.get("1").last_accessed_at is not None


# ---------- v2：软删除 ----------

def test_soft_delete_hides_from_latest_and_all(tmp_path):
    store = MemoryStore(tmp_path / "m.db")
    store.append(_make(id="1", session_id="s1"))
    assert store.soft_delete("1") is True
    assert store.latest("proj") is None
    assert store.all("proj") == []
    assert store.soft_delete("1") is False  # 重复删除


# ---------- v2：回收候选 ----------

def test_prune_candidates_excludes_protected_and_accessed(tmp_path):
    store = MemoryStore(tmp_path / "m.db")
    store.append(_make(id="old", session_id="s1", created_at="2020-01-01T00:00:00"))
    store.append(_make(id="new", session_id="s2", created_at="2999-01-01T00:00:00"))
    store.append(_make(id="core", type="core_memory", session_id=None,
                       created_at="2020-01-01T00:00:00"))
    store.touch(["old"])  # 被访问过的先不回收 → 重新造一个未访问的旧条目
    store.append(_make(id="old2", session_id="s3", created_at="2020-01-01T00:00:00"))
    ids = [e.id for e in store.prune_candidates(cold_days=30)]
    assert "old2" in ids
    assert "old" not in ids    # 已访问
    assert "new" not in ids    # 太新
    assert "core" not in ids   # 保护类型


# ---------- v2：统计 ----------

def test_stats(tmp_path):
    store = MemoryStore(tmp_path / "m.db")
    store.append(_make(id="1", session_id="s1"))
    store.append(_make(id="2", project="B", session_id="s2"))
    s = store.stats()
    assert s["total"] == 2
    assert s["db_bytes"] > 0
    assert len(s["by_project_type"]) == 2


# ---------- v2：迁移 ----------

def test_migration_from_v1_schema(tmp_path):
    import sqlite3
    db = tmp_path / "m.db"
    with sqlite3.connect(db) as conn:  # 手工造 v1 结构
        conn.execute(
            "CREATE TABLE memory_entry (id TEXT PRIMARY KEY, type TEXT NOT NULL, "
            "project TEXT NOT NULL, session_id TEXT, content TEXT NOT NULL, "
            "created_at TEXT NOT NULL)"
        )
        conn.execute("INSERT INTO memory_entry VALUES ('1','session_summary','proj','s1','旧摘要','2026-09-03T10:00:00')")
    store = MemoryStore(db)  # 打开即迁移
    e = store.latest("proj")
    assert e.content == "旧摘要" and e.importance == 0
    assert store.search("旧摘要")[0].id == "1"  # FTS 已回填
    with sqlite3.connect(db) as conn:
        assert conn.execute("PRAGMA user_version").fetchone()[0] == 2

def test_migrate_v1_with_duplicate_sessions_keeps_newest(tmp_path):
    """v1 老库同一 (project, session) 有多条摘要时，迁移先去重再建唯一索引，不应炸。"""
    import sqlite3
    db = tmp_path / "m.db"
    with sqlite3.connect(db) as conn:  # 手工造 v1 结构 + 重复会话摘要
        conn.execute(
            "CREATE TABLE memory_entry (id TEXT PRIMARY KEY, type TEXT NOT NULL, "
            "project TEXT NOT NULL, session_id TEXT, content TEXT NOT NULL, "
            "created_at TEXT NOT NULL)"
        )
        conn.execute("INSERT INTO memory_entry VALUES ('1','session_summary','proj','s1','旧摘要','2026-09-03T10:00:00')")
        conn.execute("INSERT INTO memory_entry VALUES ('2','session_summary','proj','s1','新摘要','2026-09-04T10:00:00')")
        conn.execute("INSERT INTO memory_entry VALUES ('3','session_summary','proj','s2','另一会话','2026-09-05T10:00:00')")
    store = MemoryStore(db)  # 打开即迁移，不应 IntegrityError
    entries = store.all("proj")
    assert len(entries) == 2                       # s1 的重复只留一条
    assert store.get_by_session("proj", "s1").content == "新摘要"  # 留最新
    assert store.search("新摘要")[0].id == "2"     # FTS 与去重后数据一致
    assert store.search("旧摘要") == []            # 被清掉的搜不到
