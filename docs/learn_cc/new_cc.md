# Agentica 优化方案 -- 借鉴 Claude Code v2.1.88 源码

> 基于 Claude Code v2.1.88 反编译源码分析 + learn-claude-code 12 章教程,
> 对 Agentica 框架提出的系统性优化建议。

---

## 一、总体对比

| 维度 | Claude Code (CC) | Agentica 现状 | 差距 |
|------|------------------|---------------|------|
| Agent Loop | query.ts 68KB, 多层恢复 | runner.py 810 行, 基本循环 | 缺少 max_output_tokens 恢复、自动重试 |
| Tool 并发 | StreamingToolExecutor, 只读工具并行 | 串行执行 | 缺少并发执行策略 |
| 上下文压缩 | 三层 (micro/auto/reactive) | 单层 summarization | 缺少 micro-compact 和自动触发 |
| 任务系统 | 磁盘持久化 DAG + 6 种任务类型 | 无独立任务系统 | 完全缺失 |
| 团队协作 | JSONL 邮箱 + 协议握手 + 自治 | Swarm 并行, Team 简单委托 | 缺少持久队友、消息总线、自治循环 |
| 权限系统 | 5 种模式 + allow/deny/ask 规则 | 无 | 完全缺失 |
| Worktree 隔离 | git worktree + 任务绑定 | 无 | 完全缺失 |
| 后台任务 | 通知队列 + drain 机制 | 无 | 完全缺失 |
| Cost 追踪 | cost-tracker.ts 全链路 | 无 | 完全缺失 |
| Prompt 缓存 | fork subagent 共享父级缓存 | 无 | 完全缺失 |

---

## 二、优化方案 (按优先级排序)

### P0: Agent Loop 增强

**现状**: `runner.py` 的 `_run_impl()` 是一个基本的 while 循环, 遇到 tool_use 就执行工具、拼回结果。缺少生产级的容错和效率机制。

**借鉴 CC**: query.ts 在核心循环上包了多层防护:
- max_output_tokens 恢复: 输出被截断时自动续写
- token 预算追踪: 每轮计算 input/output token, 动态调整 max_tokens
- 自动重试: API 错误时指数退避
- 循环安全阀: 最大轮次限制, 防止无限循环

**具体改动**:

#### 2.1 max_output_tokens 续写恢复

```python
# runner.py -- _run_impl() 中
async def _run_impl(self, messages, ...):
    for turn in range(self.agent.max_turns or 50):
        response = await self.agent.model.async_response(messages, tools=...)

        # CC 模式: stop_reason == "max_tokens" 时自动续写
        if response.stop_reason == "max_tokens":
            messages.append({"role": "assistant", "content": response.content})
            messages.append({"role": "user", "content": "Continue from where you left off."})
            continue

        if response.stop_reason != "tool_use":
            break
        # ... tool execution ...
```

#### 2.2 动态 token 预算

```python
# runner.py
def _compute_max_tokens(self, messages: list, model_context_window: int) -> int:
    """CC 模式: 根据已用 token 动态调整 max_tokens"""
    used = self._estimate_tokens(messages)
    remaining = model_context_window - used
    # 保留 20% 余量给工具结果
    return min(remaining * 80 // 100, self.agent.model.max_output_tokens)
```

#### 2.3 循环安全阀 + 指数退避重试

```python
# runner.py
MAX_RETRY = 3

async def _call_model_with_retry(self, messages, tools, max_tokens):
    for attempt in range(MAX_RETRY):
        try:
            return await self.agent.model.async_response(messages, tools=tools,
                                                          max_tokens=max_tokens)
        except (RateLimitError, APIConnectionError) as e:
            if attempt == MAX_RETRY - 1:
                raise
            wait = (2 ** attempt) + random.uniform(0, 1)
            await asyncio.sleep(wait)
```

**涉及文件**: `runner.py`, `run_config.py` (新增 `max_turns`, `retry_config`)

---

### P1: 三层上下文压缩

**现状**: Agentica 的 memory 模块有 `AgentMemory` (运行时) 和 `Workspace` (持久化),
但缺少 CC 的三层渐进式压缩策略。长对话时要么全量保留 (爆 context), 要么全量摘要 (丢细节)。

