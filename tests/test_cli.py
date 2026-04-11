# -*- coding: utf-8 -*-
"""
@author: XuMing(xuming624@qq.com)
@description: Unit tests for CLI module.
"""
import sys
import unittest
from unittest.mock import Mock, patch, MagicMock
import os
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from agentica.cost_tracker import CostTracker
from agentica.cli import (
    TOOL_ICONS,
    TOOL_REGISTRY,
)


class TestToolIcons(unittest.TestCase):
    """Test cases for TOOL_ICONS configuration."""

    def test_tool_icons_exists(self):
        """Test TOOL_ICONS dictionary exists."""
        self.assertIsInstance(TOOL_ICONS, dict)

    def test_default_icon_exists(self):
        """Test default icon exists."""
        self.assertIn("default", TOOL_ICONS)

    def test_common_icons_exist(self):
        """Test common tool icons exist."""
        expected_icons = ["read_file", "write_file", "execute", "web_search"]
        for icon in expected_icons:
            self.assertIn(icon, TOOL_ICONS)

    def test_icons_are_strings(self):
        """Test all icons are strings."""
        for key, value in TOOL_ICONS.items():
            self.assertIsInstance(key, str)
            self.assertIsInstance(value, str)


class TestToolRegistry(unittest.TestCase):
    """Test cases for TOOL_REGISTRY configuration."""

    def test_tool_registry_exists(self):
        """Test TOOL_REGISTRY dictionary exists."""
        self.assertIsInstance(TOOL_REGISTRY, dict)

    def test_registry_format(self):
        """Test registry entries have correct format."""
        for tool_name, (module_name, class_name) in TOOL_REGISTRY.items():
            self.assertIsInstance(tool_name, str)
            self.assertIsInstance(module_name, str)
            self.assertIsInstance(class_name, str)

    def test_common_tools_registered(self):
        """Test common tools are registered."""
        expected_tools = ["arxiv", "duckduckgo", "wikipedia"]
        for tool in expected_tools:
            self.assertIn(tool, TOOL_REGISTRY)


