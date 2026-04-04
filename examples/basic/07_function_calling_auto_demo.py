# -*- coding: utf-8 -*-
"""
@author:XuMing(xuming624@qq.com)
@description: Function Calling with Agentic Loop Demo

演示 Runner 中 agentic loop 的核心能力 — 多轮链式推理：

  用户发一条消息 → LLM 返回 tool_calls → Runner 执行工具 → 结果送回 LLM
  → LLM 可能再次调用工具 → ... → 最终 LLM 给出自然语言回答

两种对比：
1. **手动 Loop** — 循环在你的代码中，你需要处理 tool_calls 检测、消息拼接、终止判断
2. **Agentica Agent** — Runner 内置完整 agentic loop，自动处理多轮链式调用、
   并行执行、成本追踪、死循环检测，一行代码搞定

Runner agentic loop 特性：
  ✅ 自动检测 tool_calls 并循环
  ✅ 多工具并行执行 (asyncio.gather)
  ✅ 死循环/death spiral 检测
  ✅ 成本预算控制
  ✅ max_tokens 截断恢复（自动续写）
  ✅ 上下文压缩 pipeline（每轮 LLM 调用前）
  ✅ API 错误重试 + reactive compact
  ✅ 生命周期 hooks (on_llm_start/end, on_tool_start/end)

运行方式:
    python examples/basic/07_function_calling_auto_demo.py
"""
import os
import sys
import json
import asyncio

sys.path.append(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))


# ============================================================================
# 工具定义 — 模拟真实场景：天气、计算、知识库
# ============================================================================

def get_weather(location: str) -> str:
    """获取指定城市的天气信息。

    Args:
        location: 城市名称，如 "北京"、"上海"

    Returns:
        天气信息的字符串描述
    """
    weather_data = {
        "北京": {"temp": 15, "condition": "晴天", "humidity": 45},
        "上海": {"temp": 18, "condition": "多云", "humidity": 65},
        "深圳": {"temp": 25, "condition": "小雨", "humidity": 80},
        "广州": {"temp": 27, "condition": "多云", "humidity": 70},
        "成都": {"temp": 20, "condition": "阴天", "humidity": 55},
    }
    data = weather_data.get(location, {"temp": 20, "condition": "晴天", "humidity": 50})
    return f"{location}天气：{data['condition']}，温度 {data['temp']}°C，湿度 {data['humidity']}%"


def calculate(expression: str) -> str:
    """计算数学表达式。

    Args:
        expression: 数学表达式，如 "2 + 3 * 4"

    Returns:
        计算结果
    """
    allowed_chars = set("0123456789+-*/.() ")
    if not all(c in allowed_chars for c in expression):
        return f"错误：表达式包含非法字符"
    result = eval(expression)
    return f"计算结果：{expression} = {result}"


def search_knowledge(query: str) -> str:
    """搜索知识库获取信息。

    Args:
        query: 搜索查询关键词

    Returns:
        搜索结果
    """
    knowledge = {
        "python": "Python 是一种高级编程语言，以简洁易读著称。最新稳定版本是 3.12。",
        "ai agent": "AI Agent 是能够自主执行任务的智能系统，通常包含感知、决策、执行三个核心模块。",
        "function calling": "Function Calling 允许 LLM 调用外部函数，实现与真实世界的交互。",
        "agentic loop": "Agentic Loop 是 AI Agent 的核心循环：接收消息 → 调用工具 → 获取结果 → 继续推理，直到任务完成。",
    }
    for key, value in knowledge.items():
        if key in query.lower():
            return f"找到相关信息：{value}"
    return f"未找到关于 '{query}' 的信息，建议使用更具体的关键词搜索。"


# 工具定义（OpenAI 格式，用于手动 Loop）
TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "get_weather",
            "description": "获取指定城市的天气信息",
            "parameters": {
                "type": "object",
                "properties": {
                    "location": {"type": "string", "description": "城市名称，如 '北京'、'上海'"}
                },
                "required": ["location"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "calculate",
            "description": "计算数学表达式",
            "parameters": {
                "type": "object",
                "properties": {
                    "expression": {"type": "string", "description": "数学表达式，如 '2 + 3 * 4'"}
                },
                "required": ["expression"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "search_knowledge",
            "description": "搜索知识库获取信息",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "搜索查询关键词"}
                },
                "required": ["query"]
            }
        }
    }
]

TOOL_FUNCTIONS = {
    "get_weather": get_weather,
    "calculate": calculate,
    "search_knowledge": search_knowledge,
}


# ============================================================================
# 方式 1：手动 Loop（传统方式，完全可控）
# ============================================================================

