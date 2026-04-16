# -*- coding: utf-8 -*-
"""
@author:XuMing(xuming624@qq.com)
@description: Skill Tool - executes skills within the conversation.

This tool allows agents to invoke skills that provide specialized knowledge
and workflows for specific tasks.

Skills are modular packages that extend agent capabilities by providing
specialized knowledge, workflows, and tools. When a skill is invoked,
its instructions are loaded into the conversation context.

Usage:
    from agentica import Agent
    from agentica.tools.skill_tool import SkillTool
    from agentica.tools.shell_tool import ShellTool

    # Basic usage - skills loaded on-demand (not auto-loaded at startup)
    agent = Agent(
        name="Skill-Enabled Agent",
        tools=[SkillTool(), ShellTool()],
    )

    # Auto-load all skills from standard directories at startup
    skill_tool = SkillTool(auto_load=True)
    agent = Agent(
        name="Auto-Load Skill Agent",
        tools=[skill_tool, ShellTool()],
    )

    # With custom skill directories
    skill_tool = SkillTool(custom_skill_dirs=["./my-skills/web-research"])
    agent = Agent(
        name="Custom Skill Agent",
        tools=[skill_tool, ShellTool()],
    )
"""
import json
from typing import List, Optional
from pathlib import Path

from agentica.tools.base import Tool
from agentica.skills import (
    Skill,
    SkillRegistry,
    get_skill_registry,
    load_skills,
    register_skill,
)
from agentica.skills.skill_loader import SkillLoader
from agentica.utils.log import logger