class TestCLIHelpers(unittest.TestCase):
    """Test cases for CLI helper functions."""

    def test_tool_icon_lookup(self):
        """Test looking up tool icons."""
        # Test existing icon
        icon = TOOL_ICONS.get("read_file", TOOL_ICONS["default"])
        self.assertIsNotNone(icon)

        # Test default fallback
        icon = TOOL_ICONS.get("nonexistent_tool", TOOL_ICONS["default"])
        self.assertEqual(icon, TOOL_ICONS["default"])

    def test_display_token_stats_shows_context_usage(self):
        from agentica.cli.display import display_token_stats

        tracker = CostTracker()
        tracker.record("gpt-4o-mini", input_tokens=100, output_tokens=50)

        fake_console = MagicMock()
        display_token_stats(
            fake_console, tracker,
            context_window=128000,
            session_total_tokens=64000,
            tool_use_count=2,
            elapsed_seconds=5.32,
        )

        rendered = fake_console.print.call_args[0][0]
        self.assertIn("ctx 50.0%", rendered)
        self.assertIn("64K / 128K", rendered)
        self.assertIn("2 tools", rendered)
        self.assertIn("5.32s", rendered)

    def test_display_token_stats_singular_tool_use(self):
        from agentica.cli.display import display_token_stats

        tracker = CostTracker()
        tracker.record("gpt-4o-mini", input_tokens=500, output_tokens=200)

        fake_console = MagicMock()
        display_token_stats(
            fake_console, tracker,
            context_window=128000,
            session_total_tokens=700,
            tool_use_count=1,
            elapsed_seconds=1.0,
        )

        rendered = fake_console.print.call_args[0][0]
        self.assertIn("1 tool", rendered)
        self.assertNotIn("1 tools", rendered)

    def test_display_token_stats_no_tools_no_tool_label(self):
        from agentica.cli.display import display_token_stats

        tracker = CostTracker()
        tracker.record("gpt-4o-mini", input_tokens=100, output_tokens=50)

        fake_console = MagicMock()
        display_token_stats(
            fake_console, tracker,
            context_window=128000,
            session_total_tokens=150,
            tool_use_count=0,
            elapsed_seconds=0.5,
        )

        rendered = fake_console.print.call_args[0][0]
        self.assertNotIn("tool", rendered)

    def test_display_token_stats_fallback_without_session_tokens(self):
        """When session_total_tokens is 0, fall back to cost_tracker totals."""
        from agentica.cli.display import display_token_stats

        tracker = CostTracker()
        tracker.record("gpt-4o-mini", input_tokens=2000, output_tokens=500)

        fake_console = MagicMock()
        display_token_stats(fake_console, tracker, context_window=128000)

        rendered = fake_console.print.call_args[0][0]
        self.assertIn("2.5K / 128K", rendered)

    def test_format_tokens_short(self):
        from agentica.cli.display import _format_tokens_short

        self.assertEqual(_format_tokens_short(500), "500")
        self.assertEqual(_format_tokens_short(1000), "1K")
        self.assertEqual(_format_tokens_short(1500), "1.5K")
        self.assertEqual(_format_tokens_short(64000), "64K")
        self.assertEqual(_format_tokens_short(128000), "128K")
        self.assertEqual(_format_tokens_short(1000000), "1M")
        self.assertEqual(_format_tokens_short(1500000), "1.5M")

    def test_context_pct_style(self):
        from agentica.cli.display import context_pct_style
        self.assertEqual(context_pct_style(30), "green")
        self.assertEqual(context_pct_style(50), "yellow")
        self.assertEqual(context_pct_style(80), "red")
        self.assertEqual(context_pct_style(95), "bold red")

    def test_build_context_bar(self):
        from agentica.cli.display import build_context_bar
        bar = build_context_bar(50.0, width=10)
        self.assertEqual(bar.count("█"), 5)
        self.assertEqual(bar.count("░"), 5)
        bar0 = build_context_bar(0, width=10)
        self.assertNotIn("█", bar0)
        bar100 = build_context_bar(100, width=10)
        self.assertNotIn("░", bar100)

    def test_build_status_bar_fragments_narrow(self):
        from agentica.cli.display import build_status_bar_fragments
        frags = build_status_bar_fragments(
            model_name="gpt-4o", context_tokens=64000,
            context_window=128000, last_turn_seconds=12.3,
            terminal_width=40,
        )
        text = "".join(v for _, v in frags)
        self.assertIn("gpt-4o", text)
        self.assertIn("⏱ 12.3s", text)
        self.assertNotIn("64K", text)

    def test_build_status_bar_fragments_wide(self):
        from agentica.cli.display import build_status_bar_fragments
        frags = build_status_bar_fragments(
            model_name="gpt-4o", context_tokens=64000,
            context_window=128000, cost_usd=0.05,
            active_seconds=105.0, last_turn_seconds=12.3,
            terminal_width=100,
        )
        text = "".join(v for _, v in frags)
        self.assertIn("64K/128K", text)
        self.assertIn("50%", text)
        self.assertIn("$0.05", text)
        self.assertIn("⏱ 12.3s", text)
        self.assertIn("Σ 1m45s", text)

    def test_build_status_bar_fragments_cost_in_medium(self):
        from agentica.cli.display import build_status_bar_fragments
        frags = build_status_bar_fragments(
            model_name="gpt-4o", context_tokens=64000,
            context_window=128000, cost_usd=0.002,
            last_turn_seconds=5.0, terminal_width=60,
        )
        text = "".join(v for _, v in frags)
        self.assertIn("$0.0020", text)
        self.assertIn("50%", text)
        self.assertIn("⏱ 5.0s", text)

    def test_stream_display_manager_box_decorations(self):
        from agentica.cli.display import StreamDisplayManager
        fake = MagicMock()
        fake.width = 80
        dm = StreamDisplayManager(fake)
        dm.start_response()
        self.assertTrue(dm._box_opened)
        dm.stream_response("hello")
        dm.finalize()
        calls = [str(c) for c in fake.print.call_args_list]
        box_open = any("╭" in c for c in calls)
        box_close = any("╰" in c for c in calls)
        self.assertTrue(box_open, "Expected ╭ box opening")
        self.assertTrue(box_close, "Expected ╰ box closing")


class TestCLIImports(unittest.TestCase):
    """Test cases for CLI module imports."""

    def test_can_import_cli_module(self):
        """Test CLI module can be imported."""
        try:
            import agentica.cli
            self.assertTrue(True)
        except ImportError as e:
            self.fail(f"Failed to import cli module: {e}")

    def test_can_import_agent(self):
        """Test Agent can be imported from CLI."""
        try:
            from agentica import Agent
            self.assertTrue(True)
        except ImportError as e:
            self.fail(f"Failed to import Agent: {e}")


