"""语义检索兜底的端到端测试。

核心验证点（来自 Advisor）：
  (a) 无 embedding 端点时 → 行为与二期完全一致（已被 117 个老测试隐式覆盖）
  (b) 有可用端点时 → store.search_semantic + mcp_server 兜底链路通
  (c) schema v3 迁移 → 老库升级后能正常建 memory_embedding 表
"""
from __future__ import annotations

import math
from unittest.mock import patch

from ai_memory import embeddings
from ai_memory.config import DEFAULT_CONFIG
from ai_memory.embeddings import embedding_available, embed, vec_to_blob, blob_to_vec
from ai_memory.mcp_server import build_search_response
from ai_memory.store import MemoryStore, new_entry


# ---------- embedding_available：auto 探测不到时返回 False ----------

def test_embedding_available_no_endpoint(tmp_path, monkeypatch):
    """auto 模式下：既无显式 base_url 也无 env key 也无本机服务 → False。"""
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    # store 空，不探测本机
    monkeypatch.setattr(embeddings, "_pick_endpoint", lambda cfg: None)
    assert embedding_available(DEFAULT_CONFIG) is False


def test_embedding_available_disabled_flag(tmp_path):
    """semantic.enabled=False → 直接 False，不浪费探测。"""
    cfg = {**DEFAULT_CONFIG, "semantic": {**DEFAULT_CONFIG["semantic"], "enabled": False}}
    assert embedding_available(cfg) is False


# ---------- 序列化 round-trip ----------

def test_vec_blob_roundtrip():
    vec = [0.1, -0.5, 0.0, 0.8, 0.25]
    blob = vec_to_blob(vec)
    assert isinstance(blob, bytes)
    assert blob_to_vec(blob) == vec


# ---------- store.search_semantic：核心路径 ----------

def _cosine(a, b):
    dot = sum(x * y for x, y in zip(a, b))
    return dot / math.sqrt(sum(x * x for x in a)) / math.sqrt(sum(x * x for x in b))


def test_search_semantic_threshold(tmp_path):
    """余弦阈值过滤：低于阈值的条目不返回。"""
    store = MemoryStore(tmp_path / "m.db")
    # 4 维向量，query 偏向 [1,0,0,0]
    q_vec = [1.0, 0.0, 0.0, 0.0]

    close = new_entry("session_summary", "proj", "贴近 query", "2026-01-01T00:00:00", "s1")
    far = new_entry("session_summary", "proj", "远离 query", "2026-01-01T00:00:00", "s2")
    no_embed = new_entry("session_summary", "proj", "无向量", "2026-01-01T00:00:00", "s3")
    store.append(close); store.append(far); store.append(no_embed)

    store.store_embedding(close.id, [0.9, 0.1, 0.0, 0.0], "mock")   # sim ~0.994
    store.store_embedding(far.id, [0.1, 0.9, 0.0, 0.0], "mock")    # sim ~0.100

    # threshold=0.3 → 只返回 close（cos 远高于）
    hits = store.search_semantic(q_vec, project="proj", limit=10, threshold=0.3)
    assert [h.id for h in hits] == [close.id]

    # threshold=0.0 → 两者都返回，close 排前
    hits = store.search_semantic(q_vec, project="proj", limit=10, threshold=0.0)
    assert [h.id for h in hits] == [close.id, far.id]


def test_search_semantic_dim_mismatch_skipped(tmp_path):
    """不同 model 产生的向量维度不一致时，跳过不崩溃。"""
    store = MemoryStore(tmp_path / "m.db")
    e1 = new_entry("archive", "proj", "短向量", "2026-01-01T00:00:00", "s1")
    e2 = new_entry("archive", "proj", "长向量", "2026-01-01T00:00:00", "s2")
    store.append(e1); store.append(e2)
    store.store_embedding(e1.id, [0.5, 0.5], "mock")          # 2 维
    store.store_embedding(e2.id, [0.5, 0.5, 0.5, 0.5], "mock")  # 4 维

    # query 3 维 → 全跳过，返回空
    hits = store.search_semantic([0.3, 0.3, 0.3], project="proj", limit=10, threshold=0.0)
    assert hits == []


