# Agentica Gateway - Complete Directory Structure & Analysis

## 1. DIRECTORY TREE

```
/Users/xuming/Documents/Codes/agentica/agentica/gateway/
├── __init__.py                      # Package init, imports __version__
├── main.py                          # FastAPI app entry point, lifespan management
├── config.py                        # Settings dataclass, env config loading
├── deps.py                          # FastAPI dependency injection helpers
├── models.py                        # Pydantic request/response models
│
├── channels/                        # Multi-channel communication support
│   ├── __init__.py
│   ├── base.py                      # BaseChannel abstract class
│   ├── discord.py                   # Discord channel implementation
│   ├── feishu.py                    # Feishu (飞书) channel implementation
│   └── telegram.py                  # Telegram channel implementation
│
├── routes/                          # FastAPI route handlers
│   ├── __init__.py
│   ├── config.py                    # Config endpoints (/api/config/*)
│   ├── chat.py                      # Chat endpoints (/api/chat/*)
│   ├── scheduler.py                 # Scheduler REST API (/api/scheduler/*)
│   ├── channels.py                  # Channel management endpoints
│   └── ws.py                        # WebSocket connections
│
├── services/                        # Core business logic services
│   ├── __init__.py
│   ├── agent_service.py             # Agent session & execution management
│   ├── channel_manager.py           # Channel lifecycle management
│   └── router.py                    # Message routing logic
│
├── scheduler/                       # ⭐ SCHEDULER SUBSYSTEM (detailed below)
│   ├── __init__.py                  # Exports all public APIs
│   ├── types.py                     # Core type definitions (Schedule, Payload, Status)
│   ├── models.py                    # ScheduledJob, JobCreate, JobPatch dataclasses
│   ├── schedule.py                  # ⭐ Schedule calculation logic
│   ├── executor.py                  # Job execution engine
│   ├── tools.py                     # Agent tools for job creation
│   │
│   └── service/                     # Internal scheduler implementation
│       ├── __init__.py
│       ├── service.py               # Main SchedulerService class
│       ├── store.py                 # YAML config + SQLite persistence
│       ├── state.py                 # Service state management
│       ├── timer.py                 # Asyncio timer loop for job execution
│       ├── events.py                # Event emission system
│       ├── ops.py                   # Job operations (create, update, delete, etc.)
│       └── service/                 # Shared utilities
│           ├── __init__.py
│           └── (Additional helpers)
│
├── static/                          # Web UI assets
│   ├── index.html                   # SPA HTML shell
│   ├── app.js                       # Frontend JavaScript
│   └── style.css                    # Styling
│
└── __pycache__/                     # Python bytecode cache
```

---

## 2. VERSION HANDLING

**File:** `/Users/xuming/Documents/Codes/agentica/agentica/version.py`

```python
__version__ = "1.3.5"
```

**Version Usage:**
- Imported in `gateway/__init__.py`: `from agentica.version import __version__`
- Used in `main.py` FastAPI app metadata
- Displayed in startup logs
- Set during build/deployment (typically via CI/CD or VERSION file)

---

## 3. CONFIG.PY - COMPREHENSIVE BREAKDOWN

**File:** `/Users/xuming/Documents/Codes/agentica/agentica/gateway/config.py`

### Key Responsibilities
1. Load environment variables with `.env` file support
2. Define all gateway configuration with sensible defaults
3. Provide fast lookup for file extensions

### Settings Class Structure

