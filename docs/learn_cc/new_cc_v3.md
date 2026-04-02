# Agentica 优化方案 v3 — 6 个高 ROI 优化的最终实施方案

> 基于 v1（全面分析）、v2（精简务实）两版方案 + 源码深度阅读，整合出最优实施路径。
> 仅实现 ROI 最高的 6 项，合计约 310 行代码净增量，对现有架构侵入最小。

---

## 一、源码现状摘要（实施前的基线）

| 组件 | 现状 | 关键文件 |
|------|------|---------|
| Agent Loop | `runner.py` `_run_impl()` 单轮异步 generator，无多轮循环 | `runner.py` |
| Tool 执行 | `model/base.py` `run_function_calls()` 已用 `asyncio.gather` 并发，但无 `concurrency_safe` 分层 | `model/base.py:302` |
| 上下文压缩 | `compression/manager.py` 两阶段：规则截断 + LLM 压缩，**无触发器集成到循环** | `compression/manager.py` |
| 成本追踪 | `model/usage.py` 有 `Usage` 统计 token，**无 USD 成本换算** | `model/usage.py`, `run_response.py` |
| 循环状态 | 无 `LoopState`，无 `max_tokens` 恢复，无错误重试，无安全阀 | `runner.py` |
| RunResponse | 无 `cost_summary` 字段 | `run_response.py` |

**关键发现**：
- Tool 并发执行已在 `base.py` 实现（`asyncio.gather`），缺的只是 `concurrency_safe` **标记驱动的分层策略**（只读并行、写入串行）
- `CompressionManager` 已完整，缺的是**循环触发器**（在每轮 LLM 调用前自动检查）
- `Usage` 已追踪 token，缺的是**USD 价格表 + `cost_summary`**
- Agent Loop 是单轮模型，多轮由 `model/openai/chat.py` 的 `response()` 内部循环驱动

---

## 二、设计原则（选自源码启示，验证有效）

| 原则 | 含义 | 在本方案中的体现 |
|------|------|----------------|
| **循环不变性** | 增强在 while 外层叠加，核心循环不变 | Micro-compact / Reactive compact 作为 `response()` 内部的 continue sites，不改 Runner |
| **工具即扩展点** | 新功能 = 新标记 + 调度，不改 Runner | `concurrency_safe` 标记驱动 `run_function_calls()` 策略，不新增 Runner 方法 |
| **磁盘即持久化** | 会话记忆易失，磁盘持久 | CompressionManager 已有 workspace archive；cost_summary 写入 RunResponse |
| **渐进式压缩** | micro（低成本）→ auto（中成本）→ reactive（高成本） | 三层均实现，reactive 只在 `prompt_too_long` 时触发 |
| **并发分流** | 只读并行，写入串行，精准而非全量 | `concurrency_safe=True` 路径 `gather`，False 路径串行 |

---

## 三、6 个高 ROI 优化详解

### 优化 1：Tool `concurrency_safe` 分层并发

**现状**：`run_function_calls()` 用 `asyncio.gather` 并发所有工具。实际上 Bash/写文件应串行，否则有隐式依赖风险。

**目标**：只读工具（`concurrency_safe=True`）并行，写入工具串行，Bash 失败时取消后续写入工具。

**改动文件**：
- `agentica/tools/buildin_tools.py`：给只读工具标记 `concurrency_safe = True`（`Function` 字段已存在）
- `agentica/model/base.py`：`run_function_calls()` 内替换统一 `gather` 为分层策略

**核心逻辑**（替换 Phase 2 的 `asyncio.gather`）：

```python
# Phase 2: 分层并发 — 只读工具 gather，写入工具串行
safe_calls   = [(i, fc) for i, fc in enumerate(function_calls) if fc.function.concurrency_safe]
unsafe_calls = [(i, fc) for i, fc in enumerate(function_calls) if not fc.function.concurrency_safe]

results    = [None] * len(function_calls)
timers     = [Timer() for _ in function_calls]
exceptions = [None]  * len(function_calls)
bash_aborted = False   # Bash 错误后设置

# 2a: 只读工具并行
if safe_calls:
    async def _run_safe(idx, fc):
        async with semaphore:
            timers[idx].start()
            try:    return await fc.execute()
            except ToolCallException as e: exceptions[idx] = e; return False
            except Exception as e:         exceptions[idx] = e; return False
            finally: timers[idx].stop()
    safe_results = await asyncio.gather(*[_run_safe(i, fc) for i, fc in safe_calls])
    for (i, _), r in zip(safe_calls, safe_results):
        results[i] = r if not isinstance(r, BaseException) else False

# 2b: 写入工具串行（Bash 报错后跳过剩余）
for idx, fc in unsafe_calls:
    if bash_aborted:
        results[idx] = False
        exceptions[idx] = Exception("Skipped due to previous bash error")
        timers[idx].start(); timers[idx].stop()
        continue
    timers[idx].start()
    try:
        results[idx] = await fc.execute()
    except ToolCallException as e:
        exceptions[idx] = e
        results[idx] = False
        if fc.function.name in ("execute", "bash"):
            bash_aborted = True
    except Exception as e:
        exceptions[idx] = e
        results[idx] = False
    finally:
        timers[idx].stop()
```

