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

    def test_deep_agent_enables_experience_capture_by_default(self):
        """DeepAgent is the self-evolving flagship: experience + all capture_* on."""
        from agentica.agent.deep import DeepAgent

        with tempfile.TemporaryDirectory() as tmpdir, patch(
            "agentica.agent.base.Agent._load_mcp_tools"
        ), patch(
            "agentica.agent.base.Agent._merge_tool_system_prompts"
        ):
            agent = DeepAgent(model=MagicMock(), workspace=tmpdir)

        self.assertTrue(agent.experience, "DeepAgent.experience must default True")
        self.assertTrue(agent.experience_config.capture_tool_errors)
        self.assertTrue(agent.experience_config.capture_user_corrections)
        self.assertTrue(agent.experience_config.capture_success_patterns)
        # auto_extract_memory: fallback memory extraction after each run.
        self.assertTrue(agent.long_term_memory_config.auto_extract_memory)
        self.assertTrue(agent.long_term_memory_config.auto_archive)

    def test_deep_agent_respects_explicit_experience_false(self):
        """Passing experience=False must override the DeepAgent default."""
        from agentica.agent.deep import DeepAgent

        with tempfile.TemporaryDirectory() as tmpdir, patch(
            "agentica.agent.base.Agent._load_mcp_tools"
        ), patch(
            "agentica.agent.base.Agent._merge_tool_system_prompts"
        ):
            agent = DeepAgent(model=MagicMock(), workspace=tmpdir, experience=False)

        self.assertFalse(agent.experience)

    def test_deep_agent_wires_default_auxiliary_model(self):
        """DeepAgent must auto-wire a cheaper auxiliary_model for side tasks."""
        from agentica.agent.deep import DeepAgent
        from agentica.model.openai import OpenAIChat

        with tempfile.TemporaryDirectory() as tmpdir, patch(
            "agentica.agent.base.Agent._load_mcp_tools"
        ), patch(
            "agentica.agent.base.Agent._merge_tool_system_prompts"
        ):
            agent = DeepAgent(model=MagicMock(), workspace=tmpdir)

        self.assertIsInstance(agent.auxiliary_model, OpenAIChat)
        self.assertEqual(agent.auxiliary_model.id, "gpt-4o-mini")
        # CompressionManager must have been wired with the auxiliary model.
        cm = agent.tool_config.compression_manager
        self.assertIsNotNone(cm)
        self.assertIs(cm.model, agent.auxiliary_model)

    def test_deep_agent_explicit_auxiliary_model_passthrough(self):
        """An explicit auxiliary_model must be honored, not overridden."""
        from agentica.agent.deep import DeepAgent
        from agentica.model.openai import OpenAIChat

        custom_aux = OpenAIChat(id="gpt-4o-mini-custom", api_key="fake_openai_key")
        with tempfile.TemporaryDirectory() as tmpdir, patch(
            "agentica.agent.base.Agent._load_mcp_tools"
        ), patch(
            "agentica.agent.base.Agent._merge_tool_system_prompts"
        ):
            agent = DeepAgent(
                model=MagicMock(),
                workspace=tmpdir,
                auxiliary_model=custom_aux,
            )

        self.assertIs(agent.auxiliary_model, custom_aux)


if __name__ == "__main__":
    unittest.main()
