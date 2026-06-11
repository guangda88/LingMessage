"""LingMinopt adapter implementation.

Communicates with LingMinopt (灵研) via CLI.
"""

from __future__ import annotations

import asyncio
from typing import AsyncIterator, Optional
from pathlib import Path

from lingmessage.family_adapter import (
    FamilyAdapter,
    MemberStatus,
    DeltaEvent,
)


class LingMinoptAdapter(FamilyAdapter):
    """Adapter for LingMinopt (灵研) member.

    Uses CLI interface.
    """

    def __init__(self):
        self.cli_path = Path.home() / "LingMinopt" / "cli" / "lingminopt.sh"

    async def send_message(
        self,
        identity: str,
        message: str,
        thread_id: Optional[str] = None,
        timeout_seconds: int = 120,
    ) -> str:
        """Send message to LingMinopt and return response."""
        try:
            cmd = [str(self.cli_path), "ask", "--message", message]
            
            if thread_id:
                cmd.extend(["--thread", thread_id])
            
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await asyncio.wait_for(
                proc.communicate(),
                timeout=timeout_seconds,
            )
            
            if proc.returncode != 0:
                error_msg = stderr.decode("utf-8", errors="replace")
                raise RuntimeError(f"LingMinopt CLI error: {error_msg}")
            
            return stdout.decode("utf-8", errors="replace").strip()
            
        except asyncio.TimeoutError:
            raise TimeoutError(f"LingMinopt timeout after {timeout_seconds}s")
        except FileNotFoundError:
            raise RuntimeError(f"LingMinopt CLI not found at {self.cli_path}")
        except Exception as e:
            raise RuntimeError(f"LingMinopt error: {e}")

    async def stream_response(
        self,
        identity: str,
        message: str,
        thread_id: Optional[str] = None,
        timeout_seconds: int = 120,
    ) -> AsyncIterator[DeltaEvent]:
        """Stream response from LingMinopt (non-streaming for now)."""
        # LingMinopt doesn't support streaming yet, fall back to send_message
        full_response = await self.send_message(identity, message, thread_id, timeout_seconds)
        
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
            await asyncio.sleep(0.05)
        
        # Final event
        yield DeltaEvent(
            identity=identity,
            thread_id=thread_id or "",
            text="",
            is_final=True
        )

    async def get_status(self, identity: str) -> MemberStatus:
        """Check LingMinopt status."""
        # Check if process is running
        try:
            proc = await asyncio.create_subprocess_exec(
                "pgrep",
                "-f",
                "python.*lingminopt",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.DEVNULL,
            )
            await proc.communicate()
            is_running = proc.returncode == 0
        except Exception:
            is_running = False

        return MemberStatus.ONLINE if is_running else MemberStatus.OFFLINE

    async def interrupt(self, identity: str) -> bool:
        """Interrupt LingMinopt processing."""
        try:
            proc = await asyncio.create_subprocess_exec(
                "pkill",
                "-INT",
                "-f",
                "lingminopt",
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL,
            )
            await proc.wait()
            return proc.returncode == 0
        except Exception:
            return False

    async def fetch_history(
        self,
        identity: str,
        thread_id: str,
        limit: int = 100,
    ) -> list[dict]:
        """LingMinopt doesn't expose history yet."""
        return []

    @classmethod
    def supports_streaming(cls) -> bool:
        """LingMinopt doesn't support streaming yet."""
        return False

    @classmethod
    def supports_interrupt(cls) -> bool:
        """LingMinopt supports interrupt."""
        return True


# Singleton instance
_instance: Optional[LingMinoptAdapter] = None


def get_lingminopt_adapter() -> LingMinoptAdapter:
    """Get or create singleton instance."""
    global _instance
    if _instance is None:
        _instance = LingMinoptAdapter()
    return _instance
