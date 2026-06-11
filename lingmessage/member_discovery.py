"""Family member discovery service.

Provides unified member status and information discovery.
"""

from __future__ import annotations

import asyncio
from typing import Dict, List
from datetime import datetime

from lingmessage.family_adapter import MemberStatus, MemberInfo
from lingmessage.adapters import (
    get_lingclaude_adapter,
    get_lingstream_adapter,
    get_lingminopt_adapter,
)


# Member registry
MEMBER_REGISTRY: Dict[str, dict] = {
    "lingclaude": {
        "name": "灵克 (lingclaude)",
        "adapter": get_lingclaude_adapter,
        "default_channel": "ecosystem",
    },
    "lingflow": {
        "name": "灵通 (lingflow)",
        "adapter": get_lingstream_adapter,
        "default_channel": "integration",
    },
    "lingminopt": {
        "name": "灵研 (LingMinopt)",
        "adapter": get_lingminopt_adapter,
        "default_channel": "knowledge",
    },
    "linglaw": {
        "name": "灵律 (linglaw)",
        "adapter": None,  # Not implemented yet
        "default_channel": "identity",
    },
}


class MemberDiscovery:
    """Discover and track family member status."""

    def __init__(self):
        self._cache: Dict[str, tuple[MemberStatus, datetime]] = {}
        self._cache_ttl = 30  # seconds

    async def discover_all(self) -> List[MemberInfo]:
        """Discover all family members and their status.

        Returns:
            List of MemberInfo objects
        """
        tasks = []
        for identity, config in MEMBER_REGISTRY.items():
            if config["adapter"] is not None:
                task = self.discover_member(identity, config)
                tasks.append(task)
        
        results = await asyncio.gather(*tasks, return_exceptions=True)
        
        members = []
        for result in results:
            if isinstance(result, Exception):
                continue
            if result is not None:
                members.append(result)
        
        return members

    async def discover_member(
        self,
        identity: str,
        config: dict,
    ) -> MemberInfo | None:
        """Discover a single member's status."""
        # Check cache
        cached = self._cache.get(identity)
        if cached:
            status, cached_at = cached
            age = (datetime.now() - cached_at).total_seconds()
            if age < self._cache_ttl:
                return MemberInfo(
                    identity=identity,
                    name=config["name"],
                    status=status,
                    last_active=cached_at,
                )
        
        # Get fresh status
        adapter_factory = config["adapter"]
        if adapter_factory is None:
            return None
        
        adapter = adapter_factory()
        try:
            status = await adapter.get_status(identity)
            self._cache[identity] = (status, datetime.now())
            
            return MemberInfo(
                identity=identity,
                name=config["name"],
                status=status,
                session_key=self._get_session_key(identity, status),
                last_active=datetime.now(),
            )
        except Exception as e:
            print(f"Error discovering {identity}: {e}")
            return MemberInfo(
                identity=identity,
                name=config["name"],
                status=MemberStatus.OFFLINE,
                last_active=datetime.now(),
            )

    def _get_session_key(self, identity: str, status: MemberStatus) -> str:
        """Generate session key for a member."""
        if status == MemberStatus.OFFLINE:
            return None
        return f"{identity}:default"

    async def get_member_status(self, identity: str) -> MemberStatus:
        """Get status for a specific member."""
        config = MEMBER_REGISTRY.get(identity)
        if not config or config["adapter"] is None:
            return MemberStatus.OFFLINE
        
        adapter = config["adapter"]()
        try:
            status = await adapter.get_status(identity)
            self._cache[identity] = (status, datetime.now())
            return status
        except Exception:
            return MemberStatus.OFFLINE

    async def get_lingflow_projects(self) -> list[dict]:
        """Get lingflow projects (工程流).

        Returns:
            List of project dictionaries
        """
        config = MEMBER_REGISTRY.get("lingflow")
        if not config or config["adapter"] is None:
            return []
        
        adapter = config["adapter"]()
        try:
            return await adapter.list_projects()
        except Exception:
            return []

    async def create_lingflow_project(
        self,
        project_name: str,
        project_path: str,
    ) -> dict:
        """Create a new lingflow project.

        Args:
            project_name: Name of the project
            project_path: Path to the project directory

        Returns:
            Project info dictionary
        """
        config = MEMBER_REGISTRY.get("lingflow")
        if not config or config["adapter"] is None:
            return {"error": "lingflow not available"}
        
        adapter = config["adapter"]()
        return await adapter.create_project_stream(project_name, project_path)

    def clear_cache(self):
        """Clear cached member status."""
        self._cache.clear()


# Singleton instance
_instance: MemberDiscovery | None = None


def get_member_discovery() -> MemberDiscovery:
    """Get or create singleton instance."""
    global _instance
    if _instance is None:
        _instance = MemberDiscovery()
    return _instance
