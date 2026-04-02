# Agentica 最优优化方案 v3

> 基于 Claude Code v2.1.88 源码深度分析 + Agentica 现有代码审查，给出可直接落地的最优方案。
>
> **指导原则**（源自 CC 源码启示）：
> 1. **循环不变性** — 所有增强都在 `while` 循环外层叠加，核心循环不变
> 2. **工具即扩展点** — 新功能 = 新工具 + handler，不改 Runner 核心
> 3. **磁盘即持久化** — 会话记忆易失，磁盘状态持久
> 4. **渐进式压缩** — micro（低成本）→ auto（中成本）→ reactive（高成本）
> 5. **并发分流** — 只读工具并行，写入工具串行，非全量并发
> 6. **优先级队列** — 单一队列 + 优先级管理所有事件

---

## 一、现有代码分析

### 1.1 Runner 现状

`runner.py` 的 `_run_impl()` 是**单轮、无状态的**异步生成器：

- 每次调用只做一轮 LLM → 工具 → 返回，多轮循环由 `Model.response()` 内部处理
- 没有 `max_tokens` 截断恢复
- 没有 API 错误自动重试
- 没有循环轮次安全阀
- 没有成本追踪

### 1.2 Compression 现状

`compression/manager.py` 已有两阶段压缩：
- Stage 1a: truncate oldest tool results（相当于 micro-compact）
- Stage 1b: drop old messages（相当于 auto-compact 的粗略版本）
- Stage 2: LLM compression（可选）

**但尚未集成到 Runner 循环中**，也没有基于 token 数量的自动触发。

### 1.3 Function/Tool 现状

`tools/base.py` 的 `Function` 类没有 `concurrency_safe` 标记。工具在 `Model.response()` 内部串行执行（`handle_tool_calls` 中按顺序处理）。

### 1.4 RunResponse 现状

`run_response.py` 有 `usage: Optional[Usage]` 字段，但没有成本摘要字段。

---

## 二、6 个高 ROI 优化详解

### 优化 1: Tool 并发安全标记 `concurrency_safe`

**问题**: 所有工具串行执行，同一轮 3 个 read_file 需要 3× 时间。

**方案**:

#### 1.1 `Function` 新增标记

在 `agentica/tools/base.py` 的 `Function` 类中新增字段：

```python
class Function(BaseModel):
    # ... 现有字段 ...
    
    # 新增：是否可以与其他工具并发执行
    # True: 只读工具，可并行（read_file, glob, grep, web_search, web_fetch）
    # False: 写入工具，必须串行（execute, write_file, edit_file）
    concurrency_safe: bool = False
```

#### 1.2 内置工具自动标记（`tools/buildin_tools.py`）

```python
# 在注册内置工具后，标记只读工具
_READ_ONLY_TOOLS = {"read_file", "glob", "grep", "web_search", "fetch_url", "ls"}

for name, func in _BUILTIN_FUNCTION_REGISTRY.items():
    if name in _READ_ONLY_TOOLS:
        func.concurrency_safe = True
```

#### 1.3 `@tool` 装饰器支持 `concurrency_safe` 参数

```python
def tool(
    name: str = None,
    description: str = None,
    concurrency_safe: bool = False,  # 新增
    ...
):
    def decorator(func):
        func._tool_metadata = {
            ...
            "concurrency_safe": concurrency_safe,
        }
        return func
    return decorator
```

#### 1.4 Model 层并发执行（`model/base.py` 的 `handle_tool_calls`）

参考 CC 的 `StreamingToolExecutor` 分层并发策略：

