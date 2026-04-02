# Agentica 优化方案 v2 -- 基于源码深度分析的精准建议

> 对 Claude Code v2.1.88 源码进行深度逆向分析后，对 Agentica 提出的务实优化方案。
> 相比 v1 方案，本版更注重实现成本与收益的平衡，避免过度工程。

---

## 一、核心发现：Claude Code 的真实架构

### 1.1 Agent Loop：状态机而非简单循环

Claude Code 的 `query.ts` 是一个**9 个 continue sites 的状态机**，而非简单的 while 循环：

```typescript
type State = {
  messages: Message[]
  toolUseContext: ToolUseContext
  autoCompactTracking: AutoCompactTrackingState | undefined
  maxOutputTokensRecoveryCount: number
  hasAttemptedReactiveCompact: boolean
  maxOutputTokensOverride: number | undefined
  pendingToolUseSummary: Promise<ToolUseSummaryMessage | null> | undefined
  stopHookActive: boolean | undefined
  turnCount: number
  transition: Continue | undefined  // 上一轮为何继续
}

while (true) {
  let { toolUseContext } = state
  const { messages, autoCompactTracking, maxOutputTokensRecoveryCount, ... } = state
  
  // 9 个 continue sites:
  // 1. max_output_tokens recovery
  // 2. auto-compact
  // 3. tool execution
  // 4. user interruption
  // 5. reactive compact
  // 6. streaming fallback
  // 7. error retry
  // 8. task budget exhausted
  // 9. max turns reached
  
  yield { type: 'stream_request_start' }
  // ... 复杂的状态转换逻辑
}
```

**Agentica 现状**: `runner.py` 的 `_run_impl()` 是简单的 while 循环，缺少状态管理和多路径恢复。

### 1.2 Tool Execution：智能并发而非简单并行

Claude Code 的 `StreamingToolExecutor` 实现了**分层并发策略**：

```typescript
class StreamingToolExecutor {
  // 关键属性
  private tools: TrackedTool[]  // 每个 tool 有 status 和 isConcurrencySafe
  
  // 并发判断逻辑
  private canExecuteTool(isConcurrencySafe: boolean): boolean {
    const executingTools = this.tools.filter(t => t.status === 'executing')
    return (
      executingTools.length === 0 ||
      (isConcurrencySafe && executingTools.every(t => t.isConcurrencySafe))
    )
  }
  
  // Bash 错误会取消其他并行工具（sibling error）
  if (tool.block.name === BASH_TOOL_NAME) {
    this.hasErrored = true
    this.siblingAbortController.abort('sibling_error')
  }
}
```

**关键特性**：
1. **concurrency_safe 标记**: 只读工具可以并行，写入工具串行
2. **Sibling Error 处理**: Bash 错误会取消其他并行工具（因为命令间可能有隐式依赖）
3. **用户中断处理**: 支持 interrupt behavior (cancel/block)
4. **进度消息**: 立即 yield，不等待工具完成

**Agentica 现状**: 所有工具串行执行，缺少并发策略。

### 1.3 Context Compression：三层而非单层

Claude Code 实现了**三层渐进式压缩**：

#### Layer 1: Micro-compact (每轮静默)

```typescript
// Time-based trigger: 间隔超过阈值就清理
function evaluateTimeBasedTrigger(messages, querySource) {
  const config = getTimeBasedMCConfig()
  const lastAssistant = messages.findLast(m => m.type === 'assistant')
  const gapMinutes = (Date.now() - new Date(lastAssistant.timestamp).getTime()) / 60_000
  if (gapMinutes < config.gapThresholdMinutes) return null
  return { gapMinutes, config }
}

// Cached MC: 使用 cache editing API 不破坏缓存
async function cachedMicrocompactPath(messages) {
  const toolsToDelete = mod.getToolResultsToDelete(state)
  if (toolsToDelete.length > 0) {
    const cacheEdits = mod.createCacheEditsBlock(state, toolsToDelete)
    pendingCacheEdits = cacheEdits  // 交给 API 层处理
    return { messages, compactionInfo: { pendingCacheEdits } }
  }
  return { messages }
}
```

#### Layer 2: Auto-compact (超阈值 LLM 摘要)

```typescript
// 智能触发判断
export function calculateTokenWarningState(tokenUsage, model) {
  const autoCompactThreshold = getAutoCompactThreshold(model)
  const warningThreshold = threshold - WARNING_THRESHOLD_BUFFER_TOKENS
  const errorThreshold = threshold - ERROR_THRESHOLD_BUFFER_TOKENS
  
  return {
    percentLeft,
    isAboveWarningThreshold,
    isAboveErrorThreshold,
    isAboveAutoCompactThreshold,
    isAtBlockingLimit
  }
}

// Session memory compaction（优先于 LLM 摘要）
const sessionMemoryResult = await trySessionMemoryCompaction(messages, ...)
if (sessionMemoryResult) {
  return { wasCompacted: true, compactionResult: sessionMemoryResult }
}

// 传统 LLM 摘要
const compactionResult = await compactConversation(messages, ...)
```

