# -*- coding: utf-8 -*-
"""
@author:XuMing(xuming624@qq.com)
@description: 上下文压缩 + Agent Loop 状态管理 demo

演示 Optimization 3, 4, 5:
  - Micro-compact  — 每轮 LLM 调用前静默截断旧 tool_result（零成本）
  - Agent Loop 状态管理 — max_tokens 恢复 / API 错误重试 / 循环安全阀
  - Reactive compact — context_length_exceeded 时紧急压缩后重试

运行方式：
    # 基础演示（无需 API Key）
    python 12_compression_and_loop.py

    # 完整演示（含 Agent 运行）
    export OPENAI_API_KEY=sk-xxx
    python 12_compression_and_loop.py
"""
import sys
import os
import asyncio

sys.path.append(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))


# ============================================================================
# Demo 1: micro_compact 单元演示（无需 LLM）
# ============================================================================

def demo_micro_compact():
    """展示 micro_compact 如何截断旧 tool_result，保留最近 N 条。"""
    from agentica.compression.micro import micro_compact, MICRO_COMPACT_PLACEHOLDER, DEFAULT_KEEP_RECENT
    from agentica.model.message import Message

    print("=" * 60)
    print("Demo 1: Micro-compact — 每轮静默截断旧 tool_result")
    print("=" * 60)
    print(f"  策略: 保留最近 {DEFAULT_KEEP_RECENT} 个 tool_result，旧的替换为占位符")
    print(f"  占位符: '{MICRO_COMPACT_PLACEHOLDER}'\n")

    # 构建模拟对话：5 轮工具调用
    messages = [Message(role="user", content="请帮我分析这 5 个文件")]
    long_content = "A" * 500  # 模拟大文件内容
    for i in range(5):
        messages.append(Message(
            role="assistant",
            content=f"正在读取文件 {i}.py",
            tool_calls=[{"id": f"call_{i}", "function": {"name": "read_file"}}],
        ))
        messages.append(Message(
            role="tool",
            tool_call_id=f"call_{i}",
            tool_name="read_file",
            content=long_content,
        ))

    total_before = sum(len(str(m.content or "")) for m in messages if m.role == "tool")
    print(f"  压缩前: {len(messages)} 条消息, tool_result 总字符 = {total_before:,}")

    # 执行 micro_compact
    n = micro_compact(messages, keep_recent=3)

    total_after = sum(len(str(m.content or "")) for m in messages if m.role == "tool")
    saved = total_before - total_after
    print(f"  压缩后: {len(messages)} 条消息, tool_result 总字符 = {total_after:,}")
    print(f"  压缩数: {n} 条  |  节省字符: {saved:,}  ({saved/total_before*100:.0f}%)")

    # 验证
    tool_msgs = [m for m in messages if m.role == "tool"]
    assert tool_msgs[0].content == MICRO_COMPACT_PLACEHOLDER, "旧结果应被截断"
    assert tool_msgs[1].content == MICRO_COMPACT_PLACEHOLDER, "旧结果应被截断"
    assert tool_msgs[-1].content == long_content, "最新结果应保留"
    assert tool_msgs[-2].content == long_content, "最近 3 条应保留"
    assert tool_msgs[-3].content == long_content, "最近 3 条应保留"
    print("\n  验证通过 ✓")
    print(f"  tool_msg[0]: '{tool_msgs[0].content[:40]}'  ← 已截断")
    print(f"  tool_msg[-1]: '{tool_msgs[-1].content[:40]}...'  ← 保留\n")


# ============================================================================
# Demo 2: CompressionManager.auto_compact 演示（无需 LLM）
# ============================================================================

def demo_auto_compact_config():
    """展示 CompressionManager 的三层压缩配置方式。"""
    from agentica.compression.manager import CompressionManager

    print("=" * 60)
    print("Demo 2: CompressionManager 三层压缩配置")
    print("=" * 60)

    # 配置 1: 默认（规则截断，无 LLM）
    cm1 = CompressionManager(
        compress_token_limit=80_000,
        compress_target_token_limit=40_000,
    )
    print(f"  配置 1 (规则截断):")
    print(f"    触发阈值: {cm1.compress_token_limit:,} tokens")
    print(f"    目标阈值: {cm1.compress_target_token_limit:,} tokens")
    print(f"    LLM 压缩: {cm1.use_llm_compression}")

    # 配置 2: 启用 LLM 压缩（用轻量模型）
    cm2 = CompressionManager(
        compress_token_limit=60_000,
        use_llm_compression=True,
    )
    print(f"\n  配置 2 (LLM 压缩):")
    print(f"    触发阈值: {cm2.compress_token_limit:,} tokens")
    print(f"    LLM 压缩: {cm2.use_llm_compression}")

    # 显示 auto_compact circuit-breaker 状态
    print(f"\n  Auto-compact circuit-breaker:")
    print(f"    最大连续失败次数: {cm1._max_auto_compact_failures}")
    print(f"    预留 buffer tokens: {cm1._auto_compact_buffer_tokens:,}")
    print(f"    (等同于 CC 的 AUTOCOMPACT_BUFFER_TOKENS = 13,000)\n")


