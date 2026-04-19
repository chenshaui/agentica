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


_FENCE_FRONTMATTER_RE = re.compile(
    r"\A\s*```(?:ya?ml)?\s*\n(?P<yaml>.*?)\n```\s*\n?",
    re.DOTALL,
)
_FRONTMATTER_DASHES_RE = re.compile(r"^---\s*$", re.MULTILINE)


def _normalize_skill_md(text: str) -> str:
    """Coerce common LLM-malformed SKILL.md headers into ``--- ... ---``.

    The parser strictly requires the file to start with ``---\\n``. LLMs
    routinely violate this in a few ways:

    1. ``\u0060\u0060\u0060yaml ... \u0060\u0060\u0060`` markdown code fence around the frontmatter.
    2. ``\u0060\u0060\u0060yaml`` opening fence but ``---`` closing (mixed form).
    3. Stray characters ahead of the real ``---`` line (e.g. a single ``-``
       leaked because the model misread "first character must be '-'").
    4. Missing the opening ``---`` line entirely (frontmatter starts with
       ``name:``).
    5. Opening ``---`` present but closing ``---`` missing (frontmatter
       ends at the first markdown heading or blank-then-heading).

    Strategy: find the *first* line that is exactly ``---`` and the *next*
    line that is exactly ``---``. Everything between them is the YAML body;
    everything after the second ``---`` is the markdown body. Anything
    before the first ``---`` is preamble noise and is dropped. If we cannot
    find two ``---`` lines, fall back to the older variant-specific path
    so canonical-but-fenced inputs still get rewritten.
    """
    text = text.lstrip("\ufeff").lstrip()

    # Drop stray noise lines before the first proper `---`. LLMs sometimes
    # emit a lone `-` at the top because they misread "first char must be -".
    lines = text.split("\n")
    while lines and lines[0].strip() in ("-", "--"):
        lines = lines[1:]
    text = "\n".join(lines).lstrip()

    matches = list(_FRONTMATTER_DASHES_RE.finditer(text))
    if len(matches) >= 2:
        first, second = matches[0], matches[1]
        yaml_body = text[first.end():second.start()].strip("\n")
        rest = text[second.end():].lstrip("\n")
        return f"---\n{yaml_body}\n---\n{rest}"

    # Variant: ```yaml opening fence + ``` closing fence, no `---` anywhere.
    m = _FENCE_FRONTMATTER_RE.match(text)
    if m:
        yaml_body = m.group("yaml").strip()
        rest = text[m.end():].lstrip()
        return f"---\n{yaml_body}\n---\n{rest}"

    # Variant: ```yaml opening fence + `---` closing line (mixed form).
    fence_prefix = None
    for prefix in ("```yaml", "```YAML", "```yml"):
        if text.startswith(prefix):
            fence_prefix = prefix
            break
    if fence_prefix and matches:
        first = matches[0]
        # Body between the line right after the opening fence and the first ---.
        first_nl = text.find("\n")
        yaml_body = text[first_nl + 1:first.start()].strip("\n")
        rest = text[first.end():].lstrip("\n")
        return f"---\n{yaml_body}\n---\n{rest}"

    # Variant: bare YAML keys at the top, single closing `---`.
    if text.startswith("name:"):
        end = text.find("\n---")
        if end != -1:
            yaml_body = text[:end].strip()
            rest = text[end + len("\n---"):].lstrip()
            return f"---\n{yaml_body}\n---\n{rest}"

    # Variant: opening `---` present, closing `---` missing. Frontmatter is
    # everything from the first `---` up to (but not including) the first
    # markdown heading line. LLMs love this when forced into a strict format.
    #
    # The body-start signal must avoid catching YAML inline comments, which
    # are also `#`-prefixed. We require either:
    #   (a) ``##``+ heading (level >= 2), since YAML keys never start with
    #       ``##``, OR
    #   (b) a level-1 `#` heading that is preceded by a blank line — YAML
    #       frontmatter never has blank lines between keys, but markdown
    #       bodies almost always start after one.
    if len(matches) == 1:
        first = matches[0]
        body_start_re = re.compile(
            r"(?:\n\s*\n|\A)(#{2,6}\s)|"   # any heading after blank line, or
            r"(?:\n\s*\n)(#\s)",            # # heading after blank line
            re.MULTILINE,
        )
        body_match = body_start_re.search(text, first.end())
        if body_match:
            heading_start = body_match.start(1) if body_match.group(1) is not None else body_match.start(2)
            yaml_body = text[first.end():heading_start].strip("\n")
            rest = text[heading_start:]
            return f"---\n{yaml_body}\n---\n{rest}"

    return text


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
        "You are deciding whether ONE of the experience cards below should "
        "be upgraded into a reusable SKILL.md file.\n\n"
        "A SKILL.md is a 'don't step on this landmine again' note, NOT a "
        "'how to do X' tutorial. Every user-correction card with "
        "repeat_count >= 3 is a rule the user reinforced multiple times — "
        "strongly prefer install_shadow for the highest-repeat correction "
        "card unless it is genuinely a one-off preference. Tool-error cards "
        "alone are NOT skills, but a matching correction card next to them IS.\n\n"
        "Decision recipe:\n"
        "1. Pick the candidate with the highest repeat_count whose type is "
        "'correction'.\n"
        "2. If its repeat_count >= 3, return action=install_shadow.\n"
        "3. Skip only if (a) only tool_error candidates are high-repeat, "
        "(b) an existing generated skill already covers this rule, or "
        "(c) the rule is a one-off preference with no procedural content.\n\n"
        "Return JSON only:\n"
        '{"action": "ignore|install_shadow", '
        '"skill_name": "kebab-case-slug", '
        '"source_experience": "title of the source experience card", '
        '"reason": "why this deserves to be a skill", '
        '"skill_md": "full SKILL.md content (see format below)"}\n\n'
        "## skill_md format (gotcha-first, NOT textbook)\n\n"
        "The skill_md string MUST NOT be wrapped in ```yaml or any other "
        "code fence. It MUST start with '-' (the opening '---').\n\n"
        "FRONTMATTER (minimal, exactly 3 keys):\n"
        "---\n"
        "name: <kebab-case slug, equals skill_name above>\n"
        "description: <one sentence, ≤25 words>\n"
        "when-to-use: <comma-separated keywords for discovery>\n"
        "---\n\n"
        "BODY STRUCTURE (strict, in this order):\n"
        "1. One-line summary (≤30 words).\n"
        "2. ## Gotchas (REQUIRED, MUST have ≥2 items).\n"
        "   - Each gotcha = one observed failure + the fix.\n"
        "   - Format: '⚠️ <symptom>: <root cause>. <minimal fix>'\n"
        "   - Every gotcha MUST be traceable to evidence in the cards / "
        "raw events shown above. Do NOT invent gotchas.\n"
        "3. ## Minimal Example (≤10 lines, real params, no '# TODO' "
        "placeholders, no '<your_value_here>').\n"
        "4. ## Source (auto-filled by the system, leave a blank section).\n\n"
        "FORBIDDEN (will be auto-rejected):\n"
        "- Sections named 'Overview' / 'When To Use' / 'Workflow' / "
        "'Failure Recovery' (these are textbook fluff).\n"
        "- Generic steps the agent could derive from reading docs.\n"
        "- Unverified claims (every gotcha must trace to a real event).\n"
        "- Placeholder code: '# TODO', '<your_*_here>', 'FIXME', "
        "'pass  # implement'.\n"
        "- Skeleton code blocks with <10 chars per line on average.\n\n"
        "Remember: a skill captures lessons that can ONLY be learned by "
        "actually running the tool and getting burned. If you cannot point "
        "to a concrete failure event for a gotcha, do NOT include it.\n\n"
    )

    _JUDGE_PROMPT = (
        "You are evaluating a shadow-installed generated skill based on its "
        "runtime performance episodes.\n\n"
        "Signals to weigh (the most important first):\n"
        "1. gotchas_hit_count > 0 — strong evidence the skill saved the "
        "agent from documented landmines. Lean toward PROMOTE.\n"
        "2. new_gotchas_seen > 0 — the skill is working but incomplete. "
        "Lean toward REVISE and rewrite the gotchas section to cover them.\n"
        "3. consecutive_failures or low success rate without any "
        "gotchas_hit — the skill might be misleading. Lean toward "
        "ROLLBACK.\n"
        "4. Otherwise, KEEP_SHADOW until more data accumulates.\n\n"
        "Decisions:\n"
        "- keep_shadow: not enough data yet, keep running\n"
        "- promote: skill is performing well, promote to full status\n"
        "- revise: skill idea is good but needs changes — provide revised "
        "SKILL.md following the same gotcha-first format (no Overview / "
        "When To Use / Workflow sections)\n"
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
        event_store: Optional[Any] = None,
        min_success_applications: int = 0,
    ) -> Optional[str]:
        """Judge candidates and generate SKILL.md in one LLM call.

        Args:
            model: LLM model instance with async response() method.
            candidates: List of dicts with title, content, repeat_count, type, tier.
            existing_skills: Names of already-generated skill slugs.
            generated_skills_dir: Directory for generated skills.
            event_store: Optional ExperienceEventStore — when provided, raw
                tool_error / tool_recovery events are appended to the prompt
                so the LLM can ground every gotcha in real evidence.
            min_success_applications: If > 0, candidates must have at least
                this many ``tool_recovery`` events in the event store before
                being eligible. Without recoveries we only know what failed,
                not whether a fix actually worked.

        Returns:
            Skill slug name if installed, None if no upgrade.
        """
        if not candidates:
            return None

        # Optional: pull raw events for the evidence chain + recovery gating.
        # event_store.read_all() is a plain file read; if it fails that is an
        # actual I/O / permission bug the caller must see, not a silent
        # degradation — let the exception propagate.
        all_events: List[Dict[str, Any]] = []
        if event_store is not None:
            all_events = await event_store.read_all()

        # Recovery gate: require ≥ N tool_recovery events overall before
        # spawning anything. "Failed N times" alone is not evidence the
        # rule actually works — we want at least one observed save.
        if min_success_applications > 0 and event_store is not None:
            recoveries = sum(
                1 for e in all_events
                if e.get("event_type") == "tool_recovery"
            )
            if recoveries < min_success_applications:
                logger.debug(
                    f"Skill spawn: only {recoveries} tool_recovery events "
                    f"(need {min_success_applications}); deferring."
                )
                return None

        # Build context for LLM
        cards_text = "\n\n".join(
            f"### {c['title']} (repeat: {c.get('repeat_count', 1)}, "
            f"type: {c.get('type', 'unknown')})\n{c.get('content', '')}"
            for c in candidates
        )
        existing_text = ", ".join(existing_skills) if existing_skills else "(none)"

        # Evidence chain: attach the most recent raw tool_error / tool_recovery
        # events per candidate. The LLM is forbidden from inventing gotchas;
        # this is what it cites.
        evidence_text = self._build_evidence_text(candidates, all_events)

        prompt = (
            self._SPAWN_PROMPT
            + f"Existing generated skills: {existing_text}\n\n"
            + f"Experience cards to evaluate:\n{cards_text}\n"
        )
        if evidence_text:
            prompt += (
                "\nRaw event evidence (use these and only these to write "
                "gotchas — quote the symptom verbatim):\n" + evidence_text + "\n"
            )

        response = await model.response([
            Message(role="user", content=prompt),
        ])
        if not response or not response.content:
            logger.debug("Skill spawn: empty LLM response")
            return None

        text = _strip_code_fences(response.content)
        try:
            result = json.loads(text)
        except json.JSONDecodeError:
            logger.debug(
                f"Skill spawn: LLM returned invalid JSON. "
                f"Raw response (first 300 chars): {response.content[:300]}"
            )
            return None

        if not isinstance(result, dict):
            logger.debug(f"Skill spawn: LLM JSON is not a dict: {type(result).__name__}")
            return None

        action = result.get("action")
        if action != "install_shadow":
            logger.debug(
                f"Skill spawn: LLM decided action={action!r} "
                f"(reason={result.get('reason', 'n/a')!r}, "
                f"{len(candidates)} candidate(s) offered)"
            )
            return None

        skill_name = result.get("skill_name", "")
        skill_md = result.get("skill_md", "")
        source = result.get("source_experience", "")

        if not skill_name or not skill_md:
            logger.debug(
                f"Skill spawn: LLM returned install_shadow but missing fields "
                f"(skill_name={bool(skill_name)}, skill_md={bool(skill_md)})"
            )
            return None

        # Sanitize slug
        slug = re.sub(r"[^\w\-]", "-", skill_name.lower())[:50].strip("-")
        if not slug:
            return None

        # Skip if already exists
        if slug in existing_skills:
            logger.debug(f"Skill spawn: slug {slug!r} already exists, skipping")
            return None

        # LLMs often wrap frontmatter in ```yaml fences which the parser rejects;
        # normalize to canonical `---` form before persisting.
        skill_md = _normalize_skill_md(skill_md)

        # Append the auto-managed Source section so it always reflects truth
        # (LLM is told to leave it blank). Counts let humans audit.
        skill_md = self._append_source_section(
            skill_md,
            source=source,
            event_count=sum(
                1 for e in all_events
                if e.get("event_type") in ("tool_error", "tool_recovery")
            ),
        )

        # "No Execution, No Memory" gate: refuse skeletons / placeholders /
        # missing gotchas. LLMs love to hedge with textbook fluff; we don't
        # install those.
        is_valid, reason = self._validate_skill_content(skill_md)
        if not is_valid:
            logger.info(f"Skill spawn rejected by validator: {reason}")
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
            "gotchas_hit_count": 0,
            "new_gotchas_seen": 0,
            "last_judged_at": None,
        }
        self.write_meta(skill_dir / "meta.json", meta)

        # Refresh the L1 keyword index so newly-spawned skills are discoverable
        # without semantic recall. INDEX.md is regenerated from all skill
        # frontmatter on every spawn.
        self.rebuild_index(generated_skills_dir)

        logger.info(f"Installed shadow skill: {slug} from experience '{source}'")
        return slug

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
            f"skill_followed={e.get('skill_followed', True)} "
            f"hit={e.get('skill_gotchas_hit', [])} "
            f"new={e.get('new_gotchas_found', [])} "
            f"{e.get('query', '')[:100]}"
            for e in episodes
        )

        prompt = (
            self._JUDGE_PROMPT
            + f"Skill: {meta.get('skill_name', '?')}\n"
            + f"Status: {meta.get('status', '?')}\n"
            + f"Total episodes: {total}\n"
            + f"Success rate: {meta.get('success_count', 0)}/{total}\n"
            + f"Consecutive failures: {consecutive_failures}\n"
            + f"Gotchas the skill caught (cumulative): "
            + f"{meta.get('gotchas_hit_count', 0)}\n"
            + f"New gotchas not yet covered (cumulative): "
            + f"{meta.get('new_gotchas_seen', 0)}\n\n"
            + f"Recent episodes:\n{episodes_text}\n\n"
            + f"SKILL.md content:\n{skill_content}\n"
        )

        response = await model.response([
            Message(role="user", content=prompt),
        ])
        if not response or not response.content:
            return None

        text = _strip_code_fences(response.content)
        try:
            result = json.loads(text)
        except json.JSONDecodeError:
            logger.debug("Skill judge: LLM returned invalid JSON")
            return None
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
                revised_md = _normalize_skill_md(revised_md)
                revised_md = self._append_source_section(
                    revised_md,
                    source=meta.get("source_experience", ""),
                    event_count=meta.get("gotchas_hit_count", 0)
                    + meta.get("new_gotchas_seen", 0),
                )
                is_valid, reason = self._validate_skill_content(revised_md)
                if not is_valid:
                    # Validator rejected the revision. Don't write garbage
                    # over a working skill — fall back to keep_shadow so
                    # the next checkpoint can try again.
                    logger.info(
                        f"Skill {meta.get('skill_name')}: revision rejected "
                        f"by validator ({reason}); keeping current version"
                    )
                    decision = "keep_shadow"
                else:
                    await async_write_text(skill_md_path, revised_md)
                    meta["version"] = meta.get("version", 1) + 1

        self.write_meta(meta_path, meta)
        logger.info(f"Skill {meta.get('skill_name')}: judge decision = {decision}")
        return decision

    # ── Deterministic helpers (no LLM) ────────────────────────────────────

    @staticmethod
    def record_episode(
        episodes_path: Path,
        outcome: str,
        query: str = "",
        tool_errors: int = 0,
        user_corrected: bool = False,
        skill_followed: bool = True,
        skill_gotchas_hit: Optional[List[str]] = None,
        new_gotchas_found: Optional[List[str]] = None,
    ) -> None:
        """Append a runtime episode to episodes.jsonl.

        Args:
            episodes_path: Path to episodes.jsonl file.
            outcome: "success" or "failure".
            query: User query that triggered this run.
            tool_errors: Number of tool errors in this run.
            user_corrected: Whether user corrected the agent.
            skill_followed: Whether the agent actually followed the skill's
                guidance (False = shadow skill was loaded but ignored).
            skill_gotchas_hit: Gotchas (by symptom) the agent encountered
                that the skill explicitly warned about — strong evidence
                the skill is paying its keep, drives the ``promote`` signal.
            new_gotchas_found: New failure modes that appeared during this
                run but the skill does not yet cover — drives the ``revise``
                signal in the checkpoint judge.
        """
        episode = {
            "date": date.today().isoformat(),
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "outcome": outcome,
            "query": query[:200],
            "tool_errors": tool_errors,
            "user_corrected": user_corrected,
            "skill_followed": skill_followed,
            "skill_gotchas_hit": list(skill_gotchas_hit or []),
            "new_gotchas_found": list(new_gotchas_found or []),
        }
        episodes_path.parent.mkdir(parents=True, exist_ok=True)
        line = json.dumps(episode, ensure_ascii=False) + "\n"
        with episodes_path.open("a", encoding="utf-8") as f:
            f.write(line)

    @staticmethod
    def update_meta_after_episode(
        meta_path: Path,
        outcome: str,
        skill_gotchas_hit: Optional[List[str]] = None,
        new_gotchas_found: Optional[List[str]] = None,
    ) -> Dict:
        """Update meta.json counters after an episode.

        Args:
            meta_path: Path to meta.json.
            outcome: "success" or "failure".
            skill_gotchas_hit: Gotchas the agent ran into that the skill
                already warned about. Each one bumps ``gotchas_hit_count``,
                which the checkpoint judge reads to decide promote.
            new_gotchas_found: Gotchas not yet covered by the skill. Bumps
                ``new_gotchas_seen``, which drives the revise signal.

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

        if skill_gotchas_hit:
            meta["gotchas_hit_count"] = (
                meta.get("gotchas_hit_count", 0) + len(skill_gotchas_hit)
            )
        if new_gotchas_found:
            meta["new_gotchas_seen"] = (
                meta.get("new_gotchas_seen", 0) + len(new_gotchas_found)
            )

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
            tool_name = extract_frontmatter_value(raw, "tool") or ""

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
                "tool": tool_name,
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

    # Patterns the validator rejects in generated SKILL.md.
    _PLACEHOLDER_RE = re.compile(
        r"#\s*TODO\b|#\s*FIXME\b|<your[_ ][^>]*>|<placeholder>|"
        r"\bpass\s*#\s*implement\b",
        re.IGNORECASE,
    )
    _CODE_BLOCK_RE = re.compile(r"```[\w-]*\n(.*?)\n```", re.DOTALL)
    _GOTCHA_RE = re.compile(r"⚠️|##\s*Gotchas|##\s*\u907f\u5751|##\s*\u5751\u70b9")
    _FORBIDDEN_HEADINGS_RE = re.compile(
        r"^#{1,3}\s*(Overview|When To Use|Workflow|Failure Recovery)\s*$",
        re.MULTILINE | re.IGNORECASE,
    )

    @classmethod
    def _validate_skill_content(cls, skill_md: str) -> tuple:
        """Apply the No-Execution-No-Memory rules to a generated skill body.

        Returns ``(is_valid, reason)``. Callers reject the skill on False.

        Rules:
        1. Body must contain a Gotchas section (⚠️ marker or heading).
        2. Body must NOT contain placeholder markers (TODO / <your_x_here>).
        3. Body must NOT contain the textbook headings the gotcha-first
           prompt explicitly forbids.
        4. Code blocks must look like real examples — average line length
           ≥ 10 chars (rejects skeletons like ``def foo():\\n    pass``).
        """
        if not skill_md or not skill_md.strip():
            return False, "empty skill content"

        # Strip frontmatter for body checks (forbidden headings live in body).
        body = skill_md
        if body.startswith("---"):
            end = body.find("\n---", 3)
            if end != -1:
                body = body[end + len("\n---"):]

        if not cls._GOTCHA_RE.search(body):
            return False, "missing gotchas section (no ⚠️ markers or heading found)"

        m = cls._PLACEHOLDER_RE.search(body)
        if m:
            return False, f"contains placeholder/TODO marker: {m.group(0)!r}"

        m = cls._FORBIDDEN_HEADINGS_RE.search(body)
        if m:
            return False, (
                f"contains forbidden textbook heading {m.group(1)!r} "
                "(skill must be gotcha-first, not tutorial-style)"
            )

        for block in cls._CODE_BLOCK_RE.findall(body):
            non_empty = [ln for ln in block.split("\n") if ln.strip()]
            if not non_empty:
                continue
            avg_len = sum(len(ln) for ln in non_empty) / len(non_empty)
            if avg_len < 10:
                return False, (
                    f"code block looks like a skeleton "
                    f"(avg {avg_len:.1f} chars/line < 10)"
                )

        return True, ""

    @staticmethod
    def _build_evidence_text(
        candidates: List[Dict],
        all_events: List[Dict[str, Any]],
        per_candidate_limit: int = 5,
    ) -> str:
        """Render the per-candidate raw-event evidence block for the LLM.

        Matching strategy is type-aware so the LLM cannot be fed cross-wired
        events:

        - ``tool_error`` / ``success_pattern`` candidates: match events
          whose ``tool`` field equals the candidate's ``tool`` (strict
          equality — no substring fallback, which previously produced
          false matches like ``"read"`` aliasing ``"read_file"``).
        - ``correction`` candidates: surface the most recent
          ``user_message`` events verbatim so the LLM can ground the rule
          wording in real user utterances. Correction cards have no
          ``tool`` field, so we do not try to match tool_error events for
          them.

        Each error message is truncated so the prompt does not balloon when
        the workspace has thousands of events.
        """
        if not all_events:
            return ""

        sections: List[str] = []
        for c in candidates:
            ctype = c.get("type", "")
            title = c.get("title", "")
            tool = (c.get("tool") or "").strip()

            matches: List[Dict[str, Any]] = []
            if ctype == "correction":
                for e in all_events:
                    if e.get("event_type") == "user_message":
                        matches.append(e)
            elif tool:
                for e in all_events:
                    if e.get("event_type") not in ("tool_error", "tool_recovery"):
                        continue
                    if str(e.get("tool", "")) == tool:
                        matches.append(e)
            if not matches:
                continue

            recent = matches[-per_candidate_limit:]
            lines = [f"### {title}"]
            for e in recent:
                etype = e.get("event_type", "?")
                if etype == "user_message":
                    user = str(e.get("user_message", ""))[:200]
                    prev = str(e.get("previous_assistant", ""))[:120]
                    lines.append(f"- [user_message] user={user!r} after_assistant={prev!r}")
                else:
                    err = str(e.get("error", "") or e.get("note", ""))[:200]
                    lines.append(f"- [{etype}] {err}")
            sections.append("\n".join(lines))

        return "\n\n".join(sections)

    @staticmethod
    def _append_source_section(
        skill_md: str, source: str, event_count: int,
    ) -> str:
        """Replace / append the trailing ``## Source`` section.

        The prompt instructs the LLM to leave Source blank — we fill it in
        deterministically so audit info (origin card + raw event count) is
        always accurate.
        """
        block = (
            "\n\n## Source\n"
            f"- generated from experience card: `{source or 'unknown'}`\n"
            f"- raw events cited: {event_count}\n"
            f"- generated_at: {date.today().isoformat()}\n"
        )
        # Drop any existing Source section the LLM emitted to avoid dupes.
        cleaned = re.sub(
            r"\n##\s*Source\b.*\Z", "", skill_md.rstrip(), flags=re.DOTALL,
        )
        return cleaned + block

    # L1 INDEX.md format. Tiny header-only file scanned by humans / agents
    # who need to discover skills by keyword without semantic search cost.
    _INDEX_HEADER = (
        "# Generated Skills Index (L1)\n\n"
        "Auto-generated from skill frontmatter. Do not edit by hand.\n"
        "One row per active skill: `keywords -> name (description)`.\n\n"
    )

    @classmethod
    def rebuild_index(cls, generated_skills_dir: Path) -> Optional[Path]:
        """Regenerate ``INDEX.md`` listing every active generated skill.

        Returns the path to INDEX.md, or None if no skills exist. Skipped
        skills: ``rolled_back`` status or ``SKILL.md.disabled`` files.
        """
        if not generated_skills_dir.exists():
            return None

        rows: List[str] = []
        for skill_dir in sorted(generated_skills_dir.iterdir()):
            if not skill_dir.is_dir():
                continue
            md_path = skill_dir / "SKILL.md"
            if not md_path.exists():
                continue
            meta = cls.read_meta(skill_dir / "meta.json")
            if meta.get("status") == "rolled_back":
                continue
            try:
                raw = md_path.read_text(encoding="utf-8")
            except OSError:
                continue
            name = extract_frontmatter_value(raw, "name") or skill_dir.name
            desc = extract_frontmatter_value(raw, "description") or ""
            keywords = extract_frontmatter_value(raw, "when-to-use") or ""
            rows.append(f"- `{keywords}` -> **{name}** — {desc}")

        if not rows:
            return None

        index_path = generated_skills_dir / "INDEX.md"
        index_path.write_text(
            cls._INDEX_HEADER + "\n".join(rows) + "\n",
            encoding="utf-8",
        )
        return index_path

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
