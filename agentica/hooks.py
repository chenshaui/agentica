# -*- coding: utf-8 -*-
"""
@author:XuMing(xuming624@qq.com)
@description: Lifecycle hooks for Agent runs.

Two levels of hooks:
- AgentHooks: per-agent hooks (on_start, on_end), set on Agent instance
- RunHooks: global run-level hooks (on_agent_start, on_agent_end, on_llm_start,
  on_llm_end, on_tool_start, on_tool_end, on_agent_transfer), passed to run()
- ConversationArchiveHooks: auto-archives conversations to workspace after each run
"""
import json
from typing import Any, Optional, List, Dict

from agentica.experience.skill_upgrade import SkillEvolutionManager
from agentica.model.message import Message
from agentica.tools.skill_tool import SkillTool
from agentica.utils.log import logger

# ─── Shared memory type specification ───────────────────────────────────────
# Used by both BuiltinMemoryTool (LLM-facing system prompt) and
# MemoryExtractHooks (extraction sub-call). Keep in sync by importing
# from this single source.

MEMORY_TYPE_SPEC = (
    "**user** — User's role, goals, preferences, and knowledge.\n"
    "  When to save: when you learn details about the user's role, preferences, "
    "responsibilities, or knowledge.\n"
    "  Example: 'User is a data scientist focused on observability/logging.'\n\n"
    "**feedback** — Guidance on how to approach work: what to avoid AND what "
    "to keep doing.\n"
    "  When to save: any time the user corrects an approach ('don't do X') OR "
    "confirms a non-obvious approach worked ('yes exactly', 'perfect'). "
    "Corrections are easy to notice; confirmations are quieter — watch for them.\n"
    "  Body structure: lead with the rule, then **Why:** (the reason), then "
    "**How to apply:** (when/where this kicks in).\n"
    "  Example: 'Integration tests must use real DB. Why: mock/prod divergence "
    "masked a broken migration. How to apply: tests/integration/.'\n\n"
    "**project** — Information about ongoing work, goals, bugs, or incidents "
    "NOT derivable from code or git history.\n"
    "  When to save: when you learn who is doing what, why, or by when. "
    "Convert relative dates to absolute dates.\n\n"
    "**reference** — Pointers to external resources: issue trackers, dashboards, "
    "wikis, documentation sites, or internal tools.\n"
    "  When to save: when you learn about an external system the team uses.\n"
)

MEMORY_EXCLUSION_SPEC = (
    "- Code patterns, conventions, architecture, file paths, or project structure "
    "— derivable by reading the codebase.\n"
    "- Git history, recent changes, or who-changed-what — `git log`/`git blame` "
    "are authoritative.\n"
    "- Debugging solutions or fix recipes — the fix is in the code.\n"
    "- Anything already documented in AGENTS.md files.\n"
    "- Ephemeral task details: in-progress work, temporary state, current "
    "conversation context.\n"
    "- Activity logs, PR lists, or task summaries — only the *surprising* or "
    "*non-obvious* part is worth keeping.\n"
)


class AgentHooks:
    """Per-agent lifecycle hooks.

    Subclass and override the methods you need. Attach to an Agent via
    ``Agent(hooks=MyHooks())``.

    Example::

        class LoggingHooks(AgentHooks):
            async def on_start(self, agent, **kwargs):
                print(f"{agent.name} starting")

            async def on_end(self, agent, output, **kwargs):
                print(f"{agent.name} produced: {output}")
    """

    async def on_start(self, agent: Any, **kwargs) -> None:
        """Called when this agent begins a run."""
        pass

    async def on_end(self, agent: Any, output: Any, **kwargs) -> None:
        """Called when this agent finishes a run."""
        pass


