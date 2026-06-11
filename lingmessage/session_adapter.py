"""Family Session Protocol Adapter — Backward-compatible shim.

Deprecated: FamilySessionManager now implements SessionProtocol directly.
Import from session_manager instead.

This module is kept for backward compatibility only.
"""

from __future__ import annotations

import logging
from typing import Any

from .session_manager import (
    FamilySessionManager,
    _make_session_id,  # re-export for backward compat
    _parse_session_id,  # re-export for backward compat
)
from .session_protocol import SessionMetadata, SessionProtocol, SessionStatus

logger = logging.getLogger("lingmessage.session_adapter")


class FamilySessionProtocolAdapter(SessionProtocol):
    """Deprecated thin shim: delegates to FamilySessionManager.

    Use FamilySessionManager directly instead.
    """

    def __init__(self, manager: FamilySessionManager) -> None:
        self._manager = manager

    def create(self, member_id: str, **kwargs: Any) -> SessionMetadata:
        return self._manager.create(member_id, **kwargs)

    def checkpoint(self, session_id: str, data: dict[str, Any]) -> SessionMetadata:
        return self._manager.checkpoint(session_id, data)

    def restore(self, session_id: str) -> dict[str, Any]:
        return self._manager.restore(session_id)

    def archive(self, session_id: str) -> SessionMetadata:
        return self._manager.archive(session_id)

    def expire(self, session_id: str) -> SessionMetadata:
        return self._manager.expire(session_id)

    def get_metadata(self, session_id: str) -> SessionMetadata:
        return self._manager.get_metadata(session_id)

    def list_sessions(
        self,
        member_id: str | None = None,
        status: SessionStatus | None = None,
    ) -> list[SessionMetadata]:
        return self._manager.list_sessions(member_id=member_id, status=status)

    def close(self) -> None:
        self._manager.close()
