"""触发适配器核心逻辑：串联 读 session → 提炼 → 存库 → 导出。"""
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from ai_memory import embeddings as _emb
from ai_memory.providers import LLMProvider
from ai_memory.session_reader import find_sessions_since, read_conversation
from ai_memory.store import MemoryStore, new_entry
from ai_memory.summarizer import summarize
from ai_memory.summary_export import export_summary


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


# 会话里出现这些信号 → 视为「已形成结论/产出成品」，归入 archive
_ARCHIVE_HINTS = ("已生成", "已写入", "已保存", "已创建", "已完成", "```")


def classify_session(conversation: str) -> str:
    """规则分类（确定性、可离线）：

    - archive：出现成品信号（代码块 / 已生成 / 已保存 等）——已形成讨论结果
    - scratch：用户消息 ≤3 条——零散追问
    - session_summary：其余
    """
    if any(hint in conversation for hint in _ARCHIVE_HINTS):
        return "archive"
    if conversation.count("[user] ") <= 3:
        return "scratch"
    return "session_summary"


def _archive_session(store: MemoryStore, project: str, session: Path,
                     provider: LLMProvider) -> Optional[tuple[str, str]]:
    """提炼单个 session 并入库。已入库/无内容时返回 None（去重，绝不重复写入）。"""
    # 不限类型去重：同一 session 无论归为 archive/scratch/session_summary 都只入库一次
    if store.get_by_session(project, session.stem, type=None) is not None:
        return None
    conversation = read_conversation(session)
    if not conversation.strip():
        return None
    summary = summarize(conversation, provider)
    if not summary.strip():
        return None
    entry = new_entry(type=classify_session(conversation), project=project,
                      content=summary, created_at=_now(), session_id=session.stem)
    if not store.append(entry):
        return None  # 并发下已被其他窗口写入
    _emb.try_embed(store, entry.id, summary)
    return session.stem, summary


def run_trigger(store: MemoryStore, project: str, out_dir: Path,
                provider: LLMProvider,
                exclude_session_id: Optional[str] = None,
                catch_up: bool = False, max_catchup: int = 10) -> Optional[Path]:
    """执行一次续接流程，返回导出的 summary.md 路径；无任何摘要返回 None。

    - 默认只处理项目内最新一个未入库的 session。
    - catch_up=True 时把项目内所有未入库 session 补入库（最多 max_catchup 个，
      取最近的），summary.md 仍只注入最新一条摘要，其余以索引段附后。
    """
    exclude = frozenset([exclude_session_id]) if exclude_session_id else frozenset()
    sessions = find_sessions_since(project=project, exclude_ids=exclude)
    if catch_up:
        pending = sessions[-max_catchup:]
    else:
        pending = sessions[-1:]  # 只取最新一个，_archive_session 负责去重

    archived = []
    for session in pending:
        result = _archive_session(store, project, session, provider)
        if result is not None:
            archived.append(result)

    # extra_sessions 不含最新一条（最新一条由 export_summary 从库里取）
    extra = archived[:-1] if archived else None
    return export_summary(store, project, out_dir, extra_sessions=extra)
