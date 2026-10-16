"""SQLite 累积式记忆存储。

v2 升级：
- WAL 模式（多窗口并发写保护）
- PRAGMA user_version 迁移机制
- 新列：deleted_at（软删除）/ last_accessed_at（冷藏依据）/ importance
- (project, session_id) 部分唯一索引：同一 session 摘要去重，绝不重复入库
- FTS5(trigram) 全文搜索：支持中文子串匹配
"""
import sqlite3
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

SCHEMA_VERSION = 2

# 永不自动回收的类型
PROTECTED_TYPES = ("core_memory", "archive")

_BASE_TABLE = """
CREATE TABLE IF NOT EXISTS memory_entry (
    id          TEXT PRIMARY KEY,
    type        TEXT NOT NULL,
    project     TEXT NOT NULL,
    session_id  TEXT,
    content     TEXT NOT NULL,
    created_at  TEXT NOT NULL
)
"""

_COLUMNS = (
    "id", "type", "project", "session_id", "content", "created_at",
    "deleted_at", "last_accessed_at", "importance",
)


@dataclass
class MemoryEntry:
    id: str
    type: str
    project: str
    session_id: str | None
    content: str
    created_at: str
    deleted_at: str | None = None
    last_accessed_at: str | None = None
    importance: int = 0


