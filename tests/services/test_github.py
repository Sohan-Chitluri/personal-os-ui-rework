"""Tests for GitHub integration, encrypted credential storage, repository association, and context extraction."""

from __future__ import annotations

import base64
from unittest.mock import AsyncMock, patch

import httpx
import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from personal_os.config import Settings
from personal_os.db.models import Project, User
from personal_os.services.github import (
    GitHubAuthError,
    GitHubClient,
    GitHubService,
    extract_readme_brief,
)
from personal_os.services.interfaces import ServiceNotFound


def test_extract_readme_brief_full():
    """Verify README parser extracts purpose, architecture stack, setup, capabilities, and limitations."""
    readme_sample = """# CORDIC Accelerator Core

[![Build Status](https://img.shields.io/travis/user/repo.svg)](https://travis-ci.org/user/repo)

High-performance pipelined CORDIC hardware accelerator core for trigonometric calculations.
Designed for FPGA and ASIC synthesis with configurable word lengths and rotation modes.

## Tech Stack
Built using Verilog, SystemVerilog, and Python testbenches with Docker and PyTorch validation.

## Features
- Pipelined architecture with 16-stage rotation
- Configurable vectoring and rotation modes
- Full fixed-point precision with automated rounding
- Low-power clock gating

## Getting Started
Run tests using:
```bash
make test
pytest tests/
```

## Limitations
- Only supports 16-bit and 32-bit fixed point formats currently
- Hyperbolic coordinates mode is work in progress
"""
    brief = extract_readme_brief(readme_sample)
    assert "High-performance pipelined CORDIC" in brief["purpose"]
    assert "FPGA" in [s.upper() for s in brief["architecture_or_stack"]]
    assert "VERILOG" in [s.upper() for s in brief["architecture_or_stack"]]
    assert len(brief["key_capabilities"]) >= 3
    assert any("Pipelined architecture" in c for c in brief["key_capabilities"])
    assert len(brief["limitations"]) >= 1
    assert any("16-bit" in lim for lim in brief["limitations"])
    assert brief["setup"] is not None


def test_extract_readme_brief_empty():
    """Verify README parser returns safe defaults on empty or minimal content."""
    brief = extract_readme_brief("")
    assert brief["purpose"] == "No README documentation available."
    assert brief["architecture_or_stack"] == []
    assert brief["key_capabilities"] == []
    assert brief["limitations"] == []


@pytest.mark.asyncio
async def test_github_auth_lifecycle(db_session: AsyncSession, test_user: User):
    """Verify GitHub auth connection, status without token leak, and disconnection."""
    settings = Settings(
        environment="test",
        github_public_username=None,
        github_token_encryption_key="test-encryption-key-for-unit-tests",
    )
    service = GitHubService(session=db_session, settings=settings)

    # 1. Status initially disconnected
    status = await service.get_auth_status(test_user.id)
    assert status["connected"] is False
    assert status["username"] is None

    # 2. Connect token (mocking GitHub API user call)
    with patch.object(
        GitHubClient,
        "get_authenticated_user",
        new=AsyncMock(return_value={"id": "12345", "login": "testdev", "name": "Test Dev"}),
    ):
        conn_res = await service.connect_token(test_user.id, "ghp_validtesttoken12345", scopes="repo,read:org")
        assert conn_res["connected"] is True
        assert conn_res["username"] == "testdev"

    # 3. Status now connected; token is NOT in output
    status_after = await service.get_auth_status(test_user.id)
    assert status_after["connected"] is True
    assert status_after["username"] == "testdev"
    assert "ghp_" not in str(status_after)
    assert "token" not in status_after

    # 4. Decrypt token internally for client
    client = await service.get_client(test_user.id)
    assert client.token == "ghp_validtesttoken12345"

    # 5. Disconnect
    disconnected = await service.disconnect(test_user.id)
    assert disconnected is True
    status_disc = await service.get_auth_status(test_user.id)
    assert status_disc["connected"] is False

    with pytest.raises(GitHubAuthError):
        await service.get_client(test_user.id)


