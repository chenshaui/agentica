# Agentica Public API（Tier 稳定度合约）

> **适用版本**：v1.4.0 及之后
> **维护原则**：每次发版前检查本文档与代码的一致性。未列出的符号均视为 Internal，不承诺稳定。

---

## 稳定度分层

| Tier | 含义 | 变更规则 |
|------|------|----------|
| **Tier 1** | 最小稳定面 | 在 v1.x / v2.x 内不做破坏性变更；新增必须默认值 |
| **Tier 2** | 主仓可选能力 | minor 内尽量不 break；major 允许调整 |
| **Tier 3** | 实验 / 过渡 | 可能随时演进；文档需明确标注 |
| Internal | 内部实现 | 不承诺稳定；不建议外部直接 import |

---

## Tier 1：核心最小稳定面

### Agent 运行时

```python
from agentica import Agent

agent = Agent(
    model=...,        # Required: a Model instance
    tools=[...],      # Optional: list of @tool-decorated functions or Tool instances
    hooks=[...],      # Optional: list of Hook instances
    workspace=...,    # Optional: Workspace instance
    ...
)

await agent.run(message)              # async non-stream
async for evt in agent.run_stream(message): ...  # async stream
agent.run_sync(message)               # sync adapter (wraps run())
```

关键方法：

- `Agent.run(message, *, stream=False, ...) -> RunResponse`
- `Agent.run_stream(message, ...) -> AsyncIterator[RunEvent]`
- `Agent.run_sync(message, ...) -> RunResponse`
- `Agent.run_stream_sync(message, ...) -> Iterator[RunEvent]`

### Model 基础

```python
from agentica.model.base import Model
from agentica.model.message import Message, UserMessage, AssistantMessage, SystemMessage, ToolMessage
from agentica.model.response import ModelResponse
```

### Model Providers（默认装）

```python
from agentica.model.openai import OpenAIChat          # 默认可用
from agentica.model.anthropic.claude import Claude    # 默认可用
```

### Tool 基础

```python
from agentica.tools.base import Tool, Function, FunctionCall
from agentica.tools.decorators import tool
```

### 运行配置

```python
from agentica.run_response import RunResponse, RunEvent
from agentica.run_config import RunConfig
```

### Hook 接口

```python
from agentica.hooks import RunHooks, AgentHooks
```

### Workspace（默认装）

```python
from agentica.workspace import Workspace
```

### CLI（默认装）

```bash
agentica             # 交互式模式
agentica --query "..."
```

---

## Tier 2：主仓可选能力（`agentica[xxx]`）

### RAG 栈 — `pip install agentica[rag]`

```python
from agentica.knowledge import Knowledge
from agentica.vectordb.base import VectorDb, Distance
from agentica.embedding.base import Embedding
from agentica.rerank.base import Rerank
```

Vector DB 具体实现：

```python
from agentica.vectordb import InMemoryVectorDb
# 需 agentica[qdrant]：
# from agentica.vectordb import QdrantVectorDb
# 需 agentica[chroma]：
# from agentica.vectordb import ChromaDb
```

### Storage — `pip install agentica[sql]` / `[postgres]` / `[mysql]` / `[redis]`

```python
from agentica.db import InMemoryDb, JsonDb  # 默认可用
from agentica.db import SqliteDb            # 需 [sql]
from agentica.db import PostgresDb          # 需 [postgres]
from agentica.db import MysqlDb             # 需 [mysql]
from agentica.db import RedisDb             # 需 [redis]
```

### Guardrails

```python
from agentica.guardrails import (
    InputGuardrail, OutputGuardrail,
    ToolInputGuardrail, ToolOutputGuardrail,
    input_guardrail, output_guardrail,
)
```

### Gateway — `pip install agentica[gateway]`

```python
from agentica.gateway.main import app, main  # FastAPI app
from agentica.gateway.channels import (
    Channel, ChannelType, Message,
    FeishuChannel, TelegramChannel, DiscordChannel,
    QQChannel,        # 需 agentica[qq]
    WeComChannel,     # 需 agentica[wecom]
    DingTalkChannel,  # 需 agentica[dingtalk]
    WeChatChannel,    # 需 agentica[wechat]
)
```

