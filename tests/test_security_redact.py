# -*- coding: utf-8 -*-
"""Tests for central sensitive text redaction."""


def test_security_redact_masks_common_secret_shapes():
    from agentica.security.redact import redact_sensitive_text

    private_key = (
        "-----BEGIN PRIVATE KEY-----\n"
        "super-secret-key-material\n"
        "-----END PRIVATE KEY-----"
    )
    text = "\n".join([
        "openai=sk-abcdefghijklmnopqrstuvwxyz1234567890",
        "github=ghp_abcdefghijklmnopqrstuvwxyz1234567890",
        "aws=AKIAIOSFODNN7EXAMPLE",
        "auth=Authorization: Bearer eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.payload.signature",
        "bearer=Bearer abcdefghijklmnopqrstuvwxyz123456",
        "password: plain_text_password_123",
        "db=postgresql://alice:db_password_123@example.com/app",
        "url=https://api.example.com/v1?api_key=super_secret_123&other=ok",
        private_key,
    ])

    redacted = redact_sensitive_text(text)

    assert "sk-abcdefghijklmnopqrstuvwxyz" not in redacted
    assert "ghp_abcdefghijklmnopqrstuvwxyz" not in redacted
    assert "AKIAIOSFODNN7EXAMPLE" not in redacted
    assert "eyJhbGciOiJIUzI1Ni" not in redacted
    assert "abcdefghijklmnopqrstuvwxyz123456" not in redacted
    assert "plain_text_password_123" not in redacted
    assert "db_password_123" not in redacted
    assert "super_secret_123" not in redacted
    assert "super-secret-key-material" not in redacted
    assert "other=ok" in redacted
    assert "REDACTED" in redacted


def test_tools_safety_reexports_central_redactor():
    from agentica.security.redact import redact_sensitive_text as central_redact
    from agentica.tools.safety import redact_sensitive_text as safety_redact

    text = "api_key=example_secret_value_1234567890"

    assert safety_redact(text) == central_redact(text)


def test_model_function_call_results_are_redacted():
    import asyncio

    from agentica.model.openai import OpenAIChat
    from agentica.tools.base import Function, FunctionCall

    secret = "sk-abcdefghijklmnopqrstuvwxyz1234567890"

    def emit_secret() -> str:
        """Emit a fake secret for redaction testing."""
        return f"OPENAI_API_KEY={secret}"

    function = Function.from_callable(emit_secret)
    model = OpenAIChat(id="gpt-4o-mini", api_key="fake_openai_key")
    results = []

    async def run_tool() -> None:
        async for _ in model.run_function_calls(
            [FunctionCall(function=function, arguments={})],
            results,
        ):
            pass

    asyncio.run(run_tool())

    assert len(results) == 1
    assert secret not in results[0].content
    assert "REDACTED" in results[0].content


def test_model_show_result_yields_redacted_content():
    import asyncio

    from agentica.model.openai import OpenAIChat
    from agentica.tools.base import Function, FunctionCall

    secret = "sk-abcdefghijklmnopqrstuvwxyz1234567890"

    def emit_secret() -> str:
        """Emit a fake secret for redaction testing."""
        return f"OPENAI_API_KEY={secret}"

    function = Function.from_callable(emit_secret)
    function.show_result = True
    model = OpenAIChat(id="gpt-4o-mini", api_key="fake_openai_key")
    yielded = []
    results = []

    async def run_tool() -> None:
        async for chunk in model.run_function_calls(
            [FunctionCall(function=function, arguments={})],
            results,
        ):
            yielded.append(chunk.content)

    asyncio.run(run_tool())

    assert yielded
    assert all(secret not in str(content) for content in yielded)
    assert any("REDACTED" in str(content) for content in yielded)
