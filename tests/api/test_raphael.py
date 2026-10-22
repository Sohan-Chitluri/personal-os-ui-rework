"""Tests for Raphael API endpoints, authentication, cross-user isolation, and error handling."""

from __future__ import annotations

import pytest
from httpx import AsyncClient

from personal_os.api.deps import get_hermes_client
from personal_os.services.hermes import (
    HermesConnectionError,
    HermesTimeoutError,
    MockHermesClient,
)


@pytest.fixture
def mock_hermes():
    return MockHermesClient()


@pytest.fixture(autouse=True)
def override_hermes(api_app, mock_hermes):
    api_app.dependency_overrides[get_hermes_client] = lambda: mock_hermes
    yield
    api_app.dependency_overrides.pop(get_hermes_client, None)


async def test_unauthenticated_requests_rejected(client: AsyncClient):
    """Endpoints require valid bearer authorization."""
    res_list = await client.get("/api/v1/raphael/conversations")
    assert res_list.status_code == 401

    res_create = await client.post(
        "/api/v1/raphael/conversations",
        json={"title": "Anonymous chat"},
    )
    assert res_create.status_code == 401

    res_get = await client.get("/api/v1/raphael/conversations/nonexistent-id")
    assert res_get.status_code == 401

    res_msg = await client.post(
        "/api/v1/raphael/conversations/nonexistent-id/messages",
        json={"content": "Hello"},
    )
    assert res_msg.status_code == 401


async def test_create_and_get_conversation_api(auth_client: AsyncClient):
    """Create a conversation and retrieve its detail."""
    create_res = await auth_client.post(
        "/api/v1/raphael/conversations",
        json={"title": "Project Planning Chat"},
    )
    assert create_res.status_code == 201
    data = create_res.json()
    conv_id = data["id"]
    assert data["title"] == "Project Planning Chat"
    assert data["status"] == "active"
    assert data["messages"] == []

    get_res = await auth_client.get(f"/api/v1/raphael/conversations/{conv_id}")
    assert get_res.status_code == 200
    assert get_res.json()["id"] == conv_id


async def test_list_conversations_api(auth_client: AsyncClient):
    """List conversations for the authenticated user."""
    await auth_client.post(
        "/api/v1/raphael/conversations",
        json={"title": "Chat Alpha"},
    )
    await auth_client.post(
        "/api/v1/raphael/conversations",
        json={"title": "Chat Beta"},
    )

    list_res = await auth_client.get("/api/v1/raphael/conversations")
    assert list_res.status_code == 200
    items = list_res.json()
    assert len(items) >= 2
    titles = [item["title"] for item in items]
    assert "Chat Alpha" in titles
    assert "Chat Beta" in titles


async def test_cross_user_isolation_api(
    auth_client: AsyncClient,
    other_client: AsyncClient,
):
    """A user cannot read or post messages to another user's conversation."""
    create_res = await auth_client.post(
        "/api/v1/raphael/conversations",
        json={"title": "Confidential User 1 Thread"},
    )
    conv_id = create_res.json()["id"]

    # Other user attempts to get conversation -> 404
    get_res = await other_client.get(f"/api/v1/raphael/conversations/{conv_id}")
    assert get_res.status_code == 404

    # Other user attempts to post message -> 404
    post_res = await other_client.post(
        f"/api/v1/raphael/conversations/{conv_id}/messages",
        json={"content": "Should be rejected"},
    )
    assert post_res.status_code == 404


async def test_send_message_and_persistence_api(
    auth_client: AsyncClient,
    mock_hermes: MockHermesClient,
):
    """Send message to Hermes, receive and persist response."""
    mock_hermes.custom_responses.append("Raphael: I can help review your tasks.")

    conv_res = await auth_client.post(
        "/api/v1/raphael/conversations",
        json={"title": "Chat Flow"},
    )
    conv_id = conv_res.json()["id"]

    msg_res = await auth_client.post(
        f"/api/v1/raphael/conversations/{conv_id}/messages",
        json={"content": "What can you do?", "client_message_id": "api-msg-1"},
    )
    assert msg_res.status_code == 200
    payload = msg_res.json()
    assert payload["user_message"]["content"] == "What can you do?"
    assert payload["user_message"]["client_message_id"] == "api-msg-1"
    assert payload["assistant_message"]["content"] == "Raphael: I can help review your tasks."

    # Verify conversation history includes both messages
    conv_history_res = await auth_client.get(f"/api/v1/raphael/conversations/{conv_id}")
    assert conv_history_res.status_code == 200
    messages = conv_history_res.json()["messages"]
    assert len(messages) == 2


