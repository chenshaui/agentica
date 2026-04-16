# -*- coding: utf-8 -*-
"""
@author:XuMing(xuming624@qq.com)
@description: 
Experience → Skill automatic upgrade pipeline.

Two LLM touchpoints:
1. maybe_spawn_skill(): Judge candidates + generate SKILL.md in one call
2. maybe_update_skill_state(): At checkpoint, judge keep/promote/revise/rollback

All runtime evidence (episodes) is recorded deterministically — no LLM.
LLM is only invoked for semantic judgment at spawn time and at checkpoints.
"""
import json
import re
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from agentica.utils.log import logger
from agentica.model.message import Message
from agentica.utils.async_file import (
    async_read_text,
    async_write_text,
    extract_frontmatter_value,
    extract_frontmatter_int,
)


class SkillEvolutionManager:
    """Manages experience → skill upgrade lifecycle.

    Two LLM touchpoints, one deterministic evidence layer:
    - maybe_spawn_skill(): one LLM call to judge + generate SKILL.md
    - maybe_update_skill_state(): one LLM call at checkpoint to judge state
    - record_episode(): deterministic append to episodes.jsonl

    Usage::

        manager = SkillEvolutionManager()
        skill_name = await manager.maybe_spawn_skill(
            model=agent.auxiliary_model,
            candidates=candidates,
            existing_skills=["slug-a"],
            generated_skills_dir=gen_dir,
        )
    """

    # ── LLM Prompts ──────────────────────────────────────────────────────

    _SPAWN_PROMPT = (
        "You are deciding whether a set of high-value experience cards should "
        "be upgraded into a reusable SKILL.md file.\n\n"
        "A skill is a procedural, reusable strategy — NOT a one-line preference "
        "or a single fact. It must have clear steps, applicability conditions, "
        "and failure recovery.\n\n"
        "Only create a skill if the experience is:\n"
        "- Procedural (has steps or decision logic, not just a single rule)\n"
        "- Cross-session (useful beyond a single task)\n"
        "- Repeated enough to be reliable (high repeat_count)\n"
        "- Not already covered by an existing generated skill\n\n"
        "If none of the candidates qualify, return action=ignore.\n\n"
        "Return JSON only:\n"
        '{"action": "ignore|install_shadow", '
        '"skill_name": "kebab-case-slug", '
        '"source_experience": "title of the source experience card", '
        '"reason": "why this deserves to be a skill", '
        '"skill_md": "full SKILL.md content including ---frontmatter---"}\n\n'
        "The skill_md MUST include YAML frontmatter with at least:\n"
        "  name, description, when-to-use\n"
        "And a body with sections: Overview, When To Use, Workflow, "
        "Failure Recovery.\n\n"
    )

    _JUDGE_PROMPT = (
        "You are evaluating a shadow-installed generated skill based on its "
        "runtime performance episodes.\n\n"
        "Decide what to do next:\n"
        "- keep_shadow: not enough data yet, keep running\n"
        "- promote: skill is performing well, promote to full status\n"
        "- revise: skill idea is good but needs changes, provide revised SKILL.md\n"
        "- rollback: skill is causing problems, disable it\n\n"
        "Return JSON only:\n"
        '{"decision": "keep_shadow|promote|revise|rollback", '
        '"reason": "...", '
        '"revised_skill_md": "..." (only if decision is revise, otherwise null)}\n\n'
    )

    # ── Public API ────────────────────────────────────────────────────────

    async def maybe_spawn_skill(
        self,
        model: Any,
        candidates: List[Dict],
        existing_skills: List[str],
        generated_skills_dir: Path,
    ) -> Optional[str]:
        """Judge candidates and generate SKILL.md in one LLM call.

        Args:
            model: LLM model instance with async response() method.
            candidates: List of dicts with title, content, repeat_count, type, tier.
            existing_skills: Names of already-generated skill slugs.
            generated_skills_dir: Directory for generated skills.

        Returns:
            Skill slug name if installed, None if no upgrade.
        """
        if not candidates:
            return None

        # Build context for LLM
        cards_text = "\n\n".join(
            f"### {c['title']} (repeat: {c.get('repeat_count', 1)}, "
            f"type: {c.get('type', 'unknown')})\n{c.get('content', '')}"
            for c in candidates
        )
        existing_text = ", ".join(existing_skills) if existing_skills else "(none)"

        prompt = (
            self._SPAWN_PROMPT
            + f"Existing generated skills: {existing_text}\n\n"
            + f"Experience cards to evaluate:\n{cards_text}\n"
        )

        try:
            response = await model.response([
                Message(role="user", content=prompt),
            ])
            if not response or not response.content:
                return None

            text = _strip_code_fences(response.content)
            result = json.loads(text)
            if not isinstance(result, dict):
                return None

            if result.get("action") != "install_shadow":
                return None

            skill_name = result.get("skill_name", "")
            skill_md = result.get("skill_md", "")
            source = result.get("source_experience", "")

            if not skill_name or not skill_md:
                return None

            # Sanitize slug
            slug = re.sub(r"[^\w\-]", "-", skill_name.lower())[:50].strip("-")
            if not slug:
                return None

            # Skip if already exists
            if slug in existing_skills:
                return None

            # Install
            skill_dir = generated_skills_dir / slug
            skill_dir.mkdir(parents=True, exist_ok=True)
            await async_write_text(skill_dir / "SKILL.md", skill_md)

            meta = {
                "skill_name": slug,
                "status": "shadow",
                "source_experience": source,
                "generated_at": date.today().isoformat(),
                "version": 1,
                "total_episodes": 0,
                "success_count": 0,
                "failure_count": 0,
                "consecutive_failures": 0,
                "last_judged_at": None,
            }
            self.write_meta(skill_dir / "meta.json", meta)

            logger.info(f"Installed shadow skill: {slug} from experience '{source}'")
            return slug

        except json.JSONDecodeError:
            logger.debug("Skill spawn: LLM returned invalid JSON")
            return None
        except Exception as e:
            logger.debug(f"Skill spawn failed: {e}")
            return None

    async def maybe_update_skill_state(
        self,
        model: Any,
        skill_dir: Path,
        checkpoint_interval: int = 5,
        rollback_consecutive_failures: int = 2,
    ) -> Optional[str]:
        """Judge skill state from accumulated episodes at checkpoint.

        Only runs when total_episodes is a multiple of checkpoint_interval,
        or when consecutive_failures >= rollback_consecutive_failures.

        Args:
            model: LLM model instance.
            skill_dir: Path to generated_skills/{slug}/.
            checkpoint_interval: Run judgment every N episodes.
            rollback_consecutive_failures: Auto-rollback threshold.

        Returns:
            Decision string, or None if not at checkpoint.
        """
        meta_path = skill_dir / "meta.json"
        episodes_path = skill_dir / "episodes.jsonl"
        skill_md_path = skill_dir / "SKILL.md"

        meta = self.read_meta(meta_path)
        if not meta or meta.get("status") == "rolled_back":
            return None

        total = meta.get("total_episodes", 0)
        consecutive_failures = meta.get("consecutive_failures", 0)

        # Auto-rollback on consecutive failures (deterministic, no LLM)
        if consecutive_failures >= rollback_consecutive_failures:
            meta["status"] = "rolled_back"
            self.write_meta(meta_path, meta)
            self._disable_skill_md(skill_dir)
            logger.info(
                f"Auto-rolled back skill {meta.get('skill_name')} "
                f"after {consecutive_failures} consecutive failures"
            )
            return "rollback"

        # Only run LLM judgment at checkpoint intervals
        if total < checkpoint_interval or total % checkpoint_interval != 0:
            return None

        # Read recent episodes
        episodes = self._read_recent_episodes(episodes_path, limit=checkpoint_interval)
        if not episodes:
            return None

        # Read SKILL.md content
        skill_content = ""
        if skill_md_path.exists():
            skill_content = (await async_read_text(skill_md_path))[:2000]

        episodes_text = "\n".join(
            "- "
            f"[{e.get('outcome', '?')}] "
            f"tool_errors={e.get('tool_errors', 0)} "
            f"user_corrected={e.get('user_corrected', False)} "
            f"{e.get('query', '')[:100]}"
            for e in episodes
        )

        prompt = (
            self._JUDGE_PROMPT
            + f"Skill: {meta.get('skill_name', '?')}\n"
            + f"Status: {meta.get('status', '?')}\n"
            + f"Total episodes: {total}\n"
            + f"Success rate: {meta.get('success_count', 0)}/{total}\n"
            + f"Consecutive failures: {consecutive_failures}\n\n"
            + f"Recent episodes:\n{episodes_text}\n\n"
            + f"SKILL.md content:\n{skill_content}\n"
        )

        try:
            response = await model.response([
                Message(role="user", content=prompt),
            ])
            if not response or not response.content:
                return None

            text = _strip_code_fences(response.content)
            result = json.loads(text)
            if not isinstance(result, dict):
                return None

            decision = result.get("decision", "keep_shadow")
            meta["last_judged_at"] = date.today().isoformat()

            if decision == "promote":
                meta["status"] = "auto"
            elif decision == "rollback":
                meta["status"] = "rolled_back"
                self._disable_skill_md(skill_dir)
            elif decision == "revise":
                revised_md = result.get("revised_skill_md")
                if revised_md:
                    await async_write_text(skill_md_path, revised_md)
                    meta["version"] = meta.get("version", 1) + 1

            self.write_meta(meta_path, meta)
            logger.info(f"Skill {meta.get('skill_name')}: judge decision = {decision}")
            return decision

        except json.JSONDecodeError:
            logger.debug("Skill judge: LLM returned invalid JSON")
            return None
        except Exception as e:
            logger.debug(f"Skill judge failed: {e}")
            return None

    # ── Deterministic helpers (no LLM) ────────────────────────────────────

    @staticmethod
    def record_episode(
        episodes_path: Path,
        outcome: str,
        query: str = "",
        tool_errors: int = 0,
        user_corrected: bool = False,
    ) -> None:
        """Append a runtime episode to episodes.jsonl.

        Args:
            episodes_path: Path to episodes.jsonl file.
            outcome: "success" or "failure".
            query: User query that triggered this run.
            tool_errors: Number of tool errors in this run.
            user_corrected: Whether user corrected the agent.
        """
        episode = {
            "date": date.today().isoformat(),
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "outcome": outcome,
            "query": query[:200],
            "tool_errors": tool_errors,
            "user_corrected": user_corrected,
        }
        episodes_path.parent.mkdir(parents=True, exist_ok=True)
        line = json.dumps(episode, ensure_ascii=False) + "\n"
        with episodes_path.open("a", encoding="utf-8") as f:
            f.write(line)

    @staticmethod
    def update_meta_after_episode(
        meta_path: Path,
        outcome: str,
    ) -> Dict:
        """Update meta.json counters after an episode.

        Args:
            meta_path: Path to meta.json.
            outcome: "success" or "failure".

        Returns:
            Updated meta dict.
        """
        meta = SkillEvolutionManager.read_meta(meta_path)
        if not meta:
            return {}

        meta["total_episodes"] = meta.get("total_episodes", 0) + 1

        if outcome == "success":
            meta["success_count"] = meta.get("success_count", 0) + 1
            meta["consecutive_failures"] = 0
        elif outcome == "failure":
            meta["failure_count"] = meta.get("failure_count", 0) + 1
            meta["consecutive_failures"] = meta.get("consecutive_failures", 0) + 1

        SkillEvolutionManager.write_meta(meta_path, meta)
        return meta

    @staticmethod
    def read_meta(meta_path: Path) -> Dict:
        """Read meta.json for a generated skill."""
        if not meta_path.exists():
            return {}
        try:
            return json.loads(meta_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return {}

    @staticmethod
    def write_meta(meta_path: Path, meta: Dict) -> None:
        """Write/update meta.json."""
        meta_path.parent.mkdir(parents=True, exist_ok=True)
        meta_path.write_text(
            json.dumps(meta, ensure_ascii=False, indent=2, default=str),
            encoding="utf-8",
        )

    @staticmethod
    def get_candidate_cards(
        exp_dir: Path,
        min_repeat_count: int = 3,
        min_tier: str = "hot",
    ) -> List[Dict]:
        """Scan experience .md files and return cards meeting upgrade threshold.

        Args:
            exp_dir: Experience directory containing .md files.
            min_repeat_count: Minimum repeat_count to qualify.
            min_tier: Minimum tier ("hot" means only hot, "warm" means hot+warm).

        Returns:
            List of dicts with title, content, repeat_count, type, tier.
        """
        if not exp_dir.exists():
            return []

        allowed_tiers = {"hot"} if min_tier == "hot" else {"hot", "warm"}
        candidates = []

        for filepath in exp_dir.glob("*.md"):
            try:
                raw = filepath.read_text(encoding="utf-8").strip()
            except (OSError, UnicodeDecodeError):
                continue

            repeat_count = extract_frontmatter_int(raw, "repeat_count", 1)
            tier = extract_frontmatter_value(raw, "tier") or "hot"
            title = extract_frontmatter_value(raw, "title") or filepath.stem
            exp_type = extract_frontmatter_value(raw, "type") or "unknown"

            if repeat_count < min_repeat_count:
                continue
            if tier not in allowed_tiers:
                continue

            # Strip frontmatter for content
            content = re.sub(r"^---[\s\S]*?---\s*", "", raw, flags=re.MULTILINE).strip()

            candidates.append({
                "title": title,
                "content": content[:500],
                "repeat_count": repeat_count,
                "type": exp_type,
                "tier": tier,
                "filename": filepath.name,
            })

        return candidates

    @staticmethod
    def list_generated_skills(generated_skills_dir: Path) -> List[Dict]:
        """List all generated skills with their status.

        Returns:
            List of dicts with skill_name, status, source_experience, etc.
        """
        if not generated_skills_dir.exists():
            return []

        skills = []
        for skill_dir in sorted(generated_skills_dir.iterdir()):
            if not skill_dir.is_dir():
                continue
            meta_path = skill_dir / "meta.json"
            meta = SkillEvolutionManager.read_meta(meta_path)
            if meta:
                skills.append(meta)
        return skills

    # ── Private ───────────────────────────────────────────────────────────

    @staticmethod
    def _disable_skill_md(skill_dir: Path) -> None:
        """Rename SKILL.md to SKILL.md.disabled so SkillLoader won't discover it."""
        skill_md = skill_dir / "SKILL.md"
        if skill_md.exists():
            skill_md.rename(skill_dir / "SKILL.md.disabled")

    @staticmethod
    def _read_recent_episodes(
        episodes_path: Path,
        limit: int = 10,
    ) -> List[Dict]:
        """Read last N episodes from episodes.jsonl."""
        if not episodes_path.exists():
            return []
        try:
            lines = episodes_path.read_text(encoding="utf-8").strip().splitlines()
        except (OSError, UnicodeDecodeError):
            return []

        episodes = []
        for line in lines[-limit:]:
            line = line.strip()
            if line:
                try:
                    episodes.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
        return episodes


# ── Module-level helpers ─────────────────────────────────────────────────

def _strip_code_fences(text: str) -> str:
    """Strip markdown code fences from LLM response."""
    text = text.strip()
    if text.startswith("```"):
        text = text.split("\n", 1)[-1].rsplit("```", 1)[0].strip()
    return text