def demo_manual_loop():
    """
    手动实现 agentic loop。

    LLM 回复 tool_calls → 你来判断是否需要继续 → 手动拼消息 → 再调 LLM。
    这就是"没有 Runner"时你需要写的代码。

    典型流程（多轮链式推理）：
      Turn 1: LLM → 2 tool_calls (get_weather 北京, get_weather 上海) → 执行 → 结果送回
      Turn 2: LLM → 1 tool_call  (calculate 温差)                   → 执行 → 结果送回
      Turn 3: LLM → 最终回答
    """
    print("\n" + "=" * 70)
    print("方式 1: 手动 Agentic Loop (你需要自己写循环)")
    print("=" * 70)

    from openai import OpenAI
    client = OpenAI()

    query = "查询北京和上海的天气，然后计算两地温差"
    print(f"\n用户查询: {query}")
    print("-" * 70)

    messages = [{"role": "user", "content": query}]
    max_iterations = 10

    for iteration in range(1, max_iterations + 1):
        print(f"\n🔄 第 {iteration} 轮 LLM 调用...")

        response = client.chat.completions.create(
            model="gpt-4o",
            messages=messages,
            tools=TOOLS,
            tool_choice="auto",
        )

        assistant_message = response.choices[0].message
        finish_reason = response.choices[0].finish_reason

        if finish_reason == "tool_calls" and assistant_message.tool_calls:
            print(f"   LLM 请求调用 {len(assistant_message.tool_calls)} 个工具:")

            # 你必须手动拼装 assistant 消息
            messages.append({
                "role": "assistant",
                "content": assistant_message.content,
                "tool_calls": [
                    {
                        "id": tc.id,
                        "type": "function",
                        "function": {
                            "name": tc.function.name,
                            "arguments": tc.function.arguments
                        }
                    }
                    for tc in assistant_message.tool_calls
                ]
            })

            # 你必须手动执行每个工具、拼装 tool 消息
            for tool_call in assistant_message.tool_calls:
                func_name = tool_call.function.name
                func_args = json.loads(tool_call.function.arguments)

                print(f"   🔧 {func_name}({func_args})")

                if func_name in TOOL_FUNCTIONS:
                    result = TOOL_FUNCTIONS[func_name](**func_args)
                else:
                    result = f"未知工具: {func_name}"

                print(f"      → {result}")

                messages.append({
                    "role": "tool",
                    "tool_call_id": tool_call.id,
                    "content": result
                })

            # 你必须手动判断是否继续循环
            continue
        else:
            # 没有 tool_calls → LLM 给出最终回答
            print(f"\n✅ 最终响应 (经过 {iteration} 轮):")
            print(f"   {assistant_message.content}")
            break
    else:
        print(f"\n⚠️ 达到最大迭代次数 ({max_iterations})，强制终止")


# ============================================================================
# 方式 2：Agentica Agent — Runner 自动驱动 agentic loop
# ============================================================================

async def demo_agentica_agent():
    """
    使用 Agentica Agent，Runner 内置完整 agentic loop。

    Runner 自动完成以下所有步骤：
      1. 发送消息给 LLM
      2. 检测 tool_calls → 并行执行工具
      3. 将工具结果拼回消息 → 再次调用 LLM
      4. 重复 2-3 直到 LLM 不再请求工具调用
      5. 返回最终响应

    你只需一行：await agent.run("...")
    """
    from agentica import Agent, OpenAIChat, RunEvent

    print("\n" + "=" * 70)
    print("方式 2: Agentica Agent (Runner 自动驱动 agentic loop)")
    print("=" * 70)

    agent = Agent(
        model=OpenAIChat(id='gpt-4o-mini'),
        tools=[get_weather, calculate, search_knowledge],
    )

    # ---- Demo 2a: 非流式 — 多轮链式推理 ----
    print("\n【非流式】多轮链式推理")
    print("-" * 70)

    query = "查询北京和上海的天气，然后计算两地温差"
    print(f"用户查询: {query}\n")

    response = await agent.run(query)

    # 通过 response.tool_calls 回顾完整的工具调用链
    if response.tool_calls:
        print(f"📊 Runner 自动完成了 {response.tool_call_count} 次工具调用:")
        for i, tc in enumerate(response.tool_calls, 1):
            status = "❌" if tc.is_error else "✅"
            print(f"   {i}. {tc.tool_name}({tc.tool_args}) → {tc.content}  [{tc.elapsed:.2f}s] {status}")

    print(f"\n最终响应:\n   {response.content}")
    if response.total_cost_usd:
        print(f"\n   💰 总成本: ${response.total_cost_usd:.6f}")

    # ---- Demo 2b: 流式 — 实时观察 agentic loop 每一步 ----
    print("\n\n【流式】实时观察 agentic loop 每一步")
    print("-" * 70)

    query2 = "查查什么是 Agentic Loop，然后告诉我北京和深圳的天气，最后算出两地温差"
    print(f"用户查询: {query2}\n")

    async for chunk in agent.run_stream(query2):
        if chunk.event == RunEvent.tool_call_started:
            print(f"   🔧 [工具开始] {chunk.content}")
        elif chunk.event == RunEvent.tool_call_completed:
            print(f"   ✅ [工具完成] {chunk.content}")
        elif chunk.event == RunEvent.run_response and chunk.content:
            print(chunk.content, end="", flush=True)

    print()


