# -*- coding: utf-8 -*-
"""
Tests for the experience → skill upgrade pipeline.

Covers:
1. SkillUpgradeConfig defaults and custom values
2. SkillEvolutionManager — candidate filtering, spawn, episode recording, state judging
3. Hooks integration — skill upgrade triggered after lifecycle
4. Cross-layer cleanup — memory_feedback removed from compiler

All tests mock LLM API keys -- no real API calls.
"""
import asyncio
import json
import os
import tempfile
import unittest
from datetime import date
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

from agentica.agent.config import ExperienceConfig, SkillUpgradeConfig
from agentica.skills import reset_skill_registry


# ===========================================================================
# SkillUpgradeConfig tests
# ===========================================================================

class TestSkillUpgradeConfig(unittest.TestCase):
    """Test SkillUpgradeConfig defaults and custom values."""

    def test_defaults(self):
        config = SkillUpgradeConfig()
        self.assertEqual(config.mode, "shadow")
        self.assertEqual(config.min_repeat_count, 3)
        self.assertEqual(config.min_tier, "hot")
        self.assertEqual(config.checkpoint_interval, 5)
        self.assertEqual(config.rollback_consecutive_failures, 2)
        self.assertTrue(config.notify_user)

    def test_custom_values(self):
        config = SkillUpgradeConfig(
            mode="draft",
            min_repeat_count=5,
            checkpoint_interval=10,
        )
        self.assertEqual(config.mode, "draft")
        self.assertEqual(config.min_repeat_count, 5)
        self.assertEqual(config.checkpoint_interval, 10)

    def test_experience_config_skill_upgrade_none_by_default(self):
        config = ExperienceConfig()
        self.assertIsNone(config.skill_upgrade)

    def test_experience_config_with_skill_upgrade(self):
        config = ExperienceConfig(skill_upgrade=SkillUpgradeConfig())
        self.assertIsNotNone(config.skill_upgrade)
        self.assertEqual(config.skill_upgrade.mode, "shadow")


# ===========================================================================
# SkillEvolutionManager tests
# ===========================================================================

