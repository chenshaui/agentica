# -*- coding: utf-8 -*-
"""
@author:XuMing(xuming624@qq.com)
@description: Scheduler routes: /api/scheduler/*

Simplified to use the SDK cron module (agentica.cron) instead of the old
gateway-embedded SchedulerService.
"""
import json
from typing import Optional

from fastapi import APIRouter, HTTPException

from agentica.cron.jobs import (
    list_jobs,
    get_job,
    remove_job,
    pause_job,
    resume_job,
    schedule_to_human,
)
from agentica.tools.cron_tool import cronjob

router = APIRouter(prefix="/api/scheduler")


# ============== Job CRUD ==============

@router.get("/jobs")
async def api_list_jobs(
    user_id: Optional[str] = None,
    include_disabled: bool = False,
    limit: int = 100,
):
    jobs = list_jobs(user_id=user_id, include_disabled=include_disabled, limit=limit)
    return {
        "jobs": [
            {
                "id": j.id,
                "name": j.name,
                "user_id": j.user_id,
                "schedule": schedule_to_human(j.schedule),
                "status": j.status.value,
                "enabled": j.enabled,
                "next_run_at_ms": j.next_run_at_ms,
                "last_run_at_ms": j.last_run_at_ms,
                "run_count": j.run_count,
            }
            for j in jobs
        ],
        "total": len(jobs),
    }


@router.get("/jobs/{job_id}")
async def api_get_job(job_id: str):
    job = get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    return {
        "job": {
            "id": job.id,
            "name": job.name,
            "prompt": job.prompt,
            "schedule": schedule_to_human(job.schedule),
            "status": job.status.value,
            "enabled": job.enabled,
            "next_run_at_ms": job.next_run_at_ms,
            "last_run_at_ms": job.last_run_at_ms,
            "run_count": job.run_count,
            "deliver": job.deliver,
        }
    }


@router.post("/jobs")
async def api_create_job(
    name: str,
    prompt: str,
    schedule: str,
    user_id: str = "default",
    timezone: str = "Asia/Shanghai",
    deliver: str = "local",
):
    result_str = cronjob(
        action="create",
        prompt=prompt,
        schedule=schedule,
        name=name,
        user_id=user_id,
        timezone=timezone,
        deliver=deliver,
    )
    result = json.loads(result_str)
    if not result.get("success"):
        raise HTTPException(status_code=400, detail=result.get("error", "Failed to create job"))
    return result


@router.delete("/jobs/{job_id}")
async def api_delete_job(job_id: str, user_id: str = "default"):
    job = get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    if job.user_id != user_id:
        raise HTTPException(status_code=403, detail="Permission denied")
    removed = remove_job(job_id)
    if not removed:
        raise HTTPException(status_code=400, detail="Delete failed")
    return {"status": "deleted", "job_id": job_id}


# ============== Job actions ==============

@router.post("/jobs/{job_id}/pause")
async def api_pause_job(job_id: str, user_id: str = "default"):
    job = get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    if job.user_id != user_id:
        raise HTTPException(status_code=403, detail="Permission denied")
    updated = pause_job(job_id)
    if not updated:
        raise HTTPException(status_code=400, detail="Pause failed")
    return {"status": "paused", "job_id": job_id}


@router.post("/jobs/{job_id}/resume")
async def api_resume_job(job_id: str, user_id: str = "default"):
    job = get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    if job.user_id != user_id:
        raise HTTPException(status_code=403, detail="Permission denied")
    updated = resume_job(job_id)
    if not updated:
        raise HTTPException(status_code=400, detail="Resume failed")
    return {
        "status": "resumed",
        "job_id": job_id,
        "next_run_at_ms": updated.next_run_at_ms,
    }


@router.post("/jobs/{job_id}/trigger")
async def api_trigger_job(job_id: str):
    result_str = cronjob(action="run", job_id=job_id)
    result = json.loads(result_str)
    if not result.get("success"):
        raise HTTPException(status_code=400, detail=result.get("error", "Trigger failed"))
    return result