**借鉴 CC (s06)**:

```
Layer 1: micro_compact  -- 每轮静默执行, 旧 tool_result 替换为占位符
Layer 2: auto_compact   -- token 超阈值时, LLM 做摘要, 全量转录存磁盘
Layer 3: manual compact -- Agent 主动调用, 与 auto 相同逻辑
```

**具体改动**:

#### 新增 `agentica/compact.py`

```python
from dataclasses import dataclass

@dataclass
class CompactConfig:
    """每层压缩的配置"""
    micro_keep_recent: int = 3          # micro-compact 保留最近 N 轮 tool_result
    auto_threshold_tokens: int = 50000  # auto-compact 触发阈值
    transcript_dir: str = ".transcripts"

class ContextCompactor:
    """三层渐进式上下文压缩"""

    def __init__(self, config: CompactConfig, model):
        self.config = config
        self.model = model

    def micro_compact(self, messages: list) -> list:
        """Layer 1: 将 >N 轮前的 tool_result 替换为占位符"""
        tool_results = []
        for i, msg in enumerate(messages):
            if msg.get("role") == "user" and isinstance(msg.get("content"), list):
                for j, part in enumerate(msg["content"]):
                    if isinstance(part, dict) and part.get("type") == "tool_result":
                        tool_results.append((i, j, part))

        if len(tool_results) <= self.config.micro_keep_recent:
            return messages

        for _, _, part in tool_results[:-self.config.micro_keep_recent]:
            content = part.get("content", "")
            if isinstance(content, str) and len(content) > 200:
                part["content"] = f"[Previous tool result truncated, {len(content)} chars]"
        return messages

    async def auto_compact(self, messages: list) -> list:
        """Layer 2: 超阈值时 LLM 摘要, 原始转录存磁盘"""
        # 存转录
        self._save_transcript(messages)
        # LLM 摘要
        summary = await self._summarize(messages)
        return [{"role": "user", "content": f"[Context compressed]\n\n{summary}"},
                {"role": "assistant", "content": "Understood. I have the conversation context. Continuing."}]

    def should_auto_compact(self, messages: list) -> bool:
        tokens = self._estimate_tokens(messages)
        return tokens > self.config.auto_threshold_tokens
```

#### 集成到 Runner

```python
# runner.py -- _run_impl()
async def _run_impl(self, messages, ...):
    compactor = ContextCompactor(self.agent.compact_config, self.agent.model)

    for turn in range(max_turns):
        # Layer 1: 每轮静默压缩旧 tool_result
        compactor.micro_compact(messages)

        # Layer 2: 超阈值自动摘要
        if compactor.should_auto_compact(messages):
            messages[:] = await compactor.auto_compact(messages)

        response = await self._call_model(messages, ...)
        # ...
```

**涉及文件**: 新增 `compact.py`, 修改 `runner.py`, `agent/config.py` (新增 `CompactConfig`)

---

### P2: Tool 并发执行

**现状**: Runner 串行执行所有 tool call。同一轮 LLM 返回 3 个 read_file, 只能等前一个完成才跑下一个。

**借鉴 CC**: `StreamingToolExecutor` 将工具分为两类:
- `isConcurrencySafe()` = True (只读工具: read_file, glob, grep, LSP) -> 并行执行
- `isConcurrencySafe()` = False (写入工具: write_file, bash, edit) -> 串行执行

**具体改动**:

```python
# tools/base.py -- Function 类新增属性
class Function(BaseModel):
    concurrency_safe: bool = False  # 只读工具标记为 True

# runner.py -- 新增并发执行逻辑
async def _execute_tool_calls(self, tool_calls: list) -> list:
    """CC 模式: 只读工具并行, 写入工具串行"""
    safe = [tc for tc in tool_calls if self._is_concurrency_safe(tc)]
    unsafe = [tc for tc in tool_calls if not self._is_concurrency_safe(tc)]

    results = []
    # 并行执行只读工具
    if safe:
        tasks = [self._execute_single_tool(tc) for tc in safe]
        results.extend(await asyncio.gather(*tasks))

    # 串行执行写入工具
    for tc in unsafe:
        results.append(await self._execute_single_tool(tc))

    return results
```

