import json
import os
import pytest
from ai_memory.cli import main
from ai_memory.store import MemoryStore, MemoryEntry


@pytest.fixture
def env(tmp_path, monkeypatch):
    """隔离的记忆库、配置与 Claude 会话目录。"""
    db = tmp_path / "m.db"
    monkeypatch.setenv("AI_MEMORY_DB", str(db))
    monkeypatch.setenv("AI_MEMORY_CONFIG", str(tmp_path / "config.toml"))
    monkeypatch.setenv("AI_MEMORY_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("AI_MEMORY_CLAUDE_PROJECTS", str(tmp_path / "projects"))
    return tmp_path


def _write_session(tmp_path, project, name, text, mtime=None):
    d = tmp_path / "projects" / project
    d.mkdir(parents=True, exist_ok=True)
    p = d / name
    p.write_text(json.dumps({"type": "user", "message": {"content": text}}) + "\n")
    if mtime is not None:
        os.utime(p, (mtime, mtime))
    return p


def _seed(db, **kw):
    store = MemoryStore(db)
    entry = MemoryEntry(
        kw.get("id", "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"),
        kw.get("type", "session_summary"),
        kw.get("project", "proj"),
        kw.get("session_id", "s1"),
        kw.get("content", "- 目标：写爬虫抓取网页"),
        kw.get("created_at", "2026-09-03T10:00:00"),
    )
    store.append(entry)
    return entry


def test_init_writes_config_and_prints_guide(env, capsys):
    assert main(["init"]) == 0
    out = capsys.readouterr().out
    assert "接入 Claude Code" in out
    assert (env / "config.toml").exists()


def test_list_shows_entries(env, capsys):
    _seed(env / "m.db")
    assert main(["list", "--project", "proj"]) == 0
    out = capsys.readouterr().out
    assert "写爬虫" in out
    assert "aaaaaaaa" in out


def test_list_all_projects(env, capsys):
    _seed(env / "m.db")
    assert main(["list", "--all"]) == 0
    assert "写爬虫" in capsys.readouterr().out


def test_show_by_id_and_prefix(env, capsys):
    entry = _seed(env / "m.db")
    assert main(["show", entry.id]) == 0
    assert "抓取网页" in capsys.readouterr().out
    assert main(["show", "aaaaaaaa"]) == 0
    assert "抓取网页" in capsys.readouterr().out


def test_show_missing_returns_error(env, capsys):
    assert main(["show", "不存在的id"]) == 1
    assert "未找到" in capsys.readouterr().err


def test_search_finds_entry(env, capsys):
    _seed(env / "m.db")
    assert main(["search", "爬虫", "--project", "proj"]) == 0
    assert "写爬虫" in capsys.readouterr().out


def test_search_no_match(env, capsys):
    _seed(env / "m.db")
    assert main(["search", "不存在的关键词", "--project", "proj"]) == 0
    assert "未找到" in capsys.readouterr().out


def test_export_to_file_and_stdout(env, capsys):
    _seed(env / "m.db")
    out_file = env / "export.md"
    assert main(["export", "--all", "--out", str(out_file)]) == 0
    text = out_file.read_text(encoding="utf-8")
    assert "AI 记忆导出" in text and "写爬虫" in text
    assert main(["export", "--project", "proj"]) == 0
    assert "写爬虫" in capsys.readouterr().out


def test_forget_requires_yes(env, capsys):
    entry = _seed(env / "m.db")
    assert main(["forget", entry.id]) == 1
    assert "--yes" in capsys.readouterr().err
    store = MemoryStore(env / "m.db")
    assert store.get(entry.id).deleted_at is None  # 未删除


def test_forget_with_yes_soft_deletes(env):
    entry = _seed(env / "m.db")
    assert main(["forget", entry.id, "--yes"]) == 0
    store = MemoryStore(env / "m.db")
    assert store.get(entry.id).deleted_at is not None
    assert store.search("爬虫") == []  # 已删除的搜索不到


def test_prune_dry_run_then_execute(env, capsys):
    _seed(env / "m.db", id="old-old-old-old-old-old-old1",
          created_at="2020-01-01T00:00:00")
    assert main(["prune"]) == 0  # dry-run
    out = capsys.readouterr().out
    assert "dry-run" in out and "回收候选" in out
    store = MemoryStore(env / "m.db")
    assert store.list_entries() != []  # 未真删
    assert main(["prune", "--yes"]) == 0
    assert MemoryStore(env / "m.db").list_entries() == []


def test_prune_protects_core_memory(env, capsys):
    _seed(env / "m.db", id="core-core-core-core-core-core1",
          type="core_memory", created_at="2020-01-01T00:00:00")
    assert main(["prune", "--yes"]) == 0
    out = capsys.readouterr().out
    assert "没有" in out  # core_memory 不在候选里
    assert MemoryStore(env / "m.db").list_entries() != []


def test_stats(env, capsys):
    _seed(env / "m.db")
    assert main(["stats"]) == 0
    out = capsys.readouterr().out
    assert "总条目：1" in out and "proj" in out


def test_version_flag(env, capsys):
    with pytest.raises(SystemExit) as exc:
        main(["--version"])
    assert exc.value.code == 0
    assert "ai-memory" in capsys.readouterr().out


def test_status_reports_paths_and_provider(env, capsys):
    assert main(["status", "--project", "proj"]) == 0
    out = capsys.readouterr().out
    assert "记忆库" in out
    assert "NoneProvider" in out          # 默认零模型
    assert "当前项目： proj" in out


def test_status_lists_pending_sessions(env, capsys):
    _write_session(env, "proj", "s1.jsonl", "写个爬虫")
    assert main(["status", "--project", "proj"]) == 0
    out = capsys.readouterr().out
    assert "待处理 1 个" in out
    assert "待处理 s1" in out


def test_status_marks_archived_sessions(env, capsys):
    _write_session(env, "proj", "s1.jsonl", "写个爬虫")
    assert main(["run", "--project", "proj", "--out-dir", str(env / "out")]) == 0
    capsys.readouterr()
    assert main(["status", "--project", "proj"]) == 0
    out = capsys.readouterr().out
    assert "已入库 1 个" in out and "待处理 0 个" in out


def test_run_extracts_and_exports(env, capsys):
    _write_session(env, "proj", "s1.jsonl", "帮我写个爬虫")
    assert main(["run", "--project", "proj", "--out-dir", str(env / "out")]) == 0
    out = capsys.readouterr().out
    assert "摘要已导出" in out
    assert (env / "out" / "summary.md").exists()
    assert len(MemoryStore(env / "m.db").all("proj")) == 1


def test_run_catch_up_archives_all(env, capsys):
    _write_session(env, "proj", "s1.jsonl", "第一次", mtime=1000)
    _write_session(env, "proj", "s2.jsonl", "第二次", mtime=2000)
    assert main(["run", "--project", "proj", "--catch-up",
                 "--out-dir", str(env / "out")]) == 0
    assert len(MemoryStore(env / "m.db").all("proj")) == 2


def test_run_exclude_session(env, capsys):
    _write_session(env, "proj", "s1.jsonl", "老对话", mtime=1000)
    _write_session(env, "proj", "current.jsonl", "当前对话", mtime=2000)
    assert main(["run", "--project", "proj", "--exclude-session", "current",
                 "--out-dir", str(env / "out")]) == 0
    entries = MemoryStore(env / "m.db").all("proj")
    assert [e.session_id for e in entries] == ["s1"]


def test_run_no_session_prints_hint(env, capsys):
    assert main(["run", "--project", "proj", "--out-dir", str(env / "out")]) == 0
    assert "没有可提炼的历史会话" in capsys.readouterr().out


def test_run_is_idempotent(env, capsys):
    _write_session(env, "proj", "s1.jsonl", "写个爬虫")
    assert main(["run", "--project", "proj", "--out-dir", str(env / "out")]) == 0
    assert main(["run", "--project", "proj", "--out-dir", str(env / "out")]) == 0
    assert len(MemoryStore(env / "m.db").all("proj")) == 1