**内置工具标记**（在 `BuiltinFileTool.__init__` 的 `register()` 后设置）：

```python
# tools/buildin_tools.py
self.functions["read_file"].concurrency_safe = True
self.functions["ls"].concurrency_safe        = True
self.functions["glob"].concurrency_safe      = True
self.functions["grep"].concurrency_safe      = True

# 搜索/网络工具
self.functions["web_search"].concurrency_safe = True
self.functions["fetch_url"].concurrency_safe  = True
```

**预期收益**：同一轮 3 个 read_file，耗时 3s → 1s（3x 加速）。

---

### 优化 2：Cost Tracker — USD 成本追踪

**现状**：`Usage` 记录 token 数，但无 USD 换算，`RunResponse` 无费用字段。

**目标**：
1. 新增 `agentica/cost_tracker.py`：价格表 + `CostSummary` 数据类
2. 在每次 `invoke()` 后从 `usage` 构建 `CostSummary`
3. `RunResponse` 增加 `cost_summary: Optional[CostSummary]` 字段
4. Runner 在构建最终 `RunResponse` 时写入

**新文件 `agentica/cost_tracker.py`**：

```python
from dataclasses import dataclass, field
from typing import Dict, Optional

# 价格表: $/1M tokens (2025 定价)
MODEL_PRICING: Dict[str, Dict[str, float]] = {
    "gpt-4o":            {"input": 2.5,   "output": 10.0,  "cache_read": 1.25},
    "gpt-4o-mini":       {"input": 0.15,  "output": 0.6,   "cache_read": 0.075},
    "o1":                {"input": 15.0,  "output": 60.0,  "cache_read": 7.5},
    "o3-mini":           {"input": 1.1,   "output": 4.4,   "cache_read": 0.55},
    "claude-opus-4-5":   {"input": 15.0,  "output": 75.0,  "cache_read": 1.5},
    "claude-sonnet-4-5": {"input": 3.0,   "output": 15.0,  "cache_read": 0.3},
    "claude-haiku-3-5":  {"input": 0.8,   "output": 4.0,   "cache_read": 0.08},
    "deepseek-chat":     {"input": 0.27,  "output": 1.1,   "cache_read": 0.07},
    "deepseek-reasoner": {"input": 0.55,  "output": 2.19,  "cache_read": 0.14},
    "qwen-max":          {"input": 0.4,   "output": 1.2,   "cache_read": 0.1},
    "qwen-plus":         {"input": 0.07,  "output": 0.21,  "cache_read": 0.02},
    "glm-4-flash":       {"input": 0.0,   "output": 0.0,   "cache_read": 0.0},
    "glm-4-plus":        {"input": 0.071, "output": 0.071, "cache_read": 0.0},
}


@dataclass
class ModelCost:
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cost_usd: float = 0.0


@dataclass
class CostSummary:
    total_cost_usd: float = 0.0
    total_input_tokens: int = 0
    total_output_tokens: int = 0
    total_cache_read_tokens: int = 0
    requests: int = 0
    unknown_model: bool = False
    per_model: Dict[str, ModelCost] = field(default_factory=dict)

    def record(self, model_id: str, input_tokens: int, output_tokens: int,
               cache_read_tokens: int = 0) -> None:
        pricing = _resolve_pricing(model_id)
        if pricing is None:
            self.unknown_model = True
            return
        cost = (
            input_tokens        * pricing["input"]               / 1_000_000
            + output_tokens     * pricing["output"]              / 1_000_000
            + cache_read_tokens * pricing.get("cache_read", 0.0) / 1_000_000
        )
        mc = self.per_model.setdefault(model_id, ModelCost())
        mc.input_tokens      += input_tokens
        mc.output_tokens     += output_tokens
        mc.cache_read_tokens += cache_read_tokens
        mc.cost_usd          += cost
        self.total_cost_usd          += cost
        self.total_input_tokens      += input_tokens
        self.total_output_tokens     += output_tokens
        self.total_cache_read_tokens += cache_read_tokens
        self.requests                += 1

    def format(self) -> str:
        parts = [
            f"Cost: ${self.total_cost_usd:.4f}",
            f"Tokens: {self.total_input_tokens:,} in + {self.total_output_tokens:,} out",
        ]
        if self.total_cache_read_tokens:
            parts.append(f"{self.total_cache_read_tokens:,} cache_read")
        if len(self.per_model) > 1:
            model_lines = [f"  {mid}: ${mc.cost_usd:.4f}" for mid, mc in self.per_model.items()]
            parts.append("\n" + "\n".join(model_lines))
        if self.unknown_model:
            parts.append("[warning: some model prices unknown]")
        return " | ".join(parts[:3]) + ("".join(parts[3:]) if len(parts) > 3 else "")


def _resolve_pricing(model_id: str) -> Optional[Dict[str, float]]:
    if model_id in MODEL_PRICING:
        return MODEL_PRICING[model_id]
    for key in MODEL_PRICING:
        if model_id.startswith(key):
            return MODEL_PRICING[key]
    return None
```