#### Layer 3: Reactive compact (prompt_too_long 时响应式压缩)

```typescript
// API 返回 prompt_too_long 时的应急压缩
if (feature('REACTIVE_COMPACT')) {
  const reactiveCompact = require('./services/compact/reactiveCompact.js')
  // ... 执行紧急压缩
}
```

**关键特性**：
- **Circuit Breaker**: 连续失败 3 次后停止尝试（避免浪费 API 调用）
- **Buffer Tokens**: 预留 13K tokens 缓冲（AUTOCOMPACT_BUFFER_TOKENS）
- **Session Memory**: 优先尝试 session memory compaction（更轻量）
- **Cache Editing**: 使用 API 的 cache_edits 功能避免破坏缓存

**Agentica 现状**: 只有单层 summarization，缺少渐进式策略。

### 1.4 Task System：统一抽象而非独立实现

Claude Code 的 Task 系统是**统一抽象**：

```typescript
export type TaskType =
  | 'local_bash'
  | 'local_agent'
  | 'remote_agent'
  | 'in_process_teammate'
  | 'local_workflow'
  | 'monitor_mcp'
  | 'dream'

export type TaskStatus = 'pending' | 'running' | 'completed' | 'failed' | 'killed'

export type TaskStateBase = {
  id: string
  type: TaskType
  status: TaskStatus
  description: string
  toolUseId?: string
  startTime: number
  endTime?: number
  totalPausedMs?: number
  outputFile: string       // 磁盘持久化输出
  outputOffset: number
  notified: boolean
}
```

**关键特性**：
- **统一 ID 生成**: `generateTaskId(type)` 生成前缀 + 随机字符串
- **磁盘输出**: 每个任务有独立的 output file
- **状态机**: pending -> running -> completed/failed/killed
- **通知机制**: `notified` 字段标记是否已通知用户

**Agentica 现状**: 无独立任务系统，依靠 LLM 自行记忆。

### 1.5 Message Queue：统一队列而非分散管理

Claude Code 使用**单一命令队列**管理所有异步事件：

```typescript
const commandQueue: QueuedCommand[] = []

export type QueuePriority = 'now' | 'next' | 'later'

// 优先级队列
export function dequeue(filter?: (cmd: QueuedCommand) => boolean) {
  let bestIdx = -1
  let bestPriority = Infinity
  for (let i = 0; i < commandQueue.length; i++) {
    const cmd = commandQueue[i]!
    if (filter && !filter(cmd)) continue
    const priority = PRIORITY_ORDER[cmd.priority ?? 'next']
    if (priority < bestPriority) {
      bestIdx = i
      bestPriority = priority
    }
  }
  // ... dequeue
}
```

**关键特性**：
- **统一队列**: 用户输入、任务通知、权限请求都走同一队列
- **优先级**: 'now' > 'next' > 'later'（用户输入优先级更高）
- **React 集成**: 通过 `useSyncExternalStore` 订阅队列变化
- **日志记录**: 每个操作记录到 sessionStorage

**Agentica 现状**: 无统一队列，事件处理分散。

### 1.6 Cost Tracker：全链路追踪

Claude Code 实现了**细粒度的成本追踪**：

```typescript
export function addToTotalSessionCost(cost: number, usage: Usage, model: string) {
  const modelUsage = addToTotalModelUsage(cost, usage, model)
  addToTotalCostState(cost, modelUsage, model)
  
  // 监控指标
  getCostCounter()?.add(cost, { model })
  getTokenCounter()?.add(usage.input_tokens, { model, type: 'input' })
  getTokenCounter()?.add(usage.output_tokens, { model, type: 'output' })
  getTokenCounter()?.add(usage.cache_read_input_tokens ?? 0, { model, type: 'cacheRead' })
  
  // Advisor 追踪
  for (const advisorUsage of getAdvisorUsage(usage)) {
    const advisorCost = calculateUSDCost(advisorUsage.model, advisorUsage)
    logEvent('tengu_advisor_tool_token_usage', { ... })
    totalCost += addToTotalSessionCost(advisorCost, advisorUsage, advisorUsage.model)
  }
  
  return totalCost
}
```

