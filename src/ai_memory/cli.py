"""ai-memory 命令行：管理本地记忆库。

子命令：init / status / run / list / show / search / export / forget / prune / stats
"""
import argparse
import sys
from datetime import datetime, timezone
from pathlib import Path

from ai_memory import __version__
from ai_memory.config import config_path, load_config, write_default_config
from ai_memory.paths import current_project, default_db_path
from ai_memory.providers import provider_from_config
from ai_memory.session_reader import find_sessions_since, projects_root
from ai_memory.store import MemoryStore
from ai_memory.trigger import run_trigger

_SNIPPET = 80


def _store() -> MemoryStore:
    return MemoryStore(default_db_path())


def _first_line(text: str, limit: int = _SNIPPET) -> str:
    line = text.strip().splitlines()[0] if text.strip() else ""
    return line[: limit - 1] + "…" if len(line) > limit else line


# ---------- 子命令 ----------

def _cmd_init(args) -> int:
    path = write_default_config()
    print(f"配置文件：{path}（已存在则不覆盖）")
    print(f"记忆库：  {default_db_path()}")
    print(
        "\n接入 Claude Code：\n"
        "1. SessionStart hook（自动续接）：在 ~/.claude/settings.json 的 hooks.SessionStart\n"
        "   中添加本仓库 hooks/session_start.py 脚本路径，详见 README。\n"
        "2. MCP 工具（按需深查）：claude mcp add ai-memory -- python -m ai_memory.mcp_server\n"
        "3. CLAUDE.md 中加一行 @.claude/memory/summary.md 可自动注入上次摘要。"
    )
    return 0


def _cmd_status(args) -> int:
    """环境自检：一眼看清记忆工具是否接线正常，便于上手排查。"""
    cfg = load_config()
    db = default_db_path()
    project = args.project or current_project()
    store = _store()
    provider = provider_from_config(cfg)
    provider_name = cfg["provider"].get("name", "none")
    effective = type(provider).__name__
    note = "" if effective == "NoneProvider" and provider_name == "none" else \
        f"（配置 name={provider_name}，实际生效 {effective}）"

    root = projects_root()
    sessions = find_sessions_since(project=project)
    archived = {e.session_id for e in store.list_entries(project=project, limit=100000)}
    pending = [s for s in sessions if s.stem not in archived]

    print(f"版本：     {__version__}")
    print(f"记忆库：   {db}{'' if db.exists() else '（尚未创建，首次触发时自动建库）'}")
    print(f"配置文件： {config_path()}{'' if config_path().exists() else '（未创建，使用内置默认值）'}")
    print(f"提炼模型： {effective}{note}")
    print(f"会话目录： {root}{'' if root.exists() else '（不存在，尚未使用过 Claude Code？）'}")
    print(f"当前项目： {project}")
    print(f"会话统计： 共 {len(sessions)} 个，已入库 {len(sessions) - len(pending)} 个，"
          f"待处理 {len(pending)} 个")
    for s in pending:
        print(f"  - 待处理 {s.stem}")
    print(f"记忆条目： {store.stats()['total']} 条（全库）")
    if pending and not cfg["trigger"].get("catch_up", False):
        print("\n提示：待处理会话需等下次 Claude Code 启动时由 hook 提炼；"
              "想立即处理可执行 `ai-memory run --catch-up`。")
    return 0


def _cmd_run(args) -> int:
    """手动触发一次提炼（等同 SessionStart hook），便于不用重启 Claude Code 直接验证。"""
    cfg = load_config()
    project = args.project or current_project()
    store = _store()
    provider = provider_from_config(cfg)
    out_dir = Path(args.out_dir) if args.out_dir else Path(".claude") / "memory"
    catch_up = args.catch_up or bool(cfg["trigger"].get("catch_up", False))

    print(f"提炼模型：{type(provider).__name__}")
    out = run_trigger(
        store, project, out_dir, provider,
        exclude_session_id=args.exclude_session,
        catch_up=catch_up,
        max_catchup=args.max_catchup or int(cfg["trigger"].get("max_catchup", 10)),
    )
    if out is None:
        print("没有可提炼的历史会话（会话目录为空、内容为空或尚未有会话文件）。")
        return 0
    print(f"本项目现有记忆 {len(store.all(project))} 条")
    print(f"摘要已导出：{out}")
    latest = store.latest(project)
    if latest is not None:
        print(f"（摘要只注入最新一条，其余请用 `ai-memory search` 检索）")
    return 0


def _cmd_list(args) -> int:
    store = _store()
    project = None if args.all else (args.project or current_project())
    entries = store.list_entries(project=project, type=args.type, limit=args.limit)
    if not entries:
        print("（无记忆）")
        return 0
    for e in entries:
        session = f" session={e.session_id}" if e.session_id else ""
        print(f"{e.id[:8]} [{e.type}]{session} {e.created_at[:19]} {_first_line(e.content)}")
    return 0


def _cmd_show(args) -> int:
    store = _store()
    if args.id == "latest":
        entry = store.latest(args.project or current_project())
    else:
        entry = store.get(args.id)
        if entry is None:  # 支持 id 前缀
            candidates = [e for e in store.list_entries(limit=1000)
                          if e.id.startswith(args.id)]
            entry = candidates[0] if len(candidates) == 1 else None
    if entry is None:
        print("未找到该记忆。", file=sys.stderr)
        return 1
    print(f"id: {entry.id}\ntype: {entry.type}\nproject: {entry.project}\n"
          f"session: {entry.session_id}\ncreated_at: {entry.created_at}\n"
          f"---\n{entry.content}")
    return 0


