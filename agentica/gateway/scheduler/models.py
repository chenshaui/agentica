"""Data models for scheduled jobs."""
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any
import uuid

from .types import (
    Schedule,
    Payload,
    JobStatus,
    RunStatus,
    AtSchedule,
    AgentTurnPayload,
    TaskChainPayload,
    SessionTarget,
    schedule_from_dict,
    payload_from_dict,
)


@dataclass
class JobState:
    """Runtime state of a scheduled job."""
    next_run_at_ms: int | None = None
    last_run_at_ms: int | None = None
    last_status: RunStatus | None = None
    run_count: int = 0
    failure_count: int = 0
    consecutive_failures: int = 0
    last_error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "next_run_at_ms": self.next_run_at_ms,
            "last_run_at_ms": self.last_run_at_ms,
            "last_status": self.last_status.value if self.last_status else None,
            "run_count": self.run_count,
            "failure_count": self.failure_count,
            "consecutive_failures": self.consecutive_failures,
            "last_error": self.last_error,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "JobState":
        return cls(
            next_run_at_ms=data.get("next_run_at_ms"),
            last_run_at_ms=data.get("last_run_at_ms"),
            last_status=RunStatus(data["last_status"]) if data.get("last_status") else None,
            run_count=data.get("run_count", 0),
            failure_count=data.get("failure_count", 0),
            consecutive_failures=data.get("consecutive_failures", 0),
            last_error=data.get("last_error"),
        )


@dataclass
class ScheduledJob:
    """Represents a scheduled job.

    This is the new unified model that replaces ScheduledTask.
    """
    # Identity
    id: str = field(default_factory=lambda: str(uuid.uuid4()))
    user_id: str = ""
    agent_id: str = "main"

    # Definition
    name: str = ""
    description: str = ""
    enabled: bool = True

    # Schedule configuration
    schedule: Schedule = field(default_factory=lambda: AtSchedule())

    # Payload configuration
    payload: Payload = field(default_factory=lambda: AgentTurnPayload())

    # Session target (main/isolated)
    target: SessionTarget = field(default_factory=SessionTarget)

    # Execution settings
    max_retries: int = 3
    retry_delay_ms: int = 60000  # 1 minute

    # Task chain: jobs to trigger on completion
    on_complete: list[TaskChainPayload] = field(default_factory=list)

    # Runtime state
    state: JobState = field(default_factory=JobState)
    status: JobStatus = JobStatus.PENDING

    # Timestamps
    created_at_ms: int = field(default_factory=lambda: int(datetime.now().timestamp() * 1000))
    updated_at_ms: int = field(default_factory=lambda: int(datetime.now().timestamp() * 1000))

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for JSON serialization."""
        return {
            "id": self.id,
            "user_id": self.user_id,
            "agent_id": self.agent_id,
            "name": self.name,
            "description": self.description,
            "enabled": self.enabled,
            "schedule": self.schedule.to_dict(),
            "payload": self.payload.to_dict(),
            "target": self.target.to_dict(),
            "max_retries": self.max_retries,
            "retry_delay_ms": self.retry_delay_ms,
            "on_complete": [p.to_dict() for p in self.on_complete],
            "state": self.state.to_dict(),
            "status": self.status.value,
            "created_at_ms": self.created_at_ms,
            "updated_at_ms": self.updated_at_ms,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "ScheduledJob":
        """Create from dictionary."""
        job = cls(
            id=data.get("id", str(uuid.uuid4())),
            user_id=data.get("user_id", ""),
            agent_id=data.get("agent_id", "main"),
            name=data.get("name", ""),
            description=data.get("description", ""),
            enabled=data.get("enabled", True),
            max_retries=data.get("max_retries", 3),
            retry_delay_ms=data.get("retry_delay_ms", 60000),
            status=JobStatus(data.get("status", "pending")),
            created_at_ms=data.get("created_at_ms", int(datetime.now().timestamp() * 1000)),
            updated_at_ms=data.get("updated_at_ms", int(datetime.now().timestamp() * 1000)),
        )

        # Parse schedule
        if data.get("schedule"):
            job.schedule = schedule_from_dict(data["schedule"])

        # Parse payload
        if data.get("payload"):
            job.payload = payload_from_dict(data["payload"])

        # Parse target
        if data.get("target"):
            job.target = SessionTarget.from_dict(data["target"])

        # Parse state
        if data.get("state"):
            job.state = JobState.from_dict(data["state"])

        # Parse on_complete chain
        if data.get("on_complete"):
            job.on_complete = [
                TaskChainPayload.from_dict(p) for p in data["on_complete"]
            ]

        return job


@dataclass
class JobCreate:
    """Request to create a new job."""
    user_id: str
    name: str = ""
    description: str = ""
    schedule: Schedule = field(default_factory=lambda: AtSchedule())
    payload: Payload = field(default_factory=lambda: AgentTurnPayload())
    target: SessionTarget = field(default_factory=SessionTarget)
    agent_id: str = "main"
    max_retries: int = 3
    enabled: bool = True


@dataclass
class JobPatch:
    """Request to update an existing job."""
    name: str | None = None
    description: str | None = None
    schedule: Schedule | None = None
    payload: Payload | None = None
    enabled: bool | None = None
    max_retries: int | None = None

    def apply(self, job: ScheduledJob) -> None:
        """Apply patch to a job."""
        if self.name is not None:
            job.name = self.name
        if self.description is not None:
            job.description = self.description
        if self.schedule is not None:
            job.schedule = self.schedule
        if self.payload is not None:
            job.payload = self.payload
        if self.enabled is not None:
            job.enabled = self.enabled
        if self.max_retries is not None:
            job.max_retries = self.max_retries
        job.updated_at_ms = int(datetime.now().timestamp() * 1000)