内置工具标记:

```python
# tools/buildin_tools.py
READ_FILE_FUNC.concurrency_safe = True
GLOB_FUNC.concurrency_safe = True
GREP_FUNC.concurrency_safe = True
WEB_SEARCH_FUNC.concurrency_safe = True
# write_file, edit_file, execute -> 默认 False
```

**涉及文件**: `tools/base.py`, `runner.py`, `tools/buildin_tools.py`

---

### P3: 持久化任务系统

**现状**: Agentica 没有独立的任务管理。多步骤任务靠 LLM 自己记忆, context compact 后容易丢失进度。

**借鉴 CC (s03 + s07)**:

1. **s03 TodoManager**: 内存中的待办清单, 同时只允许一个 in_progress, 3 轮不更新就注入 reminder
2. **s07 TaskManager**: 磁盘持久化的任务 DAG, 支持 blockedBy 依赖

Agentica 应直接实现 s07 级别的任务系统, 同时保留 s03 的 nag reminder 机制。

**具体改动**:

#### 新增 `agentica/task_manager.py`

```python
import json
from pathlib import Path
from dataclasses import dataclass, field

@dataclass
class TaskItem:
    id: int
    subject: str
    description: str = ""
    status: str = "pending"  # pending -> in_progress -> completed
    blocked_by: list[int] = field(default_factory=list)
    owner: str = ""

class TaskManager:
    """磁盘持久化的任务 DAG, 借鉴 CC s07"""

    def __init__(self, tasks_dir: str = ".tasks"):
        self.dir = Path(tasks_dir)
        self.dir.mkdir(exist_ok=True)
        self._next_id = self._max_id() + 1

    def create(self, subject: str, description: str = "",
               blocked_by: list[int] | None = None) -> TaskItem:
        task = TaskItem(id=self._next_id, subject=subject,
                        description=description,
                        blocked_by=blocked_by or [])
        self._save(task)
        self._next_id += 1
        return task

    def update(self, task_id: int, status: str | None = None,
               owner: str | None = None) -> TaskItem:
        task = self._load(task_id)
        if status:
            task.status = status
            if status == "completed":
                self._clear_dependency(task_id)
        if owner is not None:
            task.owner = owner
        self._save(task)
        return task

    def list_available(self) -> list[TaskItem]:
        """返回可执行的任务: pending + 无阻塞 + 无 owner"""
        return [t for t in self._all()
                if t.status == "pending" and not t.blocked_by and not t.owner]

    def _clear_dependency(self, completed_id: int):
        for task in self._all():
            if completed_id in task.blocked_by:
                task.blocked_by.remove(completed_id)
                self._save(task)
```

#### 作为内置工具注册

```python
# tools/buildin_tools.py 新增
async def task_create(subject: str, description: str = "") -> str:
    """Create a task for tracking multi-step work."""
    task = TASK_MANAGER.create(subject, description)
    return f"Created task #{task.id}: {task.subject}"

async def task_update(task_id: int, status: str) -> str:
    """Update task status (pending/in_progress/completed)."""
    task = TASK_MANAGER.update(task_id, status=status)
    return f"Task #{task.id} -> {task.status}"

async def task_list() -> str:
    """List all tasks with status."""
    ...
```

#### Nag Reminder (借鉴 s03)

```python
# runner.py
def _maybe_inject_task_reminder(self, messages: list, rounds_since_task_update: int):
    """3 轮不更新任务就注入提醒"""
    if rounds_since_task_update >= 3 and self.agent.task_manager:
        in_progress = [t for t in self.agent.task_manager._all()
                       if t.status == "in_progress"]
        if in_progress:
            reminder = f"<reminder>You have {len(in_progress)} task(s) in progress. " \
                       f"Update their status when done.</reminder>"
            # 注入到最后一条 user message
            self._inject_to_last_user_msg(messages, reminder)
```

**涉及文件**: 新增 `task_manager.py`, 修改 `runner.py`, `tools/buildin_tools.py`

