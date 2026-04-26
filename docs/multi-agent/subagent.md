# Subagent

Subagent 系统允许主 Agent 生成隔离的临时子任务 Agent，用于处理复杂的分步骤任务。

如果只是固定步骤流水线，优先使用 [Workflow](workflow.md)。如果只是让父 Agent 调用一个专门助手，优先使用 `Agent.as_tool()`。只有当子任务需要独立运行时状态、工具权限、嵌套深度限制或超时控制时，再使用 Subagent；完整取舍见 [编排模式决策树](choosing.md)。

## SubagentType

```python
from agentica.subagent import SubagentType

class SubagentType(str, Enum):
    EXPLORE = "explore"    # 代码库探索（只读）
    RESEARCH = "research"  # 网页搜索和文档分析
    CODE = "code"          # 代码生成和执行
    CUSTOM = "custom"      # 用户自定义类型
```

## SubagentConfig

每种 Subagent 类型有独立的权限配置：

```python
from agentica.subagent import SubagentConfig, SubagentType

config = SubagentConfig(
    type=SubagentType.RESEARCH,
    name="Research Agent",
    description="负责搜索和分析文档",
    system_prompt="你是一个专业的研究助手...",
    allowed_tools=["web_search", "fetch_url"],
    denied_tools=["execute"],
    tool_call_limit=100,
    can_spawn_subagents=False,
    inherit_workspace=False,
    inherit_knowledge=False,
    timeout=300,  # 秒
)
```

| 参数 | 说明 |
|------|------|
| `allowed_tools` | 允许使用的工具（None = 继承父 Agent 全部） |
| `denied_tools` | 禁用的工具（优先级高于 allowed） |
| `tool_call_limit` | 最大工具调用次数 |
| `can_spawn_subagents` | 是否允许生成子 Subagent |
| `inherit_workspace` | 是否继承父 Agent 的 Workspace |
| `inherit_knowledge` | 是否继承父 Agent 的知识库 |
| `timeout` | 执行超时秒数 |

## SubagentRegistry

`SubagentRegistry` 是 Subagent 执行的唯一入口，负责：模型克隆 + 工具继承（自动按 `BLOCKED_TOOLS` / `allowed_tools` / `denied_tools` 过滤父 Agent 工具）+ 嵌套深度限制（`MAX_DEPTH=2`）+ 注册表跟踪 + 实时事件冒泡 + usage 合并 + 超时控制。

```python
from agentica import Agent, OpenAIChat
from agentica.subagent import (
    SubagentRegistry,
    SubagentType,
    register_custom_subagent,
)

# 注册自定义 Subagent 类型（模块级，全局生效）
register_custom_subagent(
    name="data_analyst",
    description="数据分析专家",
    system_prompt="你是一个数据分析师...",
    allowed_tools=["sql_query", "python_eval"],
)

parent = Agent(model=OpenAIChat(id="gpt-4o"), tools=[...])

# 直接调用 spawn() 启动子 Agent
result = await SubagentRegistry().spawn(
    parent_agent=parent,
    task="分析销售数据趋势",
    agent_type="data_analyst",
)
# result = {
#   "status": "completed",
#   "content": "...",
#   "agent_type": "custom",
#   "subagent_name": "data_analyst",
#   "run_id": "...",
#   "tool_calls_summary": [...],
#   "tool_count": N,
#   "execution_time": 12.345,
# }
```

## SubagentRun

跟踪 Subagent 执行生命周期：

```python
@dataclass
class SubagentRun:
    run_id: str              # 唯一标识
    subagent_type: SubagentType
    parent_agent_id: str     # 父 Agent agent_id
    task_label: str          # 截短的任务标签
    task_description: str    # 完整任务描述
    started_at: datetime
    status: str              # pending / running / completed / error / cancelled
    ended_at: Optional[datetime]
    result: Optional[str]
    error: Optional[str]
    token_usage: Optional[Dict[str, int]]
```

## 与 DeepAgent 内置 task 工具的关系

`DeepAgent` 的内置 `task` 工具底层使用 Subagent 系统：

```python
from agentica import DeepAgent, OpenAIChat

agent = DeepAgent(model=OpenAIChat(id="gpt-4o"))
# Agent 可通过 task 工具自动委派子任务
result = await agent.run("分析项目代码结构并生成文档")
```

## 下一步

- [Swarm](swarm.md) -- 自主多智能体协作
- [Workflow](workflow.md) -- 确定性工作流编排
- [Hooks](../advanced/hooks.md) -- 监控子任务执行
