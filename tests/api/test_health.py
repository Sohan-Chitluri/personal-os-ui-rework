"""Tests for Health, Readiness, and OpenAPI specification endpoints."""

from __future__ import annotations

from unittest.mock import AsyncMock

from httpx import AsyncClient

from personal_os.api.deps import get_db_session


async def test_health_endpoints(client: AsyncClient):
    """Liveness probe returns 200 OK without authentication."""
    res1 = await client.get("/health")
    assert res1.status_code == 200
    assert res1.json() == {"status": "ok"}

    res2 = await client.get("/api/v1/health")
    assert res2.status_code == 200
    assert res2.json() == {"status": "ok"}


async def test_ready_endpoints(client: AsyncClient):
    """Readiness probe verifies database connectivity and returns 200."""
    res1 = await client.get("/ready")
    assert res1.status_code == 200
    data = res1.json()
    assert data["status"] == "ready"
    assert data["database"] == "connected"

    res2 = await client.get("/api/v1/ready")
    assert res2.status_code == 200
    assert res2.json()["status"] == "ready"


async def test_ready_returns_503_when_db_fails(api_app, client: AsyncClient):
    """Readiness probe returns 503 when the database check raises an exception."""
    mock_session = AsyncMock()
    mock_session.execute.side_effect = RuntimeError("DB connection lost")

    async def _failing_db():
        yield mock_session

    api_app.dependency_overrides[get_db_session] = _failing_db
    try:
        res = await client.get("/ready")
        assert res.status_code == 503
        data = res.json()
        assert data["status"] == "not_ready"
        assert data["database"] == "error"
        assert "lost" in data["details"]["error"]
    finally:
        del api_app.dependency_overrides[get_db_session]


async def test_version_endpoints(client: AsyncClient):
    """Version probe returns version and environment strings."""
    res = await client.get("/version")
    assert res.status_code == 200
    data = res.json()
    assert data["version"] == "0.1.0"
    assert "environment" in data


async def test_openapi_spec_available(client: AsyncClient):
    """OpenAPI schema is exposed and includes all registered route paths."""
    res = await client.get("/openapi.json")
    assert res.status_code == 200
    schema = res.json()
    assert schema["info"]["title"] == "Personal OS API"
    paths = schema["paths"]
    assert "/health" in paths
    assert "/api/v1/projects" in paths
    assert "/api/v1/tasks" in paths
    assert "/api/v1/captures" in paths
    assert "/api/v1/processing/{job_id}" in paths


async def test_cors_preflight_allows_port_8080(client: AsyncClient):
    """CORS preflight request from localhost:8080 succeeds with correct access-control headers."""
    headers = {
        "Origin": "http://localhost:8080",
        "Access-Control-Request-Method": "GET",
    }
    res = await client.options("/api/v1/health", headers=headers)
    assert res.status_code == 200
    assert res.headers.get("access-control-allow-origin") == "http://localhost:8080"