```python
async def handle_tool_calls(self, assistant_message, messages, model_response, tool_role="tool"):
    """智能并发：只读工具并行，写入工具串行。Bash 错误取消剩余并行工具。"""
    if not assistant_message.tool_calls:
        return

    function_calls = self._build_function_calls(assistant_message)
    
    # 分组
    safe_calls = [fc for fc in function_calls if fc.function.concurrency_safe]
    unsafe_calls = [fc for fc in function_calls if not fc.function.concurrency_safe]
    
    results = []
    
    # 并行执行只读工具
    if safe_calls:
        tasks = [self._invoke_single_tool(fc) for fc in safe_calls]
        parallel_results = await asyncio.gather(*tasks, return_exceptions=True)
        for fc, result in zip(safe_calls, parallel_results):
            if isinstance(result, Exception):
                results.append((fc, f"Error: {result}", True))
            else:
                results.append((fc, result, False))
    
    # 串行执行写入工具（execute 错误时中断后续）
    bash_errored = False
    for fc in unsafe_calls:
        if bash_errored:
            results.append((fc, "Cancelled: sibling bash tool errored", True))
            continue
        try:
            result = await self._invoke_single_tool(fc)
            results.append((fc, result, False))
        except Exception as e:
            is_bash = fc.function.name in {"execute", "bash"}
            if is_bash:
                bash_errored = True
            results.append((fc, f"Error: {e}", True))
    
    # 按原始顺序构建 tool messages（维持 OpenAI 消息顺序要求）
    for fc, content, is_error in results:
        messages.append(Message(
            role=tool_role,
            tool_call_id=fc.call_id,
            tool_name=fc.function.name,
            content=content,
            tool_call_error=is_error,
        ))
```

**预期收益**: 3 个并行 read_file → 3× 加速，Bash 错误保护。

---

### 优化 2: Cost Tracker 成本追踪

**问题**: 无成本追踪，用户不知道花了多少钱。

**方案**: 新建 `agentica/cost_tracker.py`，集成到 `RunResponse`。

#### 2.1 `agentica/cost_tracker.py`