class RunHooks:
    """Global run-level lifecycle hooks.

    These hooks observe the entire run, including LLM calls, tool calls,
    and agent transfers. Pass to ``agent.run(hooks=MyRunHooks())``.

    Example::

        class MetricsHooks(RunHooks):
            def __init__(self):
                self.event_counter = 0

            async def on_agent_start(self, agent, **kwargs):
                self.event_counter += 1
                print(f"#{self.event_counter}: Agent {agent.name} started")

            async def on_llm_start(self, agent, messages, **kwargs):
                self.event_counter += 1
                print(f"#{self.event_counter}: LLM call started")

            async def on_llm_end(self, agent, response, **kwargs):
                self.event_counter += 1
                print(f"#{self.event_counter}: LLM call ended")

            async def on_tool_start(self, agent, tool_name, tool_call_id, tool_args, **kwargs):
                self.event_counter += 1
                print(f"#{self.event_counter}: Tool {tool_name} started")

            async def on_tool_end(self, agent, tool_name, tool_call_id, tool_args, result, **kwargs):
                self.event_counter += 1
                print(f"#{self.event_counter}: Tool {tool_name} ended")

            async def on_agent_transfer(self, from_agent, to_agent, **kwargs):
                self.event_counter += 1
                print(f"#{self.event_counter}: Transfer from {from_agent.name} to {to_agent.name}")

            async def on_agent_end(self, agent, output, **kwargs):
                self.event_counter += 1
                print(f"#{self.event_counter}: Agent {agent.name} ended")
    """

    async def on_agent_start(self, agent: Any, **kwargs) -> None:
        """Called when any agent begins execution within this run."""
        pass

    async def on_agent_end(self, agent: Any, output: Any, **kwargs) -> None:
        """Called when any agent finishes execution within this run."""
        pass

    async def on_llm_start(
        self,
        agent: Any,
        messages: Optional[List[Dict[str, Any]]] = None,
        **kwargs,
    ) -> None:
        """Called before each LLM API call."""
        pass

    async def on_llm_end(
        self,
        agent: Any,
        response: Any = None,
        **kwargs,
    ) -> None:
        """Called after each LLM API call returns."""
        pass

    async def on_tool_start(
        self,
        agent: Any,
        tool_name: str = "",
        tool_call_id: str = "",
        tool_args: Optional[Dict[str, Any]] = None,
        **kwargs,
    ) -> None:
        """Called before a tool begins execution."""
        pass

    async def on_tool_end(
        self,
        agent: Any,
        tool_name: str = "",
        tool_call_id: str = "",
        tool_args: Optional[Dict[str, Any]] = None,
        result: Any = None,
        is_error: bool = False,
        elapsed: float = 0.0,
        **kwargs,
    ) -> None:
        """Called after a tool finishes execution."""
        pass

    async def on_agent_transfer(
        self,
        from_agent: Any,
        to_agent: Any,
        **kwargs,
    ) -> None:
        """Called when a task is transferred from one agent to another."""
        pass

    async def on_user_prompt(
        self,
        agent: Any,
        message: str,
        **kwargs,
    ) -> Optional[str]:
        """Called before a user prompt is processed.

        Return a modified message string to replace the original, or None to
        keep it unchanged.  Mirrors CC's UserPromptSubmit hook.
        """
        return None

    async def on_pre_compact(
        self,
        agent: Any,
        messages: Optional[List] = None,
        **kwargs,
    ) -> None:
        """Called just before context compression is triggered.

        Use for: saving state, logging, custom archival before messages are
        compressed/dropped.  Mirrors CC's PreCompact hook.
        """
        pass

    async def on_post_compact(
        self,
        agent: Any,
        messages: Optional[List] = None,
        **kwargs,
    ) -> None:
        """Called right after context compression completes.

        ``messages`` is the compressed result (may be much shorter than before).
        Use for: post-compression analytics, re-injecting critical context.
        Mirrors CC's PostCompact hook.
        """
        pass


