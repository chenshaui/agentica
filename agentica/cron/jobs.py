# -*- coding: utf-8 -*-
"""
@author: XuMing(xuming624@qq.com)
@description: Cron job storage and management.

Jobs are stored in JSON file at ~/.agentica/cron/jobs.json.
Execution output is saved to ~/.agentica/cron/output/{job_id}/{timestamp}.md.
"""
import json
import logging
import re
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from agentica.config import AGENTICA_CRON_DIR
from agentica.cron.types import (
    Schedule,
    AtSchedule,
    EverySchedule,
    CronSchedule,
    JobStatus,
    RunStatus,
    schedule_from_dict,
)

logger = logging.getLogger(__name__)

# ============== Paths ==============

CRON_DIR = Path(AGENTICA_CRON_DIR)
JOBS_FILE = CRON_DIR / "jobs.json"
OUTPUT_DIR = CRON_DIR / "output"


def _ensure_dirs():
    """Create cron directories if they don't exist."""
    CRON_DIR.mkdir(parents=True, exist_ok=True)
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)


# ============== CronJob Model ==============

@dataclass
class CronJob:
    """A scheduled cron job."""
    id: str
    name: str
    prompt: str
    schedule: Schedule
    user_id: str = "default"
    status: JobStatus = JobStatus.ACTIVE
    timezone: str = "Asia/Shanghai"
    deliver: str = "local"
    created_at_ms: int = 0
    next_run_at_ms: int = 0
    last_run_at_ms: int = 0
    last_status: str = ""
    run_count: int = 0
    enabled: bool = True

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "prompt": self.prompt,
            "schedule": self.schedule.to_dict(),
            "user_id": self.user_id,
            "status": self.status.value,
            "timezone": self.timezone,
            "deliver": self.deliver,
            "created_at_ms": self.created_at_ms,
            "next_run_at_ms": self.next_run_at_ms,
            "last_run_at_ms": self.last_run_at_ms,
            "last_status": self.last_status,
            "run_count": self.run_count,
            "enabled": self.enabled,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "CronJob":
        schedule = schedule_from_dict(data.get("schedule", {"kind": "at"}))
        status_str = data.get("status", "active")
        return cls(
            id=data.get("id", ""),
            name=data.get("name", ""),
            prompt=data.get("prompt", ""),
            schedule=schedule,
            user_id=data.get("user_id", "default"),
            status=JobStatus(status_str),
            timezone=data.get("timezone", "Asia/Shanghai"),
            deliver=data.get("deliver", "local"),
            created_at_ms=data.get("created_at_ms", 0),
            next_run_at_ms=data.get("next_run_at_ms", 0),
            last_run_at_ms=data.get("last_run_at_ms", 0),
            last_status=data.get("last_status", ""),
            run_count=data.get("run_count", 0),
            enabled=data.get("enabled", True),
        )


# ============== Schedule Calculation ==============

def now_ms() -> int:
    """Get current timestamp in milliseconds."""
    return int(time.time() * 1000)


def compute_next_run_at_ms(
    schedule: Schedule,
    current_ms: int | None = None,
    last_run_at_ms: int | None = None,
) -> int | None:
    """Compute the next run time in milliseconds."""
    if current_ms is None:
        current_ms = now_ms()

    if isinstance(schedule, AtSchedule):
        if schedule.at_ms > current_ms:
            return schedule.at_ms
        return None

    if isinstance(schedule, EverySchedule):
        if schedule.interval_ms <= 0:
            return None
        if last_run_at_ms is None:
            return current_ms + schedule.interval_ms
        next_run = last_run_at_ms + schedule.interval_ms
        while next_run <= current_ms:
            next_run += schedule.interval_ms
        return next_run

    if isinstance(schedule, CronSchedule):
        return _compute_cron_next(schedule, current_ms)

    return None


def _compute_cron_next(schedule: CronSchedule, current_ms: int) -> int | None:
    """Compute next run for cron schedule using croniter with fallback."""
    try:
        from croniter import croniter
    except ImportError:
        return _compute_cron_fallback(schedule, current_ms)

    try:
        tz = ZoneInfo(schedule.timezone)
        current_dt = datetime.fromtimestamp(current_ms / 1000, tz=tz)
        parts = schedule.expression.split()
        if len(parts) == 6:
            cron = croniter(schedule.expression, current_dt, second_at_beginning=True)
        else:
            cron = croniter(schedule.expression, current_dt)
        next_dt = cron.get_next(datetime)
        return int(next_dt.timestamp() * 1000)
    except Exception:
        return _compute_cron_fallback(schedule, current_ms)


