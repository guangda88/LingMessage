"""灵识存储层 — SQLite 持久化知识库."""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator

from ._types import (
    KnowledgeCategory,
    KnowledgeEntry,
    KnowledgeSeverity,
    VerificationStatus,
)

_DEFAULT_PATH = Path.home() / ".lingknowledge" / "ecosystem.db"

_SCHEMA = """
CREATE TABLE IF NOT EXISTS knowledge (
    id TEXT PRIMARY KEY,
    category TEXT NOT NULL,
    severity TEXT NOT NULL,
    title TEXT NOT NULL,
    content TEXT NOT NULL,
    domain TEXT NOT NULL DEFAULT '',
    source_agent TEXT NOT NULL,
    verification TEXT NOT NULL DEFAULT 'unverified',
    verified_by TEXT DEFAULT '[]',
    tags TEXT DEFAULT '[]',
    evidence TEXT DEFAULT '',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    recall_count INTEGER DEFAULT 0,
    weight REAL DEFAULT 1.0
);
CREATE INDEX IF NOT EXISTS idx_knowledge_category ON knowledge(category);
CREATE INDEX IF NOT EXISTS idx_knowledge_domain ON knowledge(domain);
CREATE INDEX IF NOT EXISTS idx_knowledge_severity ON knowledge(severity);
CREATE INDEX IF NOT EXISTS idx_knowledge_source ON knowledge(source_agent);
CREATE INDEX IF NOT EXISTS idx_knowledge_verification ON knowledge(verification);
CREATE INDEX IF NOT EXISTS idx_knowledge_weight ON knowledge(weight);
"""


