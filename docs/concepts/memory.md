# Memory & Workspace

Agentica 提供两层记忆系统：**运行时记忆（WorkingMemory）** 和 **持久化记忆（Workspace）**。

## 运行时记忆：WorkingMemory

管理当前会话的消息历史，支持 token 感知的截断。

```python
from agentica import Agent

agent = Agent(
    add_history_to_messages=True,   # 将历史加入上下文
    history_window=5,               # 保留最近 5 轮
)
```

### 会话持久化

通过数据库保存会话历史：

```python
from agentica import Agent, SqliteDb
from agentica.memory import WorkingMemory

db = SqliteDb(table_name="sessions", db_file="agent.db")
agent = Agent(
    working_memory=WorkingMemory(db=db),
)
```

## 持久化记忆：Workspace

基于文件的持久化存储，使用 Markdown 文件管理上下文和记忆：

```
workspace/
+-- AGENT.md      # Agent 上下文信息
+-- PERSONA.md    # 用户画像
+-- TOOLS.md      # 工具使用记录
+-- USER.md       # 用户相关信息
+-- MEMORY.md     # 长期记忆
+-- memory/       # 每日记忆日志
|   +-- 2025-01-01.md
|   +-- 2025-01-02.md
+-- users/
    +-- {user_id}/  # 多用户隔离
        +-- MEMORY.md
        +-- memory/
```

### 基本用法

```python
from agentica import Agent, Workspace

agent = Agent(
    workspace=Workspace(path="./my_workspace", user_id="alice"),
)
```

### WorkspaceConfig

可自定义文件布局：

```python
from agentica.workspace import Workspace, WorkspaceConfig

config = WorkspaceConfig(
    agent_md="AGENT.md",
    persona_md="PERSONA.md",
    tools_md="TOOLS.md",
    user_md="USER.md",
    memory_md="MEMORY.md",
    memory_dir="memory",
    users_dir="users",
)

workspace = Workspace(path="./workspace", config=config)
```

## Git 上下文注入

Workspace 支持自动注入 Git 状态到 System Prompt，让 Agent 了解当前代码库状态：

```python
# 自动注入（workspace 存在且位于 git repo 时）
agent = Agent(
    workspace=Workspace(path="./my_project"),
)
# System Prompt 将包含：
# - Git branch: main
# - Uncommitted changes: M file1.py, A file2.py
# - Recent commits: abc1234 feat: add new feature
```

`Workspace.get_git_context()` 获取分支名、未提交变更、最近 3 条 commit。

## Workspace 记忆

### 长期记忆

Workspace 的 `MEMORY.md` 文件用于存储跨会话的长期记忆：

```python
from agentica.agent.config import WorkspaceMemoryConfig

agent = Agent(
    workspace=Workspace(path="./workspace"),
    long_term_memory_config=WorkspaceMemoryConfig(
        enabled=True,
        create_user_memories=True,
    ),
)
```

### 对话归档

使用 `ConversationArchiveHooks` 自动将对话归档到 Workspace 的每日日志：

```python
from agentica.hooks import ConversationArchiveHooks
from agentica.run_config import RunConfig

hooks = ConversationArchiveHooks()
response = await agent.run("Hello", config=RunConfig(hooks=hooks))
```

对话将保存到 `workspace/users/{user_id}/memory/YYYY-MM-DD.md`。

## 下一步

- [Agent 核心概念](agent.md) -- Agent 如何使用记忆
- [Hooks](../advanced/hooks.md) -- ConversationArchiveHooks 详解
- [Context Compression](../advanced/compression.md) -- 上下文压缩