```python
@dataclass
class Settings:
    # 🌐 SERVER CONFIG
    host: str = "0.0.0.0"
    port: int = 8789
    debug: bool = False
    gateway_token: Optional[str] = None  # Auth token; None = open (local dev)
    
    # 👤 USER MANAGEMENT
    default_user_id: str = "default"
    
    # 📁 PATHS
    workspace_path: Path = ~/.agentica/workspace    # Agent workspace
    data_dir: Path = ~/.agentica/data              # Scheduler/state storage
    base_dir: Path = $HOME                         # Agent cwd (shell tool)
    
    # 🤖 MODEL CONFIG
    model_provider: str = "zhipuai"                # LLM provider
    model_name: str = "glm-4.7-flash"              # Model identifier
    model_thinking: str = ""                       # Thinking mode (empty/enabled/disabled/auto)
    
    # 💾 AGENT CACHE
    agent_max_sessions: int = 50                   # Concurrent LRU cache size
    
    # 📤 FILE UPLOAD
    upload_max_size_mb: int = 50                   # Max upload size
    upload_allowed_extensions: str = ".txt,.md,..."  # Comma-separated list
    
    # 📊 DATA RETENTION
    job_runs_retention_days: int = 30              # Prune old scheduler runs
    
    # 🗣️ CHANNEL CONFIG (OPTIONAL)
    feishu_app_id: Optional[str] = None
    feishu_app_secret: Optional[str] = None
    feishu_allowed_users: List[str] = []
    feishu_allowed_groups: List[str] = []
    
    telegram_bot_token: Optional[str] = None
    telegram_allowed_users: List[str] = []
    
    discord_bot_token: Optional[str] = None
    discord_allowed_users: List[str] = []
    discord_allowed_guilds: List[str] = []
```

### Key Methods

#### `upload_allowed_ext_set` (Property)
```python
@property
def upload_allowed_ext_set(self) -> set[str]:
    """Return upload_allowed_extensions as lowercase set for O(1) lookup."""
    return {
        e.strip().lower()
        for e in self.upload_allowed_extensions.split(",")
        if e.strip()
    }
```
- Converts comma-separated string to set for fast file extension validation
- Cached as property

#### `from_env()` (Class Method)
```python
@classmethod
def from_env(cls) -> "Settings":
    """Load configuration from environment variables."""
    # Loads from env with fallbacks to defaults
    # Called once at module load time
```

### Environment Variables Mapping

| Env Var | Default | Type | Purpose |
|---------|---------|------|---------|
| `HOST` | 0.0.0.0 | str | Bind address |
| `PORT` | 8789 | int | Bind port |
| `DEBUG` | false | bool | Debug mode |
| `GATEWAY_TOKEN` | None | str | API auth token |
| `DEFAULT_USER_ID` | default | str | Fallback user ID |
| `AGENTICA_WORKSPACE_DIR` | ~/.agentica/workspace | path | Workspace root |
| `AGENTICA_DATA_DIR` | ~/.agentica/data | path | State storage |
| `AGENTICA_BASE_DIR` | $HOME | path | Agent cwd |
| `AGENTICA_MODEL_PROVIDER` | zhipuai | str | LLM provider |
| `AGENTICA_MODEL_NAME` | glm-4.7-flash | str | Model ID |
| `AGENTICA_MODEL_THINKING` | (empty) | str | Thinking mode |
| `AGENT_MAX_SESSIONS` | 50 | int | Cache size |
| `UPLOAD_MAX_SIZE_MB` | 50 | int | Max upload MB |
| `UPLOAD_ALLOWED_EXTENSIONS` | (csv list) | str | Allowed file types |
| `JOB_RUNS_RETENTION_DAYS` | 30 | int | Data retention |
| `FEISHU_APP_ID` | None | str | Feishu app ID |
| `FEISHU_APP_SECRET` | None | str | Feishu secret |
| `FEISHU_ALLOWED_USERS` | (csv list) | str | Allowed Feishu users |
| `FEISHU_ALLOWED_GROUPS` | (csv list) | str | Allowed Feishu groups |
| `TELEGRAM_BOT_TOKEN` | None | str | Telegram token |
| `TELEGRAM_ALLOWED_USERS` | (csv list) | str | Allowed Telegram users |
| `DISCORD_BOT_TOKEN` | None | str | Discord token |
| `DISCORD_ALLOWED_USERS` | (csv list) | str | Allowed Discord users |
| `DISCORD_ALLOWED_GUILDS` | (csv list) | str | Allowed Discord guilds |

