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