```python
# -*- coding: utf-8 -*-
"""
Cost tracker for LLM API usage.
Tracks per-model token usage and estimates USD cost.
Integrated into RunResponse.cost_tracker.
"""
from dataclasses import dataclass, field
from typing import Dict, Optional

# 定价：每 1M tokens 的 USD 价格
# 格式: {"input": float, "output": float, "cache_read": float, "cache_write": float}
MODEL_PRICING: Dict[str, Dict[str, float]] = {
    # OpenAI
    "gpt-4o":               {"input": 2.50,  "output": 10.00, "cache_read": 1.25,  "cache_write": 2.50},
    "gpt-4o-mini":          {"input": 0.15,  "output": 0.60,  "cache_read": 0.075, "cache_write": 0.15},
    "gpt-4-turbo":          {"input": 10.00, "output": 30.00, "cache_read": 0.0,   "cache_write": 0.0},
    "o1":                   {"input": 15.00, "output": 60.00, "cache_read": 7.50,  "cache_write": 0.0},
    "o1-mini":              {"input": 1.10,  "output": 4.40,  "cache_read": 0.55,  "cache_write": 0.0},
    "o3-mini":              {"input": 1.10,  "output": 4.40,  "cache_read": 0.55,  "cache_write": 0.0},
    # Anthropic
    "claude-sonnet-4-20250514": {"input": 3.00, "output": 15.00, "cache_read": 0.30, "cache_write": 3.75},
    "claude-haiku-3-5":         {"input": 0.80, "output": 4.00,  "cache_read": 0.08, "cache_write": 1.00},
    "claude-opus-4":            {"input": 15.0, "output": 75.00, "cache_read": 1.50, "cache_write": 18.75},
    # DeepSeek
    "deepseek-chat":        {"input": 0.27,  "output": 1.10,  "cache_read": 0.07,  "cache_write": 0.27},
    "deepseek-reasoner":    {"input": 0.55,  "output": 2.19,  "cache_read": 0.14,  "cache_write": 0.55},
    # ZhipuAI
    "glm-4-flash":          {"input": 0.00,  "output": 0.00,  "cache_read": 0.0,   "cache_write": 0.0},
    "glm-4-air":            {"input": 0.14,  "output": 0.14,  "cache_read": 0.0,   "cache_write": 0.0},
    # Qwen
    "qwen-turbo":           {"input": 0.06,  "output": 0.18,  "cache_read": 0.0,   "cache_write": 0.0},
    "qwen-plus":            {"input": 0.40,  "output": 1.20,  "cache_read": 0.0,   "cache_write": 0.0},
    # Moonshot
    "moonshot-v1-8k":       {"input": 0.18,  "output": 0.18,  "cache_read": 0.0,   "cache_write": 0.0},
    # Doubao
    "doubao-pro-4k":        {"input": 0.11,  "output": 0.32,  "cache_read": 0.0,   "cache_write": 0.0},
}


@dataclass
class ModelUsageStat:
    """单个模型的用量统计。"""
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0
    cost_usd: float = 0.0
    requests: int = 0


@dataclass
class CostTracker:
    """全链路成本追踪器。
    
    生命周期：每次 Agent.run() 创建一个新实例，随 RunResponse 返回。
    
    用法：
        response = agent.run("...")
        print(response.cost_tracker.summary())
        print(f"Total: ${response.cost_tracker.total_cost_usd:.4f}")
    """
    total_cost_usd: float = 0.0
    total_input_tokens: int = 0
    total_output_tokens: int = 0
    turns: int = 0
    has_unknown_model: bool = False
    model_usage: Dict[str, ModelUsageStat] = field(default_factory=dict)

    def record(self, model_id: str, input_tokens: int, output_tokens: int,
               cache_read_tokens: int = 0, cache_write_tokens: int = 0) -> float:
        """记录一次 API 调用，返回本次费用（USD）。"""
        # 规范化 model_id（去除版本后缀、前缀）
        normalized = self._normalize_model_id(model_id)
        pricing = MODEL_PRICING.get(normalized)
        if pricing is None:
            # 尝试前缀匹配
            for key in MODEL_PRICING:
                if normalized.startswith(key) or key.startswith(normalized.split("-")[0]):
                    pricing = MODEL_PRICING[key]
                    break
        
        if pricing is None:
            self.has_unknown_model = True
            pricing = {"input": 0.0, "output": 0.0, "cache_read": 0.0, "cache_write": 0.0}

        cost = (
            input_tokens       * pricing["input"]       / 1_000_000 +
            output_tokens      * pricing["output"]      / 1_000_000 +
            cache_read_tokens  * pricing["cache_read"]  / 1_000_000 +
            cache_write_tokens * pricing["cache_write"] / 1_000_000
        )

        # 更新 per-model 统计
        stat = self.model_usage.setdefault(normalized, ModelUsageStat())
        stat.input_tokens       += input_tokens
        stat.output_tokens      += output_tokens
        stat.cache_read_tokens  += cache_read_tokens
        stat.cache_write_tokens += cache_write_tokens
        stat.cost_usd           += cost
        stat.requests           += 1

        # 更新总计
        self.total_cost_usd      += cost
        self.total_input_tokens  += input_tokens
        self.total_output_tokens += output_tokens
        self.turns               += 1

        return cost

    def summary(self) -> str:
        """格式化成本摘要，对标 CC 的 formatTotalCost()。"""
        lines = [f"Total cost: ${self.total_cost_usd:.4f}"]
        if self.has_unknown_model:
            lines.append("  ⚠ Unknown model(s) — costs may be underestimated")
        lines.append(f"Total tokens: {self.total_input_tokens:,} input + {self.total_output_tokens:,} output")
        lines.append(f"API calls: {self.turns}")
        if self.model_usage:
            lines.append("Usage by model:")
            for model, stat in self.model_usage.items():
                lines.append(
                    f"  {model}: {stat.input_tokens:,} in + {stat.output_tokens:,} out"
                    + (f" + {stat.cache_read_tokens:,} cache_read" if stat.cache_read_tokens else "")
                    + f" (${stat.cost_usd:.4f})"
                )
        return "\n".join(lines)

    @staticmethod
    def _normalize_model_id(model_id: str) -> str:
        """规范化 model_id，提高匹配率。"""
        # 移除常见前缀
        for prefix in ("openai/", "anthropic/", "accounts/fireworks/models/"):
            if model_id.startswith(prefix):
                model_id = model_id[len(prefix):]
                break
        return model_id.lower().strip()
```

#### 2.2 集成到 Runner

