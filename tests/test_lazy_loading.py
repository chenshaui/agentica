# -*- coding: utf-8 -*-
"""
@author: XuMing(xuming624@qq.com)
@description: Tests for __init__.py lazy loading mechanism.
"""
import sys
import os
import threading
import importlib

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest


# ===========================================================================
# TestCoreImports
# ===========================================================================


class TestCoreImports:
    """Test that core modules are imported eagerly."""

    def test_agent_importable(self):
        from agentica import Agent
        assert Agent is not None

    def test_model_importable(self):
        from agentica import Model
        assert Model is not None

    def test_openai_chat_importable(self):
        from agentica import OpenAIChat
        assert OpenAIChat is not None

    def test_message_importable(self):
        from agentica import Message
        assert Message is not None

    def test_run_response_importable(self):
        from agentica import RunResponse
        assert RunResponse is not None

    def test_tool_importable(self):
        from agentica import Tool
        assert Tool is not None

    def test_function_importable(self):
        from agentica import Function
        assert Function is not None

    def test_workspace_importable(self):
        from agentica import Workspace
        assert Workspace is not None

    def test_workflow_importable(self):
        from agentica import Workflow
        assert Workflow is not None

    def test_working_memory_importable(self):
        from agentica import WorkingMemory
        assert WorkingMemory is not None


# ===========================================================================
# TestLazyLoadOptional
# ===========================================================================


class TestLazyLoadOptional:
    """Test that optional modules use lazy loading."""

    def test_guardrails_importable(self):
        """Guardrails should be accessible."""
        try:
            from agentica.guardrails import InputGuardrail, OutputGuardrail
            assert InputGuardrail is not None
        except ImportError:
            pytest.skip("Guardrails not available")


# ===========================================================================
# TestThreadSafety
# ===========================================================================


class TestThreadSafety:
    """Test that concurrent imports don't cause issues."""

    def test_concurrent_imports_no_error(self):
        """Multiple threads importing agentica simultaneously should not crash."""
        errors = []

        def _import_agentica():
            try:
                import agentica
                _ = agentica.Agent
                _ = agentica.OpenAIChat
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=_import_agentica) for _ in range(10)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=5)

        assert len(errors) == 0, f"Import errors: {errors}"


# ===========================================================================
# TestPublicAPI
# ===========================================================================


class TestPublicAPI:
    """Test that __all__ names are accessible.

    Some lazy-loaded names require extras (e.g. SqliteDb needs [sql], McpTool needs [mcp]).
    Any ImportError on access is treated as "extras missing" (allowed); only
    other exceptions (AttributeError, TypeError, etc) count as real failures.
    """

    def test_all_public_names_accessible(self):
        import agentica
        if not hasattr(agentica, '__all__'):
            return
        missing = []
        extras_missing = []
        for name in agentica.__all__:
            try:
                obj = getattr(agentica, name)
                if obj is None:
                    missing.append(name)
            except ImportError:
                # Either friendly or raw ImportError = extras not installed in this env
                extras_missing.append(name)
            except Exception as e:
                # AttributeError, TypeError etc are real bugs in lazy-loading mapping
                missing.append(f"{name}: {type(e).__name__}: {e}")
        assert not missing, f"Names inaccessible for non-import reasons: {missing}"
        # Log but don't fail on extras-missing names (user hasn't installed them)
        if extras_missing:
            preview = extras_missing[:5]
            print(f"\n[INFO] {len(extras_missing)} names require extras (skipped): {preview}...")


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
