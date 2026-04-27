# -*- coding: utf-8 -*-
"""Regression tests for the centralized top-level API registry."""

import warnings

from agentica.api_registry import (
    DEPRECATED_TOP_LEVEL,
    LAZY_IMPORTS,
    PROVIDER_ALIAS_TO_SLUG,
    PUBLIC_API_ALL,
)


def test_api_registry_contains_core_lazy_exports():
    assert LAZY_IMPORTS["SqliteDb"] == "agentica.db.sqlite"
    assert LAZY_IMPORTS["OpenAIChat"] == "agentica.model.openai.chat"
    assert LAZY_IMPORTS["BuiltinTodoTool"] == "agentica.tools.buildin_tools"
    assert LAZY_IMPORTS["AskUserQuestionTool"] == "agentica.tools.user_input_tool"
    assert LAZY_IMPORTS["AskUserQuestionRequired"] == "agentica.tools.user_input_tool"
    assert "UserInputTool" not in LAZY_IMPORTS
    assert "UserInputRequired" not in LAZY_IMPORTS


def test_api_registry_contains_provider_aliases():
    assert PROVIDER_ALIAS_TO_SLUG["DeepSeekChat"] == "deepseek"
    assert PROVIDER_ALIAS_TO_SLUG["MoonshotChat"] == "moonshot"
    assert PROVIDER_ALIAS_TO_SLUG["ZhipuAIChat"] == "zhipuai"


def test_api_registry_contains_deprecated_top_level_paths():
    assert DEPRECATED_TOP_LEVEL["SqliteDb"] == "agentica.db.SqliteDb"
    assert DEPRECATED_TOP_LEVEL["Swarm"] == "agentica.swarm.Swarm"
    assert DEPRECATED_TOP_LEVEL["SubagentType"] == "agentica.subagent.SubagentType"


def test_agentica_public_api_uses_registry_names():
    import agentica

    assert agentica.__all__ == PUBLIC_API_ALL
    assert "DeepSeekChat" in agentica.__all__
    assert "SqliteDb" in agentica.__all__
    assert "AskUserQuestionTool" in agentica.__all__
    assert "AskUserQuestionRequired" in agentica.__all__
    assert "UserInputTool" not in agentica.__all__
    assert "UserInputRequired" not in agentica.__all__


def test_ask_user_question_tool_lazy_import():
    import agentica
    from agentica.tools.user_input_tool import AskUserQuestionRequired, AskUserQuestionTool

    assert agentica.AskUserQuestionTool is AskUserQuestionTool
    assert agentica.AskUserQuestionRequired is AskUserQuestionRequired


def test_agentica_dir_does_not_expose_registry_internals():
    import agentica

    visible = dir(agentica)
    assert "api_registry" not in visible
    assert "LAZY_IMPORTS" not in visible
    assert "DEPRECATED_TOP_LEVEL" not in visible
    assert "PUBLIC_API_ALL" not in visible


def test_deprecated_top_level_access_still_warns():
    import agentica

    with warnings.catch_warnings(record=True) as captured:
        warnings.simplefilter("always")
        _ = agentica.SubagentType

    assert any("deprecated" in str(w.message).lower() for w in captured)