**关键特性**：
- **模型级追踪**: 按模型统计 input/output/cache read/cache creation
- **会话持久化**: 保存到 projectConfig，支持会话恢复
- **Advisor 追踪**: 单独追踪 advisor tools 的成本
- **未知模型告警**: `hasUnknownModelCost()` 标记定价缺失的模型

**Agentica 现状**: 无成本追踪。

---

## 二、优化建议（按 ROI 排序）

### P0: Tool 并发执行（高收益，中等成本）

**收益**: 同一轮读取 3 个文件，从串行 3s -> 并行 1s（3x 加速）。

**现状**: Agentica 所有工具串行执行。

**方案**:

#### 1. 工具标记

```python
# tools/base.py
from pydantic import BaseModel

class Function(BaseModel):
    name: str
    description: str
    parameters: dict
    entrypoint: Callable
    concurrency_safe: bool = False  # 新增标记
```

#### 2. 内置工具标记

```python
# tools/buildin_tools.py
READ_FILE_FUNC.concurrency_safe = True
GLOB_FUNC.concurrency_safe = True
GREP_FUNC.concurrency_safe = True
WEB_SEARCH_FUNC.concurrency_safe = True
WEB_FETCH_FUNC.concurrency_safe = True
# BashTool, WriteFile, EditFile -> 默认 False
```

#### 3. Runner 并发执行

```python
# runner.py
async def _execute_tool_calls(self, tool_calls: list[ToolCall]) -> list[ToolResult]:
    """智能并发：只读工具并行，写入工具串行"""
    
    # 分组
    safe_calls = [tc for tc in tool_calls if tc.function.concurrency_safe]
    unsafe_calls = [tc for tc in tool_calls if not tc.function.concurrency_safe]
    
    results = []
    
    # 并行执行只读工具
    if safe_calls:
        tasks = [self._execute_single_tool(tc) for tc in safe_calls]
        results.extend(await asyncio.gather(*tasks, return_exceptions=True))
    
    # 串行执行写入工具
    for tc in unsafe_calls:
        try:
            result = await self._execute_single_tool(tc)
            results.append(result)
        except Exception as e:
            # Bash 错误：取消后续 unsafe 工具（Claude Code 模式）
            if tc.function.name == "execute":
                break
            results.append(ToolResult(error=str(e)))
    
    return results
```

**实现成本**: ~300 行代码修改。

**测试要点**:
- 同一轮 3 个 read_file 是否并行
- Bash 错误是否取消后续工具
- 并发工具的结果顺序是否正确

---

### P1: Cost Tracker（高收益，低成本）

**收益**: 用户实时了解花费，避免超预算。

**现状**: 无任何费用追踪。

**方案**:

```python
# agentica/cost_tracker.py
from dataclasses import dataclass, field
from typing import Dict

# 主流模型定价（$/1M tokens）
MODEL_PRICING = {
    "gpt-4o": {"input": 2.5, "output": 10.0, "cache_read": 1.25, "cache_write": 2.5},
    "gpt-4o-mini": {"input": 0.15, "output": 0.6, "cache_read": 0.075, "cache_write": 0.15},
    "claude-sonnet-4-20250514": {"input": 3.0, "output": 15.0, "cache_read": 0.30, "cache_write": 3.75},
    "claude-haiku-3.5": {"input": 0.8, "output": 4.0, "cache_read": 0.08, "cache_write": 1.0},
    "deepseek-chat": {"input": 0.27, "output": 1.1, "cache_read": 0.07, "cache_write": 0.27},
    "glm-4-flash": {"input": 0.0, "output": 0.0, "cache_read": 0.0, "cache_write": 0.0},
}

@dataclass
class ModelUsage:
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0
    cost_usd: float = 0.0

@dataclass
class CostTracker:
    total_cost_usd: float = 0.0
    total_duration_ms: int = 0
    total_tool_duration_ms: int = 0
    model_usage: Dict[str, ModelUsage] = field(default_factory=dict)
    turns: int = 0
    unknown_model: bool = False
    
    def record(self, model: str, usage: dict):
        """记录一次 API 调用的费用"""
        pricing = MODEL_PRICING.get(model)
        if not pricing:
            self.unknown_model = True
            return
        
        model_usage = self.model_usage.setdefault(model, ModelUsage())
        model_usage.input_tokens += usage.get("input_tokens", 0)
        model_usage.output_tokens += usage.get("output_tokens", 0)
        model_usage.cache_read_tokens += usage.get("cache_read_tokens", 0)
        model_usage.cache_write_tokens += usage.get("cache_write_tokens", 0)
        
        cost = (
            usage.get("input_tokens", 0) * pricing["input"] / 1_000_000 +
            usage.get("output_tokens", 0) * pricing["output"] / 1_000_000 +
            usage.get("cache_read_tokens", 0) * pricing["cache_read"] / 1_000_000 +
            usage.get("cache_write_tokens", 0) * pricing["cache_write"] / 1_000_000
        )
        
        model_usage.cost_usd += cost
        self.total_cost_usd += cost
        self.turns += 1
    
    def summary(self) -> str:
        """格式化成本摘要"""
        lines = [f"Total cost: ${self.total_cost_usd:.4f}"]
        
        if self.model_usage:
            lines.append("\nUsage by model:")
            for model, usage in self.model_usage.items():
                lines.append(
                    f"  {model}:\n"
                    f"    {usage.input_tokens:,} input, "
                    f"{usage.output_tokens:,} output, "
                    f"{usage.cache_read_tokens:,} cache read, "
                    f"{usage.cache_write_tokens:,} cache write "
                    f"(${usage.cost_usd:.4f})"
                )
        
        if self.unknown_model:
            lines.append("\n⚠️  Unknown model costs not included")
        
        return "\n".join(lines)
```