# ============================================================================
# 对比总结
# ============================================================================

def print_comparison():
    """打印两种方式的对比总结"""
    print("\n" + "=" * 70)
    print("两种实现方式对比")
    print("=" * 70)

    comparison = """
┌───────────────────┬─────────────────────────┬─────────────────────────────┐
│      特性          │     手动 Loop            │   Agentica Agent (Runner)   │
├───────────────────┼─────────────────────────┼─────────────────────────────┤
│ 核心循环           │ 你的代码中 while True    │ Runner._run_impl 内自动驱动  │
│ tool_calls 检测    │ 手动判断 finish_reason   │ Runner 自动检测              │
│ 多工具并行执行      │ 需要自己 asyncio.gather  │ 框架自动 asyncio.gather      │
│ 消息拼装           │ 手动 append tool 消息    │ 框架自动处理                 │
│ 死循环检测         │ 需要自己实现             │ death spiral 自动检测        │
│ 成本控制           │ 需要自己统计             │ CostTracker + 预算阈值       │
│ 上下文压缩         │ 需要自己实现             │ 4 阶段压缩 pipeline          │
│ API 错误重试       │ 需要自己实现             │ 指数退避 + reactive compact  │
│ max_tokens 恢复    │ 需要自己检测 length      │ 自动注入 "Continue" 续写     │
│ Hooks 生命周期     │ 无                      │ on_llm_start/end + tool      │
│ 流式输出           │ 可自定义                │ run_stream() + events        │
│ 代码复杂度         │ ~60 行 (仅基础功能)      │ 1 行: await agent.run()     │
│ 适用场景           │ 学习原理 / 深度定制       │ 生产环境                    │
└───────────────────┴─────────────────────────┴─────────────────────────────┘

Runner Agentic Loop 自动处理的完整流程：

    ┌──────────────┐
    │  用户消息     │
    └──────┬───────┘
           ▼
    ┌──────────────┐     ┌──────────────────────┐
    │ 压缩 pipeline │◀────│ 每轮 LLM 调用前自动执行 │
    └──────┬───────┘     └──────────────────────┘
           ▼
    ┌──────────────┐
    │  调用 LLM    │◀──── _call_with_retry (指数退避 + reactive compact)
    └──────┬───────┘
           ▼
    ┌──────────────┐     yes    ┌────────────────────┐
    │ 有 tool_calls?│──────────▶│ 并行执行工具        │
    └──────┬───────┘            │ (asyncio.gather)    │
           │ no                 └──────────┬─────────┘
           ▼                               │
    ┌──────────────┐                       │
    │  返回响应     │               ┌───────▼────────┐
    └──────────────┘               │ 安全检查:       │
                                   │  - 死循环检测    │
                                   │  - 成本预算      │
                                   │  - 取消信号      │
                                   └───────┬────────┘
                                           │
                                           ▼ 回到 "压缩 pipeline"
"""
    print(comparison)


# ============================================================================
# Main
# ============================================================================

async def main():
    """运行所有演示"""
    print("Function Calling with Agentic Loop — 两种实现方式对比演示")
    print("=" * 70)
    print("核心概念: LLM 返回 tool_calls → 执行工具 → 结果送回 LLM → 循环直到完成")
    print("=" * 70)

    api_key = os.environ.get("OPENAI_API_KEY", "")
    if not api_key:
        print("\n⚠️  请设置 OPENAI_API_KEY 环境变量后运行:")
        print("   export OPENAI_API_KEY=sk-xxx")
        print("   python examples/basic/07_function_calling_auto_demo.py")
        return

    # 方式 1: 手动 Loop
    demo_manual_loop()

    # 方式 2: Agentica Agent
    await demo_agentica_agent()

    # 对比总结
    print_comparison()


if __name__ == "__main__":
    asyncio.run(main())
