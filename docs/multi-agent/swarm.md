# Swarm

Swarm 模式实现对等自主多智能体协作，与 `Agent.as_tool()`（轻量黑盒组合）和 Workflow（确定性管道）相比，更适合需要多 worker 并行/自治分工的场景。

## 核心概念

- **Coordinator** 分析任务并分配子任务给 Worker
- **Worker** 并行执行子任务
- **Synthesizer** 综合所有结果为最终输出

## 两种模式

### parallel 模式

所有 Agent 执行相同任务，结果合并：

```python
from agentica import Swarm, Agent, OpenAIChat

agents = [
    Agent(name="Analyst-1", model=OpenAIChat(id="gpt-4o")),
    Agent(name="Analyst-2", model=OpenAIChat(id="gpt-4o")),
    Agent(name="Analyst-3", model=OpenAIChat(id="gpt-4o")),
]

swarm = Swarm(agents=agents)
result = await swarm.run("分析2024年AI市场趋势", mode="parallel")
print(result.content)
```

### autonomous 模式

Coordinator 分解任务，分配给最合适的 Worker：

```python
from agentica import Swarm, Agent, OpenAIChat, BaiduSearchTool, ShellTool

researcher = Agent(
    name="researcher",
    model=OpenAIChat(id="gpt-4o"),
    tools=[BaiduSearchTool()],
    description="负责信息搜索和资料整理",
)

coder = Agent(
    name="coder",
    model=OpenAIChat(id="gpt-4o"),
    tools=[ShellTool()],
    description="负责代码编写和执行",
)

coordinator = Agent(
    name="coordinator",
    model=OpenAIChat(id="gpt-4o"),
)

swarm = Swarm(
    agents=[researcher, coder],
    coordinator=coordinator,
)

result = await swarm.run(
    "搜索最新的 Transformer 论文，并用 PyTorch 实现一个简单的 Transformer encoder",
    mode="autonomous",
)
print(result.content)
```

## 执行流程（autonomous 模式）

```
用户任务
    |
    v
Coordinator 分析任务
    |
    v
生成 JSON 子任务分配：
[
  {"agent_name": "researcher", "subtask": "搜索 Transformer 论文"},
  {"agent_name": "coder", "subtask": "实现 Transformer encoder"}
]
    |
    v
Worker 并行执行（各自独立克隆，无状态共享）
    |
    v
Synthesizer 综合结果
    |
    v
最终输出
```

## SwarmResult

```python
@dataclass
class SwarmResult:
    content: str                          # 最终合成内容
    agent_results: List[Dict[str, Any]]   # 各 Agent 的单独结果
    mode: str                             # "parallel" 或 "autonomous"
    total_time: float                     # 总执行时间
```

## Agent 克隆隔离

Swarm 为每个子任务创建 Agent 的隔离克隆：

- 共享配置（model, instructions, tools）
- 独立运行时状态（agent_id, WorkingMemory, Runner）
- 避免并发共享状态冲突

## 下一步

- [Workflow](workflow.md) -- 确定性流水线
- [Subagent](subagent.md) -- 子任务委派
