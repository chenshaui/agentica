# Agentica × Claude Code — 源码精华与最优优化方案 v4

> 基于 Claude Code v2.1.88 源码深度阅读，整合三个版本的分析，去冗留精。
> 两大部分：**CC 源码架构发现**（借鉴参考）+ **Agentica 6 项高 ROI 优化实施方案**（可落地代码）。

---

## 第一部分：CC 源码架构精华（18 项重要发现）

### 1. Agent Loop 是一个精心设计的状态机

CC 的 `query.ts` 是核心循环，不是简单的 `while(true)`，而是**有 9 个 continue site 的状态机**：

| continue site | 触发条件 | 行为 |
|---|---|---|
| max_tokens 恢复 | `stop_reason == "max_tokens"` | 追加"Continue"消息，续写 |
| auto-compact | token 接近上下文窗口 | LLM 摘要压缩，替换消息列表 |
| 工具执行 | 存在 tool_use block | 执行工具，追加 tool_result，continue |
| 用户中断 | 用户按 Ctrl+C | 立即停止流式，释放 token |
| reactive compact | `prompt_too_long` 错误 | 紧急压缩，重试一次 |
| streaming fallback | 流式失败 | 降级为非流式重试 |
| 错误重试 | rate_limit / 连接错误 | 指数退避 + jitter |
| task budget | 超出任务预算 | 发出警告，优雅终止 |
| max turns | 超过安全阀轮数 | 强制终止 |

**对应关系**：Agentica 的 `Model.response()` 内部循环 ≈ CC 的 `query.ts`。

---

### 2. Tool 并发分层：精准并发而非全量并发

CC 的 `StreamingToolExecutor` 实现**三类工具的差异化并发策略**：

- `concurrency_safe = true`（66 个只读工具）：全部 `Promise.all` 并行
- `concurrency_safe = false`（Bash/写文件）：串行执行
- Bash 报错时：取消所有仍在等待的 safe 工具（避免后续操作依赖已失败的状态）

**66 个工具中只读工具代表**：FileRead, Glob, Grep, WebSearch, WebFetch, LS, NotebookRead 等。

---

### 3. 三层渐进式压缩

```
Micro-compact  (每轮前，零成本)
    ↓ token 接近阈值
Auto-compact   (LLM 摘要，context_window - 13000 触发)
    ↓ prompt_too_long 错误
Reactive compact (紧急，只触发一次)
```

**Micro-compact 关键设计**：
- 只压缩特定工具的结果：`COMPACTABLE_TOOLS = {FileRead, Shell, Grep, Glob, WebSearch, WebFetch, FileEdit, FileWrite}`
- 占位符固定为 `"[Old tool result content cleared]"`
- 保留最近 N 个结果不压缩（`keep_recent = 3`）
- 有"time-based"和"token-based"两种触发路径

**Auto-compact 关键参数**（`autoCompact.ts`）：
```typescript
const AUTOCOMPACT_BUFFER_TOKENS = 13_000          // 留给摘要输出的 buffer
const MAX_CONSECUTIVE_AUTOCOMPACT_FAILURES = 3    // circuit breaker
// 真实遥测数据注释：
// BQ 2026-03-10: 1,279 sessions had 50+ consecutive failures (up to 3,272)
// wasting ~250K API calls/day globally.
```
环境变量 `CLAUDE_CODE_AUTO_COMPACT_WINDOW` 可覆盖窗口大小。

---

### 4. Session Memory — 后台分叉子 Agent 维护记忆文件

`sessionMemory.ts` 实现的"会话记忆"系统：
- 注册 `postSamplingHook`，每次主 LLM 调用结束后异步触发
- Fork 一个独立的子 agent，专门提取关键信息写入 markdown 文件
- **不阻塞主循环**，不影响主对话的 token 消耗
- Gate-checked（门控检查）：满足 `{minTokens: 10_000, minTextBlockMessages: 5, maxTokens: 40_000}` 才触发

`sessionMemoryCompact.ts` 的"SM 优先"压缩策略：
```typescript
// 先尝试用 session memory 文件替换对话，再 fallback 到 LLM 摘要
// SM config: { minTokens: 10000, minTextBlockMessages: 5, maxTokens: 40000 }
```

---

### 5. 三级 Agent Memory 作用域

`agentMemory.ts` 定义三个持久化层次，**不同生命周期和可见性**：

| Scope | 路径 | 生命周期 | 用途 |
|---|---|---|---|
| `user` | `~/.claude/agent-memory/{agent_type}/` | 永久，跨项目 | 全局偏好、用户习惯 |
| `project` | `.claude/agent-memory/{agent_type}/` | 项目级 | 项目约定、决策记录 |
| `local` | `.claude/agent-memory-local/{agent_type}/` | 本地私有 | 开发者个人笔记 |

