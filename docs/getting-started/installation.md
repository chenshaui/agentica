# Installation

## 环境要求

- **Python >= 3.10**（推荐 3.12）
- 至少一个 LLM 提供商的 API Key

## 安装

### 从 PyPI 安装（推荐）

```bash
pip install -U agentica
```

### 从源码安装（开发模式）

```bash
git clone https://github.com/shibing624/agentica.git
cd agentica
pip install -e .
```

开发模式下，代码修改立即生效，无需重新安装。

### 可选依赖

Agentica 的核心功能不需要额外依赖，部分工具和功能需要单独安装：

```bash
# 搜索工具
pip install duckduckgo-search       # DuckDuckGoTool
pip install exa-py                  # SearchExaTool

# 浏览器工具
pip install playwright              # BrowserTool
playwright install chromium

# RAG / 向量数据库
pip install lancedb                 # LanceDb（推荐本地向量存储）
pip install qdrant-client           # QdrantVectorDb
pip install chromadb                # ChromaDb

# MCP 协议
pip install mcp                     # McpTool（Model Context Protocol）

# 本地模型
# Ollama 无需 pip，直接下载安装：https://ollama.ai

# 文档解析
pip install pypdf                   # PDF 解析
pip install python-docx             # Word 文档

# 评测
pip install agentica[dev]           # 开发工具 + 测试依赖
```

## 配置 API Key

### 方式一：环境变量（推荐）

```bash
# 智谱AI（glm-4.7-flash 免费，支持工具调用，128k 上下文）
export ZHIPUAI_API_KEY="your-api-key"

# OpenAI
export OPENAI_API_KEY="sk-xxx"

# DeepSeek
export DEEPSEEK_API_KEY="your-api-key"

# Anthropic (Claude)
export ANTHROPIC_API_KEY="sk-ant-xxx"

# 月之暗面 (Moonshot)
export MOONSHOT_API_KEY="your-api-key"

# 阿里云 (Qwen)
export DASHSCOPE_API_KEY="your-api-key"

# 字节跳动 (Doubao)
export DOUBAO_API_KEY="your-api-key"
```

### 方式二：`.env` 文件

在项目目录（或 `~/.agentica/`）创建 `.env` 文件：

```ini
# ~/.agentica/.env
ZHIPUAI_API_KEY=your-api-key
OPENAI_API_KEY=sk-xxx
DEEPSEEK_API_KEY=your-api-key
```

Agentica 启动时自动加载 `~/.agentica/.env`。

### 方式三：代码内传入

```python
from agentica import Agent, OpenAIChat

agent = Agent(
    model=OpenAIChat(
        id="gpt-4o",
        api_key="sk-xxx",          # 直接传 api_key
        base_url="https://...",    # 自定义 API 地址（代理、私有部署）
    )
)
```

## 验证安装

```bash
# 检查版本
python -c "import agentica; print(agentica.__version__)"

# 运行 CLI（需要配置 API Key）
agentica --query "你好"
```

## 免费快速入门（零成本）

智谱 AI 的 `glm-4.7-flash` 模型免费，支持工具调用和 128k 上下文，适合快速体验：

```bash
# 1. 注册并获取免费 API Key：https://open.bigmodel.cn/
export ZHIPUAI_API_KEY="your-free-key"

# 2. 运行
agentica --model_provider zhipuai --model_name glm-4.7-flash
```

## 使用 Ollama 本地模型（无需 API Key）

```bash
# 1. 安装 Ollama：https://ollama.ai
# 2. 下载模型
ollama pull llama3.1
# 3. 运行
agentica --model_provider ollama --model_name llama3.1
```

代码中使用：

```python
from agentica import Agent
from agentica.model.ollama import OllamaChat

agent = Agent(model=OllamaChat(id="llama3.1"))
result = agent.run_sync("你好")
print(result.content)
```

## 下一步

- [快速入门](quickstart.md) -- 5 分钟上手第一个 Agent
- [CLI 终端](terminal.md) -- 命令行交互模式全功能介绍
- [模型提供商](../guides/models.md) -- 20+ 模型配置指南
