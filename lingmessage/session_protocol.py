"""Session Protocol — Abstract base for session lifecycle management.

Defines the standard interface for session management across 灵族 members.
Every member that manages conversation sessions should implement this protocol.

Three-layer memory model:
  Hot  — Active tasks + key rules (CRUSH.md, <500 lines)
  Warm — Session summaries + decisions (LingBus, rolling 30 days)
  Cold — Raw session transcripts (archive directory, on-demand)

The protocol maps to these layers:
  create()     → Hot layer: session starts, context loaded
  checkpoint() → Hot → Warm: mid-session state saved to bus
  restore()    → Warm → Hot: reload previous session state
  archive()    → Warm → Cold: move to long-term storage
  expire()     → Cold: apply decay/purge policies
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from enum import Enum
from typing import Any


class SessionStatus(str, Enum):
    ACTIVE = "active"
    CHECKPOINTED = "checkpointed"
    ARCHIVED = "archived"
    EXPIRED = "expired"


@dataclass(frozen=True)
class SessionMetadata:
    session_id: str
    member_id: str
    status: SessionStatus
    created_at: str
    updated_at: str
    message_count: int = 0
    size_bytes: int = 0
    extra: dict[str, Any] | None = None


class SessionProtocol(ABC):
    """Abstract base for session lifecycle management.

    Members implement this to provide a standardized session interface.
    The protocol covers the full lifecycle:
    create → use → checkpoint → archive → expire.

    Implementations may use any backend (SQLite, files, in-memory).
    The protocol is storage-agnostic; only the interface is prescribed.
    """

    @abstractmethod
    def create(self, member_id: str, **kwargs: Any) -> SessionMetadata:
        """Initialize a new session for a member.

        Returns metadata for the newly created session.
        """

    @abstractmethod
    def checkpoint(self, session_id: str, data: dict[str, Any]) -> SessionMetadata:
        """Save current session state mid-conversation.

        Called periodically or at natural breakpoints.
        Returns updated metadata.
        """

    @abstractmethod
    def restore(self, session_id: str) -> dict[str, Any]:
        """Reload a previous session's state.

        Returns the full session data dictionary.
        Raises KeyError if session_id not found.
        """

    @abstractmethod
    def archive(self, session_id: str) -> SessionMetadata:
        """Move session from warm to cold storage.

        Returns updated metadata with ARCHIVED status.
        """

    @abstractmethod
    def expire(self, session_id: str) -> SessionMetadata:
        """Apply decay/purge to an archived session.

        Returns updated metadata with EXPIRED status.
        """

    @abstractmethod
    def get_metadata(self, session_id: str) -> SessionMetadata:
        """Retrieve metadata for a session.

        Raises KeyError if session_id not found.
        """

    @abstractmethod
    def list_sessions(
        self,
        member_id: str | None = None,
        status: SessionStatus | None = None,
    ) -> list[SessionMetadata]:
        """List sessions, optionally filtered by member and/or status."""

    @abstractmethod
    def close(self) -> None:
        """Release resources (DB connections, file handles, etc.)."""
