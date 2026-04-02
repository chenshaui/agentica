# s06: Context Compact (上下文压缩)

`s01 > s02 > s03 > s04 > s05 > [ s06 ] | s07 > s08 > s09 > s10 > s11 > s12`

> *"上下文总会满, 要有办法腾地方"* -- 三层压缩策略, 换来无限会话。
>
> **Harness 层**: 压缩 -- 干净的记忆, 无限的会话。

## 问题

上下文窗口是有限的。读一个 1000 行的文件就吃掉 ~4000 token; 读 30 个文件、跑 20 条命令, 轻松突破 100k token。不压缩, Agent 根本没法在大项目里干活。

## 解决方案

三层压缩, 激进程度递增:

```
Every turn:
+------------------+
| Tool call result |
+------------------+
        |
        v
[Layer 1: micro_compact]        (silent, every turn)
  Two sub-paths:
  a) Time-based: 距上次 assistant 消息超过阈值 (默认60min)
     -> 将旧 tool_result 替换为 "[Old tool result content cleared]"
  b) Cached MC (ant-only): 使用 cache editing API 删除工具结果
     -> 不修改本地消息, 在 API 层通过 cache_edits 删除
        |
        v
[Check: tokens > threshold (context_window - 13000 buffer)?]
   |               |
   no              yes
   |               |
   v               v
continue    [Layer 2: auto_compact]
              Save transcript to .transcripts/
              LLM summarizes conversation.
              Replace all messages with [summary].
              Circuit breaker: 连续失败3次后停止重试
                    |
                    v
            [Layer 3: reactive compact]   (ant-only, feature-gated)
              API 返回 prompt_too_long 时触发.
              紧急压缩, 非用户主动调用.
```

## 工作原理

1. **第一层 -- micro_compact**: 分两个子路径。

**子路径 a: time-based microcompact** (距上次 assistant 消息超 60 分钟, 缓存已失效时触发)

```python
def micro_compact(messages: list) -> list:
    tool_results = []
    for i, msg in enumerate(messages):
        if msg["role"] == "user" and isinstance(msg.get("content"), list):
            for j, part in enumerate(msg["content"]):
                if isinstance(part, dict) and part.get("type") == "tool_result":
                    tool_results.append((i, j, part))
    if len(tool_results) <= KEEP_RECENT:
        return messages
    for _, _, part in tool_results[:-KEEP_RECENT]:
        if len(part.get("content", "")) > 100:
            part["content"] = "[Old tool result content cleared]"  # 注意: 此为实际占位符
    return messages
```

**子路径 b: cached microcompact** (ant-only, 使用 Anthropic cache editing API)

- **不修改本地消息内容**, 而是通过 `cache_edits` 在 API 层删除工具结果
- 只有主线程执行, 子 agent 不参与 (防止全局 state 冲突)
- 仅对特定工具生效: `read_file`, bash/shell, `grep`, `glob`, `web_search`, `web_fetch`, `edit_file`, `write_file`

2. **第二层 -- auto_compact**: token 超阈值时 (context_window - 13,000 buffer), 保存完整对话到磁盘, 让 LLM 做摘要。

```python
def auto_compact(messages: list) -> list:
    # Save transcript for recovery
    transcript_path = TRANSCRIPT_DIR / f"transcript_{int(time.time())}.jsonl"
    with open(transcript_path, "w") as f:
        for msg in messages:
            f.write(json.dumps(msg, default=str) + "\n")
    # LLM summarizes
    response = client.messages.create(
        model=MODEL,
        messages=[{"role": "user", "content":
            "Summarize this conversation for continuity..."
            + json.dumps(messages, default=str)[:80000]}],
        max_tokens=2000,
    )
    return [
        {"role": "user", "content": f"[Compressed]\n\n{response.content[0].text}"},
    ]
```

**关键保护机制**:
- **Circuit Breaker**: 连续失败 3 次后停止重试, 避免浪费 API 调用
- **Session Memory 优先**: 先尝试 session memory compaction (轻量), 失败再用 LLM 摘要
- **递归保护**: `querySource` 为 `session_memory` 或 `compact` 时跳过 auto_compact, 防止死锁

3. **第三层 -- reactive compact** (ant-only, feature-gated by `REACTIVE_COMPACT`): API 返回 `prompt_too_long` 错误时的应急压缩, 非用户主动触发。

**注意**: CC 的第三层不是用户调用的 `compact` 工具, 而是 API 错误时的响应式紧急压缩。在 learn-cc 教程中为了教学简化, 将第三层演示为用户可调用的 `compact` 工具 (触发与 auto_compact 相同的摘要逻辑)。

4. 循环整合三层:

```python
def agent_loop(messages: list):
    while True:
        micro_compact(messages)                        # Layer 1
        if estimate_tokens(messages) > THRESHOLD:
            messages[:] = auto_compact(messages)       # Layer 2
        response = client.messages.create(...)
        # ... tool execution ...
        if manual_compact:
            messages[:] = auto_compact(messages)       # Layer 3 (教学简化版)
```

完整历史通过 transcript 保存在磁盘上。信息没有真正丢失, 只是移出了活跃上下文。

## 相对 s05 的变更

| 组件           | 之前 (s05)       | 之后 (s06)                     |
|----------------|------------------|--------------------------------|
| Tools          | 5                | 5 (基础 + compact)             |
| 上下文管理     | 无               | 三层压缩                       |
| Micro-compact  | 无               | 旧结果 -> 占位符 (time-based 或 cache editing) |
| Auto-compact   | 无               | token 阈值触发 + circuit breaker |
| Transcripts    | 无               | 保存到 .transcripts/           |

## 试一试

```sh
cd learn-claude-code
python agents/s06_context_compact.py
```

试试这些 prompt (英文 prompt 对 LLM 效果更好, 也可以用中文):

1. `Read every Python file in the agents/ directory one by one` (观察 micro-compact 替换旧结果)
2. `Keep reading files until compression triggers automatically`
3. `Use the compact tool to manually compress the conversation`