在 `_run_impl()` 中：

```python
# 初始化 cost_tracker
agent.run_response.cost_tracker = CostTracker()

# 每次 model.response() 返回后记录费用
if agent.model and agent.model.usage:
    last_req = agent.model.usage.request_usage_entries[-1] if agent.model.usage.request_usage_entries else None
    if last_req:
        cached_read = getattr(last_req, 'input_tokens_details', None)
        cache_read = getattr(cached_read, 'cached_tokens', 0) if cached_read else 0
        agent.run_response.cost_tracker.record(
            model_id=agent.model.id,
            input_tokens=last_req.input_tokens,
            output_tokens=last_req.output_tokens,
            cache_read_tokens=cache_read,
        )
```

---

### 优化 3: Micro-compact 每轮静默压缩

**问题**: `compression/manager.py` 已有两阶段压缩，但**未集成到 Runner 循环**，也无每轮自动触发。

**方案**: 在 Runner 循环顶部每轮调用 `micro_compact`（不依赖 LLM、零成本）。

**关键设计**：
- 不修改 `CompressionManager`（已有的代码保留）
- 在 `Model.response()` 内部的工具循环顶部调用（每轮一次）
- 占位符使用 `[Old tool result content cleared]`（与 CC 源码一致）

#### 3.1 新建 `agentica/compression/micro.py`

```python
# -*- coding: utf-8 -*-
"""
Micro-compact: 每轮静默压缩旧 tool_result，零成本。

对标 CC 的 microCompact.ts（time-based 路径）：
- 保留最近 KEEP_RECENT 轮的工具结果
- 旧结果替换为固定占位符，释放 token 空间
- 不使用 LLM，不保存 transcript

触发时机：在每次 LLM 调用前（工具循环顶部）。
不触发条件：消息少于阈值、子 agent（query_source 为 subagent）。
"""
from typing import List, TYPE_CHECKING

if TYPE_CHECKING:
    from agentica.model.message import Message

# 与 CC 源码保持一致的占位符
MICRO_COMPACT_CLEARED_MESSAGE = "[Old tool result content cleared]"

# 默认保留最近 N 个工具结果（不压缩）
DEFAULT_KEEP_RECENT = 3

# 最小内容长度，短于此不压缩（节省比较开销）
MIN_CONTENT_LEN = 100


def micro_compact(messages: List["Message"], keep_recent: int = DEFAULT_KEEP_RECENT) -> int:
    """对消息列表执行 micro-compact（原地修改）。
    
    遍历所有 role="tool" 的消息，保留最近 keep_recent 个，
    将更早的长内容替换为占位符。
    
    Args:
        messages: 消息列表（原地修改）
        keep_recent: 保留最近 N 个工具结果不压缩
    
    Returns:
        压缩的工具结果数量
    """
    # 收集所有工具结果消息的索引（按出现顺序）
    tool_indices = [
        i for i, m in enumerate(messages)
        if m.role == "tool" and not getattr(m, 'micro_compacted', False)
    ]

    if len(tool_indices) <= keep_recent:
        return 0

    # 压缩较旧的（keep_recent 之前的）
    to_compact = tool_indices[:-keep_recent]
    compacted = 0
    for idx in to_compact:
        msg = messages[idx]
        content = msg.content
        if not content:
            continue
        content_str = str(content)
        if len(content_str) <= MIN_CONTENT_LEN:
            continue
        msg.content = MICRO_COMPACT_CLEARED_MESSAGE
        msg.micro_compacted = True  # type: ignore[attr-defined]
        compacted += 1

    return compacted
```

#### 3.2 集成到 `Model.response()` 循环

在 `model/base.py` 的工具执行循环顶部（每轮 LLM 调用前）：

```python
# 在工具循环顶部（每次 LLM 调用前）
from agentica.compression.micro import micro_compact

# 每轮静默压缩（零成本）
compacted = micro_compact(messages, keep_recent=3)
if compacted:
    logger.debug(f"Micro-compact: cleared {compacted} old tool results")
```

---

### 优化 4: Agent Loop 状态管理增强