def test_search_semantic_no_embeds(tmp_path):
    """库里无向量 → 直接返回空，不做任何网络调用。"""
    store = MemoryStore(tmp_path / "m.db")
    store.append(new_entry("scratch", "proj", "测试", "2026-01-01T00:00:00", "s1"))
    hits = store.search_semantic([0.5, 0.5], project="proj", limit=10, threshold=0.0)
    assert hits == []


# ---------- schema v3 迁移：老库升级后有 memory_embedding 表 ----------

def test_schema_v3_migration_creates_embedding_table(tmp_path):
    import sqlite3

    db = tmp_path / "m.db"
    # 手工造 v1 结构（没有 user_version 设置）
    with sqlite3.connect(db) as conn:
        conn.execute(
            "CREATE TABLE memory_entry (id TEXT PRIMARY KEY, type TEXT NOT NULL, "
            "project TEXT NOT NULL, session_id TEXT, content TEXT NOT NULL, "
            "created_at TEXT NOT NULL)"
        )
    store = MemoryStore(db)  # 触发 v1→v2→v3 迁移
    with sqlite3.connect(db) as conn:
        version = conn.execute("PRAGMA user_version").fetchone()[0]
        assert version == 3
        tables = [r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()]
        assert "memory_embedding" in tables


# ---------- mcp_server：FTS 全空时才走语义兜底 ----------

def test_mcp_falls_back_to_semantic_when_fts_empty(tmp_path, monkeypatch):
    """FTS 全空 → embed query → search_semantic → 有结果 → 组合输出。"""
    store = MemoryStore(tmp_path / "m.db")
    # 入库一条只有语义能命中的记忆
    sem = new_entry("archive", "proj",
                    "这个记录里写的是向量检索但没有 FTS 能命中的关键词",
                    "2026-01-01T00:00:00", "s1")
    store.append(sem)
    store.store_embedding(sem.id, [0.9, 0.1], "mock")

    fake_cfg = {"semantic": {"enabled": True, "threshold": 0.3, "model": "mock"},
                "provider": {"auto_detect": False}}

    def fake_embed(text, cfg):
        return [0.9, 0.1]  # 固定返回与 store_embedding 一致的向量（cos≈1.0）

    monkeypatch.setattr("ai_memory.mcp_server._load_cfg_safe", lambda: fake_cfg)
    monkeypatch.setattr(embeddings, "embedding_available", lambda cfg: True)
    monkeypatch.setattr(embeddings, "embed", fake_embed)

    resp = build_search_response(store, "proj", "完全不可能在原文里出现的xyzABC123")
    assert "向量检索" in resp


def test_mcp_no_semantic_when_disabled(tmp_path, monkeypatch):
    """semantic.enabled=False → 即使 FTS 空也不走语义，行为与二期一致。"""
    store = MemoryStore(tmp_path / "m.db")
    sem = new_entry("archive", "proj", "向量测试", "2026-01-01T00:00:00", "s1")
    store.append(sem)
    store.store_embedding(sem.id, [0.9, 0.1], "mock")

    fake_cfg = {"semantic": {"enabled": False, "threshold": 0.3, "model": "mock"}}
    monkeypatch.setattr("ai_memory.mcp_server._load_cfg_safe", lambda: fake_cfg)

    resp = build_search_response(store, "proj", "完全不可能在原文里出现的xyzABC123")
    assert "未找到与「完全不可能" in resp  # 与二期完全一致的输出


def test_mcp_semantic_never_mixes_with_fts(tmp_path, monkeypatch):
    """FTS 有命中时 → 完全不走 embed 函数（验证永不混合打分）。"""
    store = MemoryStore(tmp_path / "m.db")
    fts_hit = new_entry("session_summary", "proj",
                        "这里包含可检索的关键词 hello_world_unique_xyz",
                        "2026-01-01T00:00:00", "s1")
    sem_only = new_entry("archive", "proj", "其他无关内容",
                          "2026-01-01T00:00:00", "s2")
    store.append(fts_hit); store.append(sem_only)
    store.store_embedding(fts_hit.id, [0.9, 0.1], "mock")
    store.store_embedding(sem_only.id, [0.1, 0.9], "mock")

    fake_cfg = {"semantic": {"enabled": True, "threshold": 0.0, "model": "mock"},
                "provider": {"auto_detect": False}}

    called = {"flag": False}

    def fake_embed(text, cfg):
        called["flag"] = True
        return [0.9, 0.1]

    monkeypatch.setattr("ai_memory.mcp_server._load_cfg_safe", lambda: fake_cfg)
    monkeypatch.setattr(embeddings, "embedding_available", lambda cfg: True)
    monkeypatch.setattr(embeddings, "embed", fake_embed)

    resp = build_search_response(store, "proj", "hello_world_unique_xyz")
    assert "s1" in resp
    assert "s2" not in resp  # 语义命中的 sem_only 不应出现
    assert called["flag"] is False  # 验证：FTS 有命中时 embed 根本没被调用


