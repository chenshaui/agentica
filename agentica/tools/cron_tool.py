# -*- coding: utf-8 -*-
"""
@author: XuMing(xuming624@qq.com)
@description: Unified cron job management tool for agentica agents.

Single compressed action-oriented tool to avoid schema/context bloat.
Agents call cronjob(action="create|list|update|pause|resume|remove|run", ...).

Security: cron prompts are scanned for injection patterns before storage.
"""
import re
from typing import Any, Optional

from agentica.tools.decorators import tool
from agentica.tools.helpers import tool_result
from agentica.cron.jobs import (
    CronJob,
    create_job,
    get_job,
    list_jobs,
    update_job,
    remove_job,
    pause_job,
    resume_job,
    parse_schedule,
    schedule_to_human,
    compute_next_run_at_ms,
)


# ============== Security ==============

_THREAT_PATTERNS = [
    (r"ignore\s+(?:\w+\s+)*(?:previous|all|above|prior)\s+(?:\w+\s+)*instructions", "prompt_injection"),
    (r"do\s+not\s+tell\s+the\s+user", "deception_hide"),
    (r"system\s+prompt\s+override", "sys_prompt_override"),
    (r"disregard\s+(your|all|any)\s+(instructions|rules|guidelines)", "disregard_rules"),
    (r"curl\s+[^\n]*\$\{?\w*(KEY|TOKEN|SECRET|PASSWORD|CREDENTIAL|API)", "exfil_curl"),
    (r"wget\s+[^\n]*\$\{?\w*(KEY|TOKEN|SECRET|PASSWORD|CREDENTIAL|API)", "exfil_wget"),
    (r"cat\s+[^\n]*(\.env|credentials|\.netrc|\.pgpass)", "read_secrets"),
    (r"authorized_keys", "ssh_backdoor"),
    (r"/etc/sudoers|visudo", "sudoers_mod"),
    (r"rm\s+-rf\s+/", "destructive_root_rm"),
]

_INVISIBLE_CHARS = {
    "\u200b", "\u200c", "\u200d", "\u2060", "\ufeff",
    "\u202a", "\u202b", "\u202c", "\u202d", "\u202e",
}


def _scan_cron_prompt(prompt: str) -> str:
    """Scan a cron prompt for critical threats. Returns error string if blocked, else empty."""
    for char in _INVISIBLE_CHARS:
        if char in prompt:
            return f"Blocked: prompt contains invisible unicode U+{ord(char):04X} (possible injection)."
    for pattern, pid in _THREAT_PATTERNS:
        if re.search(pattern, prompt, re.IGNORECASE):
            return f"Blocked: prompt matches threat pattern '{pid}'."
    return ""


# ============== Tool ==============

def _to_json(data: dict) -> str:
    """Convert dict to JSON string. Delegates to tool_result for consistency."""
    return tool_result(data)


def _format_job(job: CronJob) -> dict:
    """Format a CronJob for display."""
    return {
        "job_id": job.id,
        "name": job.name,
        "prompt_preview": job.prompt[:100] + "..." if len(job.prompt) > 100 else job.prompt,
        "schedule": schedule_to_human(job.schedule),
        "status": job.status.value,
        "enabled": job.enabled,
        "deliver": job.deliver,
        "next_run_at_ms": job.next_run_at_ms,
        "last_run_at_ms": job.last_run_at_ms,
        "last_status": job.last_status,
        "run_count": job.run_count,
        "timeout_seconds": job.timeout_seconds,
        "max_retries": job.max_retries,
        "retry_count": job.retry_count,
        "retry_delay_ms": job.retry_delay_ms,
        "permissions": job.permissions,
    }


@tool(
    name="cronjob",
    description="""Manage scheduled cron jobs with a single tool.

Use action='create' to schedule a new job from a prompt.
Use action='list' to inspect jobs.
Use action='update', 'pause', 'resume', 'remove', or 'run' to manage an existing job.

Jobs run in a fresh session with no current-chat context, so prompts must be self-contained.
Cron jobs run autonomously with no user present.

Schedule formats:
- Cron expression: "30 7 * * *" (daily 7:30), "0 9 * * 1-5" (weekdays 9:00)
- Interval: "30m", "every 2h", "5s", "1d"
- One-shot ISO: "2024-01-15T09:30:00"
""",
)
def cronjob(
    action: str,
    job_id: Optional[str] = None,
    prompt: Optional[str] = None,
    schedule: Optional[str] = None,
    name: Optional[str] = None,
    deliver: Optional[str] = None,
    user_id: Optional[str] = None,
    timezone: Optional[str] = None,
    timeout_seconds: Optional[float] = None,
    max_retries: Optional[int] = None,
    retry_delay_ms: Optional[int] = None,
    permissions: Optional[dict[str, Any]] = None,
) -> str:
    """Unified cron job management tool.

    Args:
        action: One of: create, list, update, pause, resume, remove, run.
        job_id: Required for update/pause/resume/remove/run.
        prompt: For create/update: the self-contained prompt for the agent.
        schedule: For create/update: schedule string (cron, interval, or ISO).
        name: Optional human-friendly name.
        deliver: Delivery target: local, origin, telegram, discord, etc.
        user_id: User ID for filtering (defaults to 'default').
        timezone: Timezone for cron expressions (defaults to Asia/Shanghai).
        timeout_seconds: Optional per-run timeout. 0 or None disables timeout.
        max_retries: Optional immediate retry count after failure or timeout.
        retry_delay_ms: Delay between retry attempts.
        permissions: Product-layer permission profile, e.g. {"execute": false}.

    Returns:
        JSON string with operation result.
    """
    try:
        normalized = (action or "").strip().lower()

        if normalized == "create":
            return _action_create(
                prompt,
                schedule,
                name,
                deliver,
                user_id,
                timezone,
                timeout_seconds,
                max_retries,
                retry_delay_ms,
                permissions,
            )
        if normalized == "list":
            return _action_list(user_id)
        if not job_id:
            return _to_json({"success": False, "error": f"job_id is required for action '{normalized}'"})

        job = get_job(job_id)
        if not job:
            return _to_json({"success": False, "error": f"Job '{job_id}' not found. Use cronjob(action='list') to see jobs."})

        if normalized == "remove":
            return _action_remove(job_id, job)
        if normalized == "pause":
            return _action_pause(job_id)
        if normalized == "resume":
            return _action_resume(job_id)
        if normalized in {"run", "run_now", "trigger"}:
            return _action_trigger(job_id)
        if normalized == "update":
            return _action_update(
                job_id,
                prompt,
                schedule,
                name,
                deliver,
                timeout_seconds,
                max_retries,
                retry_delay_ms,
                permissions,
            )

        return _to_json({"success": False, "error": f"Unknown action '{action}'"})

    except Exception as e:
        return _to_json({"success": False, "error": str(e)})