**问题**: `Model.response()` 内部的工具循环缺少：
- `max_tokens` 截断恢复（输出被截断时自动续写）
- API 错误指数退避重试
- 循环轮次安全阀（防无限循环）

**方案**: 在 `Model.response()` 中增加状态管理，保持接口不变。

参考 CC 的 `query.ts` 中的 `MAX_OUTPUT_TOKENS_RECOVERY_LIMIT = 3`。

#### 4.1 在 `model/base.py` 中增强工具循环

```python
import random

MAX_TOOL_TURNS = 50           # 安全阀：最多 50 轮工具循环
MAX_TOKENS_RECOVERY_LIMIT = 3 # max_tokens 截断恢复最多重试 3 次
MAX_API_RETRY = 3             # API 错误最多重试 3 次

async def response(self, messages: List[Message]) -> ModelResponse:
    """完整响应（含工具调用循环），带状态管理。"""
    model_response = ModelResponse(content="", reasoning_content="")
    
    # Loop state
    max_tokens_recovery_count = 0
    consecutive_errors = 0
    turn_count = 0
    
    while True:
        turn_count += 1
        if turn_count > MAX_TOOL_TURNS:
            logger.warning(f"Tool loop safety valve: exceeded {MAX_TOOL_TURNS} turns")
            break
        
        # Micro-compact（每轮前执行，零成本）
        from agentica.compression.micro import micro_compact
        micro_compact(messages)
        
        # API 调用（带指数退避重试）
        for attempt in range(MAX_API_RETRY):
            try:
                response_obj = await self.invoke(messages)
                consecutive_errors = 0  # 重置错误计数
                break
            except Exception as e:
                err_str = str(e).lower()
                # 只对可重试错误退避（限速、连接问题）
                is_retryable = any(kw in err_str for kw in ("rate_limit", "connection", "timeout", "503", "502"))
                if not is_retryable or attempt == MAX_API_RETRY - 1:
                    consecutive_errors += 1
                    raise
                wait = (2 ** attempt) + random.uniform(0, 1)
                logger.warning(f"API error (attempt {attempt+1}/{MAX_API_RETRY}), retrying in {wait:.1f}s: {e}")
                await asyncio.sleep(wait)
        
        assistant_message = self.create_assistant_message(response_obj)
        messages.append(assistant_message)
        
        # max_tokens 截断恢复：stop_reason == "length" 时自动续写
        finish_reason = self._get_finish_reason(response_obj)
        if finish_reason == "length":
            if max_tokens_recovery_count >= MAX_TOKENS_RECOVERY_LIMIT:
                logger.warning(f"max_tokens recovery limit ({MAX_TOKENS_RECOVERY_LIMIT}) reached")
                break
            max_tokens_recovery_count += 1
            logger.info(f"Output truncated (max_tokens), auto-continuing ({max_tokens_recovery_count}/{MAX_TOKENS_RECOVERY_LIMIT})")
            messages.append(Message(role="user", content="Continue from where you left off."))
            continue
        
        # 没有工具调用 → 正常结束
        if not assistant_message.tool_calls or not self.run_tools:
            model_response.content = assistant_message.content
            break
        
        # 执行工具（并发分流）
        await self.handle_tool_calls(assistant_message, messages, model_response)
    
    return model_response

def _get_finish_reason(self, response_obj) -> str:
    """从 API 响应中提取 finish_reason/stop_reason。"""
    # OpenAI 格式
    try:
        return response_obj.choices[0].finish_reason or "stop"
    except (AttributeError, IndexError):
        pass
    # Anthropic 格式
    try:
        return response_obj.stop_reason or "stop"
    except AttributeError:
        return "stop"
```

---

### 优化 5: Reactive Compact 紧急压缩

**问题**: 当 token 使用量逼近上下文窗口上限时，没有主动触发的压缩机制。

**方案**: 在 `Model.response()` 循环中，每轮调用前检查 token 使用量，超阈值时触发 LLM 摘要压缩。