---

### P4: 后台任务 + 通知队列

**现状**: 所有工具执行都是阻塞式。`npm install` 跑 2 分钟, Agent 只能等。

**借鉴 CC (s08)**: 后台线程跑耗时命令, 通知队列在每轮 LLM 调用前排空注入。

**具体改动**:

#### 新增 `agentica/background.py`

```python
import asyncio
import subprocess
from dataclasses import dataclass, field

@dataclass
class BackgroundTask:
    id: str
    command: str
    status: str = "running"  # running / completed / failed
    result: str = ""

class BackgroundManager:
    """后台任务管理, 借鉴 CC s08"""

    def __init__(self):
        self.tasks: dict[str, BackgroundTask] = {}
        self._notifications: list[dict] = []
        self._lock = asyncio.Lock()

    async def run(self, command: str) -> str:
        task_id = str(uuid.uuid4())[:8]
        task = BackgroundTask(id=task_id, command=command)
        self.tasks[task_id] = task
        asyncio.create_task(self._execute(task))
        return f"Background task {task_id} started: {command}"

    async def _execute(self, task: BackgroundTask):
        try:
            proc = await asyncio.create_subprocess_shell(
                task.command, stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE)
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=300)
            task.result = (stdout.decode() + stderr.decode()).strip()[:50000]
            task.status = "completed"
        except asyncio.TimeoutError:
            task.result = "Timeout (300s)"
            task.status = "failed"
        async with self._lock:
            self._notifications.append({"task_id": task.id, "result": task.result[:500]})

    async def drain_notifications(self) -> list[dict]:
        async with self._lock:
            notifs = self._notifications.copy()
            self._notifications.clear()
            return notifs
```

#### 集成到 Runner

```python
# runner.py -- _run_impl() 每轮开头
notifs = await self.agent.background_manager.drain_notifications()
if notifs:
    text = "\n".join(f"[bg:{n['task_id']}] {n['result']}" for n in notifs)
    messages.append({"role": "user",
                     "content": f"<background-results>\n{text}\n</background-results>"})
```

**涉及文件**: 新增 `background.py`, 修改 `runner.py`

---

### P5: 团队协作增强 -- 持久队友 + 消息总线

**现状**: `Swarm` 支持并行执行, `TeamMixin` 支持 agent-as-tool 委托, 但缺少:
- 持久化的队友生命周期 (spawn -> idle -> working -> shutdown)
- 结构化的消息总线 (JSONL 收件箱)
- 协议握手 (shutdown request/response, plan approval)
- 自治循环 (队友自动扫描任务板认领工作)

**借鉴 CC (s09-s11)**: 渐进式增强:

```
s09: 持久队友 + JSONL 邮箱
s10: shutdown/plan_approval 协议
s11: 自治循环 (idle poll -> 扫描任务板 -> 自动认领)
```

**具体改动**:

#### 5.1 消息总线 (新增 `agentica/message_bus.py`)

```python
class MessageBus:
    """JSONL 文件收件箱, 借鉴 CC s09"""

    def __init__(self, inbox_dir: str = ".team/inbox"):
        self.dir = Path(inbox_dir)
        self.dir.mkdir(parents=True, exist_ok=True)

    def send(self, sender: str, to: str, content: str,
             msg_type: str = "message", **extra):
        msg = {"type": msg_type, "from": sender, "content": content,
               "timestamp": time.time(), **extra}
        with open(self.dir / f"{to}.jsonl", "a") as f:
            f.write(json.dumps(msg) + "\n")

    def read_inbox(self, name: str) -> list[dict]:
        path = self.dir / f"{name}.jsonl"
        if not path.exists():
            return []
        msgs = [json.loads(line) for line in path.read_text().strip().splitlines() if line]
        path.write_text("")  # drain
        return msgs
```

#### 5.2 队友生命周期 (修改 `agent/team.py`)