@pytest.mark.asyncio
async def test_project_repository_association(db_session: AsyncSession, test_user: User):
    """Verify linking a repository to a project, context caching, and isolation."""
    settings = Settings(
        environment="test",
        github_public_username=None,
        github_token_encryption_key="test-encryption-key-for-unit-tests",
    )
    service = GitHubService(session=db_session, settings=settings)

    # Create a project
    project = Project(owner_id=test_user.id, name="CORDIC Project", status="active")
    db_session.add(project)
    await db_session.commit()
    await db_session.refresh(project)

    # Connect user GitHub
    with patch.object(
        GitHubClient,
        "get_authenticated_user",
        new=AsyncMock(return_value={"id": "99", "login": "alice"}),
    ):
        await service.connect_token(test_user.id, "ghp_token_mock")

    sample_readme = "# CORDIC\nPipelined hardware accelerator."
    mock_repo_data = {
        "id": "1001",
        "name": "cordic-accelerator",
        "full_name": "alice/cordic-accelerator",
        "owner": "alice",
        "html_url": "https://github.com/alice/cordic-accelerator",
        "default_branch": "main",
    }

    with (
        patch.object(GitHubClient, "get_repository", new=AsyncMock(return_value=mock_repo_data)),
        patch.object(GitHubClient, "get_readme", new=AsyncMock(return_value=(sample_readme, "sha123"))),
        patch.object(GitHubClient, "list_milestones", new=AsyncMock(return_value=[{"id": "1", "title": "v1.0 Release"}])),
        patch.object(GitHubClient, "list_issues", new=AsyncMock(return_value=[{"id": "10", "title": "Fix overflow in mode 2"}])),
        patch.object(GitHubClient, "list_commits", new=AsyncMock(return_value=[{"sha": "a1b2c3d", "message": "Initial commit"}])),
    ):
        # Associate repo
        assoc = await service.associate_project_repository(
            owner_id=test_user.id,
            project_id=project.id,
            repo_full_name="alice/cordic-accelerator",
        )
        assert assoc.project_id == project.id
        assert assoc.full_name == "alice/cordic-accelerator"
        assert assoc.project_brief is not None
        assert "Pipelined hardware accelerator" in assoc.project_brief["purpose"]

        # Fetch context
        ctx = await service.get_repository_context(test_user.id, project.id)
        assert ctx is not None
        assert ctx["repository"]["full_name"] == "alice/cordic-accelerator"
        assert len(ctx["milestones"]) == 1
        assert len(ctx["issues"]) == 1
        assert len(ctx["commits"]) == 1

        # Sync repository
        synced = await service.sync_project_repository(test_user.id, project.id)
        assert synced.last_synced_at is not None

        # Disconnect repo
        removed = await service.disconnect_project_repository(test_user.id, project.id)
        assert removed is True
        assert await service.get_project_repository(test_user.id, project.id) is None


@pytest.mark.asyncio
async def test_project_repository_user_isolation(db_session: AsyncSession, test_user: User):
    """User B cannot associate or access User A's project repositories."""
    settings = Settings(
        environment="test",
        github_public_username=None,
        github_token_encryption_key="test-encryption-key-for-unit-tests",
    )
    service = GitHubService(session=db_session, settings=settings)

    project = Project(owner_id=test_user.id, name="User A Project", status="active")
    db_session.add(project)
    await db_session.commit()
    await db_session.refresh(project)

    # Attempt association by unauthorized user
    with pytest.raises(ServiceNotFound):
        await service.associate_project_repository(
            owner_id="unauthorized-user-id",
            project_id=project.id,
            repo_full_name="alice/cordic-accelerator",
        )