# ============================================================================
# Demo 3: Agent Loop 安全阀演示（需要 LLM API）
# ============================================================================

async def demo_loop_state_management():
    """展示 Agent Loop 状态管理：安全阀 + 重试计数器。"""
    from agentica import Agent, OpenAIChat

    print("=" * 60)
    print("Demo 3: Agent Loop 状态管理")
    print("=" * 60)

    call_count = [0]

    async def always_needs_more(step: int = 1) -> str:
        """A tool that always says there's more work to do.

        Args:
            step: Current step number.
        """
        call_count[0] += 1
        # 模拟：前几次调用返回"继续"指令
        if call_count[0] <= 3:
            return f"Step {step} done. Need to continue to step {step + 1}."
        return f"All {step} steps completed successfully!"

    agent = Agent(
        model=OpenAIChat(id="gpt-4o-mini"),
        tools=[always_needs_more],
        instructions=[
            "You are a step-by-step task executor.",
            "Always call always_needs_more tool and follow its instructions.",
        ],
    )

    response = await agent.run("Execute a 3-step process.")
    print(f"  Tool calls: {call_count[0]}")
    print(f"  Response: {response.content[:200]}")
    print(f"  Cost: ${response.total_cost_usd:.6f}\n")


# ============================================================================
# Demo 4: CompressionManager 集成到 Agent（需要 LLM API）
# ============================================================================

async def demo_compression_with_agent():
    """展示 Agent 使用 CompressionManager 自动管理上下文。"""
    from agentica import Agent, OpenAIChat
    from agentica.agent.config import ToolConfig
    from agentica.compression.manager import CompressionManager

    print("=" * 60)
    print("Demo 4: Agent + CompressionManager 自动三层压缩")
    print("=" * 60)

    file_contents = {f"file_{i}.py": f"# File {i}\n" + "x = " + str(i) * 100 for i in range(10)}

    async def read_source_file(filename: str) -> str:
        """Read a Python source file.

        Args:
            filename: Name of the file to read.
        """
        import asyncio
        await asyncio.sleep(0.02)
        return file_contents.get(filename, f"# {filename} not found")

    # CompressionManager: low token threshold for demo
    cm = CompressionManager(
        compress_token_limit=2000,      # low threshold to trigger compression in demo
        truncate_head_chars=50,         # keep max 50 chars per old tool result
    )

    agent = Agent(
        model=OpenAIChat(id="gpt-4o-mini"),
        tools=[read_source_file],
        tool_config=ToolConfig(
            compress_tool_results=True,
            compression_manager=cm,
        ),
        instructions=["You are a code analyzer. Read files and summarize their purpose."],
    )

    response = await agent.run(
        "Read file_0.py, file_1.py, file_2.py, file_3.py, file_4.py "
        "and tell me what each one does."
    )

    print(response.content[:400])
    print(f"\n  Compression stats: {cm.get_stats()}")
    print(f"  Token usage: {response.usage.total_tokens if response.usage else 'N/A'} tokens")
    print(f"  Cost: ${response.total_cost_usd:.6f}")
    print(f"\n  cost_summary:\n{chr(10).join('    ' + l for l in response.cost_summary.splitlines())}\n")


# ============================================================================
# Main
# ============================================================================

async def main():
    # Demo 1 & 2 无需 API Key，始终运行
    demo_micro_compact()
    demo_auto_compact_config()

    api_key = os.environ.get("OPENAI_API_KEY", "")
    if api_key:
        await demo_loop_state_management()
        await demo_compression_with_agent()
    else:
        print("=" * 60)
        print("Demo 3 & 4: Skipped (set OPENAI_API_KEY to run)")
        print("  运行: export OPENAI_API_KEY=sk-xxx && python 12_compression_and_loop.py")
        print("=" * 60)


if __name__ == "__main__":
    asyncio.run(main())
