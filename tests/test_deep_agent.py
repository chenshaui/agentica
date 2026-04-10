import tempfile
import unittest
from unittest.mock import MagicMock, patch


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


if __name__ == "__main__":
    unittest.main()