@pytest.mark.asyncio
async def test_oauth_authorization_url_generation_and_state(
    db_session: AsyncSession, test_user: User
):
    """Verify authorization URL generation, CSRF state persistence, and configuration guard."""
    # 1. When credentials are not configured, raises clear error
    unconfigured_settings = Settings(environment="test", github_client_id=None, github_client_secret=None)
    service_unconfigured = GitHubService(session=db_session, settings=unconfigured_settings)
    assert service_unconfigured.is_configured() is False

    with pytest.raises(Exception) as exc_info:
        await service_unconfigured.create_authorization_url(test_user.id)
    assert "GITHUB_CLIENT_ID" in str(exc_info.value)

    # 2. When configured, returns URL with client_id and state
    configured_settings = Settings(
        environment="test",
        github_client_id="test-client-id-xyz",
        github_client_secret="test-client-secret-123",
        github_token_encryption_key="test-key",
    )
    service = GitHubService(session=db_session, settings=configured_settings)
    assert service.is_configured() is True

    auth_url, state = await service.create_authorization_url(
        user_id=test_user.id,
        return_to="/projects/fpga",
    )
    assert "github.com/login/oauth/authorize" in auth_url
    assert "client_id=test-client-id-xyz" in auth_url
    assert f"state={state}" in auth_url

    # 3. State is persisted and can be validated
    oauth_record = await service.validate_and_consume_state(state, expected_user_id=test_user.id)
    assert oauth_record.user_id == test_user.id
    assert oauth_record.return_to == "/projects/fpga"
    assert oauth_record.used is True

    # 4. Replay attack: consuming the same state again fails
    with pytest.raises(GitHubAuthError) as replay_exc:
        await service.validate_and_consume_state(state, expected_user_id=test_user.id)
    assert "already consumed" in str(replay_exc.value).lower()


@pytest.mark.asyncio
async def test_oauth_callback_complete_and_token_refresh(
    db_session: AsyncSession, test_user: User
):
    """Verify OAuth code exchange, secure token storage, and automatic token refresh."""
    from datetime import UTC, datetime, timedelta

    settings = Settings(
        environment="test",
        github_client_id="client-123",
        github_client_secret="secret-456",
        github_token_encryption_key="test-encryption-key",
    )
    service = GitHubService(session=db_session, settings=settings)

    # 1. Initiate authorization
    _, state = await service.create_authorization_url(test_user.id)

    # 2. Complete callback with mock GitHub exchange
    mock_token_data = {
        "access_token": "ghu_access_mock_123",
        "refresh_token": "ghr_refresh_mock_456",
        "expires_in": 3600,
        "scope": "repo,read:user",
    }
    with (
        patch.object(service, "exchange_code_for_token", new=AsyncMock(return_value=mock_token_data)),
        patch.object(
            GitHubClient,
            "get_authenticated_user",
            new=AsyncMock(return_value={"id": "500", "login": "octocat-dev"}),
        ),
    ):
        res = await service.complete_oauth_callback(
            code="mock-github-auth-code",
            state=state,
            user_id=test_user.id,
        )
        assert res["connected"] is True
        assert res["username"] == "octocat-dev"

    # Verify status reports connected
    status = await service.get_auth_status(test_user.id)
    assert status["connected"] is True
    assert status["username"] == "octocat-dev"

    # 3. Simulate token expiration and verify automatic refresh
    from personal_os.db.models import UserGitHubAuth
    from sqlalchemy import select

    auth_stmt = select(UserGitHubAuth).where(UserGitHubAuth.user_id == test_user.id)
    auth_res = await db_session.execute(auth_stmt)
    auth_record = auth_res.scalar_one()

    # Artificially set expiry in the past
    auth_record.token_expires_at = datetime.now(UTC) - timedelta(minutes=5)
    db_session.add(auth_record)
    await db_session.commit()

    # Mock refresh call returning new access token
    mock_refresh_response = {
        "access_token": "ghu_refreshed_access_789",
        "refresh_token": "ghr_new_refresh_012",
        "expires_in": 7200,
    }
    with patch.object(
        service,
        "exchange_code_for_token",
        new=AsyncMock(return_value=mock_refresh_response),
    ):
        # When get_client is called, it automatically refreshes the expired token
        with patch.object(service, "refresh_user_token", new=AsyncMock(return_value="ghu_refreshed_access_789")) as refresh_spy:
            client = await service.get_client(test_user.id)
            assert client.token == "ghu_refreshed_access_789"
            assert refresh_spy.called


