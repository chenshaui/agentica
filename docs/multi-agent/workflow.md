# Workflow

Workflow 模式下，步骤顺序是确定性的，适合需要严格控制流程的场景。

## 何时选择 Workflow

- 你已经看过 [编排模式决策树](choosing.md)，并且需要开发者控制步骤顺序
- 步骤顺序固定
- 需要非 LLM 步骤（数据验证、计算）
- 不同步骤需要不同模型（成本优化）
- 需要混合 LLM 步骤和纯 Python 步骤

## 基本用法

继承 `Workflow` 类，实现 `run()` 方法：

```python
import asyncio
from typing import List
from pydantic import BaseModel, Field
from agentica import Agent, OpenAIChat, Workflow, RunResponse


class AnalysisReport(BaseModel):
    summary: str
    key_findings: List[str]
    recommendations: List[str]


class ResearchWorkflow(Workflow):
    """研究工作流：搜索 -> 分析 -> 报告"""

    description: str = "搜索、分析并生成研究报告"

    # 不同步骤用不同模型优化成本
    extractor: Agent = Agent(
        model=OpenAIChat(id="gpt-4o-mini"),  # 低成本模型做提取
        name="Extractor",
        instructions=["提取文本中的关键信息"],
    )

    analyst: Agent = Agent(
        model=OpenAIChat(id="gpt-4o"),  # 高质量模型做分析
        name="Analyst",
        instructions=["深入分析数据，给出洞见和建议"],
        response_model=AnalysisReport,
    )

    def _validate_data(self, data: str) -> str:
        """纯 Python 验证步骤，无需 LLM"""
        lines = [l.strip() for l in data.split("\n") if l.strip()]
        return "\n".join(lines)

    async def run(self, topic: str) -> RunResponse:
        # Step 1: LLM 提取
        extracted = await self.extractor.run(f"提取关键信息: {topic}")

        # Step 2: Python 验证（无 LLM 成本）
        cleaned = self._validate_data(extracted.content)

        # Step 3: LLM 分析
        result = await self.analyst.run(f"分析以下内容:\n{cleaned}")

        return RunResponse(content=result.content)


async def main():
    wf = ResearchWorkflow()
    result = await wf.run("2024年全球AI芯片市场分析")
    print(result.content)

asyncio.run(main())
```

## Workflow 特点

- `run()` 方法是 **async** 的，也提供 `run_sync()` 同步适配器
- 可以混合 LLM 步骤和纯 Python 步骤
- 不同步骤可以使用不同模型（成本优化）
- 步骤间可以传递结构化数据（Pydantic 模型）
- 支持会话持久化

## 同步运行

```python
wf = ResearchWorkflow()
result = wf.run_sync("2024年全球AI芯片市场分析")
print(result.content)
```

## 实际示例

### 新闻报道流水线

```python
class NewsWorkflow(Workflow):
    researcher: Agent = Agent(
        model=OpenAIChat(id="gpt-4o-mini"),
        tools=[BaiduSearchTool()],
        instructions=["搜索最新新闻"],
    )

    writer: Agent = Agent(
        model=OpenAIChat(id="gpt-4o"),
        instructions=["写一篇新闻报道，包含标题、导语、正文"],
    )

    reviewer: Agent = Agent(
        model=OpenAIChat(id="gpt-4o"),
        instructions=["审核文章的准确性和可读性，提出修改建议"],
    )

    async def run(self, topic: str) -> RunResponse:
        research = await self.researcher.run(f"搜索最新新闻: {topic}")
        draft = await self.writer.run(f"基于以下资料撰写新闻报道:\n{research.content}")
        final = await self.reviewer.run(f"审核并改进以下文章:\n{draft.content}")
        return RunResponse(content=final.content)
```

## 下一步

- [Swarm](swarm.md) -- 自主多智能体协作
- [Subagent](subagent.md) -- 子任务委派
- [Agent 核心概念](../concepts/agent.md) -- 回顾 Agent 基础
