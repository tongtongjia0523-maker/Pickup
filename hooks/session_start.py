"""Claude Code SessionStart hook 入口。

铁律：任何异常都绝不阻塞 Claude Code 启动——全部吞掉并写入 error.log。
"""
import json
import sys
import traceback
from pathlib import Path

from ai_memory.config import load_config
from ai_memory.paths import current_project, default_db_path, project_key
from ai_memory.providers import provider_from_config
from ai_memory.store import MemoryStore
from ai_memory.trigger import run_trigger

# 常驻核心记忆的注入预算（字符），超出部分截断——常驻记忆必须精简
_CORE_BUDGET = 300


def _log_error() -> None:
    """把异常 traceback 追加到 error.log（>200KB 截断），写日志本身也绝不抛出。"""
    try:
        log = default_db_path().parent / "error.log"
        log.parent.mkdir(parents=True, exist_ok=True)
        if log.exists() and log.stat().st_size > 200 * 1024:
            log.write_text("", encoding="utf-8")
        with open(log, "a", encoding="utf-8") as f:
            f.write(traceback.format_exc() + "\n")
    except Exception:
        pass


def main() -> None:
    # Claude Code 通过 stdin 传入 JSON（含 session_id、cwd）
    data = {}
    if not sys.stdin.isatty():
        try:
            data = json.loads(sys.stdin.read())
        except (json.JSONDecodeError, ValueError):
            data = {}

    cwd = data.get("cwd")
    project = project_key(cwd) if cwd else current_project()
    current_session_id = data.get("session_id")

    try:
        cfg = load_config()
        store = MemoryStore(default_db_path())
        out_dir = Path(".claude") / "memory"
        run_trigger(
            store, project, out_dir, provider_from_config(cfg),
            exclude_session_id=current_session_id,
            catch_up=bool(cfg["trigger"].get("catch_up", False)),
            max_catchup=int(cfg["trigger"].get("max_catchup", 10)),
        )
        # 记忆回收：默认关闭；开启且 delete_policy=hard 时顺带软删除冷藏条目
        retention = cfg["retention"]
        if retention.get("enabled") and retention.get("delete_policy") == "hard":
            for entry in store.prune_candidates(int(retention.get("cold_days", 180))):
                store.soft_delete(entry.id)
        # SessionStart 在退出码 0 时，stdout 会被 Claude Code 直接并入会话上下文，
        # 无需（也不应）用 hookSpecificOutput 这类未文档化的 JSON 包裹。
        # 文本写成强制指令：Claude Code 在用户发出第一条消息前不会产出回复，
        # 因此只能要求模型在「本轮回复的最开头」主动复述，而不是等用户追问。
        core = store.get_core(project)
        entry = store.latest_any(project)
        if core is None and entry is None:
            return
        parts = [
            "【AI 记忆·启动上下文】以下是本项目的历史记忆。"
            "请在本轮回复的最开头先主动向用户复述下面的进展/待办/卡点，"
            "不要等用户提到它、也不要直接开始干活。"
            "若需要更早的历史，可调用 MCP 工具 search_memory 检索。"
        ]
        if core is not None:
            parts.append("【常驻核心记忆】\n" + core.content.strip()[:_CORE_BUDGET])
        if entry is not None:
            parts.append("【上次会话摘要】\n" + entry.content.strip())
        print("\n---\n".join(parts))
    except Exception:
        _log_error()


if __name__ == "__main__":
    main()