```python
class PersistentTeammate:
    """持久化队友, 借鉴 CC s09-s11"""

    def __init__(self, name: str, role: str, agent: Agent,
                 bus: MessageBus, task_manager: TaskManager):
        self.name = name
        self.role = role
        self.agent = agent
        self.bus = bus
        self.tasks = task_manager
        self.status = "idle"  # idle / working / shutdown

    async def run_loop(self, initial_prompt: str):
        """WORK -> IDLE -> WORK 循环, 借鉴 CC s11"""
        prompt = initial_prompt
        while self.status != "shutdown":
            # -- WORK PHASE --
            self.status = "working"
            response = await self.agent.run(prompt)
            self.bus.send(self.name, "lead", str(response.content))

            # -- IDLE PHASE --
            self.status = "idle"
            resume_prompt = await self._idle_poll()
            if resume_prompt is None:
                self.status = "shutdown"
                return
            prompt = resume_prompt

    async def _idle_poll(self, timeout: int = 60, interval: int = 5) -> str | None:
        """轮询收件箱 + 任务板, 借鉴 CC s11"""
        for _ in range(timeout // interval):
            await asyncio.sleep(interval)
            # 检查收件箱
            msgs = self.bus.read_inbox(self.name)
            if msgs:
                return f"<inbox>{json.dumps(msgs)}</inbox>"
            # 扫描未认领任务
            available = self.tasks.list_available()
            if available:
                task = available[0]
                self.tasks.update(task.id, status="in_progress", owner=self.name)
                return f"<auto-claimed>Task #{task.id}: {task.subject}\n{task.description}</auto-claimed>"
        return None  # timeout -> shutdown
```

#### 5.3 身份重注入 (借鉴 s11)

```python
# 在队友的 agent loop 中, context compact 后重注入身份
def _reinject_identity(self, messages: list):
    if len(messages) <= 3:
        identity = (f"You are '{self.name}', role: {self.role}. "
                    f"You are part of a team. Continue your assigned work.")
        messages.insert(0, {"role": "user", "content": f"<identity>{identity}</identity>"})
        messages.insert(1, {"role": "assistant", "content": f"I am {self.name}. Continuing."})
```

**涉及文件**: 新增 `message_bus.py`, 重构 `agent/team.py`, 修改 `runner.py`

---

### P6: Cost 追踪

**现状**: 无任何费用追踪。用户无法知道一次 run 花了多少钱。

**借鉴 CC**: `cost-tracker.ts` 全链路追踪 input/output/cache token, 按模型计价。

**具体改动**:

#### 新增 `agentica/cost_tracker.py`

```python
from dataclasses import dataclass, field

# 主流模型的每 1M token 价格 (USD)
MODEL_PRICING = {
    "gpt-4o": {"input": 2.5, "output": 10.0},
    "gpt-4o-mini": {"input": 0.15, "output": 0.6},
    "claude-sonnet-4-20250514": {"input": 3.0, "output": 15.0},
    "claude-haiku-3.5": {"input": 0.8, "output": 4.0},
    "deepseek-chat": {"input": 0.27, "output": 1.1},
    "glm-4-flash": {"input": 0.0, "output": 0.0},
}

@dataclass
class CostTracker:
    total_input_tokens: int = 0
    total_output_tokens: int = 0
    total_cost_usd: float = 0.0
    turns: int = 0

    def record(self, model_id: str, input_tokens: int, output_tokens: int):
        self.total_input_tokens += input_tokens
        self.total_output_tokens += output_tokens
        self.turns += 1
        pricing = MODEL_PRICING.get(model_id, {"input": 0, "output": 0})
        self.total_cost_usd += (
            input_tokens * pricing["input"] / 1_000_000
            + output_tokens * pricing["output"] / 1_000_000
        )

    def summary(self) -> str:
        return (f"Turns: {self.turns} | "
                f"Tokens: {self.total_input_tokens:,} in + {self.total_output_tokens:,} out | "
                f"Cost: ${self.total_cost_usd:.4f}")
```

#### 集成到 RunResponse

```python
# run_response.py
class RunResponse:
    # ... 现有字段 ...
    input_tokens: int = 0
    output_tokens: int = 0
    cost_usd: float = 0.0
```

**涉及文件**: 新增 `cost_tracker.py`, 修改 `runner.py`, `run_response.py`

---