**集成点**：
- `model/base.py`：每次 `_update_usage()` 调用后，调用 `self._cost_summary.record(self.id, ...)`
- `runner.py`：构建最终 `RunResponse` 时，`run_response.cost_summary = agent.model._cost_summary`
- `run_response.py`：新增 `cost_summary: Optional[CostSummary] = None`

---

### 优化 3：Micro-compact — 每轮静默压缩

**现状**：`CompressionManager` 已实现两阶段压缩，但没有被循环调用。

**目标**：在每轮 `response()` 中，**在发送 LLM 请求之前**，调用一次轻量规则截断（Stage 1a），不触发 LLM 摘要。

**改动文件**：`model/base.py` + 各 Model 子类 `response()` 入口

```python
# model/base.py — 新增 helper
def _micro_compact(self, messages: List[Message]) -> None:
    """每轮 LLM 调用前静默截断旧 tool 结果，不触发 LLM，无成本。"""
    cm = getattr(self, '_compression_manager', None)
    if cm is None or not cm.compress_tool_results:
        return
    truncated = cm._truncate_oldest_tool_results(messages)
    if truncated:
        logger.debug(f"Micro-compact: truncated {truncated} old tool results")
```

**触发**（在 `model/openai/chat.py` `response()` 工具循环的每轮开头）：

```python
async def response(self, messages: List[Message]) -> ModelResponse:
    ...
    while True:
        self._micro_compact(messages)    # 每轮静默截断
        response = await self._invoke_with_retry(messages)
        ...
```

**注入**（`agent/__init__.py` `update_model()` 末尾）：

```python
if hasattr(self, 'compression') and self.compression is not None:
    self.model._compression_manager = self.compression
```

**预期收益**：长对话时每轮节省 5–30% token，零 LLM 调用成本。

---

### 优化 4：Agent Loop 状态管理增强

现有 Model 子类 `response()` 有工具循环，缺失三个防护：

#### 4a：max_output_tokens 恢复

```python
# model/openai/chat.py  response() 工具循环内
MAX_TOKENS_RECOVERY_LIMIT = 3
max_tokens_recovery_count = 0
...
while True:
    response = await self._invoke_with_retry(messages)
    finish_reason = response.choices[0].finish_reason

    if finish_reason == "length":
        if max_tokens_recovery_count >= MAX_TOKENS_RECOVERY_LIMIT:
            logger.warning("max_tokens recovery limit reached, stopping")
            break
        max_tokens_recovery_count += 1
        logger.info(f"Output truncated (max_tokens), requesting continuation ({max_tokens_recovery_count}/{MAX_TOKENS_RECOVERY_LIMIT})")
        messages.append(assistant_message)  # 本轮已有内容
        messages.append(Message(role="user", content="Continue from where you left off."))
        continue
```

#### 4b：API 错误指数退避重试

```python
# model/base.py — 新增 _invoke_with_retry helper
MAX_RETRY = 3

async def _invoke_with_retry(self, messages: List[Message]) -> Any:
    from openai import RateLimitError, APIConnectionError, APITimeoutError
    for attempt in range(MAX_RETRY):
        try:
            return await self.invoke(messages)
        except (RateLimitError, APIConnectionError, APITimeoutError) as e:
            if attempt == MAX_RETRY - 1:
                raise
            wait = 2 ** attempt
            logger.warning(f"API error (attempt {attempt+1}/{MAX_RETRY}): {e}, retry in {wait}s")
            await asyncio.sleep(wait)
```