路径中的 agent_type 会经过 **Windows 冒号问题修复**（`sanitizePath`）。
环境变量 `CLAUDE_CODE_REMOTE_MEMORY_DIR` 支持远程内存挂载（含项目命名空间）。

---

### 6. 统一任务系统：7 种 TaskType

`tasks/types.ts` 定义：

```typescript
type TaskState =
  | LocalShellTask       // 本地 bash 命令
  | LocalAgentTask       // 本地子 agent
  | RemoteAgentTask      // 远程 agent（RPC）
  | InProcessTeammate    // 同进程队友 agent
  | LocalWorkflowTask    // 本地工作流
  | MonitorMcpTask       // MCP server 监控
  | DreamTask            // 推测性/异步任务（实验性）
```

6 个任务工具（`TaskCreate/Get/List/Output/Stop/Update`）构成完整生命周期管理，任务输出落盘（disk-persisted output files），支持恢复。
`isBackgroundTask()` predicate 用于 UI 指示器（区分前台/后台任务）。

---

### 7. Cost Tracker — 全链路成本追踪

`cost-tracker.ts` 的关键设计：
- **Per-model USD 计算**：支持所有主流模型价格表
- **会话持久化**：通过 `projectConfig` 写入磁盘，跨对话累计
- **Advisor tool 子追踪**：permission advisor 使用的 mini-model 费用单独记录
- **未知模型警告**：`hasUnknownModelCost()` 返回 true 时在 UI 显示提示
- **指标计数器**：追踪 tool 调用次数、成功率等 metrics

---

### 8. CLAUDE.md 硬编码注入：40K 字符固定配额

- 每次对话开始，`claude.md`（所有层级：global + project + local）被拼接注入
- **固定配额 40K 字符**，超出截断（不压缩）
- **反删除逻辑**：如果 LLM 尝试删除或修改 CLAUDE.md 内容，会触发警告
- **始终在对话顶层**：不会被 auto-compact 清除（保留系统提示层）
- 层级：`~/.claude/CLAUDE.md`（全局）→ `.claude/CLAUDE.md`（项目）→ `.claude/CLAUDE.local.md`（本地私有）

---

### 9. Prompt Cache 共享：子 Agent 零边际成本

Sub-agent 共享父 agent 的 prompt cache：
- `runAgent.ts` 中的 `cloneFileStateCache` 模式：子 agent 克隆父 agent 的文件状态缓存
- 父 agent 的 system prompt（含 CLAUDE.md、工具定义）已缓存，子 agent **零追加成本**
- 多个并行子 agent 共享同一 cache key（通过 `createSubagentContext` 注入）

---

### 10. Git Worktree 隔离模式

- `EnterWorktree` 在 `.claude/worktrees/` 创建独立工作树，AI 修改与用户工作目录物理隔离
- 工作树有独立 branch，合并/丢弃由用户决定
- 支持 `WorktreeCreate/WorktreeRemove` hooks 扩展（非 git 项目也可用）
- Sub-agent 可带 `isolation: "worktree"` 运行，完成后自动清理（无改动时）

---

### 11. 权限分类器 AI（Permission Classifier）

- 专用 mini-model 预测用户对某操作的授权倾向
- 基于历史操作模式打分，高置信度时自动批准，低置信度才弹窗
- 将权限弹窗数量减少约 90%
- 属于 `tools/PermissionTool` 的核心逻辑

---

### 12. 5 层 KonMari 压缩（Context Collapse）

上下文崩塌前的 5 步处理：
1. Micro-compact（清除旧 tool 结果占位符）
2. Drop 老旧 message rounds
3. Session Memory 提取关键信息到本地文件
4. LLM 摘要整个对话（auto-compact）
5. 保留 system prompt + CLAUDE.md，清空其余

最终效果："Conversation Memory"落盘，context 近乎归零。

---

### 13. 8KB 文件预览窗口策略

- `FileReadTool` 默认只读文件前 8KB 内容
- 通过内部索引（行号 + chunk ID）支持按需读取后续内容
- 对大文件（>100KB）自动分块索引
- 避免一次性将整个大文件塞入上下文

---

### 14. 全生命周期 Hooks

Pre/Post tool call hooks 支持自动化：
```typescript
// registerPostSamplingHook: 每次 LLM 采样后触发（用于 session memory）
// registerToolCallHook: 工具调用前后（用于 auto-lint, auto-format）
// ConversationArchiveHook: 每次 run 结束归档到 workspace
```

---

### 15. JSONL Transcript Archive — Resume & Fork