@pytest.mark.asyncio
async def test_public_only_mode_discovery_and_association(db_session: AsyncSession, test_user: User):
    """Verify public-only mode: discovers public repos, filters private, auto-prefixes, and associates."""
    from personal_os.services.github import GitHubNotFoundError

    settings = Settings(
        environment="test",
        github_public_username="Sohan-Chitluri",
        github_token_encryption_key="test-encryption-key",
    )
    service = GitHubService(session=db_session, settings=settings)

    # 1. Verification of configuration
    assert service.is_public_only_configured() is True
    assert service.is_configured() is False

    # 2. Auth status reports public_only mode with username Sohan-Chitluri
    status = await service.get_auth_status(test_user.id)
    assert status["connected"] is True
    assert status["public_only"] is True
    assert status["mode"] == "public_only"
    assert status["username"] == "Sohan-Chitluri"
    assert status["scopes"] == []

    # 3. Client constructed in public-only mode without token
    client = await service.get_client(test_user.id)
    assert client.is_public_only is True
    assert client.token is None
    assert client.public_username == "Sohan-Chitluri"

    # 4. Mock GitHub unauthenticated REST response for /users/Sohan-Chitluri/repos
    mock_public_repos = [
        {
            "id": 101,
            "name": "personal-os-ui-rework",
            "full_name": "Sohan-Chitluri/personal-os-ui-rework",
            "owner": {"login": "Sohan-Chitluri"},
            "description": "Personal OS frontend rework",
            "html_url": "https://github.com/Sohan-Chitluri/personal-os-ui-rework",
            "default_branch": "main",
            "private": False,
        },
        {
            "id": 102,
            "name": "secret-notes",
            "full_name": "Sohan-Chitluri/secret-notes",
            "owner": {"login": "Sohan-Chitluri"},
            "description": "Private repository",
            "html_url": "https://github.com/Sohan-Chitluri/secret-notes",
            "default_branch": "main",
            "private": True,  # Must be filtered out in public mode
        },
    ]

    with patch.object(GitHubClient, "_request", new=AsyncMock(return_value=mock_public_repos)):
        repos = await service.list_repositories(test_user.id)
        assert len(repos) == 1
        assert repos[0]["name"] == "personal-os-ui-rework"
        assert repos[0]["private"] is False

    # 5. Associate repository using short name (auto-prefix)
    project = Project(owner_id=test_user.id, name="Personal OS Project", status="active")
    db_session.add(project)
    await db_session.commit()
    await db_session.refresh(project)

    mock_repo_data = {
        "id": "101",
        "name": "personal-os-ui-rework",
        "full_name": "Sohan-Chitluri/personal-os-ui-rework",
        "owner": {"login": "Sohan-Chitluri"},
        "description": "Personal OS frontend rework",
        "html_url": "https://github.com/Sohan-Chitluri/personal-os-ui-rework",
        "default_branch": "main",
        "private": False,
    }
    sample_readme = "# Personal OS UI\nComprehensive UI rework for Personal OS."

    with (
        patch.object(GitHubClient, "_request", new=AsyncMock(side_effect=[
            mock_repo_data,  # get_repository
            {"content": base64.b64encode(sample_readme.encode()).decode(), "encoding": "base64", "sha": "sha-ui-101"},  # get_readme
            [{"id": 1, "number": 1, "title": "Beta Launch", "open_issues": 2, "closed_issues": 1, "state": "open"}],  # milestones
            [{"id": 2, "number": 12, "title": "Fix alignment", "state": "open", "labels": []}],  # issues
            [{"sha": "abcdef123456", "commit": {"message": "Update layout", "author": {"name": "Sohan", "date": "2026-10-22"}}}],  # commits
        ]))
    ):
        assoc = await service.associate_project_repository(
            owner_id=test_user.id,
            project_id=project.id,
            repo_full_name="personal-os-ui-rework",  # Short name without prefix
        )
        assert assoc.full_name == "Sohan-Chitluri/personal-os-ui-rework"
        assert assoc.project_brief is not None
        assert "Personal OS" in assoc.project_brief["purpose"]

        ctx = await service.get_repository_context(test_user.id, project.id)
        assert ctx is not None
        assert ctx["repository"]["full_name"] == "Sohan-Chitluri/personal-os-ui-rework"
        assert len(ctx["milestones"]) == 1
        assert len(ctx["issues"]) == 1
        assert len(ctx["commits"]) == 1