### Instantiation
```python
# At module load time (bottom of config.py)
settings = Settings.from_env()  # Global singleton instance
```

---

## 4. SCHEDULER SUBSYSTEM - DEEP DIVE

### 4.1 Schedule Calculation (schedule.py)

**File:** `/Users/xuming/Documents/Codes/agentica/agentica/gateway/scheduler/schedule.py`

#### Core Function: `compute_next_run_at_ms()`

```python
def compute_next_run_at_ms(
    schedule: Schedule,              # Union[AtSchedule, EverySchedule, CronSchedule]
    current_ms: int | None = None,   # Current timestamp (defaults to now)
    last_run_at_ms: int | None = None,  # For interval schedules
) -> int | None:
    """Compute the next run time in milliseconds."""
    # Dispatches to appropriate handler:
    # - AtSchedule → _compute_at_next()
    # - EverySchedule → _compute_every_next()
    # - CronSchedule → _compute_cron_next()
```

#### Schedule Types

**1. AtSchedule** (One-time execution)
```python
@dataclass
class AtSchedule:
    kind: Literal["at"] = "at"
    at_ms: int = 0  # Unix timestamp in milliseconds

# Calculation logic (_compute_at_next):
# if schedule.at_ms > current_ms:
#     return schedule.at_ms  # Return target time
# return None  # Already passed
```

**2. EverySchedule** (Interval-based)
```python
@dataclass
class EverySchedule:
    kind: Literal["every"] = "every"
    interval_ms: int = 0  # Milliseconds

# Calculation logic (_compute_every_next):
# First run: return current_ms + interval_ms
# Subsequent: next_run = last_run_at_ms + interval_ms
#            while next_run <= current_ms: next_run += interval_ms
```

**3. CronSchedule** (Cron expressions)
```python
@dataclass
class CronSchedule:
    kind: Literal["cron"] = "cron"
    expression: str = ""      # "min hour day month weekday" (5-part)
                              # or "sec min hour day month weekday" (6-part)
    timezone: str = "Asia/Shanghai"

# Calculation logic (_compute_cron_next):
# 1. Try croniter library (if available)
#    - Converts ms to datetime in specified timezone
#    - Uses croniter.get_next(datetime) for next occurrence
#    - Converts back to milliseconds
# 2. Fallback: _compute_cron_fallback()
#    - Handles simple patterns manually:
#      * "0 9 * * *" → Every day at 9:00
#      * "*/30 * * * *" → Every 30 minutes
#      * "*/10 * * * * *" → Every 10 seconds (6-part)
```

#### Human-Readable Conversions

```python
def schedule_to_human(schedule: Schedule) -> str:
    """Convert schedule to Chinese description."""
    # Returns: "每天 7:30" or "每隔 5 分钟" etc.

def cron_to_human(expression: str, timezone: str) -> str:
    """Convert cron to Chinese description."""
    # "0 9 * * *" → "每天 9:00"
    # "*/30 * * * *" → "每隔 30 分钟"
    # "0 30 7 * * 1-5" → "每工作日 7:30"

def interval_to_human(interval_ms: int) -> str:
    """Convert milliseconds to Chinese description."""
    # 3600000 → "每隔 1 小时"
```

#### Validation

```python
def validate_cron_expression(expression: str) -> bool:
    """Validate cron expression (5 or 6 parts)."""
    # Returns True if valid, False otherwise
```

#### Utility Functions

```python
def now_ms() -> int:
    """Get current timestamp in milliseconds."""
    return int(time.time() * 1000)
```

---

### 4.2 Type Definitions (types.py)

**File:** `/Users/xuming/Documents/Codes/agentica/agentica/gateway/scheduler/types.py`

#### Schedule Types (Already covered above)

#### Payload Types (What the job executes)