- 每次对话以 JSONL 格式序列化到 `~/.claude-internal/projects/{hash}/` 目录
- `resume` 操作：从上次对话 JSONL 恢复状态（save-state 语义）
- `fork` 操作：从某一历史点分叉，创建平行对话线（game save-state 语义）
- Session ID 即 JSONL 文件名前缀

---

### 16. 流式即时中断

- 用户 Ctrl+C 中断直接作用于流式层（不等待当前 chunk 完成）
- 立即调用 `AbortController.abort()`
- 已生成的 token 计入计费，中断后不再消耗
- 中断后对话历史保留已生成内容（partial assistant message 入库）

---

### 17. 优先级命令队列

单一事件队列管理所有输入类型，三个优先级：
- `now`：即时执行（用户中断、系统紧急事件）
- `next`：下轮执行（工具回调、permission 请求）
- `later`：排队执行（普通用户输入、task 通知）

React UI 层的设计，保证事件处理顺序可预期。

---

### 18. Sub-Agent 运行器细节（runAgent.ts）

- 初始化独立 MCP server 连接（每个 sub-agent 独立 MCP 状态）
- Perfetto tracing 集成（性能剖析）
- `runForkedAgent` 模式：与父 agent 共享 session hooks 但独立 message 历史
- Sub-agent 完成后自动汇报给父 agent（通过 `AgentTool` 返回值）

---

## 第二部分：Agentica 6 项高 ROI 优化方案

> 基线确认（实施前状态）：
> - Tool 执行：`model/base.py` 的 `run_function_calls()` 已用 `asyncio.gather` 并发，缺 `concurrency_safe` 分层
> - 上下文压缩：`compression/manager.py` 已实现两阶段，**未集成到循环触发**
> - 成本追踪：`model/usage.py` 有 token 统计，**无 USD 换算**
> - 循环保护：无 `max_tokens` 恢复、无错误重试、无安全阀
> - RunResponse：无 `cost_tracker` / `cost_summary` 字段

---

### 优化 1：Tool `concurrency_safe` 分层并发

**问题**：所有工具 `asyncio.gather` 全量并发，写入工具有隐式依赖风险；或串行执行导致 3 个 read_file 需要 3× 时间。

**方案**：只读工具并行，写入工具串行，Bash 报错取消后续写入工具。

#### 1.1 `Function` 新增标记（`agentica/tools/base.py`）

```python
class Function(BaseModel):
    # ... 现有字段 ...
    # 只读工具可并行，写入工具必须串行
    concurrency_safe: bool = False
```

#### 1.2 内置工具标记（`agentica/tools/buildin_tools.py`）

```python
_READ_ONLY_TOOLS = {"read_file", "glob", "grep", "web_search", "fetch_url", "ls"}

for name, func in _BUILTIN_FUNCTION_REGISTRY.items():
    if name in _READ_ONLY_TOOLS:
        func.concurrency_safe = True
```

#### 1.3 `@tool` 装饰器支持（`agentica/tools/decorators.py`）

```python
def tool(name=None, description=None, concurrency_safe: bool = False, ...):
    def decorator(func):
        func._tool_metadata = {..., "concurrency_safe": concurrency_safe}
        return func
    return decorator
```

#### 1.4 分层并发执行（替换 `model/base.py` 的 `run_function_calls()`）

```python
async def run_function_calls(self, function_calls, ...):
    safe_calls   = [fc for fc in function_calls if fc.function.concurrency_safe]
    unsafe_calls = [fc for fc in function_calls if not fc.function.concurrency_safe]

    results = {}

    # 只读工具并行
    if safe_calls:
        parallel_results = await asyncio.gather(
            *[self._invoke_single_tool(fc) for fc in safe_calls],
            return_exceptions=True
        )
        for fc, r in zip(safe_calls, parallel_results):
            results[fc.call_id] = (r, isinstance(r, Exception))

    # 写入工具串行（Bash 报错后跳过剩余）
    bash_aborted = False
    for fc in unsafe_calls:
        if bash_aborted:
            results[fc.call_id] = ("Cancelled: sibling bash tool errored", True)
            continue
        try:
            r = await self._invoke_single_tool(fc)
            results[fc.call_id] = (r, False)
        except Exception as e:
            results[fc.call_id] = (f"Error: {e}", True)
            if fc.function.name in {"execute", "bash"}:
                bash_aborted = True

    # 按原始顺序构建 tool messages（维持 OpenAI 消息顺序）
    for fc in function_calls:
        content, is_error = results[fc.call_id]
        messages.append(Message(
            role=tool_role,
            tool_call_id=fc.call_id,
            tool_name=fc.function.name,
            content=content,
            tool_call_error=is_error,
        ))
```