详见 [Gateway 文档](advanced/gateway.md)。

### ACP（IDE 集成）— `pip install agentica[acp]`

```python
from agentica.acp import ACPServer
```

### MCP（Model Context Protocol）— `pip install agentica[mcp]`

```python
from agentica.mcp import MCPClient, MCPServer
```

### Tools 扩展 — 细粒度 extras

```python
# pip install agentica[arxiv]
from agentica.tools.arxiv_tool import ArxivTool

# pip install agentica[yfinance]
from agentica.tools.yfinance_tool import YFinanceTool

# pip install agentica[crawl]
from agentica.tools.url_crawler_tool import UrlCrawlerTool

# pip install agentica[ddg]
from agentica.tools.duckduckgo_tool import DuckDuckGoTool
```

超级组合：

```bash
pip install agentica[tools-search]      # DDG + Wikipedia + Serper + Exa + Bocha
pip install agentica[tools-research]    # arxiv + wikipedia + newspaper + dblp
pip install agentica[tools-finance]     # yfinance
pip install agentica[tools-media]       # dalle + cogview + cogvideo + ocr + image/video analysis
pip install agentica[tools-browser]     # browser + crawl
```

---

## Tier 3：实验 / 过渡能力

以下能力**可能随时演进**。新代码不建议深度依赖；现有用户可继续使用，但 major 版本可能调整。

```python
# 子 Agent（建议新代码用 @tool + Agent() 组合）
from agentica.subagent import SubAgent

# 多 Agent 并行 / 自治（建议新代码用 asyncio.gather）
from agentica.swarm import Swarm

# 工作流编排（建议新代码用顺序 Python）
from agentica.workflow import Workflow

# 技能 / 经验（自进化能力）
from agentica.skills import SkillRegistry
from agentica.experience import ExperienceCompiler, SkillEvolutionManager

# 压缩（引擎内部，尽量不直接依赖）
from agentica.compression import CompressionManager
```

---

## Internal（不对外承诺）

以下模块/路径属于内部实现，**外部代码不建议直接 import**，API 可能在任何 patch 版本变化：

- `agentica._internal.*`（如果未来新增）
- `agentica.model.providers.create_provider`（工厂函数，v2.x 会废弃）
- `agentica.model.providers.list_providers`
- `agentica.utils.markdown_converter.MarkdownConverter`（被 knowledge 内部使用）
- `agentica.runner.Runner`（Agent 的执行引擎，通过 Agent.run() 访问）
- `agentica.compression.*`（内部压缩管道）

---

## 默认 `__all__`（顶层 export）

`agentica/__init__.py` 的 `__all__` 保留了向后兼容的大量 export，包括便捷 alias 如：

- `Agent` / `RunResponse` / `RunEvent` / `Message` ...
- `OpenAIChat` / `DeepSeek` / `Claude` / ... (所有 model provider 别名)
- `ShellTool` / `CodeTool` / `McpTool` / ... (常用 tool 别名)

**推荐的新代码风格**（明确的 import 路径，对标 agno）：

```python
# ✅ 推荐
from agentica import Agent, tool
from agentica.model.openai import OpenAIChat
from agentica.tools.shell_tool import ShellTool

# ⚠️ 仍兼容但不推荐（v2.0 可能加 DeprecationWarning）
from agentica import Agent, OpenAIChat, ShellTool
```

---

## 变更流程

对 Tier 1 / Tier 2 符号的任何变更（新增字段、改签名、重命名、删除）必须：

1. 在 PR 中说明变更理由与影响范围
2. Tier 1 变更需写 RFC 到 `docs/rfcs/`
3. 删除或改现有行为 → 必须 major bump
4. 新增参数必须有默认值，不破坏现有调用
5. `CHANGELOG.md` 列出变更

Tier 3 变更不要求 RFC，但建议说明 dogfood 发现与选择依据。
