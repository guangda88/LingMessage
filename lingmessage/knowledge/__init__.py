"""灵识 — 灵族统一生态知识库.

所有灵字辈 agent 共享的知识存储。任何 agent 学到的规则、技能、教训，
写入此处即可被全族共享。

存储路径: ~/.lingknowledge/ecosystem.db (SQLite)
"""
from __future__ import annotations

from ._store import EcosystemKnowledgeBase
from ._types import (
    KnowledgeCategory,
    KnowledgeEntry,
    KnowledgeSeverity,
    VerificationStatus,
)

__all__ = [
    "EcosystemKnowledgeBase",
    "KnowledgeCategory",
    "KnowledgeEntry",
    "KnowledgeSeverity",
    "VerificationStatus",
]
