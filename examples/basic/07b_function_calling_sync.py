# -*- coding: utf-8 -*-
"""
@author:XuMing(xuming624@qq.com)
@description: Minimal sync function-calling example.

For users who don't want to learn asyncio just to use tools. The full
agentic loop (LLM ↔ tool ↔ LLM ↔ ...) runs internally in an event loop;
the sync wrapper just hides it.

For the deeper "manual loop vs Runner" comparison, see
07_function_calling_auto_demo.py.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from agentica import Agent, OpenAIChat


def get_weather(location: str) -> str:
    """Get weather for a city.

    Args:
        location: city name, e.g. '北京', '上海'.
    """
    data = {
        "北京": "晴, 15°C",
        "上海": "多云, 18°C",
        "深圳": "小雨, 25°C",
    }
    return f"{location} 天气：{data.get(location, '20°C 晴')}"


def calculate(expression: str) -> str:
    """Evaluate a simple math expression.

    Args:
        expression: numeric expression, e.g. '15 - 18'.
    """
    if not all(c in "0123456789+-*/.() " for c in expression):
        return "expression contains illegal characters"
    return f"{expression} = {eval(expression)}"


if __name__ == "__main__":
    agent = Agent(
        model=OpenAIChat(id="gpt-4o-mini"),
        tools=[get_weather, calculate],
    )
    response = agent.run_sync("查一下北京和上海的天气，然后告诉我两地温差。")
    print("→", response.content)

    if response.tool_calls:
        print(f"\nRunner ran {response.tool_call_count} tool calls automatically:")
        for tc in response.tool_calls:
            print(f"  • {tc.tool_name}({tc.tool_args}) → {tc.content}")