class ConversationArchiveHooks(RunHooks):
    """RunHooks that auto-archives conversations to workspace after each agent run.

    Captures user input and agent output from each run and appends them to
    the daily conversation archive in the workspace.

    Usage::

        from agentica.hooks import ConversationArchiveHooks

        hooks = ConversationArchiveHooks()
        response = await agent.run("Hello", config=RunConfig(hooks=hooks))
    """

    def __init__(self):
        pass

    async def on_agent_end(self, agent: Any, output: Any, **kwargs) -> None:
        """Archive conversation after agent completes.

        Reads agent.run_input directly (set by Runner before on_agent_end).
        """
        workspace = agent.workspace
        if workspace is None:
            return

        messages_to_archive = []

        # Read run_input directly — Runner sets it before calling on_agent_end
        run_input = agent.run_input
        if run_input and isinstance(run_input, str):
            messages_to_archive.append({"role": "user", "content": run_input})

        # Collect agent output
        if output and isinstance(output, str):
            messages_to_archive.append({"role": "assistant", "content": output})

        if not messages_to_archive:
            return

        try:
            session_id = agent.run_id
            filepath = await workspace.archive_conversation(messages_to_archive, session_id=session_id)
            logger.debug(f"Conversation saved to {filepath}")
        except Exception as e:
            logger.warning(f"Failed to archive conversation: {e}")


class MemoryExtractHooks(RunHooks):
    """RunHooks that auto-extracts memories from conversations after each agent run.

    After each run, checks if the LLM already called save_memory during the conversation.
    If not, uses the LLM to extract key information worth remembering and saves it
    to the workspace memory directory.

    This mirrors Claude Code's extractMemories service: a background process that
    fires after each conversation to capture important information the main agent
    didn't explicitly save.

    Usage::

        from agentica.hooks import MemoryExtractHooks

        hooks = MemoryExtractHooks()
        response = await agent.run("Hello", config=RunConfig(hooks=hooks))
    """

    # Prompt for the memory extraction sub-call.
    # Uses shared MEMORY_TYPE_SPEC / MEMORY_EXCLUSION_SPEC constants
    # (same source as BuiltinMemoryTool.MEMORY_SYSTEM_PROMPT).
    _EXTRACT_PROMPT = (
        "You are a memory extraction assistant. Review the conversation below and "
        "extract key information worth remembering for future sessions.\n\n"
        "Memories capture context NOT derivable from the current project state. "
        "Code patterns, architecture, git history, and file structure are derivable "
        "(via grep/git/AGENTS.md) and must NOT be saved as memories.\n\n"
        "## Memory types\n\n"
        + MEMORY_TYPE_SPEC +
        "\n## What NOT to save\n\n"
        + MEMORY_EXCLUSION_SPEC +
        "\n## Output format\n\n"
        "For each memory, output a JSON object with fields:\n"
        '  {"title": "short_name", "content": "what to remember (include Why + '
        'How to apply for feedback type)", "type": "user|feedback|project|reference"}\n\n'
        "Output a JSON array of memories. If nothing worth remembering, output: []\n\n"
        "Conversation:\n"
    )

    def __init__(self, sync_memories_to_global_agent_md: bool = False):
        self._tool_calls: Dict[str, List[str]] = {}  # agent_id -> list of tool names called
        self._sync_memories_to_global_agent_md = sync_memories_to_global_agent_md

    async def on_agent_start(self, agent: Any, **kwargs) -> None:
        self._tool_calls[agent.agent_id] = []

    async def on_tool_end(self, agent: Any, tool_name: str = "", **kwargs) -> None:
        """Track tool calls to detect if save_memory was already used."""
        agent_id = agent.agent_id
        if agent_id not in self._tool_calls:
            self._tool_calls[agent_id] = []
        self._tool_calls[agent_id].append(tool_name)

    async def on_agent_end(self, agent: Any, output: Any, **kwargs) -> None:
        """Extract and save memories if LLM didn't use save_memory during this run."""
        # Always drain accumulated state first — even when workspace is None.
        # Without this, _tool_calls leaks keys for workspace-less runs.
        agent_id = agent.agent_id
        tool_calls = self._tool_calls.pop(agent_id, [])

        workspace = agent.workspace
        if workspace is None:
            return

        # Read run_input directly — Runner sets it before calling on_agent_end
        run_input = agent.run_input
        run_input = run_input if isinstance(run_input, str) else None

        # If LLM already called save_memory, skip extraction (CC's hasMemoryWritesSince)
        if "save_memory" in tool_calls:
            logger.debug("Skipping memory extraction: save_memory was called during this run")
            return

        # Build conversation text for extraction
        if not run_input and not output:
            return

        conversation_text = ""
        if run_input:
            conversation_text += f"User: {run_input}\n\n"
        if output and isinstance(output, str):
            conversation_text += f"Assistant: {output}\n"

        # Skip very short conversations (nothing to extract)
        if len(conversation_text) < 50:
            return

        # Use the agent's model to extract memories
        model = agent.model
        if model is None:
            return

        # Await directly — asyncio.create_task() would be silently cancelled
        # in run_stream_sync()/run_sync() scenarios where the event loop exits
        # immediately after the stream is consumed. Direct await ensures the
        # extraction completes before on_agent_end returns.
        await self._extract_and_save(model, workspace, conversation_text)

    async def _extract_and_save(self, model: Any, workspace: Any, conversation_text: str) -> None:
        """Run the LLM extraction call and persist results."""
        extract_messages = [
            Message(role="user", content=self._EXTRACT_PROMPT + conversation_text),
        ]

        try:
            model_response = await model.response(extract_messages)
            if not model_response or not model_response.content:
                return

            # Parse JSON array from response
            text = model_response.content.strip()
            # Handle markdown code blocks
            if text.startswith("```"):
                text = text.split("\n", 1)[-1].rsplit("```", 1)[0].strip()

            memories = json.loads(text)
            if not isinstance(memories, list) or not memories:
                return

            for mem in memories:
                if not isinstance(mem, dict):
                    continue
                title = mem.get("title", "").strip()
                content = mem.get("content", "").strip()
                mem_type = mem.get("type", "project").strip()
                if not title or not content:
                    continue
                if mem_type not in ("user", "feedback", "project", "reference"):
                    mem_type = "project"

                await workspace.write_memory_entry(
                    title=title,
                    content=content,
                    memory_type=mem_type,
                    description=title,
                    sync_to_global_agent_md=(
                        self._sync_memories_to_global_agent_md and mem_type in ("user", "feedback")
                    ),
                )
                logger.debug(f"Auto-extracted memory: {title} (type: {mem_type})")

        except json.JSONDecodeError as e:
            logger.debug(f"Memory extraction: LLM returned invalid JSON: {e}")
        except Exception as e:
            logger.warning(f"Memory extraction failed: {e}")