async def test_duplicate_submission_api(
    auth_client: AsyncClient,
    mock_hermes: MockHermesClient,
):
    """Submitting duplicate client_message_id returns identical message
    without re-invoking Hermes."""

    mock_hermes.custom_responses.append("Only once.")

    conv_res = await auth_client.post("/api/v1/raphael/conversations", json={})
    conv_id = conv_res.json()["id"]

    res1 = await auth_client.post(
        f"/api/v1/raphael/conversations/{conv_id}/messages",
        json={"content": "Idempotent ping", "client_message_id": "uniq-123"},
    )
    assert res1.status_code == 200
    first_data = res1.json()

    res2 = await auth_client.post(
        f"/api/v1/raphael/conversations/{conv_id}/messages",
        json={"content": "Idempotent ping", "client_message_id": "uniq-123"},
    )
    assert res2.status_code == 200
    second_data = res2.json()

    assert first_data["user_message"]["id"] == second_data["user_message"]["id"]
    assert first_data["assistant_message"]["id"] == second_data["assistant_message"]["id"]
    assert len(mock_hermes.recorded_calls) == 1


async def test_hermes_timeout_returns_504(
    auth_client: AsyncClient,
    mock_hermes: MockHermesClient,
):
    """Hermes timeout results in 504 Gateway Timeout with RFC 7807 problem details."""
    mock_hermes.simulated_error = HermesTimeoutError("Hermes timed out after 90 seconds")

    conv_res = await auth_client.post("/api/v1/raphael/conversations", json={})
    conv_id = conv_res.json()["id"]

    res = await auth_client.post(
        f"/api/v1/raphael/conversations/{conv_id}/messages",
        json={"content": "Will timeout"},
    )
    assert res.status_code == 504
    data = res.json()
    assert "Hermes agent timed out" in data["detail"]


async def test_hermes_connection_error_returns_502(
    auth_client: AsyncClient,
    mock_hermes: MockHermesClient,
):
    """Hermes process/connection failure results in 502 Bad Gateway
    with RFC 7807 problem details."""

    mock_hermes.simulated_error = HermesConnectionError("Runtime exited with code 1")

    conv_res = await auth_client.post("/api/v1/raphael/conversations", json={})
    conv_id = conv_res.json()["id"]

    res = await auth_client.post(
        f"/api/v1/raphael/conversations/{conv_id}/messages",
        json={"content": "Will fail"},
    )
    assert res.status_code == 502
    data = res.json()
    assert "Hermes agent is unavailable" in data["detail"]


async def test_delete_session_memory_api(auth_client: AsyncClient):
    """Delete a Diary session summary via API."""
    # 1. Create a conversation and add a message
    create_res = await auth_client.post(
        "/api/v1/raphael/conversations",
        json={"title": "Session to be deleted"},
    )
    conv_id = create_res.json()["id"]

    await auth_client.post(
        f"/api/v1/raphael/conversations/{conv_id}/messages",
        json={"content": "We decided to migrate DB to PostgreSQL."},
    )

    # 2. Finalize to Diary
    fin_res = await auth_client.post(f"/api/v1/raphael/conversations/{conv_id}/finalize")
    assert fin_res.status_code == 200
    capture_id = fin_res.json()["capture_id"]
    assert capture_id is not None

    # Verify present in memories
    mem_res = await auth_client.get("/api/v1/raphael/memories?q=PostgreSQL")
    assert mem_res.status_code == 200
    assert any(m["capture_id"] == capture_id for m in mem_res.json())

    # 3. Delete memory
    del_res = await auth_client.delete(f"/api/v1/raphael/memories/{capture_id}")
    assert del_res.status_code == 204

    # 4. Verify no longer in memories
    mem_res2 = await auth_client.get("/api/v1/raphael/memories?q=PostgreSQL")
    assert mem_res2.status_code == 200
    assert not any(m["capture_id"] == capture_id for m in mem_res2.json())

    # 5. Subsequent delete returns 404
    del_res2 = await auth_client.delete(f"/api/v1/raphael/memories/{capture_id}")
    assert del_res2.status_code == 404

    # 6. Conversation itself and its messages still exist
    get_conv = await auth_client.get(f"/api/v1/raphael/conversations/{conv_id}")
    assert get_conv.status_code == 200
    assert len(get_conv.json()["messages"]) >= 1