**集成到 RunResponse**:

```python
# run_response.py
@dataclass
class RunResponse:
    content: str
    messages: list
    # ... 现有字段
    cost_tracker: CostTracker = field(default_factory=CostTracker)
    
    def print_summary(self):
        """打印运行摘要"""
        print(self.cost_tracker.summary())
```

**集成到 Runner**:

```python
# runner.py
async def _call_model(self, messages, tools, **kwargs):
    response = await self.agent.model.async_response(messages, tools=tools, **kwargs)
    
    # 记录费用
    if hasattr(response, 'usage'):
        self.cost_tracker.record(
            model=self.agent.model.model_id,
            usage={
                "input_tokens": getattr(response.usage, 'input_tokens', 0),
                "output_tokens": getattr(response.usage, 'output_tokens', 0),
                "cache_read_tokens": getattr(response.usage, 'cache_read_input_tokens', 0),
                "cache_write_tokens": getattr(response.usage, 'cache_creation_input_tokens', 0),
            }
        )
    
    return response
```

**实现成本**: ~200 行代码新增。

---

### P2: Micro-compact（高收益，中等成本）

**收益**: 长对话时节省大量 token，避免爆 context。

**现状**: 只有 auto-compact，缺少静默压缩。

**方案**:

```python
# agentica/compact.py
from dataclasses import dataclass
from typing import List, Tuple
import time

@dataclass
class MicroCompactConfig:
    enabled: bool = True
    keep_recent: int = 3  # 保留最近 N 轮 tool_result
    time_gap_minutes: int = 5  # 时间间隔阈值
    max_tool_result_chars: int = 200  # 单个结果最大字符数

class MicroCompactor:
    """静默压缩：每轮清理旧的 tool_result"""
    
    def __init__(self, config: MicroCompactConfig):
        self.config = config
        self._last_compact_time = 0
    
    def should_compact(self, messages: list, query_source: str = None) -> bool:
        """判断是否需要压缩"""
        if not self.config.enabled:
            return False
        
        # 子 agent 不压缩（避免死锁）
        if query_source in ['session_memory', 'compact', 'subagent']:
            return False
        
        # 检查时间间隔
        last_asst = self._find_last_assistant(messages)
        if last_asst:
            gap = (time.time() - last_asst.timestamp) / 60
            if gap >= self.config.time_gap_minutes:
                return True
        
        return False
    
    def compact(self, messages: list) -> Tuple[list, int]:
        """执行压缩，返回 (新消息列表, 节省的 token 数)"""
        if not self.should_compact(messages):
            return messages, 0
        
        # 收集所有 tool_result
        tool_results = self._collect_tool_results(messages)
        
        if len(tool_results) <= self.config.keep_recent:
            return messages, 0
        
        # 截断旧的 tool_result
        tokens_saved = 0
        for msg_idx, block_idx, block in tool_results[:-self.config.keep_recent]:
            content = block.get("content", "")
            if isinstance(content, str) and len(content) > self.config.max_tool_result_chars:
                old_len = len(content)
                block["content"] = f"[Old tool result cleared, {old_len} chars]"
                tokens_saved += old_len // 4  # 粗略估计
        
        return messages, tokens_saved
    
    def _find_last_assistant(self, messages: list):
        """找到最后一个 assistant 消息"""
        for msg in reversed(messages):
            if msg.get("role") == "assistant":
                return msg
        return None
    
    def _collect_tool_results(self, messages: list) -> List[Tuple[int, int, dict]]:
        """收集所有 tool_result 的位置"""
        results = []
        for msg_idx, msg in enumerate(messages):
            if msg.get("role") == "user" and isinstance(msg.get("content"), list):
                for block_idx, block in enumerate(msg["content"]):
                    if isinstance(block, dict) and block.get("type") == "tool_result":
                        results.append((msg_idx, block_idx, block))
        return results
```