### P7: Prompt 缓存 + SubAgent 缓存共享

**现状**: SubAgent 每次启动都完整构建 system prompt, 无复用。

**借鉴 CC**: fork subagent 共享父级已渲染的 system prompt, 利用 API 的 prompt caching (Anthropic cache_control / OpenAI 自动前缀缓存)。

**具体改动**:

```python
# subagent.py
class SubAgent:
    async def run(self, prompt: str, parent_system_prompt: str | None = None):
        """如果提供 parent_system_prompt, 复用它而非重新构建"""
        if parent_system_prompt:
            system = parent_system_prompt  # 命中 API prompt cache
        else:
            system = self._build_system_prompt()
        # ...

# Anthropic 模型: 在 system prompt 末尾加 cache_control
# runner.py
if isinstance(self.agent.model, AnthropicChat):
    system_parts[-1]["cache_control"] = {"type": "ephemeral"}
```

**涉及文件**: `subagent.py`, `runner.py`, `model/anthropic/`

---

### P8: 权限系统

**现状**: 无权限控制。所有工具都可以直接执行, 包括 bash、write_file 等危险操作。

**借鉴 CC**: 5 种权限模式 (default, bypassPermissions, dontAsk, plan, acceptEdits),
每个工具有 `isReadOnly()` / `needsPermission()` 方法。

**简化方案** (适合 Agentica 体量):

```python
# agentica/permissions.py
from enum import Enum

class PermissionMode(Enum):
    DEFAULT = "default"          # 危险操作需确认
    BYPASS = "bypass"            # 跳过所有确认 (CI/CD)
    READ_ONLY = "read_only"     # 只允许只读工具

class PermissionManager:
    def __init__(self, mode: PermissionMode = PermissionMode.DEFAULT,
                 allow_list: list[str] | None = None,
                 deny_list: list[str] | None = None):
        self.mode = mode
        self.allow_list = set(allow_list or [])
        self.deny_list = set(deny_list or [])

    def check(self, tool_name: str, is_read_only: bool) -> bool:
        if tool_name in self.deny_list:
            return False
        if self.mode == PermissionMode.BYPASS:
            return True
        if self.mode == PermissionMode.READ_ONLY:
            return is_read_only
        if tool_name in self.allow_list:
            return True
        # DEFAULT: 只读工具自动通过, 写入工具需要外部确认
        return is_read_only
```

**涉及文件**: 新增 `permissions.py`, 修改 `runner.py`, `run_config.py`

---

### P9: Worktree 任务隔离

**现状**: Swarm 中多个 Agent 共享同一工作目录, 并发修改文件会冲突。

**借鉴 CC (s12)**: 每个任务绑定独立 git worktree, 工具执行的 cwd 指向隔离目录。

**具体改动**:

#### 新增 `agentica/worktree.py`

```python
class WorktreeManager:
    """git worktree 管理, 借鉴 CC s12"""

    def __init__(self, base_dir: str = ".worktrees"):
        self.dir = Path(base_dir)
        self.dir.mkdir(exist_ok=True)
        self.index: dict[str, dict] = self._load_index()

    def create(self, name: str, task_id: int | None = None) -> Path:
        """创建 worktree 并可选绑定任务"""
        wt_path = self.dir / name
        branch = f"wt/{name}"
        subprocess.run(["git", "worktree", "add", "-b", branch,
                        str(wt_path), "HEAD"], check=True)
        self.index[name] = {"path": str(wt_path), "branch": branch,
                            "task_id": task_id, "status": "active"}
        self._save_index()
        return wt_path

    def remove(self, name: str, complete_task: bool = False):
        wt = self.index[name]
        subprocess.run(["git", "worktree", "remove", wt["path"]], check=True)
        if complete_task and wt.get("task_id"):
            TASK_MANAGER.update(wt["task_id"], status="completed")
        wt["status"] = "removed"
        self._save_index()
```

**涉及文件**: 新增 `worktree.py`, 修改 `swarm.py` (每个 Swarm agent 分配独立 worktree)

---

### P10: Skill 触发优化 -- 两层注入

