# Agentica Gateway - Architecture Overview

## System Architecture Diagram

```
┌─────────────────────────────────────────────────────────────────┐
│                        GATEWAY SERVICE                          │
├─────────────────────────────────────────────────────────────────┤
│                                                                 │
│  ┌──────────────────────────────────────────────────────────┐  │
│  │                    FASTAPI APP (main.py)                │  │
│  │  - CORS & Auth Middleware                              │  │
│  │  - Request ID tracking (ContextVar)                    │  │
│  │  - Lifespan: startup/shutdown management              │  │
│  └──────────────────────────────────────────────────────────┘  │
│                              ▲                                  │
│     ┌────────────────────────┼────────────────────────┐        │
│     │                        │                        │        │
│  ┌──┴─────────┐      ┌──────┴──────┐      ┌─────────┴──┐     │
│  │   Routes   │      │  Services   │      │ Scheduler  │     │
│  ├────────────┤      ├─────────────┤      ├────────────┤     │
│  │ /config    │      │   Agent     │      │  Service   │     │
│  │ /chat      │      │  Service    │      └────────────┘     │
│  │ /scheduler │      │             │           ▲              │
│  │ /channels  │      │ manages     │           │              │
│  │ /ws        │      │ agent       │      Listens every       │
│  └────────────┘      │ sessions    │      job's next_run_at  │
│                      │ (LRU cache) │           │              │
│                      │             │      ┌────┴──────────┐  │
│                      │   Channel   │      │ Timer Loop    │  │
│                      │  Manager    │      │ (asyncio)     │  │
│                      │             │      │ - arm_timer() │  │
│                      │   Message   │      │ - run_due_    │  │
│                      │   Router    │      │   jobs()      │  │
│                      └─────────────┘      └───────────────┘  │
│                                                                 │
│  ┌──────────────────────────────────────────────────────────┐  │
│  │              SCHEDULER SUBSYSTEM                         │  │
│  │                                                          │  │
│  │  ┌─────────────┬──────────────┬──────────────────────┐  │  │
│  │  │   Types     │   Models     │    Schedule Calc     │  │  │
│  │  ├─────────────┼──────────────┼──────────────────────┤  │  │
│  │  │ Schedule    │ ScheduledJob │ compute_next_run()   │  │  │
│  │  │ - At        │ - id         │ - Cron parsing       │  │  │
│  │  │ - Every     │ - name       │ - Interval calc      │  │  │
│  │  │ - Cron      │ - schedule   │ - One-time schedule  │  │  │
│  │  │             │ - payload    │ - Human-readable     │  │  │
│  │  │ Payload     │ - target     │                      │  │  │
│  │  │ - Agent     │ - state      │ ┌────────────────┐   │  │  │
│  │  │ - Webhook   │ - status     │ │ croniter lib   │   │  │  │
│  │  │ - System    │              │ │ w/ fallback    │   │  │  │
│  │  │   Event     │ JobCreate    │ │ simple patterns│   │  │  │
│  │  │ - Task      │ JobPatch     │ └────────────────┘   │  │  │
│  │  │   Chain     │              │                      │  │  │
│  │  └─────────────┴──────────────┴──────────────────────┘  │  │
│  │                                                          │  │
│  │  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐  │  │
│  │  │   Service    │  │   Executor   │  │    Tools     │  │  │
│  │  ├──────────────┤  ├──────────────┤  ├──────────────┤  │  │
│  │  │ start()      │  │execute()     │  │create_job    │  │  │
│  │  │ stop()       │  │ - main mode  │  │list_jobs     │  │  │
│  │  │create_job()  │  │ - isolated   │  │delete_job    │  │  │
│  │  │delete_job()  │  │   mode       │  │pause_job     │  │  │
│  │  │pause_job()   │  │ - dispatch   │  │resume_job    │  │  │
│  │  │resume_job()  │  │   by type    │  │create_chain  │  │  │
│  │  │list_jobs()   │  │             │  │             │  │  │
│  │  │get_stats()   │  │_execute_     │  │For LLM use   │  │  │
│  │  │             │  │agent_task()  │  │             │  │  │
│  │  │ Manages:    │  │_execute_     │  │Returns JSON  │  │  │
│  │  │ - Job store │  │webhook()     │  │             │  │  │
│  │  │ - Timer     │  │_execute_     │  │             │  │  │
│  │  │ - Events    │  │system_event()│  │             │  │  │
│  │  └──────────────┘  └──────────────┘  └──────────────┘  │  │
│  │                                                          │  │
│  │  ┌──────────────────────────────────────────────────┐   │  │
│  │  │            PERSISTENCE LAYER                    │   │  │
│  │  ├──────────────────────────────────────────────────┤   │  │
│  │  │ YAML: scheduler.yaml                            │   │  │
│  │  │   - Job definitions (user-editable)             │   │  │
│  │  │   - Serialized ScheduledJob objects             │   │  │
│  │  │                                                 │   │  │
│  │  │ SQLite: scheduler_state.db                      │   │  │
│  │  │   - job_state table (runtime state)             │   │  │
│  │  │   - job_runs table (execution history)          │   │  │
│  │  │   - Timestamps, status, retry counts            │   │  │
│  │  └──────────────────────────────────────────────────┘   │  │
│  │                                                          │  │
│  └──────────────────────────────────────────────────────────┘  │
│                                                                 │
└─────────────────────────────────────────────────────────────────┘
       ▲                      ▲                      ▲
       │                      │                      │
   HTTP/WS              REST API              Config (env)
   Clients              (/api/*)              & Channels
```