```python
# 1. SystemEventPayload - Simple notification
@dataclass
class SystemEventPayload:
    kind: Literal["system_event"] = "system_event"
    message: str = ""
    channel: str = "telegram"
    chat_id: str = ""

# 2. AgentTurnPayload - Run agent with prompt
@dataclass
class AgentTurnPayload:
    kind: Literal["agent_turn"] = "agent_turn"
    prompt: str = ""                    # Instruction to agent
    agent_id: str = "main"
    context: dict[str, Any] = {}        # Pass context to agent
    timeout_seconds: int = 300

# 3. WebhookPayload - Call webhook
@dataclass
class WebhookPayload:
    kind: Literal["webhook"] = "webhook"
    url: str = ""
    method: str = "POST"               # GET, POST, PUT, etc.
    headers: dict[str, str] = {}
    body: dict[str, Any] = {}
    timeout_seconds: int = 30

# 4. TaskChainPayload - Trigger next job
@dataclass
class TaskChainPayload:
    kind: Literal["task_chain"] = "task_chain"
    next_job_id: str = ""               # Job to trigger next
    on_status: list[str] = ["ok"]       # Trigger conditions
```

#### Session Target Modes

```python
class SessionTargetKind(str, Enum):
    MAIN = "main"           # Inject into user's main session
    ISOLATED = "isolated"   # Run independent agent session

@dataclass
class SessionTarget:
    kind: SessionTargetKind = SessionTargetKind.ISOLATED
    trigger_heartbeat: bool = True      # For main mode
    report_to_main: bool = True         # For isolated mode
```

#### Status Enums

```python
class JobStatus(str, Enum):
    PENDING = "pending"         # Created but not armed
    ACTIVE = "active"           # Armed and will execute
    PAUSED = "paused"           # Temporarily paused
    COMPLETED = "completed"     # One-time job done
    FAILED = "failed"           # Max retries exceeded

class RunStatus(str, Enum):
    OK = "ok"               # Completed successfully
    FAILED = "failed"       # Failed with error
    SKIPPED = "skipped"     # Skipped
    TIMEOUT = "timeout"     # Timed out
```

---

### 4.3 Scheduler Service (service.py)

**File:** `/Users/xuming/Documents/Codes/agentica/agentica/gateway/scheduler/service/service.py`

#### Main SchedulerService Class

```python
class SchedulerService:
    """Unified entry point for all scheduler operations."""
    
    def __init__(
        self,
        data_dir: str | Path = "~/.agentica/data",
        executor: Any = None,
        # Dependency injection callbacks
        on_system_event: OnSystemEventCallback | None = None,
        run_heartbeat: RunHeartbeatCallback | None = None,
        report_to_main: ReportToMainCallback | None = None,
    ):
        """Initialize scheduler service."""
        self.store = JobStore(data_dir)          # YAML + SQLite persistence
        self.events = EventEmitter()             # Event system
        self.deps = SchedulerServiceDeps(executor=executor)
        self.state = SchedulerServiceState()
        
        # Callbacks for main mode
        self.on_system_event = on_system_event
        self.run_heartbeat = run_heartbeat
        self.report_to_main = report_to_main
```

#### Key Methods

```python
async def start() -> None:
    """Start scheduler service."""
    # 1. Initialize store (load YAML configs from disk)
    # 2. Prune old run history (older than job_runs_retention_days)
    # 3. Activate pending jobs (calculate next_run_at_ms)
    # 4. Start timer loop (asyncio task)

async def stop() -> None:
    """Shutdown scheduler gracefully."""

async def create_job(job_create: JobCreate) -> ScheduledJob:
    """Create new scheduled job."""

async def delete_job(user_id: str, job_id: str) -> RemoveResult:
    """Delete a job."""

async def pause_job(user_id: str, job_id: str) -> None:
    """Pause job (status → PAUSED)."""

async def resume_job(user_id: str, job_id: str) -> None:
    """Resume paused job (status → ACTIVE)."""

async def get_job(user_id: str, job_id: str) -> ScheduledJob | None:
    """Retrieve single job."""

async def list_jobs(user_id: str) -> list[ScheduledJob]:
    """List all jobs for user."""

async def get_stats() -> SchedulerStats:
    """Get global scheduler statistics."""
```

