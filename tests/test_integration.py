import json
from ai_memory.providers import FakeProvider
from ai_memory.store import MemoryStore
from ai_memory.trigger import run_trigger
from ai_memory.mcp_server import build_get_memory_response, build_search_response


def test_full_pipeline(tmp_path, monkeypatch):
    # 造一个「上次会话」的 session 文件
    proj_root = tmp_path / "projects"
    d = proj_root / "proj"
    d.mkdir(parents=True, exist_ok=True)
    (d / "old.jsonl").write_text(
        json.dumps({"type": "user", "message": {"content": "帮我写个爬虫"}}) + "\n" +
        json.dumps({"type": "assistant", "message": {"content": "好的，用 requests"}}) + "\n"
    )
    monkeypatch.setenv("AI_MEMORY_CLAUDE_PROJECTS", str(proj_root))
    db = tmp_path / "m.db"
    monkeypatch.setenv("AI_MEMORY_DB", str(db))

    # 1. 触发续接流程
    store = MemoryStore(db)
    out = run_trigger(store, "proj", tmp_path, FakeProvider("- 目标：写爬虫"))
    assert out is not None

    # 2. 摘要已累积入库
    assert store.latest_any("proj").content == "- 目标：写爬虫"

    # 3. summary.md 已导出
    assert (tmp_path / "summary.md").exists()

    # 4. MCP 工具能读到同一份摘要
    resp = build_get_memory_response(store, "proj")
    assert "写爬虫" in resp

    # 5. search_memory 全文搜索链路能命中
    assert "写爬虫" in build_search_response(store, "proj", "爬虫")

    # 6. 同一 session 二次触发去重，不重复入库
    run_trigger(store, "proj", tmp_path, FakeProvider("- 目标：写爬虫"))
    assert len(store.list_entries(project="proj", limit=10)) == 1