**预期收益**：3 个并行 read_file 耗时 1× 而非 3×，写入操作安全串行。

---

### 优化 2：CostTracker — USD 成本追踪

**问题**：用户运行 agent 后不知道花了多少钱。

**方案**：新建 `agentica/cost_tracker.py`，集成到 `RunResponse`。

#### 新文件 `agentica/cost_tracker.py`

```python
from dataclasses import dataclass, field
from typing import Dict, Optional

# 定价：每 1M tokens 的 USD 价格（2025 定价）
MODEL_PRICING: Dict[str, Dict[str, float]] = {
    # OpenAI
    "gpt-4o":               {"input": 2.50,  "output": 10.00, "cache_read": 1.25,  "cache_write": 2.50},
    "gpt-4o-mini":          {"input": 0.15,  "output": 0.60,  "cache_read": 0.075, "cache_write": 0.15},
    "gpt-4-turbo":          {"input": 10.00, "output": 30.00, "cache_read": 0.0,   "cache_write": 0.0},
    "o1":                   {"input": 15.00, "output": 60.00, "cache_read": 7.50,  "cache_write": 0.0},
    "o1-mini":              {"input": 1.10,  "output": 4.40,  "cache_read": 0.55,  "cache_write": 0.0},
    "o3-mini":              {"input": 1.10,  "output": 4.40,  "cache_read": 0.55,  "cache_write": 0.0},
    # Anthropic
    "claude-sonnet-4-20250514": {"input": 3.00,  "output": 15.00, "cache_read": 0.30, "cache_write": 3.75},
    "claude-haiku-3-5":         {"input": 0.80,  "output": 4.00,  "cache_read": 0.08, "cache_write": 1.00},
    "claude-opus-4":            {"input": 15.00, "output": 75.00, "cache_read": 1.50, "cache_write": 18.75},
    # DeepSeek
    "deepseek-chat":        {"input": 0.27,  "output": 1.10,  "cache_read": 0.07, "cache_write": 0.27},
    "deepseek-reasoner":    {"input": 0.55,  "output": 2.19,  "cache_read": 0.14, "cache_write": 0.55},
    # ZhipuAI
    "glm-4-flash":          {"input": 0.00,  "output": 0.00,  "cache_read": 0.0,  "cache_write": 0.0},
    "glm-4-air":            {"input": 0.14,  "output": 0.14,  "cache_read": 0.0,  "cache_write": 0.0},
    # Qwen
    "qwen-turbo":           {"input": 0.06,  "output": 0.18,  "cache_read": 0.0,  "cache_write": 0.0},
    "qwen-plus":            {"input": 0.40,  "output": 1.20,  "cache_read": 0.0,  "cache_write": 0.0},
    "qwen-max":             {"input": 0.40,  "output": 1.20,  "cache_read": 0.1,  "cache_write": 0.0},
    # Moonshot
    "moonshot-v1-8k":       {"input": 0.18,  "output": 0.18,  "cache_read": 0.0,  "cache_write": 0.0},
    # Doubao
    "doubao-pro-4k":        {"input": 0.11,  "output": 0.32,  "cache_read": 0.0,  "cache_write": 0.0},
}


@dataclass
class ModelUsageStat:
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0
    cost_usd: float = 0.0
    requests: int = 0


@dataclass
class CostTracker:
    """全链路成本追踪器，随 RunResponse 返回。

    用法：
        response = agent.run_sync("...")
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
        normalized = self._normalize_model_id(model_id)
        pricing = MODEL_PRICING.get(normalized)
        if pricing is None:
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
        stat = self.model_usage.setdefault(normalized, ModelUsageStat())
        stat.input_tokens       += input_tokens
        stat.output_tokens      += output_tokens
        stat.cache_read_tokens  += cache_read_tokens
        stat.cache_write_tokens += cache_write_tokens
        stat.cost_usd           += cost
        stat.requests           += 1

        self.total_cost_usd      += cost
        self.total_input_tokens  += input_tokens
        self.total_output_tokens += output_tokens
        self.turns               += 1
        return cost

    def summary(self) -> str:
        lines = [f"Total cost: ${self.total_cost_usd:.4f}"]
        if self.has_unknown_model:
            lines.append("  [warning: unknown model(s), costs may be underestimated]")
        lines.append(f"Total tokens: {self.total_input_tokens:,} input + {self.total_output_tokens:,} output")
        lines.append(f"API calls: {self.turns}")
        if self.model_usage:
            lines.append("Usage by model:")
            for model, stat in self.model_usage.items():
                cache_str = f" + {stat.cache_read_tokens:,} cache_read" if stat.cache_read_tokens else ""
                lines.append(f"  {model}: {stat.input_tokens:,} in + {stat.output_tokens:,} out{cache_str} (${stat.cost_usd:.4f})")
        return "\n".join(lines)

    @staticmethod
    def _normalize_model_id(model_id: str) -> str:
        for prefix in ("openai/", "anthropic/", "accounts/fireworks/models/"):
            if model_id.startswith(prefix):
                model_id = model_id[len(prefix):]
                break
        return model_id.lower().strip()
```