**集成到 Runner**:

```python
# runner.py
async def _run_impl(self, messages, ...):
    compactor = MicroCompactor(self.agent.micro_compact_config)
    
    for turn in range(max_turns):
        # 每轮开头执行 micro-compact
        messages, tokens_saved = compactor.compact(messages)
        if tokens_saved > 0:
            logger.debug(f"Micro-compact saved {tokens_saved} tokens")
        
        # ... 正常的 agent loop
```

**实现成本**: ~250 行代码新增。

---

### P3: Agent Loop 状态管理（中等收益，高成本）

**收益**: 支持 max_output_tokens 恢复、自动重试、更健壮的循环。

**现状**: 简单 while 循环，缺少状态管理。

**方案**:

```python
# runner.py
from dataclasses import dataclass
from enum import Enum
from typing import Optional

class Transition(Enum):
    CONTINUE = "continue"
    MAX_TOKENS_RECOVERY = "max_tokens_recovery"
    AUTO_COMPACT = "auto_compact"
    TOOL_EXECUTION = "tool_execution"
    ERROR_RETRY = "error_retry"
    TERMINAL = "terminal"

@dataclass
class LoopState:
    """Agent loop 的跨轮次状态"""
    messages: list
    turn_count: int = 0
    max_tokens_recovery_count: int = 0
    consecutive_errors: int = 0
    last_transition: Optional[Transition] = None
    
    # 配置
    max_turns: int = 50
    max_tokens_recovery_limit: int = 3
    max_consecutive_errors: int = 3

async def _run_impl(self, messages: list, **kwargs):
    state = LoopState(messages=messages)
    
    while True:
        # 解构状态
        messages = state.messages
        state.turn_count += 1
        
        # 循环安全阀
        if state.turn_count > state.max_turns:
            logger.warning(f"Max turns ({state.max_turns}) reached")
            break
        
        # API 调用（带重试）
        try:
            response = await self._call_model_with_retry(messages, **kwargs)
            state.consecutive_errors = 0  # 重置错误计数
        except Exception as e:
            state.consecutive_errors += 1
            if state.consecutive_errors >= state.max_consecutive_errors:
                logger.error(f"Consecutive errors ({state.max_consecutive_errors}), aborting")
                raise
            state.last_transition = Transition.ERROR_RETRY
            continue
        
        # max_output_tokens 恢复
        if response.stop_reason == "max_tokens":
            if state.max_tokens_recovery_count >= state.max_tokens_recovery_limit:
                logger.warning("Max tokens recovery limit reached")
                break
            
            state.max_tokens_recovery_count += 1
            state.last_transition = Transition.MAX_TOKENS_RECOVERY
            messages.append({"role": "assistant", "content": response.content})
            messages.append({"role": "user", "content": "Continue from where you left off."})
            state.messages = messages
            continue
        
        # 正常结束
        if response.stop_reason != "tool_use":
            state.last_transition = Transition.TERMINAL
            break
        
        # 工具执行
        state.last_transition = Transition.TOOL_EXECUTION
        tool_results = await self._execute_tool_calls(response.tool_calls)
        messages = self._append_tool_results(messages, response, tool_results)
        state.messages = messages
        
        # Auto-compact 检查
        if self._should_auto_compact(messages):
            messages = await self._auto_compact(messages)
            state.messages = messages
            state.last_transition = Transition.AUTO_COMPACT
    
    return self._build_response(state)
```

**实现成本**: ~400 行代码重构。

**注意**: 这是**高风险改动**，需要充分测试。

---

### P4: 简化版任务系统（中等收益，中等成本）

**收益**: 多步骤任务不丢失进度，context compact 后也能恢复。

**现状**: 无任务系统。

**方案**: **简化版**，只实现核心功能（不需要 DAG）。

