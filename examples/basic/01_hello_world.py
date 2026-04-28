# -*- coding: utf-8 -*-
"""
@author:XuMing(xuming624@qq.com)
@description: Hello World — the simplest Agent example.

Two ways to run an Agent. Pick whichever matches your code style.

▶ Sync (default, recommended for scripts / Jupyter / Gateway / FastAPI):
      result = agent.run_sync("...")
   No asyncio knowledge required. Internally Agentica still runs the
   tool loop concurrently — the `_sync` wrapper just hides the event loop.

▶ Async (when you're already inside an asyncio event loop, or want to
   await multiple agents in parallel):
      result = await agent.run("...")
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
from agentica import Agent, OpenAIChat


def sync_demo():
    """Sync style — no asyncio, no await. Recommended starting point."""
    agent = Agent(model=OpenAIChat(id="gpt-4o-mini"))
    result = agent.run_sync("一句话介绍北京")
    print("[sync]", result.content)


async def async_demo():
    """Async style — for code that already lives inside an event loop."""
    agent = Agent(model=OpenAIChat(id="gpt-4o-mini"))
    result = await agent.run("一句话介绍上海")
    print("[async]", result.content)


if __name__ == "__main__":
    sync_demo()

    import asyncio
    asyncio.run(async_demo())
