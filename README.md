[**🇨🇳中文**](https://github.com/shibing624/agentica/blob/main/README.md) | [**🌐English**](https://github.com/shibing624/agentica/blob/main/README_EN.md) | [**🇯🇵日本語**](https://github.com/shibing624/agentica/blob/main/README_JP.md)

<div align="center">
  <a href="https://github.com/shibing624/agentica">
    <img src="https://raw.githubusercontent.com/shibing624/agentica/main/docs/assets/logo.png" height="150" alt="Logo">
  </a>
</div>

-----------------

# Agentica: Build AI Agents
[![PyPI version](https://badge.fury.io/py/agentica.svg)](https://badge.fury.io/py/agentica)
[![Downloads](https://static.pepy.tech/badge/agentica)](https://pepy.tech/project/agentica)
[![License Apache 2.0](https://img.shields.io/badge/license-Apache%202.0-blue.svg)](LICENSE)
[![python_version](https://img.shields.io/badge/Python-3.12%2B-green.svg)](requirements.txt)
[![GitHub issues](https://img.shields.io/github/issues/shibing624/agentica.svg)](https://github.com/shibing624/agentica/issues)
[![Wechat Group](https://img.shields.io/badge/wechat-group-green.svg?logo=wechat)](#社区与支持)

**Agentica** 是一个轻量级 Python 框架，用于构建 AI 智能体。Async-First 架构，支持工具调用、RAG、多智能体团队、工作流编排和 MCP 协议。

<div align="center">
  <img src="https://raw.githubusercontent.com/shibing624/agentica/main/docs/assets/agent_loop.png" width="800" alt="Agentica Loop Architecture" />
</div>

## 安装

```bash
pip install -U agentica
```

## 快速开始

```python
import asyncio
from agentica import Agent, ZhipuAI

async def main():
    agent = Agent(model=ZhipuAI())
    result = await agent.run("一句话介绍北京")
    print(result.content)

asyncio.run(main())
```

```
北京是中国的首都，是一座拥有三千多年历史的文化名城，也是全国的政治、文化和国际交流中心。
```

需要先设置 API Key：

```bash
export ZHIPUAI_API_KEY="your-api-key"      # 智谱AI（glm-4.7-flash 免费）
export OPENAI_API_KEY="sk-xxx"              # OpenAI
export DEEPSEEK_API_KEY="your-api-key"      # DeepSeek
```

## 功能特性

- **Async-First** — 原生 async API，`asyncio.gather()` 并行工具执行，同步适配器兼容
- **Runner Agentic Loop** — LLM ↔ 工具调用自动循环，多轮链式推理、死循环检测、成本预算、压缩 pipeline、API 重试
- **20+ 模型** — OpenAI / DeepSeek / Claude / 智谱 / Qwen / Moonshot / Ollama / LiteLLM 等
- **40+ 内置工具** — 搜索、代码执行、文件操作、浏览器、OCR、图像生成
- **RAG** — 知识库管理、混合检索、Rerank，集成 LangChain / LlamaIndex
- **多智能体** — Team（动态委派）、Swarm（并行/自治）和 Workflow（确定性编排）
- **安全守卫** — 输入/输出/工具级 Guardrails，流式实时检测
- **MCP / ACP** — Model Context Protocol 和 Agent Communication Protocol 支持
- **Skill 系统** — 基于 Markdown 的技能注入，模型无关
- **多模态** — 文本、图像、音频、视频理解
- **持久化记忆** — 索引/内容分离、相关性召回、四类型分类、drift 防御

## Workspace 记忆

Workspace 提供跨会话的持久化记忆，采用索引/召回设计：

```python
from agentica import Workspace

workspace = Workspace("./workspace")
workspace.initialize()

# 写入带类型的记忆条目（每条独立文件，自动更新索引）
await workspace.write_memory_entry(
    title="Python Style",
    content="User prefers concise, typed Python.",
    memory_type="feedback",              # user|feedback|project|reference
    description="python coding style",   # 相关性匹配关键词
)

# 相关性召回（根据当前 query 返回最相关的 ≤5 条）
memory = await workspace.get_relevant_memories(query="how to write python")
```

Agent 自动根据当前 query 召回最相关记忆，而非全量注入：

```python
from agentica import Agent, Workspace
from agentica.agent.config import WorkspaceMemoryConfig

agent = Agent(
    workspace=Workspace("./workspace"),
    long_term_memory_config=WorkspaceMemoryConfig(
        max_memory_entries=5,  # 最多注入 5 条相关记忆
    ),
)
```

## CLI

```bash
agentica --model_provider zhipuai --model_name glm-4.7-flash
```

<img src="https://github.com/shibing624/agentica/blob/main/docs/assets/cli_snap.png" width="800" />

## Web UI

通过 [agentica-gateway](https://github.com/shibing624/agentica-gateway) 提供 Web 页面，同时支持飞书 App、企业微信直连调用 Agentica。

## 示例

查看 [examples/](https://github.com/shibing624/agentica/tree/main/examples) 获取完整示例，涵盖：

| 类别 | 内容 |
|------|------|
| **基础用法** | Hello World、流式输出、结构化输出、多轮对话、多模态、**Agentic Loop 对比** |
| **工具** | 自定义工具、Async 工具、搜索、代码执行、并行工具、并发安全、成本追踪、沙箱隔离、压缩 |
| **Agent 模式** | Agent 作为工具、并行执行、团队协作、辩论、路由分发、Swarm、子 Agent、模型层钩子、会话恢复 |
| **安全护栏** | 输入/输出/工具级 Guardrails、流式护栏 |
| **记忆** | 会话历史、WorkingMemory、上下文压缩、Workspace 记忆、LLM 自动记忆 |
| **RAG** | PDF 问答、高级 RAG、LangChain / LlamaIndex 集成 |
| **工作流** | 数据管道、投资研究、新闻报道、代码审查 |
| **MCP** | Stdio / SSE / HTTP 传输、JSON 配置 |
| **可观测性** | Langfuse、Token 追踪、Usage 聚合 |
| **应用** | LLM OS、深度研究、客服系统、**金融研究（6-Agent 流水线）** |

[→ 查看完整示例目录](https://github.com/shibing624/agentica/blob/main/examples/README.md)

## 文档

完整使用文档：**https://shibing624.github.io/agentica**

## 社区与支持

- **GitHub Issues** — [提交 issue](https://github.com/shibing624/agentica/issues)
- **微信群** — 添加微信号 `xuming624`，备注 "llm"，加入技术交流群

<img src="https://github.com/shibing624/agentica/blob/main/docs/assets/wechat.jpeg" width="200" />

## 引用

如果您在研究中使用了 Agentica，请引用：

> Xu, M. (2026). Agentica: A Human-Centric Framework for Large Language Model Agent Workflows. GitHub. https://github.com/shibing624/agentica

## 许可证

[Apache License 2.0](LICENSE)

## 贡献

欢迎贡献！请查看 [CONTRIBUTING.md](CONTRIBUTING.md)。

## 致谢

- [phidatahq/phidata](https://github.com/phidatahq/phidata)
- [openai/openai-agents-python](https://github.com/openai/openai-agents-python)