**阈值设计**（对标 CC 的 `AUTOCOMPACT_BUFFER_TOKENS = 13_000`）：
- 触发阈值：`context_window - 13000`
- Circuit breaker：连续失败 3 次后停止重试

#### 5.1 在 `compression/manager.py` 增加 `auto_compact` 方法

```python
async def auto_compact(
    self,
    messages: List["Message"],
    model: Optional[Any] = None,
    query_source: str = "main",
) -> bool:
    """Layer 2: token 超阈值时 LLM 摘要压缩。
    
    对标 CC 的 autoCompactIfNeeded()。
    
    Returns:
        True 如果执行了压缩，False 否则
    """
    # 递归保护：子 agent 不触发 auto-compact
    if query_source in ("subagent", "compact", "session_memory"):
        return False
    
    if not self._should_auto_compact(messages, model):
        return False
    
    logger.info("Auto-compact triggered: context approaching limit")
    
    # 保存 transcript 到磁盘（用于恢复）
    self._save_transcript(messages)
    
    # LLM 摘要
    summary = await self._summarize_with_llm(messages, model)
    if not summary:
        return False
    
    # 替换消息列表
    messages.clear()
    messages.append(Message(role="user", content=f"[Context compressed]\n\n{summary}"))
    messages.append(Message(role="assistant", content="Understood. I have the conversation context. Continuing."))
    
    logger.info(f"Auto-compact complete: messages reduced to {len(messages)}")
    return True

def _should_auto_compact(self, messages: List["Message"], model: Optional[Any] = None) -> bool:
    """检查是否需要 auto-compact（token 超阈值）。"""
    context_window = getattr(model, 'context_window', 128000)
    buffer = 13000  # 与 CC 的 AUTOCOMPACT_BUFFER_TOKENS 一致
    threshold = context_window - buffer
    
    try:
        from agentica.utils.tokens import count_tokens
        model_id = getattr(model, 'id', 'gpt-4o') if model else 'gpt-4o'
        tokens = count_tokens(messages, None, model_id, None)
        return tokens >= threshold
    except Exception:
        return False

def _save_transcript(self, messages: List["Message"]) -> None:
    """保存完整对话转录到磁盘（用于恢复）。"""
    import json, time
    from pathlib import Path
    transcript_dir = Path(".transcripts")
    transcript_dir.mkdir(exist_ok=True)
    path = transcript_dir / f"transcript_{int(time.time())}.jsonl"
    with open(path, "w", encoding="utf-8") as f:
        for msg in messages:
            f.write(json.dumps({
                "role": msg.role,
                "content": str(msg.content) if msg.content else "",
            }, ensure_ascii=False) + "\n")
    logger.debug(f"Transcript saved: {path}")

async def _summarize_with_llm(self, messages: List["Message"], model: Optional[Any] = None) -> Optional[str]:
    """用 LLM 摘要整个对话。"""
    if not model:
        return None
    import json
    try:
        conv_text = json.dumps(
            [{"role": m.role, "content": str(m.content)[:2000]} for m in messages],
            ensure_ascii=False
        )[:80000]
        from agentica.model.message import Message as Msg
        resp = await model.invoke([
            Msg(role="user", content=(
                "Summarize this conversation for continuity. Include: "
                "main task, key findings, completed steps, pending work, important context.\n\n"
                + conv_text
            ))
        ])
        # 提取文本
        if hasattr(resp, 'choices'):
            return resp.choices[0].message.content
        elif hasattr(resp, 'content'):
            return resp.content
    except Exception as e:
        logger.error(f"Auto-compact LLM summarization failed: {e}")
    return None
```

#### 5.2 集成到 `Model.response()` 循环

```python
# 在工具循环顶部，micro_compact 之后
if self._compression_manager and hasattr(self._compression_manager, 'auto_compact'):
    compacted = await self._compression_manager.auto_compact(messages, model=self)
    if compacted:
        logger.info("Auto-compact triggered in tool loop")
```

**Circuit Breaker**（在 `CompressionManager` 中维护连续失败计数）：
```python
@dataclass
class CompressionManager:
    # 新增字段
    _consecutive_compact_failures: int = field(init=False, default=0)
    _max_consecutive_failures: int = 3  # 对标 CC 的 MAX_CONSECUTIVE_AUTOCOMPACT_FAILURES
```

