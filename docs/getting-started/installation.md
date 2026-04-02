# Installation

## 环境要求

- **Python >= 3.12**
- 至少一个 LLM 提供商的 API Key

## 安装

### 从 PyPI 安装

```bash
pip install -U agentica
```

### 从源码安装（开发模式）

```bash
git clone https://github.com/shibing624/agentica.git
cd agentica
pip install -e .
```

## 配置 API Key

在 `~/.agentica/.env` 中配置，或直接设置环境变量：

```bash
# 推荐：智谱AI（glm-4.7-flash 免费，支持工具调用，128k 上下文）
export ZHIPUAI_API_KEY="your-api-key"

# OpenAI
export OPENAI_API_KEY="sk-xxx"

# DeepSeek
export DEEPSEEK_API_KEY="your-api-key"

# Anthropic (Claude)
export ANTHROPIC_API_KEY="your-api-key"
```

## 验证安装

```bash
python -c "import agentica; print(agentica.__version__)"
```

## 下一步

- [快速入门](quickstart.md) -- 5 分钟上手第一个 Agent
- [CLI 终端](terminal.md) -- 命令行交互模式