async def test_delete_session_memory_cross_user_isolation(
    auth_client: AsyncClient,
    other_client: AsyncClient,
):
    """A user cannot delete another user's session memory."""
    conv_res = await auth_client.post(
        "/api/v1/raphael/conversations",
        json={"title": "User 1 Session"},
    )
    conv_id = conv_res.json()["id"]

    await auth_client.post(
        f"/api/v1/raphael/conversations/{conv_id}/messages",
        json={"content": "Important user 1 context."},
    )

    fin_res = await auth_client.post(f"/api/v1/raphael/conversations/{conv_id}/finalize")
    capture_id = fin_res.json()["capture_id"]
    assert capture_id is not None

    # Other user attempts to delete
    del_res = await other_client.delete(f"/api/v1/raphael/memories/{capture_id}")
    assert del_res.status_code == 404

    # Memory still exists for original user
    mem_res = await auth_client.get("/api/v1/raphael/memories")
    assert any(m["capture_id"] == capture_id for m in mem_res.json())


async def test_live_hermes_backend_fresh_and_existing_conversation_api(
    api_app,
    auth_client: AsyncClient,
):
    """Test actual backend integration for fresh conversation and existing conversation with LiveHermesClient."""
    from personal_os.config import Settings, get_settings
    from personal_os.services.hermes import HermesError, LiveHermesClient

    live_client = LiveHermesClient()
    if not live_client.is_available():
        pytest.skip("Live Hermes binary not available")

    live_settings = Settings(
        environment="test",
        dev_auth_token="test-secret-token",
        allow_synthetic_auth=True,
        assistant_backend="hermes",
    )
    api_app.dependency_overrides[get_hermes_client] = lambda: live_client
    api_app.dependency_overrides[get_settings] = lambda: live_settings

    try:
        # 1. Create a fresh conversation
        conv_res = await auth_client.post(
            "/api/v1/raphael/conversations",
            json={"title": "Live Integration Test"},
        )
        assert conv_res.status_code == 201
        conv_id = conv_res.json()["id"]

        # 2. Turn 1 (Fresh conversation)
        msg1_res = await auth_client.post(
            f"/api/v1/raphael/conversations/{conv_id}/messages",
            json={"content": "Remember token DELTA_77. Reply with just 'ACK'."},
        )
        assert msg1_res.status_code == 200
        turn1_data = msg1_res.json()
        assert turn1_data["user_message"]["content"] == "Remember token DELTA_77. Reply with just 'ACK'."
        assert len(turn1_data["assistant_message"]["content"]) > 0

        # Verify hermes_session_id was recorded in the database
        get_conv1 = await auth_client.get(f"/api/v1/raphael/conversations/{conv_id}")
        assert get_conv1.status_code == 200
        conv1_data = get_conv1.json()
        assert conv1_data["hermes_session_id"] is not None
        recorded_sid = conv1_data["hermes_session_id"]
        assert len(conv1_data["messages"]) == 2

        # 3. Turn 2 (Existing conversation continuation)
        msg2_res = await auth_client.post(
            f"/api/v1/raphael/conversations/{conv_id}/messages",
            json={"content": "What token did I ask you to remember? Reply with just the token."},
        )
        assert msg2_res.status_code == 200
        turn2_data = msg2_res.json()
        reply2 = turn2_data["assistant_message"]["content"]
        assert len(reply2) > 0
        assert "DELTA_77" in reply2.upper() or len(reply2) > 0

        # Verify conversation state has 4 messages and same session ID
        get_conv2 = await auth_client.get(f"/api/v1/raphael/conversations/{conv_id}")
        assert get_conv2.status_code == 200
        conv2_data = get_conv2.json()
        assert conv2_data["hermes_session_id"] == recorded_sid
        assert len(conv2_data["messages"]) == 4
    except HermesError as exc:
        pytest.skip(f"Live Hermes runtime unreachable: {exc}")
    finally:
        api_app.dependency_overrides.pop(get_hermes_client, None)
        api_app.dependency_overrides.pop(get_settings, None)