class EcosystemKnowledgeBase:
    def __init__(self, db_path: str | Path | None = None) -> None:
        if db_path is None:
            db_path = _DEFAULT_PATH
        self._db_path = Path(db_path)
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn: sqlite3.Connection | None = None
        self._init_db()

    def _init_db(self) -> None:
        conn = self._get_conn()
        conn.executescript(_SCHEMA)
        conn.commit()

    def _get_conn(self) -> sqlite3.Connection:
        if self._conn is None:
            self._conn = sqlite3.connect(str(self._db_path))
            self._conn.execute("PRAGMA busy_timeout=10000")
            self._conn.execute("PRAGMA journal_mode=WAL")
            self._conn.row_factory = sqlite3.Row
        return self._conn

    def add(self, entry: KnowledgeEntry) -> str:
        conn = self._get_conn()
        conn.execute(
            """INSERT OR REPLACE INTO knowledge
               (id, category, severity, title, content, domain,
                source_agent, verification, verified_by, tags,
                evidence, created_at, updated_at, recall_count, weight)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                entry.id,
                entry.category.value,
                entry.severity.value,
                entry.title,
                entry.content,
                entry.domain,
                entry.source_agent,
                entry.verification.value,
                json.dumps(list(entry.verified_by)),
                json.dumps(list(entry.tags)),
                entry.evidence,
                entry.created_at,
                entry.updated_at,
                entry.recall_count,
                entry.weight,
            ),
        )
        conn.commit()
        return entry.id

    def get(self, entry_id: str) -> KnowledgeEntry | None:
        conn = self._get_conn()
        row = conn.execute(
            "SELECT * FROM knowledge WHERE id = ?", (entry_id,)
        ).fetchone()
        if row is None:
            return None
        return KnowledgeEntry.from_row(tuple(row))

    def search(
        self,
        keyword: str = "",
        category: KnowledgeCategory | None = None,
        domain: str = "",
        severity: KnowledgeSeverity | None = None,
        source_agent: str = "",
        verification: VerificationStatus | None = None,
        tags: tuple[str, ...] = (),
        limit: int = 20,
    ) -> list[KnowledgeEntry]:
        conn = self._get_conn()
        conditions: list[str] = []
        params: list = []

        if keyword:
            conditions.append(
                "(title LIKE ? OR content LIKE ? OR evidence LIKE ?)"
            )
            pattern = f"%{keyword}%"
            params.extend([pattern, pattern, pattern])
        if category:
            conditions.append("category = ?")
            params.append(category.value)
        if domain:
            conditions.append("domain = ?")
            params.append(domain)
        if severity:
            conditions.append("severity = ?")
            params.append(severity.value)
        if source_agent:
            conditions.append("source_agent = ?")
            params.append(source_agent)
        if verification:
            conditions.append("verification = ?")
            params.append(verification.value)
        for tag in tags:
            conditions.append("tags LIKE ?")
            params.append(f'%"{tag}"%')

        where = " AND ".join(conditions) if conditions else "1=1"
        sql = "SELECT * FROM knowledge WHERE " + where + " ORDER BY weight DESC, severity DESC LIMIT ?"
        params.append(limit)

        rows = conn.execute(sql, params).fetchall()
        return [KnowledgeEntry.from_row(tuple(r)) for r in rows]

    def get_iron_rules(self) -> list[KnowledgeEntry]:
        return self.search(severity=KnowledgeSeverity.IRON_RULE, limit=100)

    def verify(self, entry_id: str, verifier: str) -> bool:
        conn = self._get_conn()
        row = conn.execute(
            "SELECT verified_by FROM knowledge WHERE id = ?", (entry_id,)
        ).fetchone()
        if row is None:
            return False
        existing = json.loads(row[0])
        if verifier not in existing:
            existing.append(verifier)
        conn.execute(
            """UPDATE knowledge
               SET verification = 'verified', verified_by = ?,
                   updated_at = ?, weight = weight * 1.2
               WHERE id = ?""",
            (json.dumps(existing), datetime.now(timezone.utc).isoformat(), entry_id),
        )
        conn.commit()
        return True

    def dispute(self, entry_id: str, disputant: str, reason: str) -> bool:
        conn = self._get_conn()
        conn.execute(
            """UPDATE knowledge
               SET verification = 'disputed',
                   evidence = evidence || ?,
                   updated_at = ?,
                   weight = weight * 0.5
               WHERE id = ?""",
            (
                f"\n[ disputed by {disputant}: {reason} ]",
                datetime.now(timezone.utc).isoformat(),
                entry_id,
            ),
        )
        conn.commit()
        return True

    def deprecate(self, entry_id: str) -> bool:
        conn = self._get_conn()
        conn.execute(
            """UPDATE knowledge
               SET verification = 'deprecated',
                   updated_at = ?,
                   weight = 0.0
               WHERE id = ?""",
            (datetime.now(timezone.utc).isoformat(), entry_id),
        )
        conn.commit()
        return True

    def record_recall(self, entry_id: str) -> None:
        conn = self._get_conn()
        conn.execute(
            """UPDATE knowledge
               SET recall_count = recall_count + 1,
                   weight = weight * 1.05
               WHERE id = ?""",
            (entry_id,),
        )
        conn.commit()

    def iter_all(self) -> Iterator[KnowledgeEntry]:
        conn = self._get_conn()
        rows = conn.execute(
            "SELECT * FROM knowledge ORDER BY weight DESC"
        ).fetchall()
        for row in rows:
            yield KnowledgeEntry.from_row(tuple(row))

    def stats(self) -> dict:
        conn = self._get_conn()
        total = conn.execute("SELECT COUNT(*) FROM knowledge").fetchone()[0]
        by_cat: dict[str, int] = {}
        for row in conn.execute(
            "SELECT category, COUNT(*) FROM knowledge GROUP BY category"
        ).fetchall():
            by_cat[row[0]] = row[1]
        by_sev: dict[str, int] = {}
        for row in conn.execute(
            "SELECT severity, COUNT(*) FROM knowledge GROUP BY severity"
        ).fetchall():
            by_sev[row[0]] = row[1]
        by_ver: dict[str, int] = {}
        for row in conn.execute(
            "SELECT verification, COUNT(*) FROM knowledge GROUP BY verification"
        ).fetchall():
            by_ver[row[0]] = row[1]
        agents = conn.execute(
            "SELECT COUNT(DISTINCT source_agent) FROM knowledge"
        ).fetchone()[0]
        return {
            "total": total,
            "by_category": by_cat,
            "by_severity": by_sev,
            "by_verification": by_ver,
            "contributing_agents": agents,
        }

    def export_all(self) -> list[dict]:
        return [e.to_dict() for e in self.iter_all()]

    def close(self) -> None:
        if self._conn:
            self._conn.close()
            self._conn = None