# ---------- 写入点自动 embed ----------

def _cfg_true(threshold: float = 0.3) -> dict:
    return {"semantic": {"enabled": True, "threshold": threshold, "model": "mock"}}


def test_try_embed_stores_vector(tmp_path, monkeypatch):
    """try_embed：写入后生成向量并落库。"""
    store = MemoryStore(tmp_path / "m.db")
    e = new_entry("core_memory", "proj", "我是后端工程师", "2026-01-01T00:00:00")
    store.append(e)

    monkeypatch.setattr(embeddings, "embedding_available", lambda cfg: True)
    monkeypatch.setattr(embeddings, "embed", lambda text, cfg, timeout=5.0: [0.2, 0.4])

    assert embeddings.try_embed(store, e.id, e.content, cfg=_cfg_true()) is True
    assert store.get_embedding(e.id) == [0.2, 0.4]


def test_try_embed_silent_when_disabled(tmp_path, monkeypatch):
    """语义关闭时 try_embed 直接返回 False，不做任何调用。"""
    store = MemoryStore(tmp_path / "m.db")
    e = new_entry("core_memory", "proj", "内容", "2026-01-01T00:00:00")
    store.append(e)

    called = {"flag": False}

    def fake_embed(text, cfg, timeout=5.0):
        called["flag"] = True
        return [1.0]

    monkeypatch.setattr(embeddings, "embed", fake_embed)
    cfg = {"semantic": {"enabled": False}}
    assert embeddings.try_embed(store, e.id, "内容", cfg=cfg) is False
    assert called["flag"] is False
    assert store.get_embedding(e.id) is None


def test_core_set_auto_embeds(tmp_path, monkeypatch):
    """CLI `core set` 写入后自动生成 embedding。"""
    db = tmp_path / "m.db"
    monkeypatch.setenv("AI_MEMORY_DB", str(db))
    monkeypatch.setenv("AI_MEMORY_CONFIG", str(tmp_path / "config.toml"))
    monkeypatch.setattr(embeddings, "embedding_available", lambda cfg: True)
    monkeypatch.setattr(embeddings, "embed", lambda text, cfg, timeout=5.0: [0.3, 0.7])

    from ai_memory.cli import main

    assert main(["core", "set", "我是后端工程师", "--project", "proj"]) == 0

    store = MemoryStore(db)
    core = store.get_core("proj")
    assert core is not None
    assert store.get_embedding(core.id) == [0.3, 0.7]


def test_core_update_overwrites_embedding(tmp_path, monkeypatch):
    """set_core 原地更新已有条目时，旧向量必须被新向量覆盖。"""
    db = tmp_path / "m.db"
    monkeypatch.setenv("AI_MEMORY_DB", str(db))
    monkeypatch.setenv("AI_MEMORY_CONFIG", str(tmp_path / "config.toml"))

    vectors = [[1.0, 0.0], [0.0, 1.0]]
    monkeypatch.setattr(embeddings, "embedding_available", lambda cfg: True)
    monkeypatch.setattr(embeddings, "embed",
                        lambda text, cfg, timeout=5.0: vectors.pop(0))

    from ai_memory.cli import main

    assert main(["core", "set", "第一版", "--project", "proj"]) == 0
    assert main(["core", "set", "第二版", "--project", "proj"]) == 0

    store = MemoryStore(db)
    core = store.get_core("proj")
    assert core.content == "第二版"
    assert store.get_embedding(core.id) == [0.0, 1.0]


