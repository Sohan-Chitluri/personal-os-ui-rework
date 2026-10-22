"""Domain service and API client for GitHub repository discovery, context extraction, and project association."""

from __future__ import annotations

import base64
import logging
import re
import secrets
from datetime import UTC, datetime, timedelta
from typing import Any, cast
from urllib.parse import quote

import httpx
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from personal_os.config import Settings
from personal_os.core.crypto import decrypt_secret, encrypt_secret
from personal_os.db.models import GitHubOAuthState, Project, ProjectRepository, UserGitHubAuth
from personal_os.services.interfaces import ServiceNotFound

log = logging.getLogger(__name__)


class GitHubError(Exception):
    """Base exception for GitHub service operations."""


class GitHubAuthError(GitHubError):
    """Raised when GitHub authorization fails or token is missing/expired."""


class GitHubNotFoundError(GitHubError):
    """Raised when a repository or resource is not found."""


class GitHubRateLimitError(GitHubError):
    """Raised when GitHub API rate limits are exceeded."""


def extract_readme_brief(readme_text: str | None) -> dict[str, Any]:
    """Distill README markdown into a structured project brief.

    Extracts:
    - purpose: concise summary or introductory paragraph
    - architecture_or_stack: detected tech stack and architecture keywords
    - setup: setup / installation commands
    - key_capabilities: extracted feature bullets
    - limitations: known limitations or WIP items
    """
    if not readme_text or not readme_text.strip():
        return {
            "purpose": "No README documentation available.",
            "architecture_or_stack": [],
            "setup": None,
            "key_capabilities": [],
            "limitations": [],
        }

    lines = readme_text.splitlines()

    # Common tech keywords to look for
    tech_keywords = [
        "python",
        "fastapi",
        "react",
        "typescript",
        "javascript",
        "rust",
        "verilog",
        "systemverilog",
        "vhdl",
        "c++",
        "c",
        "go",
        "docker",
        "postgresql",
        "sqlite",
        "redis",
        "tailwind",
        "vite",
        "next.js",
        "pytorch",
        "tensorflow",
        "asic",
        "fpga",
        "cordic",
        "chisel",
    ]

    detected_stack: list[str] = []
    lower_content = readme_text.lower()
    for kw in tech_keywords:
        # Match whole word
        if re.search(rf"\b{re.escape(kw)}\b", lower_content):
            detected_stack.append(
                kw.title() if kw not in ("c++", "next.js", "asic", "fpga", "vhdl") else kw.upper()
            )

    # Extract purpose: First substantive paragraph (skipping badges and headings)
    purpose_lines: list[str] = []
    collecting_purpose = False
    in_code_block = False

    for line in lines:
        stripped = line.strip()
        if stripped.startswith("```"):
            in_code_block = not in_code_block
            continue
        if in_code_block:
            continue
        if not stripped:
            if collecting_purpose and purpose_lines:
                break
            continue
        if stripped.startswith("#"):
            continue
        # Skip badge images [![...](...)] or HTML
        if stripped.startswith("[![") or stripped.startswith("<p") or stripped.startswith("<img"):
            continue

        collecting_purpose = True
        purpose_lines.append(stripped)
        if len(purpose_lines) >= 3:
            break

    purpose = " ".join(purpose_lines).strip()
    if not purpose:
        purpose = "Project documentation present without an explicit summary."

    # Extract sections by heading
    current_section = ""
    section_content: dict[str, list[str]] = {
        "features": [],
        "setup": [],
        "limitations": [],
    }

    in_code = False
    for line in lines:
        stripped = line.strip()
        if stripped.startswith("```"):
            in_code = not in_code
        if stripped.startswith("#"):
            h_text = stripped.lstrip("#").strip().lower()
            if any(k in h_text for k in ("feature", "capabilit", "what it does", "highlights")):
                current_section = "features"
            elif any(
                k in h_text for k in ("install", "setup", "getting started", "quick start", "usage")
            ):
                current_section = "setup"
            elif any(
                k in h_text
                for k in (
                    "limitation",
                    "known issue",
                    "roadmap",
                    "caveat",
                    "work in progress",
                    "wip",
                )
            ):
                current_section = "limitations"
            else:
                current_section = ""
            continue

        if current_section and stripped:
            section_content[current_section].append(stripped)

    # Clean capabilities (extract bullets)
    capabilities: list[str] = []
    for line in section_content["features"]:
        if line.startswith(("-", "*", "•", "1.", "2.", "3.", "4.", "5.")):
            item = re.sub(r"^[-*•\d.]+\s*", "", line).strip()
            if item and len(item) > 3:
                capabilities.append(item)
        if len(capabilities) >= 8:
            break

    # Clean limitations (extract bullets)
    limitations: list[str] = []
    for line in section_content["limitations"]:
        if line.startswith(("-", "*", "•", "1.", "2.", "3.")):
            item = re.sub(r"^[-*•\d.]+\s*", "", line).strip()
            if item and len(item) > 3:
                limitations.append(item)
        if len(limitations) >= 5:
            break

    # Clean setup summary
    setup_summary = (
        "\n".join(section_content["setup"][:6]).strip() if section_content["setup"] else None
    )

    return {
        "purpose": purpose[:500],
        "architecture_or_stack": list(dict.fromkeys(detected_stack)),
        "setup": setup_summary[:400] if setup_summary else None,
        "key_capabilities": capabilities,
        "limitations": limitations,
    }