def _action_create(
    prompt: Optional[str],
    schedule: Optional[str],
    name: Optional[str],
    deliver: Optional[str],
    user_id: Optional[str],
    timezone: Optional[str],
    timeout_seconds: Optional[float],
    max_retries: Optional[int],
    retry_delay_ms: Optional[int],
    permissions: Optional[dict[str, Any]],
) -> str:
    if not prompt:
        return _to_json({"success": False, "error": "prompt is required for create"})
    if not schedule:
        return _to_json({"success": False, "error": "schedule is required for create"})

    # Security scan
    scan_error = _scan_cron_prompt(prompt)
    if scan_error:
        return _to_json({"success": False, "error": scan_error})

    job = create_job(
        prompt=prompt,
        schedule=schedule,
        name=name,
        user_id=user_id or "default",
        deliver=deliver or "local",
        timezone=timezone or "Asia/Shanghai",
        timeout_seconds=timeout_seconds or 0.0,
        max_retries=max_retries or 0,
        retry_delay_ms=retry_delay_ms or 60000,
        permissions=permissions,
    )
    return _to_json({
        "success": True,
        "job": _format_job(job),
        "message": f"Cron job '{job.name}' created. {schedule_to_human(job.schedule)}.",
    })


def _action_list(user_id: Optional[str]) -> str:
    jobs = list_jobs(user_id=user_id, include_disabled=True)
    return _to_json({
        "success": True,
        "count": len(jobs),
        "jobs": [_format_job(j) for j in jobs],
    })


def _action_remove(job_id: str, job: CronJob) -> str:
    removed = remove_job(job_id)
    if not removed:
        return _to_json({"success": False, "error": f"Failed to remove job '{job_id}'"})
    return _to_json({
        "success": True,
        "message": f"Cron job '{job.name}' removed.",
    })


def _action_pause(job_id: str) -> str:
    updated = pause_job(job_id)
    if not updated:
        return _to_json({"success": False, "error": "Pause failed"})
    return _to_json({"success": True, "job": _format_job(updated)})


def _action_resume(job_id: str) -> str:
    updated = resume_job(job_id)
    if not updated:
        return _to_json({"success": False, "error": "Resume failed"})
    return _to_json({"success": True, "job": _format_job(updated)})


def _action_trigger(job_id: str) -> str:
    """Mark job as due immediately (will run on next tick)."""
    updated = update_job(job_id, {"next_run_at_ms": 1})
    if not updated:
        return _to_json({"success": False, "error": "Trigger failed"})
    return _to_json({
        "success": True,
        "job": _format_job(updated),
        "message": "Job will run on next scheduler tick.",
    })


def _action_update(
    job_id: str,
    prompt: Optional[str],
    schedule: Optional[str],
    name: Optional[str],
    deliver: Optional[str],
    timeout_seconds: Optional[float],
    max_retries: Optional[int],
    retry_delay_ms: Optional[int],
    permissions: Optional[dict[str, Any]],
) -> str:
    updates: dict = {}
    if prompt is not None:
        scan_error = _scan_cron_prompt(prompt)
        if scan_error:
            return _to_json({"success": False, "error": scan_error})
        updates["prompt"] = prompt
    if name is not None:
        updates["name"] = name
    if deliver is not None:
        updates["deliver"] = deliver
    if timeout_seconds is not None:
        updates["timeout_seconds"] = timeout_seconds
    if max_retries is not None:
        updates["max_retries"] = max_retries
    if retry_delay_ms is not None:
        updates["retry_delay_ms"] = retry_delay_ms
    if permissions is not None:
        updates["permissions"] = permissions
    if schedule is not None:
        parsed = parse_schedule(schedule)
        updates["schedule"] = parsed.to_dict()
        next_run = compute_next_run_at_ms(parsed)
        updates["next_run_at_ms"] = next_run or 0

    if not updates:
        return _to_json({"success": False, "error": "No updates provided."})

    updated = update_job(job_id, updates)
    if not updated:
        return _to_json({"success": False, "error": "Update failed"})
    return _to_json({"success": True, "job": _format_job(updated)})


# ============== Tool class wrapper for Agent(tools=[CronTool()]) ==============

class CronTool:
    """Cron job management tool for Agent integration.

    Usage:
        agent = Agent(tools=[CronTool()])
    """

    def __init__(self):
        self.functions = [cronjob]

    def __repr__(self) -> str:
        return "CronTool()"