```python
# agentica/task.py
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
import json
from typing import List, Optional

class TaskStatus(Enum):
    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    CANCELLED = "cancelled"

@dataclass
class Task:
    id: int
    subject: str
    description: str = ""
    status: TaskStatus = TaskStatus.PENDING
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)

class TaskManager:
    """简化版任务管理器（无 DAG，无依赖）"""
    
    def __init__(self, tasks_file: str = ".agentica/tasks.json"):
        self.tasks_file = Path(tasks_file)
        self.tasks_file.parent.mkdir(exist_ok=True)
        self.tasks: Dict[int, Task] = self._load()
        self._next_id = max(self.tasks.keys(), default=0) + 1
    
    def create(self, subject: str, description: str = "") -> Task:
        """创建新任务"""
        task = Task(id=self._next_id, subject=subject, description=description)
        self.tasks[task.id] = task
        self._next_id += 1
        self._save()
        return task
    
    def update(self, task_id: int, status: TaskStatus = None) -> Optional[Task]:
        """更新任务状态"""
        if task_id not in self.tasks:
            return None
        
        task = self.tasks[task_id]
        if status:
            task.status = status
        task.updated_at = time.time()
        self._save()
        return task
    
    def list(self, status: TaskStatus = None) -> List[Task]:
        """列出任务"""
        if status:
            return [t for t in self.tasks.values() if t.status == status]
        return list(self.tasks.values())
    
    def get_in_progress(self) -> Optional[Task]:
        """获取当前进行中的任务"""
        for task in self.tasks.values():
            if task.status == TaskStatus.IN_PROGRESS:
                return task
        return None
    
    def _load(self) -> Dict[int, Task]:
        """从磁盘加载"""
        if not self.tasks_file.exists():
            return {}
        
        data = json.loads(self.tasks_file.read_text())
        return {
            int(k): Task(id=v["id"], subject=v["subject"], 
                         description=v["description"], status=TaskStatus(v["status"]),
                         created_at=v["created_at"], updated_at=v["updated_at"])
            for k, v in data.items()
        }
    
    def _save(self):
        """保存到磁盘"""
        data = {
            t.id: {
                "id": t.id,
                "subject": t.subject,
                "description": t.description,
                "status": t.status.value,
                "created_at": t.created_at,
                "updated_at": t.updated_at,
            }
            for t in self.tasks.values()
        }
        self.tasks_file.write_text(json.dumps(data, indent=2))
```

**作为内置工具注册**:

```python
# tools/buildin_tools.py
from agentica.task import TaskManager, TaskStatus

# 全局单例
_TASK_MANAGER = None

def get_task_manager() -> TaskManager:
    global _TASK_MANAGER
    if _TASK_MANAGER is None:
        _TASK_MANAGER = TaskManager()
    return _TASK_MANAGER

@tool
def task_create(subject: str, description: str = "") -> str:
    """创建新任务"""
    tm = get_task_manager()
    task = tm.create(subject, description)
    return f"Created task #{task.id}: {task.subject}"

@tool
def task_update(task_id: int, status: str) -> str:
    """更新任务状态 (pending/in_progress/completed/cancelled)"""
    tm = get_task_manager()
    task = tm.update(task_id, status=TaskStatus(status))
    if not task:
        return f"Task #{task_id} not found"
    return f"Task #{task.id} -> {task.status.value}"

@tool
def task_list(status: str = None) -> str:
    """列出所有任务"""
    tm = get_task_manager()
    tasks = tm.list(TaskStatus(status) if status else None)
    if not tasks:
        return "No tasks"
    return "\n".join(
        f"#{t.id} [{t.status.value}] {t.subject}"
        for t in tasks
    )
```

**Nag Reminder（3 轮不更新就提醒）**:

```python
# runner.py
async def _run_impl(self, messages, ...):
    rounds_since_task_update = 0
    
    for turn in range(max_turns):
        # ... agent loop
        
        # Nag reminder
        tm = get_task_manager()
        in_progress = tm.get_in_progress()
        if in_progress:
            rounds_since_task_update += 1
            if rounds_since_task_update >= 3:
                reminder = (
                    f"<reminder>You have task #{in_progress.id} in progress. "
                    f"Update its status when done or if blocked.</reminder>"
                )
                # 注入到最后一条 user message
                self._inject_reminder(messages, reminder)
                rounds_since_task_update = 0
        else:
            rounds_since_task_update = 0
```

**实现成本**: ~300 行代码新增。

---

### P5: 统一消息队列（中等收益，中等成本）

**收益**: 统一管理用户输入、后台任务通知、权限请求。

**现状**: 无统一队列。

**方案**:

