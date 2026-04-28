# -*- coding: utf-8 -*-
"""
@author:XuMing(xuming624@qq.com)
@description: History filter demo — customize multi-turn history before it's sent to the LLM.

Two layers, both opt-in:

1. HistoryConfig (declarative, covers ~80% of cases):
   - excluded_tools=["search_*"]      → drop noisy tool results
   - assistant_max_chars=200          → truncate long AI replies

2. history_filter callable (escape hatch, anything Python can express):
   - strip user-prompt prefixes ("用纯文本回复 ...")
   - drop messages by metadata
   - reformat reasoning content
   - whatever else

Both run sync — uses run_sync for simplicity (no asyncio needed).
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from agentica import Agent, HistoryConfig, OpenAIChat
from agentica.model.message import Message


def demo_excluded_tools():
    """Drop search_* tool results from history — they're huge and rarely needed on later turns."""
    print("=" * 60)
    print("Demo 1: Drop search_* tool results from history")
    print("=" * 60)

    agent = Agent(
        model=OpenAIChat(id="gpt-4o-mini"),
        add_history_to_context=True,
        num_history_turns=5,
        history_config=HistoryConfig(
            excluded_tools=["search_*", "web_search"],
        ),
    )
    agent.run_sync("Hi, just remember the number 42.")
    agent.run_sync("What number did I tell you?")
    print("→ History is intact, but any search_* tool results would have been stripped.\n")


def demo_truncate_assistant():
    """Cap AI reply length in history to save tokens on long-running sessions."""
    print("=" * 60)
    print("Demo 2: Truncate assistant replies in history to 200 chars")
    print("=" * 60)

    agent = Agent(
        model=OpenAIChat(id="gpt-4o-mini"),
        add_history_to_context=True,
        num_history_turns=10,
        history_config=HistoryConfig(assistant_max_chars=200),
    )
    agent.run_sync("Write a one-sentence summary of Python.")
    agent.run_sync("Now in two sentences.")
    print("→ Earlier assistant replies are truncated to 200 chars + '...' in the history window.\n")


def demo_user_filter_callable():
    """Custom filter: strip a user-prompt prefix and lowercase user messages."""
    print("=" * 60)
    print("Demo 3: history_filter callable — strip prefix from user messages")
    print("=" * 60)

    PREFIX = "用纯文本回复 "

    def strip_prefix(history):
        out = []
        for m in history:
            if m.role == "user" and isinstance(m.content, str) and m.content.startswith(PREFIX):
                m = m.model_copy(update={"content": m.content[len(PREFIX):]})
            out.append(m)
        return out

    agent = Agent(
        model=OpenAIChat(id="gpt-4o-mini"),
        add_history_to_context=True,
        num_history_turns=5,
        history_filter=strip_prefix,
    )
    agent.run_sync(f"{PREFIX}北京天气如何？")
    agent.run_sync(f"{PREFIX}那上海呢？")
    print("→ The prefix '用纯文本回复 ' was stripped before history reached the model.\n")


def demo_combined():
    """Combine config rules + callable: rules run first, callable has final say."""
    print("=" * 60)
    print("Demo 4: HistoryConfig + history_filter combined")
    print("=" * 60)

    def drop_old_user_prefixes(history):
        out = []
        for m in history:
            if m.role == "user" and isinstance(m.content, str):
                m = m.model_copy(update={"content": m.content.removeprefix("Q: ")})
            out.append(m)
        return out

    agent = Agent(
        model=OpenAIChat(id="gpt-4o-mini"),
        add_history_to_context=True,
        num_history_turns=5,
        history_config=HistoryConfig(
            excluded_tools=["search_*"],
            assistant_max_chars=300,
        ),
        history_filter=drop_old_user_prefixes,
    )
    agent.run_sync("Q: Remember the city Tokyo.")
    agent.run_sync("Q: What city?")
    print("→ Pipeline: drop search_* → truncate assistant → strip 'Q: ' prefix → consistency fix.\n")


if __name__ == "__main__":
    demo_excluded_tools()
    demo_truncate_assistant()
    demo_user_filter_callable()
    demo_combined()
