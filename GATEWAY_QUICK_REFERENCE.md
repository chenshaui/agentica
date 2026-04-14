# Agentica Gateway - Quick Reference Guide

## File Map at a Glance

| File | Purpose | Key Exports |
|------|---------|-------------|
| `config.py` | Settings management | `Settings`, `settings` |
| `main.py` | FastAPI app entry | `app`, `lifespan()` |
| `deps.py` | FastAPI DI helpers | `get_agent_service()`, `get_scheduler()` |
| `models.py` | Pydantic DTOs | `ChatRequest`, `JobResponse` |
| **scheduler/** | | |
| `types.py` | Type definitions | `Schedule`, `Payload`, `JobStatus` |
| `models.py` | Job dataclasses | `ScheduledJob`, `JobCreate` |
| `schedule.py` | ⭐ Next run calculation | `compute_next_run_at_ms()` |
| `executor.py` | Job execution | `JobExecutor` |
| `tools.py` | LLM agent tools | `create_scheduled_job_tool`, etc. |
| `service/service.py` | Main scheduler | `SchedulerService` |
| `service/timer.py` | Execution loop | `timer_loop()`, `arm_timer()` |
| `service/store.py` | YAML + SQLite | `JobStore` |

---

## How config.py Works

### Load Flow
```
Environment (.env or env vars)
    ↓
config.py imports + dotenv.load_dotenv()
    ↓
Settings.from_env() class method
    ├─ Parse all env vars
    ├─ Apply defaults from dataclass
    └─ Create Settings instance
    ↓
Global: settings = Settings.from_env()
    ↓
Used in:
  - main.py: agent creation, scheduler setup
  - routes: accessed via FastAPI depends
  - services: imported directly
```

### Key Methods

```python
# Load from environment
settings = Settings.from_env()

# Fast extension lookup
ext_set = settings.upload_allowed_ext_set  # O(1) lookup
if ".pdf" in ext_set:
    ...
```

### Critical Fields

```python
settings.model_provider    # "zhipuai"
settings.model_name        # "glm-4.7-flash"
settings.data_dir          # ~/.agentica/data (scheduler storage)
settings.gateway_token     # API auth (None = open)
settings.job_runs_retention_days  # Pruning policy
```

---

## How schedule.py Works

### Main Function: compute_next_run_at_ms()

```python
from scheduler.schedule import compute_next_run_at_ms
from scheduler.types import CronSchedule

schedule = CronSchedule(
    expression="0 15 * * *",  # 3pm daily
    timezone="Asia/Shanghai"
)

next_ms = compute_next_run_at_ms(
    schedule=schedule,
    current_ms=None,  # defaults to now
    last_run_at_ms=None
)
# Returns: 1705329600000 (next 3pm in ms)
```

### Three Schedule Types

**AtSchedule (one-time)**
```python
from scheduler.types import AtSchedule
from datetime import datetime

# Run at specific time
schedule = AtSchedule.from_datetime(datetime(2024, 1, 15, 15, 0))
next_run = compute_next_run_at_ms(schedule)
# Returns at_ms if future, None if past
```

**EverySchedule (interval)**
```python
from scheduler.types import EverySchedule

# Run every 5 minutes
schedule = EverySchedule.from_seconds(300)
next_run = compute_next_run_at_ms(
    schedule=schedule,
    last_run_at_ms=1705329500000  # Important for interval!
)
# Returns: last_run + 300000 ms (or aligned if past)
```

**CronSchedule (cron expression)**
```python
from scheduler.types import CronSchedule

# Run daily at 9am Shanghai time
schedule = CronSchedule(
    expression="0 9 * * *",      # 5-part: min hour day month weekday
    timezone="Asia/Shanghai"
)
next_run = compute_next_run_at_ms(schedule)

# 6-part format (with seconds):
schedule = CronSchedule(
    expression="0 0 9 * * *",    # 6-part: sec min hour day month weekday
    timezone="Asia/Shanghai"
)
```

### Utility Functions

```python
from scheduler.schedule import (
    now_ms,                    # Current time in ms
    validate_cron_expression,  # Check cron syntax
    schedule_to_human,         # "每天 3:00"
    cron_to_human,            # "每隔 30 分钟"
    interval_to_human,        # "每隔 5 分钟"
)

# Current time
current = now_ms()  # 1705329600000

# Validate before creating
if validate_cron_expression("0 15 * * *"):
    schedule = CronSchedule(expression="0 15 * * *")

# Human readable
desc = schedule_to_human(schedule)  # "每天 3:00 PM"
```

---

## How SchedulerService Works

### Initialization & Startup

```python
from scheduler import SchedulerService, JobExecutor
from pathlib import Path

# Create executor
executor = JobExecutor(agent_runner=agent_runner)

# Create scheduler
scheduler = SchedulerService(
    data_dir=str(settings.data_dir),
    executor=executor,
)

# Start (loads YAML, starts timer loop)
await scheduler.start()

# Later: stop gracefully
await scheduler.stop()
```

### Creating Jobs

```python
from scheduler.models import JobCreate
from scheduler.types import (
    CronSchedule,
    AgentTurnPayload,
    SessionTarget,
    SessionTargetKind,
)

# Create job definition
job_create = JobCreate(
    user_id="user123",
    name="Daily Report",
    schedule=CronSchedule.at_time(15, 0),  # 3pm daily
    payload=AgentTurnPayload(prompt="Generate report"),
    target=SessionTarget(kind=SessionTargetKind.ISOLATED),
    agent_id="main",
    enabled=True,
)

# Submit to scheduler
job = await scheduler.create_job(job_create)
print(job.id)  # "job-abc123"
```

### Job Lifecycle

```python
# List jobs
jobs = await scheduler.list_jobs(user_id="user123")

# Pause execution
await scheduler.pause_job(user_id="user123", job_id="job-abc123")

# Resume execution
await scheduler.resume_job(user_id="user123", job_id="job-abc123")

# Delete job
await scheduler.delete_job(user_id="user123", job_id="job-abc123")

# Get single job
job = await scheduler.get_job(user_id="user123", job_id="job-abc123")

# Statistics
stats = await scheduler.get_stats()
print(f"Active jobs: {stats.active_jobs}")
print(f"Next run at: {stats.next_run_at_ms}")
```

---

## How Timer Loop Works

### Architecture

```
Timer Loop (runs in background):
1. Get next_wake_at_ms from store
2. Calculate sleep time
3. asyncio.wait_for(wake_event.wait(), timeout=sleep_seconds)
   ├─ Times out → Jobs due, execute
   └─ Event fires early → New job added, restart
4. Repeat
```

### Execution Sequence

```
Timer fires (timeout at scheduled time)
    ↓
service._run_due_jobs()
    ├─ Query: jobs with next_run_at_ms <= now()
    ├─ For each job:
    │   ├─ Create JobRun record
    │   ├─ executor.execute(job, target)
    │   ├─ Update job.state (run_count, last_run_at_ms, etc.)
    │   ├─ Calculate next_run_at_ms
    │   └─ Save to SQLite
    ├─ Handle on_complete task chains
    └─ Emit events
    ↓
arm_timer(service)
    ├─ Get new next_wake_at_ms from store
    ├─ Set service.state.next_wake_at_ms
    └─ Signal wake_event
    ↓
Timer loop sleeps until next_wake_at_ms
```

---

## How Executor Works

### Two Execution Modes

**Main Mode** (inject into user's session)
```python
target = SessionTarget(kind="main", trigger_heartbeat=True)
result = await executor.execute(job, target)
# Result: "Injected to main session"
# Side effect: on_system_event(user_id, event_data) called
```

**Isolated Mode** (run independent session)
```python
target = SessionTarget(kind="isolated", report_to_main=True)
result = await executor.execute(job, target)
# Result: actual execution result (from agent, webhook, etc.)
# Side effect: report_to_main(user_id, job_id, result) called
```

### Payload Dispatch

```python
# Based on job.payload type:

AgentTurnPayload
  └─→ executor._execute_agent_task()
      └─→ Create isolated session f"scheduled_{job_id}"
      └─→ agent_runner.run(prompt, context)
      └─→ Return result

WebhookPayload
  └─→ executor._execute_webhook()
      └─→ aiohttp.post(url, json=body, headers=headers)
      └─→ Return status or raise

SystemEventPayload
  └─→ executor._execute_system_event()
      └─→ logger.info(message)
      └─→ Return log message

TaskChainPayload
  └─→ Handled by SchedulerService
      └─→ Trigger next_job_id on condition
```

---

## How Agent Tools Work

### Registration

```python
# In main.py lifespan:
from scheduler import init_scheduler_tools

init_scheduler_tools(sched)  # Register tools for agent access
```

### Available Tools

```python
# Tool 1: Create scheduled job
create_scheduled_job(
    name="Daily Report",
    prompt="Generate report",
    user_id="user123",
    # One of:
    cron_expression="0 15 * * *",  # or
    interval_seconds=300,           # or
    run_at_iso="2024-01-15T15:00:00",
    timezone="Asia/Shanghai",
)

# Tool 2: List jobs
list_scheduled_jobs(user_id="user123")

# Tool 3: Delete job
delete_scheduled_job(user_id="user123", job_id="job-abc123")

# Tool 4: Pause job
pause_scheduled_job(user_id="user123", job_id="job-abc123")

# Tool 5: Resume job
resume_scheduled_job(user_id="user123", job_id="job-abc123")

# Tool 6: Create task chain
create_task_chain(
    current_job_id="job-abc123",
    next_job_id="job-def456",
    on_status=["ok"],  # Trigger when status is "ok"
)
```

---

## Version Handling

### Where Version Comes From

```python
# agentica/version.py
__version__ = "1.3.5"

# gateway/__init__.py imports it
from agentica.version import __version__

# Used in main.py
from agentica.version import __version__

app = FastAPI(
    title="Agentica Gateway",
    version=__version__,  # In OpenAPI docs
)

logger.info(f"Agentica Gateway v{__version__}")  # In logs
```

### How to Update Version

1. Edit `/Users/xuming/Documents/Codes/agentica/agentica/version.py`
2. Change `__version__ = "X.Y.Z"`
3. Automatic propagation to:
   - FastAPI docs
   - Logs
   - Gateway responses

---

## Storage Format

### YAML: scheduler.yaml

```yaml
jobs:
  - id: "job-12345"
    user_id: "user123"
    agent_id: "main"
    name: "Daily Report"
    description: "Generate daily report"
    enabled: true
    
    schedule:
      kind: "cron"
      expression: "0 15 * * *"
      timezone: "Asia/Shanghai"
    
    payload:
      kind: "agent_turn"
      prompt: "Generate report"
      agent_id: "main"
      context: {}
      timeout_seconds: 300
    
    target:
      kind: "isolated"
      trigger_heartbeat: true
      report_to_main: true
    
    max_retries: 3
    retry_delay_ms: 60000
    
    state:
      next_run_at_ms: 1705329600000
      last_run_at_ms: 1705243200000
      last_status: "ok"
      run_count: 5
      failure_count: 0
      consecutive_failures: 0
      last_error: null
    
    status: "active"
    created_at_ms: 1705000000000
    updated_at_ms: 1705329000000
```

### SQLite: scheduler_state.db

```sql
-- job_state table
CREATE TABLE job_state (
    job_id TEXT PRIMARY KEY,
    next_run_at_ms INTEGER,
    last_run_at_ms INTEGER,
    run_count INTEGER,
    ...
);

-- job_runs table
CREATE TABLE job_runs (
    id TEXT PRIMARY KEY,
    job_id TEXT,
    started_at_ms INTEGER,
    finished_at_ms INTEGER,
    status TEXT,           -- "ok", "failed", "timeout"
    result TEXT,           -- execution result
    error TEXT,            -- error message
    duration_ms INTEGER,
    ...
);
```

---

## Troubleshooting Checklist

| Issue | Check |
|-------|-------|
| Jobs not running | Timer loop started? `scheduler.state.running == True` |
| Jobs run too often | Check `last_run_at_ms` vs `next_run_at_ms` calculation |
| Cron not working | Validate with `validate_cron_expression()` |
| Data lost | Check SQLite not corrupted, YAML writable |
| Memory leak | Check SQLite pruning (run history retention) |
| High CPU | Timer loop logic, agent execution bottleneck |

---

## Key Constants & Defaults

```python
# Server
DEFAULT_HOST = "0.0.0.0"
DEFAULT_PORT = 8789

# Paths
DEFAULT_WORKSPACE = ~/.agentica/workspace
DEFAULT_DATA_DIR = ~/.agentica/data
DEFAULT_BASE_DIR = $HOME

# Model
DEFAULT_PROVIDER = "zhipuai"
DEFAULT_MODEL = "glm-4.7-flash"

# Limits
DEFAULT_AGENT_SESSIONS = 50
DEFAULT_UPLOAD_MB = 50
DEFAULT_RETENTION_DAYS = 30

# Timing (all in milliseconds)
RETRY_DELAY_MS = 60000  # 1 minute
AGENT_TIMEOUT_SECONDS = 300  # 5 minutes
WEBHOOK_TIMEOUT_SECONDS = 30

# Timezone
DEFAULT_TIMEZONE = "Asia/Shanghai"
```

---

## Common Patterns

### Pattern 1: Create Daily Job at Specific Time

```python
from scheduler.types import CronSchedule, AgentTurnPayload, SessionTarget
from scheduler.models import JobCreate

job_create = JobCreate(
    user_id="user123",
    name="Morning Standup",
    schedule=CronSchedule.at_time(9, 0),  # 9:00 AM daily
    payload=AgentTurnPayload(prompt="Generate standup"),
    target=SessionTarget(),  # Isolated mode
)
```

### Pattern 2: Every N Minutes

```python
from scheduler.types import EverySchedule

job_create = JobCreate(
    user_id="user123",
    name="Health Check",
    schedule=EverySchedule.from_seconds(300),  # Every 5 min
    payload=AgentTurnPayload(prompt="Check system"),
)
```

### Pattern 3: Job Chain (Sequential Execution)

```python
# Create first job
job1 = await scheduler.create_job(job1_create)

# Create second job
job2 = await scheduler.create_job(job2_create)

# Link: job1 triggers job2 on success
from scheduler.types import TaskChainPayload
chain = TaskChainPayload(
    next_job_id=job2.id,
    on_status=["ok"]
)
# Add to job1.on_complete
```

### Pattern 4: Webhook Notification

```python
from scheduler.types import WebhookPayload

job_create = JobCreate(
    user_id="user123",
    name="Send Report",
    schedule=CronSchedule.at_time(15, 0),
    payload=WebhookPayload(
        url="https://example.com/webhook",
        method="POST",
        body={"action": "report"},
    ),
)
```