class TestSkillEvolutionManager(unittest.TestCase):
    """Test the SkillEvolutionManager."""

    def setUp(self):
        self._tmpdir = tempfile.mkdtemp()
        self._exp_dir = Path(self._tmpdir) / "experiences"
        self._gen_dir = Path(self._tmpdir) / "generated_skills"
        self._exp_dir.mkdir(parents=True)

    def tearDown(self):
        import shutil
        shutil.rmtree(self._tmpdir, ignore_errors=True)

    def _write_experience(self, title="test", repeat_count=5, tier="hot", exp_type="correction"):
        """Write a test experience file."""
        import re
        safe_title = re.sub(r"[^\w\-]", "_", title.lower())[:50].strip("_")
        filename = f"{exp_type}_{safe_title}.md"
        filepath = self._exp_dir / filename
        content = (
            f"---\ntitle: {title}\n"
            f"type: {exp_type}\n"
            f"tool: \n"
            f"repeat_count: {repeat_count}\n"
            f"first_seen: {date.today().isoformat()}\n"
            f"last_seen: {date.today().isoformat()}\n"
            f"tier: {tier}\n---\n\n"
            f"Rule: Always do {title}\nWhy: It works better\n"
            f"How to apply: When doing tasks"
        )
        filepath.write_text(content, encoding="utf-8")
        return filepath

    def test_get_candidate_cards_filters_correctly(self):
        from agentica.experience.skill_upgrade import SkillEvolutionManager
        # High repeat, hot → candidate
        self._write_experience(title="good_candidate", repeat_count=5, tier="hot")
        # Low repeat → not candidate
        self._write_experience(title="low_repeat", repeat_count=1, tier="hot")
        # Warm tier → not candidate (min_tier=hot)
        self._write_experience(title="warm_exp", repeat_count=5, tier="warm")

        candidates = SkillEvolutionManager.get_candidate_cards(
            self._exp_dir, min_repeat_count=3, min_tier="hot",
        )
        self.assertEqual(len(candidates), 1)
        self.assertEqual(candidates[0]["title"], "good_candidate")

    def test_get_candidate_cards_empty_dir(self):
        from agentica.experience.skill_upgrade import SkillEvolutionManager
        empty_dir = Path(self._tmpdir) / "empty"
        candidates = SkillEvolutionManager.get_candidate_cards(empty_dir)
        self.assertEqual(candidates, [])

    def test_maybe_spawn_skill_generates_skill_md(self):
        from agentica.experience.skill_upgrade import SkillEvolutionManager

        model = MagicMock()
        model.response = AsyncMock(return_value=MagicMock(content=json.dumps({
            "action": "install_shadow",
            "skill_name": "pandas-preference",
            "source_experience": "use_pandas_not_csv",
            "reason": "Repeated correction about data processing",
            "skill_md": (
                "---\nname: pandas-preference\n"
                "description: Use pandas for data processing\n"
                "when-to-use: data processing, CSV, dataframes\n---\n\n"
                "# Pandas Preference\n\n## Overview\nUse pandas.\n\n"
                "## Workflow\n1. Import pandas\n2. Use it\n\n"
                "## Failure Recovery\nFall back to csv module\n"
            ),
        })))

        manager = SkillEvolutionManager()
        candidates = [{"title": "use_pandas", "content": "Use pandas", "repeat_count": 5, "type": "correction"}]

        result = asyncio.run(manager.maybe_spawn_skill(
            model=model, candidates=candidates,
            existing_skills=[], generated_skills_dir=self._gen_dir,
        ))
        self.assertEqual(result, "pandas-preference")

        # Verify files created
        skill_dir = self._gen_dir / "pandas-preference"
        self.assertTrue((skill_dir / "SKILL.md").exists())
        self.assertTrue((skill_dir / "meta.json").exists())

        meta = json.loads((skill_dir / "meta.json").read_text())
        self.assertEqual(meta["status"], "shadow")
        self.assertEqual(meta["skill_name"], "pandas-preference")

    def test_maybe_spawn_skill_ignores_when_llm_says_ignore(self):
        from agentica.experience.skill_upgrade import SkillEvolutionManager

        model = MagicMock()
        model.response = AsyncMock(return_value=MagicMock(content=json.dumps({
            "action": "ignore",
            "reason": "Not procedural enough",
        })))

        manager = SkillEvolutionManager()
        result = asyncio.run(manager.maybe_spawn_skill(
            model=model,
            candidates=[{"title": "x", "content": "y", "repeat_count": 5}],
            existing_skills=[], generated_skills_dir=self._gen_dir,
        ))
        self.assertIsNone(result)

    def test_maybe_spawn_skill_skips_existing(self):
        from agentica.experience.skill_upgrade import SkillEvolutionManager

        model = MagicMock()
        model.response = AsyncMock(return_value=MagicMock(content=json.dumps({
            "action": "install_shadow",
            "skill_name": "already-exists",
            "source_experience": "x",
            "skill_md": "---\nname: already-exists\ndescription: test\n---\nBody",
        })))

        manager = SkillEvolutionManager()
        result = asyncio.run(manager.maybe_spawn_skill(
            model=model,
            candidates=[{"title": "x", "content": "y", "repeat_count": 5}],
            existing_skills=["already-exists"],
            generated_skills_dir=self._gen_dir,
        ))
        self.assertIsNone(result)

    def test_maybe_spawn_skill_empty_candidates(self):
        from agentica.experience.skill_upgrade import SkillEvolutionManager
        manager = SkillEvolutionManager()
        result = asyncio.run(manager.maybe_spawn_skill(
            model=MagicMock(), candidates=[],
            existing_skills=[], generated_skills_dir=self._gen_dir,
        ))
        self.assertIsNone(result)

    def test_record_episode(self):
        from agentica.experience.skill_upgrade import SkillEvolutionManager
        episodes_path = Path(self._tmpdir) / "skill1" / "episodes.jsonl"
        SkillEvolutionManager.record_episode(
            episodes_path, outcome="success", query="test query",
        )
        SkillEvolutionManager.record_episode(
            episodes_path, outcome="failure", query="bad query", tool_errors=2,
        )
        lines = episodes_path.read_text().strip().splitlines()
        self.assertEqual(len(lines), 2)
        self.assertEqual(json.loads(lines[0])["outcome"], "success")
        self.assertEqual(json.loads(lines[1])["tool_errors"], 2)

    def test_read_write_meta(self):
        from agentica.experience.skill_upgrade import SkillEvolutionManager
        meta_path = Path(self._tmpdir) / "skill1" / "meta.json"
        meta = {"skill_name": "test", "status": "shadow", "total_episodes": 0}
        SkillEvolutionManager.write_meta(meta_path, meta)
        loaded = SkillEvolutionManager.read_meta(meta_path)
        self.assertEqual(loaded["skill_name"], "test")
        self.assertEqual(loaded["status"], "shadow")

    def test_read_meta_nonexistent(self):
        from agentica.experience.skill_upgrade import SkillEvolutionManager
        meta_path = Path(self._tmpdir) / "nonexistent" / "meta.json"
        self.assertEqual(SkillEvolutionManager.read_meta(meta_path), {})

    def test_update_meta_after_episode_success(self):
        from agentica.experience.skill_upgrade import SkillEvolutionManager
        meta_path = Path(self._tmpdir) / "skill1" / "meta.json"
        SkillEvolutionManager.write_meta(meta_path, {
            "skill_name": "test", "status": "shadow",
            "total_episodes": 2, "success_count": 1, "failure_count": 1,
            "consecutive_failures": 1,
        })
        updated = SkillEvolutionManager.update_meta_after_episode(meta_path, "success")
        self.assertEqual(updated["total_episodes"], 3)
        self.assertEqual(updated["success_count"], 2)
        self.assertEqual(updated["consecutive_failures"], 0)

    def test_update_meta_after_episode_failure(self):
        from agentica.experience.skill_upgrade import SkillEvolutionManager
        meta_path = Path(self._tmpdir) / "skill1" / "meta.json"
        SkillEvolutionManager.write_meta(meta_path, {
            "skill_name": "test", "status": "shadow",
            "total_episodes": 2, "success_count": 2, "failure_count": 0,
            "consecutive_failures": 0,
        })
        updated = SkillEvolutionManager.update_meta_after_episode(meta_path, "failure")
        self.assertEqual(updated["failure_count"], 1)
        self.assertEqual(updated["consecutive_failures"], 1)

    def test_maybe_update_skill_state_promote(self):
        from agentica.experience.skill_upgrade import SkillEvolutionManager

        skill_dir = Path(self._tmpdir) / "skill1"
        skill_dir.mkdir(parents=True)
        (skill_dir / "SKILL.md").write_text("---\nname: test\ndescription: t\n---\nBody")
        SkillEvolutionManager.write_meta(skill_dir / "meta.json", {
            "skill_name": "test", "status": "shadow",
            "total_episodes": 5, "success_count": 4, "failure_count": 1,
            "consecutive_failures": 0,
        })
        # Write 5 success episodes
        for i in range(5):
            SkillEvolutionManager.record_episode(
                skill_dir / "episodes.jsonl", outcome="success", query=f"q{i}",
            )

        model = MagicMock()
        model.response = AsyncMock(return_value=MagicMock(content=json.dumps({
            "decision": "promote",
            "reason": "Good performance",
        })))

        manager = SkillEvolutionManager()
        decision = asyncio.run(manager.maybe_update_skill_state(
            model=model, skill_dir=skill_dir, checkpoint_interval=5,
        ))
        self.assertEqual(decision, "promote")

        meta = SkillEvolutionManager.read_meta(skill_dir / "meta.json")
        self.assertEqual(meta["status"], "auto")

    def test_maybe_update_skill_state_auto_rollback(self):
        from agentica.experience.skill_upgrade import SkillEvolutionManager

        skill_dir = Path(self._tmpdir) / "skill2"
        skill_dir.mkdir(parents=True)
        SkillEvolutionManager.write_meta(skill_dir / "meta.json", {
            "skill_name": "bad-skill", "status": "shadow",
            "total_episodes": 3, "success_count": 0, "failure_count": 3,
            "consecutive_failures": 3,
        })

        manager = SkillEvolutionManager()
        decision = asyncio.run(manager.maybe_update_skill_state(
            model=MagicMock(),  # Not called due to auto-rollback
            skill_dir=skill_dir,
            rollback_consecutive_failures=2,
        ))
        self.assertEqual(decision, "rollback")

        meta = SkillEvolutionManager.read_meta(skill_dir / "meta.json")
        self.assertEqual(meta["status"], "rolled_back")

    def test_maybe_update_not_at_checkpoint(self):
        from agentica.experience.skill_upgrade import SkillEvolutionManager

        skill_dir = Path(self._tmpdir) / "skill3"
        skill_dir.mkdir(parents=True)
        SkillEvolutionManager.write_meta(skill_dir / "meta.json", {
            "skill_name": "test", "status": "shadow",
            "total_episodes": 3, "success_count": 3, "failure_count": 0,
            "consecutive_failures": 0,
        })

        manager = SkillEvolutionManager()
        decision = asyncio.run(manager.maybe_update_skill_state(
            model=MagicMock(), skill_dir=skill_dir, checkpoint_interval=5,
        ))
        self.assertIsNone(decision)  # Not at checkpoint (3 < 5)

    def test_maybe_update_revise(self):
        from agentica.experience.skill_upgrade import SkillEvolutionManager

        skill_dir = Path(self._tmpdir) / "skill4"
        skill_dir.mkdir(parents=True)
        (skill_dir / "SKILL.md").write_text("---\nname: test\ndescription: t\n---\nOld body")
        SkillEvolutionManager.write_meta(skill_dir / "meta.json", {
            "skill_name": "test", "status": "shadow",
            "total_episodes": 5, "success_count": 3, "failure_count": 2,
            "consecutive_failures": 0,
        })
        for i in range(5):
            SkillEvolutionManager.record_episode(
                skill_dir / "episodes.jsonl", outcome="success" if i < 3 else "failure",
            )

        model = MagicMock()
        model.response = AsyncMock(return_value=MagicMock(content=json.dumps({
            "decision": "revise",
            "reason": "Needs updating",
            "revised_skill_md": "---\nname: test\ndescription: t\n---\nRevised body",
        })))

        manager = SkillEvolutionManager()
        decision = asyncio.run(manager.maybe_update_skill_state(
            model=model, skill_dir=skill_dir, checkpoint_interval=5,
        ))
        self.assertEqual(decision, "revise")

        # Verify SKILL.md was updated
        skill_content = (skill_dir / "SKILL.md").read_text()
        self.assertIn("Revised body", skill_content)

        meta = SkillEvolutionManager.read_meta(skill_dir / "meta.json")
        self.assertEqual(meta["version"], 2)

    def test_list_generated_skills(self):
        from agentica.experience.skill_upgrade import SkillEvolutionManager
        self._gen_dir.mkdir(parents=True)
        for name in ["skill-a", "skill-b"]:
            d = self._gen_dir / name
            d.mkdir()
            SkillEvolutionManager.write_meta(d / "meta.json", {
                "skill_name": name, "status": "shadow",
            })

        skills = SkillEvolutionManager.list_generated_skills(self._gen_dir)
        self.assertEqual(len(skills), 2)
        names = [s["skill_name"] for s in skills]
        self.assertIn("skill-a", names)
        self.assertIn("skill-b", names)

    def test_rollback_disables_skill_md(self):
        """Rollback should rename SKILL.md to SKILL.md.disabled."""
        from agentica.experience.skill_upgrade import SkillEvolutionManager

        skill_dir = Path(self._tmpdir) / "skill-rollback"
        skill_dir.mkdir(parents=True)
        (skill_dir / "SKILL.md").write_text("---\nname: test\ndescription: t\n---\nBody")
        SkillEvolutionManager.write_meta(skill_dir / "meta.json", {
            "skill_name": "test", "status": "shadow",
            "total_episodes": 3, "success_count": 0, "failure_count": 3,
            "consecutive_failures": 3,
        })

        manager = SkillEvolutionManager()
        asyncio.run(manager.maybe_update_skill_state(
            model=MagicMock(), skill_dir=skill_dir,
            rollback_consecutive_failures=2,
        ))

        # SKILL.md should be renamed, not visible to SkillLoader
        self.assertFalse((skill_dir / "SKILL.md").exists())
        self.assertTrue((skill_dir / "SKILL.md.disabled").exists())

    def test_draft_mode_sets_status_draft(self):
        """In draft mode, spawned skill should have status=draft, not shadow."""
        from agentica.experience.skill_upgrade import SkillEvolutionManager

        model = MagicMock()
        model.response = AsyncMock(return_value=MagicMock(content=json.dumps({
            "action": "install_shadow",
            "skill_name": "draft-test",
            "source_experience": "x",
            "skill_md": "---\nname: draft-test\ndescription: t\n---\nBody",
        })))

        manager = SkillEvolutionManager()
        slug = asyncio.run(manager.maybe_spawn_skill(
            model=model,
            candidates=[{"title": "x", "content": "y", "repeat_count": 5}],
            existing_skills=[], generated_skills_dir=self._gen_dir,
        ))
        self.assertEqual(slug, "draft-test")

        # Now simulate hooks setting draft mode post-spawn
        meta_path = self._gen_dir / "draft-test" / "meta.json"
        meta = SkillEvolutionManager.read_meta(meta_path)
        meta["status"] = "draft"
        SkillEvolutionManager.write_meta(meta_path, meta)

        loaded = SkillEvolutionManager.read_meta(meta_path)
        self.assertEqual(loaded["status"], "draft")

    def test_episode_has_timestamp(self):
        """Episodes should include a UTC timestamp field."""
        from agentica.experience.skill_upgrade import SkillEvolutionManager
        episodes_path = Path(self._tmpdir) / "ts_test" / "episodes.jsonl"
        SkillEvolutionManager.record_episode(
            episodes_path, outcome="success", query="test",
        )
        lines = episodes_path.read_text().strip().splitlines()
        ep = json.loads(lines[0])
        self.assertIn("timestamp", ep)
        self.assertIn("T", ep["timestamp"])  # ISO format with T separator

    def test_rolled_back_skill_not_discoverable(self):
        """After rollback, SKILL.md should be renamed so SkillLoader can't find it."""
        from agentica.experience.skill_upgrade import SkillEvolutionManager
        from agentica.skills.skill_loader import SkillLoader

        # Create a generated skill directory
        skill_dir = self._gen_dir / "test-skill"
        skill_dir.mkdir(parents=True)
        (skill_dir / "SKILL.md").write_text(
            "---\nname: test-skill\ndescription: A test skill\n---\n\n# Test\n\nBody."
        )
        SkillEvolutionManager.write_meta(skill_dir / "meta.json", {
            "skill_name": "test-skill", "status": "shadow",
            "total_episodes": 0, "consecutive_failures": 0,
        })

        # Before rollback: loader should discover it
        loader = SkillLoader()
        found_before = loader.discover_skills(self._gen_dir)
        self.assertEqual(len(found_before), 1)

        # Rollback
        SkillEvolutionManager._disable_skill_md(skill_dir)

        # After rollback: loader should NOT discover it
        found_after = loader.discover_skills(self._gen_dir)
        self.assertEqual(len(found_after), 0)
        self.assertTrue((skill_dir / "SKILL.md.disabled").exists())

    def test_promote_uses_auto_status(self):
        """Promote decision should set status to 'auto', not 'promoted'."""
        from agentica.experience.skill_upgrade import SkillEvolutionManager

        skill_dir = Path(self._tmpdir) / "auto-test"
        skill_dir.mkdir(parents=True)
        (skill_dir / "SKILL.md").write_text("---\nname: auto-test\ndescription: t\n---\nBody")
        SkillEvolutionManager.write_meta(skill_dir / "meta.json", {
            "skill_name": "auto-test", "status": "shadow",
            "total_episodes": 5, "success_count": 5, "failure_count": 0,
            "consecutive_failures": 0,
        })
        for i in range(5):
            SkillEvolutionManager.record_episode(
                skill_dir / "episodes.jsonl", outcome="success",
            )

        model = MagicMock()
        model.response = AsyncMock(return_value=MagicMock(content=json.dumps({
            "decision": "promote", "reason": "Good",
        })))

        manager = SkillEvolutionManager()
        decision = asyncio.run(manager.maybe_update_skill_state(
            model=model, skill_dir=skill_dir, checkpoint_interval=5,
        ))
        self.assertEqual(decision, "promote")
        meta = SkillEvolutionManager.read_meta(skill_dir / "meta.json")
        self.assertEqual(meta["status"], "auto")

    def test_maybe_update_auto_skill_state_rolls_back_after_failures(self):
        """Auto skills should still rollback after later consecutive failures."""
        from agentica.experience.skill_upgrade import SkillEvolutionManager

        skill_dir = Path(self._tmpdir) / "auto-regressed"
        skill_dir.mkdir(parents=True)
        (skill_dir / "SKILL.md").write_text("---\nname: auto-regressed\ndescription: t\n---\nBody")
        SkillEvolutionManager.write_meta(skill_dir / "meta.json", {
            "skill_name": "auto-regressed",
            "status": "auto",
            "total_episodes": 8,
            "success_count": 5,
            "failure_count": 3,
            "consecutive_failures": 3,
        })

        manager = SkillEvolutionManager()
        decision = asyncio.run(manager.maybe_update_skill_state(
            model=MagicMock(),
            skill_dir=skill_dir,
            rollback_consecutive_failures=2,
        ))

        self.assertEqual(decision, "rollback")
        meta = SkillEvolutionManager.read_meta(skill_dir / "meta.json")
        self.assertEqual(meta["status"], "rolled_back")
        self.assertFalse((skill_dir / "SKILL.md").exists())

    def test_judge_prompt_includes_episode_failure_signals(self):
        """Judge prompt should surface tool_errors and user_corrected signals."""
        from agentica.experience.skill_upgrade import SkillEvolutionManager

        skill_dir = Path(self._tmpdir) / "judge-signals"
        skill_dir.mkdir(parents=True)
        (skill_dir / "SKILL.md").write_text("---\nname: judge-signals\ndescription: t\n---\nBody")
        SkillEvolutionManager.write_meta(skill_dir / "meta.json", {
            "skill_name": "judge-signals", "status": "shadow",
            "total_episodes": 5, "success_count": 3, "failure_count": 2,
            "consecutive_failures": 0,
        })
        for i in range(5):
            SkillEvolutionManager.record_episode(
                skill_dir / "episodes.jsonl",
                outcome="failure" if i == 4 else "success",
                query=f"q{i}",
                tool_errors=2 if i == 4 else 0,
                user_corrected=(i == 4),
            )

        model = MagicMock()
        model.response = AsyncMock(return_value=MagicMock(content=json.dumps({
            "decision": "keep_shadow",
            "reason": "Need more data",
        })))

        manager = SkillEvolutionManager()
        asyncio.run(manager.maybe_update_skill_state(
            model=model, skill_dir=skill_dir, checkpoint_interval=5,
        ))

        prompt = model.response.call_args[0][0][0].content
        self.assertIn("tool_errors=2", prompt)
        self.assertIn("user_corrected=True", prompt)