class GitHubClient:
    """Async client for read-only GitHub API interactions (supports authenticated and public modes)."""

    def __init__(
        self,
        token: str | None = None,
        public_username: str | None = None,
        base_url: str = "https://api.github.com",
    ) -> None:
        self.token = token.strip() if token and token.strip() else None
        self.public_username = (
            public_username.strip() if public_username and public_username.strip() else None
        )
        self.base_url = base_url.rstrip("/")
        self.headers: dict[str, str] = {
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "Personal-OS-Raphael",
        }
        if self.token:
            self.headers["Authorization"] = f"Bearer {self.token}"

    @property
    def is_public_only(self) -> bool:
        return self.token is None

    async def _request(
        self,
        method: str,
        endpoint: str,
        params: dict[str, Any] | None = None,
    ) -> Any:
        url = f"{self.base_url}{endpoint}"
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.request(method, url, headers=self.headers, params=params)
            if resp.status_code == 401:
                raise GitHubAuthError("GitHub credentials are invalid or expired.")
            if resp.status_code == 404:
                raise GitHubNotFoundError(f"GitHub resource not found at {endpoint}")
            if resp.status_code == 403 and "rate limit" in resp.text.lower():
                raise GitHubRateLimitError("GitHub API rate limit exceeded.")
            if resp.status_code >= 400:
                raise GitHubError(f"GitHub API error ({resp.status_code}): {resp.text[:200]}")
            return resp.json()

    async def get_authenticated_user(self) -> dict[str, Any]:
        """Fetch user profile (authenticated or public profile)."""
        if self.token:
            data = await self._request("GET", "/user")
        elif self.public_username:
            data = await self._request("GET", f"/users/{self.public_username}")
        else:
            raise GitHubAuthError(
                "Cannot fetch user profile without authentication or public username."
            )

        return {
            "id": str(data["id"]),
            "login": data["login"],
            "name": data.get("name"),
            "html_url": data.get("html_url", f"https://github.com/{data['login']}"),
        }

    async def list_repositories(self, limit: int = 50) -> list[dict[str, Any]]:
        """List repositories accessible to the user (authenticated or public profile)."""
        if self.token:
            endpoint = "/user/repos"
            params = {
                "per_page": min(limit, 100),
                "sort": "updated",
                "affiliation": "owner,collaborator,organization_member",
            }
        elif self.public_username:
            endpoint = f"/users/{self.public_username}/repos"
            params = {
                "type": "public",
                "per_page": min(limit, 100),
                "sort": "updated",
            }
        else:
            raise GitHubAuthError(
                "Cannot list repositories without authentication or public username."
            )

        data = await self._request(
            "GET",
            endpoint,
            params=params,
        )
        repos = []
        for r in data:
            if self.is_public_only and r.get("private", False):
                continue
            repos.append(
                {
                    "id": str(r["id"]),
                    "name": r["name"],
                    "full_name": r["full_name"],
                    "owner": r["owner"]["login"] if "owner" in r else "",
                    "description": r.get("description") or "",
                    "html_url": r.get("html_url", ""),
                    "default_branch": r.get("default_branch", "main"),
                    "updated_at": r.get("updated_at"),
                    "private": r.get("private", False),
                }
            )
        return repos

    async def get_repository(self, full_name: str) -> dict[str, Any]:
        """Fetch details for a specific repository."""
        target = full_name.strip()
        if "/" not in target and self.public_username:
            target = f"{self.public_username}/{target}"

        r = await self._request("GET", f"/repos/{target}")
        if self.is_public_only and r.get("private", False):
            raise GitHubNotFoundError(
                f"GitHub repository '{target}' is private or inaccessible in public-only mode."
            )

        return {
            "id": str(r["id"]),
            "name": r["name"],
            "full_name": r["full_name"],
            "owner": r["owner"]["login"] if "owner" in r else "",
            "description": r.get("description") or "",
            "html_url": r.get("html_url", ""),
            "default_branch": r.get("default_branch", "main"),
            "updated_at": r.get("updated_at"),
            "private": r.get("private", False),
        }

    async def get_readme(self, full_name: str) -> tuple[str, str | None]:
        """Fetch and decode repository README. Returns (content, sha)."""
        target = full_name.strip()
        if "/" not in target and self.public_username:
            target = f"{self.public_username}/{target}"

        try:
            data = await self._request("GET", f"/repos/{target}/readme")
            raw_content = data.get("content", "")
            encoding = data.get("encoding", "base64")
            sha = data.get("sha")
            if encoding == "base64" and raw_content:
                decoded = base64.b64decode(raw_content).decode("utf-8", errors="replace")
                return decoded, sha
            return raw_content, sha
        except GitHubNotFoundError:
            return "", None

    async def list_milestones(
        self, full_name: str, state: str = "open", limit: int = 10
    ) -> list[dict[str, Any]]:
        """List repository milestones."""
        target = full_name.strip()
        if "/" not in target and self.public_username:
            target = f"{self.public_username}/{target}"

        try:
            data = await self._request(
                "GET",
                f"/repos/{target}/milestones",
                params={"state": state, "per_page": limit, "sort": "due_on"},
            )
            return [
                {
                    "id": str(m["id"]),
                    "number": m.get("number", 0),
                    "title": m.get("title", ""),
                    "description": m.get("description"),
                    "state": m.get("state", "open"),
                    "open_issues": m.get("open_issues", 0),
                    "closed_issues": m.get("closed_issues", 0),
                    "due_on": m.get("due_on"),
                }
                for m in data
            ]
        except Exception as exc:
            log.warning("Failed to fetch milestones for %s: %s", target, exc)
            return []

    async def list_issues(
        self, full_name: str, state: str = "open", limit: int = 10
    ) -> list[dict[str, Any]]:
        """List repository open issues (excluding pull requests)."""
        target = full_name.strip()
        if "/" not in target and self.public_username:
            target = f"{self.public_username}/{target}"

        try:
            data = await self._request(
                "GET",
                f"/repos/{target}/issues",
                params={"state": state, "per_page": limit * 2, "sort": "updated"},
            )
            issues = []
            for i in data:
                # GitHub issues endpoint returns PRs unless filtered out
                if "pull_request" in i:
                    continue
                issues.append(
                    {
                        "id": str(i["id"]),
                        "number": i["number"],
                        "title": i["title"],
                        "state": i["state"],
                        "labels": [
                            lbl["name"] for lbl in i.get("labels", []) if isinstance(lbl, dict)
                        ],
                        "created_at": i.get("created_at"),
                        "updated_at": i.get("updated_at"),
                    }
                )
                if len(issues) >= limit:
                    break
            return issues
        except Exception as exc:
            log.warning("Failed to fetch issues for %s: %s", target, exc)
            return []

    async def list_commits(self, full_name: str, limit: int = 10) -> list[dict[str, Any]]:
        """List recent repository commits."""
        target = full_name.strip()
        if "/" not in target and self.public_username:
            target = f"{self.public_username}/{target}"

        try:
            data = await self._request(
                "GET",
                f"/repos/{target}/commits",
                params={"per_page": limit},
            )
            commits = []
            for c in data:
                commit_info = c.get("commit", {})
                author_info = commit_info.get("author", {})
                commits.append(
                    {
                        "sha": c["sha"][:7],
                        "full_sha": c["sha"],
                        "message": commit_info.get("message", "").split("\n")[0],
                        "author": author_info.get("name", "Unknown"),
                        "date": author_info.get("date"),
                    }
                )
            return commits
        except Exception as exc:
            log.warning("Failed to fetch commits for %s: %s", target, exc)
            return []