```python
# agentica/queue.py
from dataclasses import dataclass
from enum import Enum
from typing import Any, Callable, Optional
import asyncio
from collections import deque

class Priority(Enum):
    NOW = 0      # 最高优先级（用户中断）
    NEXT = 1     # 正常优先级（用户输入）
    LATER = 2    # 低优先级（任务通知）

@dataclass
class QueuedCommand:
    value: Any
    priority: Priority = Priority.NEXT
    command_type: str = "prompt"  # prompt, notification, permission
    metadata: dict = None

class CommandQueue:
    """统一命令队列"""
    
    def __init__(self):
        self._queue: deque[QueuedCommand] = deque()
        self._lock = asyncio.Lock()
        self._notifier = asyncio.Condition()
    
    async def enqueue(self, command: QueuedCommand):
        """入队"""
        async with self._lock:
            self._queue.append(command)
            async with self._notifier:
                self._notifier.notify()
    
    async def dequeue(self, filter_fn: Callable = None) -> Optional[QueuedCommand]:
        """出队（带优先级）"""
        async with self._lock:
            if not self._queue:
                return None
            
            # 找最高优先级
            best_idx = -1
            best_priority = Priority.LATER
            for i, cmd in enumerate(self._queue):
                if filter_fn and not filter_fn(cmd):
                    continue
                if cmd.priority.value < best_priority.value:
                    best_idx = i
                    best_priority = cmd.priority
            
            if best_idx == -1:
                return None
            
            return self._queue[best_idx]
    
    async def wait_for_command(self, timeout: float = None) -> Optional[QueuedCommand]:
        """等待命令（阻塞）"""
        async with self._notifier:
            await asyncio.wait_for(self._notifier.wait(), timeout)
        
        return await self.dequeue()
    
    def __len__(self):
        return len(self._queue)
```

**使用示例**:

```python
# CLI 集成
queue = CommandQueue()

# 用户输入
await queue.enqueue(QueuedCommand(
    value="Help me refactor this code",
    priority=Priority.NEXT,
    command_type="prompt"
))

# 后台任务通知
await queue.enqueue(QueuedCommand(
    value="[Task #123] npm install completed",
    priority=Priority.LATER,
    command_type="notification"
))

# Runner 消费
async def run_loop(self):
    while True:
        # 优先处理用户输入
        cmd = await queue.dequeue(filter_fn=lambda c: c.priority == Priority.NEXT)
        if cmd:
            await self._handle_prompt(cmd.value)
        
        # 然后处理通知
        cmd = await queue.dequeue()
        if cmd:
            await self._handle_notification(cmd.value)
```

**实现成本**: ~200 行代码新增。

---

## 三、不建议实现的特性（ROI 低或过度工程）

### ❌ Worktree 任务隔离

**理由**:
- Claude Code 用 worktree 是因为多 agent 并发修改同一 repo
- Agentica 的 Swarm 量级远小于 CC
- 实现成本高（需要 git 操作、进程隔离）
- **ROI 极低**

**替代方案**: 在文档中建议用户在不同目录运行多个 agent。

### ❌ 三层压缩 + Session Memory

**理由**:
- Claude Code 的 session memory compaction 是实验性功能
- 依赖 Anthropic 的缓存机制，Agentica 支持多模型
- 实现成本高，需要额外存储层
- **过度工程**

**替代方案**: 只实现 micro-compact（P2）和 auto-compact（现有）。

### ❌ Reactive Compact

**理由**:
- 只在 API 返回 `prompt_too_long` 时触发
- Agentica 已经有 auto-compact
- 实现成本高（需要捕获 API 错误并恢复）
- **边际收益低**

**替代方案**: 通过更激进的 auto-compact 阈值避免 `prompt_too_long`。

### ❌ Prompt Cache 共享

**理由**:
- 依赖 Anthropic 的 cache_control
- Agentica 支持多模型，无法统一实现
- OpenAI 的自动前缀缓存不需要显式标记
- **模型绑定**

**替代方案**: 在文档中说明 Anthropic 模型的缓存最佳实践。

### ❌ 完整权限系统

**理由**:
- Claude Code 的 5 种权限模式适合 IDE 集成
- Agentica 主要是编程框架，用户自己控制权限
- 实现成本高
- **超出 scope**

**替代方案**: 提供 `read_only` 模式配置（见 P4）。

---

## 四、实施路线图（4 个月）

### Month 1: 基础设施（P0 + P1）

**Week 1-2: Tool 并发执行**
- 实现 `concurrency_safe` 标记
- 修改 Runner 执行逻辑
- 测试并发场景

**Week 3-4: Cost Tracker**
- 实现 CostTracker 类
- 集成到 Runner 和 RunResponse
- 添加 CLI 显示

**交付物**:
- Tool 并发执行
- 实时成本显示

### Month 2: 上下文管理（P2）

**Week 1-3: Micro-compact**
- 实现 MicroCompactor
- 时间触发和计数触发
- 集成到 Runner

**Week 4: Auto-compact 增强**
- 调整阈值（参考 CC 的 13K buffer）
- 添加 circuit breaker

**交付物**:
- Micro-compact 自动触发
- 更健壮的 auto-compact

### Month 3: 状态管理（P3）

**Week 1-2: Agent Loop 重构**
- 实现 LoopState 状态机
- max_output_tokens 恢复
- 自动重试机制

**Week 3-4: 测试与优化**
- 边界情况测试
- 性能优化
- 文档更新

**交付物**:
- 更健壮的 agent loop
- 错误恢复机制

