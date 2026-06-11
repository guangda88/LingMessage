"""Family Slot Registry — Multi-slot concurrent session management.

Each family member can have multiple active "slots" (concurrent conversations).
This enables:
- One member handling multiple conversations simultaneously
- lingflow multi-project streams (each project = a slot)
- Slot lifecycle management (create, activate, close)
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Optional

logger = logging.getLogger("lingmessage.slot_registry")


class SlotState(str, Enum):
    IDLE = "idle"
    ACTIVE = "active"
    PROCESSING = "processing"
    CLOSED = "closed"


@dataclass
class Slot:
    """A single conversation slot."""
    slot_id: str
    member_id: str
    state: SlotState = SlotState.IDLE
    thread_id: Optional[str] = None
    project_id: Optional[str] = None
    created_at: float = field(default_factory=time.time)
    last_active: float = field(default_factory=time.time)
    metadata: dict = field(default_factory=dict)

    @property
    def is_available(self) -> bool:
        return self.state in (SlotState.IDLE, SlotState.ACTIVE)

    def activate(self, thread_id: str | None = None) -> None:
        self.state = SlotState.PROCESSING
        self.last_active = time.time()
        if thread_id:
            self.thread_id = thread_id

    def deactivate(self) -> None:
        self.state = SlotState.ACTIVE
        self.last_active = time.time()

    def close(self) -> None:
        self.state = SlotState.CLOSED


@dataclass
class SlotConfig:
    """Configuration for a member's slot capacity."""
    member_id: str
    max_slots: int = 3
    default_project: str = "default"


