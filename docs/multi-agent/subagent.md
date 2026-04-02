# Subagent

Subagent 系统允许主 Agent 生成隔离的临时子任务 Agent，用于处理复杂的分步骤任务。

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

注册和管理 Subagent 类型：

```python
from agentica.subagent import SubagentRegistry, SubagentType

registry = SubagentRegistry()

# 注册自定义类型
registry.register(SubagentConfig(
    type=SubagentType.CUSTOM,
    name="Data Analyst",
    description="数据分析专家",
    system_prompt="你是一个数据分析师...",
    allowed_tools=["sql_query", "python_eval"],
))

# 生成子任务
result = await registry.spawn(
    "analyze_data",
    agent_type=SubagentType.CUSTOM,
    task="分析销售数据趋势",
)
```

## SubagentRun

跟踪 Subagent 执行生命周期：

```python
@dataclass
class SubagentRun:
    run_id: str              # 唯一标识
    subagent_type: SubagentType
    parent_agent_id: str     # 父 Agent ID
    task_label: str          # 任务描述
    status: str              # pending/running/completed/failed
    result: Optional[str]    # 执行结果
    started_at: datetime
    completed_at: Optional[datetime]
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

- [Team](team.md) -- 团队协作
- [Swarm](swarm.md) -- 自主多智能体协作
- [Hooks](../advanced/hooks.md) -- 监控子任务执行
