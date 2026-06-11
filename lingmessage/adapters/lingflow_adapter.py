"""lingflow adapter implementation with multi-project stream support.

Communicates with lingflow via CLI/API and manages multiple project streams.
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


class LingStreamAdapter(FamilyAdapter):
    """Adapter for lingflow (灵通) member with multi-project stream support.

    Supports multiple concurrent project streams (工程流).
    """

    def __init__(self):
        self.cli_path = Path.home() / "lingflow" / "cli" / "lingflow.sh"
        self.projects = {}  # project_id -> ProjectInfo
        self.active_streams = {}  # project_id -> subprocess

    async def send_message(
        self,
        identity: str,
        message: str,
        thread_id: Optional[str] = None,
        timeout_seconds: int = 120,
    ) -> str:
        """Send message to lingflow and return response.

        If thread_id is provided, it's treated as a project stream ID.
        """
        # Extract project ID from thread_id if available
        project_id = self._extract_project_id(thread_id) if thread_id else "default"
        
        try:
            # Use CLI to send message
            cmd = [
                str(self.cli_path),
                "ask",
                "--project", project_id,
                "--message", message,
            ]
            
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
                raise RuntimeError(f"lingflow CLI error: {error_msg}")
            
            return stdout.decode("utf-8", errors="replace").strip()
            
        except asyncio.TimeoutError:
            raise TimeoutError(f"lingflow timeout after {timeout_seconds}s")
        except FileNotFoundError:
            raise RuntimeError(f"lingflow CLI not found at {self.cli_path}")
        except Exception as e:
            raise RuntimeError(f"lingflow error: {e}")

    async def stream_response(
        self,
        identity: str,
        message: str,
        thread_id: Optional[str] = None,
        timeout_seconds: int = 120,
    ) -> AsyncIterator[DeltaEvent]:
        """Stream response from lingflow with project stream context.

        If thread_id contains project stream ID, the response is streamed
        with project context.
        """
        project_id = self._extract_project_id(thread_id) if thread_id else "default"
        
        try:
            # Start CLI in stream mode
            cmd = [
                str(self.cli_path),
                "stream",
                "--project", project_id,
                "--message", message,
            ]
            
            if thread_id:
                cmd.extend(["--thread", thread_id])
            
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            
            # Stream stdout line by line
            try:
                while True:
                    line = await asyncio.wait_for(
                        proc.stdout.readline(),
                        timeout=timeout_seconds,
                    )
                    if not line:
                        break
                    
                    text = line.decode("utf-8", errors="replace").strip()
                    if text:
                        # Check for special markers
                        if text.startswith("STREAM_START"):
                            continue
                        elif text.startswith("STREAM_END"):
                            break
                        else:
                            yield DeltaEvent(
                                identity=identity,
                                thread_id=thread_id or "",
                                text=text,
                                is_final=False
                            )

                # Final event
                yield DeltaEvent(
                    identity=identity,
                    thread_id=thread_id or "",
                    text="",
                    is_final=True
                )

            except asyncio.TimeoutError:
                # Ensure process is terminated
                try:
                    proc.terminate()
                    await proc.wait()
                except Exception:
                    pass
                raise TimeoutError(f"lingflow stream timeout after {timeout_seconds}s")

            except FileNotFoundError:
                raise RuntimeError(f"lingflow CLI not found at {self.cli_path}")

            except Exception as e:
                raise RuntimeError(f"lingflow stream error: {e}")

        except FileNotFoundError:
            raise RuntimeError(f"lingflow CLI not found at {self.cli_path}")

    async def get_status(self, identity: str) -> MemberStatus:
        """Check lingflow status."""
        # Check if process is running
        try:
            proc = await asyncio.create_subprocess_exec(
                "pgrep",
                "-f",
                "python.*lingflow",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.DEVNULL,
            )
            await proc.communicate()
            is_running = proc.returncode == 0
        except Exception:
            is_running = False

        if not is_running:
            return MemberStatus.OFFLINE

        # Check if CLI is responsive
        try:
            proc = await asyncio.create_subprocess_exec(
                str(self.cli_path),
                "status",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.DEVNULL,
            )
            stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=5.0)
            status_output = stdout.decode("utf-8", errors="replace").strip()
            
            if "processing" in status_output.lower():
                return MemberStatus.PROCESSING
            elif "idle" in status_output.lower():
                return MemberStatus.IDLE
            else:
                return MemberStatus.ONLINE
                
        except Exception:
            pass

        return MemberStatus.ONLINE

    async def interrupt(self, identity: str) -> bool:
        """Interrupt lingflow processing."""
        # Send SIGINT to lingflow process
        try:
            proc = await asyncio.create_subprocess_exec(
                "pkill",
                "-INT",
                "-f",
                "lingflow",
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL,
            )
            await proc.wait()
            return proc.returncode == 0
        except Exception:
            return False

    async def list_projects(self) -> list[dict]:
        """List all available projects (工程流).

        Returns:
            List of project dictionaries with id, name, status
        """
        try:
            proc = await asyncio.create_subprocess_exec(
                str(self.cli_path),
                "list-projects",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=10.0)
            
            if proc.returncode != 0:
                return []
            
            output = stdout.decode("utf-8", errors="replace").strip()
            # Parse JSON output
            import json
            return json.loads(output)
            
        except Exception:
            return []

    async def create_project_stream(self, project_name: str, project_path: str) -> dict:
        """Create a new project stream.

        Args:
            project_name: Name of the project
            project_path: Path to the project directory

        Returns:
            Project info dictionary
        """
        try:
            proc = await asyncio.create_subprocess_exec(
                str(self.cli_path),
                "create-project",
                "--name", project_name,
                "--path", project_path,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=10.0)
            
            if proc.returncode != 0:
                return {}
            
            import json
            return json.loads(stdout.decode("utf-8", errors="replace"))
            
        except Exception as e:
            return {"error": str(e)}

    def _extract_project_id(self, thread_id: Optional[str]) -> str:
        """Extract project ID from thread ID.

        Thread ID format: "project:{project_id}" or "lingflow:{project_id}"
        """
        if not thread_id:
            return "default"
        
        if thread_id.startswith("project:"):
            return thread_id[8:]  # Remove "project:" prefix
        elif thread_id.startswith("lingflow:"):
            return thread_id[10:]  # Remove "lingflow:" prefix
        
        return "default"

    async def fetch_history(
        self,
        identity: str,
        thread_id: str,
        limit: int = 100,
    ) -> list[dict]:
        """Fetch history for a project stream."""
        project_id = self._extract_project_id(thread_id)
        
        try:
            proc = await asyncio.create_subprocess_exec(
                str(self.cli_path),
                "history",
                "--project", project_id,
                "--limit", str(limit),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=10.0)
            
            if proc.returncode != 0:
                return []
            
            import json
            return json.loads(stdout.decode("utf-8", errors="replace"))
            
        except Exception:
            return []

    @classmethod
    def supports_streaming(cls) -> bool:
        """LingStreamAdapter supports streaming."""
        return True

    @classmethod
    def supports_interrupt(cls) -> bool:
        """lingflow supports interrupt."""
        return True


# Singleton instance
_instance: Optional[LingStreamAdapter] = None


def get_lingstream_adapter() -> LingStreamAdapter:
    """Get or create singleton instance."""
    global _instance
    if _instance is None:
        _instance = LingStreamAdapter()
    return _instance