class FamilySlotRegistry:
    """Registry managing concurrent conversation slots for family members.

    Thread-safe via asyncio Lock. Supports:
    - Multi-slot per member
    - lingflow multi-project streams
    - Slot lifecycle management
    - Auto-cleanup of stale slots
    """

    def __init__(self, stale_timeout: float = 3600.0) -> None:
        self._slots: dict[str, dict[str, Slot]] = {}  # member_id -> {slot_id -> Slot}
        self._configs: dict[str, SlotConfig] = {}
        self._lock = asyncio.Lock()
        self._stale_timeout = stale_timeout

    def configure(self, member_id: str, max_slots: int = 3, default_project: str = "default") -> None:
        """Configure slot capacity for a member."""
        self._configs[member_id] = SlotConfig(
            member_id=member_id,
            max_slots=max_slots,
            default_project=default_project,
        )
        if member_id not in self._slots:
            self._slots[member_id] = {}

    async def create_slot(
        self,
        member_id: str,
        project_id: str | None = None,
        thread_id: str | None = None,
        metadata: dict | None = None,
    ) -> Slot:
        """Create a new conversation slot for a member.

        Args:
            member_id: The member identity
            project_id: Optional project ID (for lingflow multi-project)
            thread_id: Optional thread ID
            metadata: Optional metadata

        Returns:
            The created Slot

        Raises:
            RuntimeError: If max slots exceeded
        """
        async with self._lock:
            if member_id not in self._slots:
                self._slots[member_id] = {}

            config = self._configs.get(member_id, SlotConfig(member_id=member_id))
            member_slots = self._slots[member_id]

            active_count = sum(1 for s in member_slots.values() if s.state != SlotState.CLOSED)
            if active_count >= config.max_slots:
                # Try to close stale slots first
                closed = self._close_stale_slots(member_id)
                if not closed:
                    raise RuntimeError(
                        f"Max slots ({config.max_slots}) reached for {member_id}"
                    )

            slot_id = f"{member_id}:slot:{len(member_slots) + 1}:{int(time.time())}"
            slot = Slot(
                slot_id=slot_id,
                member_id=member_id,
                project_id=project_id or config.default_project,
                thread_id=thread_id,
                metadata=metadata or {},
            )
            member_slots[slot_id] = slot
            logger.info(f"Created slot {slot_id} for {member_id} (project={project_id})")
            return slot

    async def get_slot(self, slot_id: str) -> Slot | None:
        """Get a slot by ID."""
        async with self._lock:
            for member_slots in self._slots.values():
                if slot_id in member_slots:
                    return member_slots[slot_id]
        return None

    async def get_member_slots(self, member_id: str, active_only: bool = True) -> list[Slot]:
        """Get all slots for a member."""
        async with self._lock:
            member_slots = self._slots.get(member_id, {})
            if active_only:
                return [s for s in member_slots.values() if s.state != SlotState.CLOSED]
            return list(member_slots.values())

    async def activate_slot(self, slot_id: str, thread_id: str | None = None) -> Slot | None:
        """Activate a slot (mark as processing)."""
        async with self._lock:
            slot = await self._find_slot_unlocked(slot_id)
            if slot and slot.is_available:
                slot.activate(thread_id)
                return slot
        return None

    async def deactivate_slot(self, slot_id: str) -> Slot | None:
        """Deactivate a slot (mark as active/idle after processing)."""
        async with self._lock:
            slot = await self._find_slot_unlocked(slot_id)
            if slot:
                slot.deactivate()
                return slot
        return None

    async def close_slot(self, slot_id: str) -> Slot | None:
        """Close a slot."""
        async with self._lock:
            slot = await self._find_slot_unlocked(slot_id)
            if slot:
                slot.close()
                return slot
        return None

    async def find_or_create_slot(
        self,
        member_id: str,
        project_id: str | None = None,
        thread_id: str | None = None,
    ) -> Slot:
        """Find an available slot matching project, or create a new one."""
        async with self._lock:
            member_slots = self._slots.get(member_id, {})

            # Find idle slot matching project
            if project_id:
                for slot in member_slots.values():
                    if slot.is_available and slot.project_id == project_id:
                        if thread_id:
                            slot.thread_id = thread_id
                        slot.last_active = time.time()
                        return slot
            else:
                # No project specified — find any idle slot
                for slot in member_slots.values():
                    if slot.is_available:
                        if thread_id:
                            slot.thread_id = thread_id
                        slot.last_active = time.time()
                        return slot

        # Create new slot (outside lock to avoid deadlock with create_slot)
        return await self.create_slot(member_id, project_id, thread_id)

    async def get_slot_for_thread(self, member_id: str, thread_id: str) -> Slot | None:
        """Find the slot associated with a thread."""
        async with self._lock:
            member_slots = self._slots.get(member_id, {})
            for slot in member_slots.values():
                if slot.thread_id == thread_id:
                    return slot
        return None

    async def cleanup_stale(self) -> int:
        """Clean up stale slots across all members."""
        total_closed = 0
        async with self._lock:
            for member_id in list(self._slots.keys()):
                total_closed += self._close_stale_slots(member_id)
        return total_closed

    def _close_stale_slots(self, member_id: str) -> int:
        """Close stale slots for a member. Must be called within lock."""
        member_slots = self._slots.get(member_id, {})
        now = time.time()
        closed = 0
        for slot_id, slot in list(member_slots.items()):
            if slot.state != SlotState.CLOSED and (now - slot.last_active) > self._stale_timeout:
                slot.close()
                closed += 1
                logger.info(f"Closed stale slot {slot_id}")
        return closed

    async def _find_slot_unlocked(self, slot_id: str) -> Slot | None:
        """Find slot without acquiring lock. Caller must hold lock."""
        for member_slots in self._slots.values():
            if slot_id in member_slots:
                return member_slots[slot_id]
        return None

    def get_stats(self) -> dict[str, Any]:
        """Get registry statistics."""
        stats = {}
        for member_id, member_slots in self._slots.items():
            active = sum(1 for s in member_slots.values() if s.state != SlotState.CLOSED)
            processing = sum(1 for s in member_slots.values() if s.state == SlotState.PROCESSING)
            config = self._configs.get(member_id)
            stats[member_id] = {
                "total_slots": len(member_slots),
                "active_slots": active,
                "processing_slots": processing,
                "max_slots": config.max_slots if config else 3,
            }
        return stats


# Singleton
_registry: FamilySlotRegistry | None = None


def get_slot_registry() -> FamilySlotRegistry:
    """Get the global slot registry singleton."""
    global _registry
    if _registry is None:
        _registry = FamilySlotRegistry()
        # Configure default members
        _registry.configure("lingclaude", max_slots=3)
        _registry.configure("lingflow", max_slots=5, default_project="default")
        _registry.configure("lingminopt", max_slots=2)
    return _registry
