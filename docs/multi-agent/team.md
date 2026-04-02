# Team

Team 模式下，一个主 Agent 充当协调者，根据任务需要动态委派给团队成员。

## 基本用法

```python
import asyncio
from agentica import Agent, OpenAIChat, BaiduSearchTool

# 专业研究员
researcher = Agent(
    name="Researcher",
    model=OpenAIChat(id="gpt-4o"),
    tools=[BaiduSearchTool()],
    description="负责信息搜索和资料整理",
    instructions=["搜索相关信息并整理要点"],
)

# 专业写手
writer = Agent(
    name="Writer",
    model=OpenAIChat(id="gpt-4o"),
    description="负责内容写作和文章润色",
    instructions=["基于研究材料撰写高质量文章"],
)

# 团队协调者
team = Agent(
    name="Editor",
    team=[researcher, writer],
    instructions=[
        "你是一个编辑，协调研究员和写手完成任务",
        "研究任务交给 Researcher",
        "写作任务交给 Writer",
    ],
)

async def main():
    await team.print_response_stream("写一篇关于 AI Agent 最新发展的文章")

asyncio.run(main())
```

## 工作原理

1. 团队成员通过 `as_tool()` 自动转换为工具注册到主 Agent
2. 主 Agent 根据任务描述决定调用哪个成员
3. 成员 Agent 独立执行任务并返回结果
4. 主 Agent 整合结果，生成最终响应

## Agent 作为工具

也可以手动将 Agent 转换为工具：

```python
research_tool = researcher.as_tool(
    tool_name="research",
    tool_description="搜索并整理指定主题的资料",
)

main_agent = Agent(tools=[research_tool])
```

## 设计原则

| 原则 | 说明 |
|------|------|
| **单一职责** | 每个成员专注一个领域 |
| **清晰描述** | `description` 帮助协调者正确委派 |
| **适量成员** | 3-5 个成员最佳，过多会降低委派准确性 |
| **适量工具** | 每个成员 3-7 个工具 |

## Team vs Workflow vs Swarm

| 场景 | Team | Workflow | Swarm |
|------|:----:|:--------:|:-----:|
| 步骤顺序固定 | | **适合** | |
| 任务分解不确定 | **适合** | | |
| 需要动态判断 | **适合** | | |
| 并行执行 | | | **适合** |
| 自主协作 | | | **适合** |

## 下一步

- [Workflow](workflow.md) -- 确定性工作流编排
- [Swarm](swarm.md) -- 自主多智能体协作
- [Agent 核心概念](../concepts/agent.md) -- 回顾 Agent 基础