class GitHubService:
    """Manages user GitHub authorizations, repository associations, and context snapshots."""

    def __init__(self, session: AsyncSession, settings: Settings) -> None:
        self.session = session
        self.settings = settings
        self._encryption_key = (
            settings.github_token_encryption_key
            or settings.companion_jwt_secret
            or "personal-os-default-github-key-32b"
        )

    def is_configured(self) -> bool:
        """Return True if GitHub App client_id and client_secret are configured."""
        return bool(self.settings.github_client_id and self.settings.github_client_secret)

    def is_public_only_configured(self) -> bool:
        """Return True if public-only username is configured."""
        return bool(
            self.settings.github_public_username and self.settings.github_public_username.strip()
        )

    async def get_auth_status(self, user_id: str) -> dict[str, Any]:
        """Return authorization status without exposing secrets."""
        configured = self.is_configured()
        public_configured = self.is_public_only_configured()

        stmt = select(UserGitHubAuth).where(UserGitHubAuth.user_id == user_id)
        result = await self.session.execute(stmt)
        auth = result.scalar_one_or_none()

        now = datetime.now(UTC)
        token_expired = False
        if auth is not None and auth.token_expires_at is not None and auth.token_expires_at <= now:
            # Token is expired; attempt automatic refresh if refresh token exists
            try:
                await self.refresh_user_token(auth)
            except Exception as exc:
                log.warning("GitHub token refresh failed for user %s: %s", user_id, exc)
                token_expired = True

        if auth is not None:
            if not token_expired:
                return {
                    "configured": configured,
                    "connected": True,
                    "username": auth.github_username,
                    "scopes": auth.scopes.split(",") if auth.scopes else [],
                    "updated_at": auth.updated_at.isoformat() if auth.updated_at else None,
                    "token_expired": False,
                    "mode": "authenticated",
                    "public_only": False,
                }
            # Token expired and refresh failed
            if public_configured:
                pub_username = (self.settings.github_public_username or "").strip()
                return {
                    "configured": configured,
                    "connected": True,
                    "username": pub_username,
                    "scopes": [],
                    "updated_at": auth.updated_at.isoformat() if auth.updated_at else None,
                    "token_expired": True,
                    "mode": "public_only",
                    "public_only": True,
                }
            return {
                "configured": configured,
                "connected": False,
                "username": auth.github_username,
                "scopes": auth.scopes.split(",") if auth.scopes else [],
                "updated_at": auth.updated_at.isoformat() if auth.updated_at else None,
                "token_expired": True,
                "mode": "oauth_ready" if configured else "unconfigured",
                "public_only": False,
            }

        # No user-specific OAuth record
        if public_configured:
            pub_username = (self.settings.github_public_username or "").strip()
            return {
                "configured": configured,
                "connected": True,
                "username": pub_username,
                "scopes": [],
                "updated_at": None,
                "token_expired": False,
                "mode": "public_only",
                "public_only": True,
            }

        return {
            "configured": configured,
            "connected": False,
            "username": None,
            "scopes": None,
            "updated_at": None,
            "token_expired": False,
            "mode": "oauth_ready" if configured else "unconfigured",
            "public_only": False,
        }

    async def create_authorization_url(
        self,
        user_id: str,
        redirect_uri: str | None = None,
        return_to: str | None = None,
    ) -> tuple[str, str]:
        """Generate a CSRF-protected GitHub OAuth authorization URL and persist the state."""
        if not self.is_configured():
            raise GitHubError(
                "GitHub application credentials are not configured on the server. "
                "Please configure GITHUB_CLIENT_ID and GITHUB_CLIENT_SECRET."
            )

        state = secrets.token_urlsafe(32)
        expires_at = datetime.now(UTC) + timedelta(minutes=10)

        oauth_state = GitHubOAuthState(
            user_id=user_id,
            state=state,
            return_to=return_to or "/projects",
            expires_at=expires_at,
            used=False,
        )
        self.session.add(oauth_state)
        await self.session.commit()

        oauth_base = (
            self.settings.github_oauth_base_url or "https://github.com/login/oauth"
        ).rstrip("/")
        effective_redirect = redirect_uri or self.settings.github_redirect_uri

        auth_url = f"{oauth_base}/authorize?client_id={quote(self.settings.github_client_id or '')}&state={quote(state)}"
        if effective_redirect:
            auth_url += f"&redirect_uri={quote(effective_redirect, safe='')}"

        return auth_url, state

    async def validate_and_consume_state(
        self,
        state: str,
        expected_user_id: str | None = None,
    ) -> GitHubOAuthState:
        """Validate an OAuth state parameter, ensuring it is unexpired, unconsumed, and user-scoped."""
        if not state or not state.strip():
            raise GitHubAuthError("Missing or empty OAuth state parameter.")

        clean_state = state.strip()
        stmt = select(GitHubOAuthState).where(GitHubOAuthState.state == clean_state)
        result = await self.session.execute(stmt)
        record = result.scalar_one_or_none()

        if record is None or record.used:
            raise GitHubAuthError("Invalid or already consumed OAuth state parameter.")

        if record.expires_at <= datetime.now(UTC):
            raise GitHubAuthError(
                "OAuth state parameter has expired. Please initiate authorization again."
            )

        if expected_user_id is not None and record.user_id != expected_user_id:
            raise GitHubAuthError("OAuth state parameter does not match the authenticated user.")

        record.used = True
        self.session.add(record)
        await self.session.commit()
        return record

    async def exchange_code_for_token(
        self,
        code: str,
        redirect_uri: str | None = None,
    ) -> dict[str, Any]:
        """Exchange temporary OAuth code for access token via GitHub."""
        if not self.is_configured():
            raise GitHubError("GitHub integration is not configured.")

        oauth_base = (
            self.settings.github_oauth_base_url or "https://github.com/login/oauth"
        ).rstrip("/")
        url = f"{oauth_base}/access_token"

        payload: dict[str, Any] = {
            "client_id": self.settings.github_client_id,
            "client_secret": self.settings.github_client_secret,
            "code": code.strip(),
        }
        if redirect_uri:
            payload["redirect_uri"] = redirect_uri

        headers = {
            "Accept": "application/json",
            "User-Agent": "Personal-OS-Raphael",
        }

        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.post(url, json=payload, headers=headers)
            if resp.status_code >= 400:
                raise GitHubError(
                    f"GitHub token exchange failed ({resp.status_code}): {resp.text[:200]}"
                )

            data = resp.json()
            if "error" in data:
                error_msg = (
                    data.get("error_description") or data.get("error") or "Unknown OAuth error"
                )
                raise GitHubAuthError(f"GitHub authorization failed: {error_msg}")

            return cast(dict[str, Any], data)

    async def complete_oauth_callback(
        self,
        code: str,
        state: str,
        user_id: str | None = None,
        redirect_uri: str | None = None,
    ) -> dict[str, Any]:
        """Validate CSRF state, exchange code, fetch profile, encrypt credentials, and save auth."""
        oauth_state = await self.validate_and_consume_state(state, expected_user_id=user_id)
        target_user_id = oauth_state.user_id

        effective_redirect = redirect_uri or self.settings.github_redirect_uri
        token_data = await self.exchange_code_for_token(code, redirect_uri=effective_redirect)

        access_token = token_data.get("access_token")
        if not access_token:
            raise GitHubAuthError("GitHub did not return an access token.")

        refresh_token = token_data.get("refresh_token")
        expires_in = token_data.get("expires_in")
        token_expires_at = (
            datetime.now(UTC) + timedelta(seconds=int(expires_in))
            if expires_in is not None
            else None
        )

        # Validate token by fetching authenticated user details
        client = GitHubClient(token=access_token, base_url=self.settings.github_api_base_url)
        user_info = await client.get_authenticated_user()

        encrypted_access = encrypt_secret(access_token, self._encryption_key)
        encrypted_refresh = (
            encrypt_secret(refresh_token, self._encryption_key) if refresh_token else None
        )

        stmt = select(UserGitHubAuth).where(UserGitHubAuth.user_id == target_user_id)
        res = await self.session.execute(stmt)
        existing = res.scalar_one_or_none()

        if existing is not None:
            existing.encrypted_token = encrypted_access
            existing.encrypted_refresh_token = encrypted_refresh
            existing.token_expires_at = token_expires_at
            existing.github_username = user_info["login"]
            existing.scopes = token_data.get("scope")
            self.session.add(existing)
        else:
            new_auth = UserGitHubAuth(
                user_id=target_user_id,
                encrypted_token=encrypted_access,
                encrypted_refresh_token=encrypted_refresh,
                token_expires_at=token_expires_at,
                github_username=user_info["login"],
                scopes=token_data.get("scope"),
            )
            self.session.add(new_auth)

        await self.session.commit()
        return {
            "connected": True,
            "username": user_info["login"],
            "scopes": token_data.get("scope", "").split(",") if token_data.get("scope") else [],
            "return_to": oauth_state.return_to or "/projects",
        }

    async def refresh_user_token(self, auth: UserGitHubAuth) -> str:
        """Refresh user access token using refresh token if available."""
        if not auth.encrypted_refresh_token:
            raise GitHubAuthError("No refresh token available to refresh expired GitHub token.")

        refresh_token = decrypt_secret(auth.encrypted_refresh_token, self._encryption_key)
        oauth_base = (
            self.settings.github_oauth_base_url or "https://github.com/login/oauth"
        ).rstrip("/")
        url = f"{oauth_base}/access_token"

        payload = {
            "client_id": self.settings.github_client_id,
            "client_secret": self.settings.github_client_secret,
            "grant_type": "refresh_token",
            "refresh_token": refresh_token,
        }
        headers = {
            "Accept": "application/json",
            "User-Agent": "Personal-OS-Raphael",
        }

        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.post(url, json=payload, headers=headers)
            if resp.status_code >= 400:
                raise GitHubAuthError("Failed to refresh GitHub token with provider.")

            data = resp.json()
            if "error" in data:
                raise GitHubAuthError(
                    f"GitHub token refresh error: {data.get('error_description') or data['error']}"
                )

            new_access = data.get("access_token")
            if not new_access:
                raise GitHubAuthError("Provider did not return a refreshed access token.")

            auth.encrypted_token = encrypt_secret(new_access, self._encryption_key)
            if data.get("refresh_token"):
                auth.encrypted_refresh_token = encrypt_secret(
                    data["refresh_token"], self._encryption_key
                )
            if data.get("expires_in"):
                auth.token_expires_at = datetime.now(UTC) + timedelta(
                    seconds=int(data["expires_in"])
                )

            self.session.add(auth)
            await self.session.commit()
            return str(new_access)

    async def connect_token(
        self, user_id: str, token: str, scopes: str | None = None
    ) -> dict[str, Any]:
        """Validate token with GitHub API, encrypt it, and persist UserGitHubAuth."""
        token_clean = token.strip()
        if not token_clean:
            raise ValueError("GitHub token cannot be blank.")

        client = GitHubClient(token=token_clean, base_url=self.settings.github_api_base_url)
        user_info = await client.get_authenticated_user()

        encrypted = encrypt_secret(token_clean, self._encryption_key)

        stmt = select(UserGitHubAuth).where(UserGitHubAuth.user_id == user_id)
        result = await self.session.execute(stmt)
        existing = result.scalar_one_or_none()

        if existing is not None:
            existing.encrypted_token = encrypted
            existing.github_username = user_info["login"]
            existing.scopes = scopes
            self.session.add(existing)
        else:
            new_auth = UserGitHubAuth(
                user_id=user_id,
                encrypted_token=encrypted,
                github_username=user_info["login"],
                scopes=scopes,
            )
            self.session.add(new_auth)

        await self.session.commit()
        return {
            "connected": True,
            "username": user_info["login"],
        }

    async def disconnect(self, user_id: str) -> bool:
        """Remove user GitHub credentials. Does not delete projects or tasks."""
        stmt = delete(UserGitHubAuth).where(UserGitHubAuth.user_id == user_id)
        result = await self.session.execute(stmt)
        await self.session.commit()
        rowcount = getattr(result, "rowcount", 0) or 0
        return bool(rowcount > 0)

    async def get_client(self, user_id: str) -> GitHubClient:
        """Construct an authorized or public GitHubClient for the given user, refreshing token if expired."""
        stmt = select(UserGitHubAuth).where(UserGitHubAuth.user_id == user_id)
        result = await self.session.execute(stmt)
        auth = result.scalar_one_or_none()
        if auth is None:
            if self.is_public_only_configured():
                return GitHubClient(
                    token=None,
                    public_username=(self.settings.github_public_username or "").strip(),
                    base_url=self.settings.github_api_base_url,
                )
            raise GitHubAuthError("GitHub is not connected. Connect your GitHub account first.")

        now = datetime.now(UTC)
        if auth.token_expires_at is not None and auth.token_expires_at <= now:
            try:
                decrypted_token = await self.refresh_user_token(auth)
                return GitHubClient(
                    token=decrypted_token, base_url=self.settings.github_api_base_url
                )
            except Exception as exc:
                if self.is_public_only_configured():
                    log.warning(
                        "GitHub token expired for user %s, falling back to public-only client: %s",
                        user_id,
                        exc,
                    )
                    return GitHubClient(
                        token=None,
                        public_username=(self.settings.github_public_username or "").strip(),
                        base_url=self.settings.github_api_base_url,
                    )
                raise GitHubAuthError(
                    f"GitHub token has expired and could not be refreshed: {exc}"
                ) from exc
        else:
            decrypted_token = decrypt_secret(auth.encrypted_token, self._encryption_key)

        return GitHubClient(token=decrypted_token, base_url=self.settings.github_api_base_url)

    async def list_repositories(self, user_id: str, limit: int = 50) -> list[dict[str, Any]]:
        """List repositories accessible to the user."""
        client = await self.get_client(user_id)
        return await client.list_repositories(limit=limit)

    async def search_repositories(self, user_id: str, query: str) -> list[dict[str, Any]]:
        """Search and rank user repositories matching query."""
        client = await self.get_client(user_id)
        all_repos = await client.list_repositories(limit=100)
        q = query.strip().lower()
        if not q:
            return all_repos[:20]

        exact_matches = []
        substring_matches = []
        desc_matches = []

        for r in all_repos:
            r_name = r["name"].lower()
            r_full = r["full_name"].lower()
            r_desc = (r.get("description") or "").lower()

            if q in (r_name, r_full):
                exact_matches.append(r)
            elif q in r_name or q in r_full:
                substring_matches.append(r)
            elif q in r_desc:
                desc_matches.append(r)

        results = exact_matches + substring_matches + desc_matches
        if not results and q:
            try:
                direct = await client.get_repository(query)
                results.append(direct)
            except Exception:
                pass

        return results

    async def associate_project_repository(
        self,
        owner_id: str,
        project_id: str,
        repo_full_name: str,
    ) -> ProjectRepository:
        """Link a GitHub repository to a user project and extract its initial brief."""
        # Check project ownership
        stmt_proj = select(Project).where(
            Project.id == project_id,
            Project.owner_id == owner_id,
            Project.deleted_at.is_(None),
        )
        res_proj = await self.session.execute(stmt_proj)
        project = res_proj.scalar_one_or_none()
        if project is None:
            raise ServiceNotFound(f"Project {project_id} not found")

        client = await self.get_client(owner_id)
        repo_data = await client.get_repository(repo_full_name)
        readme_content, readme_sha = await client.get_readme(repo_full_name)
        brief = extract_readme_brief(readme_content)

        # Check for existing association
        stmt_repo = select(ProjectRepository).where(ProjectRepository.project_id == project_id)
        res_repo = await self.session.execute(stmt_repo)
        existing = res_repo.scalar_one_or_none()

        raw_owner = repo_data.get("owner")
        repo_owner = raw_owner["login"] if isinstance(raw_owner, dict) else str(raw_owner or "")

        now = datetime.now(UTC)
        if existing is not None:
            existing.repo_id = str(repo_data["id"])
            existing.repo_owner = repo_owner
            existing.repo_name = repo_data["name"]
            existing.full_name = repo_data["full_name"]
            existing.default_branch = repo_data["default_branch"]
            existing.html_url = repo_data["html_url"]
            existing.project_brief = brief
            existing.readme_sha = readme_sha
            existing.readme_content = readme_content
            existing.last_synced_at = now
            self.session.add(existing)
            proj_repo = existing
        else:
            proj_repo = ProjectRepository(
                project_id=project_id,
                owner_id=owner_id,
                repo_id=str(repo_data["id"]),
                repo_owner=repo_owner,
                repo_name=repo_data["name"],
                full_name=repo_data["full_name"],
                default_branch=repo_data["default_branch"],
                html_url=repo_data["html_url"],
                project_brief=brief,
                readme_sha=readme_sha,
                readme_content=readme_content,
                last_synced_at=now,
            )
            self.session.add(proj_repo)

        await self.session.commit()
        await self.session.refresh(proj_repo)
        return proj_repo

    async def get_project_repository(
        self,
        owner_id: str,
        project_id: str,
    ) -> ProjectRepository | None:
        """Fetch associated repository record for a project."""
        stmt = select(ProjectRepository).where(
            ProjectRepository.project_id == project_id,
            ProjectRepository.owner_id == owner_id,
        )
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    async def sync_project_repository(
        self,
        owner_id: str,
        project_id: str,
    ) -> ProjectRepository:
        """Refresh README brief and metadata for an associated repository."""
        assoc = await self.get_project_repository(owner_id, project_id)
        if assoc is None:
            raise ServiceNotFound(f"No repository linked to project {project_id}")

        client = await self.get_client(owner_id)
        repo_data = await client.get_repository(assoc.full_name)
        readme_content, readme_sha = await client.get_readme(assoc.full_name)
        brief = extract_readme_brief(readme_content)

        raw_owner = repo_data.get("owner")
        assoc.repo_owner = (
            raw_owner["login"] if isinstance(raw_owner, dict) else str(raw_owner or "")
        )
        assoc.repo_name = repo_data["name"]
        assoc.default_branch = repo_data["default_branch"]
        assoc.html_url = repo_data["html_url"]
        assoc.project_brief = brief
        assoc.readme_sha = readme_sha
        assoc.readme_content = readme_content
        assoc.last_synced_at = datetime.now(UTC)

        self.session.add(assoc)
        await self.session.commit()
        await self.session.refresh(assoc)
        return assoc

    async def get_repository_context(
        self,
        owner_id: str,
        project_id: str,
    ) -> dict[str, Any] | None:
        """Fetch deep repository context including live milestones, issues, and commits."""
        assoc = await self.get_project_repository(owner_id, project_id)
        if assoc is None:
            return None

        context: dict[str, Any] = {
            "repository": {
                "id": assoc.repo_id,
                "full_name": assoc.full_name,
                "name": assoc.repo_name,
                "owner": assoc.repo_owner,
                "html_url": assoc.html_url,
                "default_branch": assoc.default_branch,
                "last_synced_at": assoc.last_synced_at.isoformat()
                if assoc.last_synced_at
                else None,
            },
            "project_brief": assoc.project_brief or {},
            "milestones": [],
            "issues": [],
            "commits": [],
        }

        # Try fetching live context if user is still authorized
        try:
            client = await self.get_client(owner_id)
            context["milestones"] = await client.list_milestones(assoc.full_name, limit=5)
            context["issues"] = await client.list_issues(assoc.full_name, limit=10)
            context["commits"] = await client.list_commits(assoc.full_name, limit=10)
        except Exception as exc:
            log.warning("Could not fetch live GitHub data for %s: %s", assoc.full_name, exc)

        return context

    async def disconnect_project_repository(
        self,
        owner_id: str,
        project_id: str,
    ) -> bool:
        """Remove repository link from a project."""
        stmt = delete(ProjectRepository).where(
            ProjectRepository.project_id == project_id,
            ProjectRepository.owner_id == owner_id,
        )
        res = await self.session.execute(stmt)
        await self.session.commit()
        rowcount = getattr(res, "rowcount", 0) or 0
        return bool(rowcount > 0)
