import tempfile
import unittest
from unittest.mock import MagicMock, patch

import pytest

# DeepAgent defaults include BuiltinWebSearchTool which needs [crawl] extras.
try:
    import bs4  # noqa: F401
    _has_crawl_extras = True
except ImportError:
    _has_crawl_extras = False


@pytest.mark.skipif(not _has_crawl_extras, reason="DeepAgent defaults need agentica[crawl]")
class TestDeepAgentDefaults(unittest.TestCase):
    """DeepAgent should be the batteries-included default."""

    def test_deep_agent_defaults_enable_skills_and_auto_load_mcp(self):
        from agentica.agent.deep import DeepAgent
        from agentica.tools.skill_tool import SkillTool

        with tempfile.TemporaryDirectory() as tmpdir, patch(
            "agentica.agent.base.Agent._load_mcp_tools"
        ) as load_mcp_tools, patch(
            "agentica.agent.base.Agent._merge_tool_system_prompts"
        ):
            agent = DeepAgent(model=MagicMock(), workspace=tmpdir)

        self.assertTrue(agent.tool_config.auto_load_mcp)
        self.assertTrue(any(isinstance(tool, SkillTool) for tool in agent.tools))
        load_mcp_tools.assert_called_once()

    def test_deep_agent_model_exposes_file_tools(self):
        from agentica.agent.deep import DeepAgent
        from agentica.model.openai import OpenAIChat

        with tempfile.TemporaryDirectory() as tmpdir, patch(
            "agentica.agent.base.Agent._load_mcp_tools"
        ):
            agent = DeepAgent(
                model=OpenAIChat(id="gpt-4o-mini", api_key="fake_openai_key"),
                workspace=tmpdir,
                include_skills=False,
            )
            agent.update_model()

        self.assertIn("read_file", agent.model.functions)
        self.assertIn("ls", agent.model.functions)
        tool_names = {tool["function"]["name"] for tool in agent.model.tools}
        self.assertIn("read_file", tool_names)
        self.assertIn("ls", tool_names)


if __name__ == "__main__":
    unittest.main()