#### 4c：循环安全阀

```python
# model/openai/chat.py  response() 工具循环
MAX_TURNS = 50
turn_count = 0
while True:
    turn_count += 1
    if turn_count > MAX_TURNS:
        logger.warning(f"Tool call loop exceeded {MAX_TURNS} turns, forcing stop")
        break
    ...
```

---

### 优化 5：Reactive Compact — 紧急压缩

**现状**：context 超长时 API 直接报错，运行崩溃。

**目标**：捕获 `context_length_exceeded`，执行一次紧急压缩后重试，只触发一次。

```python
# model/base.py  _invoke_with_retry() 扩展
async def _invoke_with_retry(self, messages: List[Message]) -> Any:
    reactive_attempted = False
    for attempt in range(MAX_RETRY):
        try:
            return await self.invoke(messages)
        except Exception as e:
            err_str = str(e).lower()
            if ("context_length_exceeded" in err_str or "prompt_too_long" in err_str
                    or "maximum context length" in err_str):
                if not reactive_attempted:
                    reactive_attempted = True
                    logger.warning("Context too long, attempting reactive compact")
                    await self._reactive_compact(messages)
                    continue  # 重试一次
                raise  # 已压缩仍超长，放弃
            # 可重试错误（rate limit / connection）
            if any(t in err_str for t in ("rate_limit", "connection", "timeout")):
                if attempt == MAX_RETRY - 1:
                    raise
                await asyncio.sleep(2 ** attempt)
                continue
            raise  # 其他错误直接抛出

async def _reactive_compact(self, messages: List[Message]) -> None:
    cm = getattr(self, '_compression_manager', None)
    if cm is not None:
        cm._drop_old_messages(messages)
        if cm.use_llm_compression:
            await cm._llm_compress_old_tool_results(messages)
    else:
        # 无 CompressionManager: fallback - 保留最近 3 轮
        _keep_recent_rounds(messages, keep_rounds=3)
```

---

### 优化 6：`RunResponse.cost_summary` 字段

（与优化 2 协同，单独列出完整链路）

```
LLM 调用 (invoke)
  -> model.usage 更新 token
  -> model._cost_summary.record(model.id, input, output, cache_read)
  -> runner._run_impl() 末尾:
       run_response.cost_summary = getattr(agent.model, '_cost_summary', None)
  -> RunResponse.cost_summary 可用
```

**用户使用**：

```python
response = agent.run_sync("分析这个文件")
if response.cost_summary:
    print(response.cost_summary.format())
    # Cost: $0.0023 | Tokens: 1,234 in + 456 out
```

---

## 四、文件改动清单

| 文件 | 改动类型 | 约改动量 | 说明 |
|------|---------|--------|------|
| `agentica/cost_tracker.py` | **新增** | ~100 行 | CostSummary + 价格表 |
| `agentica/run_response.py` | 修改 | +3 行 | 新增 `cost_summary` 字段 |
| `agentica/tools/buildin_tools.py` | 修改 | ~10 行 | 只读工具 `concurrency_safe=True` |
| `agentica/model/base.py` | 修改 | ~80 行 | 分层并发 + micro_compact + retry + reactive compact |
| `agentica/model/openai/chat.py` | 修改 | ~60 行 | max_tokens 恢复 + 安全阀 + cost 集成 |
| `agentica/model/anthropic/*.py` | 修改 | ~40 行 | 同 OpenAI 模式 |
| `agentica/agent/__init__.py` | 修改 | ~10 行 | 注入 `_compression_manager` |
| `agentica/runner.py` | 修改 | ~10 行 | 写入 `cost_summary` 到 RunResponse |
| **合计** | — | **~313 行** | — |

---

## 五、不实现项目说明（ROI 分析）

| 项目 | 来自 | 不实现理由 |
|------|------|-----------|
| 完整任务系统（DAG） | v1 P3 | 现有 `write_todos`/`read_todos` 已覆盖，DAG 过度 |
| 统一消息队列 | v2 P5 | 无 CLI 实时交互需求，ROI 低 |
| 持久队友 + JSONL 邮箱 | v1 P5 | 已有 Swarm，scope 外 |
| Worktree 隔离 | v1 P9 | ROI 极低 |
| Prompt Cache 共享 | v1 P7 | 模型绑定，多模型无法统一 |
| Skill 两层注入 | v1 P10 | 现有注入已够用 |
| 完整权限系统 | v1 P8 | 超出框架 scope |

---

## 六、实施顺序（建议 2 周）

