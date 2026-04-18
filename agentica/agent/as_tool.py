# -*- coding: utf-8 -*-
"""
@author:XuMing(xuming624@qq.com)
@description: Agent.as_tool() — wrap an Agent as a callable Function so it can
be used as a tool by another agent.

This is the *only* multi-agent surface area at the Agent level. Earlier "team"
machinery (transfer functions, has_team, get_transfer_prompt) has been removed
in favor of two clearer composition primitives:
  - ``Agent.as_tool()`` — orchestrator agent calls a worker agent as a tool
  - ``Swarm`` / ``BuiltinTaskTool`` — explicit multi-agent runtimes
"""

import json
from typing import Any, Callable, Optional

from agentica.tools.base import Function


def _serialize_content(content: Any) -> str:
    """Serialize agent response content to string.

    Handles Pydantic models, lists of Pydantic models, dicts, and plain values.
    """
    if isinstance(content, str):
        return content

    if hasattr(content, 'model_dump'):
        return json.dumps(content.model_dump(), ensure_ascii=False, default=str)

    if isinstance(content, (list, tuple)):
        items = [i.model_dump() if hasattr(i, 'model_dump') else i for i in content]
        return json.dumps(items, ensure_ascii=False, default=str)

    return json.dumps(content, ensure_ascii=False, default=str)


class AsToolMixin:
    """Mixin exposing ``as_tool()`` on Agent."""

    def as_tool(
        self,
        tool_name: Optional[str] = None,
        tool_description: Optional[str] = None,
        custom_output_extractor: Optional[Callable] = None,
    ) -> Function:
        """Convert this Agent to a Function that can be used by other agents.

        Args:
            tool_name: Tool name. Defaults to snake_case of agent name or 'agent_{id}'.
            tool_description: Tool description. Defaults to when_to_use or description.
            custom_output_extractor: Optional callable to extract output from RunResponse.

        Returns:
            A Function instance that wraps this agent.
        """
        if tool_name:
            name = tool_name
        elif self.name:
            name = self.name.lower().replace(' ', '_').replace('-', '_')
        else:
            name = f"agent_{self.agent_id[:8]}"

        description = (
            tool_description
            or self.when_to_use
            or self.description
            or self.prompt_config.role
            or f"Run the {name} agent."
        )

        agent_self = self

        async def agent_entrypoint(message: str) -> str:
            """Run the agent with the given message and return the response."""
            clone = agent_self.clone()
            response = await clone.run(message)

            if custom_output_extractor and response:
                return custom_output_extractor(response)

            if response and response.content:
                return _serialize_content(response.content)
            return "No response from agent."

        return Function(
            name=name,
            description=description,
            entrypoint=agent_entrypoint,
        )