#### Storage Strategy

- **YAML configs**: `scheduler.yaml` (user-editable job definitions)
- **SQLite state**: `scheduler_state.db` (runtime state + run history)
  - Tables: `job_state`, `job_runs`, etc.

---

### 4.4 Job Executor (executor.py)

**File:** `/Users/xuming/Documents/Codes/agentica/agentica/gateway/scheduler/executor.py`

#### JobExecutor Class

```python
class JobExecutor:
    """Executes scheduled jobs."""
    
    def __init__(
        self,
        agent_runner: AgentRunner | None = None,
        # Callbacks for main mode
        on_system_event: OnSystemEventCallback | None = None,
        run_heartbeat: RunHeartbeatCallback | None = None,
        report_to_main: ReportToMainCallback | None = None,
    ):
        """Initialize executor."""
        self.agent_runner = agent_runner
        self.on_system_event = on_system_event
        self.run_heartbeat = run_heartbeat
        self.report_to_main = report_to_main
```

#### Execution Modes

**Main Mode:**
```python
async def _execute_main_mode(
    self,
    job: ScheduledJob,
    target: SessionTarget,
) -> str:
    """Execute in main session mode."""
    # 1. Build system event payload with job details
    # 2. Call on_system_event(user_id, event_data)
    #    → Injects event into user's active session
    # 3. If trigger_heartbeat: call run_heartbeat(user_id)
    #    → Triggers session heartbeat
    return "Injected to main session"
```

**Isolated Mode:**
```python
async def _execute_isolated_mode(
    self,
    job: ScheduledJob,
    target: SessionTarget,
) -> str:
    """Execute in isolated agent session mode."""
    # 1. Dispatch based on payload type:
    #    - AgentTurnPayload → _execute_agent_task()
    #    - SystemEventPayload → _execute_system_event()
    #    - WebhookPayload → _execute_webhook()
    #    - TaskChainPayload → handled by service
    #
    # 2. If report_to_main: call report_to_main(user_id, job_id, result)
    #    → Reports result back to main session
    return result
```

#### Payload-Specific Execution

```python
async def _execute_agent_task(job, payload):
    """Run agent with prompt."""
    context = {
        "job_id": job.id,
        "user_id": job.user_id,
        "scheduled": True,
        **payload.context,
    }
    return await self.agent_runner.run(
        prompt=payload.prompt,
        context=context,
    )

async def _execute_webhook(job, payload):
    """Call webhook (GET/POST/PUT)."""
    async with aiohttp.ClientSession() as session:
        # Make HTTP request to URL with optional body/headers
        # Return status or raise on 4xx/5xx

async def _execute_system_event(payload):
    """Log system event."""
    logger.info(f"System event: {payload.message}")
    return f"System event logged: {payload.message}"
```

---

### 4.5 Timer Loop (timer.py)

**File:** `/Users/xuming/Documents/Codes/agentica/agentica/gateway/scheduler/service/timer.py`

#### Timer Architecture

```python
async def arm_timer(service: SchedulerService) -> None:
    """Arm timer for next scheduled job."""
    async with service.state.lock:
        next_run_ms = await service.store.get_next_run_time()
        if next_run_ms is None:
            service.state.next_wake_at_ms = None
            return
        
        service.state.next_wake_at_ms = next_run_ms
        service.state.wake_event.set()  # Signal timer loop

async def timer_loop(service: SchedulerService) -> None:
    """Main asyncio timer loop."""
    while service.state.running:
        try:
            current_ms = now_ms()
            next_wake_ms = service.state.next_wake_at_ms
            
            if next_wake_ms is None:
                # No jobs, wait for signal (60s timeout)
                service.state.wake_event.clear()
                await asyncio.wait_for(
                    service.state.wake_event.wait(),
                    timeout=60.0,
                )
                continue
            
            # Calculate sleep duration
            sleep_ms = max(0, next_wake_ms - current_ms)
            sleep_seconds = sleep_ms / 1000.0
            
            # Sleep until next job or interrupted
            try:
                await asyncio.wait_for(
                    service.state.wake_event.wait(),
                    timeout=sleep_seconds,
                )
            except asyncio.TimeoutError:
                # Time to run jobs
                await service._run_due_jobs()  # Executes all due jobs
                await arm_timer(service)       # Re-arm for next batch
```

