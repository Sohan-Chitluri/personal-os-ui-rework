"""M1-A application settings.

Uses pydantic-settings so configuration comes from environment variables (and an
optional ``.env`` file for local development). This is deliberately separate
from the legacy ``personal_os.core.config.Config`` (which describes the local
TUI/vault layout) — that module stays untouched so the existing 189 tests keep
passing. Cloud/API/worker components read :class:`Settings` instead.

Secrets are NEVER hard-coded. In AWS they are injected from Secrets Manager into
the process environment; locally they come from ``.env`` (git-ignored).
"""

from __future__ import annotations

from functools import lru_cache
from typing import Any, Literal

from pydantic import Field, field_validator, model_validator
from pydantic_settings import (
    BaseSettings,
    PydanticBaseSettingsSource,
    SettingsConfigDict,
)

# Default local database endpoint from docker-compose.postgres.yml.
# The local container trusts connections only on its loopback-bound port.
DEFAULT_LOCAL_DATABASE_URL = "postgresql+asyncpg://personal_os@127.0.0.1:54329/personal_os_dev"


class Settings(BaseSettings):
    """Runtime configuration for the API, worker, and persistence layers."""

    model_config = SettingsConfigDict(
        env_file=(".env", "/opt/personal-os/.hermes/.env"),
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # --- Environment ---
    environment: Literal["local", "test", "staging", "production"] = "local"
    debug: bool = False
    log_level: str = "INFO"

    # --- Database ---
    # PostgreSQL is canonical locally and in AWS; sync URL forms are normalized
    # to asyncpg by db/session.py.
    database_url: str = Field(default=DEFAULT_LOCAL_DATABASE_URL)
    db_pool_size: int = 5
    db_max_overflow: int = 10
    db_echo: bool = False

    # --- Queue (local development or durable at-least-once SQS) ---
    queue_backend: Literal["inline", "sqs"] = "inline"
    sqs_queue_url: str | None = None
    sqs_region: str = "us-east-1"
    sqs_wait_time_seconds: int = Field(default=20, ge=0, le=20)
    sqs_visibility_timeout: int = Field(default=300, ge=1, le=43200)
    sqs_max_messages: int = Field(default=1, ge=1, le=10)

    # --- Auth (Cognito; enforced from M1-C) ---
    cognito_user_pool_id: str | None = None
    cognito_app_client_id: str | None = None
    cognito_region: str = "us-east-1"
    cognito_token_use: Literal["access", "id"] = "access"
    # Local/test only: when set, a static bearer token is accepted.
    dev_auth_token: str | None = None
    # Explicit opt-in for synthetic test:user tokens in local/test only.
    allow_synthetic_auth: bool = False
    # Separate signing key for companion device credentials; never reuse the
    # user API development token as a device credential secret.
    companion_jwt_secret: str | None = None

    # --- LLM (existing pipeline & Amazon Bedrock) ---
    llm_provider: Literal["bedrock", "openrouter"] = "bedrock"
    openrouter_api_key: str | None = None
    llm_model: str = "google/gemma-4-31b-it:free"
    bedrock_region: str = "us-east-1"
    bedrock_model_id: str = "us.amazon.nova-2-lite-v1:0"
    bedrock_timeout_seconds: float = Field(default=30.0, ge=1.0, le=300.0)
    bedrock_max_tokens: int = Field(default=4096, ge=1, le=65536)
    bedrock_temperature: float = Field(default=0.1, ge=0.0, le=1.0)
    bedrock_top_p: float = Field(default=0.9, ge=0.0, le=1.0)
    assistant_backend: Literal["bedrock", "hermes", "mock"] = "bedrock"

    # --- CORS ---
    cors_origins: list[str] = Field(
        default_factory=lambda: [
            "http://localhost:5173",
            "http://127.0.0.1:5173",
            "http://localhost:3000",
            "http://testserver",
            "https://app.pos-deku.com",
            "https://app.dev.pos-deku.com",
            "https://app.staging.pos-deku.com",
        ]
    )

    @field_validator("cors_origins", mode="before")
    @classmethod
    def _parse_cors_origins(cls, v: Any) -> list[str]:
        if isinstance(v, str):
            return [origin.strip() for origin in v.split(",") if origin.strip()]
        if isinstance(v, (list, tuple, set)):
            return [str(origin).strip() for origin in v if str(origin).strip()]
        return v  # type: ignore[no-any-return]

    # --- Hermes Runtime (Raphael MVP) ---
    hermes_adapter: Literal["live", "mock"] = "live"
    hermes_binary_path: str = "hermes"
    hermes_timeout_seconds: float = 90.0
    hermes_skill_name: str = "personal-os"
    hermes_model: str = "nvidia/nemotron-3.5-lightning:free"
    hermes_provider: str = "openrouter"

    # --- GitHub Integration ---
    github_public_username: str | None = None
    github_client_id: str | None = None
    github_client_secret: str | None = None
    github_redirect_uri: str | None = None
    github_token_encryption_key: str | None = None
    github_api_base_url: str = "https://api.github.com"
    github_oauth_base_url: str = "https://github.com/login/oauth"

    # --- AWS ---
    aws_region: str = "us-east-1"
    s3_bucket: str | None = None

    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls: type[BaseSettings],
        init_settings: PydanticBaseSettingsSource,
        env_settings: PydanticBaseSettingsSource,
        dotenv_settings: PydanticBaseSettingsSource,
        file_secret_settings: PydanticBaseSettingsSource,
    ) -> tuple[PydanticBaseSettingsSource, ...]:
        class FilteredDotEnvSource(PydanticBaseSettingsSource):
            def get_field_value(self, field: Any, field_name: str) -> tuple[Any, str, bool]:
                return dotenv_settings.get_field_value(field, field_name)

            def __call__(self) -> dict[str, Any]:
                d = dict(dotenv_settings())

                def _get_env(data: dict[str, Any]) -> str | None:
                    for k, v in data.items():
                        if k.lower() == "environment" and isinstance(v, str):
                            return v.lower()
                    return None

                env = (
                    _get_env(init_settings()) or _get_env(env_settings()) or _get_env(d) or "local"
                )
                if env in ("staging", "production"):
                    keys_to_remove = [
                        k for k in d if k.lower() in ("allow_synthetic_auth", "dev_auth_token")
                    ]
                    for k in keys_to_remove:
                        d.pop(k, None)
                return d

        return (
            init_settings,
            env_settings,
            FilteredDotEnvSource(settings_cls),
            file_secret_settings,
        )

    @model_validator(mode="after")
    def _validate_auth_guards(self) -> Settings:
        """Reject synthetic development authentication outside local/test."""
        if self.environment in ("staging", "production"):
            if self.allow_synthetic_auth:
                raise ValueError("allow_synthetic_auth cannot be enabled in staging or production")
            if self.dev_auth_token:
                raise ValueError("dev_auth_token cannot be configured in staging or production")
        return self

    @property
    def is_postgres(self) -> bool:
        return self.database_url.startswith(("postgresql", "postgres"))


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return a process-wide, cached Settings instance."""
    return Settings()


def reset_settings_cache() -> None:
    """Clear the settings cache (used by tests that mutate the environment)."""
    get_settings.cache_clear()


__all__ = ["Settings", "get_settings", "reset_settings_cache", "DEFAULT_LOCAL_DATABASE_URL"]