class TestCLIConfiguration(unittest.TestCase):
    """Test cases for CLI configuration."""

    def test_history_file_path(self):
        """Test history file path is set."""
        from agentica.cli import history_file
        self.assertIsInstance(history_file, str)
        self.assertTrue(history_file.endswith("cli_history.txt"))

    def test_parse_extensions_remove_command(self):
        """CLI supports `agentica extensions remove <skill-name>`."""
        from agentica.cli.config import parse_args

        with patch.object(
            sys,
            "argv",
            ["agentica", "extensions", "remove", "learn-from-experience"],
        ):
            args = parse_args()

        self.assertEqual(args.command, "extensions")
        self.assertEqual(args.extensions_command, "remove")
        self.assertEqual(args.skill_name, "learn-from-experience")

    def test_parse_extensions_install_command(self):
        """CLI parses local install sources without network access."""
        from agentica.cli.config import parse_args

        with patch.object(
            sys,
            "argv",
            ["agentica", "extensions", "install", "/tmp/mock-skill-repo"],
        ):
            args = parse_args()

        self.assertEqual(args.command, "extensions")
        self.assertEqual(args.extensions_command, "install")
        self.assertEqual(args.source, "/tmp/mock-skill-repo")

    def test_interactive_extensions_install_reports_replaced_symlinked_skill(self):
        """Interactive install prints when it replaces a symlinked skill."""
        import agentica.cli.interactive as interactive
        from agentica.skills.skill import Skill
        from agentica.skills.skill_registry import SkillRegistry

        refreshed_registry = SkillRegistry()
        refreshed_registry.register(
            Skill(
                name="learn-from-experience",
                description="Learn from feedback",
                path=MagicMock(),
                location="user",
            )
        )
        installed_skill = Skill(
            name="learn-from-experience",
            description="Learn from feedback",
            path=MagicMock(),
            location="user",
        )

        def fake_install_skills(source, destination_dir=None, force=False, replaced_symlinked_skills=None):
            self.assertTrue(force)
            self.assertEqual(source, "/tmp/mock-skill-repo")
            replaced_symlinked_skills.append("learn-from-experience")
            return [installed_skill]

        with patch.object(interactive, "install_skills", side_effect=fake_install_skills), patch.object(
            interactive, "reset_skill_registry"
        ), patch.object(interactive, "load_skills"), patch.object(
            interactive, "get_skill_registry", return_value=refreshed_registry
        ), patch.object(
            interactive, "create_agent", return_value=MagicMock()
        ), patch.object(interactive.console, "print") as console_print:
            interactive._cmd_extensions(
                cmd_args="install /tmp/mock-skill-repo --force",
                agent_config={"model_provider": "zhipuai", "model_name": "glm-5", "debug": False, "work_dir": None},
                extra_tools=[],
                workspace=None,
                skills_registry=SkillRegistry(),
            )

        self.assertTrue(
            any(
                "replaced existing symlinked skill" in str(call.args[0])
                for call in console_print.call_args_list
                if call.args
            )
        )

    def test_create_agent_moves_skills_summary_out_of_instructions(self):
        """CLI should not stuff skill summaries into static instructions."""
        from agentica.cli.config import create_agent
        from agentica.skills.skill import Skill
        from agentica.skills.skill_registry import SkillRegistry

        registry = SkillRegistry()
        registry.register(
            Skill(
                name="learn-from-experience",
                description="Learn from feedback",
                path=MagicMock(),
                location="user",
            )
        )

        class FakeDeepAgent:
            def __init__(self, **kwargs):
                self.instructions = kwargs.get("instructions")
                self.tools = []
                self.session_guidance = []

            def add_session_guidance(self, text):
                self.session_guidance.append(text)

        with patch("agentica.cli.config.get_model", return_value=MagicMock()), patch(
            "agentica.agent.deep.DeepAgent",
            FakeDeepAgent,
        ):
            agent = create_agent(
                {
                    "model_provider": "zhipuai",
                    "model_name": "glm-5",
                    "debug": False,
                    "work_dir": None,
                },
                extra_tools=[],
                workspace=None,
                skills_registry=registry,
            )

        self.assertIsNone(agent.instructions)
        self.assertEqual(len(agent.session_guidance), 1)
        self.assertIn("Available Skills", agent.session_guidance[0])


class TestToolRegistryIntegrity(unittest.TestCase):
    """Test cases for tool registry integrity."""

    def test_all_tools_have_valid_module_names(self):
        """Test all tools have valid module names."""
        for tool_name, (module_name, class_name) in TOOL_REGISTRY.items():
            # Module name should not be empty
            self.assertTrue(len(module_name) > 0, f"Empty module name for {tool_name}")
            # Class name should not be empty
            self.assertTrue(len(class_name) > 0, f"Empty class name for {tool_name}")
            # Class name should be PascalCase (start with uppercase)
            self.assertTrue(
                class_name[0].isupper(),
                f"Class name {class_name} should start with uppercase"
            )

    def test_no_duplicate_tools(self):
        """Test no duplicate tool names in registry."""
        tool_names = list(TOOL_REGISTRY.keys())
        self.assertEqual(len(tool_names), len(set(tool_names)))


if __name__ == "__main__":
    unittest.main()