#### Flow Diagram

```
Timer Loop:
1. Get next_wake_ms from store
2. Calculate sleep_seconds = (next_wake_ms - now()) / 1000
3. asyncio.wait_for(wake_event.wait(), timeout=sleep_seconds)
   - If event fires early: new job added, re-arm
   - If timeout: sleep expired, run due jobs
4. Repeat
```

---

### 4.6 Agent Tools (tools.py)

**File:** `/Users/xuming/Documents/Codes/agentica/agentica/gateway/scheduler/tools.py`

#### Tool Definitions for LLM

```python
# Global singleton (initialized by gateway)
_scheduler_service: SchedulerService | None = None

def init_scheduler_tools(scheduler_service: SchedulerService) -> None:
    """Initialize tools at gateway startup."""
    global _scheduler_service
    _scheduler_service = scheduler_service
```

#### CREATE_SCHEDULED_JOB_TOOL

```python
{
    "name": "create_scheduled_job",
    "description": "创建定时任务...",  # Chinese descriptions for Chinese LLM
    "parameters": {
        "type": "object",
        "properties": {
            "name": {"type": "string", "description": "任务名称"},
            "prompt": {"type": "string", "description": "执行指令"},
            "user_id": {"type": "string"},
            # One of these:
            "cron_expression": {"type": "string"},  # "30 7 * * *"
            "interval_seconds": {"type": "integer"},  # 300
            "run_at_iso": {"type": "string"},  # "2024-01-15T09:30:00"
            "timezone": {"type": "string", "default": "Asia/Shanghai"},
        },
        "required": ["name", "prompt", "user_id"]
    }
}
```

#### LIST_SCHEDULED_JOBS_TOOL
```python
# Lists all jobs for user with schedule descriptions
```

#### DELETE_SCHEDULED_JOB_TOOL
```python
# Removes a job by ID
```

#### PAUSE_JOB_TOOL & RESUME_JOB_TOOL
```python
# Pause/resume execution without deletion
```

#### CREATE_TASK_CHAIN_TOOL
```python
# Link jobs: trigger next_job_id when current job completes
```

---

## 5. MAIN.PY - FastAPI Application Setup

**File:** `/Users/xuming/Documents/Codes/agentica/agentica/gateway/main.py`

### Lifespan Management

```python
@asynccontextmanager
async def lifespan(app: FastAPI):
    """Initialize all services on startup; clean up on shutdown."""
    
    # Startup
    logger.info(f"Agentica Gateway v{__version__}")
    logger.info(f"Model: {settings.model_provider}/{settings.model_name}")
    
    # 1. Agent Service
    agent_svc = AgentService(...)
    await agent_svc._ensure_initialized()
    deps.agent_service = agent_svc
    
    # 2. Channel Manager + Message Router
    deps.channel_manager = ChannelManager()
    deps.message_router = MessageRouter()
    
    # 3. Scheduler Setup
    agent_runner = _GatewayAgentRunner(agent_svc)
    executor = JobExecutor(agent_runner=agent_runner)
    sched = SchedulerService(
        data_dir=str(settings.data_dir),
        executor=executor,
    )
    deps.scheduler = sched
    init_scheduler_tools(sched)  # ⭐ Register scheduler tools for LLM
    
    # 4. Channels Setup
    await _setup_channels()
    
    # 5. Start Scheduler
    await sched.start()
    
    yield  # ← App runs here
    
    # Shutdown
    await deps.channel_manager.disconnect_all()
    await sched.stop()
```