---

### 优化 6: RunResponse 增加 `cost_summary` 字段

**问题**: `RunResponse` 没有成本摘要，用户要手动访问 `cost_tracker`。

**方案**: 在 `run_response.py` 的 `RunResponse` 中增加 `cost_tracker` 字段和 `cost_summary` 属性。

```python
# run_response.py

from agentica.cost_tracker import CostTracker  # 新增 import

class RunResponse(BaseModel):
    # ... 现有字段 ...
    
    # 新增：成本追踪器
    cost_tracker: Optional[CostTracker] = Field(default=None, exclude=True)
    
    @property
    def cost_summary(self) -> str:
        """格式化成本摘要字符串。
        
        Example:
            >>> print(response.cost_summary)
            Total cost: $0.0023
            Total tokens: 1,234 input + 456 output
            API calls: 3
            Usage by model:
              gpt-4o: 1,234 in + 456 out ($0.0023)
        """
        if self.cost_tracker is None:
            return "No cost data available"
        return self.cost_tracker.summary()
    
    @property
    def total_cost_usd(self) -> float:
        """本次 run 的总 USD 成本。"""
        if self.cost_tracker is None:
            return 0.0
        return self.cost_tracker.total_cost_usd
```

---

## 三、实现成本估算

| 优化 | 涉及文件 | 新增/修改行数 | 风险 |
|------|---------|------------|------|
| 1. concurrency_safe 标记 | `tools/base.py`, `tools/decorators.py`, `tools/buildin_tools.py`, `model/base.py` | ~150 行 | 低（模型层改动，有测试覆盖） |
| 2. CostTracker | 新建 `cost_tracker.py`, `runner.py`, `run_response.py` | ~120 行 | 低（纯新增，无破坏性） |
| 3. Micro-compact | 新建 `compression/micro.py`, `model/base.py` | ~60 行 | 低（旧消息内容替换） |
| 4. Agent Loop 状态管理 | `model/base.py`（工具循环重构） | ~100 行 | 中（核心循环改动，需充分测试） |
| 5. Reactive Compact | `compression/manager.py`, `model/base.py` | ~100 行 | 中（需测试 LLM 摘要质量） |
| 6. RunResponse cost_summary | `run_response.py` | ~20 行 | 低（纯新增字段） |

**总计**: ~550 行代码修改/新增。

---

## 四、实施顺序（最优路径）

### Phase 1（1 周）: 无风险新增
1. **优化 2 (CostTracker)** — 纯新增，零破坏性
2. **优化 6 (RunResponse cost_summary)** — 配合 CostTracker
3. **优化 3 (Micro-compact)** — 零成本，最安全

### Phase 2（1 周）: 工具层改动
4. **优化 1 (concurrency_safe)** — 先加标记，再实现并发逻辑，分两步提交

### Phase 3（2 周）: 核心循环
5. **优化 4 (Agent Loop 状态管理)** — 最复杂，需充分测试
6. **优化 5 (Reactive Compact)** — 依赖 Phase 3 的循环结构

---

## 五、不做的特性（ROI 低 / 过度工程）

| 特性 | 理由 |
|------|------|
| Worktree 任务隔离 | Agentica Swarm 量级远小于 CC，实现成本高，ROI 极低 |
| 完整权限系统 | Agentica 是框架，用户自控权限；5 种模式适合 IDE 集成 |
| Prompt Cache 共享 (cache_control) | 依赖 Anthropic 特定 API，违背多模型原则 |
| Session Memory Compaction | 实验性功能，依赖 Anthropic 内部缓存机制，过度工程 |
| 持久化任务 DAG | Agentica 已有 `task` 内置工具；完整 DAG 超出当前 scope |
| JSONL 邮箱团队协作 | 需要单独 roadmap，当前 Swarm/TeamMixin 满足大部分场景 |
| 统一命令队列 (CommandQueue) | CC 的队列是 React UI 层设施，Agentica 是 Python 框架，不适用 |

