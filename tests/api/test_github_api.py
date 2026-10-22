"""API tests for GitHub account connection, repository association, and Raphael active session lifecycle."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest
from httpx import AsyncClient

from personal_os.api.deps import get_hermes_client
from personal_os.db.models import Project, User
from personal_os.services.github import GitHubClient
from personal_os.services.hermes import MockHermesClient


@pytest.fixture
def mock_hermes():
    return MockHermesClient()


@pytest.fixture(autouse=True)
def override_hermes(api_app, mock_hermes):
    api_app.dependency_overrides[get_hermes_client] = lambda: mock_hermes
    yield
    api_app.dependency_overrides.pop(get_hermes_client, None)


@pytest.mark.asyncio
async def test_github_api_lifecycle(
    auth_client: AsyncClient,
    test_user: User,
    db_session,
    api_app,
):
    """Verify GitHub connect, status, repo discovery, project linking, context, and disconnect via HTTP."""
    from personal_os.config import Settings, get_settings

    api_app.dependency_overrides[get_settings] = lambda: Settings(
        environment="test",
        github_public_username=None,
        github_client_id=None,
        github_client_secret=None,
        github_token_encryption_key="test-encryption-key",
    )

    # 1. Status initially disconnected
    res = await auth_client.get("/api/v1/github/status")
    assert res.status_code == 200
    assert res.json()["connected"] is False
    assert res.json()["username"] is None

    # 2. Connect token (mocking GitHub API)
    with patch.object(
        GitHubClient,
        "get_authenticated_user",
        new=AsyncMock(return_value={"id": "123", "login": "octocat"}),
    ):
        conn_res = await auth_client.post(
            "/api/v1/github/connect",
            json={"token": "ghp_mock_token_for_api_test", "scopes": "repo"},
        )
        assert conn_res.status_code == 200
        assert conn_res.json()["connected"] is True
        assert conn_res.json()["username"] == "octocat"

    # 3. Status is now connected
    st_res = await auth_client.get("/api/v1/github/status")
    assert st_res.status_code == 200
    assert st_res.json()["connected"] is True
    assert st_res.json()["username"] == "octocat"
    assert "token" not in st_res.json()

    # 4. Create a project
    proj_res = await auth_client.post(
        "/api/v1/projects",
        json={"name": "FPGA Accelerator", "description": "Hardware math accelerator"},
    )
    assert proj_res.status_code == 201
    project_id = proj_res.json()["id"]

    # 5. Associate repository
    mock_repo = {
        "id": "555",
        "name": "fpga-accelerator",
        "full_name": "octocat/fpga-accelerator",
        "owner": "octocat",
        "html_url": "https://github.com/octocat/fpga-accelerator",
        "default_branch": "main",
    }
    readme_text = "# FPGA Accelerator\nHigh-speed pipelined math core in Verilog.\n## Features\n- 16-stage pipeline"

    with (
        patch.object(GitHubClient, "get_repository", new=AsyncMock(return_value=mock_repo)),
        patch.object(GitHubClient, "get_readme", new=AsyncMock(return_value=(readme_text, "sha555"))),
        patch.object(GitHubClient, "list_milestones", new=AsyncMock(return_value=[{"id": "1", "title": "Tapeout"}])),
        patch.object(GitHubClient, "list_issues", new=AsyncMock(return_value=[{"id": "2", "title": "Timing closure"}])),
        patch.object(GitHubClient, "list_commits", new=AsyncMock(return_value=[{"sha": "c0ffee1", "message": "Add pipeline registers"}])),
    ):
        assoc_res = await auth_client.post(
            f"/api/v1/projects/{project_id}/repository",
            json={"repository": "octocat/fpga-accelerator"},
        )
        assert assoc_res.status_code == 201
        data = assoc_res.json()
        assert data["repository"]["full_name"] == "octocat/fpga-accelerator"
        assert "High-speed pipelined math" in data["project_brief"]["purpose"]

        # 6. Fetch project repository context
        ctx_res = await auth_client.get(f"/api/v1/projects/{project_id}/repository")
        assert ctx_res.status_code == 200
        ctx_data = ctx_res.json()
        assert len(ctx_data["milestones"]) == 1
        assert len(ctx_data["issues"]) == 1
        assert len(ctx_data["commits"]) == 1

        # 7. Sync repository
        sync_res = await auth_client.post(f"/api/v1/projects/{project_id}/repository/sync")
        assert sync_res.status_code == 200

        # 8. Disconnect repository from project
        del_repo_res = await auth_client.delete(f"/api/v1/projects/{project_id}/repository")
        assert del_repo_res.status_code == 204

        # Verify it's gone
        get_none = await auth_client.get(f"/api/v1/projects/{project_id}/repository")
        assert get_none.status_code == 404

    # 9. Disconnect GitHub account
    disc_res = await auth_client.post("/api/v1/github/disconnect")
    assert disc_res.status_code == 200
    assert disc_res.json()["connected"] is False


@pytest.mark.asyncio
async def test_github_oauth_authorization_and_callbacks_api(
    auth_client: AsyncClient,
    test_user: User,
    api_app,
):
    """Verify GitHub OAuth initiation, state validation, browser callback redirect, and SPA callback completion."""
    from personal_os.config import Settings
    from personal_os.config import get_settings

    # 1. Authorize when NOT configured returns 400 Bad Request
    unconfigured_settings = Settings(
        environment="test",
        github_public_username=None,
        github_client_id=None,
        github_client_secret=None,
    )
    api_app.dependency_overrides[get_settings] = lambda: unconfigured_settings

    auth_res_unconf = await auth_client.get("/api/v1/github/authorize")
    assert auth_res_unconf.status_code == 400
    assert "GITHUB_CLIENT_ID" in auth_res_unconf.json()["detail"]

    # Status shows configured=False
    status_unconf = await auth_client.get("/api/v1/github/status")
    assert status_unconf.status_code == 200
    assert status_unconf.json()["configured"] is False
    assert status_unconf.json()["connected"] is False

    # 2. Authorize when configured returns authorization URL and CSRF state
    configured_settings = Settings(
        environment="test",
        github_public_username=None,
        github_client_id="gh-client-xyz",
        github_client_secret="gh-secret-123",
        github_token_encryption_key="test-encryption-key",
    )
    api_app.dependency_overrides[get_settings] = lambda: configured_settings

    auth_res = await auth_client.get("/api/v1/github/authorize?return_to=/projects")
    assert auth_res.status_code == 200
    auth_data = auth_res.json()
    assert "github.com/login/oauth/authorize" in auth_data["authorization_url"]
    assert "client_id=gh-client-xyz" in auth_data["authorization_url"]
    state = auth_data["state"]
    assert state is not None

    # Status shows configured=True
    status_conf = await auth_client.get("/api/v1/github/status")
    assert status_conf.status_code == 200
    assert status_conf.json()["configured"] is True

    # 3. GET callback with OAuth error query param redirects cleanly to frontend error URL
    err_res = await auth_client.get(
        "/api/v1/github/callback?error=access_denied&error_description=User+cancelled+authorization",
        follow_redirects=False,
    )
    assert err_res.status_code == 302
    assert "github_error=" in err_res.headers["location"]

    # 4. POST callback with invalid state returns 400
    post_invalid = await auth_client.post(
        "/api/v1/github/callback",
        json={"code": "valid-code", "state": "fake-state-that-does-not-exist"},
    )
    assert post_invalid.status_code == 400
    assert "state" in post_invalid.json()["detail"].lower()

    # 5. Successful POST callback completion (mocking GitHub token exchange)
    mock_token_payload = {
        "access_token": "ghu_oauth_token_api_test",
        "refresh_token": "ghr_refresh_api_test",
        "expires_in": 3600,
        "scope": "repo,read:user",
    }
    with (
        patch("personal_os.services.github.GitHubService.exchange_code_for_token", new=AsyncMock(return_value=mock_token_payload)),
        patch.object(
            GitHubClient,
            "get_authenticated_user",
            new=AsyncMock(return_value={"id": "777", "login": "octo-authorizer"}),
        ),
    ):
        cb_res = await auth_client.post(
            "/api/v1/github/callback",
            json={"code": "valid-github-code", "state": state},
        )
        assert cb_res.status_code == 200
        assert cb_res.json()["connected"] is True
        assert cb_res.json()["username"] == "octo-authorizer"

        # Verify status is now connected
        st_after = await auth_client.get("/api/v1/github/status")
        assert st_after.status_code == 200
        assert st_after.json()["connected"] is True
        assert st_after.json()["username"] == "octo-authorizer"

        # Disconnect cleans up
        disc = await auth_client.post("/api/v1/github/disconnect")
        assert disc.status_code == 200
        assert disc.json()["connected"] is False

    api_app.dependency_overrides.pop(get_settings, None)


@pytest.mark.asyncio
async def test_raphael_active_session_and_reset_api(
    auth_client: AsyncClient,
    test_user: User,
):
    """Verify GET /api/v1/raphael/active returns active conversation and POST /api/v1/raphael/reset creates fresh session."""
    # 1. Get active session (creates one if none exists)
    res = await auth_client.get("/api/v1/raphael/active")
    assert res.status_code == 200
    active_conv = res.json()
    assert active_conv["id"] is not None
    assert active_conv["status"] == "active"

    # Send a message
    msg_res = await auth_client.post(
        f"/api/v1/raphael/conversations/{active_conv['id']}/messages",
        json={"content": "Hello Raphael! We decided on using Verilog."},
    )
    assert msg_res.status_code == 200

    # 2. Reset active session (finalizes to Diary and returns a fresh one)
    reset_res = await auth_client.post("/api/v1/raphael/reset")
    assert reset_res.status_code == 200
    new_conv = reset_res.json()
    assert new_conv["id"] != active_conv["id"]
    assert new_conv["messages"] == []

    # 3. Check memory search finds the previous session
    mem_res = await auth_client.get("/api/v1/raphael/memories?q=Verilog")
    assert mem_res.status_code == 200
    memories = mem_res.json()
    assert len(memories) >= 1
    assert "Verilog" in memories[0]["summary"]


@pytest.mark.asyncio
async def test_github_public_only_mode_api(
    auth_client: AsyncClient,
    test_user: User,
    api_app,
):
    """Verify public-only mode in API: unauthenticated status, repo discovery, and project linking."""
    from personal_os.config import Settings, get_settings

    pub_settings = Settings(
        environment="test",
        github_public_username="Sohan-Chitluri",
        github_client_id=None,
        github_client_secret=None,
        github_token_encryption_key="test-encryption-key",
    )
    api_app.dependency_overrides[get_settings] = lambda: pub_settings

    # 1. Status reports public_only mode with configured=False
    status_res = await auth_client.get("/api/v1/github/status")
    assert status_res.status_code == 200
    status_data = status_res.json()
    assert status_data["connected"] is True
    assert status_data["public_only"] is True
    assert status_data["mode"] == "public_only"
    assert status_data["username"] == "Sohan-Chitluri"
    assert status_data["configured"] is False

    # 2. List public repositories without any auth token
    mock_repos = [
        {
            "id": 888,
            "name": "personal-os-ui-rework",
            "full_name": "Sohan-Chitluri/personal-os-ui-rework",
            "owner": {"login": "Sohan-Chitluri"},
            "description": "Public UI rework",
            "html_url": "https://github.com/Sohan-Chitluri/personal-os-ui-rework",
            "default_branch": "main",
            "private": False,
        }
    ]
    with patch.object(GitHubClient, "_request", new=AsyncMock(return_value=mock_repos)):
        repo_res = await auth_client.get("/api/v1/github/repositories")
        assert repo_res.status_code == 200
        assert len(repo_res.json()) == 1
        assert repo_res.json()[0]["full_name"] == "Sohan-Chitluri/personal-os-ui-rework"

    # 3. Create a project and associate repo by name
    proj_res = await auth_client.post(
        "/api/v1/projects",
        json={"name": "Public Project", "description": "Testing public github integration"},
    )
    assert proj_res.status_code == 201
    project_id = proj_res.json()["id"]

    mock_repo_details = {
        "id": "888",
        "name": "personal-os-ui-rework",
        "full_name": "Sohan-Chitluri/personal-os-ui-rework",
        "owner": {"login": "Sohan-Chitluri"},
        "description": "Public UI rework",
        "html_url": "https://github.com/Sohan-Chitluri/personal-os-ui-rework",
        "default_branch": "main",
        "private": False,
    }
    readme_sample = "# Personal OS\nPublic repository README brief."

    with (
        patch.object(GitHubClient, "get_repository", new=AsyncMock(return_value=mock_repo_details)),
        patch.object(GitHubClient, "get_readme", new=AsyncMock(return_value=(readme_sample, "sha-pub-888"))),
        patch.object(GitHubClient, "list_milestones", new=AsyncMock(return_value=[])),
        patch.object(GitHubClient, "list_issues", new=AsyncMock(return_value=[])),
        patch.object(GitHubClient, "list_commits", new=AsyncMock(return_value=[])),
    ):
        assoc_res = await auth_client.post(
            f"/api/v1/projects/{project_id}/repository",
            json={"repository": "personal-os-ui-rework"},
        )
        assert assoc_res.status_code == 201
        assoc_data = assoc_res.json()
        assert assoc_data["repository"]["full_name"] == "Sohan-Chitluri/personal-os-ui-rework"

        # 4. Fetch repository context
        ctx_res = await auth_client.get(f"/api/v1/projects/{project_id}/repository")
        assert ctx_res.status_code == 200
        assert ctx_res.json()["repository"]["full_name"] == "Sohan-Chitluri/personal-os-ui-rework"

    api_app.dependency_overrides.pop(get_settings, None)

