# -*- coding: utf-8 -*-
"""
@author:XuMing(xuming624@qq.com)
@description: Basic workspace usage example

This example shows how to:
1. Create and initialize a workspace
2. Agent auto-registers BuiltinMemoryTool when workspace is set
3. LLM autonomously saves important info via save_memory tool call
4. auto_archive (zero cost) + auto_extract_memory (LLM cost) are separate configs
5. New agent from same workspace auto-loads context + memory
"""
import os
import sys
import asyncio
import tempfile
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from agentica import Agent, ZhipuAIChat
from agentica.agent.config import WorkspaceMemoryConfig
from agentica.workspace import Workspace


async def main():
    # Create a temporary workspace for demo
    temp_dir = tempfile.mkdtemp()
    workspace_path = Path(temp_dir) / "my_workspace"

    print(f"Creating workspace at: {workspace_path}")

    # Create and initialize workspace
    workspace = Workspace(workspace_path)
    workspace.initialize()

    # Customize workspace files
    workspace.write_file("USER.md", """# User Profile

## Preferences
- Language: Chinese
- Style: Concise and technical
- Focus: Python programming

## Context
Software developer working on AI projects.
""")

    workspace.write_file("AGENT.md", """# Agent Instructions

You are a helpful AI assistant specialized in Python programming.

## Guidelines
1. Provide concise, accurate answers
2. Include code examples when helpful
3. Use Chinese for explanations
""")

    # Create agent with workspace
    #
    # What happens automatically:
    # 1. BuiltinMemoryTool (save_memory, search_memory) is auto-registered
    #    because workspace is set -- LLM can save important info as tool calls
    # 2. auto_archive=True: saves raw conversation to conversations/ (zero cost)
    # 3. auto_extract_memory=True: if LLM didn't call save_memory, uses a
    #    sub-LLM call to extract memories (costs one extra LLM request)
    # 4. Workspace context (AGENT.md, USER.md) is injected into system prompt
    # 5. Workspace memories are auto-loaded into system prompt on next run
    agent = Agent(
        model=ZhipuAIChat(model="glm-4-flash"),
        workspace=workspace,
        long_term_memory_config=WorkspaceMemoryConfig(
            auto_archive=True,
            auto_extract_memory=True,
        ),
    )

    # Run 1: Tell the agent something memorable
    # The LLM may call save_memory directly (via BuiltinMemoryTool),
    # or MemoryExtractHooks will extract memories after the run.
    print("\n=== Run 1: Tell agent about yourself ===")
    response = await agent.run(
        "Hi! I'm a Python developer working on a RAG system. "
        "I prefer using pytest for testing and type hints everywhere. "
        "Please remember my preferences."
    )
    print(response.content)

    # Show what was saved
    print("\n=== Saved Memory ===")
    memory = await workspace.get_relevant_memories()
    if memory:
        print(memory)
    else:
        print("(Memory will be auto-extracted by MemoryExtractHooks)")

    # Run 2: Create a new agent from same workspace — it auto-loads context + memory
    print("\n=== Run 2 (new agent, same workspace) ===")
    agent2 = Agent.from_workspace(
        workspace_path=str(workspace_path),
        model=ZhipuAIChat(model="glm-4-flash"),
        long_term_memory_config=WorkspaceMemoryConfig(
            auto_archive=True,
            auto_extract_memory=True,
        ),
    )
    response2 = await agent2.run("What do you know about me? What are my preferences?")
    print(response2.content)

    # Clean up
    import shutil
    shutil.rmtree(temp_dir, ignore_errors=True)


if __name__ == "__main__":
    asyncio.run(main())