class ExperienceCaptureHooks(RunHooks):
    """Capture tool failures, user corrections, and success patterns.

    Tool errors and success patterns are captured deterministically (zero LLM cost).
    User corrections are classified by an auxiliary LLM model for accuracy —
    keyword matching is too fragile for nuanced human feedback.

    Persists experiences to workspace at on_agent_end for cross-session learning.

    Delegates to three experience-layer classes:
    - ExperienceEventStore: append-only raw event persistence
    - ExperienceCompiler: pure/stateless compilation (errors/successes -> cards)
    - CompiledExperienceStore: card CRUD, lifecycle, sync

    Usage::

        from agentica.hooks import ExperienceCaptureHooks
        from agentica.agent.config import ExperienceConfig

        hooks = ExperienceCaptureHooks(ExperienceConfig())
        response = await agent.run("Hello", config=RunConfig(hooks=hooks))
    """

    # LLM prompt for feedback classification
    _FEEDBACK_CLASSIFY_PROMPT = (
        "You are judging whether the user's latest message is a correction or "
        "behavioral feedback to the assistant.\n\n"
        "Inputs:\n"
        "- Previous assistant message\n"
        "- Current user message\n\n"
        "Decide:\n"
        "1. Is the user correcting the assistant, rejecting its approach, or "
        "imposing a behavioral constraint?\n"
        "2. Is this feedback only relevant to the current turn, or should it be "
        "remembered across future sessions?\n"
        "3. If it should be remembered, normalize it into a reusable rule.\n\n"
        "Important:\n"
        "- Do not rely on literal phrases.\n"
        "- Indirect corrections count.\n"
        "- Quoted text, examples, or hypothetical statements are NOT corrections.\n"
        "- Only mark should_persist=true when the feedback is generalizable.\n\n"
        "Return JSON only with these fields:\n"
        '{"is_correction": bool, "confidence": float (0-1), '
        '"category": "factual|preference|workflow|tool_usage|rejection|not_correction", '
        '"scope": "turn_only|session|cross_session", '
        '"should_persist": bool, '
        '"persist_target": "none|experience|session_only", '
        '"title": "snake_case_short_name", '
        '"rule": "one-line reusable rule", '
        '"why": "reason this matters", '
        '"how_to_apply": "when and where to apply this rule"}\n\n'
    )

    def __init__(self, config: Any):
        self._config = config
        # Per-agent state (keyed by agent_id)
        self._tool_errors: Dict[str, List[Dict]] = {}
        self._tool_successes: Dict[str, List[Dict]] = {}
        self._last_assistant_output: Dict[str, Optional[str]] = {}
        self._skills_used: Dict[str, set] = {}  # agent_id -> set of skill names loaded via get_skill_info
        self._correction_detected: Dict[str, bool] = {}  # agent_id -> True if correction persisted this run

    async def on_agent_start(self, agent: Any, **kwargs) -> None:
        """Initialize per-agent capture state."""
        aid = agent.agent_id
        self._tool_errors[aid] = []
        self._tool_successes[aid] = []
        self._last_assistant_output[aid] = None
        self._skills_used[aid] = set()
        self._correction_detected[aid] = False

    async def on_user_prompt(self, agent: Any, message: str, **kwargs) -> Optional[str]:
        """No-op: classification uses agent.run_input at on_agent_end time."""
        return None  # Never modify the message

    async def on_tool_end(
        self,
        agent: Any,
        tool_name: str = "",
        tool_call_id: str = "",
        tool_args: Optional[Dict[str, Any]] = None,
        result: Any = None,
        is_error: bool = False,
        elapsed: float = 0.0,
        **kwargs,
    ) -> None:
        """Record tool errors and successes."""
        aid = agent.agent_id

        if is_error and self._config.capture_tool_errors:
            result_str = str(result)[:500] if result else ""
            self._tool_errors.setdefault(aid, []).append({
                "tool": tool_name,
                "args": tool_args or {},
                "error": result_str,
                "elapsed": elapsed,
            })
        elif not is_error and self._config.capture_success_patterns:
            self._tool_successes.setdefault(aid, []).append({
                "tool": tool_name,
                "elapsed": elapsed,
            })

        # Track generated skills only when get_skill_info returned real content.
        if tool_name == "get_skill_info" and tool_args and not is_error:
            result_text = str(result) if result is not None else ""
            skill_name = tool_args.get("skill_name") or tool_args.get("name", "")
            if skill_name and not result_text.startswith("Error:"):
                self._skills_used.setdefault(aid, set()).add(skill_name)

    async def on_agent_end(self, agent: Any, output: Any, **kwargs) -> None:
        """Persist captured experiences to workspace.

        Flow: write raw events -> compile cards -> persist -> lifecycle -> sync.
        Delegates compilation to ExperienceCompiler (pure, no I/O).
        Delegates persistence to ExperienceEventStore / CompiledExperienceStore.
        """
        from agentica.experience.compiler import ExperienceCompiler

        # Always drain accumulated state first — even when workspace is None.
        # Without this, state leaks into the next run that DOES have a workspace.
        aid = agent.agent_id
        errors = self._tool_errors.pop(aid, [])
        successes = self._tool_successes.pop(aid, [])
        self._last_assistant_output.pop(aid, None)
        skills_used = self._skills_used.pop(aid, set())
        correction_this_run = self._correction_detected.pop(aid, False)

        workspace = agent.workspace
        if workspace is None:
            return

        # Read run_input directly — Runner sets it before calling on_agent_end
        run_input = agent.run_input
        user_msg = run_input if isinstance(run_input, str) else None

        # Extract previous assistant message from working_memory for correction context.
        previous_assistant_text = self._get_previous_assistant_text(agent)

        # Get stores from workspace
        event_store = workspace.get_experience_event_store()
        compiled_store = workspace.get_compiled_experience_store()

        # ── 1. Write raw events (pure builder + store) ──
        raw_events = ExperienceCompiler.build_raw_events(
            errors=errors,
            user_msg=user_msg,
            previous_assistant=previous_assistant_text,
            successes=successes,
            capture_corrections=self._config.capture_user_corrections,
        )
        for event in raw_events:
            await event_store.append(event)

        # ── 2. Compile and persist experience cards ──

        # 2a. Tool errors (deterministic, zero LLM cost)
        # Dedup by title: same run may produce duplicate error titles (e.g. two
        # PermissionErrors from the same tool), which would inflate repeat_count.
        error_cards = ExperienceCompiler.compile_tool_errors(errors)
        seen_titles: set = set()
        for card in error_cards:
            if card.title in seen_titles:
                continue
            seen_titles.add(card.title)
            try:
                await compiled_store.write(card)
            except Exception as e:
                logger.warning(f"Failed to write tool error experience: {e}")

        # 2b. LLM-based correction classification
        if self._config.capture_user_corrections and user_msg:
            model = self._get_classification_model(agent)
            if model is not None:
                was_correction = await self._classify_and_persist_feedback(
                    model, event_store, compiled_store,
                    user_msg, previous_assistant_text or "",
                )
                if was_correction:
                    correction_this_run = True

        # 2c. Success pattern
        success_card = ExperienceCompiler.compile_success_pattern(successes)
        if success_card and not errors:
            try:
                await compiled_store.write(success_card)
            except Exception as e:
                logger.warning(f"Failed to write success pattern experience: {e}")

        # ── 3. Lifecycle sweep ──
        try:
            await compiled_store.run_lifecycle(
                promotion_count=self._config.promotion_count,
                promotion_window_days=self._config.promotion_window_days,
                demotion_days=self._config.demotion_days,
                archive_days=self._config.archive_days,
            )
        except Exception as e:
            logger.debug(f"Experience lifecycle sweep failed: {e}")

        # ── 3.5 Skill upgrade (after lifecycle, before sync) ──
        skill_cfg = self._config.skill_upgrade
        if skill_cfg is not None and skill_cfg.mode != "off":
            try:
                manager = SkillEvolutionManager()
                upgrade_model = self._get_classification_model(agent)
                if upgrade_model is not None:
                    gen_dir = workspace._get_user_generated_skills_dir()
                    exp_dir = workspace._get_user_experience_dir()
                    skill_tool = None
                    for tool in agent.tools or []:
                        if isinstance(tool, SkillTool):
                            skill_tool = tool
                            break
                    should_reload_generated_skills = False

                    # Phase A: try to spawn new skill from experience
                    # (draft mode only generates, shadow mode generates + installs)
                    candidates = manager.get_candidate_cards(
                        exp_dir=exp_dir,
                        min_repeat_count=skill_cfg.min_repeat_count,
                        min_tier=skill_cfg.min_tier,
                    )
                    if candidates:
                        existing = set(
                            [d.name for d in gen_dir.iterdir() if d.is_dir()]
                            if gen_dir.exists() else []
                        )
                        if skill_tool is not None:
                            existing.update(skill.name for skill in skill_tool.registry.list_all())
                        spawned = await manager.maybe_spawn_skill(
                            model=upgrade_model,
                            candidates=candidates,
                            existing_skills=sorted(existing),
                            generated_skills_dir=gen_dir,
                        )
                        # In draft mode, mark as draft instead of shadow
                        if spawned and skill_cfg.mode == "draft":
                            meta_path = gen_dir / spawned / "meta.json"
                            meta = manager.read_meta(meta_path)
                            if meta:
                                meta["status"] = "draft"
                                manager.write_meta(meta_path, meta)

                        if spawned and skill_cfg.mode == "shadow":
                            should_reload_generated_skills = True

                    # Phase B: record episode only for skills actually used this run
                    if skill_cfg.mode == "shadow" and skills_used and gen_dir.exists():
                        outcome = "failure" if errors or correction_this_run else "success"
                        query_text = user_msg or ""
                        for skill_dir in gen_dir.iterdir():
                            if not skill_dir.is_dir():
                                continue
                            meta = manager.read_meta(skill_dir / "meta.json")
                            skill_name = meta.get("skill_name", "")
                            if not skill_name or skill_name not in skills_used:
                                continue
                            if meta.get("status") not in ("shadow", "auto"):
                                continue
                            if skill_tool is not None:
                                loaded_skill = skill_tool.registry.get(skill_name)
                                if loaded_skill is None or loaded_skill.location != "generated":
                                    continue
                            episodes_path = skill_dir / "episodes.jsonl"
                            manager.record_episode(
                                episodes_path=episodes_path,
                                outcome=outcome,
                                query=query_text,
                                tool_errors=len(errors),
                                user_corrected=correction_this_run,
                            )
                            manager.update_meta_after_episode(
                                skill_dir / "meta.json", outcome,
                            )
                            # Phase C: checkpoint judgment
                            decision = await manager.maybe_update_skill_state(
                                model=upgrade_model,
                                skill_dir=skill_dir,
                                checkpoint_interval=skill_cfg.checkpoint_interval,
                                rollback_consecutive_failures=skill_cfg.rollback_consecutive_failures,
                            )
                            if decision is not None:
                                should_reload_generated_skills = True

                    if should_reload_generated_skills and skill_tool is not None:
                        skill_tool.reload_generated_skills()
                        agent.refresh_tool_system_prompts()
            except Exception as e:
                logger.debug(f"Skill upgrade check failed: {e}")

        # ── 4. Sync to global AGENTS.md ──
        if self._config.sync_to_global_agent_md:
            try:
                global_md = workspace._get_global_agent_md_path()
                await compiled_store.sync_to_global_agent_md(global_md)
            except Exception as e:
                logger.debug(f"Experience sync to global AGENTS.md failed: {e}")

    @staticmethod
    def _get_previous_assistant_text(agent: Any) -> Optional[str]:
        """Get the last assistant message text from working_memory.

        At on_agent_end time, the current run's messages haven't been added
        to working_memory yet, so the last assistant message reflects previous
        runs — which is exactly what a user correction refers to.
        """
        messages = agent.working_memory.messages
        for msg in reversed(messages):
            if msg.role == "assistant" and msg.content:
                return msg.content if isinstance(msg.content, str) else str(msg.content)
        return None

    @staticmethod
    def _get_classification_model(agent: Any) -> Any:
        """Get the model for feedback classification.

        Prefers auxiliary_model (cheaper), falls back to main model.
        Returns None if no model is available.
        """
        model = agent.auxiliary_model
        if model is not None:
            return model
        return agent.model

    async def _classify_and_persist_feedback(
        self,
        model: Any,
        event_store: Any,
        compiled_store: Any,
        user_message: str,
        previous_assistant_text: str,
    ) -> bool:
        """Classify user feedback with LLM and persist if appropriate.

        Uses ExperienceCompiler for card building (pure logic).
        Delegates I/O to event_store and compiled_store.

        Returns:
            True if a correction was persisted to the experience store.
        """
        from agentica.experience.compiler import ExperienceCompiler

        prompt = (
            self._FEEDBACK_CLASSIFY_PROMPT
            + f"Previous assistant message:\n{previous_assistant_text[:1000]}\n\n"
            + f"Current user message:\n{user_message[:1000]}\n"
        )

        threshold = self._config.feedback_confidence_threshold

        try:
            response = await model.response([
                Message(role="user", content=prompt),
            ])
            if not response or not response.content:
                return False

            text = response.content.strip()
            if text.startswith("```"):
                text = text.split("\n", 1)[-1].rsplit("```", 1)[0].strip()

            result = json.loads(text)
            if not isinstance(result, dict):
                return False

            is_correction = result.get("is_correction", False)
            confidence = result.get("confidence", 0.0)

            # Write classification result as raw event
            await event_store.append({
                "event_type": "correction_classification",
                "is_correction": is_correction,
                "confidence": confidence,
                "should_persist": result.get("should_persist", False),
                "persist_target": result.get("persist_target", "none"),
                "user_message": user_message[:300],
            })

            if not is_correction or confidence < threshold:
                return False
            if not result.get("should_persist", False) or result.get("persist_target") == "none":
                return False

            # All corrections go to experience store (no cross-layer memory writes)
            card = ExperienceCompiler.compile_correction(result)
            if card:
                await compiled_store.write(card)
                return True

            return False

        except json.JSONDecodeError:
            logger.debug("Feedback classification: LLM returned invalid JSON")
            return False
        except Exception as e:
            logger.warning(f"Feedback classification failed ({type(e).__name__}): {e}")
            return False