def _compute_cron_fallback(schedule: CronSchedule, current_ms: int) -> int | None:
    """Fallback cron calculation for simple patterns when croniter is unavailable."""
    parts = schedule.expression.split()
    if len(parts) == 5:
        minute, hour, day, month, weekday = parts
    elif len(parts) == 6:
        _second, minute, hour, day, month, weekday = parts  # noqa: F841
    else:
        return None

    try:
        tz = ZoneInfo(schedule.timezone)
        current_dt = datetime.fromtimestamp(current_ms / 1000, tz=tz)

        # Handle simple daily patterns: "M H * * *"
        if day == "*" and month == "*" and weekday == "*":
            if minute.isdigit() and hour.isdigit():
                target_minute = int(minute)
                target_hour = int(hour)
                next_dt = current_dt.replace(
                    hour=target_hour, minute=target_minute, second=0, microsecond=0,
                )
                if next_dt <= current_dt:
                    next_dt += timedelta(days=1)
                return int(next_dt.timestamp() * 1000)

        # Handle interval patterns: "*/N * * * *"
        if minute.startswith("*/") and hour == "*" and day == "*":
            interval_minutes = int(minute[2:])
            current_minute = current_dt.minute
            next_minute = ((current_minute // interval_minutes) + 1) * interval_minutes
            next_dt = current_dt.replace(second=0, microsecond=0)
            next_dt += timedelta(minutes=next_minute - current_minute)
            return int(next_dt.timestamp() * 1000)

    except Exception:
        pass

    return None


def validate_cron_expression(expression: str) -> bool:
    """Validate a cron expression."""
    parts = expression.split()
    if len(parts) not in (5, 6):
        return False

    try:
        from croniter import croniter
        if len(parts) == 6:
            croniter(expression, second_at_beginning=True)
        else:
            croniter(expression)
        return True
    except ImportError:
        pass
    except Exception:
        return False

    # Basic validation without croniter
    for part in parts:
        if part == "*":
            continue
        if part.startswith("*/"):
            try:
                int(part[2:])
                continue
            except ValueError:
                return False
        if "-" in part or "," in part:
            continue
        try:
            int(part)
        except ValueError:
            return False
    return True


def schedule_to_human(schedule: Schedule) -> str:
    """Convert schedule to human-readable description."""
    if isinstance(schedule, AtSchedule):
        if schedule.at_ms > 0:
            dt = datetime.fromtimestamp(schedule.at_ms / 1000)
            return f"Run once at {dt.strftime('%Y-%m-%d %H:%M:%S')}"
        return "No time set"

    if isinstance(schedule, EverySchedule):
        seconds = schedule.interval_ms // 1000
        if seconds < 60:
            return f"Every {seconds} seconds"
        if seconds < 3600:
            return f"Every {seconds // 60} minutes"
        if seconds < 86400:
            return f"Every {seconds // 3600} hours"
        return f"Every {seconds // 86400} days"

    if isinstance(schedule, CronSchedule):
        return _cron_to_human(schedule.expression)

    return "Unknown schedule"


def _cron_to_human(expression: str) -> str:
    """Convert cron expression to human-readable description."""
    parts = expression.split()
    if len(parts) not in (5, 6):
        return f"Cron: {expression}"

    if len(parts) == 6:
        second, minute, hour, day, month, weekday = parts
    else:
        second = "0"
        minute, hour, day, month, weekday = parts

    # Minute-level interval: "*/N * * * *"
    if minute.startswith("*/") and hour == "*" and day == "*" and month == "*" and weekday == "*":
        return f"Every {minute[2:]} minutes"

    # Hour-level interval
    if hour.startswith("*/") and minute == "0" and day == "*" and month == "*" and weekday == "*":
        return f"Every {hour[2:]} hours"

    # Common patterns
    if day == "*" and month == "*":
        time_str = ""
        if minute.isdigit() and hour.isdigit():
            time_str = f"{hour}:{minute.zfill(2)}"

        if weekday == "*":
            return f"Daily at {time_str}" if time_str else f"Cron: {expression}"
        weekday_names = {
            "0": "Sun", "1": "Mon", "2": "Tue", "3": "Wed",
            "4": "Thu", "5": "Fri", "6": "Sat", "7": "Sun",
            "1-5": "weekdays", "0,6": "weekends",
        }
        wd = weekday_names.get(weekday, f"day {weekday}")
        return f"Every {wd} at {time_str}" if time_str else f"Cron: {expression}"

    return f"Cron: {expression}"


# ============== Schedule Parsing ==============

_INTERVAL_PATTERN = re.compile(
    r"^(?:every\s+)?(\d+)\s*"
    r"(s|sec|secs|seconds?|m|min|mins|minutes?|h|hr|hrs|hours?|d|days?)$",
    re.IGNORECASE,
)


def parse_schedule(schedule_str: str) -> Schedule:
    """Parse a schedule string into a Schedule object.

    Supports:
    - Cron expression: "30 7 * * *"
    - Natural language interval: "30m", "every 2h", "5s"
    - ISO datetime (one-shot): "2024-01-15T09:30:00"
    """
    schedule_str = schedule_str.strip()

    # Try cron expression (5 or 6 parts, starts with a digit or *)
    parts = schedule_str.split()
    if len(parts) in (5, 6) and all(_is_cron_field(p) for p in parts):
        return CronSchedule(expression=schedule_str)

    # Try natural language interval
    m = _INTERVAL_PATTERN.match(schedule_str)
    if m:
        value = int(m.group(1))
        unit = m.group(2).lower()
        if unit.startswith("s"):
            seconds = value
        elif unit.startswith("m"):
            seconds = value * 60
        elif unit.startswith("h"):
            seconds = value * 3600
        elif unit.startswith("d"):
            seconds = value * 86400
        else:
            seconds = value * 60
        return EverySchedule.from_seconds(seconds)

    # Try ISO datetime (one-shot)
    try:
        dt = datetime.fromisoformat(schedule_str.replace("Z", "+00:00"))
        return AtSchedule.from_datetime(dt)
    except ValueError:
        pass

    raise ValueError(
        f"Cannot parse schedule: {schedule_str!r}. "
        "Use cron expression (e.g. '30 7 * * *'), "
        "interval (e.g. '30m', 'every 2h'), "
        "or ISO datetime (e.g. '2024-01-15T09:30:00')."
    )


def _is_cron_field(s: str) -> bool:
    """Check if a string looks like a cron field."""
    if s == "*":
        return True
    if s.startswith("*/"):
        return s[2:].isdigit()
    if "," in s:
        return all(p.strip().replace("-", "").isdigit() for p in s.split(","))
    if "-" in s:
        parts = s.split("-")
        return len(parts) == 2 and all(p.isdigit() for p in parts)
    return s.isdigit()


# ============== File Storage ==============

def _load_jobs() -> list[dict[str, Any]]:
    """Load jobs from JSON file."""
    if not JOBS_FILE.exists():
        return []
    try:
        data = json.loads(JOBS_FILE.read_text(encoding="utf-8"))
        return data if isinstance(data, list) else []
    except (json.JSONDecodeError, OSError) as e:
        logger.warning(f"Failed to load jobs file: {e}")
        return []


def _save_jobs(jobs: list[dict[str, Any]]) -> None:
    """Save jobs to JSON file with atomic write."""
    _ensure_dirs()
    tmp_path = JOBS_FILE.with_suffix(".tmp")
    try:
        tmp_path.write_text(
            json.dumps(jobs, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        tmp_path.replace(JOBS_FILE)
    except OSError as e:
        logger.error(f"Failed to save jobs file: {e}")
        if tmp_path.exists():
            tmp_path.unlink()
        raise


# ============== CRUD Operations ==============

def create_job(
    prompt: str,
    schedule: str,
    name: str | None = None,
    user_id: str = "default",
    deliver: str = "local",
    timezone: str = "Asia/Shanghai",
) -> CronJob:
    """Create a new cron job and persist it.

    Args:
        prompt: The prompt to run when the job fires.
        schedule: Parseable schedule string (cron, interval, or ISO datetime).
        name: Human-friendly name (auto-generated if omitted).
        user_id: Owner user ID.
        deliver: Delivery target (local, origin, telegram, etc.).
        timezone: Timezone for cron expressions.

    Returns:
        The created CronJob.
    """
    parsed = parse_schedule(schedule)

    if isinstance(parsed, CronSchedule) and timezone != "Asia/Shanghai":
        parsed = CronSchedule(
            expression=parsed.expression,
            timezone=timezone,
        )

    current = now_ms()
    job_id = uuid.uuid4().hex[:12]
    next_run = compute_next_run_at_ms(parsed, current)

    job = CronJob(
        id=job_id,
        name=name or prompt[:50],
        prompt=prompt,
        schedule=parsed,
        user_id=user_id,
        status=JobStatus.ACTIVE,
        timezone=timezone,
        deliver=deliver,
        created_at_ms=current,
        next_run_at_ms=next_run or 0,
    )

    jobs = _load_jobs()
    jobs.append(job.to_dict())
    _save_jobs(jobs)
    logger.info(f"Created cron job {job_id}: {job.name}")
    return job


def get_job(job_id: str) -> CronJob | None:
    """Get a job by ID."""
    for data in _load_jobs():
        if data.get("id") == job_id:
            return CronJob.from_dict(data)
    return None


def list_jobs(
    user_id: str | None = None,
    include_disabled: bool = False,
    limit: int = 100,
) -> list[CronJob]:
    """List jobs, optionally filtered by user and status."""
    jobs = []
    for data in _load_jobs():
        if user_id and data.get("user_id") != user_id:
            continue
        if not include_disabled and not data.get("enabled", True):
            continue
        jobs.append(CronJob.from_dict(data))
        if len(jobs) >= limit:
            break
    return jobs


def update_job(job_id: str, updates: dict[str, Any]) -> CronJob | None:
    """Update a job's fields."""
    jobs = _load_jobs()
    for i, data in enumerate(jobs):
        if data.get("id") == job_id:
            # Apply updates (skip schedule — handled separately)
            for key, value in updates.items():
                if key == "schedule" and isinstance(value, (AtSchedule, EverySchedule, CronSchedule)):
                    data["schedule"] = value.to_dict()
                else:
                    data[key] = value
            jobs[i] = data
            _save_jobs(jobs)
            return CronJob.from_dict(data)
    return None


def remove_job(job_id: str) -> bool:
    """Remove a job by ID."""
    jobs = _load_jobs()
    original_len = len(jobs)
    jobs = [j for j in jobs if j.get("id") != job_id]
    if len(jobs) < original_len:
        _save_jobs(jobs)
        logger.info(f"Removed cron job {job_id}")
        return True
    return False


def pause_job(job_id: str) -> CronJob | None:
    """Pause a job."""
    return update_job(job_id, {
        "status": JobStatus.PAUSED.value,
        "enabled": False,
    })


def resume_job(job_id: str) -> CronJob | None:
    """Resume a paused job."""
    job = get_job(job_id)
    if not job:
        return None
    next_run = compute_next_run_at_ms(job.schedule)
    return update_job(job_id, {
        "status": JobStatus.ACTIVE.value,
        "enabled": True,
        "next_run_at_ms": next_run or 0,
    })


# ============== Execution Helpers ==============

def get_due_jobs(current_ms: int | None = None) -> list[CronJob]:
    """Get all jobs that are due to run."""
    if current_ms is None:
        current_ms = now_ms()

    due = []
    for data in _load_jobs():
        if not data.get("enabled", True):
            continue
        if data.get("status") != JobStatus.ACTIVE.value:
            continue
        next_run = data.get("next_run_at_ms", 0)
        if 0 < next_run <= current_ms:
            due.append(CronJob.from_dict(data))
    return due


def mark_job_run(
    job_id: str,
    status: RunStatus,
    result: str = "",
    error: str = "",
) -> CronJob | None:
    """Mark a job as having been run, update next_run_at."""
    current = now_ms()
    job = get_job(job_id)
    if not job:
        return None

    # Compute next run
    next_run = compute_next_run_at_ms(job.schedule, current, current)

    updates: dict[str, Any] = {
        "last_run_at_ms": current,
        "last_status": status.value,
        "run_count": job.run_count + 1,
        "next_run_at_ms": next_run or 0,
    }

    # One-shot jobs: mark completed
    if isinstance(job.schedule, AtSchedule) or next_run is None:
        updates["status"] = JobStatus.COMPLETED.value
        updates["enabled"] = False

    # Save output
    _save_output(job_id, current, result or error)

    return update_job(job_id, updates)


def _save_output(job_id: str, timestamp_ms: int, content: str) -> None:
    """Save job execution output to file."""
    if not content:
        return
    try:
        output_dir = OUTPUT_DIR / job_id
        output_dir.mkdir(parents=True, exist_ok=True)
        dt = datetime.fromtimestamp(timestamp_ms / 1000)
        filename = dt.strftime("%Y%m%d_%H%M%S") + ".md"
        (output_dir / filename).write_text(content, encoding="utf-8")
    except OSError as e:
        logger.warning(f"Failed to save output for job {job_id}: {e}")
