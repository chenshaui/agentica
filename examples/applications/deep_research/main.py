# -*- coding: utf-8 -*-
"""
@author:XuMing(xuming624@qq.com)
@description: Deep Research Agent — Full-featured demo with ALL capabilities enabled.

Features enabled:
- 40+ built-in tools (file ops, web search, execute, task, memory, etc.)
- JSONL SessionLog (CC-style append-only, compact boundary, /resume support)
- Conversation archive (auto_archive → search_conversations can find history)
- Workspace memory (AGENT.md, MEMORY.md, daily memory, git context)
- Context compression (micro-compact + auto-compact + reactive compact)
- Death spiral detection (5 consecutive all-error turns → stop)
- Cost tracking (per-model USD, RunResponse.cost_summary)
- Cost budget (max_cost_usd, prevents runaway spending)
- Agentic prompt (enhanced system prompt with heartbeat/soul/tools guide)
- Add history to messages (multi-turn context from previous runs)
- Markdown output formatting
- Debug logging

Usage:
    # Interactive mode (full CLI experience)
    python main.py

    # Single query mode
    python main.py --query "RAG技术的原理和最佳实践"

    # With cost budget
    python main.py --query "深度分析 Transformer 架构" --max_cost 2.0

    # Resume previous session
    python main.py --resume
"""
import argparse
import sys
import os

sys.path.append(os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))))

from agentica import DeepAgent, RunConfig
from agentica.memory.session_log import SessionLog


def create_full_agent(session_id: str = None, work_dir: str = None) -> DeepAgent:
    """Create a full-featured agent — one line with DeepAgent."""
    return DeepAgent(
        session_id=session_id,
        work_dir=work_dir,
        description=(
            "You are a world-class deep research assistant with full system access. "
            "You can search the web, read/write files, execute code, manage tasks, "
            "save long-term memories, and conduct multi-step research with iterative refinement. "
            "Always cite sources, cross-validate findings, and produce comprehensive reports."
        ),
    )


def interactive_mode(agent: Agent):
    """Run in interactive mode with streaming."""
    from rich.console import Console
    console = Console()

    console.print(f"\n[bold cyan]{'=' * 60}[/bold cyan]")
    console.print(f"[bold]Deep Research Agent — Full Feature Mode[/bold]")
    console.print(f"[dim]Session: {agent.session_id}[/dim]")
    console.print(f"[dim]Model: {agent.model.id}[/dim]")
    console.print(f"[dim]Tools: {len(agent.get_tools() or [])} loaded[/dim]")
    console.print(f"[bold cyan]{'=' * 60}[/bold cyan]")
    console.print("[dim]Type your question, or 'exit' to quit.[/dim]\n")

    while True:
        try:
            user_input = console.input("[bold green]You:[/bold green] ").strip()
            if not user_input:
                continue
            if user_input.lower() in ("exit", "quit", "/exit", "/quit"):
                break

            console.print()
            # Stream response
            for chunk in agent.run_stream_sync(user_input):
                if chunk and chunk.content:
                    console.print(chunk.content, end="")
            console.print("\n")

            # Show cost after each turn
            if agent.run_response and agent.run_response.cost_tracker:
                ct = agent.run_response.cost_tracker
                if ct.total_cost_usd > 0:
                    console.print(
                        f"[dim]💰 Cost: ${ct.total_cost_usd:.4f} "
                        f"({ct.total_input_tokens} in + {ct.total_output_tokens} out)[/dim]\n"
                    )

        except KeyboardInterrupt:
            console.print("\n[yellow]Interrupted.[/yellow]")
            break
        except Exception as e:
            console.print(f"\n[red]Error: {e}[/red]\n")

    console.print("[dim]Session saved. Use --resume to continue later.[/dim]")


def single_query_mode(agent: Agent, query: str, max_cost: float = None):
    """Run a single query with optional cost budget."""
    config = RunConfig(max_cost_usd=max_cost) if max_cost else None

    print(f"\n{'=' * 60}")
    print(f"Deep Researching: {query}")
    print(f"Session: {agent.session_id}")
    print(f"{'=' * 60}\n")

    response = agent.run_sync(query, config=config)
    print(response.content)

    # Show cost summary
    if response.cost_tracker and response.cost_tracker.total_cost_usd > 0:
        print(f"\n{'─' * 40}")
        print(response.cost_tracker.summary())


def resume_mode():
    """List and resume a previous session."""
    sessions = SessionLog.list_sessions()
    if not sessions:
        print("No sessions found to resume.")
        return

    print(f"\n{'=' * 60}")
    print("Available sessions:")
    print(f"{'=' * 60}\n")

    for i, s in enumerate(sessions[:10], 1):
        ts = s.get("last_timestamp", "")
        if ts:
            ts = ts[:19].replace("T", " ")
        size_kb = s["size_bytes"] / 1024
        print(f"  {i}. {s['session_id']}  {ts}  ({size_kb:.0f}KB)")

    print()
    choice = input("Enter session number or ID to resume (or Enter to cancel): ").strip()
    if not choice:
        return

    # Resolve choice
    try:
        idx = int(choice) - 1
        if 0 <= idx < len(sessions):
            chosen = sessions[idx]
        else:
            print("Invalid number.")
            return
    except ValueError:
        matching = [s for s in sessions if choice in s["session_id"]]
        if matching:
            chosen = matching[0]
        else:
            print(f"No session matching '{choice}'")
            return

    print(f"\nResuming: {chosen['session_id']}")
    agent = create_full_agent(session_id=chosen["session_id"])
    interactive_mode(agent)


def main():
    parser = argparse.ArgumentParser(description="Deep Research Agent — Full Feature Mode")
    parser.add_argument("--query", "-q", type=str, help="Single query (non-interactive)")
    parser.add_argument("--resume", "-r", action="store_true", help="Resume a previous session")
    parser.add_argument("--session_id", "-s", type=str, help="Specific session ID")
    parser.add_argument("--max_cost", type=float, help="Max cost budget in USD")
    parser.add_argument("--work_dir", "-w", type=str, help="Working directory")
    parser.add_argument("--debug", "-d", action="store_true", help="Enable debug logging")
    args = parser.parse_args()

    if args.debug:
        os.environ["AGENTICA_LOG_LEVEL"] = "DEBUG"

    if args.resume:
        resume_mode()
        return

    agent = create_full_agent(session_id=args.session_id, work_dir=args.work_dir)

    if args.query:
        single_query_mode(agent, args.query, max_cost=args.max_cost)
    else:
        interactive_mode(agent)


if __name__ == "__main__":
    main()