---

## Execution Flow: Timer-Triggered Job

```
┌─────────────────────────────────────────────────────────────────┐
│                       TIMER LOOP (asyncio)                      │
├─────────────────────────────────────────────────────────────────┤
│                                                                 │
│  1. Get next_wake_at_ms from store                            │
│     └─→ Query all jobs, find minimum next_run_at_ms            │
│                                                                 │
│  2. Calculate sleep_seconds = (next_wake_at_ms - now()) / 1000 │
│     └─→ If 0, execute immediately; if >0, sleep               │
│                                                                 │
│  3. asyncio.wait_for(wake_event.wait(), timeout=sleep_seconds) │
│     ├─ If timeout expires → job due, proceed to step 4        │
│     └─ If event fires → new job added, restart loop           │
│                                                                 │
│  4. Run due jobs (all with next_run_at_ms <= now())            │
│     ├─→ For each job:                                          │
│     │   a. Create JobRun record with started_at_ms            │
│     │   b. Call executor.execute(job, target_mode)            │
│     │   │  └─→ Dispatch by payload type:                      │
│     │   │     ├─ AgentTurnPayload                             │
│     │   │     │  └─→ Run agent with prompt                    │
│     │   │     │     └─→ Create isolated session               │
│     │   │     │     └─→ Return result                         │
│     │   │     ├─ WebhookPayload                               │
│     │   │     │  └─→ Call HTTP endpoint                       │
│     │   │     ├─ SystemEventPayload                           │
│     │   │     │  └─→ Log event                                │
│     │   │     └─ TaskChainPayload                             │
│     │   │        └─→ Trigger next job on condition            │
│     │   c. Update JobRun with result, finished_at_ms, status  │
│     │   d. Update job.state:                                  │
│     │      - last_run_at_ms                                   │
│     │      - run_count++                                      │
│     │      - last_status                                      │
│     │   e. Calculate next_run_at_ms via compute_next_run()    │
│     │   f. Save updated job & state to SQLite                 │
│     └─→ (parallel execution for concurrent jobs)              │
│                                                                 │
│  5. Re-arm timer → Go back to step 1                           │
│                                                                 │
└─────────────────────────────────────────────────────────────────┘
```

---

## Configuration Flow

```
┌──────────────────┐
│   .env file      │
│  (or environ)    │
└────────┬─────────┘
         │
         ▼
┌──────────────────────────────────┐
│   config.py module load          │
│   Settings.from_env()            │
│   └─→ Parse all env vars         │
│       └─→ Set defaults           │
└────────┬─────────────────────────┘
         │
         ▼
┌──────────────────────────────────┐
│   Global: settings               │
│   (singleton Settings instance)  │
└────────┬─────────────────────────┘
         │
         ├─→ Used in main.py lifespan
         │   ├─→ Create AgentService
         │   ├─→ Create SchedulerService
         │   ├─→ Configure routes
         │   └─→ Setup channels
         │
         └─→ Used in all routes via FastAPI depends
             └─→ Dependency injection helpers
```

---

## Key Configuration Parameters

```
📦 SERVER
   - HOST: 0.0.0.0              (bind address)
   - PORT: 8789                 (bind port)
   - DEBUG: false                (debug mode)
   - GATEWAY_TOKEN: None         (API auth)

👤 IDENTITY
   - DEFAULT_USER_ID: default    (fallback user)

📁 STORAGE
   - AGENTICA_WORKSPACE_DIR      (agent working dir)
   - AGENTICA_DATA_DIR           (scheduler state + runs)
   - AGENTICA_BASE_DIR           (agent cwd)

🤖 MODEL
   - AGENTICA_MODEL_PROVIDER: zhipuai
   - AGENTICA_MODEL_NAME: glm-4.7-flash
   - AGENTICA_MODEL_THINKING: "" (empty/enabled/disabled/auto)

💾 CACHING & LIMITS
   - AGENT_MAX_SESSIONS: 50     (LRU cache size)
   - UPLOAD_MAX_SIZE_MB: 50
   - UPLOAD_ALLOWED_EXTENSIONS: ".txt,.md,..."
   - JOB_RUNS_RETENTION_DAYS: 30

🗣️ CHANNELS (optional)
   - FEISHU_*
   - TELEGRAM_*
   - DISCORD_*
```

---

## Version Handling

```
agentica/version.py
└─→ __version__ = "1.3.5"
    └─→ Imported in gateway/__init__.py
        └─→ Used in main.py FastAPI app:
            - title="Agentica Gateway"
            - version=__version__
        └─→ Displayed in startup logs
```