# ---------- 懒回填 ----------

def test_backfill_embeddings_fills_missing(tmp_path, monkeypatch):
    """老库无向量的条目被补齐；第二次调用不再重复补。"""
    store = MemoryStore(tmp_path / "m.db")
    a = new_entry("session_summary", "proj", "第一条记忆", "2026-01-01T00:00:00", "s1")
    b = new_entry("archive", "proj", "第二条记忆", "2026-01-02T00:00:00", "s2")
    store.append(a); store.append(b)

    monkeypatch.setattr(embeddings, "embedding_available", lambda cfg: True)
    monkeypatch.setattr(embeddings, "embed", lambda text, cfg, timeout=5.0: [0.5, 0.5])

    assert embeddings.backfill_embeddings(store, project="proj", cfg=_cfg_true()) == 2
    assert store.get_embedding(a.id) == [0.5, 0.5]
    assert store.get_embedding(b.id) == [0.5, 0.5]
    # 已全部补齐 → 第二次 0 条
    assert embeddings.backfill_embeddings(store, project="proj", cfg=_cfg_true()) == 0


def test_backfill_stops_when_endpoint_fails(tmp_path, monkeypatch):
    """首条 embed 就失败 → 立即停止，不逐条超时。"""
    store = MemoryStore(tmp_path / "m.db")
    for i in range(5):
        store.append(new_entry("scratch", "proj", f"记忆{i}", "2026-01-01T00:00:00",
                               f"s{i}"))

    calls = {"n": 0}
    monkeypatch.setattr(embeddings, "embedding_available", lambda cfg: True)

    def fake_embed(text, cfg, timeout=5.0):
        calls["n"] += 1
        return None

    monkeypatch.setattr(embeddings, "embed", fake_embed)
    assert embeddings.backfill_embeddings(store, project="proj", cfg=_cfg_true()) == 0
    assert calls["n"] == 1  # 只试了第一条就停


def test_backfill_respects_project(tmp_path, monkeypatch):
    """只回填目标项目的条目。"""
    store = MemoryStore(tmp_path / "m.db")
    mine = new_entry("session_summary", "proj", "我的", "2026-01-01T00:00:00", "s1")
    other = new_entry("session_summary", "other", "别人的", "2026-01-01T00:00:00", "s2")
    store.append(mine); store.append(other)

    monkeypatch.setattr(embeddings, "embedding_available", lambda cfg: True)
    monkeypatch.setattr(embeddings, "embed", lambda text, cfg, timeout=5.0: [0.5, 0.5])

    assert embeddings.backfill_embeddings(store, project="proj", cfg=_cfg_true()) == 1
    assert store.get_embedding(mine.id) is not None
    assert store.get_embedding(other.id) is None


def test_semantic_search_backfills_then_hits(tmp_path, monkeypatch):
    """FTS 全空的场景：懒回填后语义检索立即能命中存量记忆。"""
    store = MemoryStore(tmp_path / "m.db")
    e = new_entry("session_summary", "proj", "存量记忆，没有任何向量",
                  "2026-01-01T00:00:00", "s1")
    store.append(e)
    assert store.get_embedding(e.id) is None

    monkeypatch.setattr(embeddings, "embedding_available", lambda cfg: True)
    monkeypatch.setattr(embeddings, "embed", lambda text, cfg, timeout=5.0: [0.9, 0.1])

    hits = embeddings.semantic_search(store, "完全不可能命中的xyzABC",
                                      project="proj", cfg=_cfg_true())
    assert len(hits) == 1
    assert hits[0].id == e.id
    assert store.get_embedding(e.id) == [0.9, 0.1]  # 回填生效


def test_semantic_search_no_endpoint_returns_empty(tmp_path, monkeypatch):
    """无端点时 semantic_search 返回空且不写任何向量。"""
    store = MemoryStore(tmp_path / "m.db")
    e = new_entry("session_summary", "proj", "记忆", "2026-01-01T00:00:00", "s1")
    store.append(e)

    monkeypatch.setattr(embeddings, "embedding_available", lambda cfg: False)
    assert embeddings.semantic_search(store, "查询", project="proj", cfg=_cfg_true()) == []
    assert store.get_embedding(e.id) is None
