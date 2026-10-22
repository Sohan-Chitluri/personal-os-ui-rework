# GitHub App Setup and Configuration Guide

This guide documents the setup, permissions, and environment variables required for the browser-based GitHub authorization flow in Personal OS (Blockday & Raphael).

---

## 1. Overview

Personal OS uses a GitHub App with user authorization (OAuth web flow) to inspect repositories, parse README documentation into structured architecture briefs, list open issues and milestones, and track recent commits.

- **No manual Personal Access Tokens (PATs)** are accepted in the UI.
- All tokens are encrypted at rest using Fernet AES-128 authenticated encryption (`personal_os.core.crypto`).
- Credentials are never exposed to frontend JavaScript, logs, or Diary summaries.
- Tokens are refreshed automatically if token expiration is enabled on the GitHub App.

---

## 2. GitHub App Creation

To configure a GitHub App for Personal OS:

1. Navigate to **GitHub Settings** → **Developer Settings** → **GitHub Apps** → **New GitHub App**.
2. Fill in the general settings:
   - **GitHub App name**: `Personal OS - Blockday` (or your choice).
   - **Homepage URL**: `http://localhost:5173` (development) or `https://<your-app-domain>` (production).
   - **Callback URL**: 
     - Development: `http://localhost:8000/api/v1/github/callback` (or `http://localhost:5173/projects`)
     - Production: `https://<your-api-domain>/api/v1/github/callback`
   - **Request user authorization (OAuth) during installation**: Checked.
   - **Expire user authorization tokens**: Checked (recommended).
3. **Webhook**:
   - Uncheck **Active** (Webhooks are not required for read-only user queries).

---

## 3. Required Permissions (Least-Privilege)

Configure the following read-only permissions under **Repository permissions**:

| Permission | Access | Purpose |
| :--- | :--- | :--- |
| **Contents** | Read-only | Reading repository `README.md` and recent commit logs |
| **Issues** | Read-only | Inspecting open issues and issue labels |
| **Metadata** | Read-only | Basic repository attributes (branches, visibility, owner) |

Under **Account permissions** (optional):
- **Email addresses**: Read-only (optional).

No write permissions or administrative permissions are requested or required.

---

## 4. Environment Variables

Configure the following environment variables in your `.env` file or AWS Secrets Manager:

| Variable | Description | Example / Default |
| :--- | :--- | :--- |
| `GITHUB_CLIENT_ID` | Client ID from the GitHub App settings page. | `Iv1.xxxxxxxxxxxx` |
| `GITHUB_CLIENT_SECRET` | Client Secret generated under your GitHub App settings. | `xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx` |
| `GITHUB_REDIRECT_URI` | *(Optional)* Override the OAuth callback URI. | `http://localhost:8000/api/v1/github/callback` |
| `GITHUB_TOKEN_ENCRYPTION_KEY` | Secret seed or 32-byte key used to derive the Fernet AES-128 encryption key. | `change-me-fernet-secret-key-32b` |
| `GITHUB_API_BASE_URL` | GitHub API base URL. | `https://api.github.com` |
| `GITHUB_OAUTH_BASE_URL` | GitHub OAuth base URL. | `https://github.com/login/oauth` |

> [!IMPORTANT]
> If `GITHUB_CLIENT_ID` or `GITHUB_CLIENT_SECRET` are unset, Personal OS will display a clear setup message in the project development section and prevent insecure fallbacks.

---

## 5. Security & CSRF Architecture

1. **Authorization Initiation**: When the user clicks **Authorize with GitHub**, the frontend requests `GET /api/v1/github/authorize`. The backend generates a cryptographically secure random state (`secrets.token_urlsafe(32)`) and records it in `github_oauth_states` with a 10-minute expiration window and single-use constraint.
2. **State Validation**: Upon callback, `validate_and_consume_state` verifies that the state exists, is not expired, matches the user, and has not been replayed.
3. **Token Encryption**: Access tokens and refresh tokens are encrypted via `encrypt_secret` before being committed to the `user_github_auth` table.
4. **Token Expiry**: When `token_expires_at` is reached, `GitHubService.get_client` automatically performs a token refresh with GitHub and updates the encrypted credentials transparently.
5. **Account Disconnection**: `POST /api/v1/github/disconnect` purges credentials immediately from the database while leaving all Blockday projects and tasks intact.
