# Context Compression

Agentica 提供多层上下文压缩策略，防止长对话/大量工具输出导致 token 超限。

## 压缩架构

```
Tool 输出 (可能很大)
    |
    v
[Tool Result Storage] -- 超大输出持久化到磁盘
    |
    v
Context Messages
    |
    v
[Auto-Compact] -- Token 超限时自动压缩
    |
    +--> [Rule-based] -- 截断旧结果, 丢弃旧轮次
    +--> [LLM-based]  -- 智能摘要 (可选)
```

## CompressionManager

### 基本配置

```python
from agentica import Agent, OpenAIChat, CompressionManager

agent = Agent(
    model=OpenAIChat(id="gpt-4o"),
    compression_manager=CompressionManager(
        compress_tool_results=True,
        compress_token_limit=100000,       # 触发压缩的 token 阈值
        compress_target_token_limit=60000, # 压缩后的目标 token 数
    ),
)
```

### 两阶段压缩策略

**Stage 1 -- Rule-based（免费，始终先执行）**：

- 截断最旧的未压缩工具结果到 `truncate_head_chars` 字符
- 如果仍超限，丢弃最旧的消息轮次，只保留最近 `keep_recent_rounds` 轮

**Stage 2 -- LLM-based（可选，消耗 token）**：

- 使用轻量级 LLM 智能摘要工具结果
- 保留关键信息：数字、日期、实体、标识符
- 删除冗余内容：过渡语、元评论、格式

```python
manager = CompressionManager(
    model=OpenAIChat(id="gpt-4o-mini"),  # 用便宜模型做摘要
    compress_tool_results=True,
    use_llm_compression=True,
)
```

## Tool Result Storage

当单个工具输出超过阈值时，自动持久化到磁盘：

```
~/.agentica/projects/<project-hash>/<session-id>/tool-results/
+-- {tool_use_id}.txt    # 完整输出
```

Context 中只保留前 2000 字符的预览 + 文件路径。

### 两层预算

| 层级 | 阈值 | 说明 |
|------|------|------|
| 单工具限制 | 50,000 字符 | 单个 tool result 超过此值 -> 持久化 |
| 消息预算 | 200,000 字符 | 单条消息中所有 tool_result 总和超过此值 -> 持久化最大的几个 |

### 配置

通过 `Function.max_result_size_chars` 控制单工具阈值：

- 默认阈值：50,000 字符
- 设为 `None` 禁用持久化
- 预览长度：2,000 字符

### 工作流程

1. 工具执行完成，返回输出字符串
2. Layer 1: `maybe_persist_result()` 检查单工具大小，超限 -> 写入磁盘 -> 返回预览
3. Layer 2: `enforce_tool_result_budget()` 检查本轮所有 tool_result 总大小，超限 -> 持久化最大的结果

## Hooks 集成

压缩前后可以通过 Hooks 插入自定义逻辑：

```python
from agentica.hooks import RunHooks

class CompactionTracker(RunHooks):
    async def on_pre_compact(self, agent, messages, **kwargs):
        print(f"Before: {len(messages)} messages")

    async def on_post_compact(self, agent, messages, **kwargs):
        print(f"After: {len(messages)} messages")
```

## 自动压缩触发

CompressionManager 的 `auto_compact` 在以下条件触发：

1. 当前 token 数超过 `compress_token_limit`
2. 带有 circuit-breaker 防止连续压缩

如果 auto_compact 失败，回退到 rule-based 压缩。

## 下一步

- [RunConfig](run-config.md) -- 超时和成本控制
- [Hooks](hooks.md) -- on_pre_compact / on_post_compact
- [Agent 概念](../concepts/agent.md) -- Agent 上下文管理