#### 集成到 `run_response.py`

```python
from agentica.cost_tracker import CostTracker

class RunResponse(BaseModel):
    # ... 现有字段 ...
    cost_tracker: Optional[CostTracker] = Field(default=None, exclude=True)

    @property
    def cost_summary(self) -> str:
        if self.cost_tracker is None:
            return "No cost data available"
        return self.cost_tracker.summary()

    @property
    def total_cost_usd(self) -> float:
        return self.cost_tracker.total_cost_usd if self.cost_tracker else 0.0
```

#### 集成到 `runner.py`

```python
from agentica.cost_tracker import CostTracker

# _run_impl() 开始时初始化
cost_tracker = CostTracker()

# model.response() 返回后记录（每轮）
if agent.model and agent.model.usage:
    usage = agent.model.usage
    cost_tracker.record(
        model_id=agent.model.id,
        input_tokens=usage.input_tokens or 0,
        output_tokens=usage.output_tokens or 0,
        cache_read_tokens=getattr(usage, 'cache_read_tokens', 0),
    )

# 最终写入 RunResponse
run_response.cost_tracker = cost_tracker
```

---

### 优化 3：Micro-compact — 每轮静默压缩

**问题**：`CompressionManager` 已实现，但没被循环调用；长对话中旧 tool 结果白白占用大量 token。

**方案**：在 `Model.response()` 循环**每轮 LLM 调用前**执行轻量规则截断，零 LLM 成本。

#### 新文件 `agentica/compression/micro.py`

```python
"""
Micro-compact: 每轮静默压缩旧 tool_result，零 LLM 调用成本。

对标 CC 的 microCompact.ts（time-based 路径）：
- 只压缩特定工具的旧结果（read_file, execute, grep, glob, web_search 等）
- 保留最近 keep_recent 轮不压缩
- 占位符固定为 CC 源码一致的字符串
"""
from typing import List, TYPE_CHECKING

if TYPE_CHECKING:
    from agentica.model.message import Message

MICRO_COMPACT_CLEARED_MESSAGE = "[Old tool result content cleared]"
DEFAULT_KEEP_RECENT = 3
MIN_CONTENT_LEN = 100

# 对标 CC 的 COMPACTABLE_TOOLS（只压缩这些工具的旧结果）
COMPACTABLE_TOOL_NAMES = {
    "read_file", "execute", "bash", "grep", "glob",
    "web_search", "fetch_url", "write_file", "edit_file",
}


def micro_compact(messages: List["Message"], keep_recent: int = DEFAULT_KEEP_RECENT) -> int:
    """对消息列表执行 micro-compact（原地修改）。

    Returns:
        压缩的工具结果数量
    """
    tool_indices = [
        i for i, m in enumerate(messages)
        if m.role == "tool" and not getattr(m, 'micro_compacted', False)
    ]
    if len(tool_indices) <= keep_recent:
        return 0

    to_compact = tool_indices[:-keep_recent]
    compacted = 0
    for idx in to_compact:
        msg = messages[idx]
        # 只压缩可压缩工具的结果
        tool_name = getattr(msg, 'tool_name', '') or ''
        if tool_name and tool_name not in COMPACTABLE_TOOL_NAMES:
            continue
        content = msg.content
        if not content or len(str(content)) <= MIN_CONTENT_LEN:
            continue
        msg.content = MICRO_COMPACT_CLEARED_MESSAGE
        msg.micro_compacted = True  # type: ignore[attr-defined]
        compacted += 1
    return compacted
```

#### 集成到 `model/base.py` 的工具循环

```python
from agentica.compression.micro import micro_compact

# 在工具循环顶部（每次 LLM 调用前）
compacted = micro_compact(messages, keep_recent=3)
if compacted:
    logger.debug(f"Micro-compact: cleared {compacted} old tool results")
```

#### 注入 `_compression_manager`（`agent/__init__.py`）

```python
if hasattr(self, 'compression') and self.compression is not None:
    self.model._compression_manager = self.compression
```

**预期收益**：长对话每轮节省 5–30% token，零 LLM 调用成本。

---