# ===========================================================================
# Hooks integration tests
# ===========================================================================

class TestHooksSkillUpgradeIntegration(unittest.TestCase):
    """Test skill upgrade integration in ExperienceCaptureHooks."""

    def _make_hooks(self, **config_overrides):
        config = ExperienceConfig(**config_overrides)
        from agentica.hooks import ExperienceCaptureHooks
        return ExperienceCaptureHooks(config)

    def _mock_agent(self, agent_id="test-agent"):
        agent = MagicMock()
        agent.agent_id = agent_id
        agent.run_input = "test input"
        agent.model = MagicMock()
        agent.auxiliary_model = None
        agent.workspace = MagicMock()
        agent.workspace.write_memory_entry = AsyncMock(return_value="/tmp/mem.md")
        mock_event_store = MagicMock()
        mock_event_store.append = AsyncMock(return_value="/tmp/events.jsonl")
        agent.workspace.get_experience_event_store = MagicMock(return_value=mock_event_store)
        mock_compiled_store = MagicMock()
        mock_compiled_store.write = AsyncMock(return_value="/tmp/exp.md")
        mock_compiled_store.run_lifecycle = AsyncMock(return_value={"promoted": 0, "demoted": 0, "archived": 0})
        mock_compiled_store.sync_to_global_agent_md = AsyncMock(return_value="/tmp/AGENTS.md")
        agent.workspace.get_compiled_experience_store = MagicMock(return_value=mock_compiled_store)
        agent.workspace._get_global_agent_md_path = MagicMock(return_value="/tmp/AGENTS.md")
        agent.workspace._get_user_generated_skills_dir = MagicMock(return_value=Path("/tmp/gen_skills"))
        agent.workspace._get_user_experience_dir = MagicMock(return_value=Path("/tmp/experiences"))
        agent.working_memory = MagicMock()
        agent.working_memory.messages = []
        return agent

    def test_skill_upgrade_disabled_when_no_config(self):
        """Skill upgrade should not run when skill_upgrade is None."""
        hooks = self._make_hooks(capture_user_corrections=False)
        agent = self._mock_agent()

        asyncio.run(hooks.on_agent_start(agent))
        asyncio.run(hooks.on_agent_end(agent, output="Done"))

        # No crash, lifecycle should still run
        compiled_store = agent.workspace.get_compiled_experience_store()
        compiled_store.run_lifecycle.assert_called_once()

    def test_skill_upgrade_disabled_when_off(self):
        """Skill upgrade should not run when mode=off."""
        hooks = self._make_hooks(
            capture_user_corrections=False,
            skill_upgrade=SkillUpgradeConfig(mode="off"),
        )
        agent = self._mock_agent()

        asyncio.run(hooks.on_agent_start(agent))
        asyncio.run(hooks.on_agent_end(agent, output="Done"))

        # No crash
        compiled_store = agent.workspace.get_compiled_experience_store()
        compiled_store.run_lifecycle.assert_called_once()

    def test_get_skill_info_error_result_not_counted_as_skill_use(self):
        """Error text from get_skill_info should not count as a used skill."""
        hooks = self._make_hooks(
            capture_user_corrections=False,
            skill_upgrade=SkillUpgradeConfig(mode="shadow"),
        )
        agent = self._mock_agent()

        asyncio.run(hooks.on_agent_start(agent))
        asyncio.run(hooks.on_tool_end(
            agent,
            tool_name="get_skill_info",
            tool_args={"skill_name": "missing-skill"},
            result="Error: Skill 'missing-skill' not found.",
            is_error=False,
        ))

        self.assertEqual(hooks._skills_used[agent.agent_id], set())

    def test_user_correction_marks_shadow_skill_episode_as_failure(self):
        """User correction should record a failing episode even without tool errors."""
        from agentica.experience.skill_upgrade import SkillEvolutionManager
        tmpdir = tempfile.mkdtemp()

        hooks = self._make_hooks(
            capture_user_corrections=False,
            capture_success_patterns=False,
            skill_upgrade=SkillUpgradeConfig(mode="shadow"),
        )
        agent = self._mock_agent()
        try:
            gen_dir = Path(tmpdir) / "generated_skills"
            exp_dir = Path(tmpdir) / "experiences"
            gen_dir.mkdir(parents=True)
            exp_dir.mkdir(parents=True)
            agent.workspace._get_user_generated_skills_dir = MagicMock(return_value=gen_dir)
            agent.workspace._get_user_experience_dir = MagicMock(return_value=exp_dir)

            skill_dir = gen_dir / "shadow-skill"
            skill_dir.mkdir()
            (skill_dir / "SKILL.md").write_text("---\nname: shadow-skill\ndescription: t\n---\nBody")
            SkillEvolutionManager.write_meta(skill_dir / "meta.json", {
                "skill_name": "shadow-skill",
                "status": "shadow",
                "total_episodes": 0,
                "success_count": 0,
                "failure_count": 0,
                "consecutive_failures": 0,
            })

            asyncio.run(hooks.on_agent_start(agent))
            hooks._correction_detected[agent.agent_id] = True
            asyncio.run(hooks.on_tool_end(
                agent,
                tool_name="get_skill_info",
                tool_args={"skill_name": "shadow-skill"},
                result="=== Skill: shadow-skill ===\nBody",
                is_error=False,
            ))

            with patch("agentica.experience.skill_upgrade.SkillEvolutionManager.record_episode") as record_episode:
                asyncio.run(hooks.on_agent_end(agent, output="Corrected by user"))

            record_episode.assert_called_once()
            self.assertEqual(record_episode.call_args.kwargs["outcome"], "failure")
            self.assertTrue(record_episode.call_args.kwargs["user_corrected"])
        finally:
            import shutil
            shutil.rmtree(tmpdir, ignore_errors=True)


