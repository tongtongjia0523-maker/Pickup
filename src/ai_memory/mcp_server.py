"""MCP server：提供 get_memory / search_memory 工具按需深查记忆。"""
from ai_memory.store import MemoryStore

_NO_MEMORY = "暂无历史会话记忆，这是全新的开始。"
_SNIPPET_LIMIT = 300


def build_get_memory_response(store: MemoryStore, project: str) -> str:
    entry = store.latest(project)
    if entry is None:
        return _NO_MEMORY
    return entry.content


def build_search_response(store: MemoryStore, project: str, query: str) -> str:
    """全文搜索本项目记忆，格式化为可读文本。命中条目会被 touch（更新访问时间）。"""
    entries = store.search(query, project=project)
    if not entries:
        return f"未找到与「{query}」相关的记忆。"
    blocks = [f"找到 {len(entries)} 条与「{query}」相关的记忆："]
    for i, e in enumerate(entries, 1):
        snippet = e.content.strip()
        if len(snippet) > _SNIPPET_LIMIT:
            snippet = snippet[: _SNIPPET_LIMIT - 1] + "…"
        session = f" session={e.session_id}" if e.session_id else ""
        blocks.append(f"\n---\n[{i}] {e.created_at} type={e.type}{session} id={e.id}\n{snippet}")
    return "\n".join(blocks)


def create_server(store: MemoryStore, project: str):
    """创建 MCP server 实例。project 为记忆归属项目 key。"""
    from mcp.server.mcpserver import MCPServer

    server = MCPServer("ai-memory")

    @server.tool()
    def get_memory() -> str:
        """获取本项目的上次会话记忆摘要（目标/进展/结论/待办/卡点）。"""
        return build_get_memory_response(store, project)

    @server.tool()
    def search_memory(query: str) -> str:
        """全文搜索本项目的所有历史记忆（会话摘要、归档等），按相关度返回片段。"""
        return build_search_response(store, project, query)

    return server


def main() -> None:
    from ai_memory.paths import current_project, default_db_path

    store = MemoryStore(default_db_path())
    project = current_project()
    server = create_server(store, project)
    server.run()


if __name__ == "__main__":
    main()
