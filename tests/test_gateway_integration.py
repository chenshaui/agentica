"""Integration tests for gateway FastAPI endpoints using TestClient.

Tests that exercise the HTTP layer (routes, middleware, response format).
Agent calls are mocked at the AgentService level to avoid real LLM calls.
"""
from unittest.mock import MagicMock, AsyncMock, patch

import pytest

# Guard: only run if fastapi + httpx are available
pytest.importorskip("fastapi")
pytest.importorskip("httpx")

from fastapi.testclient import TestClient


@pytest.fixture()
def mock_app():
    """Create a TestClient with deps patched after lifespan initialization.

    The lifespan creates real services; we patch deps.agent_service
    with a mock after startup to intercept all agent calls.
    """
    from agentica.gateway.services.agent_service import ChatResult
    from agentica.gateway.main import app
    from agentica.gateway import deps

    mock_svc = MagicMock()
    mock_svc.chat = AsyncMock(return_value=ChatResult(
        content="Hello from agent",
        tool_calls=1,
        session_id="test-session",
        user_id="test-user",
        tools_used=["read_file"],
    ))
    mock_svc.list_sessions = MagicMock(return_value=["s1", "s2"])
    mock_svc.delete_session = MagicMock(return_value=True)
    mock_svc.get_context_window = MagicMock(return_value=128000)
    mock_svc._ensure_initialized = AsyncMock()
    mock_svc.model_provider = "openai"
    mock_svc.model_name = "gpt-4o"
    mock_svc.reload_model = AsyncMock()
    mock_svc.save_memory = AsyncMock()
    mock_svc._cache = MagicMock()
    mock_svc._cache.keys = MagicMock(return_value=[])
    mock_svc._workspace = None

    with TestClient(app, raise_server_exceptions=False) as client:
        # Override deps AFTER lifespan has initialized
        original_svc = deps.agent_service
        deps.agent_service = mock_svc
        yield client, mock_svc
        deps.agent_service = original_svc


class TestHealthEndpoint:
    """Test /health and / endpoints."""

    def test_root(self, mock_app):
        client, _ = mock_app
        resp = client.get("/")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "running"
        assert "version" in data

    def test_health(self, mock_app):
        client, _ = mock_app
        resp = client.get("/health")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "ok"


class TestChatEndpoint:
    """Test /api/chat (non-streaming)."""

    def test_chat_success(self, mock_app):
        client, mock_svc = mock_app
        resp = client.post("/api/chat", json={
            "message": "Hello",
            "session_id": "test-session",
            "user_id": "test-user",
        })
        assert resp.status_code == 200
        data = resp.json()
        assert data["content"] == "Hello from agent"
        assert data["tool_calls"] == 1
        mock_svc.chat.assert_awaited_once()

    def test_chat_missing_message(self, mock_app):
        client, _ = mock_app
        resp = client.post("/api/chat", json={})
        assert resp.status_code == 422  # validation error


class TestSessionEndpoints:
    """Test /api/sessions endpoints."""

    def test_list_sessions(self, mock_app):
        client, _ = mock_app
        resp = client.get("/api/sessions")
        assert resp.status_code == 200
        data = resp.json()
        assert data["sessions"] == ["s1", "s2"]

    def test_delete_session(self, mock_app):
        client, mock_svc = mock_app
        resp = client.delete("/api/sessions/s1")
        assert resp.status_code == 200
        mock_svc.delete_session.assert_called_with("s1")

    def test_delete_nonexistent_session(self, mock_app):
        client, mock_svc = mock_app
        mock_svc.delete_session = MagicMock(return_value=False)
        resp = client.delete("/api/sessions/nonexistent")
        assert resp.status_code == 404


class TestConfigEndpoints:
    """Test /api/status and /api/models endpoints."""

    def test_status(self, mock_app):
        client, _ = mock_app
        resp = client.get("/api/status")
        assert resp.status_code == 200
        data = resp.json()
        assert "model" in data
        assert "workspace" in data

    def test_list_models(self, mock_app):
        client, _ = mock_app
        resp = client.get("/api/models")
        assert resp.status_code == 200
        data = resp.json()
        assert "providers" in data
        assert "current_provider" in data