@pytest.mark.asyncio
async def test_public_only_mode_private_repo_rejected(db_session: AsyncSession, test_user: User):
    """Verify public-only mode rejects private repositories with GitHubNotFoundError."""
    from personal_os.services.github import GitHubNotFoundError

    settings = Settings(
        environment="test",
        github_public_username="Sohan-Chitluri",
        github_token_encryption_key="test-encryption-key",
    )
    service = GitHubService(session=db_session, settings=settings)

    project = Project(owner_id=test_user.id, name="Private Check Project", status="active")
    db_session.add(project)
    await db_session.commit()
    await db_session.refresh(project)

    mock_private_repo = {
        "id": "999",
        "name": "private-code",
        "full_name": "Sohan-Chitluri/private-code",
        "owner": {"login": "Sohan-Chitluri"},
        "private": True,
    }

    with patch.object(GitHubClient, "_request", new=AsyncMock(return_value=mock_private_repo)):
        with pytest.raises(GitHubNotFoundError) as exc:
            await service.associate_project_repository(
                owner_id=test_user.id,
                project_id=project.id,
                repo_full_name="private-code",
            )
        assert "private or inaccessible in public-only mode" in str(exc.value)


@pytest.mark.asyncio
async def test_public_only_mode_rate_limiting_and_graceful_degradation(
    db_session: AsyncSession, test_user: User
):
    """Verify rate limit error handling and that repository context falls back to cached brief."""
    from personal_os.services.github import GitHubRateLimitError

    settings = Settings(
        environment="test",
        github_public_username="Sohan-Chitluri",
        github_token_encryption_key="test-encryption-key",
    )
    service = GitHubService(session=db_session, settings=settings)

    client = await service.get_client(test_user.id)

    # 1. Rate limit error raised on client call
    with patch("httpx.AsyncClient.request") as mock_http:
        mock_resp = AsyncMock()
        mock_resp.status_code = 403
        mock_resp.text = "API rate limit exceeded for 127.0.0.1"
        mock_http.return_value = mock_resp

        with pytest.raises(GitHubRateLimitError):
            await client.list_repositories()

    # 2. Context retrieval degrades gracefully when live metrics fail
    project = Project(owner_id=test_user.id, name="Rate Limit Project", status="active")
    db_session.add(project)
    await db_session.commit()
    await db_session.refresh(project)

    # Pre-associate with cached brief
    from personal_os.db.models import ProjectRepository
    from datetime import UTC, datetime
    cached_repo = ProjectRepository(
        project_id=project.id,
        owner_id=test_user.id,
        repo_id="101",
        repo_owner="Sohan-Chitluri",
        repo_name="personal-os-ui-rework",
        full_name="Sohan-Chitluri/personal-os-ui-rework",
        default_branch="main",
        html_url="https://github.com/Sohan-Chitluri/personal-os-ui-rework",
        project_brief={"purpose": "Cached brief when rate limited"},
        last_synced_at=datetime.now(UTC),
    )
    db_session.add(cached_repo)
    await db_session.commit()

    # Live call fails with rate limit
    with patch.object(GitHubClient, "list_milestones", side_effect=GitHubRateLimitError("Rate limited")):
        ctx = await service.get_repository_context(test_user.id, project.id)
        assert ctx is not None
        assert ctx["repository"]["full_name"] == "Sohan-Chitluri/personal-os-ui-rework"
        assert ctx["project_brief"]["purpose"] == "Cached brief when rate limited"
        # Milestones should be empty list, not crash
        assert ctx["milestones"] == []