### 优化 4：Agent Loop 状态管理增强

**问题**：`Model.response()` 内部循环缺少三个防护机制。

#### 4a：max_output_tokens 截断恢复

```python
# model/openai/chat.py  response() 工具循环内
MAX_TOKENS_RECOVERY_LIMIT = 3   # 对标 CC 的 MAX_OUTPUT_TOKENS_RECOVERY_LIMIT
max_tokens_recovery_count = 0

finish_reason = self._get_finish_reason(response_obj)
if finish_reason == "length":
    if max_tokens_recovery_count >= MAX_TOKENS_RECOVERY_LIMIT:
        logger.warning(f"max_tokens recovery limit ({MAX_TOKENS_RECOVERY_LIMIT}) reached")
        break
    max_tokens_recovery_count += 1
    logger.info(f"Output truncated, auto-continuing ({max_tokens_recovery_count}/{MAX_TOKENS_RECOVERY_LIMIT})")
    messages.append(assistant_message)
    messages.append(Message(role="user", content="Continue from where you left off."))
    continue
```

#### 4b：API 错误指数退避重试

```python
# model/base.py — 新增 _invoke_with_retry
import random

MAX_API_RETRY = 3

async def _invoke_with_retry(self, messages) -> Any:
    for attempt in range(MAX_API_RETRY):
        try:
            return await self.invoke(messages)
        except Exception as e:
            err_str = str(e).lower()
            is_retryable = any(kw in err_str for kw in
                               ("rate_limit", "connection", "timeout", "503", "502"))
            if not is_retryable or attempt == MAX_API_RETRY - 1:
                raise
            wait = (2 ** attempt) + random.uniform(0, 1)
            logger.warning(f"API error (attempt {attempt+1}/{MAX_API_RETRY}), retry in {wait:.1f}s: {e}")
            await asyncio.sleep(wait)
```

#### 4c：循环安全阀

```python
MAX_TOOL_TURNS = 50   # 对标 CC 的 maxTurns 默认值
turn_count = 0
while True:
    turn_count += 1
    if turn_count > MAX_TOOL_TURNS:
        logger.warning(f"Tool loop safety valve: exceeded {MAX_TOOL_TURNS} turns, forcing stop")
        break
    ...
```

---

### 优化 5：Reactive Compact — 紧急上下文压缩

**问题**：上下文超长时 API 直接报 `context_length_exceeded`，运行崩溃。

**方案**：捕获上下文超长错误，紧急压缩后重试一次；同时在 `CompressionManager` 添加 token 阈值触发的 `auto_compact`。

#### 5.1 `_invoke_with_retry` 扩展（`model/base.py`）

```python
async def _invoke_with_retry(self, messages) -> Any:
    reactive_attempted = False
    for attempt in range(MAX_API_RETRY):
        try:
            return await self.invoke(messages)
        except Exception as e:
            err_str = str(e).lower()
            # context 超长：紧急压缩，只重试一次
            if any(kw in err_str for kw in
                   ("context_length_exceeded", "prompt_too_long", "maximum context length")):
                if not reactive_attempted:
                    reactive_attempted = True
                    logger.warning("Context too long, attempting reactive compact")
                    await self._reactive_compact(messages)
                    continue
                raise  # 压缩后仍超长，放弃
            # 可重试错误：指数退避
            if any(kw in err_str for kw in ("rate_limit", "connection", "timeout", "503", "502")):
                if attempt == MAX_API_RETRY - 1:
                    raise
                await asyncio.sleep((2 ** attempt) + random.uniform(0, 1))
                continue
            raise  # 其他错误直接抛出

async def _reactive_compact(self, messages) -> None:
    cm = getattr(self, '_compression_manager', None)
    if cm is not None:
        # 使用 CompressionManager 现有方法
        cm._drop_old_messages(messages)
        if getattr(cm, 'use_llm_compression', False):
            await cm._llm_compress_old_tool_results(messages)
    else:
        # fallback：保留最近 3 轮
        _keep_recent_rounds(messages, keep_rounds=3)
```

#### 5.2 `CompressionManager` 新增 `auto_compact` 方法（`compression/manager.py`）

