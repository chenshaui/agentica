# -*- coding: utf-8 -*-
"""
@author: XuMing(xuming624@qq.com)
@description: Cron scheduling module for agentica.

Provides SDK-level cron job management:
- CronJob model with JSON file storage
- Schedule types (at/every/cron) with next-run calculation
- tick() scheduler for periodic execution
- cronjob() tool for agent integration
"""
from agentica.cron.types import (
    AtSchedule,
    EverySchedule,
    CronSchedule,
    DailyTaskSpec,
    Schedule,
    ScheduleKind,
    JobStatus,
    RunStatus,
    TaskRun,
    schedule_from_dict,
)
from agentica.cron.jobs import (
    CronJob,
    create_job,
    create_daily_task,
    get_job,
    list_jobs,
    list_task_runs,
    update_job,
    remove_job,
    pause_job,
    resume_job,
    get_due_jobs,
    mark_job_run,
    parse_schedule,
    schedule_to_human,
    compute_next_run_at_ms,
    validate_cron_expression,
)
from agentica.cron.scheduler import tick, AgentRunner

__all__ = [
    # Types
    "AtSchedule",
    "EverySchedule",
    "CronSchedule",
    "DailyTaskSpec",
    "Schedule",
    "ScheduleKind",
    "JobStatus",
    "RunStatus",
    "TaskRun",
    "schedule_from_dict",
    # Job management
    "CronJob",
    "create_job",
    "create_daily_task",
    "get_job",
    "list_jobs",
    "list_task_runs",
    "update_job",
    "remove_job",
    "pause_job",
    "resume_job",
    "get_due_jobs",
    "mark_job_run",
    "parse_schedule",
    "schedule_to_human",
    "compute_next_run_at_ms",
    "validate_cron_expression",
    # Scheduler
    "tick",
    "AgentRunner",
]