class SkillTool(Tool):
    """
    Tool for executing skills within the main conversation.

    Skills are modular packages that extend agent capabilities by providing
    specialized knowledge, workflows, and tools. When a skill is invoked,
    its instructions are loaded into the conversation context.

    Auto-loads skills from standard directories:
    - .claude/skills (project-level)
    - .agentica/skills (project-level)
    - ~/.claude/skills (user-level)
    - ~/.agentica/skills (user-level)

    Also supports custom skill directories via constructor.
    """

    _VISIBLE_GENERATED_STATUSES = {"shadow", "auto"}

    def __init__(
        self,
        custom_skill_dirs: Optional[List[str]] = None,
        auto_load: bool = False,
        name: str = "skill_tool",
    ):
        """
        Initialize the SkillTool.

        Args:
            custom_skill_dirs: Optional list of custom skill directory paths to load.
            auto_load: If True, automatically load skills from standard directories.
                       Default is False to avoid loading all skills on startup.
                       Set to True to auto-load all skills from standard directories.
            name: Name of the tool.
        """
        super().__init__(name=name)
        self._registry: Optional[SkillRegistry] = None
        self._custom_skill_dirs = custom_skill_dirs or []
        self._auto_load = auto_load
        self._initialized = False
        self._agent = None  # Set by Agent for runtime skill filtering

        # Register tool functions
        self.register(self.list_skills)
        self.register(self.get_skill_info)

    def _ensure_initialized(self):
        """Ensure skills are loaded before use."""
        if self._initialized:
            return

        # Auto-load from standard directories
        if self._auto_load:
            self._registry = load_skills()
        else:
            self._registry = get_skill_registry()

        self._load_custom_skill_dirs()

        self._initialized = True

    def _load_custom_skill_dirs(self) -> None:
        """Load custom skill directories during first initialization."""
        loader = SkillLoader()

        # Each entry can be either:
        # - A direct skill dir (contains SKILL.md) -> register it directly
        # - A parent dir with subdirectories (e.g., generated_skills/{slug}/SKILL.md)
        #   -> discover and register all visible generated sub-skills
        for skill_dir in self._custom_skill_dirs:
            skill_dir_path = Path(skill_dir).resolve()
            direct_md = skill_dir_path / "SKILL.md"
            if direct_md.exists():
                if (skill_dir_path / "meta.json").exists():
                    loaded = self._load_generated_skill(loader, direct_md)
                    if loaded and self._registry.register(loaded):
                        logger.info(f"Loaded generated skill: {loaded.name} from {direct_md}")
                else:
                    skill = register_skill(skill_dir)
                    if skill:
                        logger.info(f"Loaded custom skill: {skill.name} from {skill_dir}")
                continue

            if not skill_dir_path.is_dir():
                continue

            for skill_md_path in loader.discover_skills(skill_dir_path):
                loaded = self._load_generated_skill(loader, skill_md_path)
                if loaded and self._registry.register(loaded):
                    logger.info(f"Loaded generated skill: {loaded.name} from {skill_md_path}")

    @classmethod
    def _is_generated_skill_visible(cls, skill_md_path: Path) -> bool:
        """Check whether a generated skill should be visible at runtime."""
        meta_path = skill_md_path.parent / "meta.json"
        if not meta_path.exists():
            return True

        try:
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return False

        status = meta.get("status")
        return status in cls._VISIBLE_GENERATED_STATUSES

    def _load_generated_skill(self, loader: SkillLoader, skill_md_path: Path) -> Optional[Skill]:
        """Load a generated skill only when its runtime status is visible."""
        if not self._is_generated_skill_visible(skill_md_path):
            return None
        return loader.load_skill(skill_md_path, "generated")

    def reload_generated_skills(self) -> int:
        """Re-scan custom skill dirs and sync generated skills.

        This performs a full sync for generated skills:
        - add newly visible skills
        - remove rolled back / draft skills
        - refresh revised skills in-place

        Returns:
            Number of visible generated skills after sync.
        """
        if self._registry is None:
            return 0

        loader = SkillLoader()
        generated_skills = {}
        for skill_dir in self._custom_skill_dirs:
            skill_dir_path = Path(skill_dir).resolve()
            if not skill_dir_path.is_dir():
                continue
            for skill_md_path in loader.discover_skills(skill_dir_path):
                loaded = self._load_generated_skill(loader, skill_md_path)
                if loaded is not None:
                    generated_skills[loaded.name] = loaded

        existing_generated_names = {
            skill.name for skill in self._registry.list_by_location("generated")
        }

        for skill_name in existing_generated_names:
            self._registry.remove(skill_name)

        for skill in generated_skills.values():
            self._registry.register(skill)

        return len(generated_skills)

    @property
    def registry(self) -> SkillRegistry:
        """Get the skill registry, loading skills if needed."""
        self._ensure_initialized()
        return self._registry

    def _get_enabled_skills(self) -> list:
        """Get list of enabled skills, respecting agent-level and query-level filtering."""
        all_skills = self.registry.list_all()
        if self._agent is None:
            return all_skills
        return [s for s in all_skills if self._agent._is_skill_enabled(s.name)]

    def list_skills(self) -> str:
        """
        List all available skills.

        Returns:
            Formatted string containing list of available skills with their descriptions
        """
        skills = self._get_enabled_skills()

        if not skills:
            return (
                "No skills available.\n\n"
                "Skills can be added to:\n"
                "- .claude/skills/ (project-level)\n"
                "- .agentica/skills/ (project-level)\n"
                "- ~/.claude/skills/ (user-level)\n"
                "- ~/.agentica/skills/ (user-level)"
            )

        result = f"Available Skills ({len(skills)}):\n"
        result += "-" * 40 + "\n"
        for skill in skills:
            result += f"- {skill.name}\n"
            result += f"  Description: {skill.description}\n"
            result += f"  Location: {skill.location}\n"
            result += f"  Path: {skill.path}\n\n"

        return result.strip()

    def get_skill_info(self, skill_name: str) -> str:
        """
        Get detailed information and full instructions for a specific skill.

        This loads the complete SKILL.md content for the requested skill.

        Args:
            skill_name: Name of the skill to get info for

        Returns:
            Full skill content including instructions, or error if not found
        """
        # Check if skill is enabled
        if self._agent is not None and not self._agent._is_skill_enabled(skill_name):
            return f"Error: Skill '{skill_name}' is disabled."

        skill_obj = self.registry.get(skill_name)

        if skill_obj is None:
            available = [s.name for s in self._get_enabled_skills()]
            return (
                f"Error: Skill '{skill_name}' not found.\n"
                f"Available skills: {', '.join(available[:50]) if available else 'None'}"
            )

        # Return full skill prompt with instructions
        result = f"=== Skill: {skill_obj.name} ===\n"
        result += f"Description: {skill_obj.description}\n"
        result += f"Location: {skill_obj.location}\n"
        result += f"Path: {skill_obj.path}\n"
        if skill_obj.license:
            result += f"License: {skill_obj.license}\n"
        if skill_obj.allowed_tools:
            result += f"Allowed Tools: {', '.join(skill_obj.allowed_tools)}\n"
        
        # Include full instructions from SKILL.md
        result += f"\n--- Instructions ---\n{skill_obj.content}\n"

        return result

    def add_skill_dir(self, skill_dir: str) -> Optional[Skill]:
        """
        Add a custom skill directory at runtime.

        Args:
            skill_dir: Path to the skill directory containing SKILL.md

        Returns:
            Skill instance if loaded successfully, None otherwise
        """
        self._ensure_initialized()
        skill = register_skill(skill_dir)
        if skill:
            logger.info(f"Added skill: {skill.name} from {skill_dir}")
        return skill

    def get_system_prompt(self) -> Optional[str]:
        """
        Get the system prompt for the skill tool.

        This prompt is injected into the agent's system message to guide
        the LLM on how to use skills effectively. Only includes enabled skills.

        Returns:
            System prompt string describing available skills
        """
        self._ensure_initialized()
        skills = self._get_enabled_skills()

        if not skills:
            return """# Skills

No skills are currently available.

If a matching skill is later installed, load it with `get_skill_info(skill_name)` before acting on the task.
"""

        # Build skill summary list (name + description only)
        skill_list = []
        for skill in skills:
            trigger_info = f" (trigger: `{skill.trigger}`)" if skill.trigger else ""
            skill_list.append(f"- **{skill.name}**{trigger_info}: {skill.description}")

        skills_summary = "\n".join(skill_list)

        return f"""# Skills

Use a skill only when it clearly matches the current task.

## Available Skills ({len(skills)})
{skills_summary}

## Skill Workflow
- Load the matching skill with `get_skill_info(skill_name)` before giving task guidance.
- Treat slash commands like `/<something>` as skill references and load the matching skill first.
- Skills provide instructions, not executable actions.
- Do not mention a skill without loading it.
- Do not reload the same skill within the current turn.
"""

    def __repr__(self) -> str:
        self._ensure_initialized()
        skill_count = len(self.registry)
        return f"<SkillTool skills={skill_count}>"