```python
# 新增字段（circuit breaker）
_consecutive_compact_failures: int = 0
MAX_CONSECUTIVE_FAILURES: int = 3   # 对标 CC 源码真实遥测数据
AUTOCOMPACT_BUFFER_TOKENS: int = 13_000   # 对标 CC 的 AUTOCOMPACT_BUFFER_TOKENS

async def auto_compact(
    self,
    messages: List["Message"],
    model: Optional[Any] = None,
    query_source: str = "main",
) -> bool:
    """token 超阈值时 LLM 摘要压缩。对标 CC 的 autoCompactIfNeeded()。"""
    # 递归保护：子 agent 不触发 auto-compact
    if query_source in ("subagent", "compact", "session_memory"):
        return False
    # Circuit breaker：连续失败超过 3 次停止尝试
    if self._consecutive_compact_failures >= self.MAX_CONSECUTIVE_FAILURES:
        return False
    if not self._should_auto_compact(messages, model):
        return False

    logger.info("Auto-compact triggered: context approaching limit")
    try:
        self._save_transcript(messages)
        summary = await self._summarize_with_llm(messages, model)
        if not summary:
            self._consecutive_compact_failures += 1
            return False
        messages.clear()
        messages.append(Message(role="user", content=f"[Context compressed]\n\n{summary}"))
        messages.append(Message(role="assistant", content="Understood. Continuing."))
        self._consecutive_compact_failures = 0
        logger.info(f"Auto-compact complete: messages reduced to {len(messages)}")
        return True
    except Exception as e:
        self._consecutive_compact_failures += 1
        logger.error(f"Auto-compact failed ({self._consecutive_compact_failures}/{self.MAX_CONSECUTIVE_FAILURES}): {e}")
        return False

def _should_auto_compact(self, messages, model=None) -> bool:
    context_window = getattr(model, 'context_window', 128_000)
    threshold = context_window - self.AUTOCOMPACT_BUFFER_TOKENS
    try:
        from agentica.utils.tokens import count_tokens
        model_id = getattr(model, 'id', 'gpt-4o') if model else 'gpt-4o'
        tokens = count_tokens(messages, None, model_id, None)
        return tokens >= threshold
    except Exception:
        return False
```

---

### 优化 6：`RunResponse` 完整成本摘要入口

（与优化 2 协同，完整数据流链路）

```
LLM 调用 (invoke)
  -> model.usage 更新 token 统计
  -> runner._run_impl():
       cost_tracker.record(model.id, input, output, cache_read)
  -> 最终:
       run_response.cost_tracker = cost_tracker
  -> 用户访问:
       response.cost_summary       # 格式化字符串
       response.total_cost_usd     # float
       response.cost_tracker       # 完整 CostTracker 对象
```

**用户使用示例**：

```python
response = agent.run_sync("分析这些文件并生成报告")
print(response.cost_summary)
# Total cost: $0.0023
# Total tokens: 1,234 input + 456 output
# API calls: 3
# Usage by model:
#   gpt-4o: 1,234 in + 456 out ($0.0023)
```

---

## 第三部分：实施规划

### 文件改动清单

| 文件 | 改动类型 | 约行数 | 说明 |
|------|---------|-------|------|
| `agentica/cost_tracker.py` | **新增** | ~120 | CostTracker + 价格表 |
| `agentica/compression/micro.py` | **新增** | ~60 | Micro-compact |
| `agentica/run_response.py` | 修改 | +20 | cost_tracker 字段 + 属性 |
| `agentica/tools/base.py` | 修改 | +5 | Function.concurrency_safe 字段 |
| `agentica/tools/decorators.py` | 修改 | +10 | @tool 支持 concurrency_safe 参数 |
| `agentica/tools/buildin_tools.py` | 修改 | ~10 | 只读工具标记 |
| `agentica/model/base.py` | 修改 | ~120 | 分层并发 + micro_compact + retry + reactive compact |
| `agentica/model/openai/chat.py` | 修改 | ~60 | max_tokens 恢复 + 安全阀 |
| `agentica/model/anthropic/*.py` | 修改 | ~40 | 同 OpenAI 模式 |
| `agentica/compression/manager.py` | 修改 | ~80 | auto_compact + circuit breaker |
| `agentica/agent/__init__.py` | 修改 | ~10 | 注入 _compression_manager |
| `agentica/runner.py` | 修改 | ~20 | cost_tracker 初始化 + 写入 RunResponse |
| **合计** | — | **~555 行** | — |

---

### 实施顺序（建议 2 周）

```
Phase 1 — 无风险新增（Day 1–4）
  Day 1-2: 优化 2 + 优化 6  (CostTracker + RunResponse 字段，零破坏性)
  Day 3-4: 优化 3 (Micro-compact，零 LLM 成本，最安全)

Phase 2 — 工具层改动（Day 5–7）
  Day 5-7: 优化 1 (concurrency_safe 分层并发，分两步：先加标记，再实现并发逻辑)

Phase 3 — 核心循环（Day 8–12）
  Day 8-10: 优化 4 (Loop 状态管理：max_tokens 恢复 + retry + 安全阀)
  Day 11-12: 优化 5 (Reactive compact + auto_compact + circuit breaker)

Phase 4 — 测试 + 文档（Day 13–14）
```

