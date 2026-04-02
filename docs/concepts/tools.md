# Tools

工具赋予 Agent 与外部世界交互的能力。Agentica 提供 40+ 内置工具，并支持自定义工具和 MCP 协议集成。

## 三层工具架构

```
Tool (容器)
 +-- Function (Schema + 入口)
      +-- FunctionCall (调用实例)
```

- `Tool`: 一组相关功能的容器
- `Function`: 单个工具函数的 Schema 定义和入口点
- `FunctionCall`: 一次具体的工具调用

## 创建自定义工具

### 函数工具（推荐）

任何带类型注解和 docstring 的 Python 函数都可以作为工具：

```python
def get_weather(city: str) -> str:
    """获取指定城市的天气信息

    Args:
        city: 城市名称，如 "北京"、"上海"
    """
    return f"{city}: 晴, 25C"

agent = Agent(tools=[get_weather])
```

**要点：**

- 函数名即工具名
- docstring 第一行是工具描述
- `Args` 部分描述各参数含义
- 参数类型注解用于生成 JSON Schema
- 返回值建议用 `str`（JSON 格式更佳）

### 异步函数工具

I/O 密集型工具建议使用 async：

```python
import aiohttp

async def fetch_url(url: str) -> str:
    """抓取指定 URL 的网页内容

    Args:
        url: 要抓取的网页 URL
    """
    async with aiohttp.ClientSession() as session:
        async with session.get(url) as resp:
            return await resp.text()

agent = Agent(tools=[fetch_url])
```

Agentica 会自动检测 sync/async，sync 函数在 async 上下文中通过 `run_in_executor` 执行。

### 类工具

适合封装一组相关功能：

```python
from agentica import Tool

class DatabaseTool(Tool):
    def __init__(self, connection_string: str):
        super().__init__(name="database")
        self.conn_str = connection_string
        self.register(self.query)
        self.register(self.list_tables)

    def query(self, sql: str) -> str:
        """执行 SQL 查询"""
        return "query results"

    def list_tables(self) -> str:
        """列出所有数据库表"""
        return "users, orders, products"

agent = Agent(tools=[DatabaseTool("sqlite:///app.db")])
```

### 流控异常

工具可以通过特殊异常控制 Agent 行为：

```python
from agentica.tools.base import StopAgentRun, RetryAgentRun

def strict_tool(query: str) -> str:
    """严格校验的工具"""
    if not query:
        raise RetryAgentRun("query 不能为空，请重新提供")
    if "dangerous" in query:
        raise StopAgentRun("检测到危险操作，终止执行")
    return f"结果: {query}"
```

## 内置工具

### 搜索类

| 工具 | 说明 | 依赖 |
|------|------|------|
| `BaiduSearchTool` | 百度搜索 | -- |
| `DuckDuckGoTool` | DuckDuckGo 搜索 | `duckduckgo-search` |
| `SearchSerperTool` | Serper 搜索 API | `SERPER_API_KEY` |
| `ExaTool` | Exa 语义搜索 | `exa-py` |
| `SearchBochaTool` | 博查搜索 | -- |

### 代码与Shell

| 工具 | 说明 |
|------|------|
| `ShellTool` | 执行 Shell 命令 |
| `CodeTool` | Python 代码执行 |
| `PatchTool` | 文件补丁操作 |

### 网页与文件

| 工具 | 说明 |
|------|------|
| `UrlCrawlerTool` | 网页内容抓取 |
| `JinaTool` | Jina Reader API |
| `BrowserTool` | 浏览器自动化 |
| `FileTool` | 文件读写操作 |

### 知识与数据

| 工具 | 说明 |
|------|------|
| `ArxivTool` | Arxiv 论文搜索 |
| `WikipediaTool` | Wikipedia 搜索 |
| `YFinanceTool` | 金融数据查询 |
| `WeatherTool` | 天气查询 |
| `SqlTool` | SQL 数据库查询 |
| `HackerNewsTool` | Hacker News |

### 多媒体

| 工具 | 说明 |
|------|------|
| `DalleTool` | DALL-E 图像生成 |
| `CogViewTool` | 智谱 CogView 图像生成 |
| `CogVideoTool` | 智谱 CogVideo 视频生成 |
| `ImageAnalysisTool` | 图像分析 |
| `OcrTool` | 文字识别 (OCR) |

### 特殊工具

| 工具 | 说明 |
|------|------|
| `UserInputTool` | 运行时向用户提问 (Human-in-the-loop) |
| `SkillTool` | Agent Skill 系统 |
| `McpTool` | MCP 协议集成 |

### DeepAgent 内置工具

`DeepAgent` 预配置了一组强大的内置工具：

`ls`, `read_file`, `write_file`, `edit_file`, `glob`, `grep`, `execute`, `web_search`, `fetch_url`, `task`（子任务委派）

## 工具使用最佳实践

### 1. 清晰的工具描述

模型通过工具描述决定何时调用。描述越清晰，调用越准确。

### 2. 控制工具数量

```python
# 推荐：3-7 个相关工具
agent = Agent(tools=[search, crawl, analyze])

# 避免：过多工具
agent = Agent(tools=[...15个工具...])
```

### 3. 错误处理

工具应返回有意义的错误信息，而非抛出异常。

## 下一步

- [Agent 概念](agent.md) -- Agent 如何使用工具
- [RAG 指南](rag.md) -- 知识库检索
- [Guardrails](../advanced/guardrails.md) -- 工具级安全验证
- [MCP 集成](../advanced/mcp.md) -- MCP 协议工具
