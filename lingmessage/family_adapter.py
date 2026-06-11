"""Family Adapter Protocol for unified member communication.

This protocol provides a standardized interface for all LingFamily members,
enabling pluggable communication without hardcoded logic in _family_poller.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from enum import Enum
from typing import AsyncIterator, Optional
from datetime import datetime


class MemberStatus(str, Enum):
    """Member status indicators."""
    ONLINE = "online"
    PROCESSING = "processing"
    IDLE = "idle"
    WARMING = "warming"
    OFFLINE = "offline"


@dataclass
class MemberInfo:
    """Information about a family member."""
    identity: str
    name: str
    status: MemberStatus
    session_key: Optional[str] = None
    last_active: Optional[datetime] = None
    metadata: dict = None

    def __post_init__(self):
        if self.metadata is None:
            self.metadata = {}


@dataclass
class DeltaEvent:
    """Streaming response delta."""
    identity: str
    thread_id: str
    text: str
    is_final: bool = False
    metadata: dict = None

    def __post_init__(self):
        if self.metadata is None:
            self.metadata = {}


class FamilyAdapter(ABC):
    """Abstract base class for family member adapters.

    Concrete adapters (lingclaudeAdapter, lingflowAdapter, etc.)
    implement the communication protocol for specific members.
    """

    @abstractmethod
    async def send_message(
        self,
        identity: str,
        message: str,
        thread_id: Optional[str] = None,
        timeout_seconds: int = 120,
    ) -> str:
        """Send a message to the member and return the complete response.

        Args:
            identity: Member identity (e.g., 'lingclaude', 'lingflow')
            message: User message text
            thread_id: Optional thread ID for context
            timeout_seconds: Maximum time to wait for response

        Returns:
            Complete response text

        Raises:
            TimeoutError: If no response within timeout_seconds
            ConnectionError: If member is unreachable
        """
        ...

    @abstractmethod
    async def stream_response(
        self,
        identity: str,
        message: str,
        thread_id: Optional[str] = None,
        timeout_seconds: int = 120,
    ) -> AsyncIterator[DeltaEvent]:
        """Stream response deltas from the member.

        Yields DeltaEvent objects as response is generated.
        The final event has is_final=True.

        Args:
            identity: Member identity
            message: User message text
            thread_id: Optional thread ID for context
            timeout_seconds: Maximum time to wait for response

        Yields:
            DeltaEvent: Streaming response chunks
        """
        ...

    @abstractmethod
    async def get_status(self, identity: str) -> MemberStatus:
        """Get the current status of a family member.

        Returns:
            MemberStatus: Current status (online/processing/idle/offline)
        """
        ...

    async def interrupt(self, identity: str) -> bool:
        """Interrupt an ongoing request.

        Returns:
            bool: True if interrupted successfully
        """
        return False

    async def fetch_history(
        self,
        identity: str,
        thread_id: str,
        limit: int = 100,
    ) -> list[dict]:
        """Fetch conversation history for a thread.

        Returns:
            List of message dictionaries
        """
        return []

    @classmethod
    def supports_streaming(cls) -> bool:
        """Check if this adapter supports streaming responses."""
        return False

    @classmethod
    def supports_interrupt(cls) -> bool:
        """Check if this adapter supports interrupting requests."""
        return False