def _cmd_search(args) -> int:
    store = _store()
    entries = store.search(args.query, project=args.project or current_project(),
                           limit=args.limit)
    if not entries:
        print(f"未找到与「{args.query}」相关的记忆。")
        return 0
    for e in entries:
        print(f"{e.id[:8]} [{e.type}] {e.created_at[:19]} {_first_line(e.content)}")
    return 0


def _cmd_export(args) -> int:
    store = _store()
    project = None if args.all else (args.project or current_project())
    entries = store.list_entries(project=project, limit=10000)
    lines = [
        "# AI 记忆导出",
        f"项目：{project or '（全部）'}",
        f"导出时间：{datetime.now(timezone.utc).isoformat(timespec='seconds')}",
        f"条目数：{len(entries)}",
        "",
    ]
    for e in entries:
        session = f" session={e.session_id}" if e.session_id else ""
        lines.append(f"## [{e.type}]{session} {e.created_at[:19]}")
        lines.append(f"id: {e.id}")
        lines.append("")
        lines.append(e.content.strip())
        lines.append("\n---\n")
    text = "\n".join(lines)
    if args.out:
        Path(args.out).write_text(text, encoding="utf-8")
        print(f"已导出 {len(entries)} 条到 {args.out}")
    else:
        print(text)
    return 0


def _cmd_forget(args) -> int:
    if not args.yes:
        print("危险操作：请加 --yes 确认软删除（删除后搜索不到，可联系作者手工恢复）。",
              file=sys.stderr)
        return 1
    store = _store()
    if store.soft_delete(args.id):
        print(f"已软删除 {args.id}")
        return 0
    print("未找到该记忆或已删除。", file=sys.stderr)
    return 1


def _cmd_prune(args) -> int:
    cfg = load_config()
    cold_days = int(cfg["retention"].get("cold_days", 180))
    store = _store()
    candidates = store.prune_candidates(cold_days)
    if not candidates:
        print(f"没有超过 {cold_days} 天且从未被访问的冷藏记忆。")
        return 0
    for e in candidates:
        print(f"{e.id[:8]} [{e.type}] {e.created_at[:10]} {_first_line(e.content)}")
    if not args.yes:
        print(f"\n（dry-run）以上 {len(candidates)} 条为回收候选，"
              f"core_memory/archive 类型永不回收。加 --yes 执行软删除。")
        return 0
    for e in candidates:
        store.soft_delete(e.id)
    print(f"已软删除 {len(candidates)} 条。")
    return 0


def _cmd_stats(args) -> int:
    s = _store().stats()
    print(f"总条目：{s['total']}    库文件：{s['db_bytes'] / 1024:.1f} KB")
    for row in s["by_project_type"]:
        print(f"  {row['project']} [{row['type']}] "
              f"{row['count']} 条，最新 {str(row['latest'])[:19]}")
    return 0


# ---------- 入口 ----------

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="ai-memory", description="个人 AI 长期记忆工具")
    parser.add_argument("--version", action="version", version=f"ai-memory {__version__}")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("init", help="写入默认配置并打印接入说明")

    p = sub.add_parser("status", help="环境自检：路径、模型、待处理会话")
    p.add_argument("--project")

    p = sub.add_parser("run", help="手动触发一次提炼（等同 hook，便于测试）")
    p.add_argument("--project")
    p.add_argument("--exclude-session", help="排除的 session id（如当前会话）")
    p.add_argument("--catch-up", action="store_true", help="强制补课全部未入库会话")
    p.add_argument("--max-catchup", type=int, help="补课上限，缺省取配置值")
    p.add_argument("--out-dir", help="summary.md 输出目录，缺省 .claude/memory")

    p = sub.add_parser("list", help="列出记忆")
    p.add_argument("--project")
    p.add_argument("--all", action="store_true", help="跨项目列出全部")
    p.add_argument("--type")
    p.add_argument("-n", "--limit", type=int, default=50)

    p = sub.add_parser("show", help="查看一条记忆全文（id 或 latest）")
    p.add_argument("id")
    p.add_argument("--project")

    p = sub.add_parser("search", help="全文搜索记忆")
    p.add_argument("query")
    p.add_argument("--project")
    p.add_argument("-n", "--limit", type=int, default=10)

    p = sub.add_parser("export", help="导出记忆为 markdown")
    p.add_argument("--project")
    p.add_argument("--all", action="store_true")
    p.add_argument("--out", help="输出文件，缺省打印到 stdout")

    p = sub.add_parser("forget", help="软删除一条记忆（需 --yes）")
    p.add_argument("id")
    p.add_argument("--yes", action="store_true")

    p = sub.add_parser("prune", help="回收冷藏记忆（默认 dry-run）")
    p.add_argument("--yes", action="store_true", help="真正执行软删除")

    sub.add_parser("stats", help="记忆库统计")
    return parser


_COMMANDS = {
    "init": _cmd_init,
    "status": _cmd_status,
    "run": _cmd_run,
    "list": _cmd_list,
    "show": _cmd_show,
    "search": _cmd_search,
    "export": _cmd_export,
    "forget": _cmd_forget,
    "prune": _cmd_prune,
    "stats": _cmd_stats,
}


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    return _COMMANDS[args.command](args)


if __name__ == "__main__":
    sys.exit(main())