### Middleware Stack

```python
# 1. CORS Middleware (allow all origins)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# 2. Request ID Middleware
# - Assigns unique ID to each request
# - Stores in ContextVar for async safety
# - Returns in X-Request-ID header

# 3. Authentication Middleware
# - Enforces gateway_token for /api/* and /ws routes
# - Skips auth if GATEWAY_TOKEN not set (local dev)
# - Accepts: Authorization: Bearer <token> or ?token=<token>
```

### Route Registration

```python
app.include_router(config_routes.router)      # /api/config/*
app.include_router(chat.router)               # /api/chat/*
app.include_router(scheduler_routes.router)   # /api/scheduler/*
app.include_router(channels.router)           # /api/channels/*
app.include_router(ws.router)                 # /ws/*
```

### Static File Serving

```python
app.mount("/static", StaticFiles(...), name="static")

@app.get("/chat", response_class=HTMLResponse)
async def web_chat():
    """Serve SPA HTML shell with no-cache headers."""
```

### _GatewayAgentRunner Adapter

```python
class _GatewayAgentRunner:
    """Adapts AgentService to AgentRunner protocol."""
    
    async def run(self, prompt: str, context: dict | None = None) -> str:
        """Run agent and return result."""
        job_id = context.get("job_id", str(uuid4()))
        user_id = context.get("user_id", settings.default_user_id)
        session_id = f"scheduled_{job_id}"
        
        result = await self._svc.chat(
            message=prompt,
            session_id=session_id,
            user_id=user_id,
        )
        return result.content
```

This adapter bridges the scheduler's `AgentRunner` protocol to the `AgentService` API, creating isolated sessions for scheduled jobs.

---

## 6. KEY IMPORTS & DEPENDENCIES

### Top-Level Imports

```python
# gateway/__init__.py
from agentica.version import __version__

# gateway/main.py
from fastapi import FastAPI, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from loguru import logger
from . import deps
from agentica.version import __version__
from .config import settings
from .services.agent_service import AgentService
from .services.channel_manager import ChannelManager
from .services.router import MessageRouter
from .scheduler import (
    SchedulerService,
    JobExecutor,
    init_scheduler_tools,
)
```

### Scheduler Module Imports

```python
# scheduler/__init__.py (exposes all public APIs)
from .types import (
    Schedule, AtSchedule, EverySchedule, CronSchedule,
    Payload, SystemEventPayload, AgentTurnPayload, WebhookPayload,
    JobStatus, RunStatus,
)
from .models import ScheduledJob, JobCreate, JobPatch
from .schedule import compute_next_run_at_ms, schedule_to_human
from .service import SchedulerService
from .executor import JobExecutor
from .tools import init_scheduler_tools, ALL_SCHEDULER_TOOLS
```

### External Dependencies

```python
croniter              # Cron expression parsing
aiohttp               # Async HTTP client (webhooks)
pydantic              # Request/response validation
fastapi + uvicorn     # Web framework
loguru                # Structured logging
dotenv                # Environment variable loading
```

---

## 7. DATA FLOW EXAMPLES

### Example 1: Creating a Scheduled Job via Agent

```
User Chat
    ↓
Agent receives prompt: "创建一个每天下午3点的任务，执行 'generate report'"
    ↓
Agent calls create_scheduled_job_tool()
    ↓
Tool Implementation:
    - Parses natural language
    - Creates JobCreate with:
      schedule=CronSchedule("0 15 * * *", timezone="Asia/Shanghai")
      payload=AgentTurnPayload(prompt="generate report")
    ↓
SchedulerService.create_job(job_create)
    ↓
Stores job in scheduler.yaml
Calculates next_run_at_ms
    ↓
Timer armed for 3:00 PM
    ↓
Timer fires at 3:00 PM
    ↓
JobExecutor.execute(job)
    ↓
Runs agent with prompt "generate report"
    ↓
Result stored in SQLite run history
    ↓
Next run calculated and timer re-armed
```

