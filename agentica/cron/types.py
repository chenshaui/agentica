# -*- coding: utf-8 -*-
"""
@author: XuMing(xuming624@qq.com)
@description: Core type definitions for the cron scheduling system.

Schedule types (at/every/cron), job status, and run status enums.
"""
from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import Any, Literal


# ============== Schedule Types ==============

class ScheduleKind(str, Enum):
    """Kind of schedule."""
    AT = "at"
    EVERY = "every"
    CRON = "cron"


@dataclass
class AtSchedule:
    """One-time schedule at a specific timestamp."""
    kind: Literal["at"] = "at"
    at_ms: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {"kind": self.kind, "at_ms": self.at_ms}

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "AtSchedule":
        return cls(at_ms=data.get("at_ms", 0))

    @classmethod
    def from_datetime(cls, dt: datetime) -> "AtSchedule":
        return cls(at_ms=int(dt.timestamp() * 1000))


@dataclass
class EverySchedule:
    """Interval-based schedule."""
    kind: Literal["every"] = "every"
    interval_ms: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {"kind": self.kind, "interval_ms": self.interval_ms}

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "EverySchedule":
        return cls(interval_ms=data.get("interval_ms", 0))

    @classmethod
    def from_seconds(cls, seconds: int) -> "EverySchedule":
        return cls(interval_ms=seconds * 1000)


@dataclass
class CronSchedule:
    """Cron expression schedule.

    Supports both 5-part (minute precision) and 6-part (second precision) formats:
    - 5-part: "min hour day month weekday" (e.g., "30 7 * * *" = every day at 7:30)
    - 6-part: "sec min hour day month weekday" (e.g., "0 30 7 * * *" = every day at 7:30:00)
    """
    kind: Literal["cron"] = "cron"
    expression: str = ""
    timezone: str = "Asia/Shanghai"

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "expression": self.expression,
            "timezone": self.timezone,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "CronSchedule":
        return cls(
            expression=data.get("expression", ""),
            timezone=data.get("timezone", "Asia/Shanghai"),
        )

    @classmethod
    def at_time(cls, hour: int, minute: int = 0, second: int = 0,
                weekday: str = "*", timezone: str = "Asia/Shanghai") -> "CronSchedule":
        """Create a daily schedule at specific time.

        Examples:
            CronSchedule.at_time(7, 30)        # every day at 7:30
            CronSchedule.at_time(9, 0, 0, "1-5")  # weekdays 9:00
        """
        if second == 0:
            expr = f"{minute} {hour} * * {weekday}"
        else:
            expr = f"{second} {minute} {hour} * * {weekday}"
        return cls(expression=expr, timezone=timezone)


# Union type for all schedule types
Schedule = AtSchedule | EverySchedule | CronSchedule


def schedule_from_dict(data: dict[str, Any]) -> Schedule:
    """Create a Schedule from a dictionary."""
    kind = data.get("kind", "at")
    if kind == "at":
        return AtSchedule.from_dict(data)
    elif kind == "every":
        return EverySchedule.from_dict(data)
    elif kind == "cron":
        return CronSchedule.from_dict(data)
    raise ValueError(f"Unknown schedule kind: {kind}")


# ============== Job Status ==============

class JobStatus(str, Enum):
    """Status of a scheduled job."""
    ACTIVE = "active"
    PAUSED = "paused"
    COMPLETED = "completed"


class RunStatus(str, Enum):
    """Status of a single job run."""
    OK = "ok"
    FAILED = "failed"
    TIMEOUT = "timeout"