```
Day 1-2:  优化 2 + 优化 6 — CostTracker + RunResponse.cost_summary
          (独立新文件，零风险，立即有用户价值)

Day 3-4:  优化 1 — concurrency_safe 分层并发
          (改 model/base.py run_function_calls，有现成 gather 基础)

Day 5-6:  优化 4 — Loop 状态管理
          (max_tokens 恢复 + retry + 安全阀，改各 Model 子类)

Day 7-8:  优化 3 — Micro-compact 集成
          (挂 _compression_manager 到 model，调用 _micro_compact)

Day 9-10: 优化 5 — Reactive compact
          (捕获 context_length_exceeded，调用 _reactive_compact)

Day 11-14: 测试 + 文档 + 示例
```

---

## 七、架构演进图

```
v3 目标架构:

User -> Agent -> Runner (_run_impl, 单轮 generator, 不变)
                    |
                    v
                Model.response(messages)
                    |
                    +-- [优化 3] _micro_compact(messages)
                    |
                    +-- while True:  (工具循环, 内含 safety valve [优化 4c])
                    |     |
                    |     +-- [优化 4b] _invoke_with_retry(messages)
                    |     |     |-- [优化 5] reactive_compact on context error
                    |     |     |-- rate_limit 指数退避
                    |     |
                    |     +-- [优化 4a] finish_reason=="length" -> 续写 continue
                    |     |
                    |     +-- run_function_calls()
                    |           +-- [优化 1] safe tools  -> gather (并行)
                    |           +-- [优化 1] unsafe tools -> serial (串行)
                    |           +-- [优化 1] bash error  -> cancel rest
                    |
                    +-- [优化 2] _cost_summary.record(usage)
                    |
                    v
               RunResponse
                    +-- [优化 6] cost_summary: CostSummary
```

---

## 八、来自源码深度探索的新设计模式（2026-04-01 补充）

> 基于对 CC v2.1.88 源码 8 个核心模块的深度阅读，提取出可直接借鉴到 Agentica 的设计模式。
> 优先级按 ROI 排序，标注每条的 Agentica 落地路径。

### 8.1 FileReadTool 双重卡口 + 分页索引强制

**CC 源码**：`src/tools/FileReadTool/limits.ts` + `FileReadTool.ts`

**核心机制**：两个独立的限制，各在不同阶段检查：
```typescript
maxSizeBytes = 256_000   // stat 阶段（读取前）— 防止大文件进内存
maxTokens    = 25_000    // 读取后 token 计数 — 防止上下文爆炸

// 读取窗口：强制模型使用 offset + limit 精确定位
// 超出 maxTokens 时抛出 MaxFileReadTokenExceededError，
// 错误消息明确告诉模型"请用 offset+limit 参数重新读取"
```

**去重优化**：相同 `file_path + offset + limit + mtime` 的重复读取返回 `FILE_UNCHANGED_STUB`，避免同一内容塞入上下文两次（约 18% 的读取是重复的）。

**Agentica 落地（P1，~30 行）**：

```python
# agentica/tools/buildin_tools.py — read_file() 增加 stat 前置检查 + 分页引导
MAX_FILE_SIZE_BYTES = 256_000

async def read_file(self, path: str, offset: int = 1, limit: int = None) -> str:
    """Read file content. For large files, use offset+limit to read specific sections.

    Args:
        path:   File path to read.
        offset: Starting line (1-based). Default 1.
        limit:  Max lines to read. Default MAX_READ_LINES.
    """
    resolved = self._resolve_path(path)
    file_size = resolved.stat().st_size
    if file_size > MAX_FILE_SIZE_BYTES:
        raise ValueError(
            f"File too large ({file_size:,} bytes > {MAX_FILE_SIZE_BYTES:,}). "
            f"Use offset+limit parameters to read specific sections, "
            f"e.g. read_file('{path}', offset=1, limit=100)"
        )
    # ... 现有读取逻辑，支持 offset/limit
```

---

### 8.2 全生命周期 Hooks（19+ 事件）

**CC 源码**：`src/utils/hooks.ts`（5000+ 行）、`src/types/hooks.ts`

**Agentica 已有 vs CC 缺口**：

| CC Hook 事件 | Agentica 现状 |
|-------------|--------------|
| `PreToolUse` | `on_tool_start` ✅ |
| `PostToolUse` | `on_tool_end` ✅ |
| `Stop` / `StopFailure` | `on_agent_end` ✅ |
| `UserPromptSubmit` | ❌ 无 |
| `PreCompact` / `PostCompact` | ❌ 无 |
| `PermissionDenied`（可 retry） | ❌ 无 |
| `SubagentStart` / `SubagentStop` | ❌ 无 |
| `TeammateIdle` / `TaskCompleted` | ❌ 无 |