**现状**: Skill 内容在系统提示构建时全量注入 (如果匹配了 RunConfig.enabled_skills),
无论当前任务是否需要。

**借鉴 CC (s05)**: 两层注入:
- Layer 1: system prompt 只放 skill 名称 + 一行描述 (~100 tokens/skill)
- Layer 2: Agent 调用 `load_skill(name)` 时, 完整内容通过 tool_result 注入

**具体改动**:

```python
# agent/prompts.py -- 系统提示中只放目录
def _build_skill_catalog(self) -> str:
    """Layer 1: 只放名称和描述"""
    lines = ["Available skills (use load_skill tool to activate):"]
    for name, skill in self.skill_registry.items():
        lines.append(f"  - {name}: {skill.description}")
    return "\n".join(lines)

# tools/buildin_tools.py -- 新增 load_skill 工具
async def load_skill(name: str) -> str:
    """Layer 2: 按需加载完整 skill 内容"""
    skill = SKILL_REGISTRY.get(name)
    if not skill:
        return f"Unknown skill: {name}"
    return f"<skill name=\"{name}\">\n{skill.full_content}\n</skill>"
```

10 个 skill 从 20,000 token -> ~1,000 token (仅目录), 按需加载单个 ~2,000 token。

**涉及文件**: `agent/prompts.py`, `skills/`, `tools/buildin_tools.py`

---

## 三、实施路线图

```
Phase 1 (Month 1) -- 核心循环加固
  [P0] Agent Loop 增强 (max_tokens 恢复 + 重试 + 安全阀)
  [P1] 三层上下文压缩
  [P6] Cost 追踪

Phase 2 (Month 2) -- 效率提升
  [P2] Tool 并发执行
  [P7] Prompt 缓存 + SubAgent 缓存共享
  [P10] Skill 两层注入

Phase 3 (Month 3) -- 多 Agent 协作
  [P3] 持久化任务系统
  [P4] 后台任务 + 通知队列
  [P5] 团队协作增强 (消息总线 + 自治循环)

Phase 4 (Month 4) -- 安全与隔离
  [P8] 权限系统
  [P9] Worktree 任务隔离
```

---

## 四、架构演进总览

```
当前 Agentica:

  User -> Agent -> Runner (while tool_use) -> Model
                     |
                     +-> Tool (串行执行)
                     +-> Memory (单层)

目标 Agentica:

  User -> Agent -> Runner (增强循环) -> Model
                     |
                     +-> ToolExecutor (并发/串行分流)
                     +-> ContextCompactor (三层压缩)
                     +-> TaskManager (磁盘 DAG)
                     +-> BackgroundManager (通知队列)
                     +-> CostTracker (全链路计费)
                     +-> PermissionManager (权限门控)
                     |
                     +-> PersistentTeammate (自治循环)
                           +-> MessageBus (JSONL 邮箱)
                           +-> WorktreeManager (目录隔离)
```

---

## 五、关键设计原则 (来自 CC 源码的启示)

1. **循环不变**: 所有增强都是在 `while tool_use` 循环外层叠加机制, 循环核心永远不变。
   CC 12 章教程的核心信条: "One loop & Bash is all you need" -- 循环是 Agent 的心跳。

2. **工具即扩展点**: 新能力 = 新工具 + handler, 不需要改循环。CC 用 dispatch map,
   Agentica 用 registry, 模式相同。Task, Compact, Background 都是工具。

3. **磁盘即持久化**: 上下文会被压缩, 但磁盘上的 .tasks/, .transcripts/, .team/ 不会。
   CC 的核心哲学: "会话记忆是易失的; 磁盘状态是持久的。"

4. **两层注入节省 token**: 系统提示放目录 (便宜), tool_result 放内容 (按需)。
   适用于 Skill, 也适用于 RAG context、长文档等。

5. **并发分流而非全量并发**: 不是所有工具都能并行, 只有只读工具可以。
   `concurrency_safe` 标记比全局锁更精准。

6. **自治 > 指派**: 队友自己扫描任务板认领工作, 比领导逐一分配扩展性更好。
   但需要 nag reminder 保持问责 + 身份重注入保持连贯。