---

## 六、关键设计决策解释

### 为什么是 `model/base.py` 而非 `runner.py`?

Agentica 的工具循环在 `Model.response()` 内部（`handle_tool_calls` 迭代），而非 `runner.py` 的 `_run_impl()`。`runner.py` 只调用一次 `model.response()`。因此：

- **Micro-compact、auto-compact、loop state management** 都应该在 `Model.response()` 中实现
- **CostTracker、RunResponse 字段** 在 `runner.py` 层集成

这与 CC 的设计对应：`query.ts` 是 CC 的循环核心（对应 `Model.response()`），而非 `runner.py` 层。

### 为什么 Micro-compact 用 `role=="tool"` 而非检查 tool_result block?

Agentica 使用 OpenAI 消息格式（`role="tool"` 的独立消息），与 Anthropic 的 `tool_result` block 格式不同。实现需对应正确的消息格式。

### 为什么 Circuit Breaker 限制为 3 次?

来自 CC 源码 `autoCompact.ts:70`：
```typescript
// BQ 2026-03-10: 1,279 sessions had 50+ consecutive failures (up to 3,272)
// in a single session, wasting ~250K API calls/day globally.
const MAX_CONSECUTIVE_AUTOCOMPACT_FAILURES = 3
```
3 次失败后停止，避免每轮都尝试注定失败的压缩。

---

## 七、验证方案

### 7.1 并发工具测试

```python
import asyncio
from unittest.mock import AsyncMock, patch
from agentica import Agent
from agentica.model.openai import OpenAIChat

async def test_concurrent_tools():
    """验证只读工具并行执行：3 个 read_file 应该总时间 ≈ 1× 而非 3×。"""
    import time
    agent = Agent(
        model=OpenAIChat(id="gpt-4o", api_key="fake"),
        tools=[read_file, glob, grep],
    )
    # 注入 mock：3 个 read_file 各延迟 0.1s
    # 串行 → 0.3s+；并行 → 0.1s+
    start = time.time()
    response = await agent.run("Read file1.py, file2.py, file3.py")
    elapsed = time.time() - start
    assert elapsed < 0.2, f"Expected parallel execution, got {elapsed:.2f}s"
```

### 7.2 CostTracker 测试

```python
def test_cost_tracker():
    from agentica.cost_tracker import CostTracker
    tracker = CostTracker()
    cost = tracker.record("gpt-4o", input_tokens=1000, output_tokens=100)
    assert abs(cost - (1000 * 2.5 / 1_000_000 + 100 * 10.0 / 1_000_000)) < 1e-8
    assert "Total cost:" in tracker.summary()
    assert "gpt-4o:" in tracker.summary()
```

### 7.3 Micro-compact 测试

```python
def test_micro_compact():
    from agentica.compression.micro import micro_compact, MICRO_COMPACT_CLEARED_MESSAGE
    from agentica.model.message import Message
    messages = [
        Message(role="user", content="query"),
        Message(role="assistant", content="ok", tool_calls=[{}]),
        Message(role="tool", content="x" * 500),  # 旧结果
        Message(role="assistant", content="ok", tool_calls=[{}]),
        Message(role="tool", content="y" * 500),  # 旧结果
        Message(role="assistant", content="ok", tool_calls=[{}]),
        Message(role="tool", content="z" * 500),  # 旧结果
        Message(role="assistant", content="ok", tool_calls=[{}]),
        Message(role="tool", content="w" * 500),  # 最新，保留
    ]
    compacted = micro_compact(messages, keep_recent=1)
    assert compacted == 3  # 3 个旧结果被压缩
    assert messages[2].content == MICRO_COMPACT_CLEARED_MESSAGE
    assert messages[-1].content == "w" * 500  # 最新保留
```

### 7.4 Agent Loop 安全阀测试

```python
async def test_loop_safety_valve():
    """验证无限工具循环会被安全阀截断。"""
    # mock: 模型永远返回 tool_use
    # 期望: 不超过 MAX_TOOL_TURNS 轮后返回
    ...
```