**最强大的 CC feature — `PreToolUse.updatedInput`**：hook 可以**直接修改工具入参**：
```typescript
hookSpecificOutput: {
  hookEventName: 'PreToolUse',
  updatedInput: { command: "git diff --cached" },  // 替换原来的命令
  permissionDecision: 'allow'
}
```

**Agentica 落地（P2，~50 行）**：

```python
# agentica/hooks.py — 扩展 RunHooks，增加压缩前后和权限拒绝钩子
class RunHooks:
    # 已有：on_tool_start / on_tool_end / on_agent_start / on_agent_end / on_llm_start / on_llm_end

    async def on_user_prompt(self, agent, message: str) -> Optional[str]:
        """Called before user prompt is processed. Return modified message or None."""
        return None

    async def on_pre_compact(self, agent, messages) -> None:
        """Called before context compression is triggered."""

    async def on_post_compact(self, agent, messages) -> None:
        """Called after context compression. `messages` is the compressed result."""

    async def on_permission_denied(self, agent, tool_name: str, reason: str) -> bool:
        """Called when a tool call is denied. Return True to retry the tool call."""
        return False
```

---

### 8.3 工具结果大文件持久化（读写分离）

**CC 源码**：`src/utils/toolResultStorage.ts`（1041 行）

**核心设计**：当工具结果超过阈值，写磁盘 + context 里只放"预览 + 路径"：
```
[Output too large (1.2 MB). Full output saved to: .sessions/<id>/tool-results/<toolUseId>.txt]

Preview:
<前 2000 字符内容>
...
```

工具级别单独配置 `maxResultSizeChars`：
- `FileReadTool`：`Infinity`（永不持久化，避免"读自己写的文件"循环）
- `BashTool`：有限值（大命令输出持久化）

**状态机保证 prompt cache 稳定**：`seenIds` 记录"已处理过的工具 ID"，保证同一 ID 的替换决策在整个会话内一致（子代理 fork 时复制状态，共享父 cache）。

**Agentica 落地（P1，~100 行，新模块）**：

```python
# agentica/tools/base.py — Function 新增字段
class Function(BaseModel):
    max_result_size_chars: Optional[int] = None
    # None  → 不限制（read_file 类工具）
    # N > 0 → 超过 N 字符时持久化到磁盘

# agentica/compression/tool_result_storage.py — 新模块
PREVIEW_SIZE_CHARS = 2000

def maybe_persist_tool_result(
    tool_name: str, tool_use_id: str, content: str,
    max_result_size_chars: Optional[int], session_id: str,
) -> str:
    """If content exceeds threshold, persist to disk and return preview message."""
    if max_result_size_chars is None or len(content) <= max_result_size_chars:
        return content
    path = Path(".sessions") / session_id / "tool-results" / f"{tool_use_id}.txt"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    preview = content[:PREVIEW_SIZE_CHARS]
    return (
        f"[Output too large ({len(content):,} chars). Saved to: {path}]\n\n"
        f"Preview:\n{preview}" + ("\n..." if len(content) > PREVIEW_SIZE_CHARS else "")
    )
```

---

### 8.4 JSONL 进度回档 + Resume/Fork

**CC 源码**：`src/utils/sessionStorage.ts`（5106 行）

会话文件 `<projectDir>/<sessionId>.jsonl`，每行一个 Entry（append-only）：

| Entry 类型 | 恢复策略 | 说明 |
|-----------|---------|------|
| `TranscriptMessage` | 全部顺序重放 | 核心对话记录 |
| `SummaryMessage` | 清除之前记录，以此为起点 | compact 边界 |
| `ContentReplacementEntry` | 重放 | 工具结果替换状态恢复 |
| `marble-origami-commit` | 全部重放（顺序依赖） | Context Collapse 提交 |
| `marble-origami-snapshot` | last-wins（只保留最新） | Collapse 暂存状态 |

**Agentica 落地（P3，~200 行，新模块）**：

