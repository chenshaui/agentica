# -*- coding: utf-8 -*-
"""
@author:XuMing(xuming624@qq.com)
@description: Tests for the SDK-first run lifecycle types (arch_v5.md Phase 0/3).

Covers:
- TaskAnchor.from_message handles str/dict/Message-like inputs
- TaskAnchor.to_prompt_block renders empty for empty anchors
- RunContext lifecycle transitions (running/completed/failed/cancelled)
- RunEventRecord.to_dict shape
"""
import pytest

from agentica.run_context import RunContext, RunSource, RunStatus, TaskAnchor
from agentica.run_events import RunEventRecord, RunEventType


class TestTaskAnchor:
    def test_from_str_message(self):
        a = TaskAnchor.from_message("Build me a thing")
        assert a.goal == "Build me a thing"
        assert a.source_query == "Build me a thing"

    def test_from_dict_message(self):
        a = TaskAnchor.from_message({"role": "user", "content": "hello"})
        assert a.goal == "hello"
        assert a.source_query == "hello"

    def test_from_object_with_content(self):
        class _Msg:
            content = "fix bug X"
        a = TaskAnchor.from_message(_Msg())
        assert a.goal == "fix bug X"

    def test_from_unsupported_message_is_empty(self):
        a = TaskAnchor.from_message(None)
        assert a.goal == ""
        assert a.source_query == ""

    def test_to_prompt_block_empty(self):
        assert TaskAnchor().to_prompt_block() == ""

    def test_to_prompt_block_renders_goal_and_constraints(self):
        a = TaskAnchor(
            goal="Refactor X",
            source_query="Refactor X",
            acceptance_criteria=["pass tests", "no breakage"],
            constraints=["don't touch DB"],
            confirmed_facts=["repo=foo"],
            next_step_hint="start with module Y",
        )
        block = a.to_prompt_block()
        assert "<original_task>" in block and "</original_task>" in block
        assert "GOAL: Refactor X" in block
        assert "ACCEPTANCE CRITERIA:" in block
        assert "- pass tests" in block
        assert "CONSTRAINTS:" in block
        assert "- don't touch DB" in block
        assert "NEXT STEP HINT: start with module Y" in block


class TestRunContext:
    def test_default_run_id_is_unique(self):
        a = RunContext()
        b = RunContext()
        assert a.run_id != b.run_id
        assert a.status == RunStatus.created
        assert a.source == RunSource.sdk

    def test_lifecycle_transitions(self):
        ctx = RunContext()
        ctx.mark_running()
        assert ctx.status == RunStatus.running
        ctx.mark_completed()
        assert ctx.status == RunStatus.completed
        assert ctx.ended_at is not None
        assert (ctx.duration_seconds or 0) >= 0

    def test_mark_failed_records_error(self):
        ctx = RunContext()
        ctx.mark_failed("kaboom")
        assert ctx.status == RunStatus.failed
        assert ctx.error == "kaboom"
        assert ctx.ended_at is not None

    def test_mark_cancelled_default_reason(self):
        ctx = RunContext()
        ctx.mark_cancelled()
        assert ctx.status == RunStatus.cancelled
        assert ctx.error == "user_cancelled"

    def test_to_dict_round_trip_shape(self):
        anchor = TaskAnchor(goal="g", source_query="g")
        ctx = RunContext(
            session_id="s1",
            agent_id="a1",
            task_anchor=anchor,
            metadata={"foo": "bar"},
        )
        d = ctx.to_dict()
        assert d["run_id"] == ctx.run_id
        assert d["status"] == "created"
        assert d["task_anchor"]["goal"] == "g"
        assert d["task_anchor"]["source_query"] == "g"
        assert d["metadata"] == {"foo": "bar"}


class TestRunEventRecord:
    def test_to_dict_flatten_payload(self):
        rec = RunEventRecord(
            run_id="r1",
            event_type=RunEventType.run_started,
            agent_id="a1",
            payload={"source_query": "hi"},
        )
        d = rec.to_dict()
        assert d["type"] == "run.started"
        assert d["run_id"] == "r1"
        assert d["agent_id"] == "a1"
        assert d["source_query"] == "hi"
