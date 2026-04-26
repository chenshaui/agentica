# Daily Tasks

Daily Tasks are the product-layer shape for recurring or scheduled agent work. They reuse the SDK cron backend, but add the missing product semantics: user intent, permission profile, run limits, retries, and structured run history.

## Layering

```text
User intent
    |
    v
DailyTaskSpec(name, prompt, schedule, workspace, permissions)
    |
    v
CronJob storage and scheduler tick
    |
    v
AgentRunner.run(prompt, context)
    |
    v
TaskRun(run_id, task_id, status, timing, error_type)
```

`DailyTaskSpec` is the stable input shape for CLI/Gateway/product surfaces. `CronJob` remains the file-backed execution record. `TaskRun` is the append-only history item written after every execution attempt.

## SDK Usage

```python
from agentica.cron import CronSchedule, DailyTaskSpec, create_daily_task

spec = DailyTaskSpec(
    name="Morning Brief",
    prompt="Summarize overnight incidents and list follow-up actions.",
    schedule=CronSchedule.at_time(8, 30),
    user_id="alice",
    workspace="/srv/agentica/workspaces/alice",
    permissions={"execute": False, "web_search": True},
    timeout_seconds=60,
    max_retries=2,
)

job = create_daily_task(spec)
```

## Failure Visibility

Every scheduler attempt writes a `TaskRun` record with:

- `status`: `ok`, `failed`, or `timeout`
- `error_type`: named failure class such as `TimeoutError`
- `started_at_ms`, `ended_at_ms`, and `duration_ms`
- `attempt`: retry attempt number
- `result` or `error`

```python
from agentica.cron import list_task_runs

runs = list_task_runs(job_id=job.id)
latest = runs[0]
print(latest.status, latest.error_type, latest.duration_ms)
```

## Product Rules

- Prompts must be self-contained because scheduled runs do not inherit the current chat.
- Product surfaces should pass a bounded `permissions` profile instead of exposing the full `DeepAgent` tool surface by default.
- Use `timeout_seconds` for all unattended tasks. A stuck run should become a visible `timeout`, not a silent hang.
- Use `max_retries` for transient failures. Retries are immediate scheduled attempts with `retry_delay_ms`; after retries are exhausted, recurring jobs wait for their next normal schedule.
