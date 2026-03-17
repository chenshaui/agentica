# -*- coding: utf-8 -*-
"""
@author:XuMing(xuming624@qq.com)
@description: Swarm multi-agent parallel collaboration demo

Demonstrates Swarm — a peer-to-peer autonomous collaboration system that differs
from Team (master-slave, serial transfer) and Workflow (deterministic pipeline):

1. **Parallel mode**: All agents run the same task concurrently, results are merged
2. **Autonomous mode**: A coordinator decomposes tasks, assigns to workers, synthesizes results

Key features shown:
- Swarm parallel execution
- Swarm autonomous mode with coordinator
- SwarmResult structure (content, agent_results, mode, total_time)
"""
import sys
import os

sys.path.append(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

import asyncio
from agentica import Agent, OpenAIChat
from agentica.swarm import Swarm


# ---------------------------------------------------------------------------
# 1. Define specialized agents
# ---------------------------------------------------------------------------

researcher = Agent(
    name="researcher",
    model=OpenAIChat(id="gpt-4o-mini"),
    description="An expert researcher skilled at finding information and providing factual analysis.",
    instructions="You are a researcher. Provide factual, well-sourced analysis. Be concise.",
)

coder = Agent(
    name="coder",
    model=OpenAIChat(id="gpt-4o-mini"),
    description="An expert programmer skilled at writing clean Python code and technical solutions.",
    instructions="You are a coder. Write clean, well-documented Python code. Be practical.",
)

reviewer = Agent(
    name="reviewer",
    model=OpenAIChat(id="gpt-4o-mini"),
    description="A senior code reviewer who evaluates quality, correctness, and best practices.",
    instructions="You are a reviewer. Evaluate code quality, find potential issues, suggest improvements.",
)

# Coordinator (used in autonomous mode)
coordinator = Agent(
    name="coordinator",
    model=OpenAIChat(id="gpt-4o-mini"),
    description="A task coordinator that analyzes tasks and assigns them to the right team members.",
)


# ---------------------------------------------------------------------------
# 2. Parallel mode demo
# ---------------------------------------------------------------------------

async def demo_parallel():
    """All agents run the same task, results are merged."""
    print("=" * 60)
    print("Demo 1: Swarm Parallel Mode")
    print("=" * 60)
    print("All agents answer the same question, results are synthesized.\n")

    swarm = Swarm(
        agents=[researcher, coder, reviewer],
        mode="parallel",
    )

    result = await swarm.run("What are the top 3 best practices for building AI agents?")

    print(f"Mode: {result.mode}")
    print(f"Time: {result.total_time}s")
    print(f"Agent results: {len(result.agent_results)}")
    for r in result.agent_results:
        status = "OK" if r["success"] else "FAILED"
        print(f"  - {r['agent_name']}: [{status}] {r['content'][:80]}...")

    print(f"\nSynthesized response:\n{result.content[:500]}...")


# ---------------------------------------------------------------------------
# 3. Autonomous mode demo
# ---------------------------------------------------------------------------

async def demo_autonomous():
    """Coordinator decomposes task, assigns to agents, synthesizes."""
    print("\n" + "=" * 60)
    print("Demo 2: Swarm Autonomous Mode")
    print("=" * 60)
    print("Coordinator decomposes the task and assigns subtasks to agents.\n")

    swarm = Swarm(
        agents=[researcher, coder, reviewer],
        mode="autonomous",
        coordinator=coordinator,
        max_concurrent=3,
    )

    result = await swarm.run(
        "Build a Python function that fetches weather data from an API, "
        "with proper error handling and tests."
    )

    print(f"Mode: {result.mode}")
    print(f"Time: {result.total_time}s")
    print(f"Agent results: {len(result.agent_results)}")
    for r in result.agent_results:
        status = "OK" if r["success"] else "FAILED"
        subtask = r.get("subtask", "N/A")
        print(f"  - {r['agent_name']}: [{status}] subtask={subtask[:60]}")

    print(f"\nSynthesized response:\n{result.content[:800]}...")


# ---------------------------------------------------------------------------
# 4. Run all demos
# ---------------------------------------------------------------------------

async def main():
    print("Agentica Swarm: Multi-Agent Parallel Collaboration\n")

    await demo_parallel()
    await demo_autonomous()

    print("\n" + "=" * 60)
    print("Swarm vs Team vs Workflow")
    print("=" * 60)
    print("""
    Swarm     — Peer-to-peer parallel collaboration (parallel / autonomous)
    Team      — Master-slave serial transfer (coordinator delegates sequentially)
    Workflow  — Deterministic pipeline (fixed execution order)
    """)


if __name__ == "__main__":
    asyncio.run(main())