@pytest.mark.asyncio
async def test_oauth_precedence_and_fallback_to_public(db_session: AsyncSession, test_user: User):
    """Verify OAuth auth takes precedence over public-only, and disconnecting falls back to public-only."""
    settings = Settings(
        environment="test",
        github_public_username="Sohan-Chitluri",
        github_client_id="app-client-id",
        github_client_secret="app-client-secret",
        github_token_encryption_key="test-encryption-key",
    )
    service = GitHubService(session=db_session, settings=settings)

    # 1. Initially reports public_only mode with configured=True (App credentials available for upgrade)
    status_initial = await service.get_auth_status(test_user.id)
    assert status_initial["connected"] is True
    assert status_initial["public_only"] is True
    assert status_initial["mode"] == "public_only"
    assert status_initial["username"] == "Sohan-Chitluri"
    assert status_initial["configured"] is True

    # 2. User authorizes OAuth token
    with patch.object(
        GitHubClient,
        "get_authenticated_user",
        new=AsyncMock(return_value={"id": "555", "login": "sohan-oauth"}),
    ):
        await service.connect_token(test_user.id, "ghp_oauth_token_val")

    # Status is now authenticated (OAuth takes precedence)
    status_auth = await service.get_auth_status(test_user.id)
    assert status_auth["connected"] is True
    assert status_auth["public_only"] is False
    assert status_auth["mode"] == "authenticated"
    assert status_auth["username"] == "sohan-oauth"

    client_auth = await service.get_client(test_user.id)
    assert client_auth.is_public_only is False
    assert client_auth.token == "ghp_oauth_token_val"

    # 3. User disconnects OAuth -> falls back to public_only mode seamlessly
    disc = await service.disconnect(test_user.id)
    assert disc is True

    status_fallback = await service.get_auth_status(test_user.id)
    assert status_fallback["connected"] is True
    assert status_fallback["public_only"] is True
    assert status_fallback["mode"] == "public_only"
    assert status_fallback["username"] == "Sohan-Chitluri"

    client_fallback = await service.get_client(test_user.id)
    assert client_fallback.is_public_only is True
    assert client_fallback.token is None
    assert client_fallback.public_username == "Sohan-Chitluri"


@pytest.mark.asyncio
async def test_github_client_connection_reuse_and_lifecycle():
    """GitHubClient reuses underlying HTTP client across requests and closes cleanly."""
    gh = GitHubClient(token="test_token", base_url="https://api.github.com")

    # Initial state
    assert gh._http_client is None

    # First access creates the client
    c1 = await gh._get_client()
    assert isinstance(c1, httpx.AsyncClient)
    assert not c1.is_closed

    # Second access reuses the exact same client instance
    c2 = await gh._get_client()
    assert c2 is c1

    # Shutdown
    await gh.aclose()
    assert c1.is_closed

    # Accessing again after close creates a fresh client
    c3 = await gh._get_client()
    assert c3 is not c1
    assert not c3.is_closed
    await gh.aclose()


@pytest.mark.asyncio
async def test_github_client_context_manager():
    """GitHubClient supports async context manager and auto-closes."""
    async with GitHubClient(token="test_token") as gh:
        client = await gh._get_client()
        assert not client.is_closed
    assert client.is_closed


@pytest.mark.asyncio
async def test_github_client_external_http_client_ownership():
    """Injected external http_client is not closed when GitHubClient.aclose() is called."""
    external_client = httpx.AsyncClient()
    try:
        gh = GitHubClient(token="test_token", http_client=external_client)
        assert gh._owns_http_client is False
        client = await gh._get_client()
        assert client is external_client

        await gh.aclose()
        # External client remains open
        assert not external_client.is_closed
    finally:
        await external_client.aclose()

