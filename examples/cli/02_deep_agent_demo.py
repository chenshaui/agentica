# -*- coding: utf-8 -*-
"""
@author:XuMing(xuming624@qq.com)
@description: Deep Agent Demo — interactive demo of DeepAgent.
"""
import asyncio
import sys
import os

sys.path.append(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))


from agentica import DeepAgent, RunConfig, pprint_run_response


async def main():
    """Run DeepAgent with sample queries to demonstrate full capabilities."""
    print("=" * 60)
    print("Deep Agent Demo")
    print("=" * 60)

    agent = DeepAgent(
        debug=True,
    )
    print(f"Model: {agent.model.id}")
    print(f"Tools: {len(agent.get_tools() or [])} loaded")
    while True:
        query = input("Enter your query: ")
        if query.lower() in ["exit", "quit", "bye"]:
            break
        await agent.print_response_stream(query)

    print("Goodbye!")
if __name__ == "__main__":
    asyncio.run(main())