---

## Schedule Types & Calculations

```
THREE SCHEDULE TYPES:

1. AT (One-time)
   ├─ at_ms: 1705381800000  (Unix ms)
   ├─ Calculation: if at_ms > now_ms → return at_ms, else None
   └─ Use case: "Run once tomorrow at 3pm"

2. EVERY (Interval)
   ├─ interval_ms: 300000  (5 minutes in ms)
   ├─ Calculation: 
   │  first:     now_ms + interval_ms
   │  next:      last_run_at_ms + interval_ms
   │  (aligned forward if past due)
   └─ Use case: "Run every 5 minutes"

3. CRON (Expression)
   ├─ expression: "0 15 * * *"  (3pm daily, 5-part)
   │              "0 0 15 * * *"  (3pm daily, 6-part with seconds)
   ├─ timezone: "Asia/Shanghai"
   ├─ Calculation:
   │  1. Parse to 5 or 6 parts
   │  2. Use croniter library (or fallback simple patterns)
   │  3. Get next occurrence in timezone
   │  4. Convert to ms
   └─ Use case: "Every weekday at 9am"

HUMAN-READABLE CONVERSION:
  AT: "在 2024-01-15 15:00:00 执行一次"
  EVERY: "每隔 5 分钟"
  CRON: "每天 3:00 PM" or "每隔 30 分钟"
```

---

## Session Execution Modes

```
TWO EXECUTION MODES:

1. MAIN MODE
   ├─ Inject systemEvent into user's active main session
   ├─ Trigger heartbeat (optional)
   ├─ Use case: Update UI in real-time
   └─ Flow:
      Job fires
      └─→ executor.execute(job, SessionTarget(kind="main"))
         └─→ Call on_system_event(user_id, event_data)
            └─→ Event queued for user's main session
            └─→ If trigger_heartbeat: run_heartbeat(user_id)
               └─→ Trigger UI refresh

2. ISOLATED MODE
   ├─ Run in independent agent session
   ├─ Report result back to main (optional)
   ├─ Use case: Long-running tasks, don't block UI
   └─ Flow:
      Job fires
      └─→ executor.execute(job, SessionTarget(kind="isolated"))
         └─→ Create new session: f"scheduled_{job_id}"
         └─→ Run agent with payload.prompt
         └─→ Get result
         └─→ If report_to_main: report_to_main(user_id, job_id, result)
            └─→ Queue result for main session
```

---

## Payload Types (What Jobs Execute)

```
FOUR PAYLOAD TYPES:

1. AGENT_TURN (Most common)
   ├─ prompt: "Generate daily report"
   ├─ agent_id: "main"
   ├─ context: {"key": "value"}  (passed to agent)
   ├─ timeout_seconds: 300
   └─ Execution:
      └─→ Run agent with prompt in isolated session
      └─→ Agent has full tool access
      └─→ Result stored in SQLite run_history

2. WEBHOOK
   ├─ url: "https://example.com/webhook"
   ├─ method: "POST"
   ├─ headers: {"Authorization": "Bearer token"}
   ├─ body: {"key": "value"}
   └─ Execution:
      └─→ Make HTTP request (GET/POST/PUT)
      └─→ Include job_id, name, timestamp in payload
      └─→ Raise on 4xx/5xx status

3. SYSTEM_EVENT
   ├─ message: "Daily report generated"
   ├─ channel: "telegram"
   ├─ chat_id: "123456"
   └─ Execution:
      └─→ Log event
      └─→ No external action

4. TASK_CHAIN
   ├─ next_job_id: "job-456"
   ├─ on_status: ["ok"]  (trigger conditions)
   └─ Execution:
      └─→ If previous job status matches on_status
      └─→ Trigger next_job_id immediately
      └─→ Enables job chaining/workflows
```

---

## Data Storage

```
YAML: ~/.agentica/data/scheduler.yaml
  └─→ Job definitions (user-editable)
  └─→ Example:
     jobs:
       - id: job-123
         name: "Daily Report"
         schedule: {kind: "cron", expression: "0 15 * * *", ...}
         payload: {kind: "agent_turn", prompt: "Generate report", ...}
         ...

SQLite: ~/.agentica/data/scheduler_state.db
  ├─→ job_state table
  │   └─ next_run_at_ms, last_run_at_ms, run_count, etc.
  │
  ├─→ job_runs table
  │   └─ Historical execution records
  │      └─ job_id, started_at_ms, finished_at_ms, status, result, error
  │
  └─→ (Pruned when runs older than JOB_RUNS_RETENTION_DAYS)

FLOW:
  SchedulerService.create_job()
    └─→ Save to scheduler.yaml
    └─→ Initialize job_state in SQLite
    └─→ Calculate next_run_at_ms
    
  Job execution:
    └─→ Create job_run record
    └─→ Update after execution
    └─→ Save to SQLite
    
  Data cleanup:
    └─→ On startup: _prune_old_runs()
    └─→ Delete runs older than retention_days
```