---

### v3 目标架构图

```
User -> Agent -> Runner (_run_impl, 单轮 generator，不变)
                    |
                    v
                Model.response(messages)
                    |
                    +-- [优化 3] micro_compact(messages)
                    |
                    +-- while True:  (工具循环，含安全阀 [优化 4c])
                    |     |
                    |     +-- [优化 4b] _invoke_with_retry(messages)
                    |     |     |-- [优化 5] reactive_compact on context error
                    |     |     |-- rate_limit 指数退避
                    |     |
                    |     +-- [优化 5] auto_compact (token 阈值检查)
                    |     |
                    |     +-- [优化 4a] finish_reason=="length" -> 续写 continue
                    |     |
                    |     +-- run_function_calls()
                    |           +-- [优化 1] safe tools  -> asyncio.gather (并行)
                    |           +-- [优化 1] unsafe tools -> serial (串行)
                    |           +-- [优化 1] bash error  -> cancel rest
                    |
                    +-- [优化 2] cost_tracker.record(usage)
                    |
                    v
               RunResponse
                    +-- [优化 6] cost_tracker: CostTracker
                    +-- cost_summary (property)
                    +-- total_cost_usd (property)
```

---

### 设计决策说明

**为什么改 `Model.response()` 而非 `runner.py`？**

Agentica 的工具循环在 `Model.response()` 内部（`handle_tool_calls` 迭代），`runner.py` 只调用一次 `model.response()`。因此 micro-compact、auto-compact、loop state management 都应在 `Model.response()` 中实现，与 CC 的 `query.ts`（CC 循环核心）对应。

**为什么 Circuit Breaker 限制 3 次？**

直接来自 CC `autoCompact.ts` 真实遥测注释：1,279 个会话出现 50+ 次连续失败（最多 3,272 次），每天全局浪费约 25 万次 API 调用。3 次失败后停止是实测最优值。

**为什么 Micro-compact 只压缩特定工具？**

CC 定义 `COMPACTABLE_TOOLS` 集合，是因为某些工具的结果（如用户消息、决策记录）不应被清除，只有文件内容、搜索结果等可安全替换为占位符。

---

### 不实施项目（ROI 分析）

| 项目 | 不实施理由 |
|------|----------|
| Worktree 任务隔离 | Agentica Swarm 量级远小于 CC，ROI 极低 |
| 完整权限系统 | Agentica 是框架，用户自控权限；5 种 RunConfig 模式已覆盖 |
| Prompt Cache 共享（cache_control）| 依赖 Anthropic 特定 API，违背多模型原则 |
| Session Memory Compaction | 实验性功能，依赖 Anthropic 内部缓存机制，过度工程 |
| 持久化任务 DAG | 已有 `task` 内置工具；完整 DAG 超出当前 scope |
| JSONL 邮箱团队协作 | 需独立 roadmap，Swarm/TeamMixin 已满足大部分场景 |
| 统一命令队列（CommandQueue）| CC 队列是 React UI 层设施，Agentica 是 Python 框架，不适用 |
| 三级 Agent Memory 作用域 | 现有 Workspace（`WorkspaceConfig`）已实现 project 级持久化；user 级可作 P2 |
| 权限分类器 AI | 需独立 mini-model + 历史数据训练，超出当前 scope |

---

### 测试验证方案

```python
# 1. 并发工具测试：验证只读工具并行
async def test_concurrent_tools():
    # mock 3 个 read_file 各延迟 0.1s，总时间应 ~0.1s 而非 ~0.3s
    ...

# 2. CostTracker 精度测试
def test_cost_tracker():
    from agentica.cost_tracker import CostTracker
    tracker = CostTracker()
    cost = tracker.record("gpt-4o", input_tokens=1_000_000, output_tokens=0)
    assert abs(cost - 2.50) < 1e-8   # $2.50/M input tokens

# 3. Micro-compact 正确性测试
def test_micro_compact():
    from agentica.compression.micro import micro_compact, MICRO_COMPACT_CLEARED_MESSAGE
    # 4 个 tool 消息，keep_recent=1，期望压缩 3 个
    ...

# 4. 安全阀测试：无限工具循环不超过 MAX_TOOL_TURNS
async def test_loop_safety_valve():
    # mock 模型永远返回 tool_use，期望在 MAX_TOOL_TURNS 轮后退出
    ...

# 5. Circuit Breaker 测试：auto_compact 连续失败后停止
async def test_auto_compact_circuit_breaker():
    # mock LLM 摘要总是失败，期望在 3 次后不再调用
    ...
```
