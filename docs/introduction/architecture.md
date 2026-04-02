# Architecture Overview

Agentica 采用分层架构，将 Agent 的身份定义与执行引擎解耦。

## 系统架构图

<div align="center">
  <img src="../assets/architecturev2.jpg" alt="Architecture" width="800" />
</div>

## 五层架构

```
+------------------------------------------------------------------+
|                        Application Layer                          |
|   CLI / Web UI / API Service / ACP Server                        |
+------------------------------------------------------------------+
|                        Orchestration Layer                         |
|   Team (delegation) | Workflow (pipeline) | Swarm (autonomous)   |
+------------------------------------------------------------------+
|                          Agent Layer                               |
|   Agent  <-->  Runner (execution engine)                          |
|   PromptsMixin | ToolsMixin | TeamMixin | PrinterMixin           |
+------------------------------------------------------------------+
|                          Model Layer                               |
|   OpenAI | Anthropic | ZhipuAI | DeepSeek | Ollama | ...        |
|   Tool loop | Compression | Death spiral | Cost budget           |
+------------------------------------------------------------------+
|                        Infrastructure Layer                        |
|   Tools | Knowledge(RAG) | Memory | VectorDB | MCP | Hooks      |
+------------------------------------------------------------------+
```

## Agent 与 Runner 的分离

Agent 定义 **"我是谁、我能做什么"**，Runner 负责 **"怎么执行"**：

```python
@dataclass(init=False)
class Agent(PromptsMixin, TeamMixin, ToolsMixin, PrinterMixin):
    def __init__(self, ...):
        self._runner = Runner(self)

    async def run(self, message, **kw) -> RunResponse:
        return await self._runner.run(message, **kw)
```

### Runner 执行流程

```
User Message
    |
    v
[InputGuardrail] -- 输入验证
    |
    v
Runner._run_impl()
    |
    +--> 构建 System Prompt (PromptsMixin)
    +--> 调用 Model (LLM API)
    |       |
    |       v
    |   Model Response
    |       |
    |       +--> 有 tool_calls? --> 执行工具 --> [ToolGuardrail]
    |       |                           |
    |       |                           v
    |       |                     工具结果加入消息
    |       |                           |
    |       |                           v
    |       |                     继续 Model 循环 (agentic loop)
    |       |
    |       +--> 无 tool_calls? --> 返回响应
    |
    v
[OutputGuardrail] -- 输出验证
    |
    v
RunResponse
```

## System Prompt 三区结构

为最大化 LLM prefix-cache 命中率，System Prompt 分为三个区域：

```
+-- STATIC ZONE (不变) -----------------------------------+
|  description, task, role, team, instructions,           |
|  guidelines, expected_output, additional_context        |
+---------------------------------------------------------+
|                                                         |
+-- SEMI-STATIC ZONE (少变) ------------------------------+
|  workspace context (AGENT.md), git status,              |
|  model system message, team transfer prompt             |
+---------------------------------------------------------+
|                                                         |
+-- DYNAMIC ZONE (每轮可变) ------------------------------+
|  workspace memory, session summary, datetime,           |
|  json output prompt                                     |
+---------------------------------------------------------+
```

静态内容在前，动态内容在后。同一分钟内的请求共享相同的 prefix-cache。

## 数据流

### 非流式

```
agent.run(message)
  -> Runner.run()
    -> Runner._run_impl()
      -> Model.response(messages)
        -> [tool loop if needed]
      <- ModelResponse
    <- RunResponse
```

### 流式

```
agent.run_stream(message)
  -> Runner.run_stream()
    -> Runner._run_impl_stream()
      -> Model.response_stream(messages)
        -> [SSE token-by-token]
        -> [tool loop if needed]
      <- AsyncIterator[ModelResponse]
    <- AsyncIterator[RunResponse]
```

## 安全机制

| 机制 | 说明 |
|------|------|
| **Guardrails** | 4 层守卫: InputGuardrail, OutputGuardrail, ToolInputGuardrail, ToolOutputGuardrail |
| **Death Spiral Detection** | 连续 5 轮全部工具调用失败时自动停止 |
| **Cost Budget** | `RunConfig(max_cost_usd=N)` 设置运行成本上限 |
| **Stream Idle Timeout** | 检测流式响应中的 "静默挂起" |

## 下一步

- [安装指南](../getting-started/installation.md)
- [Agent 核心概念](../concepts/agent.md)
- [Hooks 生命周期](../advanced/hooks.md)