class _CompositeRunHooks(RunHooks):
    """Internal wrapper that dispatches to multiple RunHooks instances.

    Used to combine auto-injected hooks (e.g. ConversationArchiveHooks)
    with user-provided hooks without requiring users to manage composition.
    """

    def __init__(self, hooks_list: List[RunHooks]):
        self._hooks_list = hooks_list

    async def on_agent_start(self, agent: Any, **kwargs) -> None:
        for h in self._hooks_list:
            await h.on_agent_start(agent=agent, **kwargs)

    async def on_agent_end(self, agent: Any, output: Any, **kwargs) -> None:
        for h in self._hooks_list:
            await h.on_agent_end(agent=agent, output=output, **kwargs)

    async def on_llm_start(self, agent: Any, messages: Optional[List[Dict[str, Any]]] = None, **kwargs) -> None:
        for h in self._hooks_list:
            await h.on_llm_start(agent=agent, messages=messages, **kwargs)

    async def on_llm_end(self, agent: Any, response: Any = None, **kwargs) -> None:
        for h in self._hooks_list:
            await h.on_llm_end(agent=agent, response=response, **kwargs)

    async def on_tool_start(self, agent: Any, tool_name: str = "", tool_call_id: str = "",
                            tool_args: Optional[Dict[str, Any]] = None, **kwargs) -> None:
        for h in self._hooks_list:
            await h.on_tool_start(agent=agent, tool_name=tool_name, tool_call_id=tool_call_id,
                                  tool_args=tool_args, **kwargs)

    async def on_tool_end(self, agent: Any, tool_name: str = "", tool_call_id: str = "",
                          tool_args: Optional[Dict[str, Any]] = None, result: Any = None,
                          is_error: bool = False, elapsed: float = 0.0, **kwargs) -> None:
        for h in self._hooks_list:
            await h.on_tool_end(agent=agent, tool_name=tool_name, tool_call_id=tool_call_id,
                                tool_args=tool_args, result=result, is_error=is_error,
                                elapsed=elapsed, **kwargs)

    async def on_agent_transfer(self, from_agent: Any, to_agent: Any, **kwargs) -> None:
        for h in self._hooks_list:
            await h.on_agent_transfer(from_agent=from_agent, to_agent=to_agent, **kwargs)

    async def on_user_prompt(self, agent: Any, message: str, **kwargs) -> Optional[str]:
        result = None
        for h in self._hooks_list:
            r = await h.on_user_prompt(agent=agent, message=message, **kwargs)
            if r is not None:
                result = r
                message = r  # chain: next hook sees the modified message
        return result

    async def on_pre_compact(self, agent: Any, messages=None, **kwargs) -> None:
        for h in self._hooks_list:
            await h.on_pre_compact(agent=agent, messages=messages, **kwargs)

    async def on_post_compact(self, agent: Any, messages=None, **kwargs) -> None:
        for h in self._hooks_list:
            await h.on_post_compact(agent=agent, messages=messages, **kwargs)
