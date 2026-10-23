"""本地配置网页的纯函数与 HTTP 端点测试。"""
import json
import threading
import urllib.request

from ai_memory import web
from ai_memory.config import save_config_values, write_default_config
from ai_memory.store import MemoryStore, MemoryEntry


def test_coerce_types():
    assert web._coerce("timeout_seconds", "45") == 45
    assert web._coerce("auto_detect", "false") is False
    assert web._coerce("auto_detect", "true") is True
    assert web._coerce("name", None) == ""


def test_build_state_masks_api_key(tmp_path, monkeypatch):
    cfg = tmp_path / "config.toml"
    monkeypatch.setenv("AI_MEMORY_CONFIG", str(cfg))
    monkeypatch.setenv("AI_MEMORY_DB", str(tmp_path / "m.db"))
    write_default_config(cfg)
    save_config_values({"provider": {"api_key": "sk-secret"}}, cfg)

    state = web.build_state(MemoryStore(tmp_path / "m.db"), "proj")

    assert state["config"]["provider"]["api_key"] == ""
    assert state["config"]["provider"]["api_key_set"] is True
    assert "sk-secret" not in json.dumps(state, ensure_ascii=False)


def test_list_memories_uses_tiered_search(tmp_path):
    store = MemoryStore(tmp_path / "m.db")
    store.set_core("proj", "常用部署脚本")
    store.append(MemoryEntry("1", "scratch", "proj", "s1", "部署脚本零散", "2026-01-01T00:00:00"))

    items = web.list_memories(store, "proj", "部署脚本", None, 10)

    assert [i["type"] for i in items] == ["core_memory"]


def test_http_endpoints(tmp_path, monkeypatch):
    monkeypatch.setenv("AI_MEMORY_CONFIG", str(tmp_path / "config.toml"))
    monkeypatch.setenv("AI_MEMORY_DB", str(tmp_path / "m.db"))
    store = MemoryStore(tmp_path / "m.db")
    store.set_core("proj", "核心内容")

    server, port = web.create_server(port=18765, project="proj", store=store)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/state") as resp:
            state = json.loads(resp.read())
        assert state["project"] == "proj"
        assert state["core"] == "核心内容"

        body = json.dumps({"project": "proj", "content": "新的核心"}).encode()
        req = urllib.request.Request(
            f"http://127.0.0.1:{port}/api/core", data=body,
            headers={"Content-Type": "application/json"}, method="POST")
        with urllib.request.urlopen(req) as resp:
            assert json.loads(resp.read())["ok"] is True
        assert store.get_core("proj").content == "新的核心"

        with urllib.request.urlopen(f"http://127.0.0.1:{port}/") as resp:
            assert "Pickup" in resp.read().decode("utf-8")
    finally:
        server.shutdown()
        server.server_close()