### Example 2: Timer-Triggered Execution

```
Timer Loop running in background:
    ↓
1. Check next_wake_at_ms (e.g., 1705329600000 = 2024-01-15 15:00:00)
2. Sleep until then: await wait_for(wake_event.wait(), timeout=sleep_seconds)
3. Timeout fires → Time elapsed
    ↓
4. Run due jobs: await service._run_due_jobs()
    - Query all jobs with next_run_at_ms <= now()
    - For each job:
      a. Create JobRun record
      b. Call executor.execute(job)
      c. Update job.state.last_run_at_ms, run_count, etc.
      d. Calculate next_run_at_ms
      e. Store updated state in SQLite
    ↓
5. Re-arm timer: await arm_timer(service)
    - Get next job's wake time
    - Sleep until then
    ↓
6. Loop continues
```

### Example 3: Cron Schedule Calculation

```
Job: CronSchedule(expression="30 7 * * *", timezone="Asia/Shanghai")
     (Every day at 7:30 AM Shanghai time)

Input:
- schedule: CronSchedule(...)
- current_ms: 1705318200000  (2024-01-15 12:30:00 UTC / 20:30 Shanghai)

compute_next_run_at_ms(schedule, current_ms):
    ↓
1. Create datetime in Shanghai timezone: 2024-01-15 20:30:00+08:00
2. Create croniter("30 7 * * *", current_dt)
3. croniter.get_next(datetime)
    → Returns 2024-01-16 07:30:00 (tomorrow at 7:30, since today's 7:30 passed)
4. Convert to ms: int(next_dt.timestamp() * 1000)
    → 1705381800000
    
Output: 1705381800000 (next run in ms)
```

---

## 8. CONFIGURATION EXAMPLES

### .env File Example

```bash
# Server
HOST=0.0.0.0
PORT=8789
DEBUG=false
GATEWAY_TOKEN=secret-token-123

# Paths
AGENTICA_WORKSPACE_DIR=/home/user/.agentica/workspace
AGENTICA_DATA_DIR=/home/user/.agentica/data
AGENTICA_BASE_DIR=/home/user

# Model
AGENTICA_MODEL_PROVIDER=zhipuai
AGENTICA_MODEL_NAME=glm-4.7-flash
AGENTICA_MODEL_THINKING=auto

# Scheduler
JOB_RUNS_RETENTION_DAYS=30

# Channels
FEISHU_APP_ID=abc123
FEISHU_APP_SECRET=secret456
TELEGRAM_BOT_TOKEN=7890123456:ABCDEFGHIJKLMNOpqrstuvwxyz1234567890

# Upload
UPLOAD_MAX_SIZE_MB=100
UPLOAD_ALLOWED_EXTENSIONS=.txt,.md,.py,.pdf
```

---

## 9. SUMMARY TABLE

| Component | File | Responsibility |
|-----------|------|-----------------|
| Config | `config.py` | Load/manage settings from env |
| Version | `version.py` | Semantic version string |
| Scheduler Service | `scheduler/service/service.py` | Job lifecycle management |
| Schedule Calc | `scheduler/schedule.py` | Compute next run times |
| Job Executor | `scheduler/executor.py` | Execute payloads (agent/webhook/etc) |
| Timer Loop | `scheduler/service/timer.py` | Asyncio-based job trigger |
| Persistence | `scheduler/service/store.py` | YAML + SQLite storage |
| Agent Tools | `scheduler/tools.py` | LLM-callable job tools |
| FastAPI App | `main.py` | App entry, lifespan, middleware |
| Dependencies | `deps.py` | FastAPI dependency injection |
| Channels | `channels/*.py` | Multi-channel message support |
| Routes | `routes/*.py` | HTTP endpoints for clients |