```python
# agentica/memory/session_log.py — 新模块
# 现有 WorkingMemory 纯内存，会话结束即丢失
# 此模块提供 append-only JSONL 日志，支持 /resume

class SessionLog:
    """Append-only JSONL session log. Enables session resume."""

    def __init__(self, session_id: str, base_dir: str = ".sessions"):
        self.path = Path(base_dir) / f"{session_id}.jsonl"
        self.path.parent.mkdir(exist_ok=True)

    def append_message(self, role: str, content: str) -> None:
        self._append({"type": "message", "role": role, "content": content})

    def append_compact_boundary(self, summary: str) -> None:
        self._append({"type": "summary", "content": summary})

    def load(self) -> List[Dict]:
        """Replay log for resume. compact boundary resets message history."""
        messages = []
        if not self.path.exists():
            return messages
        for line in self.path.read_text().splitlines():
            entry = json.loads(line)
            if entry["type"] == "message":
                messages.append({"role": entry["role"], "content": entry["content"]})
            elif entry["type"] == "summary":
                messages = [{"role": "user", "content": f"[Resumed]\n{entry['content']}"}]
        return messages

    def _append(self, entry: Dict) -> None:
        with open(self.path, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
```

---

### 8.5 Context Collapse — 渐进式精细归档

**CC 源码**：`services/contextCollapse/index.js`（feature-gated，内部代号 `marble_origami`）

**与 autoCompact 的本质区别**：

| 维度 | autoCompact | Context Collapse |
|------|------------|-----------------|
| 触发阈值 | 单点（context_window - 13K） | 多层渐进（90% staged → 95% blocking） |
| 处理粒度 | 全量 LLM 摘要（历史→一段文字） | 精细归档（逐段 collapse，保留结构） |
| 与对方关系 | 独立 | **互斥**：collapse 启用时 autoCompact 强制禁用 |

**Token 使用率层级**（从源码反推）：
```
< 80%  → 正常
80-90% → 警告阶段
~90%   → Collapse staged（开始分析可归档消息段，后台 ctx-agent 分析）
~93%   → autoCompact 触发（如果 collapse 未启用）
~95%   → Collapse blocking spawn（强制 collapse，阻塞继续）
```

**JSONL 持久化的两类 Collapse 记录**：
```typescript
// marble-origami-commit（全部重放，顺序依赖）
{
  type: 'marble-origami-commit',
  collapseId: "16位ID",
  summaryUuid: "placeholder消息的UUID",
  summaryContent: '<collapsed id="c1">摘要文本</collapsed>',
  firstArchivedUuid: "...",  // 被归档消息的边界
  lastArchivedUuid: "..."
}

// marble-origami-snapshot（last-wins）
{
  type: 'marble-origami-snapshot',
  staged: [{ startUuid, endUuid, summary, risk, stagedAt }],
  armed: boolean,          // spawn 触发状态
  lastSpawnTokens: number  // 用于计算下次触发间隔
}
```

**Agentica 落地（P3，复杂度极高，先做简化版）**：

```python
# 简化版：不是完整的 Context Collapse，而是"分段摘要归档"
# 把每轮工具调用总结成一行 <collapsed> 标记，保留结构

def collapse_old_rounds(messages, keep_recent=3):
    """Replace old tool-call rounds with collapsed summaries."""
    rounds = _identify_tool_rounds(messages)
    if len(rounds) <= keep_recent:
        return messages
    to_collapse = rounds[:-keep_recent]
    collapsed_summary = "\n".join(
        f'<collapsed id="{i}">{_summarize_round(r)}</collapsed>'
        for i, r in enumerate(to_collapse)
    )
    # 替换被归档的消息
    keep_from = to_collapse[-1].end_idx + 1
    head = [m for m in messages if m.role in ("system",)]
    tail = messages[keep_from:]
    return head + [Message(role="user", content=collapsed_summary)] + tail
```

---

### 8.6 流式输出中断 + 空闲看门狗

**CC 源码**：`src/services/api/claude.ts`

**两类"中断"精确区分**：
```typescript
// ① 用户主动中断（ESC）→ 不产出错误消息，query.ts 注入 "interrupted" 提示
if (error instanceof APIUserAbortError && signal.aborted) { return }

// ② SDK 内部超时（非用户操作）→ 包装成具体错误类型
if (error instanceof APIUserAbortError && !signal.aborted) {
    throw new APIConnectionTimeoutError(...)
}
```

**流式空闲看门狗**：连接未断但长时间无 token 时触发，防止"静默挂起"（比 SDK 超时更细粒度）。

**Agentica 落地（P2，~40 行）**：

