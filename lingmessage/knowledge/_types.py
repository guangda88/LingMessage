"""灵识类型定义 — 知识条目的数据结构."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum


class KnowledgeCategory(str, Enum):
    RULE = "rule"
    SKILL = "skill"
    LESSON = "lesson"
    FACT = "fact"
    PATTERN = "pattern"


class KnowledgeSeverity(str, Enum):
    IRON_RULE = "iron_rule"
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"
    INFO = "info"


class VerificationStatus(str, Enum):
    UNVERIFIED = "unverified"
    VERIFIED = "verified"
    DISPUTED = "disputed"
    DEPRECATED = "deprecated"


@dataclass(frozen=True)
class KnowledgeEntry:
    id: str
    category: KnowledgeCategory
    severity: KnowledgeSeverity
    title: str
    content: str
    domain: str
    source_agent: str
    verification: VerificationStatus
    verified_by: tuple[str, ...]
    tags: tuple[str, ...]
    evidence: str
    created_at: str
    updated_at: str
    recall_count: int
    weight: float

    @classmethod
    def create(
        cls,
        *,
        category: KnowledgeCategory,
        severity: KnowledgeSeverity,
        title: str,
        content: str,
        domain: str,
        source_agent: str,
        tags: tuple[str, ...] = (),
        evidence: str = "",
    ) -> KnowledgeEntry:
        now = datetime.now(timezone.utc).isoformat()
        from uuid import uuid4
        return cls(
            id=uuid4().hex[:16],
            category=category,
            severity=severity,
            title=title,
            content=content,
            domain=domain,
            source_agent=source_agent,
            verification=VerificationStatus.UNVERIFIED,
            verified_by=(),
            tags=tags,
            evidence=evidence,
            created_at=now,
            updated_at=now,
            recall_count=0,
            weight=1.0,
        )

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "category": self.category.value,
            "severity": self.severity.value,
            "title": self.title,
            "content": self.content,
            "domain": self.domain,
            "source_agent": self.source_agent,
            "verification": self.verification.value,
            "verified_by": list(self.verified_by),
            "tags": list(self.tags),
            "evidence": self.evidence,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "recall_count": self.recall_count,
            "weight": self.weight,
        }

    @classmethod
    def from_row(cls, row: tuple) -> KnowledgeEntry:
        import json
        return cls(
            id=row[0],
            category=KnowledgeCategory(row[1]),
            severity=KnowledgeSeverity(row[2]),
            title=row[3],
            content=row[4],
            domain=row[5],
            source_agent=row[6],
            verification=VerificationStatus(row[7]),
            verified_by=tuple(json.loads(row[8])) if row[8] else (),
            tags=tuple(json.loads(row[9])) if row[9] else (),
            evidence=row[10] or "",
            created_at=row[11],
            updated_at=row[12],
            recall_count=row[13],
            weight=row[14],
        )