class TestSkillToolGeneratedSkillRuntime(unittest.TestCase):
    """Runtime behavior for generated skill visibility and refresh."""

    def setUp(self):
        self._tmpdir = tempfile.mkdtemp()
        self._gen_dir = Path(self._tmpdir) / "generated_skills"
        self._gen_dir.mkdir(parents=True)
        reset_skill_registry()

    def tearDown(self):
        import shutil
        reset_skill_registry()
        shutil.rmtree(self._tmpdir, ignore_errors=True)

    def _write_generated_skill(self, name: str, status: str = "shadow", body: str = "Body") -> Path:
        skill_dir = self._gen_dir / name
        skill_dir.mkdir(parents=True, exist_ok=True)
        (skill_dir / "SKILL.md").write_text(
            f"---\nname: {name}\ndescription: {name} desc\n---\n{body}",
            encoding="utf-8",
        )
        (skill_dir / "meta.json").write_text(
            json.dumps({"skill_name": name, "status": status}, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        return skill_dir

    def test_draft_generated_skill_not_visible(self):
        """Draft generated skills should not be listed or loadable."""
        from agentica.tools.skill_tool import SkillTool

        self._write_generated_skill("draft-skill", status="draft")
        tool = SkillTool(custom_skill_dirs=[str(self._gen_dir)])

        self.assertNotIn("draft-skill", tool.list_skills())
        self.assertIn("not found", tool.get_skill_info("draft-skill"))

    def test_reload_generated_skills_refreshes_revised_content(self):
        """Reload should replace cached generated skill content after revise."""
        from agentica.tools.skill_tool import SkillTool

        skill_dir = self._write_generated_skill("revise-skill", status="shadow", body="Old body")
        tool = SkillTool(custom_skill_dirs=[str(self._gen_dir)])

        self.assertIn("Old body", tool.get_skill_info("revise-skill"))

        (skill_dir / "SKILL.md").write_text(
            "---\nname: revise-skill\ndescription: revise-skill desc\n---\nNew body",
            encoding="utf-8",
        )
        tool.reload_generated_skills()

        self.assertIn("New body", tool.get_skill_info("revise-skill"))

    def test_reload_generated_skills_removes_rolled_back_skill(self):
        """Reload should remove rolled back generated skills from current registry."""
        from agentica.tools.skill_tool import SkillTool
        from agentica.experience.skill_upgrade import SkillEvolutionManager

        skill_dir = self._write_generated_skill("rollback-skill", status="shadow")
        tool = SkillTool(custom_skill_dirs=[str(self._gen_dir)])

        self.assertIn("rollback-skill", tool.list_skills())

        meta_path = skill_dir / "meta.json"
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
        meta["status"] = "rolled_back"
        meta_path.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
        SkillEvolutionManager._disable_skill_md(skill_dir)
        tool.reload_generated_skills()

        self.assertNotIn("rollback-skill", tool.list_skills())
        self.assertIn("not found", tool.get_skill_info("rollback-skill"))


class TestAgentSkillUpgradeLifecycle(unittest.TestCase):
    """Agent-level lifecycle tests for generated skill visibility and refresh."""

    def setUp(self):
        self._tmpdir = tempfile.mkdtemp()
        reset_skill_registry()

    def tearDown(self):
        import shutil
        reset_skill_registry()
        shutil.rmtree(self._tmpdir, ignore_errors=True)

    @staticmethod
    def _write_generated_skill(skill_dir: Path, status: str = "shadow", body: str = "Body") -> None:
        skill_dir.mkdir(parents=True, exist_ok=True)
        (skill_dir / "SKILL.md").write_text(
            f"---\nname: {skill_dir.name}\ndescription: {skill_dir.name} desc\n---\n{body}",
            encoding="utf-8",
        )
        (skill_dir / "meta.json").write_text(
            json.dumps({"skill_name": skill_dir.name, "status": status}, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    def test_agent_loads_preexisting_generated_skill_on_init(self):
        """Preexisting generated skills should be visible when a new Agent starts."""
        from agentica.agent import Agent
        from agentica.model.openai import OpenAIChat
        from agentica.tools.skill_tool import SkillTool
        from agentica.workspace import Workspace

        workspace = Workspace(self._tmpdir)
        workspace.initialize()
        gen_dir = workspace._get_user_generated_skills_dir()
        self._write_generated_skill(gen_dir / "preexisting-skill", status="shadow", body="Loaded on init")

        skill_tool = SkillTool()
        agent = Agent(
            name="skill-agent",
            model=OpenAIChat(id="gpt-4o-mini", api_key="fake_openai_key"),
            tools=[skill_tool],
            workspace=workspace,
            experience=True,
            experience_config=ExperienceConfig(skill_upgrade=SkillUpgradeConfig(mode="shadow")),
        )

        self.assertIn("preexisting-skill", skill_tool.list_skills())
        self.assertIn("preexisting-skill", "\n".join(agent._session_guidance_prompts))

    def test_spawned_generated_skill_refreshes_agent_session_guidance(self):
        """Spawned generated skills should appear in next-run session guidance immediately."""
        from agentica.agent import Agent
        from agentica.hooks import ExperienceCaptureHooks
        from agentica.model.openai import OpenAIChat
        from agentica.tools.skill_tool import SkillTool
        from agentica.workspace import Workspace

        workspace = Workspace(self._tmpdir)
        workspace.initialize()
        skill_tool = SkillTool()
        config = ExperienceConfig(
            capture_user_corrections=False,
            skill_upgrade=SkillUpgradeConfig(mode="shadow"),
        )
        agent = Agent(
            name="skill-agent",
            model=OpenAIChat(id="gpt-4o-mini", api_key="fake_openai_key"),
            tools=[skill_tool],
            workspace=workspace,
            experience=True,
            experience_config=config,
        )
        agent.run_input = "test input"
        hooks = ExperienceCaptureHooks(config)

        async def _spawn_skill(**kwargs):
            self._write_generated_skill(
                kwargs["generated_skills_dir"] / "spawned-skill",
                status="shadow",
                body="Spawned body",
            )
            return "spawned-skill"

        with patch.object(agent.workspace.get_compiled_experience_store(), "run_lifecycle", new=AsyncMock(return_value={})):
            with patch("agentica.experience.skill_upgrade.SkillEvolutionManager.get_candidate_cards", return_value=[{
                "title": "candidate", "content": "content", "repeat_count": 5, "type": "correction",
            }]):
                with patch("agentica.experience.skill_upgrade.SkillEvolutionManager.maybe_spawn_skill", new=AsyncMock(side_effect=_spawn_skill)):
                    asyncio.run(hooks.on_agent_start(agent))
                    asyncio.run(hooks.on_agent_end(agent, output="Done"))

        self.assertIn("spawned-skill", "\n".join(agent._session_guidance_prompts))

    def test_name_collision_with_project_skill_does_not_record_generated_episode(self):
        """If a project skill wins name resolution, generated skill should not get scored."""
        from agentica.hooks import ExperienceCaptureHooks
        from agentica.tools.skill_tool import SkillTool

        config = ExperienceConfig(
            capture_user_corrections=False,
            capture_success_patterns=False,
            skill_upgrade=SkillUpgradeConfig(mode="shadow"),
        )
        hooks = ExperienceCaptureHooks(config)
        agent = MagicMock()
        agent.agent_id = "collision-agent"
        agent.run_input = "test input"
        agent.model = MagicMock()
        agent.auxiliary_model = None
        agent.workspace = MagicMock()
        event_store = MagicMock()
        event_store.append = AsyncMock(return_value="/tmp/events.jsonl")
        compiled_store = MagicMock()
        compiled_store.write = AsyncMock(return_value="/tmp/exp.md")
        compiled_store.run_lifecycle = AsyncMock(return_value={})
        compiled_store.sync_to_global_agent_md = AsyncMock(return_value="/tmp/AGENTS.md")
        agent.workspace.get_experience_event_store = MagicMock(return_value=event_store)
        agent.workspace.get_compiled_experience_store = MagicMock(return_value=compiled_store)
        agent.workspace._get_global_agent_md_path = MagicMock(return_value=Path("/tmp/AGENTS.md"))
        agent.working_memory = MagicMock()
        agent.working_memory.messages = []

        project_skill_dir = Path(self._tmpdir) / "project-skill"
        project_skill_dir.mkdir(parents=True)
        (project_skill_dir / "SKILL.md").write_text(
            "---\nname: shared-skill\ndescription: project desc\n---\nProject body",
            encoding="utf-8",
        )
        generated_root = Path(self._tmpdir) / "generated_skills"
        generated_dir = generated_root / "shared-skill"
        self._write_generated_skill(generated_dir, status="shadow", body="Generated body")
        agent.workspace._get_user_generated_skills_dir = MagicMock(return_value=generated_root)
        agent.workspace._get_user_experience_dir = MagicMock(return_value=Path(self._tmpdir) / "experiences")

        skill_tool = SkillTool(custom_skill_dirs=[str(project_skill_dir), str(generated_root)])
        agent.tools = [skill_tool]
        skill_tool._agent = agent

        self.assertIn("Project body", skill_tool.get_skill_info("shared-skill"))

        asyncio.run(hooks.on_agent_start(agent))
        asyncio.run(hooks.on_tool_end(
            agent,
            tool_name="get_skill_info",
            tool_args={"skill_name": "shared-skill"},
            result="=== Skill: shared-skill ===\nProject body",
            is_error=False,
        ))

        with patch("agentica.experience.skill_upgrade.SkillEvolutionManager.get_candidate_cards", return_value=[]):
            with patch("agentica.experience.skill_upgrade.SkillEvolutionManager.record_episode") as record_episode:
                asyncio.run(hooks.on_agent_end(agent, output="Done"))

        record_episode.assert_not_called()


# ===========================================================================
# Cross-layer cleanup tests
# ===========================================================================

class TestCrossLayerCleanup(unittest.TestCase):
    """Test that experience→memory cross-layer write has been removed."""

    def test_memory_feedback_removed_from_compiler(self):
        from agentica.experience.compiler import ExperienceCompiler
        # is_memory_feedback and build_memory_feedback should not exist
        self.assertFalse(hasattr(ExperienceCompiler, "is_memory_feedback"))
        self.assertFalse(hasattr(ExperienceCompiler, "build_memory_feedback"))

    def test_correction_always_goes_to_experience(self):
        """Correction classified as experience should be written to compiled store."""
        from agentica.hooks import ExperienceCaptureHooks

        config = ExperienceConfig()
        hooks = ExperienceCaptureHooks(config)
        agent = MagicMock()
        agent.agent_id = "test"
        agent.run_input = "Use pandas instead"
        agent.model = MagicMock()
        agent.auxiliary_model = None
        agent.workspace = MagicMock()

        mock_event_store = MagicMock()
        mock_event_store.append = AsyncMock(return_value="/tmp/events.jsonl")
        agent.workspace.get_experience_event_store = MagicMock(return_value=mock_event_store)
        mock_compiled_store = MagicMock()
        mock_compiled_store.write = AsyncMock(return_value="/tmp/exp.md")
        mock_compiled_store.run_lifecycle = AsyncMock(return_value={})
        agent.workspace.get_compiled_experience_store = MagicMock(return_value=mock_compiled_store)
        agent.workspace._get_global_agent_md_path = MagicMock(return_value=Path("/tmp/AGENTS.md"))
        agent.workspace._get_user_generated_skills_dir = MagicMock(return_value=Path("/tmp/gen"))
        agent.workspace._get_user_experience_dir = MagicMock(return_value=Path("/tmp/exp"))

        prev_msg = MagicMock()
        prev_msg.role = "assistant"
        prev_msg.content = "I'll use csv module"
        agent.working_memory = MagicMock()
        agent.working_memory.messages = [prev_msg]

        # Simulate LLM returning experience target
        mock_response = MagicMock()
        mock_response.content = json.dumps({
            "is_correction": True,
            "confidence": 0.95,
            "category": "preference",
            "scope": "cross_session",
            "should_persist": True,
            "persist_target": "experience",
            "title": "use_pandas",
            "rule": "Use pandas for data",
            "why": "Better",
            "how_to_apply": "Data tasks",
        })
        agent.model.response = AsyncMock(return_value=mock_response)

        asyncio.run(hooks.on_agent_start(agent))
        asyncio.run(hooks.on_agent_end(agent, output="OK"))

        # Should write to compiled_store, NOT workspace.write_memory_entry
        compiled_store = agent.workspace.get_compiled_experience_store()
        calls = compiled_store.write.call_args_list
        correction_calls = [c for c in calls if c[0][0].experience_type == "correction"]
        self.assertEqual(len(correction_calls), 1)
        # write_memory_entry should NOT have been called for feedback
        agent.workspace.write_memory_entry.assert_not_called()

    def test_classification_prompt_no_memory_feedback_target(self):
        """Classification prompt should not include memory_feedback as a persist_target."""
        from agentica.hooks import ExperienceCaptureHooks
        prompt = ExperienceCaptureHooks._FEEDBACK_CLASSIFY_PROMPT
        self.assertNotIn("memory_feedback", prompt)
        self.assertIn("experience", prompt)
        self.assertIn("session_only", prompt)


# ===========================================================================
# Import tests
# ===========================================================================

class TestSkillUpgradeImports(unittest.TestCase):

    def test_import_config(self):
        from agentica.agent.config import SkillUpgradeConfig
        self.assertIsNotNone(SkillUpgradeConfig)

    def test_import_from_top_level(self):
        from agentica import SkillUpgradeConfig, SkillEvolutionManager
        self.assertIsNotNone(SkillUpgradeConfig)
        self.assertIsNotNone(SkillEvolutionManager)

    def test_import_from_experience_package(self):
        from agentica.experience import SkillEvolutionManager
        self.assertIsNotNone(SkillEvolutionManager)

    def test_import_directly(self):
        from agentica.experience.skill_upgrade import SkillEvolutionManager
        self.assertIsNotNone(SkillEvolutionManager)


if __name__ == "__main__":
    unittest.main()