```python
# agentica/model/base.py — response_stream 增加看门狗
STREAM_IDLE_TIMEOUT_SECS = 60

async def _stream_with_watchdog(self, stream_iter, idle_timeout=STREAM_IDLE_TIMEOUT_SECS):
    """Wrap a streaming iterator with an idle watchdog."""
    import asyncio, time
    last_token_time = time.monotonic()
    q = asyncio.Queue()

    async def _producer():
        try:
            async for chunk in stream_iter:
                await q.put(chunk)
            await q.put(None)  # sentinel
        except Exception as e:
            await q.put(e)

    task = asyncio.create_task(_producer())
    try:
        while True:
            try:
                item = await asyncio.wait_for(q.get(), timeout=idle_timeout)
            except asyncio.TimeoutError:
                task.cancel()
                logger.warning(f"Stream idle for {idle_timeout}s, cancelled")
                raise
            if item is None:
                break
            if isinstance(item, Exception):
                raise item
            last_token_time = time.monotonic()
            yield item
    finally:
        task.cancel()
```

---

### 8.7 子代理 Prompt Cache 共享（零边际成本并发）

**CC 源码**：`src/utils/forkedAgent.ts`

**核心原理**：Anthropic Prompt Cache key = `system_prompt + tools + model + messages_prefix + thinking_config` 5 个要素。子代理复用父代理的这 5 个参数 → 命中已建立的 cache → `cache_read_tokens`（约为写入价格的 1/10）。

```typescript
// 全局 slot — 每轮 turn 后保存，所有 post-turn fork 读取（无需传参）
let lastCacheSafeParams: CacheSafeParams | null = null

// ⚠️ 关键陷阱：设置 maxOutputTokens 会改变 budget_tokens → 破坏 cache key！
// skipCacheWrite: true → fire-and-forget fork 不污染父 cache 空间
```

**Agentica 落地（P2，仅 Anthropic 模型，~50 行）**：

```python
# agentica/agent/team.py — 子代理 fork 时继承父消息列表作为 prefix
async def _fork_subagent(self, prompt: str) -> RunResponse:
    """Fork a subagent sharing parent's message history as prompt cache prefix."""
    from agentica.model.anthropic.claude import AnthropicChat

    if not isinstance(self.model, AnthropicChat):
        # 非 Anthropic 模型无 prompt cache，走普通子代理路径
        return await self._run_subagent(prompt)

    # 关键：把父代理的当前消息列表作为 prefix 传入
    # Anthropic API 会识别这段 prefix 已在 cache 中
    parent_messages = list(self.working_memory.get_messages())
    subagent = Agent(
        model=self.model,        # 相同 model 实例（相同 api_key + 参数）
        instructions=self.instructions,  # 相同系统提示
        tools=self.tools,        # 相同工具列表
    )
    return await subagent.run(prompt, messages=parent_messages)
    # subagent 的请求：messages = parent_messages + [{"role":"user","content":prompt}]
    # 与父代理的请求共享相同 prefix → cache hit
```

---

### 8.8 ROI 分析总表

| 设计模式 | 实现成本 | 预期收益 | **优先级** |
|---------|---------|---------|----------|
| **8.1 FileReadTool 双卡口 + 分页引导** | 低（~30 行） | 防大文件爆 context，引导模型正确使用分页 | **P1** |
| **8.3 工具结果大文件持久化** | 中（~100 行，新模块） | bash 大输出不占 context，长任务不丢内容 | **P1** |
| **8.2 PreCompact / PostCompact hooks** | 低（~20 行） | 压缩前后可做自定义操作（存档、通知） | **P2** |
| **8.2 UserPromptSubmit hook** | 低（~15 行） | 用户输入时可注入动态上下文 | **P2** |
| **8.6 流式空闲看门狗** | 低（~40 行） | 防止流式静默挂起，超时优雅取消 | **P2** |
| **8.7 子代理 Cache 共享** | 中（~50 行，仅 Anthropic） | 并发子代理近零边际成本 | **P2** |
| **8.4 JSONL 会话日志 + Resume** | 高（~200 行，新模块） | 支持会话恢复（`/resume`） | **P3** |
| **8.5 Context Collapse 精细归档** | 高（极高复杂度） | 百万 Token 任务不降智 | **P3** |

### 8.9 不借鉴的模式（ROI 低或不可移植）

| 模式 | 理由 |
|------|------|
| Permission Classifier（auto mode） | 依赖 Anthropic 内部 AI 分类器服务，不可移植 |
| FileChanged 文件监听 Hook | 需要 watchdog 进程，超出框架 scope |
| JSONL 完整实现（所有 Entry 类型） | 过度复杂，Agentica 用简化版 SessionLog 即可 |
| Context Collapse 完整实现 | 需要独立 ctx-agent + commit log + 归档服务，极大 scope |
| 子代理 Cache 共享完整实现 | 模型绑定（仅 Anthropic），多模型框架不适合内置 |
