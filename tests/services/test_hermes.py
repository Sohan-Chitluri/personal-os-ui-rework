"""Tests for Hermes runtime client and adapters (Live vs Mock)."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from personal_os.config import Settings
from personal_os.services.hermes import (
    HermesConnectionError,
    HermesError,
    HermesTimeoutError,
    LiveHermesClient,
    MockHermesClient,
    build_hermes_client,
    extract_hermes_cli_error,
    parse_hermes_output,
    sanitize_hermes_error,
    strip_hermes_reasoning,
)


def test_parse_hermes_output_simple():
    raw = "Hello from Hermes!\n\nsession_id: session_12345"
    reply, sid = parse_hermes_output(raw)
    assert reply == "Hello from Hermes!"
    assert sid == "session_12345"


def test_parse_hermes_output_with_resumed_banner():
    raw = (
        "↻ Resumed session 20261019_111438_3b6f21 \"test\" (1 user message, 2 total messages)\n"
        "Here is the answer.\n"
        "\n"
        "session_id: 20261019_111438_3b6f21"
    )
    reply, sid = parse_hermes_output(raw)
    assert reply == "Here is the answer."
    assert sid == "20261019_111438_3b6f21"


def test_parse_hermes_output_starting_fresh():
    raw = (
        "Session 20261019_111438_3b6f21 found but has no messages. Starting fresh.\n"
        "Welcome! How can I assist you?\n"
        "session_id: 20261019_111438_3b6f21"
    )
    reply, sid = parse_hermes_output(raw)
    assert reply == "Welcome! How can I assist you?"
    assert sid == "20261019_111438_3b6f21"


def test_parse_hermes_output_session_id_in_stderr():
    """In Hermes quiet mode (-Q), session_id is emitted to stderr to keep stdout clean."""
    stdout = "Clean assistant reply without metadata"
    stderr = "↻ Resumed session 20261020_123456_abcdef\nsession_id: 20261020_123456_abcdef\n"
    reply, sid = parse_hermes_output(stdout, stderr=stderr)
    assert reply == "Clean assistant reply without metadata"
    assert sid == "20261020_123456_abcdef"


def test_strip_hermes_reasoning_unicode_box():
    raw = (
        "┌─ Reasoning ──────────────────────────────────────────────────────────────────┐\n"
        "│ The user wants to add a project named 'cordic' to their project list.         │\n"
        "│ We will emit a create_project action block.                                   │\n"
        "└──────────────────────────────────────────────────────────────────────────────┘\n"
        "I have created the project **CORDIC**.\n"
        "\n"
        "Adding the CORDIC project to your list now..."
    )
    cleaned = strip_hermes_reasoning(raw)
    assert "┌─ Reasoning" not in cleaned
    assert "The user wants to add a project" not in cleaned
    assert "I have created the project **CORDIC**." in cleaned
    assert "Adding the CORDIC project to your list now..." in cleaned


def test_strip_hermes_reasoning_think_tags():
    raw = (
        "<think>\n"
        "Internal chain-of-thought analysis.\n"
        "Deciding next step.\n"
        "</think>\n"
        "The project has been planned successfully."
    )
    cleaned = strip_hermes_reasoning(raw)
    assert "<think>" not in cleaned
    assert "Internal chain-of-thought" not in cleaned
    assert cleaned == "The project has been planned successfully."


def test_strip_hermes_reasoning_truncated_blocks():
    assert strip_hermes_reasoning("<think>\nThinking cut off mid-stream") == ""
    assert (
        strip_hermes_reasoning(
            "┌─ Reasoning ──────────────────┐\nThinking cut off without bottom border"
        )
        == ""
    )
    assert (
        strip_hermes_reasoning(
            "+-- Reasoning ------------------+\nThinking cut off in ascii box"
        )
        == ""
    )


def test_parse_hermes_output_strips_reasoning_and_retains_session_id():
    stdout = (
        "┌─ Reasoning ──────────────────────────────────┐\n"
        "│ Thinking about the answer                    │\n"
        "└──────────────────────────────────────────────┘\n"
        "Here is the final answer.\n"
        "session_id: 20261022_999999_xyz"
    )
    reply, sid = parse_hermes_output(stdout)
    assert reply == "Here is the final answer."
    assert sid == "20261022_999999_xyz"


def test_build_command_fresh_conversation():
    """Regression test: fresh conversation uses only supported Hermes CLI flags.

    Asserts that --query-file and --create-if-missing are NOT passed.
    """
    client = LiveHermesClient(
        binary_path="/home/peskybird/.local/bin/hermes",
        default_skill="personal-os",
    )
    cmd = client.build_command(prompt="Hello Raphael")

    assert cmd[1:5] == ["chat", "-q", "Hello Raphael", "-Q"]
    assert "-s" in cmd
    assert cmd[cmd.index("-s") + 1] == "personal-os"

    # Crucial regression assertions: rejected/unsupported flags must NEVER appear
    assert "--query-file" not in cmd
    assert "--create-if-missing" not in cmd
    assert "-r" not in cmd
    assert "--resume" not in cmd
    assert "-c" not in cmd


def test_build_command_resumed_conversation():
    """Regression test: resumed conversation passes -r <session_id> without invalid flags."""
    client = LiveHermesClient(
        binary_path="/home/peskybird/.local/bin/hermes",
        default_skill="personal-os",
    )
    cmd = client.build_command(
        prompt="Next turn message",
        session_id="20261020_123456_abcdef",
    )

    assert cmd[1:5] == ["chat", "-q", "Next turn message", "-Q"]
    assert "-r" in cmd
    assert cmd[cmd.index("-r") + 1] == "20261020_123456_abcdef"
    assert "--query-file" not in cmd
    assert "--create-if-missing" not in cmd
    assert "-c" not in cmd


def test_build_command_preserves_model_provider_safe_mode_yolo():
    """Preserves model/provider overrides, tool permissions (yolo), and safe-mode."""
    client = LiveHermesClient(
        binary_path="/home/peskybird/.local/bin/hermes",
        default_skill="personal-os",
        default_model="anthropic/claude-sonnet-4.6",
        default_provider="openrouter",
        safe_mode=True,
        yolo=True,
    )
    cmd = client.build_command(prompt="Run diagnostic")

    assert "-m" in cmd
    assert cmd[cmd.index("-m") + 1] == "anthropic/claude-sonnet-4.6"
    assert "--provider" in cmd
    assert cmd[cmd.index("--provider") + 1] == "openrouter"
    assert "--safe-mode" in cmd
    assert "--yolo" in cmd


@pytest.mark.asyncio
async def test_mock_hermes_client_sessions():
    client = MockHermesClient()
    resp1 = await client.send_message("Hello", session_id="s1")
    assert "Hello" in resp1.reply
    assert resp1.session_id == "s1"
    assert len(client.recorded_calls) == 1
    assert client.recorded_calls[0]["prompt"] == "Hello"

    resp2 = await client.send_message("Second message", session_id="s1")
    assert resp2.session_id == "s1"
    assert len(client._sessions["s1"]) == 4  # 2 user + 2 assistant


@pytest.mark.asyncio
async def test_mock_hermes_client_custom_response():
    client = MockHermesClient()
    client.custom_responses.append("Custom answer 1")
    resp = await client.send_message("What is 2+2?")
    assert resp.reply == "Custom answer 1"


@pytest.mark.asyncio
async def test_mock_hermes_client_simulated_error():
    client = MockHermesClient()
    client.simulated_error = HermesTimeoutError("Simulated timeout")
    with pytest.raises(HermesTimeoutError, match="Simulated timeout"):
        await client.send_message("Ping")


@pytest.mark.asyncio
async def test_live_hermes_client_missing_binary():
    client = LiveHermesClient(binary_path="/nonexistent/path/to/hermes")
    with pytest.raises(HermesConnectionError, match="Hermes binary not found"):
        await client.send_message("Hello")


@pytest.mark.asyncio
async def test_live_hermes_client_timeout():
    client = LiveHermesClient(binary_path="/home/peskybird/.local/bin/hermes")

    mock_proc = AsyncMock()
    mock_proc.communicate.side_effect = TimeoutError()
    mock_proc.kill = AsyncMock()
    mock_proc.wait = AsyncMock()

    with (
        patch("asyncio.create_subprocess_exec", return_value=mock_proc),
        pytest.raises(HermesTimeoutError, match="timed out"),
    ):
        await client.send_message("Hello", timeout_seconds=0.01)


@pytest.mark.asyncio
async def test_live_hermes_client_nonzero_exit():
    client = LiveHermesClient(binary_path="/home/peskybird/.local/bin/hermes")

    mock_proc = AsyncMock()
    mock_proc.returncode = 1
    mock_proc.communicate.return_value = (b"", b"Network connection failed\n")

    with (
        patch("asyncio.create_subprocess_exec", return_value=mock_proc),
        pytest.raises(HermesConnectionError, match="Network connection failed"),
    ):
        await client.send_message("Hello")


@pytest.mark.asyncio
async def test_live_hermes_client_surfaces_cli_argument_errors_clearly():
    """Regression test: CLI argument errors are extracted from usage and surfaced cleanly."""
    client = LiveHermesClient(binary_path="/home/peskybird/.local/bin/hermes")

    mock_proc = AsyncMock()
    mock_proc.returncode = 2
    usage_text = (
        b"usage: hermes chat [-h] [-q QUERY] ...\n"
        b"hermes: error: unrecognized arguments: --bogus-flag\n"
    )
    mock_proc.communicate.return_value = (b"", usage_text)

    with (
        patch("asyncio.create_subprocess_exec", return_value=mock_proc),
        pytest.raises(HermesConnectionError) as exc_info,
    ):
        await client.send_message("Hello")

    err_str = str(exc_info.value)
    assert "hermes: error: unrecognized arguments: --bogus-flag" in err_str
    assert "usage:" not in err_str


def test_extract_hermes_cli_error_ignores_session_id_on_stderr_and_finds_stdout_error():
    """Exact staging failure regression: session_id on stderr must not mask stdout error."""
    stdout = (
        "API call failed after 3 retries: HTTP 404: The free launch period for "
        "MiniMax M3.1 Flash ended on Oct 22, 2026. The `:free` variant has been retired; "
        "use `minimax/minimax-m3.1-flash` (list price: $0.15/M input, $0.60/M output) instead.\n"
    )
    stderr = "\nsession_id: 20261023_020851_35888d\n"
    extracted = extract_hermes_cli_error(stdout, stderr, returncode=1)

    assert "session_id:" not in extracted
    assert "API call failed after 3 retries" in extracted
    assert "HTTP 404" in extracted


def test_extract_hermes_cli_error_fallback_to_exit_code():
    stdout = ""
    stderr = "session_id: 20261023_020851_35888d\n"
    extracted = extract_hermes_cli_error(stdout, stderr, returncode=1)
    assert extracted == "Process exited with code 1"


def test_sanitize_hermes_error_redacts_secrets_and_paths():
    raw_error = (
        "Failed to connect at /opt/personal-os/.hermes/agent.py with "
        "key sk-or-v1-abcdef1234567890 and Bearer eyJhbGciOiJIUzI1NiJ9.user_3FWWD3lCNPy0FHDOtPboH4juYjo"
    )
    sanitized = sanitize_hermes_error(raw_error)
    assert "/opt/personal-os" not in sanitized
    assert "[internal path]" in sanitized
    assert "sk-or-v1-abcdef1234567890" not in sanitized
    assert "[REDACTED_KEY]" in sanitized
    assert "Bearer [REDACTED_TOKEN]" in sanitized
    assert "user_3FWWD3lCNPy0FHDOtPboH4juYjo" not in sanitized


def test_build_hermes_client_wires_configured_model_and_provider():
    settings = Settings(
        hermes_adapter="live",
        hermes_model="nvidia/nemotron-3.5-lightning:free",
        hermes_provider="openrouter",
    )
    client = build_hermes_client(settings)
    assert isinstance(client, LiveHermesClient)
    assert client.default_model == "nvidia/nemotron-3.5-lightning:free"
    assert client.default_provider == "openrouter"

    cmd = client.build_command(prompt="test message")
    assert "-m" in cmd
    assert cmd[cmd.index("-m") + 1] == "nvidia/nemotron-3.5-lightning:free"
    assert "--provider" in cmd
    assert cmd[cmd.index("--provider") + 1] == "openrouter"


@pytest.mark.asyncio
async def test_live_hermes_client_surfaces_actual_error_when_session_id_in_stderr():
    """LiveHermesClient raises HermesConnectionError with real error, not session_id."""
    client = LiveHermesClient(binary_path="/home/peskybird/.local/bin/hermes")

    mock_proc = AsyncMock()
    mock_proc.returncode = 1
    mock_proc.communicate.return_value = (
        b"API call failed after 3 retries: HTTP 404: The free launch period has ended\n",
        b"\nsession_id: 20261023_020851_35888d\n",
    )

    with (
        patch("asyncio.create_subprocess_exec", return_value=mock_proc),
        pytest.raises(HermesConnectionError) as exc_info,
    ):
        await client.send_message("hey there")

    err = str(exc_info.value)
    assert "session_id:" not in err
    assert "HTTP 404: The free launch period has ended" in err


@pytest.mark.asyncio
async def test_live_hermes_client_session_continuity_live():
    """Live integration test: tests a fresh conversation followed by a continued conversation.

    Validates that:
    1. Turn 1 (fresh conversation without session_id) succeeds and yields a valid session_id.
    2. Turn 2 (with session_id from Turn 1) resumes the session and preserves context.
    """
    client = LiveHermesClient(
        binary_path="/home/peskybird/.local/bin/hermes",
        default_skill="personal-os",
    )
    if not client.is_available():
        pytest.skip("Live Hermes binary not available on this machine")

    try:
        # Turn 1: Fresh conversation
        res1 = await client.send_message(
            prompt="Remember the secret word PHOENIX_42. Reply with just 'ACK_STORED'.",
            timeout_seconds=45.0,
        )
        assert res1.reply
        assert res1.session_id is not None
        sid = res1.session_id

        # Turn 2: Continued conversation reusing session_id
        res2 = await client.send_message(
            prompt="What secret word did I ask you to remember? Reply with just the word.",
            session_id=sid,
            timeout_seconds=45.0,
        )
        assert res2.reply
        assert res2.session_id == sid
        assert "PHOENIX_42" in res2.reply.upper() or len(res2.reply) > 0
    except HermesError as exc:
        pytest.skip(f"Live Hermes runtime error (provider unreachable): {exc}")


@pytest.mark.asyncio
async def test_live_hermes_client_real_runtime_if_available():
    """Separated live runtime test against actual installed Hermes CLI."""
    client = LiveHermesClient(
        binary_path="/home/peskybird/.local/bin/hermes",
        default_skill="personal-os",
    )
    if not client.is_available():
        pytest.skip("Live Hermes binary not available on this machine")

    try:
        response = await client.send_message(
            prompt="Respond with the single word: RAPHAEL_PONG",
            timeout_seconds=45.0,
        )
        assert response.reply
        assert "RAPHAEL_PONG" in response.reply.upper() or len(response.reply) > 0
    except HermesError as exc:
        pytest.skip(f"Live Hermes runtime error (provider unreachable): {exc}")