### Month 4: 任务与队列（P4 + P5）

**Week 1-2: 简化版任务系统**
- 实现 TaskManager
- 注册内置工具
- Nag reminder

**Week 3: 统一消息队列**
- 实现 CommandQueue
- CLI 集成

**Week 4: 文档与示例**
- 更新文档
- 编写示例
- 发布 release

**交付物**:
- 任务管理工具
- 统一事件队列
- 完整文档

---

## 五、关键设计原则（来自 Claude Code 源码的启示）

### 1. 循环不变性

Claude Code 的核心循环是**不可变的**，所有增强都在循环外层叠加：

```typescript
while (true) {
  // 这个结构永远不变
  yield { type: 'stream_request_start' }
  
  // 所有增强都是 continue sites
  if (condition1) {
    state = { ...state, transition: Transition.X }
    continue  // 继续循环，而不是 break 或递归
  }
}
```

**Agentica 应用**: 在 `_run_impl()` 中使用 `continue` 而非嵌套逻辑。

### 2. 工具即扩展点

Claude Code 用 `dispatch map` 管理工具，Agentica 用 `registry`，本质相同：

```python
# 新功能 = 新工具 + handler
@tool
def task_create(subject: str) -> str:
    ...

# 不需要修改 runner 核心逻辑
```

**Agentica 应用**: 优先通过工具扩展，而非修改 Runner。

### 3. 磁盘即持久化

Claude Code 的哲学：**会话记忆是易失的，磁盘状态是持久的**。

```typescript
// 任务输出持久化到磁盘
export type TaskStateBase = {
  outputFile: string  // 磁盘文件
  outputOffset: number
}
```

**Agentica 应用**: 任务系统、压缩日志等都应该持久化到磁盘。

### 4. 渐进式压缩

Claude Code 的三层压缩是**渐进式的**：

```
Layer 1: Micro-compact (每轮静默，低成本)
Layer 2: Auto-compact (超阈值 LLM 摘要，中成本)
Layer 3: Reactive compact (API 错误时紧急压缩，高成本)
```

**Agentica 应用**: 优先实现 Layer 1（micro-compact），Layer 3 可选。

### 5. 并发分流

Claude Code 的并发是**精准分流的**，而非全量并发：

```typescript
// 不是所有工具都能并行
const isConcurrencySafe = toolDefinition.isConcurrencySafe(parsedInput.data)

// 只有只读工具可以并行
if (isConcurrencySafe && executingTools.every(t => t.isConcurrencySafe)) {
  // 并行执行
}
```

**Agentica 应用**: 使用 `concurrency_safe` 标记，而非全局锁或全量并发。

### 6. 优先级队列

Claude Code 用**单一队列 + 优先级**管理所有事件：

```typescript
export type QueuePriority = 'now' | 'next' | 'later'

// 用户输入 > 任务通知
await queue.enqueue(QueuedCommand(value=user_input, priority='next'))
await queue.enqueue(QueuedCommand(value=task_notification, priority='later'))
```

**Agentica 应用**: 统一事件管理，避免分散的逻辑。

---

## 六、总结

### 核心建议

1. **P0 Tool 并发**: 最高 ROI，立即可见性能提升
2. **P1 Cost Tracker**: 用户强需求，实现简单
3. **P2 Micro-compact**: 长对话必备，节省成本
4. **P3 Agent Loop 状态管理**: 健壮性提升，但实现成本高
5. **P4 简化版任务系统**: 多步骤任务追踪，不需要 DAG
6. **P5 统一消息队列**: 统一事件管理，为未来扩展打基础

### 不建议实现的特性

- Worktree 隔离（ROI 极低）
- Session Memory Compaction（过度工程）
- Reactive Compact（边际收益低）
- Prompt Cache 共享（模型绑定）
- 完整权限系统（超出 scope）

### 实施优先级

```
Month 1: P0 + P1（基础能力）
Month 2: P2（上下文管理）
Month 3: P3（健壮性）
Month 4: P4 + P5（任务与队列）
```

### 与 v1 方案的差异

| 维度 | v1 方案 | v2 方案 | 理由 |
|------|---------|---------|------|
| 任务系统 | 完整 DAG | 简化版 | DAG 过度复杂，无依赖场景 |
| 压缩策略 | 三层完整实现 | 只实现 micro | Session memory 过度工程 |
| 权限系统 | 完整实现 | 不实现 | 超出框架 scope |
| Worktree | 实现 | 不实现 | ROI 极低 |
| 团队协作 | 持久队友 + 消息总线 | 不实现 | 需要单独的 roadmap |

**v2 方案更务实，专注于高 ROI 特性，避免过度工程。**