def new_entry(type: str, project: str, content: str, created_at: str,
              session_id: str | None = None, importance: int = 0) -> MemoryEntry:
    return MemoryEntry(str(uuid.uuid4()), type, project, session_id,
                       content, created_at, importance=importance)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class MemoryStore:
    def __init__(self, db_path: Path):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA busy_timeout=3000")
        return conn

    # ---------- schema & migration ----------

    def _init_db(self) -> None:
        with self._connect() as conn:
            version = conn.execute("PRAGMA user_version").fetchone()[0]
            if version < 1:
                conn.execute(_BASE_TABLE)
                conn.execute("PRAGMA user_version = 1")
                version = 1
            if version < 2:
                self._migrate_v2(conn)
                conn.execute("PRAGMA user_version = 2")

    def _migrate_v2(self, conn: sqlite3.Connection) -> None:
        for ddl in (
            "ALTER TABLE memory_entry ADD COLUMN deleted_at TEXT",
            "ALTER TABLE memory_entry ADD COLUMN last_accessed_at TEXT",
            "ALTER TABLE memory_entry ADD COLUMN importance INTEGER NOT NULL DEFAULT 0",
        ):
            try:
                conn.execute(ddl)
            except sqlite3.OperationalError:
                pass  # 列已存在（幂等）
        # v1 时代同一会话可能重复入库（每次启动都 append），先去重（保留最新一条），
        # 否则唯一索引建不起来、迁移会反复失败
        conn.execute(
            """
            DELETE FROM memory_entry
            WHERE type = 'session_summary' AND session_id IS NOT NULL AND rowid NOT IN (
                SELECT MAX(rowid) FROM memory_entry
                WHERE type = 'session_summary' AND session_id IS NOT NULL
                GROUP BY project, session_id
            )
            """
        )
        conn.execute(
            """
            CREATE UNIQUE INDEX IF NOT EXISTS ux_memory_session
            ON memory_entry(project, session_id)
            WHERE session_id IS NOT NULL AND type = 'session_summary'
            """
        )
        conn.execute(
            """
            CREATE VIRTUAL TABLE IF NOT EXISTS memory_fts
            USING fts5(content, entry_id UNINDEXED, tokenize='trigram')
            """
        )
        conn.execute(
            """
            CREATE TRIGGER IF NOT EXISTS memory_fts_ai AFTER INSERT ON memory_entry BEGIN
                INSERT INTO memory_fts(rowid, content, entry_id)
                VALUES (new.rowid, new.content, new.id);
            END
            """
        )
        # 全量重建 FTS（幂等），保证与去重后的数据一致
        conn.execute("DELETE FROM memory_fts")
        conn.execute(
            "INSERT INTO memory_fts(rowid, content, entry_id) "
            "SELECT rowid, content, id FROM memory_entry"
        )

    # ---------- write ----------

    def append(self, entry: MemoryEntry) -> bool:
        """追加一条记忆。同一 (project, session_id) 的摘要重复时跳过，返回是否真正写入。"""
        try:
            with self._connect() as conn:
                conn.execute(
                    "INSERT INTO memory_entry VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (entry.id, entry.type, entry.project, entry.session_id,
                     entry.content, entry.created_at, entry.deleted_at,
                     entry.last_accessed_at, entry.importance),
                )
            return True
        except sqlite3.IntegrityError:
            return False

    def soft_delete(self, entry_id: str) -> bool:
        with self._connect() as conn:
            cur = conn.execute(
                "UPDATE memory_entry SET deleted_at = ? WHERE id = ? AND deleted_at IS NULL",
                (_now(), entry_id),
            )
        return cur.rowcount > 0

    def touch(self, entry_ids: list[str]) -> None:
        if not entry_ids:
            return
        with self._connect() as conn:
            conn.executemany(
                "UPDATE memory_entry SET last_accessed_at = ? WHERE id = ?",
                [(_now(), i) for i in entry_ids],
            )

    # ---------- read ----------

    def _row_to_entry(self, row) -> MemoryEntry:
        return MemoryEntry(*row)

    def get(self, entry_id: str) -> Optional[MemoryEntry]:
        with self._connect() as conn:
            row = conn.execute(
                f"SELECT {', '.join(_COLUMNS)} FROM memory_entry WHERE id = ?",
                (entry_id,),
            ).fetchone()
        return self._row_to_entry(row) if row else None

    def get_by_session(self, project: str, session_id: str,
                       type: str = "session_summary") -> Optional[MemoryEntry]:
        with self._connect() as conn:
            row = conn.execute(
                f"SELECT {', '.join(_COLUMNS)} FROM memory_entry "
                "WHERE project = ? AND session_id = ? AND type = ?",
                (project, session_id, type),
            ).fetchone()
        return self._row_to_entry(row) if row else None

    def latest(self, project: str, type: str = "session_summary") -> MemoryEntry | None:
        with self._connect() as conn:
            row = conn.execute(
                f"SELECT {', '.join(_COLUMNS)} FROM memory_entry "
                "WHERE project = ? AND type = ? AND deleted_at IS NULL "
                "ORDER BY created_at DESC LIMIT 1",
                (project, type),
            ).fetchone()
        return self._row_to_entry(row) if row else None

    def all(self, project: str, type: str = "session_summary") -> list[MemoryEntry]:
        with self._connect() as conn:
            rows = conn.execute(
                f"SELECT {', '.join(_COLUMNS)} FROM memory_entry "
                "WHERE project = ? AND type = ? AND deleted_at IS NULL "
                "ORDER BY created_at ASC",
                (project, type),
            ).fetchall()
        return [self._row_to_entry(r) for r in rows]

    def list_entries(self, project: str | None = None, type: str | None = None,
                     include_deleted: bool = False, limit: int = 100) -> list[MemoryEntry]:
        sql = f"SELECT {', '.join(_COLUMNS)} FROM memory_entry WHERE 1=1"
        params: list = []
        if not include_deleted:
            sql += " AND deleted_at IS NULL"
        if project is not None:
            sql += " AND project = ?"
            params.append(project)
        if type is not None:
            sql += " AND type = ?"
            params.append(type)
        sql += " ORDER BY created_at DESC LIMIT ?"
        params.append(limit)
        with self._connect() as conn:
            rows = conn.execute(sql, params).fetchall()
        return [self._row_to_entry(r) for r in rows]

    # ---------- search ----------

    @staticmethod
    def _fts_query(query: str) -> str:
        escaped = query.replace('"', '""')
        return f'"{escaped}"'

    def search(self, query: str, project: str | None = None,
               limit: int = 10) -> list[MemoryEntry]:
        """FTS5(trigram) 全文搜索；query 不足 3 字符时回退 LIKE。命中条目更新 last_accessed_at。"""
        query = query.strip()
        if not query:
            return []
        if len(query) >= 3:
            sql = (
                f"SELECT {', '.join('e.' + c for c in _COLUMNS)} "
                "FROM memory_entry e JOIN memory_fts f ON f.entry_id = e.id "
                "WHERE memory_fts MATCH ? AND e.deleted_at IS NULL"
            )
            params: list = [self._fts_query(query)]
        else:
            sql = (
                f"SELECT {', '.join(_COLUMNS)} FROM memory_entry "
                "WHERE content LIKE ? AND deleted_at IS NULL"
            )
            params = [f"%{query}%"]
        if project is not None:
            sql += " AND " + ("e." if len(query) >= 3 else "") + "project = ?"
            params.append(project)
        sql += " ORDER BY " + ("e." if len(query) >= 3 else "") + "created_at DESC LIMIT ?"
        params.append(limit)
        with self._connect() as conn:
            rows = conn.execute(sql, params).fetchall()
        entries = [self._row_to_entry(r) for r in rows]
        self.touch([e.id for e in entries])
        return entries

    # ---------- retention & stats ----------

    def prune_candidates(self, cold_days: int, now: Optional[datetime] = None) -> list[MemoryEntry]:
        """超过 cold_days 且从未被访问过的非保护类型条目（回收候选，绝不返回保护类型）。"""
        now = now or datetime.now(timezone.utc)
        cutoff = (now - timedelta(days=cold_days)).isoformat()
        placeholders = ",".join("?" for _ in PROTECTED_TYPES)
        with self._connect() as conn:
            rows = conn.execute(
                f"SELECT {', '.join(_COLUMNS)} FROM memory_entry "
                f"WHERE deleted_at IS NULL AND last_accessed_at IS NULL "
                f"AND created_at < ? AND type NOT IN ({placeholders}) "
                "ORDER BY created_at ASC",
                (cutoff, *PROTECTED_TYPES),
            ).fetchall()
        return [self._row_to_entry(r) for r in rows]

    def stats(self) -> dict:
        with self._connect() as conn:
            total = conn.execute(
                "SELECT COUNT(*) FROM memory_entry WHERE deleted_at IS NULL"
            ).fetchone()[0]
            rows = conn.execute(
                "SELECT project, type, COUNT(*), MAX(created_at) "
                "FROM memory_entry WHERE deleted_at IS NULL "
                "GROUP BY project, type ORDER BY project, type"
            ).fetchall()
        size = self.db_path.stat().st_size if self.db_path.exists() else 0
        return {
            "total": total,
            "db_bytes": size,
            "by_project_type": [
                {"project": p, "type": t, "count": c, "latest": latest}
                for p, t, c, latest in rows
            ],
        }
