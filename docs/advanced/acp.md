# ACP Integration

ACP (Agent Client Protocol) 是一个标准化协议，类似于 LSP (Language Server Protocol)，用于 IDE 与 AI Agent 之间的通信。

## 核心特性

- **通信方式**: JSON-RPC 2.0 over stdio
- **Session 管理**: 多会话支持，独立的上下文和消息历史
- **流式输出**: 支持实时进度通知
- **工具系统**: 文件操作、命令执行、代码搜索等
- **优势**: 一次实现，多处使用（Zed, JetBrains, VSCode 等）

## 架构

```
+-----------+      JSON-RPC over stdio      +-----------+
|   IDE     |  <------------------------->  | Agentica  |
|  (Client) |                               |   ACP     |
+-----------+                               |   Server  |
      |                                     +-----+-----+
      |                                           |
      |                                     +-----v-----+
      +---------------------------------------->| Agent   |
                                              | Engine  |
                                              +---------+
```

## 快速开始

### 命令行启动

```bash
# 直接启动 ACP 服务器
agentica acp

# 或者使用 Python
python -m agentica.cli acp
```

### IDE 配置

#### Zed

编辑 `~/.config/zed/settings.json`:

```json
{
  "agent_servers": {
    "Agentica": {
      "type": "custom",
      "command": "agentica",
      "args": ["acp"],
      "env": {
        "OPENAI_API_KEY": "your-api-key"
      }
    }
  }
}
```

#### JetBrains (IntelliJ IDEA, PyCharm, WebStorm)

编辑 `~/.jetbrains/acp.json`:

```json
{
  "agent_servers": {
    "Agentica": {
      "command": "agentica",
      "args": ["acp"],
      "env": {
        "OPENAI_API_KEY": "your-api-key"
      }
    }
  }
}
```

## 下一步

- [MCP 集成](mcp.md) -- Model Context Protocol
- [CLI 终端](../getting-started/terminal.md) -- 命令行使用
- [Agent 概念](../concepts/agent.md) -- Agent 核心概念
