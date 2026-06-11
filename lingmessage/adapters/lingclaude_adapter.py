"""lingclaude adapter implementation.

Communicates with lingclaude via HTTP API on port 8700.
"""

from __future__ import annotations

import httpx
import asyncio
import logging
from typing import AsyncIterator, Optional

from lingmessage.family_adapter import (
    FamilyAdapter,
    MemberStatus,
    DeltaEvent,
)

logger = logging.getLogger("lingmessage.adapters.lingclaude")


class lingclaudeAdapter(FamilyAdapter):
    """Adapter for lingclaude (灵克) member.

    Uses HTTP API on http://127.0.0.1:8700
    Supports multi-slot concurrent sessions via slot_id parameter.
    """

    def __init__(self):
        self.api_base = "http://127.0.0.1:8700"
        self.timeout = 30
        self._client = httpx.AsyncClient(timeout=self.timeout)
        self._active_slots: dict[str, str] = {}  # slot_id -> thread_id
        self._session_restored = False

    async def restore_sessions(self) -> int:
        """Restore saved sessions from persistence. Returns count restored."""
        if self._session_restored:
            return 0
        self._session_restored = True
        try:
            from lingmessage.session_manager import get_session_manager
            mgr = get_session_manager()
            sessions = mgr.load_all_sessions("lingclaude")
            for session in sessions:
                if session.thread_id:
                    self._active_slots[session.slot_id] = session.thread_id
            logger.info(f"Restored {len(sessions)} sessions for lingclaude")
            return len(sessions)
        except Exception as e:
            logger.debug(f"Session restore failed: {e}")
            return 0

    def _resolve_slot(self, thread_id: str | None) -> str | None:
        """Extract or map thread_id to a slot context."""
        if not thread_id:
            return None
        # Thread ID format: "slot:{slot_id}:..." or plain thread_id
        if thread_id.startswith("slot:"):
            parts = thread_id.split(":", 2)
            if len(parts) >= 2:
                return parts[1]
        return thread_id

    async def send_message(
        self,
        identity: str,
        message: str,
        thread_id: Optional[str] = None,
        timeout_seconds: int = 120,
        slot_id: Optional[str] = None,
    ) -> str:
        """Send message to lingclaude and return response."""
        try:
            payload: dict = {"query": message}
            if thread_id:
                payload["thread_id"] = thread_id
            if slot_id:
                payload["slot_id"] = slot_id
                self._active_slots[slot_id] = thread_id or ""

            response = await self._client.post(
                f"{self.api_base}/api/submit",
                json=payload,
                timeout=timeout_seconds,
            )
            response.raise_for_status()
            data = response.json()
            return data.get("text", "").strip()
        except httpx.TimeoutException:
            raise TimeoutError(f"lingclaude timeout after {timeout_seconds}s")
        except httpx.ConnectError:
            raise ConnectionError(f"lingclaude not reachable at {self.api_base}")
        except Exception as e:
            raise RuntimeError(f"lingclaude error: {e}")

    async def stream_response(
        self,
        identity: str,
        message: str,
        thread_id: Optional[str] = None,
        timeout_seconds: int = 120,
        slot_id: Optional[str] = None,
    ) -> AsyncIterator[DeltaEvent]:
        """Stream response from lingclaude (non-streaming for now)."""
        full_response = await self.send_message(identity, message, thread_id, timeout_seconds, slot_id=slot_id)
        
        # Split into chunks for typewriter effect
        chunk_size = 20
        for i in range(0, len(full_response), chunk_size):
            chunk = full_response[i:i + chunk_size]
            yield DeltaEvent(
                identity=identity,
                thread_id=thread_id or "",
                text=chunk,
                is_final=False
            )
            await asyncio.sleep(0.05)  # Small delay for typewriter effect
        
        # Final event
        yield DeltaEvent(
            identity=identity,
            thread_id=thread_id or "",
            text="",
            is_final=True
        )

    async def get_status(self, identity: str) -> MemberStatus:
        """Check lingclaude status."""
        # Check if process is running
        try:
            proc = await asyncio.create_subprocess_exec(
                "pgrep",
                "-f",
                "python.*lingclaude",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.DEVNULL,
            )
            await proc.communicate()
            is_running = proc.returncode == 0
        except Exception:
            is_running = False

        if not is_running:
            return MemberStatus.OFFLINE

        # Check if API is reachable
        try:
            response = await self._client.get(
                f"{self.api_base}/api/health",
                timeout=2.0,
            )
            if response.status_code == 200:
                data = response.json()
                state = data.get("state", "idle")
                if state == "processing":
                    return MemberStatus.PROCESSING
                else:
                    return MemberStatus.IDLE
        except Exception:
            pass

        return MemberStatus.ONLINE

    async def interrupt(self, identity: str) -> bool:
        """Interrupt lingclaude (not implemented yet)."""
        # lingclaude doesn't support interrupt via API yet
        return False

    async def fetch_history(
        self,
        identity: str,
        thread_id: str,
        limit: int = 100,
    ) -> list[dict]:
        """Fetch history from lingclaude sessions."""
        # lingclaude doesn't expose history API yet
        return []

    @classmethod
    def supports_streaming(cls) -> bool:
        """lingclaude doesn't support streaming yet."""
        return False

    @classmethod
    def supports_interrupt(cls) -> bool:
        """lingclaude doesn't support interrupt yet."""
        return False

    async def close(self):
        """Clean up resources."""
        await self._client.aclose()


# Singleton instance
_instance: Optional[lingclaudeAdapter] = None


def get_lingclaude_adapter() -> lingclaudeAdapter:
    """Get or create singleton instance."""
    global _instance
    if _instance is None:
        _instance = lingclaudeAdapter()
    return _instance
